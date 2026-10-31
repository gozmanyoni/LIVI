// SPDX-FileCopyrightText: 2026 OpenNBT contributors
// SPDX-License-Identifier: GPL-3.0-or-later
// Additional terms: see NOTICE

use std::cell::Cell;
use std::rc::Rc;

use livi_core_client::Client;
use livi_core_proto::state::{MapPreview, VideoRect};
use slint::ComponentHandle;

use crate::{AppWindow, MapViewport};

fn requested(view: MapViewport) -> Option<VideoRect> {
    let rect = VideoRect {
        x: f64::from(view.x),
        y: f64::from(view.y),
        width: f64::from(view.width),
        height: f64::from(view.height),
    };
    (view.shown && rect.valid()).then_some(rect)
}

fn same_rect(a: Option<VideoRect>, b: Option<VideoRect>) -> bool {
    // Requests originate in Slint's f32 geometry and return unchanged through
    // JSON/core. Keep this identical to Slint's confirmed-viewport equality so
    // even subpixel resize updates are acknowledged rather than hidden forever.
    a == b
}

pub fn wire(ui: &AppWindow, client: &Client) {
    let weak = ui.as_weak();
    let client = client.clone();
    let last = Rc::new(Cell::new(None::<Option<VideoRect>>));
    ui.on_map_viewport_changed({
        let last = last.clone();
        move || {
            let Some(ui) = weak.upgrade() else { return };
            let next = requested(ui.get_map_viewport());
            if last.get().is_some_and(|old| same_rect(old, next)) {
                return;
            }
            // Cover the old video immediately. Only an acknowledgement for the
            // current viewport may expose the compositor's new buffer.
            ui.set_map_preview_ready(false);
            last.set(Some(next));
            client.map_preview(next);
        }
    });
    ui.invoke_map_viewport_changed();
}

pub fn apply(ui: &AppWindow, preview: MapPreview) {
    let wanted = requested(ui.get_map_viewport());
    ui.set_map_preview_confirmed(preview.viewport.map_or_else(MapViewport::default, |rect| {
        MapViewport {
            shown: true,
            x: rect.x as f32,
            y: rect.y as f32,
            width: rect.width as f32,
            height: rect.height as f32,
        }
    }));
    ui.set_map_preview_ready(
        preview.ready && wanted.is_some() && same_rect(wanted, preview.viewport),
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    fn viewport() -> MapViewport {
        MapViewport {
            shown: true,
            x: 0.1,
            y: 0.2,
            width: 0.3,
            height: 0.4,
        }
    }

    #[test]
    fn hidden_or_invalid_geometry_does_not_request_video() {
        let mut view = viewport();
        assert!(requested(view.clone()).is_some());
        view.shown = false;
        assert_eq!(requested(view.clone()), None);
        view.shown = true;
        view.width = f32::NAN;
        assert_eq!(requested(view.clone()), None);
        view.width = 2.0;
        assert_eq!(requested(view), None);
    }

    #[test]
    fn every_geometry_change_requires_an_acknowledgement() {
        let a = requested(viewport()).unwrap();
        let wire = serde_json::to_string(&a).unwrap();
        let echoed: VideoRect = serde_json::from_str(&wire).unwrap();
        assert!(same_rect(Some(a), Some(echoed)));
        let mut b = a;
        b.x += 1e-8;
        assert!(!same_rect(Some(a), Some(b)));
        b.x += 0.01;
        assert!(!same_rect(Some(a), Some(b)));
        assert!(!same_rect(Some(a), None));
    }
}
