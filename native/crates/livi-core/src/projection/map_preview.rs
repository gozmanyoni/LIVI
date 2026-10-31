// SPDX-FileCopyrightText: 2025-2026 Lasse Heitgres
// SPDX-License-Identifier: GPL-3.0-or-later
// Additional terms: see NOTICE

use super::*;

impl Projection {
    pub(super) fn map_preview(&mut self, owner: u64, viewport: Option<VideoRect>) {
        // Connection IDs increase monotonically. Retain the watermark after
        // disconnect so a delayed older connection cannot reclaim the tile.
        if self.map_preview_latest_owner.is_some_and(|latest| {
            owner < latest || (owner == latest && self.map_preview_owner != Some(owner))
        }) {
            return;
        }
        self.map_preview_latest_owner = Some(owner);
        self.map_preview_owner = Some(owner);
        self.map_preview_requested = viewport.filter(|rect| rect.valid());
        self.publish();
    }

    pub(super) fn map_preview_gone(&mut self, owner: u64) {
        if self.map_preview_owner == Some(owner) {
            self.map_preview_owner = None;
            self.map_preview_requested = None;
            self.publish();
        }
    }

    pub(super) fn map_video_ready(&mut self, ready: bool) {
        if self.map_preview_ready != ready {
            self.map_preview_ready = ready;
            self.publish();
        }
    }

    pub(super) fn effective_map_preview(&self) -> MapPreview {
        let viewport = self.map_preview_requested.filter(|_| {
            self.config_seen.car_play_nav_tile
                && matches!(self.active().map(|s| s.link), Some(Link::Cp(_)))
                && self.front_main() == Front::Livi
        });
        MapPreview {
            viewport,
            ready: viewport.is_some() && self.map_preview_ready,
        }
    }

    pub(super) fn reconcile_cluster_demand(&mut self) {
        let dashboard = shows_cluster(&self.wants);
        let carplay = dashboard || self.effective_map_preview().viewport.is_some();
        if carplay != self.cluster_on {
            self.cluster_on = carplay;
            self.cp.send(CpCmd::ClusterActive(carplay));
        }
        if dashboard != self.aa_cluster_on {
            self.aa_cluster_on = dashboard;
            self.aa.send(AaCmd::ClusterActive(dashboard));
        }
    }
}
