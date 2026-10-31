// SPDX-FileCopyrightText: 2025-2026 Lasse Heitgres
// SPDX-License-Identifier: GPL-3.0-or-later
// Additional terms: see NOTICE

use std::io::{Read, Write};
use std::os::unix::net::UnixListener;
use std::time::{Duration, Instant};

use smithay::reexports::calloop::generic::Generic;
use smithay::reexports::calloop::{Interest, LoopHandle, Mode, PostAction};

use crate::state::{Kind, LiviState, VideoBox};

pub fn leave_with_core(handle: &LoopHandle<'static, LiviState>) {
    if !std::env::var("LIVI_LIFELINE").is_ok_and(|v| v == "1") {
        return;
    }
    handle
        .insert_source(
            Generic::new(std::io::stdin(), Interest::READ, Mode::Level),
            |_, _, state: &mut LiviState| {
                let mut buf = [0u8; 64];
                match std::io::stdin().read(&mut buf) {
                    Ok(n) if n > 0 => Ok(PostAction::Continue),
                    Err(e) if e.kind() == std::io::ErrorKind::Interrupted => {
                        Ok(PostAction::Continue)
                    }
                    _ => {
                        log::info!("core is gone, the compositor stops");
                        state.running = false;
                        Ok(PostAction::Remove)
                    }
                }
            },
        )
        .expect("insert core lifeline");
}

pub fn init(state: &mut LiviState, handle: &LoopHandle<'static, LiviState>) {
    let Some(path) = state.ctrl_path.clone() else {
        return;
    };
    let _ = std::fs::remove_file(&path);
    let listener = match UnixListener::bind(&path) {
        Ok(l) => l,
        Err(e) => {
            log::error!("control socket bind failed: {e}");
            return;
        }
    };
    listener.set_nonblocking(true).ok();
    handle
        .insert_source(
            Generic::new(listener, Interest::READ, Mode::Level),
            |_, listener, state: &mut LiviState| {
                while let Ok((client, _)) = listener.accept() {
                    client.set_nonblocking(true).ok();
                    state.ctrl_client = Some(client);
                    state.ctrl_buf.clear();
                    state.ctrl_out.clear();
                    send_ui(state);
                    send_panels(state);
                    send_displays(state);
                    send_video_readiness(state);
                }
                Ok(PostAction::Continue)
            },
        )
        .expect("insert ctrl listener");

    handle
        .insert_source(
            smithay::reexports::calloop::timer::Timer::from_duration(Duration::from_millis(20)),
            |_, _, state: &mut LiviState| {
                poll_client(state);
                flush_out(state);
                smithay::reexports::calloop::timer::TimeoutAction::ToDuration(
                    Duration::from_millis(20),
                )
            },
        )
        .expect("insert ctrl poll timer");
    log::info!("control socket at {path}");
}

fn poll_client(state: &mut LiviState) {
    let Some(client) = state.ctrl_client.as_mut() else {
        return;
    };
    let mut chunk = Vec::new();
    let mut buf = [0u8; 4096];
    loop {
        match client.read(&mut buf) {
            Ok(0) => {
                state.ctrl_client = None;
                state.ctrl_buf.clear();
                return;
            }
            Ok(n) => chunk.extend_from_slice(&buf[..n]),
            Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => break,
            Err(_) => {
                state.ctrl_client = None;
                state.ctrl_buf.clear();
                return;
            }
        }
    }
    if chunk.is_empty() {
        return;
    }
    state.ctrl_buf.push_str(&String::from_utf8_lossy(&chunk));
    if state.ctrl_buf.len() > 64 * 1024 {
        log::warn!(
            "control buffer overflow, dropping {} bytes",
            state.ctrl_buf.len()
        );
        state.ctrl_buf.clear();
        return;
    }
    while let Some(pos) = state.ctrl_buf.find('\n') {
        let line: String = state.ctrl_buf.drain(..=pos).collect();
        let line = line.trim().to_string();
        if !line.is_empty() {
            log::debug!("ctrl < {line}");
            handle_line(state, &line);
        }
    }
}

pub fn send(state: &mut LiviState, line: &str) {
    state.ctrl_out.extend_from_slice(line.as_bytes());
    flush_out(state);
}

fn flush_out(state: &mut LiviState) {
    let LiviState {
        ctrl_client,
        ctrl_out,
        ..
    } = state;
    let Some(client) = ctrl_client.as_mut() else {
        ctrl_out.clear();
        return;
    };
    while !ctrl_out.is_empty() {
        match client.write(ctrl_out) {
            Ok(0) => break,
            Ok(n) => {
                ctrl_out.drain(..n);
            }
            Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => break,
            Err(e) => {
                log::warn!("control write failed: {e}");
                *ctrl_client = None;
                ctrl_out.clear();
                return;
            }
        }
    }
}

fn send_ui(state: &mut LiviState) {
    let node = state
        .render_node
        .as_ref()
        .map_or_else(|| "-".to_string(), |p| p.display().to_string());
    let line = format!("ui {} {node}\n", state.ui_socket);
    send(state, &line);
}

fn send_video_readiness(state: &mut LiviState) {
    let lines: String = state
        .toplevels
        .iter()
        .filter(|t| t.kind == Kind::Video && !t.tag.is_empty() && !t.video_status.retired)
        .map(|t| t.video_status.readiness_line(&t.tag, t.video_status.ready))
        .collect();
    send(state, &lines);
}

fn parse_video_box(words: &[&str]) -> Option<Option<VideoBox>> {
    if words == ["-"] {
        return Some(None);
    }
    let [x, y, w, h] = words else { return None };
    Some(Some(VideoBox::new(
        x.parse().ok()?,
        y.parse().ok()?,
        w.parse().ok()?,
        h.parse().ok()?,
    )?))
}

fn parse_generation(words: &[&str], current: u64) -> Option<u64> {
    match words {
        [] => Some(current),
        [generation] => generation.parse().ok(),
        _ => None,
    }
}

fn parse_box_command(words: &[&str], current: u64) -> Option<(Option<VideoBox>, u64)> {
    let count = if words.first() == Some(&"-") { 1 } else { 4 };
    if words.len() < count {
        return None;
    }
    Some((
        parse_video_box(&words[..count])?,
        parse_generation(&words[count..], current)?,
    ))
}

pub fn send_displays(state: &mut LiviState) {
    if let Some(lines) = crate::drm::displays_lines(state) {
        send(state, &lines);
    }
}

pub fn send_panels(state: &mut LiviState) {
    let panels: Vec<String> = state
        .screens
        .iter()
        .enumerate()
        .filter_map(|(i, s)| {
            let (mm_w, mm_h) = crate::host::panel_mm(state, i)?;
            if s.width > 0 && s.height > 0 {
                Some(format!(
                    "panel {} {} {} {} {}\n",
                    s.role, mm_w, mm_h, s.width, s.height
                ))
            } else {
                None
            }
        })
        .collect();
    for p in panels {
        send(state, &p);
    }
}

fn handle_line(state: &mut LiviState, line: &str) {
    if line == "restart" {
        log::info!("restart requested -> waiting for inner UI to quit, then re-exec");
        state.full_restart = true;
        if state.startup_pid.is_some() {
            state.restart_deadline = Some(Instant::now() + Duration::from_secs(8));
            crate::spawn::terminate_child(state);
        } else {
            state.running = false;
        }
        return;
    }

    let mut parts = line.split_whitespace();
    match parts.next() {
        Some("screen") => {
            let Some(role) = parts.next() else { return };
            let Some(onoff) = parts.next().and_then(|v| v.parse::<i32>().ok()) else {
                return;
            };
            let w = parts.next().and_then(|v| v.parse::<i32>().ok());
            let h = parts.next().and_then(|v| v.parse::<i32>().ok());
            let Some(idx) = state.screen_idx_by_role(role) else {
                return;
            };
            if let (Some(w), Some(h)) = (w, h)
                && w > 0
                && h > 0
            {
                state.screens[idx].req_width = w;
                state.screens[idx].req_height = h;
            }
            if onoff != 0 {
                crate::host::open_screen(state, idx);
            } else {
                crate::host::close_screen(state, idx);
            }
        }
        Some("fullscreen") => {
            let (Some(role), Some(on)) = (
                parts.next(),
                parts.next().and_then(|v| v.parse::<u8>().ok()),
            ) else {
                return;
            };
            let Some(idx) = state.screen_idx_by_role(role) else {
                return;
            };
            let want = on != 0;
            if state.drm.is_none() && state.screens[idx].fullscreen != want {
                log::info!("ctrl < {line}");
                crate::layout::set_fullscreen(state, idx, want);
            }
        }
        Some("claim") => {
            if let Some(tag) = parts.next() {
                let cfg = state.cfg_for_tag(tag);
                let Some(generation) = parse_generation(&parts.collect::<Vec<_>>(), cfg.generation)
                else {
                    return;
                };
                if cfg.accept_generation(generation).is_none() {
                    return;
                }
                // A replacement claim supersedes the old queued owner of this tag.
                state
                    .pending_video_tags
                    .retain(|(pending, _)| pending != tag);
                if crate::shell::bind_waiting_plane(state, tag, generation) {
                    log::info!("ctrl < claim {tag} (took the plane already waiting)");
                } else if state.pending_video_tags.len() < 16 {
                    state
                        .pending_video_tags
                        .push_back((tag.to_string(), generation));
                    log::info!(
                        "ctrl < claim {tag} (pending: {:?})",
                        state.pending_video_tags
                    );
                }
            }
        }
        Some("unclaim") => {
            if let Some(tag) = parts.next() {
                let cfg = state.cfg_for_tag(tag);
                let Some(generation) = parse_generation(&parts.collect::<Vec<_>>(), cfg.generation)
                else {
                    return;
                };
                if cfg.accept_generation(generation).is_none() {
                    return;
                }
                state
                    .pending_video_tags
                    .retain(|(t, pending)| t != tag || *pending > generation);
                crate::shell::retire_video_tag(state, tag, generation);
                log::info!(
                    "ctrl < unclaim {tag} (pending: {:?})",
                    state.pending_video_tags
                );
            }
        }
        Some("videocfg") => {
            log::info!("ctrl < {line}");
            let (Some(tag), Some(screen)) = (parts.next(), parts.next()) else {
                return;
            };
            let nums: Vec<f64> = parts.filter_map(|v| v.parse().ok()).collect();
            if nums.len() != 6 {
                return;
            }
            let tag = tag.to_string();
            {
                let cfg = state.cfg_for_tag(&tag);
                cfg.screen = screen.to_string();
                cfg.has_crop = nums[2] > 0.0 && nums[3] > 0.0;
                cfg.crop_l = nums[0];
                cfg.crop_t = nums[1];
                cfg.vis_w = nums[2];
                cfg.vis_h = nums[3];
                cfg.tier_w = nums[4];
                cfg.tier_h = nums[5];
            }
            if let Some(v) = state.find_video_by_tag(&tag) {
                crate::layout::apply_cfg_to_video(state, &tag, v);
            }
        }
        Some("videobox") => {
            let (Some(tag), Some(screen)) = (parts.next(), parts.next()) else {
                return;
            };
            if state.screen_idx_by_role(screen).is_none() {
                return;
            }
            let cfg = state.cfg_for_tag(tag);
            let Some((destination, generation)) =
                parse_box_command(&parts.collect::<Vec<_>>(), cfg.generation)
            else {
                return;
            };
            let Some(new_generation) = cfg.accept_generation(generation) else {
                return;
            };
            log::info!("ctrl < {line}");
            let changed = cfg.set_destination(screen, destination) || new_generation;
            if let Some(v) = state.find_video_by_tag(tag) {
                state.toplevels[v]
                    .video_status
                    .advance_generation(generation);
                if changed {
                    state.toplevels[v].video_status.invalidate_placement();
                }
                crate::layout::apply_cfg_to_video(state, tag, v);
            }
            if changed {
                // Acknowledge that placement has been applied before permitting
                // a later matching-size video buffer to reopen the UI aperture.
                send(state, &format!("videoready {tag} {generation} 0\n"));
            }
        }
        Some("videoshow") => {
            log::info!("ctrl < {line}");
            let (Some(tag), Some(onoff)) = (
                parts.next(),
                parts.next().and_then(|v| v.parse::<i32>().ok()),
            ) else {
                return;
            };
            let tag = tag.to_string();
            {
                let cfg = state.cfg_for_tag(&tag);
                cfg.has_visible = true;
                cfg.visible = onoff != 0;
            }
            if let Some(v) = state.find_video_by_tag(&tag) {
                state.toplevels[v].visible = onoff != 0;
                crate::host::damage_all(state);
            }
        }
        Some("aspect") => {
            let Some(idx) = parts.next().and_then(|role| state.screen_idx_by_role(role)) else {
                return;
            };
            let w = parts
                .next()
                .and_then(|v| v.parse::<i32>().ok())
                .unwrap_or(0);
            let h = parts
                .next()
                .and_then(|v| v.parse::<i32>().ok())
                .unwrap_or(0);
            let aspect = (w > 0 && h > 0).then_some((w, h));
            if state.screens[idx].aspect != aspect {
                log::info!("ctrl < {line}");
                state.screens[idx].aspect = aspect;
                crate::host::refit(state, idx);
            }
        }
        Some("mode") => {
            let (Some(idx), Some(mode)) = (
                parts.next().and_then(|role| state.screen_idx_by_role(role)),
                parts.next(),
            ) else {
                return;
            };
            log::info!("ctrl < {line}");
            crate::drm::set_mode(state, idx, (mode != "-").then(|| mode.to_string()));
        }
        Some("backdrop") => {
            let nums: Vec<i32> = parts.filter_map(|v| v.parse().ok()).collect();
            if nums.len() != 3 {
                return;
            }
            let dbg = std::env::var("LIVI_DEBUG_BG").is_ok();
            for s in &mut state.screens {
                s.backdrop_color = [
                    nums[0] as f32 / 255.0,
                    nums[1] as f32 / 255.0,
                    nums[2] as f32 / 255.0,
                    1.0,
                ];
                s.has_backdrop_color = true;
            }
            if !dbg {
                crate::host::damage_all(state);
            }
        }
        Some("gamma") => {
            log::info!("ctrl < {line}");
            let nums: Vec<f64> = parts.filter_map(|v| v.parse().ok()).collect();
            if nums.len() != 5 {
                return;
            }
            state.cal.gamma = nums[0] as f32;
            state.cal.contrast = nums[1] as f32;
            state.cal.gain = [nums[2] as f32, nums[3] as f32, nums[4] as f32];
            state.cal.active = nums.iter().any(|&v| v != 1.0);
            crate::drm::apply_calibration(state);
            crate::host::damage_all(state);
        }
        _ => {}
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn video_boxes_accept_only_bounded_finite_coordinates_or_explicit_reset() {
        assert_eq!(parse_video_box(&["-"]), Some(None));
        assert_eq!(
            parse_video_box(&["0.5", "0.1", "0.25", "0.8"]),
            Some(VideoBox::new(0.5, 0.1, 0.25, 0.8))
        );
        for words in [
            vec!["0", "0", "1", "1", "extra"],
            vec!["-", "extra"],
            vec!["0", "0", "broken", "1"],
            vec!["NaN", "0", "1", "1"],
            vec!["0", "0", "inf", "1"],
            vec!["-0.1", "0", "1", "1"],
            vec!["0", "0", "0", "1"],
            vec!["0.9", "0", "0.2", "1"],
            vec!["0", "0.5", "1", "0.6"],
        ] {
            assert_eq!(parse_video_box(&words), None, "{words:?}");
        }
    }

    #[test]
    fn viewport_generation_is_optional_for_legacy_commands_and_strict_when_present() {
        let viewport = VideoBox::new(0.5, 0.1, 0.25, 0.8);
        assert_eq!(
            parse_box_command(&["0.5", "0.1", "0.25", "0.8", "9"], 3),
            Some((viewport, 9))
        );
        assert_eq!(
            parse_box_command(&["0.5", "0.1", "0.25", "0.8"], 3),
            Some((viewport, 3))
        );
        assert_eq!(parse_box_command(&["-", "12"], 3), Some((None, 12)));
        assert_eq!(parse_box_command(&["-"], 3), Some((None, 3)));
        assert_eq!(parse_box_command(&["-", "broken"], 3), None);
        assert_eq!(parse_box_command(&["-", "-1"], 3), None);
        assert_eq!(parse_box_command(&["-", "9", "extra"], 3), None);
        assert_eq!(parse_generation(&["9"], 3), Some(9));
        assert_eq!(parse_generation(&[], 3), Some(3));
    }
}
