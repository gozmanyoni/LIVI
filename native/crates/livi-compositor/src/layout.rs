// SPDX-FileCopyrightText: 2025-2026 Lasse Heitgres
// SPDX-License-Identifier: GPL-3.0-or-later
// Additional terms: see NOTICE

use smithay::utils::{Logical, Point, Rectangle, Size};

use crate::state::{Kind, LiviState, TopLevel, VideoBox};

/// Round shared edges together so fractional destinations never bleed outside the box.
pub fn video_destination(
    content: Rectangle<i32, Logical>,
    destination: Option<VideoBox>,
) -> Rectangle<i32, Logical> {
    let Some(d) = destination else { return content };
    let x = (d.x * f64::from(content.size.w)).round() as i32;
    let y = (d.y * f64::from(content.size.h)).round() as i32;
    let right = ((d.x + d.w) * f64::from(content.size.w)).round() as i32;
    let bottom = ((d.y + d.h) * f64::from(content.size.h)).round() as i32;
    Rectangle::new(
        content.loc + Point::from((x, y)),
        (right - x, bottom - y).into(),
    )
}

#[derive(Clone, Copy)]
struct VideoSource {
    crop_l: f64,
    crop_t: f64,
    vis_w: f64,
    vis_h: f64,
    tier_w: f64,
    tier_h: f64,
}

fn covers_destination(tag: &str, destination: Option<VideoBox>) -> bool {
    tag == "cluster-main" && destination.is_some()
}

/// Size the complete video buffer around its visible source crop. Navigation
/// tiles fill their clipped body; ordinary projection retains the entire image.
fn video_geometry(
    destination: Rectangle<i32, Logical>,
    source: Option<VideoSource>,
    cover: bool,
) -> Rectangle<i32, Logical> {
    let Some(source) = source else {
        return destination;
    };
    if ![
        source.crop_l,
        source.crop_t,
        source.vis_w,
        source.vis_h,
        source.tier_w,
        source.tier_h,
    ]
    .into_iter()
    .all(f64::is_finite)
        || source.vis_w <= 0.0
        || source.vis_h <= 0.0
        || source.tier_w <= 0.0
        || source.tier_h <= 0.0
    {
        return destination;
    }
    let (ow, oh) = (f64::from(destination.size.w), f64::from(destination.size.h));
    let (scale_x, scale_y) = (ow / source.vis_w, oh / source.vis_h);
    let scale = if cover {
        scale_x.max(scale_y)
    } else {
        scale_x.min(scale_y)
    };
    let off_x = (ow - source.vis_w * scale) / 2.0;
    let off_y = (oh - source.vis_h * scale) / 2.0;
    Rectangle::new(
        (
            (f64::from(destination.loc.x) + off_x - source.crop_l * scale).round() as i32,
            (f64::from(destination.loc.y) + off_y - source.crop_t * scale).round() as i32,
        )
            .into(),
        (
            (source.tier_w * scale).round() as i32,
            (source.tier_h * scale).round() as i32,
        )
            .into(),
    )
}

fn set_size(t: &mut TopLevel, w: i32, h: i32) {
    let size = (w.max(1), h.max(1));
    t.video_status.expected_size = Some(size);
    t.toplevel.with_pending_state(|st| {
        st.size = Some(Size::from(size));
    });
    t.toplevel.send_pending_configure();
}

pub fn apply_video_layout(state: &mut LiviState, idx: usize) {
    let t = &state.toplevels[idx];
    if t.kind != Kind::Video || t.video_status.retired {
        return;
    }
    let s = &state.screens[t.screen_idx];
    let top = s.top_inset();
    let destination = video_destination(
        Rectangle::new((s.x, top).into(), (s.width, s.height - top).into()),
        t.destination,
    );
    let (ow, oh) = (destination.size.w, destination.size.h);
    if ow <= 0 || oh <= 0 {
        log::info!(
            "video '{}' deferred: screen '{}' has no window yet",
            state.toplevels[idx].tag,
            s.role
        );
        state.toplevels[idx].video_status.expected_size = None;
        return;
    }
    let role = s.role.clone();
    let t = &mut state.toplevels[idx];
    let geometry = video_geometry(
        destination,
        t.has_crop.then_some(VideoSource {
            crop_l: t.crop_l,
            crop_t: t.crop_t,
            vis_w: t.vis_w,
            vis_h: t.vis_h,
            tier_w: t.tier_w,
            tier_h: t.tier_h,
        }),
        covers_destination(&t.tag, t.destination),
    );
    let (tw, th) = (geometry.size.w, geometry.size.h);
    let (px, py) = (geometry.loc.x, geometry.loc.y);
    set_size(t, tw, th);
    t.position = geometry.loc;
    log::info!(
        "video '{}' -> screen '{role}' {tw}x{th} at {px},{py}",
        t.tag
    );
}

pub fn apply_cfg_to_video(state: &mut LiviState, tag: &str, idx: usize) {
    let Some(cfg) = state.video_cfgs.iter().find(|c| c.tag == tag).cloned() else {
        return;
    };
    if state.toplevels[idx].video_status.retired
        || state.toplevels[idx].video_status.generation != cfg.generation
    {
        return;
    }
    let screen = if cfg.destination.is_some() {
        &cfg.destination_screen
    } else {
        &cfg.screen
    };
    if let Some(si) = state.screen_idx_by_role(screen) {
        state.toplevels[idx].screen_idx = si;
    }
    let t = &mut state.toplevels[idx];
    t.has_crop = cfg.has_crop;
    t.crop_l = cfg.crop_l;
    t.crop_t = cfg.crop_t;
    t.vis_w = cfg.vis_w;
    t.vis_h = cfg.vis_h;
    t.tier_w = cfg.tier_w;
    t.tier_h = cfg.tier_h;
    t.destination = cfg.destination;
    apply_video_layout(state, idx);
    if cfg.has_visible {
        state.toplevels[idx].visible = cfg.visible;
    }
    crate::host::damage_all(state);
}

pub fn apply_ui_layout(state: &mut LiviState, screen_idx: usize) {
    let s = &state.screens[screen_idx];
    let (ow, oh) = (s.width, s.height);
    if ow <= 0 || oh <= 0 {
        return;
    }
    let (sx, top) = (s.x, s.top_inset());
    if let Some(ui) = state
        .toplevels
        .iter_mut()
        .find(|t| t.kind == Kind::Ui && t.screen_idx == screen_idx)
    {
        use smithay::reexports::wayland_protocols::xdg::shell::server::xdg_toplevel::State as XdgState;
        ui.position = Point::from((sx, top));
        ui.toplevel.with_pending_state(|st| {
            st.states.set(XdgState::TiledTop);
            st.states.set(XdgState::TiledBottom);
            st.states.set(XdgState::TiledLeft);
            st.states.set(XdgState::TiledRight);
            st.size = Some(Size::from((ow.max(1), (oh - top).max(1))));
        });
        ui.toplevel.send_pending_configure();
    }
    // Fullscreen changes the content inset even when the output size stays fixed.
    for idx in 0..state.toplevels.len() {
        if state.toplevels[idx].kind == Kind::Video && state.toplevels[idx].screen_idx == screen_idx
        {
            apply_video_layout(state, idx);
        }
    }
    crate::host::damage_all(state);
}

pub fn toggle_fullscreen(state: &mut LiviState, screen_idx: usize) {
    let want = !state.screens[screen_idx].fullscreen;
    set_fullscreen(state, screen_idx, want);
}

pub fn set_fullscreen(state: &mut LiviState, screen_idx: usize, want: bool) {
    state.screens[screen_idx].fullscreen = want;
    crate::host::set_fullscreen(state, screen_idx, want);
    if let Some(ui) = state
        .toplevels
        .iter()
        .find(|t| t.kind == Kind::Ui && t.screen_idx == screen_idx)
    {
        ui.toplevel.with_pending_state(|st| {
            if want {
                st.states.set(
                    smithay::reexports::wayland_protocols::xdg::shell::server::xdg_toplevel::State::Fullscreen,
                );
            } else {
                st.states.unset(
                    smithay::reexports::wayland_protocols::xdg::shell::server::xdg_toplevel::State::Fullscreen,
                );
            }
        });
        ui.toplevel.send_pending_configure();
    }
    apply_ui_layout(state, screen_idx);
}

#[cfg(test)]
mod tests {
    use super::*;
    use smithay::backend::renderer::Color32F;
    use smithay::backend::renderer::element::solid::{SolidColorBuffer, SolidColorRenderElement};
    use smithay::backend::renderer::element::utils::CropRenderElement;
    use smithay::backend::renderer::element::{Element, Kind as ElementKind};

    fn source(w: f64, h: f64) -> VideoSource {
        VideoSource {
            crop_l: 0.0,
            crop_t: 0.0,
            vis_w: w,
            vis_h: h,
            tier_w: w,
            tier_h: h,
        }
    }

    fn assert_clipped_to_tile(
        geometry: Rectangle<i32, Logical>,
        destination: Rectangle<i32, Logical>,
    ) {
        let element = SolidColorRenderElement::from_buffer(
            &SolidColorBuffer::new(
                (geometry.size.w, geometry.size.h),
                Color32F::new(0.0, 0.0, 0.0, 1.0),
            ),
            (geometry.loc.x, geometry.loc.y),
            1.0,
            1.0,
            ElementKind::Unspecified,
        );
        let clip = Rectangle::new(
            (destination.loc.x, destination.loc.y).into(),
            (destination.size.w, destination.size.h).into(),
        );
        let rendered = CropRenderElement::from_element(element, 1.0, clip).unwrap();
        assert_eq!(rendered.geometry(1.0.into()), clip);
    }

    #[test]
    fn navigation_tile_covers_and_centers_landscape_and_portrait_video() {
        let destination = Rectangle::new((1061, 157).into(), (422, 412).into());
        for source in [source(1280.0, 720.0), source(720.0, 1280.0)] {
            let geometry = video_geometry(destination, Some(source), true);
            assert!(geometry.size.w >= destination.size.w);
            assert!(geometry.size.h >= destination.size.h);
            assert!(
                (2 * geometry.loc.x + geometry.size.w - 2 * destination.loc.x - destination.size.w)
                    .abs()
                    <= 1
            );
            assert!(
                (2 * geometry.loc.y + geometry.size.h - 2 * destination.loc.y - destination.size.h)
                    .abs()
                    <= 1
            );
            assert_clipped_to_tile(geometry, destination);
        }
    }

    #[test]
    fn only_explicit_navigation_tile_changes_from_contain_to_cover() {
        let tile = VideoBox::new(0.5, 0.1, 0.25, 0.8);
        assert!(covers_destination("cluster-main", tile));
        for (tag, destination) in [
            ("cluster-main", None),
            ("main", tile),
            ("camera", tile),
            ("cluster-dash", tile),
        ] {
            assert!(!covers_destination(tag, destination));
            let target = Rectangle::new((0, 0).into(), (422, 412).into());
            let geometry = video_geometry(
                target,
                Some(source(1280.0, 720.0)),
                covers_destination(tag, destination),
            );
            assert_eq!(geometry.size.w, target.size.w);
            assert!(geometry.size.h < target.size.h);
            assert!(geometry.loc.y > target.loc.y);
        }
    }

    #[test]
    fn navigation_cover_centers_the_visible_crop_in_the_complete_buffer() {
        let destination = Rectangle::new((1061, 157).into(), (422, 412).into());
        let source = VideoSource {
            crop_l: 160.0,
            crop_t: 90.0,
            vis_w: 1280.0,
            vis_h: 720.0,
            tier_w: 1600.0,
            tier_h: 900.0,
        };
        let geometry = video_geometry(destination, Some(source), true);
        // The complete source buffer is configured; its internal crop stays
        // centered at the destination, rather than becoming an extra offset.
        let scale_x = f64::from(geometry.size.w) / source.tier_w;
        let scale_y = f64::from(geometry.size.h) / source.tier_h;
        let visible_center_x =
            f64::from(geometry.loc.x) + (source.crop_l + source.vis_w / 2.0) * scale_x;
        let visible_center_y =
            f64::from(geometry.loc.y) + (source.crop_t + source.vis_h / 2.0) * scale_y;
        assert!(
            (visible_center_x
                - (f64::from(destination.loc.x) + f64::from(destination.size.w) / 2.0))
                .abs()
                <= 1.0
        );
        assert!(
            (visible_center_y
                - (f64::from(destination.loc.y) + f64::from(destination.size.h) / 2.0))
                .abs()
                <= 1.0
        );
        assert!(source.vis_w * scale_x >= f64::from(destination.size.w));
        assert!(source.vis_h * scale_y >= f64::from(destination.size.h));
        assert_clipped_to_tile(geometry, destination);
    }

    #[test]
    fn fractional_tile_edges_are_filled_without_rendering_outside_the_destination() {
        let destination = video_destination(
            Rectangle::new((100_000, 32).into(), (1601, 601).into()),
            VideoBox::new(
                1061.0 / 1600.0,
                157.0 / 600.0,
                422.0 / 1600.0,
                412.0 / 600.0,
            ),
        );
        let geometry = video_geometry(destination, Some(source(1280.0, 720.0)), true);
        assert_clipped_to_tile(geometry, destination);
    }

    #[test]
    fn unknown_or_invalid_source_dimensions_keep_the_destination_fallback() {
        let destination = Rectangle::new((1061, 157).into(), (422, 412).into());
        assert_eq!(video_geometry(destination, None, true), destination);
        for source in [
            source(0.0, 720.0),
            source(-1280.0, 720.0),
            source(1280.0, f64::NAN),
            VideoSource {
                crop_l: f64::INFINITY,
                ..source(1280.0, 720.0)
            },
            VideoSource {
                tier_h: -720.0,
                ..source(1280.0, 720.0)
            },
        ] {
            assert_eq!(video_geometry(destination, Some(source), true), destination);
        }
    }

    #[test]
    fn video_boxes_follow_output_resize_and_exclude_server_decorations() {
        let destination = VideoBox::new(0.5, 0.1, 0.25, 0.8);
        assert_eq!(
            video_destination(
                Rectangle::new((100_000, 32).into(), (1600, 600).into()),
                destination
            ),
            Rectangle::new((100_800, 92).into(), (400, 480).into()),
        );
        assert_eq!(
            video_destination(
                Rectangle::new((0, 0).into(), (800, 300).into()),
                destination
            ),
            Rectangle::new((400, 30).into(), (200, 240).into()),
        );
        let full = Rectangle::new((100_000, 32).into(), (1600, 600).into());
        assert_eq!(video_destination(full, None), full);
    }

    #[test]
    fn fractional_shared_edges_do_not_overlap_or_leave_gaps() {
        let content = Rectangle::new((0, 0).into(), (801, 301).into());
        let left = video_destination(content, VideoBox::new(0.0, 0.0, 0.5, 1.0));
        let right = video_destination(content, VideoBox::new(0.5, 0.0, 0.5, 1.0));
        assert_eq!(left.loc.x + left.size.w, right.loc.x);
        assert_eq!(right.loc.x + right.size.w, content.size.w);
        assert_eq!(left.size.h, content.size.h);
    }
}
