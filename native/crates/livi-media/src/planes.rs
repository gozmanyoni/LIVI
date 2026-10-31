// SPDX-FileCopyrightText: 2025-2026 Lasse Heitgres
// SPDX-License-Identifier: GPL-3.0-or-later
// Additional terms: see NOTICE

use std::collections::HashSet;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};

use livi_core_proto::state::VideoRect;
use livi_host_proto::{
    CAMERA_TAG, CLUSTER_PLANE_MIN, CameraOrder, MAIN_TAG, PLANE_CAMERA, PLANE_MAIN, PLANE_VIDEO,
    SCREENS, VIDEO_TAG,
};
use tokio::sync::{broadcast, watch};

use crate::compositor::CompositorControl;
use crate::gst_host::{Codec, GstHost};

const TAG: &str = MAIN_TAG;
const SCREEN: &str = SCREENS[0];

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Crop {
    pub crop_l: f64,
    pub crop_t: f64,
    pub vis_w: f64,
    pub vis_h: f64,
    pub tier_w: f64,
    pub tier_h: f64,
}

fn content_area(frame: (u32, u32), user: (u32, u32)) -> (f64, f64) {
    let ratio = |(w, h): (u32, u32)| f64::from(w.max(1)) / f64::from(h.max(1));
    let even = |v: f64| ((v.floor() as i64) & !1).max(2) as f64;
    let (user_ar, frame_ar) = (ratio(user), ratio(frame));
    if user_ar <= frame_ar {
        (even(f64::from(frame.1) * user_ar), f64::from(frame.1))
    } else {
        (f64::from(frame.0), even(f64::from(frame.0) / user_ar))
    }
}

pub fn crop_for(tier: (u32, u32), display: (u32, u32)) -> Option<Crop> {
    let (tw, th) = tier;
    if tw == 0 || th == 0 || display.0 == 0 || display.1 == 0 {
        return None;
    }
    let (cw, ch) = content_area(tier, display);
    Some(Crop {
        crop_l: ((f64::from(tw) - cw) / 2.0).max(0.0),
        crop_t: ((f64::from(th) - ch) / 2.0).max(0.0),
        vis_w: cw,
        vis_h: ch,
        tier_w: f64::from(tw),
        tier_h: f64::from(th),
    })
}

fn same_stream(codec: Option<Codec>, data: &[u8], next: Codec, atom: &[u8]) -> bool {
    codec == Some(next) && data == atom
}

#[derive(Default)]
struct State {
    started: bool,
    claiming: bool,
    codec: Option<Codec>,
    codec_data: Vec<u8>,
    generation: u64,
}

pub struct MainPlane {
    gst: GstHost,
    comp: CompositorControl,
    state: Mutex<State>,
    created: broadcast::Sender<()>,
}

impl MainPlane {
    pub fn new(gst: GstHost, comp: CompositorControl) -> Arc<Self> {
        let (created, _) = broadcast::channel(8);
        Arc::new(Self {
            gst,
            comp,
            state: Mutex::new(State::default()),
            created,
        })
    }

    fn state(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(|e| e.into_inner())
    }

    pub fn player_created(&self) -> broadcast::Receiver<()> {
        self.created.subscribe()
    }

    pub fn prepare(self: &Arc<Self>, codec: Codec, atom: Vec<u8>) {
        let generation = {
            let st = self.state();
            if (st.started || st.claiming) && same_stream(st.codec, &st.codec_data, codec, &atom) {
                return;
            }
            drop(st);
            self.dispose();
            let mut st = self.state();
            st.claiming = true;
            st.codec = Some(codec);
            st.codec_data = atom;
            st.generation
        };
        let plane = self.clone();
        tokio::spawn(async move {
            plane.comp.claim(TAG).await;
            let data = {
                let mut st = plane.state();
                if st.generation != generation {
                    return;
                }
                st.claiming = false;
                st.started = true;
                st.codec = Some(codec);
                st.codec_data.clone()
            };
            plane.gst.create_player(PLANE_MAIN, codec, &data);
            let _ = plane.created.send(());
        });
    }

    pub fn set_crop(&self, crop: Option<Crop>) {
        let c = crop.unwrap_or(Crop {
            crop_l: 0.0,
            crop_t: 0.0,
            vis_w: 0.0,
            vis_h: 0.0,
            tier_w: 0.0,
            tier_h: 0.0,
        });
        self.comp.videocfg(
            TAG, SCREEN, c.crop_l, c.crop_t, c.vis_w, c.vis_h, c.tier_w, c.tier_h,
        );
    }

    pub fn set_visible(&self, visible: bool) {
        self.comp.videoshow(TAG, visible);
    }

    pub fn dispose(&self) {
        let (started, claiming) = {
            let mut st = self.state();
            let claiming = std::mem::take(&mut st.claiming);
            st.generation += 1;
            st.codec = None;
            (std::mem::take(&mut st.started), claiming)
        };
        if started || claiming {
            self.comp.release(TAG);
        }
        if started {
            self.gst.stop(PLANE_MAIN);
        }
    }
}

pub struct VideoPlane {
    gst: GstHost,
    comp: CompositorControl,
    stops: AtomicU64,
    visibility: Mutex<VideoVisibility>,
}

struct VideoVisibility {
    allowed: bool,
    presented: bool,
}

impl VideoPlane {
    pub fn new(gst: GstHost, comp: CompositorControl) -> Arc<Self> {
        Arc::new(Self {
            gst,
            comp,
            stops: AtomicU64::new(0),
            visibility: Mutex::new(VideoVisibility {
                allowed: true,
                presented: false,
            }),
        })
    }

    pub fn turn(&self) -> u64 {
        self.stops.load(Ordering::SeqCst)
    }

    pub async fn play(&self, turn: u64, url: &str, audio_device: &str) -> bool {
        if self.turn() != turn {
            return false;
        }
        self.comp
            .videocfg(VIDEO_TAG, SCREEN, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0);
        self.update_visibility(|_| {});
        self.comp.claim(VIDEO_TAG).await;
        if self.turn() != turn {
            return false;
        }
        self.update_visibility(|_| {});
        self.gst.play_url(PLANE_VIDEO, url, audio_device);
        true
    }

    pub fn set_rate(&self, rate: f64) {
        self.gst.set_play_rate(PLANE_VIDEO, rate);
    }

    pub fn seek(&self, seconds: f64) {
        self.gst.seek(PLANE_VIDEO, seconds);
    }

    pub fn set_muted(&self, muted: bool) {
        self.gst.set_play_muted(PLANE_VIDEO, muted);
    }

    pub fn set_shown(&self, shown: bool) {
        self.update_visibility(|visibility| visibility.allowed = shown);
    }

    /// Playback may continue behind native menus, but its picture belongs only
    /// to full-screen projection, never to a transparent navigation tile.
    pub fn set_presented(&self, presented: bool) {
        self.update_visibility(|visibility| visibility.presented = presented);
    }

    fn update_visibility(&self, update: impl FnOnce(&mut VideoVisibility)) {
        let mut visibility = self.visibility.lock().unwrap_or_else(|e| e.into_inner());
        update(&mut visibility);
        // Serialize both the state change and its command: an allowance update
        // racing with Home must not enqueue a stale visible=true afterward.
        self.comp
            .videoshow(VIDEO_TAG, visibility.allowed && visibility.presented);
    }

    pub fn stop(&self) {
        self.stops.fetch_add(1, Ordering::SeqCst);
        self.comp.release(VIDEO_TAG);
        self.comp.videoshow(VIDEO_TAG, false);
        self.gst.stop(PLANE_VIDEO);
    }
}

pub struct CameraPlane {
    gst: GstHost,
    comp: CompositorControl,
    stops: AtomicU64,
}

impl CameraPlane {
    pub fn new(gst: GstHost, comp: CompositorControl) -> Arc<Self> {
        Arc::new(Self {
            gst,
            comp,
            stops: AtomicU64::new(0),
        })
    }

    pub fn turn(&self) -> u64 {
        self.stops.load(Ordering::SeqCst)
    }

    pub async fn open(&self, turn: u64, order: &CameraOrder) -> bool {
        if self.turn() != turn {
            return false;
        }
        self.comp
            .videocfg(CAMERA_TAG, SCREEN, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0);
        self.comp.videoshow(CAMERA_TAG, true);
        self.comp.claim(CAMERA_TAG).await;
        if self.turn() != turn {
            return false;
        }
        self.gst.open_camera(PLANE_CAMERA, order);
        true
    }

    pub fn close(&self) {
        self.stops.fetch_add(1, Ordering::SeqCst);
        self.comp.release(CAMERA_TAG);
        self.comp.videoshow(CAMERA_TAG, false);
        self.gst.stop(PLANE_CAMERA);
    }
}

const CLUSTER_SCREENS: [&str; 3] = SCREENS;

fn cluster_tag(i: usize) -> String {
    livi_host_proto::cluster_tag(CLUSTER_SCREENS[i])
}

fn cluster_plane(i: usize) -> u32 {
    CLUSTER_PLANE_MIN + i as u32
}

#[derive(Default)]
struct Slot {
    shown: bool,
    started: bool,
    claiming: bool,
    generation: u64,
}

#[derive(Default)]
struct ClusterState {
    codec: Option<Codec>,
    codec_data: Vec<u8>,
    crop: Option<Crop>,
    main_viewport: Option<VideoRect>,
    slots: [Slot; 3],
}

pub struct ClusterPlanes {
    gst: GstHost,
    comp: CompositorControl,
    state: Mutex<ClusterState>,
    created: broadcast::Sender<()>,
}

impl ClusterPlanes {
    pub fn new(gst: GstHost, comp: CompositorControl) -> Arc<Self> {
        let (created, _) = broadcast::channel(8);
        Arc::new(Self {
            gst,
            comp,
            state: Mutex::new(ClusterState::default()),
            created,
        })
    }

    fn state(&self) -> MutexGuard<'_, ClusterState> {
        self.state.lock().unwrap_or_else(|e| e.into_inner())
    }

    pub fn player_created(&self) -> broadcast::Receiver<()> {
        self.created.subscribe()
    }

    pub fn video_ready(&self) -> watch::Receiver<HashSet<String>> {
        self.comp.video_ready()
    }

    /// Place the map copy on the main UI without changing its source crop.
    pub fn set_main_viewport(&self, viewport: Option<VideoRect>) -> bool {
        let viewport = viewport.filter(|rect| rect.valid());
        let mut st = self.state();
        if st.main_viewport == viewport {
            return false;
        }
        st.main_viewport = viewport;
        self.comp
            .videobox(&cluster_tag(0), CLUSTER_SCREENS[0], viewport);
        true
    }

    pub fn prepare(self: &Arc<Self>, codec: Codec, atom: Vec<u8>) {
        let restart = {
            let mut st = self.state();
            let same = same_stream(st.codec, &st.codec_data, codec, &atom);
            st.codec = Some(codec);
            st.codec_data = atom;
            !same
        };
        for i in 0..CLUSTER_SCREENS.len() {
            if restart {
                self.stop(i);
            }
            self.start(i);
        }
    }

    pub fn show_on(self: &Arc<Self>, shown: [bool; 3]) {
        for (i, on) in shown.into_iter().enumerate() {
            let was = std::mem::replace(&mut self.state().slots[i].shown, on);
            if on && !was {
                self.start(i);
            } else if !on && was {
                self.stop(i);
            }
        }
    }

    pub fn set_crop(&self, crop: Option<Crop>) {
        let started: Vec<usize> = {
            let mut st = self.state();
            st.crop = crop;
            (0..CLUSTER_SCREENS.len())
                .filter(|&i| st.slots[i].started)
                .collect()
        };
        for i in started {
            self.place(i, crop);
        }
    }

    pub fn dispose(&self) {
        self.state().codec = None;
        for i in 0..CLUSTER_SCREENS.len() {
            self.stop(i);
        }
    }

    fn start(self: &Arc<Self>, i: usize) {
        let generation = {
            let mut st = self.state();
            let known = st.codec.is_some();
            let slot = &mut st.slots[i];
            if !known || !slot.shown || slot.started || slot.claiming {
                return;
            }
            slot.claiming = true;
            slot.generation
        };
        let planes = self.clone();
        tokio::spawn(async move {
            let tag = cluster_tag(i);
            if i == 0 {
                let st = planes.state();
                planes
                    .comp
                    .videobox(&tag, CLUSTER_SCREENS[i], st.main_viewport);
            }
            planes.comp.claim(&tag).await;
            let (codec, data, crop) = {
                let mut st = planes.state();
                let Some(codec) = st.codec else { return };
                let slot = &mut st.slots[i];
                if slot.generation != generation {
                    return;
                }
                slot.claiming = false;
                slot.started = true;
                (codec, st.codec_data.clone(), st.crop)
            };
            planes.place(i, crop);
            planes.comp.videoshow(&tag, true);
            planes.gst.create_player(cluster_plane(i), codec, &data);
            let _ = planes.created.send(());
        });
    }

    fn stop(&self, i: usize) {
        let (started, claiming) = {
            let mut st = self.state();
            let slot = &mut st.slots[i];
            let claiming = std::mem::take(&mut slot.claiming);
            slot.generation += 1;
            (std::mem::take(&mut slot.started), claiming)
        };
        let tag = cluster_tag(i);
        if started || claiming {
            self.comp.release(&tag);
        }
        if started {
            self.comp.videoshow(&tag, false);
            self.gst.stop(cluster_plane(i));
        }
    }

    fn place(&self, i: usize, crop: Option<Crop>) {
        let c = crop.unwrap_or(Crop {
            crop_l: 0.0,
            crop_t: 0.0,
            vis_w: 0.0,
            vis_h: 0.0,
            tier_w: 0.0,
            tier_h: 0.0,
        });
        self.comp.videocfg(
            &cluster_tag(i),
            CLUSTER_SCREENS[i],
            c.crop_l,
            c.crop_t,
            c.vis_w,
            c.vis_h,
            c.tier_w,
            c.tier_h,
        );
    }
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
    use tokio::net::{UnixListener, UnixStream};

    use super::*;
    use crate::test_dir::TempDir;

    #[test]
    fn carplay_streams_at_the_display_size_need_no_crop() {
        let c = crop_for((1280, 720), (1280, 720)).unwrap();
        assert_eq!(
            (c.crop_l, c.crop_t, c.vis_w, c.vis_h),
            (0.0, 0.0, 1280.0, 720.0)
        );
        assert_eq!(crop_for((0, 720), (1280, 720)), None);
    }

    #[test]
    fn a_wider_display_crops_the_frame_top_and_bottom() {
        let c = crop_for((1280, 720), (1280, 480)).unwrap();
        assert_eq!((c.vis_w, c.vis_h), (1280.0, 480.0));
        assert_eq!(c.crop_t, 120.0);
        let c = crop_for((1280, 720), (800, 600)).unwrap();
        assert_eq!((c.vis_w, c.vis_h, c.crop_l), (960.0, 720.0, 160.0));
    }

    #[test]
    fn only_the_same_stream_keeps_the_running_player() {
        assert!(same_stream(
            Some(Codec::H265),
            &[1, 2],
            Codec::H265,
            &[1, 2]
        ));
        assert!(!same_stream(Some(Codec::H265), &[1, 2], Codec::H265, &[]));
        assert!(!same_stream(
            Some(Codec::H265),
            &[1, 2],
            Codec::H265,
            &[3, 4]
        ));
        assert!(!same_stream(Some(Codec::H264), &[], Codec::H265, &[]));
        assert!(!same_stream(None, &[], Codec::H265, &[]));
    }

    #[tokio::test]
    async fn the_player_is_created_after_the_claim_and_only_once() {
        let dir = TempDir::new();
        let comp_listener = UnixListener::bind(dir.0.join("ctrl")).unwrap();
        let gst = GstHost::new(dir.0.join("gst.sock"));
        let plane = MainPlane::new(gst.clone(), CompositorControl::connect(dir.0.join("ctrl")));
        let mut created = plane.player_created();

        plane.set_visible(true);
        plane.prepare(Codec::H264, vec![1, 2]);
        plane.prepare(Codec::H264, vec![1, 2]);
        tokio::time::timeout(Duration::from_secs(5), created.recv())
            .await
            .unwrap()
            .unwrap();

        let (ctrl, _) = tokio::time::timeout(Duration::from_secs(5), comp_listener.accept())
            .await
            .unwrap()
            .unwrap();
        let mut lines = BufReader::new(ctrl).lines();
        let mut seen = Vec::new();
        while seen.len() < 2 {
            seen.push(
                tokio::time::timeout(Duration::from_secs(5), lines.next_line())
                    .await
                    .unwrap()
                    .unwrap()
                    .unwrap(),
            );
        }
        assert!(seen.contains(&"claim main".to_string()));
        assert!(seen.contains(&"videoshow main 1".to_string()));

        let mut host = UnixStream::connect(dir.0.join("gst.sock")).await.unwrap();
        let mut buf = vec![0u8; 64];
        let n = tokio::time::timeout(Duration::from_secs(5), host.read(&mut buf))
            .await
            .unwrap()
            .unwrap();
        let mut framer = livi_host_proto::Framer::new();
        framer.push(&buf[..n]);
        let msg = framer.next_message().unwrap();
        assert_eq!((msg.op, msg.id), (1, PLANE_MAIN));
        assert_eq!(msg.rest, [&[4u8][..], b"h264", &[1, 2]].concat());
        assert!(created.try_recv().is_err());
    }

    async fn next_line<R: tokio::io::AsyncRead + Unpin>(
        lines: &mut tokio::io::Lines<BufReader<R>>,
    ) -> String {
        tokio::time::timeout(Duration::from_secs(5), lines.next_line())
            .await
            .unwrap()
            .unwrap()
            .unwrap()
    }

    async fn next_msg(
        host: &mut UnixStream,
        framer: &mut livi_host_proto::Framer,
    ) -> livi_host_proto::Message {
        loop {
            if let Some(msg) = framer.next_message() {
                return msg;
            }
            let mut buf = vec![0u8; 64];
            let n = tokio::time::timeout(Duration::from_secs(5), host.read(&mut buf))
                .await
                .unwrap()
                .unwrap();
            framer.push(&buf[..n]);
        }
    }

    #[tokio::test]
    async fn a_cluster_player_runs_on_each_screen_that_shows_it() {
        let dir = TempDir::new();
        let comp_listener = UnixListener::bind(dir.0.join("ctrl")).unwrap();
        let gst = GstHost::new(dir.0.join("gst.sock"));
        let planes =
            ClusterPlanes::new(gst.clone(), CompositorControl::connect(dir.0.join("ctrl")));
        let mut created = planes.player_created();

        planes.show_on([false, true, false]);
        planes.set_crop(None);
        planes.prepare(Codec::H264, vec![7]);
        tokio::time::timeout(Duration::from_secs(5), created.recv())
            .await
            .unwrap()
            .unwrap();

        let (ctrl, _) = tokio::time::timeout(Duration::from_secs(5), comp_listener.accept())
            .await
            .unwrap()
            .unwrap();
        let mut lines = BufReader::new(ctrl).lines();
        let mut seen = Vec::new();
        for _ in 0..3 {
            seen.push(next_line(&mut lines).await);
        }
        assert!(seen.contains(&"claim cluster-cluster".to_string()));
        assert!(seen.contains(&"videocfg cluster-cluster cluster 0 0 0 0 0 0".to_string()));
        assert!(seen.contains(&"videoshow cluster-cluster 1".to_string()));

        let mut host = UnixStream::connect(dir.0.join("gst.sock")).await.unwrap();
        let mut framer = livi_host_proto::Framer::new();
        let msg = next_msg(&mut host, &mut framer).await;
        assert_eq!((msg.op, msg.id), (1, CLUSTER_PLANE_MIN + 1));

        planes.show_on([false, false, false]);
        let mut after = Vec::new();
        while !after.contains(&"videoshow cluster-cluster 0".to_string()) {
            after.push(next_line(&mut lines).await);
        }
        assert!(after.contains(&"unclaim cluster-cluster 1".to_string()));
        let msg = next_msg(&mut host, &mut framer).await;
        assert_eq!(msg.id, CLUSTER_PLANE_MIN + 1);
        assert_ne!(msg.op, 1);
        assert!(created.try_recv().is_err());
    }

    #[tokio::test]
    async fn the_main_map_viewport_survives_source_crop_changes_and_player_restarts() {
        let dir = TempDir::new();
        let comp_listener = UnixListener::bind(dir.0.join("ctrl")).unwrap();
        let gst = GstHost::new(dir.0.join("gst.sock"));
        let planes = ClusterPlanes::new(gst, CompositorControl::connect(dir.0.join("ctrl")));
        let mut created = planes.player_created();
        let viewport = VideoRect {
            x: 0.6,
            y: 0.2,
            width: 0.3,
            height: 0.5,
        };
        planes.set_main_viewport(Some(viewport));
        planes.show_on([true, false, false]);
        let crop = crop_for((1280, 720), (800, 600));
        planes.set_crop(crop);
        planes.prepare(Codec::H264, vec![7]);
        tokio::time::timeout(Duration::from_secs(5), created.recv())
            .await
            .unwrap()
            .unwrap();
        let (ctrl, _) = comp_listener.accept().await.unwrap();
        let mut lines = BufReader::new(ctrl).lines();
        let mut seen = Vec::new();
        while !seen.contains(&"videoshow cluster-main 1".to_string()) {
            seen.push(next_line(&mut lines).await);
        }
        assert!(seen.contains(&"videobox cluster-main main 0.6 0.2 0.3 0.5 2".to_string()));
        assert!(seen.contains(&"videocfg cluster-main main 160 0 960 720 1280 720".to_string()));

        planes.set_crop(None);
        while next_line(&mut lines).await != "videocfg cluster-main main 0 0 0 0 0 0" {}
        assert_eq!(planes.state().main_viewport, Some(viewport));
        planes.dispose();
        assert_eq!(planes.state().main_viewport, Some(viewport));
        planes.prepare(Codec::H265, vec![8]);
        tokio::time::timeout(Duration::from_secs(5), created.recv())
            .await
            .unwrap()
            .unwrap();
        while next_line(&mut lines).await != "videoshow cluster-main 0" {}
        let mut restarted = Vec::new();
        while !restarted.contains(&"videoshow cluster-main 1".to_string()) {
            restarted.push(next_line(&mut lines).await);
        }
        assert!(restarted.contains(&"videobox cluster-main main 0.6 0.2 0.3 0.5 4".to_string()));
        assert_eq!(planes.state().crop, None);

        planes.set_main_viewport(None);
        while next_line(&mut lines).await != "videobox cluster-main main - 5" {}
        assert_eq!(planes.state().crop, None);
    }

    #[tokio::test]
    async fn changing_the_map_viewport_invalidates_old_frames_but_duplicate_placement_does_not() {
        let dir = TempDir::new();
        let listener = UnixListener::bind(dir.0.join("ctrl")).unwrap();
        let comp = CompositorControl::connect(dir.0.join("ctrl"));
        let planes = ClusterPlanes::new(GstHost::new(dir.0.join("gst.sock")), comp.clone());
        let mut ready = planes.video_ready();
        let (ctrl, _) = listener.accept().await.unwrap();
        let (rd, mut wr) = ctrl.into_split();
        let mut lines = BufReader::new(rd).lines();
        wr.write_all(b"videoready cluster-main 1\n").await.unwrap();
        tokio::time::timeout(
            Duration::from_secs(5),
            ready.wait_for(|tags| tags.contains("cluster-main")),
        )
        .await
        .unwrap()
        .unwrap();

        let viewport = VideoRect {
            x: 0.6,
            y: 0.2,
            width: 0.3,
            height: 0.5,
        };
        assert!(planes.set_main_viewport(Some(viewport)));
        assert!(
            !ready.borrow().contains("cluster-main"),
            "a new box immediately invalidates the old frame"
        );
        assert_eq!(
            next_line(&mut lines).await,
            "videobox cluster-main main 0.6 0.2 0.3 0.5 1"
        );
        wr.write_all(b"videoready cluster-main 1\nvideoready cluster-aux 1\n")
            .await
            .unwrap();
        tokio::time::timeout(
            Duration::from_secs(5),
            ready.wait_for(|tags| tags.contains("cluster-aux")),
        )
        .await
        .unwrap()
        .unwrap();
        assert!(
            !ready.borrow().contains("cluster-main"),
            "an old queued frame is suppressed until the box acknowledgement"
        );
        wr.write_all(b"videoready cluster-main 1 0\nvideoready cluster-main 1 1\n")
            .await
            .unwrap();
        tokio::time::timeout(
            Duration::from_secs(5),
            ready.wait_for(|tags| tags.contains("cluster-main")),
        )
        .await
        .unwrap()
        .unwrap();

        assert!(!planes.set_main_viewport(Some(viewport)));
        assert!(
            ready.borrow().contains("cluster-main"),
            "duplicate service placement preserves the fresh frame"
        );
        comp.backdrop("#000000");
        assert_eq!(
            next_line(&mut lines).await,
            "videobox cluster-main main 0.6 0.2 0.3 0.5 1"
        );
        assert_eq!(next_line(&mut lines).await, "backdrop 0 0 0");
        assert!(planes.set_main_viewport(None));
        assert!(!ready.borrow().contains("cluster-main"));
    }

    #[tokio::test]
    async fn the_video_plane_lies_over_the_main_screen_until_it_stops() {
        let dir = TempDir::new();
        let comp_listener = UnixListener::bind(dir.0.join("ctrl")).unwrap();
        let gst = GstHost::new(dir.0.join("gst.sock"));
        let plane = VideoPlane::new(gst.clone(), CompositorControl::connect(dir.0.join("ctrl")));
        plane.set_presented(true);

        assert!(
            plane
                .play(plane.turn(), "http://127.0.0.1:9/i.m3u8", "car")
                .await
        );
        let (ctrl, _) = tokio::time::timeout(Duration::from_secs(5), comp_listener.accept())
            .await
            .unwrap()
            .unwrap();
        let mut lines = BufReader::new(ctrl).lines();
        let mut seen = Vec::new();
        while ![
            "claim video",
            "videocfg video main 0 0 0 0 0 0",
            "videoshow video 1",
        ]
        .iter()
        .all(|line| seen.iter().any(|seen| seen == line))
        {
            seen.push(next_line(&mut lines).await);
        }
        assert!(seen.contains(&"videocfg video main 0 0 0 0 0 0".to_string()));
        assert!(seen.contains(&"videoshow video 1".to_string()));
        assert!(seen.contains(&"claim video".to_string()));

        let mut host = UnixStream::connect(dir.0.join("gst.sock")).await.unwrap();
        let mut framer = livi_host_proto::Framer::new();
        let msg = next_msg(&mut host, &mut framer).await;
        assert_eq!((msg.op, msg.id), (22, PLANE_VIDEO));
        assert_eq!(
            livi_host_proto::parse_url_body(&msg.rest),
            Some(("http://127.0.0.1:9/i.m3u8".into(), "car".into()))
        );

        plane.set_rate(0.0);
        plane.seek(3.5);
        plane.set_muted(true);
        let rate = next_msg(&mut host, &mut framer).await;
        assert_eq!((rate.op, rate.rest), (23, 0.0f64.to_le_bytes().to_vec()));
        let seek = next_msg(&mut host, &mut framer).await;
        assert_eq!((seek.op, seek.rest), (24, 3.5f64.to_le_bytes().to_vec()));
        let mute = next_msg(&mut host, &mut framer).await;
        assert_eq!((mute.op, mute.rest), (25, vec![1]));

        plane.stop();
        let mut after = Vec::new();
        while !after.contains(&"videoshow video 0".to_string()) {
            after.push(next_line(&mut lines).await);
        }
        let msg = next_msg(&mut host, &mut framer).await;
        assert_eq!((msg.op, msg.id), (3, PLANE_VIDEO));

        let turn = plane.turn();
        plane.stop();
        assert!(
            !plane.play(turn, "http://127.0.0.1:9/i.m3u8", "car").await,
            "stopped first"
        );
        let msg = next_msg(&mut host, &mut framer).await;
        assert_eq!(
            (msg.op, msg.id),
            (3, PLANE_VIDEO),
            "no play behind the stop"
        );
    }

    #[tokio::test]
    async fn a_hidden_video_plane_stays_hidden_for_the_next_play() {
        let dir = TempDir::new();
        let comp_listener = UnixListener::bind(dir.0.join("ctrl")).unwrap();
        let gst = GstHost::new(dir.0.join("gst.sock"));
        let plane = VideoPlane::new(gst.clone(), CompositorControl::connect(dir.0.join("ctrl")));

        let (ctrl, _) = tokio::time::timeout(Duration::from_secs(5), comp_listener.accept())
            .await
            .unwrap()
            .unwrap();
        let mut lines = BufReader::new(ctrl).lines();
        plane.set_shown(false);
        plane.set_presented(true);
        assert!(
            plane
                .play(plane.turn(), "http://127.0.0.1:9/i.m3u8", "")
                .await
        );
        plane.set_shown(true);
        let mut seen = Vec::new();
        while !seen.contains(&"videoshow video 1".to_string()) {
            seen.push(next_line(&mut lines).await);
        }
        let shows: Vec<&str> = seen
            .iter()
            .map(String::as_str)
            .filter(|l| l.starts_with("videoshow"))
            .collect();
        let (last, before) = shows.split_last().unwrap();
        assert_eq!(*last, "videoshow video 1");
        assert!(!before.is_empty() && before.iter().all(|l| *l == "videoshow video 0"));
        assert!(seen.contains(&"claim video".to_string()));
    }

    #[tokio::test]
    async fn native_home_hides_video_without_stopping_playback_or_changing_its_allowance() {
        let dir = TempDir::new();
        let listener = UnixListener::bind(dir.0.join("ctrl")).unwrap();
        let gst = GstHost::new(dir.0.join("gst.sock"));
        let plane = VideoPlane::new(gst.clone(), CompositorControl::connect(dir.0.join("ctrl")));
        let (ctrl, _) = listener.accept().await.unwrap();
        let mut lines = BufReader::new(ctrl).lines();

        // A play can start before the first service state update. Default to
        // hidden, and keep it hidden when parked-video allowance changes.
        assert!(
            plane
                .play(plane.turn(), "http://127.0.0.1:9/i.m3u8", "car")
                .await
        );
        plane.set_shown(false);
        plane.set_shown(true);
        plane.set_presented(false);
        let mut seen = Vec::new();
        while !seen.contains(&"claim video".to_string()) {
            seen.push(next_line(&mut lines).await);
        }
        assert!(seen.contains(&"videoshow video 0".to_string()));
        assert!(!seen.contains(&"videoshow video 1".to_string()));
        while next_line(&mut lines).await != "videoshow video 0" {}

        let mut host = UnixStream::connect(dir.0.join("gst.sock")).await.unwrap();
        let mut framer = livi_host_proto::Framer::new();
        let play = next_msg(&mut host, &mut framer).await;
        assert_eq!((play.op, play.id), (22, PLANE_VIDEO));

        // Full-screen projection shows the same running URL. Returning Home
        // only hides its picture; audio/player state and the map stay separate.
        plane.set_presented(true);
        while next_line(&mut lines).await != "videoshow video 1" {}
        plane.set_presented(false);
        while next_line(&mut lines).await != "videoshow video 0" {}
        plane.set_shown(false);
        while next_line(&mut lines).await != "videoshow video 0" {}
        plane.set_presented(true);
        while next_line(&mut lines).await != "videoshow video 0" {}
        plane.set_shown(true);
        while next_line(&mut lines).await != "videoshow video 1" {}
        plane.set_rate(0.0);
        let rate = next_msg(&mut host, &mut framer).await;
        assert_eq!(
            (rate.op, rate.id),
            (23, PLANE_VIDEO),
            "no stop/restart behind visibility changes"
        );
    }

    #[tokio::test]
    async fn a_play_waiting_for_its_claim_cannot_restore_old_fullscreen_visibility() {
        let dir = TempDir::new();
        let listener = UnixListener::bind(dir.0.join("ctrl")).unwrap();
        let comp = CompositorControl::connect(dir.0.join("ctrl"));
        let gst = GstHost::new(dir.0.join("gst.sock"));
        let plane = VideoPlane::new(gst, comp.clone());
        let (ctrl, _) = listener.accept().await.unwrap();
        let (rd, mut wr) = ctrl.into_split();
        let mut lines = BufReader::new(rd).lines();

        // Hold an unrelated in-flight window claim so playback really yields
        // while Home takes over, then allow the delayed video claim to finish.
        comp.claim("main").await;
        assert_eq!(next_line(&mut lines).await, "claim main");
        plane.set_presented(true);
        let playing = plane.clone();
        let play = tokio::spawn(async move {
            playing
                .play(playing.turn(), "http://127.0.0.1:9/i.m3u8", "")
                .await
        });
        while next_line(&mut lines).await != "videocfg video main 0 0 0 0 0 0" {}
        while next_line(&mut lines).await != "videoshow video 1" {}
        assert!(!play.is_finished());
        plane.set_presented(false);
        while next_line(&mut lines).await != "videoshow video 0" {}
        wr.write_all(b"bound main\n").await.unwrap();
        assert!(
            tokio::time::timeout(Duration::from_secs(5), play)
                .await
                .unwrap()
                .unwrap()
        );
        plane.set_shown(false);
        plane.set_shown(true);
        comp.videoshow("test-barrier", true);
        let mut seen = Vec::new();
        loop {
            let line = next_line(&mut lines).await;
            if line == "videoshow test-barrier 1" {
                break;
            }
            seen.push(line);
        }
        assert!(seen.contains(&"claim video".to_string()));
        assert!(seen.contains(&"videoshow video 0".to_string()));
        assert!(
            !seen.contains(&"videoshow video 1".to_string()),
            "late claim/reopen and allowance changes cannot reveal playback in Home"
        );
    }
}
