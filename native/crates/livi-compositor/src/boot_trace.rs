// SPDX-FileCopyrightText: 2025-2026 Lasse Heitgres
// SPDX-License-Identifier: GPL-3.0-or-later
// Additional terms: see NOTICE

//! Opt-in startup diagnostics. Times describe software milestones, not photons.

use std::collections::HashSet;
use std::sync::{Mutex, OnceLock};
use std::time::Instant;

const MAX_MARKERS: usize = 40;
static TRACE: OnceLock<Option<Trace>> = OnceLock::new();

struct Trace {
    start: Instant,
    seen: Mutex<HashSet<&'static str>>,
}

impl Trace {
    fn new() -> Self {
        Self {
            start: Instant::now(),
            seen: Mutex::new(HashSet::new()),
        }
    }

    fn take(&self, stage: &'static str) -> bool {
        let Ok(mut seen) = self.seen.lock() else {
            return false;
        };
        seen.len() < MAX_MARKERS && seen.insert(stage)
    }
}

pub fn init() {
    TRACE.get_or_init(|| (std::env::var("LIVI_BOOT_TRACE").as_deref() == Ok("1")).then(Trace::new));
    mark("main_entry");
}

pub fn enabled() -> bool {
    TRACE.get().is_some_and(Option::is_some)
}

fn record(trace: Option<&Trace>, stage: &'static str, emit: impl FnOnce(&Trace)) {
    if let Some(trace) = trace
        && trace.take(stage)
    {
        emit(trace);
    }
}

pub fn mark(stage: &'static str) {
    record(TRACE.get().and_then(Option::as_ref), stage, |trace| {
        let mut now = nix::libc::timespec {
            tv_sec: 0,
            tv_nsec: 0,
        };
        // CLOCK_MONOTONIC shares the kernel/journal monotonic time domain.
        let result = unsafe { nix::libc::clock_gettime(nix::libc::CLOCK_MONOTONIC, &mut now) };
        let elapsed_ms = trace.start.elapsed().as_secs_f64() * 1000.0;
        if result == 0 {
            let monotonic_ms = now.tv_sec as f64 * 1000.0 + now.tv_nsec as f64 / 1_000_000.0;
            eprintln!(
                "[boot-trace] stage={stage} elapsed_ms={elapsed_ms:.3} monotonic_ms={monotonic_ms:.3}"
            );
        } else {
            eprintln!(
                "[boot-trace] stage={stage} elapsed_ms={elapsed_ms:.3} monotonic_ms=unavailable"
            );
        }
    });
}

/// Per-output tracking: a blank frame or a failed flip must not become a UI frame.
/// Pass queue_frame's result to `queued`, and call `submitted` only for that
/// output's vblank. Completion requires frame_submitted to return Ok(Some(_));
/// it means the DRM backend reported completion, not optical proof.
#[derive(Default)]
pub struct FirstUiFrame {
    active: bool,
    queued_once: bool,
    pending_ui: bool,
    completed: bool,
}

impl FirstUiFrame {
    pub fn new(active: bool) -> Self {
        Self {
            active,
            ..Self::default()
        }
    }

    pub fn queued(&mut self, has_ui: bool, succeeded: bool) -> bool {
        if !self.active || self.completed || !succeeded {
            return false;
        }
        self.pending_ui = has_ui;
        if !has_ui || self.queued_once {
            return false;
        }
        self.queued_once = true;
        true
    }

    pub fn submitted(&mut self, succeeded: bool) -> bool {
        let pending_ui = std::mem::take(&mut self.pending_ui);
        if !self.active || self.completed || !pending_ui || !succeeded {
            return false;
        }
        self.completed = true;
        true
    }

    pub fn reset_pending(&mut self) {
        self.pending_ui = false;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::Cell;

    #[test]
    fn disabled_trace_does_not_emit() {
        record(None, "disabled", |_| panic!("disabled trace emitted"));
        let mut frame = FirstUiFrame::new(false);
        assert!(!frame.queued(true, true));
        assert!(!frame.submitted(true));
    }

    #[test]
    fn markers_are_one_shot_and_bounded() {
        let trace = Trace::new();
        let emitted = Cell::new(0);
        record(Some(&trace), "once", |_| emitted.set(emitted.get() + 1));
        record(Some(&trace), "once", |_| emitted.set(emitted.get() + 1));
        assert_eq!(emitted.get(), 1);
        let mut seen = trace.seen.lock().unwrap();
        for i in 1..MAX_MARKERS {
            seen.insert(Box::leak(format!("marker_{i}").into_boxed_str()));
        }
        drop(seen);
        record(Some(&trace), "over_limit", |_| {
            panic!("marker limit exceeded")
        });
    }

    #[test]
    fn blank_frames_and_unqueued_vblanks_are_not_ui() {
        let mut frame = FirstUiFrame::new(true);
        assert!(!frame.submitted(true));
        assert!(!frame.queued(false, true));
        assert!(!frame.submitted(true));
        assert!(!frame.queued(true, false));
        assert!(!frame.submitted(true));
    }

    #[test]
    fn successful_ui_queue_and_submission_are_one_shot() {
        let mut frame = FirstUiFrame::new(true);
        assert!(frame.queued(true, true));
        assert!(frame.submitted(true));
        assert!(!frame.queued(true, true));
        assert!(!frame.submitted(true));
    }

    #[test]
    fn failed_submission_or_session_reset_requires_another_ui_queue() {
        let mut frame = FirstUiFrame::new(true);
        assert!(frame.queued(true, true));
        assert!(!frame.submitted(false));
        assert!(!frame.submitted(true));
        assert!(!frame.queued(true, true));
        frame.reset_pending();
        assert!(!frame.submitted(true));
        assert!(!frame.queued(true, true));
        assert!(frame.submitted(true));
    }

    #[test]
    fn a_different_outputs_vblank_cannot_complete_the_main_ui() {
        let mut main = FirstUiFrame::new(true);
        let mut other = FirstUiFrame::new(false);
        assert!(main.queued(true, true));
        assert!(!other.queued(true, true));
        assert!(!other.submitted(true));
        assert!(main.submitted(true));
    }
}
