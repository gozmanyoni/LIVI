// SPDX-FileCopyrightText: 2025-2026 Lasse Heitgres
// SPDX-License-Identifier: GPL-3.0-or-later
// Additional terms: see NOTICE

use std::collections::VecDeque;
use std::ffi::OsString;
use std::time::Instant;

use smithay::input::{Seat, SeatState};
use smithay::output::Output;
use smithay::reexports::calloop::generic::Generic;
use smithay::reexports::calloop::{EventLoop, Interest, LoopHandle, Mode, PostAction};
use smithay::reexports::wayland_server::backend::ClientData;
use smithay::reexports::wayland_server::{Display, DisplayHandle};
use smithay::utils::{Logical, Point};
use smithay::wayland::compositor::CompositorState;
use smithay::wayland::cursor_shape::CursorShapeManagerState;
use smithay::wayland::dmabuf::DmabufState;
use smithay::wayland::drm_syncobj::DrmSyncobjState;
use smithay::wayland::selection::data_device::DataDeviceState;
use smithay::wayland::shell::xdg::decoration::XdgDecorationState;
use smithay::wayland::shell::xdg::{ToplevelSurface, XdgShellState};
use smithay::wayland::shm::ShmState;
use smithay::wayland::socket::ListeningSocketSource;
use smithay::wayland::viewporter::ViewporterState;

use crate::host::HostState;

pub const SCREEN_X_SLOT: i32 = 100_000;
pub const TITLEBAR_H: i32 = 32;
pub const BTN_W: i32 = 32;
pub const BTN_GAP: i32 = 2;
pub const RESIZE_BORDER: i32 = 8;

/// Destination in normalized UI content coordinates, excluding server decorations.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct VideoBox {
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub h: f64,
}

impl VideoBox {
    pub fn new(x: f64, y: f64, w: f64, h: f64) -> Option<Self> {
        ([x, y, w, h].into_iter().all(f64::is_finite)
            && x >= 0.0
            && y >= 0.0
            && w > 0.0
            && h > 0.0
            && x + w <= 1.0
            && y + h <= 1.0)
            .then_some(Self { x, y, w, h })
    }
}

#[derive(Default, Clone)]
pub struct VideoCfg {
    pub tag: String,
    pub generation: u64,
    pub screen: String,
    pub has_crop: bool,
    pub crop_l: f64,
    pub crop_t: f64,
    pub vis_w: f64,
    pub vis_h: f64,
    pub tier_w: f64,
    pub tier_h: f64,
    pub destination: Option<VideoBox>,
    pub destination_screen: String,
    pub has_visible: bool,
    pub visible: bool,
}

impl VideoCfg {
    pub fn accept_generation(&mut self, generation: u64) -> Option<bool> {
        if generation < self.generation {
            return None;
        }
        let changed = generation > self.generation;
        self.generation = generation;
        Some(changed)
    }

    pub fn set_destination(&mut self, screen: &str, destination: Option<VideoBox>) -> bool {
        let changed = self.destination != destination
            || (destination.is_some() && self.destination_screen != screen);
        self.destination = destination;
        self.destination_screen = screen.to_string();
        changed
    }
}

#[derive(Clone, PartialEq)]
pub enum Kind {
    Pending,
    Ui,
    Video,
    Dialog,
}

/// A released surface never belongs to a later claimant of the same tag.
#[derive(Default)]
pub struct VideoStatus {
    pub ready: bool,
    pub retired: bool,
    pub generation: u64,
    pub expected_size: Option<(i32, i32)>,
}

impl VideoStatus {
    /// Only a still-queued claim may follow layout revisions before its window arrives.
    /// The caller retains the original claim token for the `bound` acknowledgment.
    pub fn bind_pending_generation(&mut self, claim: u64, latest: u64) -> bool {
        self.bind_generation(claim.max(latest), latest)
    }

    pub fn bind_generation(&mut self, generation: u64, latest: u64) -> bool {
        if self.retired {
            return false;
        }
        self.generation = generation;
        self.invalidate_placement();
        if generation < latest {
            self.retire();
            return false;
        }
        true
    }

    pub fn advance_generation(&mut self, generation: u64) -> bool {
        if self.retired || generation < self.generation {
            return false;
        }
        if generation != self.generation {
            self.invalidate_placement();
            self.generation = generation;
        }
        true
    }

    pub fn readiness_line(&self, tag: &str, ready: bool) -> String {
        format!("videoready {tag} {} {}\n", self.generation, u8::from(ready))
    }

    pub fn invalidate_placement(&mut self) {
        self.ready = false;
    }

    pub fn buffer_committed(&mut self, mapped: bool, size: (i32, i32)) -> Option<bool> {
        self.committed(mapped && self.expected_size == Some(size))
    }

    pub fn committed(&mut self, ready: bool) -> Option<bool> {
        if self.retired || self.ready == ready {
            return None;
        }
        self.ready = ready;
        Some(ready)
    }

    pub fn retire(&mut self) -> bool {
        let changed = !self.retired;
        self.retired = true;
        self.ready = false;
        changed
    }
}

pub struct TopLevel {
    pub toplevel: ToplevelSurface,
    pub kind: Kind,
    pub screen_idx: usize,
    pub tag: String,
    pub awaiting_claim: bool,
    pub visible: bool,
    pub has_crop: bool,
    pub crop_l: f64,
    pub crop_t: f64,
    pub vis_w: f64,
    pub vis_h: f64,
    pub tier_w: f64,
    pub tier_h: f64,
    pub destination: Option<VideoBox>,
    pub video_status: VideoStatus,
    pub position: Point<i32, Logical>,
    pub clear: bool,
}

pub struct Screen {
    pub role: String,
    pub x: i32,
    pub width: i32,
    pub height: i32,
    pub req_width: i32,
    pub req_height: i32,
    pub fullscreen: bool,
    pub output: Option<Output>,
    pub resize_pending: Option<Instant>,
    pub applied_width: i32,
    pub applied_height: i32,
    pub backdrop_color: [f32; 4],
    pub has_backdrop_color: bool,
    pub aspect: Option<(i32, i32)>,
}

impl Screen {
    pub fn top_inset(&self) -> i32 {
        if self.fullscreen { 0 } else { TITLEBAR_H }
    }
}

pub struct CalState {
    pub in_lut: bool,
    pub active: bool,
    pub gamma: f32,
    pub contrast: f32,
    pub gain: [f32; 3],
}

pub struct LiviState {
    pub running: bool,
    pub display_handle: DisplayHandle,
    pub ui_socket: String,

    pub compositor_state: CompositorState,
    pub xdg_shell_state: XdgShellState,
    pub _xdg_decoration_state: XdgDecorationState,
    pub seat_state: SeatState<LiviState>,
    pub seat: Seat<LiviState>,
    pub data_device_state: DataDeviceState,
    pub shm_state: ShmState,
    pub dmabuf_state: DmabufState,
    pub render_node: Option<std::path::PathBuf>,
    pub syncobj_state: Option<DrmSyncobjState>,
    pub loop_handle: LoopHandle<'static, LiviState>,
    pub _viewporter_state: ViewporterState,
    pub _cursor_shape_state: CursorShapeManagerState,

    pub output_app_id: String,
    pub screens: Vec<Screen>,
    pub toplevels: Vec<TopLevel>,
    pub video_order: Vec<usize>,
    pub pending_video_tags: VecDeque<(String, u64)>,
    pub video_cfgs: Vec<VideoCfg>,

    pub cal: CalState,
    pub renderer: Option<smithay::backend::renderer::gles::GlesRenderer>,
    pub cal_program: Option<smithay::backend::renderer::gles::GlesTexProgram>,
    pub host: HostState,
    pub drm: Option<crate::drm::DrmState>,
    pub fingers: crate::projection::Fingers,

    pub ctrl_client: Option<std::os::unix::net::UnixStream>,
    pub ctrl_buf: String,
    pub ctrl_out: Vec<u8>,
    pub ctrl_path: Option<String>,

    pub startup_cmd: Option<String>,
    pub startup_pid: Option<nix::unistd::Pid>,
    pub full_restart: bool,
    pub restart_deadline: Option<Instant>,
    pub saved_argv: Vec<OsString>,
}

#[derive(Default)]
pub struct ClientState {
    pub compositor_state: smithay::wayland::compositor::CompositorClientState,
}

impl ClientData for ClientState {}

impl LiviState {
    pub fn new(
        event_loop: &mut EventLoop<'static, LiviState>,
        startup_cmd: Option<String>,
    ) -> Self {
        let mut display: Display<LiviState> = Display::new().expect("wayland display");
        let dh = display.handle();

        let compositor_state = CompositorState::new::<Self>(&dh);
        let xdg_shell_state = XdgShellState::new::<Self>(&dh);
        let xdg_decoration_state = XdgDecorationState::new::<Self>(&dh);
        let mut seat_state = SeatState::new();
        let seat = seat_state.new_wl_seat(&dh, "seat0");
        let data_device_state = DataDeviceState::new::<Self>(&dh);
        let shm_state = ShmState::new::<Self>(&dh, vec![]);
        let dmabuf_state = DmabufState::new();
        let viewporter_state = ViewporterState::new::<Self>(&dh);
        let cursor_shape_state = CursorShapeManagerState::new::<Self>(&dh);

        let source = ListeningSocketSource::new_auto().expect("wayland socket");
        let ui_socket = source.socket_name().to_string_lossy().into_owned();
        let loop_handle = event_loop.handle();
        loop_handle
            .insert_source(source, |client_stream, _, state: &mut LiviState| {
                let _ = state
                    .display_handle
                    .insert_client(client_stream, std::sync::Arc::new(ClientState::default()));
            })
            .expect("insert wayland socket");

        let poll_fd = display.backend().poll_fd().try_clone_to_owned().unwrap();
        loop_handle
            .insert_source(Generic::new(poll_fd, Interest::READ, Mode::Level), {
                let mut display = display;
                move |_, _, state: &mut LiviState| {
                    display.dispatch_clients(state).unwrap();
                    Ok(PostAction::Continue)
                }
            })
            .expect("insert display source");

        let kiosk = std::env::var("LIVI_KIOSK")
            .map(|v| v != "0")
            .unwrap_or(false);
        let roles = std::env::var("LIVI_SCREENS").unwrap_or_default();
        let roles = if roles.is_empty() {
            "main,cluster,aux"
        } else {
            &roles
        };
        let screens: Vec<Screen> = roles
            .split(',')
            .filter(|r| !r.is_empty())
            .take(8)
            .enumerate()
            .map(|(i, role)| Screen {
                role: role.to_string(),
                x: i as i32 * SCREEN_X_SLOT,
                width: 0,
                height: 0,
                req_width: 0,
                req_height: 0,
                fullscreen: kiosk,
                output: None,
                resize_pending: None,
                applied_width: 0,
                applied_height: 0,
                backdrop_color: [0.0, 0.0, 0.0, 1.0],
                has_backdrop_color: false,
                aspect: None,
            })
            .collect();

        let output_app_id =
            std::env::var("LIVI_OUTPUT_APP_ID").unwrap_or_else(|_| "dev.f-io.livi".to_string());

        Self {
            running: true,
            display_handle: dh,
            ui_socket,
            compositor_state,
            xdg_shell_state,
            _xdg_decoration_state: xdg_decoration_state,
            seat_state,
            seat,
            data_device_state,
            shm_state,
            dmabuf_state,
            render_node: None,
            syncobj_state: None,
            loop_handle: loop_handle.clone(),
            _viewporter_state: viewporter_state,
            _cursor_shape_state: cursor_shape_state,
            output_app_id,
            screens,
            toplevels: Vec::new(),
            video_order: Vec::new(),
            pending_video_tags: VecDeque::new(),
            video_cfgs: Vec::new(),
            cal: CalState {
                in_lut: false,
                active: false,
                gamma: 1.0,
                contrast: 1.0,
                gain: [1.0, 1.0, 1.0],
            },
            renderer: None,
            cal_program: None,
            host: HostState::new(),
            drm: None,
            fingers: Default::default(),
            ctrl_client: None,
            ctrl_buf: String::new(),
            ctrl_out: Vec::new(),
            ctrl_path: std::env::var("LIVI_COMPOSITOR_CTRL")
                .ok()
                .filter(|p| !p.is_empty()),
            startup_cmd,
            startup_pid: None,
            full_restart: false,
            restart_deadline: None,
            saved_argv: std::env::args_os().collect(),
        }
    }

    pub fn screen_idx_by_role(&self, role: &str) -> Option<usize> {
        self.screens.iter().position(|s| s.role == role)
    }

    pub fn cfg_for_tag(&mut self, tag: &str) -> &mut VideoCfg {
        if let Some(i) = self.video_cfgs.iter().position(|c| c.tag == tag) {
            return &mut self.video_cfgs[i];
        }
        self.video_cfgs.push(VideoCfg {
            tag: tag.to_string(),
            ..Default::default()
        });
        self.video_cfgs.last_mut().unwrap()
    }

    pub fn find_video_by_tag(&self, tag: &str) -> Option<usize> {
        self.toplevels
            .iter()
            .position(|t| t.kind == Kind::Video && t.tag == tag && !t.video_status.retired)
    }

    pub fn after_dispatch(&mut self) {
        if let Some(deadline) = self.restart_deadline
            && Instant::now() >= deadline
        {
            crate::spawn::force_restart(self);
        }
        crate::projection::flush(self);
        crate::host::apply_settled_resizes(self);
        crate::host::pump(self);
        self.display_handle.flush_clients().ok();
    }
}

pub fn role_title(role: &str) -> &str {
    match role {
        "main" => "LIVI",
        "cluster" => "Cluster",
        "aux" => "Auxiliary",
        other => other,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn released_surface_cannot_restore_ready_or_invalidate_its_replacement() {
        let mut old = VideoStatus::default();
        assert_eq!(old.committed(true), Some(true));
        assert!(old.retire());
        assert!(!old.ready);
        assert_eq!(
            old.committed(true),
            None,
            "late old-phone frames stay retired"
        );
        assert!(
            !old.retire(),
            "old destruction cannot clear a replacement's readiness"
        );

        let mut replacement = VideoStatus::default();
        assert!(!replacement.ready);
        assert_eq!(replacement.committed(true), Some(true));
        assert_eq!(old.committed(false), None);
        assert!(replacement.ready);
        assert!(!replacement.retired);
    }

    #[test]
    fn box_changes_wait_for_matching_new_buffers_and_identical_replay_stays_ready() {
        let mut cfg = VideoCfg::default();
        let mut video = VideoStatus {
            expected_size: Some((1600, 600)),
            ..Default::default()
        };
        assert_eq!(video.buffer_committed(true, (1600, 600)), Some(true));

        let tile = VideoBox::new(0.5, 0.2, 0.25, 0.5);
        assert!(cfg.set_destination("main", tile));
        video.invalidate_placement();
        video.expected_size = Some((400, 300));
        assert!(!video.ready);
        assert_eq!(
            video.buffer_committed(true, (1600, 600)),
            None,
            "old full-screen buffers stay covered"
        );
        assert_eq!(video.buffer_committed(true, (400, 300)), Some(true));

        if cfg.set_destination("main", tile) {
            video.invalidate_placement();
        }
        assert!(video.ready, "identical replay must not flicker");
        assert!(
            cfg.set_destination("cluster", tile),
            "moving to another output changes placement"
        );
        assert!(
            cfg.set_destination("main", None),
            "clearing restores the full-screen destination"
        );
        video.invalidate_placement();
        video.expected_size = Some((1600, 600));
        assert_eq!(video.buffer_committed(true, (400, 300)), None);
        assert_eq!(video.buffer_committed(true, (1600, 600)), Some(true));
        assert!(
            !cfg.set_destination("cluster", None),
            "an already cleared box has no destination output"
        );
    }

    #[test]
    fn rapid_viewport_revisions_keep_old_acknowledgments_and_pending_claims_on_their_own_token() {
        let mut cfg = VideoCfg::default();
        let mut video = VideoStatus::default();
        assert_eq!(cfg.accept_generation(1), Some(true));
        assert!(video.bind_generation(1, cfg.generation));
        let a0 = video.readiness_line("cluster-main", false);
        let a1 = video.readiness_line("cluster-main", true);

        assert_eq!(cfg.accept_generation(2), Some(true));
        assert!(video.advance_generation(2));
        assert!(!video.ready);
        assert_eq!(
            video.readiness_line("cluster-main", false),
            "videoready cluster-main 2 0\n"
        );
        assert_eq!(a0, "videoready cluster-main 1 0\n");
        assert_eq!(
            a1, "videoready cluster-main 1 1\n",
            "delayed A events cannot acknowledge B"
        );
        assert_eq!(
            cfg.accept_generation(1),
            None,
            "stale commands cannot roll back B"
        );
        assert_eq!(
            cfg.accept_generation(2),
            Some(false),
            "identical token replay needs no reset"
        );

        let mut cancelled = VideoStatus::default();
        assert!(cancelled.bind_generation(1, 1));
        cancelled.retire();
        assert!(!cancelled.bind_pending_generation(1, cfg.generation));
        assert!(
            !cancelled.advance_generation(2),
            "retired surfaces cannot be revived"
        );
        assert_eq!(cancelled.generation, 1);
        assert_eq!(cancelled.committed(true), None);
    }

    #[test]
    fn active_pending_claim_survives_a_viewport_revision_without_losing_its_acknowledgment() {
        let claim_generation = 1;
        let latest_layout = 2;
        let mut pending = VecDeque::from([(String::from("cluster-main"), claim_generation)]);
        let (tag, original) = pending.pop_front().unwrap();
        let mut video = VideoStatus::default();
        assert!(video.bind_pending_generation(original, latest_layout));
        assert_eq!(
            format!("bound {tag} {original}\n"),
            "bound cluster-main 1\n"
        );
        assert_eq!(
            video.readiness_line(&tag, false),
            "videoready cluster-main 2 0\n"
        );
        video.expected_size = Some((400, 300));
        assert_eq!(video.buffer_committed(true, (400, 300)), Some(true));
        assert_eq!(
            video.readiness_line(&tag, true),
            "videoready cluster-main 2 1\n"
        );
        assert!(!video.retired);
    }
}
