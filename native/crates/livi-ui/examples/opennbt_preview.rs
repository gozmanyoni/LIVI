// SPDX-FileCopyrightText: 2026 OpenNBT contributors
// SPDX-License-Identifier: GPL-3.0-or-later
// Additional terms: see NOTICE

//! Render the actual Slint UI to a PNG without a display server or vehicle services.
//! Run with: cargo run -p livi-ui --example opennbt_preview -- home /tmp/opennbt-home.png

use std::cell::{Cell, RefCell};
use std::path::Path;
use std::rc::Rc;
use std::time::Duration;

use slint::platform::software_renderer::{
    MinimalSoftwareWindow, PremultipliedRgbaColor, RepaintBufferType,
};
use slint::platform::{Platform, PlatformError, PointerEventButton, WindowAdapter, WindowEvent};
use slint::{ComponentHandle, LogicalPosition, ModelRc, PhysicalSize, Rgb8Pixel, VecModel};

slint::include_modules!();

struct PreviewPlatform(Rc<MinimalSoftwareWindow>);

thread_local! {
    static PREVIEW_TIME: Cell<Duration> = const { Cell::new(Duration::ZERO) };
}

impl Platform for PreviewPlatform {
    fn create_window_adapter(&self) -> Result<Rc<dyn WindowAdapter>, PlatformError> {
        Ok(self.0.clone())
    }

    fn duration_since_start(&self) -> Duration {
        PREVIEW_TIME.with(Cell::get)
    }
}

fn advance(milliseconds: u64) {
    PREVIEW_TIME.with(|clock| clock.set(clock.get() + Duration::from_millis(milliseconds)));
    slint::platform::update_timers_and_animations();
}

fn settle_home(ui: &AppWindow) {
    for _ in 0..320 {
        advance(16);
        if !ui.get_home_moving() {
            return;
        }
    }
    panic!(
        "Home spring did not settle: offset={}",
        ui.get_home_offset()
    );
}

fn pointer_down(ui: &AppWindow, x: f32, y: f32) {
    ui.window().dispatch_event(WindowEvent::PointerPressed {
        position: LogicalPosition::new(x, y),
        button: PointerEventButton::Left,
    });
}

fn pointer_move(ui: &AppWindow, x: f32, y: f32) {
    ui.window().dispatch_event(WindowEvent::PointerMoved {
        position: LogicalPosition::new(x, y),
    });
}

fn pointer_up(ui: &AppWindow, x: f32, y: f32) {
    ui.window().dispatch_event(WindowEvent::PointerReleased {
        position: LogicalPosition::new(x, y),
        button: PointerEventButton::Left,
    });
}

fn icon(name: &str) -> slint::Image {
    slint::Image::load_from_path(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("ui/icons")
            .join(format!("{name}.svg")),
    )
    .unwrap_or_default()
}

fn click(ui: &AppWindow, x: f32, y: f32) {
    let position = LogicalPosition::new(x, y);
    ui.window().dispatch_event(WindowEvent::PointerPressed {
        position,
        button: PointerEventButton::Left,
    });
    ui.window().dispatch_event(WindowEvent::PointerReleased {
        position,
        button: PointerEventButton::Left,
    });
}

fn render_alpha(
    window: &MinimalSoftwareWindow,
    width: usize,
    height: usize,
) -> Vec<PremultipliedRgbaColor> {
    slint::platform::update_timers_and_animations();
    window.request_redraw();
    let mut pixels = vec![PremultipliedRgbaColor::default(); width * height];
    window.draw_if_needed(|renderer| {
        renderer.render(&mut pixels, width);
    });
    pixels
}

fn save_frame(
    window: &MinimalSoftwareWindow,
    output: impl AsRef<Path>,
    width: u32,
    height: u32,
) -> Result<(), Box<dyn std::error::Error>> {
    let pixels = render_alpha(window, width as usize, height as usize);
    let bytes: Vec<u8> = pixels
        .iter()
        .flat_map(|p| [p.red, p.green, p.blue, p.alpha])
        .collect();
    image::save_buffer(output, &bytes, width, height, image::ColorType::Rgba8)?;
    Ok(())
}

fn swipe_demo(
    ui: &AppWindow,
    window: &MinimalSoftwareWindow,
    output: &Path,
    width: u32,
    height: u32,
) -> Result<(), Box<dyn std::error::Error>> {
    std::fs::create_dir_all(output)?;
    let x = |value: f32| value * width as f32 / 1600.0;
    let y = 330.0 * height as f32 / 600.0;
    for frame in 0..105 {
        advance(16);
        match frame {
            7 => pointer_down(ui, x(1000.0), y),
            8..=20 => pointer_move(ui, x(1000.0 - 35.0 * (frame - 7) as f32), y),
            21 => pointer_up(ui, x(545.0), y),
            56 => pointer_down(ui, x(600.0), y),
            57..=69 => pointer_move(ui, x(600.0 + 35.0 * (frame - 56) as f32), y),
            70 => pointer_up(ui, x(1055.0), y),
            _ => {}
        }
        save_frame(
            window,
            output.join(format!("frame-{frame:03}.png")),
            width,
            height,
        )?;
    }
    println!(
        "Rendered 105 native gesture/spring frames into {}",
        output.display()
    );
    Ok(())
}

fn check_map_preview(ui: &AppWindow, window: &MinimalSoftwareWindow) {
    ui.set_page(Page::Home);
    ui.set_home_page(0);
    ui.set_map_preview_enabled(true);
    settle_home(ui);
    let changes = Rc::new(RefCell::new(Vec::new()));
    let record = changes.clone();
    let weak = ui.as_weak();
    ui.on_map_viewport_changed(move || {
        if let Some(ui) = weak.upgrade() {
            record.borrow_mut().push(ui.get_map_viewport());
        }
    });
    let fallback = render_alpha(window, 1600, 600);
    let view = ui.get_map_viewport();
    assert!(view.shown, "request video before receiving a frame");
    let center_x = ((view.x + view.width / 2.0) * 1600.0) as usize;
    let center_y = ((view.y + view.height / 2.0) * 600.0) as usize;
    assert_eq!(
        fallback[center_y * 1600 + center_x].alpha,
        255,
        "globe fallback is opaque"
    );
    ui.set_map_preview_confirmed(ui.get_map_viewport());
    ui.set_map_preview_ready(true);
    let ready = render_alpha(window, 1600, 600);
    assert_eq!(
        ready[center_y * 1600 + center_x].alpha,
        0,
        "map passes through every UI ancestor"
    );
    for (x, y) in [
        (center_x, 110),
        (800, center_y),
        (1590, center_y),
        (center_x, 590),
    ] {
        assert_eq!(
            ready[y * 1600 + x].alpha,
            255,
            "only map content may become transparent at {x},{y}"
        );
    }
    let opened = Rc::new(RefCell::new(Vec::new()));
    let record = opened.clone();
    ui.on_home_open(move |index| record.borrow_mut().push(index));
    click(ui, center_x as f32, center_y as f32);
    assert_eq!(
        *opened.borrow(),
        vec![2],
        "live map tile retains its navigation action"
    );
    click(ui, 1530.0, 330.0);
    render_alpha(window, 1600, 600);
    assert!(
        !ui.get_map_viewport().shown,
        "paging away releases map demand"
    );
    assert!(changes.borrow().iter().any(|v| !v.shown));
    ui.set_home_page(0);
    ui.set_map_preview_ready(false);
    let disconnected = render_alpha(window, 1600, 600);
    assert_eq!(
        disconnected[center_y * 1600 + center_x].alpha,
        255,
        "disconnect restores fallback"
    );
    click(ui, 45.0, 30.0);
    render_alpha(window, 1600, 600);
    assert!(
        !ui.get_map_viewport().shown,
        "native menu obscures map demand"
    );
    click(ui, 45.0, 30.0);
    render_alpha(window, 1600, 600);
    assert!(ui.get_map_viewport().shown);
    ui.set_map_preview_confirmed(ui.get_map_viewport());
    ui.set_map_preview_ready(true);
    render_alpha(window, 1600, 600);
    window.set_size(PhysicalSize::new(800, 300));
    ui.window().dispatch_event(WindowEvent::Resized {
        size: slint::LogicalSize::new(800.0, 300.0),
    });
    window.request_redraw();
    let mut resized = vec![PremultipliedRgbaColor::default(); 800 * 300];
    window.draw_if_needed(|renderer| {
        renderer.render(&mut resized, 800);
    });
    let compact = ui.get_map_viewport();
    let compact_x = ((compact.x + compact.width / 2.0) * 800.0) as usize;
    let compact_y = ((compact.y + compact.height / 2.0) * 300.0) as usize;
    assert_eq!(
        resized[compact_y * 800 + compact_x].alpha,
        255,
        "resize covers video immediately, before deferred viewport callbacks"
    );
    slint::platform::update_timers_and_animations();
    let compact = ui.get_map_viewport();
    let compact_x = ((compact.x + compact.width / 2.0) * 800.0) as usize;
    let compact_y = ((compact.y + compact.height / 2.0) * 300.0) as usize;
    ui.set_map_preview_confirmed(compact.clone());
    let acknowledged = render_alpha(window, 800, 300);
    assert_eq!(
        acknowledged[compact_y * 800 + compact_x].alpha,
        0,
        "only acknowledged geometry reopens the resized map hole"
    );
    assert!(compact.shown && compact.width > 0.0 && compact.height > 0.0);
    assert_ne!(
        compact, view,
        "resizing updates border-inset normalized geometry"
    );
    window.set_size(PhysicalSize::new(1600, 600));
    ui.window().dispatch_event(WindowEvent::Resized {
        size: slint::LogicalSize::new(1600.0, 600.0),
    });
    ui.set_map_preview_enabled(false);
    render_alpha(window, 1600, 600);
    assert!(
        !ui.get_map_viewport().shown,
        "disabled feature has no video demand"
    );
    ui.set_map_preview_enabled(true);
    render_alpha(window, 1600, 600);
    println!(
        "Passed map tile checks: alpha cutout, opaque fallback, paging, drawer, resizing and disabled demand"
    );
}

fn check_home_swipe(ui: &AppWindow, window: &MinimalSoftwareWindow) {
    let opened = Rc::new(RefCell::new(Vec::new()));
    let record = opened.clone();
    ui.on_home_open(move |index| record.borrow_mut().push(index));
    ui.set_page(Page::Home);
    ui.set_home_page(0);
    ui.set_home_media_enabled(true);
    ui.global::<Focus>().set_zone(FocusZone::None);
    settle_home(ui);
    let settled = render_alpha(window, 1600, 600);
    let color = |index: usize| {
        let p = settled[index];
        [p.red, p.green, p.blue, p.alpha]
    };
    assert_eq!(
        color(90 * 1600 + 800),
        color(90 * 1600 + 610),
        "the middle tile header stays continuous; gutter masks must not cover it"
    );
    let view = ui.get_map_viewport();
    ui.set_map_preview_confirmed(view.clone());
    ui.set_map_preview_ready(true);
    let map_x = ((view.x + view.width / 2.0) * 1600.0) as usize;
    let map_y = ((view.y + view.height / 2.0) * 600.0) as usize;
    let map_covered = || {
        let pixels = render_alpha(window, 1600, 600);
        assert_eq!(
            pixels[map_y * 1600 + map_x].alpha,
            255,
            "moving tile never exposes stale map video"
        );
        assert!(!ui.get_map_viewport().shown, "motion suppresses map demand");
    };

    // Recognition must preserve ordinary taps, including small touch jitter.
    pointer_down(ui, 330.0, 330.0);
    pointer_move(ui, 334.0, 333.0);
    pointer_up(ui, 334.0, 333.0);
    assert_eq!(*opened.borrow(), vec![0]);
    opened.borrow_mut().clear();

    // Slow, short drags rebound, while the strip tracks the actual finger delta.
    pointer_down(ui, 900.0, 330.0);
    advance(150);
    pointer_move(ui, 880.0, 330.0);
    advance(200);
    pointer_move(ui, 820.0, 330.0);
    advance(1);
    render_alpha(window, 1600, 600);
    assert!(
        (ui.get_home_offset() + 80.0).abs() < 3.0,
        "tiles follow finger rather than jumping a page: offset={}, moving={}, page={}",
        ui.get_home_offset(),
        ui.get_home_moving(),
        ui.get_home_page()
    );
    map_covered();
    advance(160);
    pointer_up(ui, 820.0, 330.0);
    assert_eq!(ui.get_home_page(), 0);
    assert!(
        ui.get_home_moving(),
        "short drag springs back rather than snapping"
    );
    map_covered();
    settle_home(ui);
    assert!(
        opened.borrow().is_empty(),
        "drag after a held press must cancel tile click"
    );

    // A swipe starting over the live map must page without opening navigation.
    pointer_down(ui, 1330.0, 330.0);
    advance(16);
    pointer_move(ui, 830.0, 330.0);
    map_covered();
    pointer_up(ui, 830.0, 330.0);
    assert_eq!(ui.get_home_page(), 1);
    assert!(ui.get_home_moving());
    let target = -1600.0 * 3.0 * 0.29375;
    let mut overshot = false;
    let mut caught_overshoot = false;
    for _ in 0..320 {
        advance(16);
        let offset = ui.get_home_offset();
        overshot |= offset < target - 1.0;
        if !caught_overshoot && offset < target - 1.0 {
            pointer_down(ui, 900.0, 330.0);
            pointer_move(ui, 880.0, 330.0);
            advance(1);
            let caught = ui.get_home_offset();
            assert!(
                caught < offset && offset - caught < 20.0,
                "catching an edge overshoot preserves its pose and resists only the new outward drag: before={offset}, after={caught}"
            );
            map_covered();
            pointer_up(ui, 880.0, 330.0);
            caught_overshoot = true;
        }
        if !ui.get_home_moving() {
            break;
        }
        map_covered();
    }
    assert!(
        !ui.get_home_moving(),
        "spring reaches its final resting position"
    );
    assert!((ui.get_home_offset() - target).abs() < 0.1);
    assert!(overshot, "release uses a real spring, with visible rebound");
    assert!(
        caught_overshoot,
        "interruption includes the spring's edge overshoot"
    );
    assert!(
        opened.borrow().is_empty(),
        "map swipe is not a navigation tap"
    );
    click(ui, 1330.0, 330.0);
    assert_eq!(
        *opened.borrow(),
        vec![5],
        "settled second-page tiles retain their identity"
    );
    opened.borrow_mut().clear();

    // Both ends resist dragging and rebound without wrapping the two pages.
    pointer_down(ui, 900.0, 330.0);
    pointer_move(ui, 500.0, 330.0);
    let resistance = target - ui.get_home_offset();
    assert!(resistance > 0.0 && resistance < 400.0);
    pointer_up(ui, 500.0, 330.0);
    settle_home(ui);
    assert_eq!(ui.get_home_page(), 1);
    pointer_down(ui, 600.0, 330.0);
    pointer_move(ui, 1100.0, 330.0);
    pointer_up(ui, 1100.0, 330.0);
    settle_home(ui);
    assert_eq!(ui.get_home_page(), 0);
    pointer_down(ui, 600.0, 330.0);
    pointer_move(ui, 1000.0, 330.0);
    assert!(ui.get_home_offset() > 0.0 && ui.get_home_offset() < 400.0);
    pointer_up(ui, 1000.0, 330.0);
    settle_home(ui);
    assert_eq!(ui.get_home_page(), 0);

    // Short, fast flicks can page without dragging most of the screen width.
    pointer_down(ui, 1000.0, 330.0);
    pointer_move(ui, 980.0, 330.0);
    advance(16);
    pointer_move(ui, 860.0, 330.0);
    pointer_up(ui, 860.0, 330.0);
    assert_eq!(ui.get_home_page(), 1);
    settle_home(ui);
    ui.set_home_page(0);
    advance(32);
    assert!(
        ui.get_home_moving(),
        "external/controller page selection animates"
    );
    let before = ui.get_home_offset();
    pointer_down(ui, 600.0, 330.0);
    pointer_move(ui, 900.0, 330.0);
    assert!(
        (ui.get_home_offset() - before - 300.0).abs() < 3.0,
        "grabbing a running spring preserves its displayed position"
    );
    pointer_up(ui, 900.0, 330.0);
    settle_home(ui);
    assert_eq!(ui.get_home_page(), 0);

    pointer_down(ui, 800.0, 210.0);
    pointer_move(ui, 800.0, 430.0);
    map_covered();
    pointer_up(ui, 800.0, 430.0);
    settle_home(ui);
    assert_eq!(ui.get_home_page(), 0);
    assert!(
        opened.borrow().is_empty(),
        "vertical gestures must not click tiles"
    );
    pointer_down(ui, 800.0, 330.0);
    pointer_move(ui, 400.0, 330.0);
    ui.window().dispatch_event(WindowEvent::PointerExited);
    pointer_up(ui, 400.0, 330.0);
    settle_home(ui);
    assert_eq!(
        ui.get_home_page(),
        0,
        "cancelled swipe returns to its original page"
    );
    assert!(opened.borrow().is_empty());

    // Showing native navigation or leaving Home immediately cancels a gesture.
    pointer_down(ui, 800.0, 330.0);
    pointer_move(ui, 400.0, 330.0);
    ui.global::<Focus>().set_zone(FocusZone::Nav);
    render_alpha(window, 1600, 600);
    assert!(!ui.get_home_moving());
    assert!(!ui.get_map_viewport().shown);
    pointer_up(ui, 400.0, 330.0);
    ui.global::<Focus>().set_zone(FocusZone::None);
    settle_home(ui);
    pointer_down(ui, 800.0, 330.0);
    pointer_move(ui, 400.0, 330.0);
    ui.set_page(Page::Media);
    render_alpha(window, 1600, 600);
    pointer_up(ui, 400.0, 330.0);
    assert!(!ui.get_home_moving());
    ui.set_page(Page::Home);
    settle_home(ui);
    assert!(opened.borrow().is_empty());

    ui.set_home_media_enabled(false);
    click(ui, 330.0, 330.0);
    assert!(opened.borrow().is_empty());
    pointer_down(ui, 440.0, 330.0);
    pointer_move(ui, 40.0, 330.0);
    pointer_up(ui, 40.0, 330.0);
    settle_home(ui);
    assert_eq!(
        ui.get_home_page(),
        1,
        "disabled tile still allows parent swipe"
    );
    ui.set_home_media_enabled(true);
    ui.set_home_page(0);
    settle_home(ui);

    // Resize during a drag invalidates input coordinates and the old map ack.
    pointer_down(ui, 800.0, 330.0);
    pointer_move(ui, 400.0, 330.0);
    window.set_size(PhysicalSize::new(800, 300));
    ui.window().dispatch_event(WindowEvent::Resized {
        size: slint::LogicalSize::new(800.0, 300.0),
    });
    render_alpha(window, 800, 300);
    pointer_up(ui, 200.0, 165.0);
    settle_home(ui);
    assert_eq!(ui.get_home_page(), 0);
    assert!(ui.get_map_viewport().shown);
    ui.set_map_preview_confirmed(ui.get_map_viewport());
    ui.set_map_preview_ready(true);
    let view = ui.get_map_viewport();
    let pixels = render_alpha(window, 800, 300);
    let x = ((view.x + view.width / 2.0) * 800.0) as usize;
    let y = ((view.y + view.height / 2.0) * 300.0) as usize;
    assert_eq!(
        pixels[y * 800 + x].alpha,
        0,
        "stationary confirmed map can reopen after resize"
    );
    window.set_size(PhysicalSize::new(1600, 600));
    ui.window().dispatch_event(WindowEvent::Resized {
        size: slint::LogicalSize::new(1600.0, 600.0),
    });
    settle_home(ui);
    ui.set_map_preview_ready(false);
    assert!(opened.borrow().is_empty());
    println!(
        "Passed Home gesture checks: taps, finger tracking, distance/flick paging, spring overshoot, edge resistance, interruption/cancellation, resize and map safety"
    );
}

// Find real Slint hit regions rather than assuming that drawn glyphs are the target.
fn hit(
    ui: &AppWindow,
    action: &RefCell<Option<&'static str>>,
    x: f32,
    y: f32,
) -> Option<&'static str> {
    *action.borrow_mut() = None;
    click(ui, x, y);
    *action.borrow()
}

fn check_target(
    ui: &AppWindow,
    action: &RefCell<Option<&'static str>>,
    expected: &'static str,
    width: u32,
    height: u32,
    minimum: f32,
) -> (f32, f32) {
    let page = ui.get_page();
    let mut found = None;
    // Keep discovery below native chrome: clicking the roundel changes the page.
    let chrome = 62.0 * (width as f32 / 1600.0).min(height as f32 / 600.0).max(0.45);
    let start_y = if page == Page::Settings || matches!(expected, "phone" | "dab") {
        chrome.ceil() as u32 + 2
    } else {
        height / 2
    };
    'rows: for y in (start_y..height).step_by(12) {
        for x in (8..width).step_by(12) {
            if hit(ui, action, x as f32, y as f32) == Some(expected) {
                found = Some((x as f32, y as f32));
                break 'rows;
            }
        }
    }
    assert_eq!(ui.get_page(), page, "target discovery stays on its page");
    let (x, y) = found.unwrap_or_else(|| panic!("missing {expected} target at {width}x{height}"));
    let mut left = x;
    while left > 1.0 && hit(ui, action, left - 1.0, y) == Some(expected) {
        left -= 1.0;
    }
    let mut right = x;
    while right < width as f32 - 1.0 && hit(ui, action, right + 1.0, y) == Some(expected) {
        right += 1.0;
    }
    let center = (left + right) / 2.0;
    let mut top = y;
    while top > 1.0 && hit(ui, action, center, top - 1.0) == Some(expected) {
        top -= 1.0;
    }
    let mut bottom = y;
    while bottom < height as f32 - 1.0 && hit(ui, action, center, bottom + 1.0) == Some(expected) {
        bottom += 1.0;
    }
    assert!(
        right - left + 1.0 >= minimum && bottom - top + 1.0 >= minimum,
        "{expected} touch target is {}x{} at {width}x{height}",
        right - left + 1.0,
        bottom - top + 1.0
    );
    for (x, y) in [
        (left + 2.0, top + 2.0),
        (right - 2.0, top + 2.0),
        (left + 2.0, bottom - 2.0),
        (right - 2.0, bottom - 2.0),
    ] {
        assert_eq!(
            hit(ui, action, x, y),
            Some(expected),
            "{expected} accepts its corners"
        );
    }
    (center, (top + bottom) / 2.0)
}

fn check_media_touch(ui: &AppWindow, window: &MinimalSoftwareWindow) {
    let action = Rc::new(RefCell::new(None));
    let record = action.clone();
    ui.on_prev(move || *record.borrow_mut() = Some("previous"));
    let record = action.clone();
    ui.on_play_pause(move || *record.borrow_mut() = Some("play"));
    let record = action.clone();
    ui.on_next(move || *record.borrow_mut() = Some("next"));
    let record = action.clone();
    let sought = Rc::new(RefCell::new(0.0));
    let seek_record = sought.clone();
    ui.on_seek(move |share| {
        *record.borrow_mut() = Some("seek");
        *seek_record.borrow_mut() = share;
    });
    ui.set_page(Page::Media);
    ui.global::<DabState>().set_receiver(false);
    for (width, height, minimum) in [(1600, 600, 94.0), (800, 300, 54.0), (320, 300, 54.0)] {
        window.set_size(PhysicalSize::new(width, height));
        ui.window().dispatch_event(WindowEvent::Resized {
            size: slint::LogicalSize::new(width as f32, height as f32),
        });
        render_alpha(window, width as usize, height as usize);
        for target in ["previous", "play", "next"] {
            check_target(
                ui,
                &action,
                target,
                width,
                height,
                if target == "play" {
                    minimum + 6.0
                } else {
                    minimum
                },
            );
        }
        let x = width as f32 / 2.0;
        let ys: Vec<_> = (height / 2..height)
            .filter(|y| hit(ui, &action, x, *y as f32) == Some("seek"))
            .collect();
        let top = *ys.first().expect("seek band exists");
        let bottom = *ys.last().unwrap();
        assert!(
            bottom - top + 1 >= if width == 1600 { 60 } else { 40 },
            "seek band accepts touches away from the thin visual line"
        );
        assert_eq!(hit(ui, &action, x, (top + 2) as f32), Some("seek"));
        assert!(
            (*sought.borrow() - 0.5).abs() < 0.02,
            "seek retains normalized position"
        );
        ui.set_can_seek(false);
        assert_eq!(
            hit(ui, &action, x, (top + 2) as f32),
            None,
            "disabled seeking stays inactive"
        );
        ui.set_can_seek(true);
    }
    window.set_size(PhysicalSize::new(1600, 600));
    ui.window().dispatch_event(WindowEvent::Resized {
        size: slint::LogicalSize::new(1600.0, 600.0),
    });
    ui.global::<DabState>().set_receiver(true);
    let record = action.clone();
    ui.global::<DabState>()
        .on_resume(move || *record.borrow_mut() = Some("dab"));
    let record = action.clone();
    ui.global::<DabState>()
        .on_phone(move || *record.borrow_mut() = Some("phone"));
    let record = action.clone();
    ui.global::<DabState>()
        .on_toggle_favorite(move || *record.borrow_mut() = Some("favorite"));
    render_alpha(window, 1600, 600);
    check_target(ui, &action, "dab", 1600, 600, 60.0);
    ui.global::<DabState>().set_view_dab(true);
    ui.global::<DabState>().set_has_tuned(true);
    render_alpha(window, 1600, 600);
    check_target(ui, &action, "phone", 1600, 600, 60.0);
    for target in ["previous", "play", "next"] {
        check_target(ui, &action, target, 1600, 600, 94.0);
    }
    let (x, y) = check_target(ui, &action, "favorite", 1600, 600, 94.0);
    ui.global::<DabState>().set_has_tuned(false);
    assert_eq!(
        hit(ui, &action, x, y),
        None,
        "favorite requires a tuned station"
    );
    ui.global::<DabState>().set_view_dab(false);
    ui.global::<DabState>().set_receiver(false);
    println!(
        "Passed media touch checks: transport corners at 1600/800/320 widths, full seek band, source tabs, DAB and disabled actions"
    );
}

fn check_settings_touch(ui: &AppWindow, window: &MinimalSoftwareWindow) {
    let action = Rc::new(RefCell::new(None));
    let settings = ui.global::<SettingsState>();
    let record = action.clone();
    settings.on_back(move || *record.borrow_mut() = Some("back"));
    let record = action.clone();
    settings.on_apply(move || *record.borrow_mut() = Some("apply"));
    let record = action.clone();
    settings.on_press(move |_| *record.borrow_mut() = Some("setting-action"));
    let record = action.clone();
    settings.on_tool(move |_, _| *record.borrow_mut() = Some("setting-tool"));
    let record = action.clone();
    settings.on_toggle(move |_| *record.borrow_mut() = Some("setting-toggle"));
    settings.set_kind(ScreenKind::List);
    settings.set_title("Devices".into());
    settings.set_can_back(true);
    settings.set_show_apply(true);
    ui.global::<Focus>().set_zone(FocusZone::None);
    let rows = vec![
        RowData {
            kind: RowKind::Button,
            label: "Mobile device".into(),
            button: "Connect".into(),
            enabled: true,
            ..Default::default()
        },
        RowData {
            kind: RowKind::Info,
            label: "Device actions".into(),
            enabled: true,
            tools: ModelRc::from(Rc::new(VecModel::from(vec![ToolData {
                icon: icon("SettingsOutlined"),
                enabled: true,
                ..Default::default()
            }]))),
            ..Default::default()
        },
        RowData {
            kind: RowKind::Toggle,
            label: "Projection".into(),
            enabled: true,
            ..Default::default()
        },
    ];
    let rows = Rc::new(VecModel::from(rows));
    settings.set_rows(ModelRc::from(rows.clone()));
    ui.set_page(Page::Settings);
    for (width, height, minimum) in [(1600, 600, 62.0), (800, 300, 42.0)] {
        window.set_size(PhysicalSize::new(width, height));
        ui.window().dispatch_event(WindowEvent::Resized {
            size: slint::LogicalSize::new(width as f32, height as f32),
        });
        render_alpha(window, width as usize, height as usize);
        for target in [
            "back",
            "apply",
            "setting-action",
            "setting-tool",
            "setting-toggle",
        ] {
            check_target(ui, &action, target, width, height, minimum);
        }
    }
    // Disabled trailing actions must not activate the enclosing row instead.
    let (x, y) = check_target(ui, &action, "setting-tool", 800, 300, 42.0);
    use slint::Model;
    let mut row = rows.row_data(1).unwrap();
    let mut tool = row.tools.row_data(0).unwrap();
    tool.enabled = false;
    row.tools = ModelRc::from(Rc::new(VecModel::from(vec![tool])));
    rows.set_row_data(1, row);
    assert_eq!(hit(ui, &action, x, y), None);
    settings.set_rows(ModelRc::default());
    settings.set_can_back(false);
    settings.set_show_apply(false);
    window.set_size(PhysicalSize::new(1600, 600));
    ui.window().dispatch_event(WindowEvent::Resized {
        size: slint::LogicalSize::new(1600.0, 600.0),
    });
    println!(
        "Passed settings touch checks: action/toggle/tool/header corners and disabled actions"
    );
}

fn check_interactions(ui: &AppWindow, window: &MinimalSoftwareWindow) {
    check_map_preview(ui, window);
    check_home_swipe(ui, window);
    check_media_touch(ui, window);
    check_settings_touch(ui, window);
    ui.set_page(Page::Home);
    let mut pixels = vec![Rgb8Pixel::default(); 1600 * 600];
    let mut render = || {
        slint::platform::update_timers_and_animations();
        window.request_redraw();
        window.draw_if_needed(|renderer| {
            renderer.render(&mut pixels, 1600);
        });
    };
    let opened = Rc::new(RefCell::new(Vec::new()));
    let record = opened.clone();
    ui.on_home_open(move |index| record.borrow_mut().push(index));
    render();
    click(ui, 333.0, 330.0);
    assert_eq!(*opened.borrow(), vec![0], "first home tile must activate");
    click(ui, 832.0, 330.0);
    click(ui, 1331.0, 330.0);
    assert_eq!(
        *opened.borrow(),
        vec![0, 1, 2],
        "all first-page tile actions"
    );
    click(ui, 1530.0, 330.0);
    assert_eq!(ui.get_home_page(), 1, "next arrow must switch home pages");
    settle_home(ui);
    render();
    for x in [333.0, 832.0, 1331.0] {
        click(ui, x, 330.0);
    }
    assert_eq!(
        *opened.borrow(),
        vec![0, 1, 2, 3, 4, 5],
        "second home page tile actions"
    );
    click(ui, 58.0, 330.0);
    assert_eq!(
        ui.get_home_page(),
        0,
        "previous arrow must return to page one"
    );
    settle_home(ui);
    ui.set_home_media_enabled(false);
    render();
    click(ui, 333.0, 330.0);
    assert_eq!(
        *opened.borrow(),
        vec![0, 1, 2, 3, 4, 5],
        "disabled media must not activate"
    );

    ui.set_page(Page::Settings);
    render();
    click(ui, 45.0, 30.0);
    assert_eq!(ui.get_page(), Page::Home, "BMW roundel returns to Home");
    click(ui, 45.0, 30.0);
    render();
    click(ui, 32.0, 330.0);
    assert_eq!(
        ui.get_page(),
        Page::Media,
        "roundel menu opens native media"
    );

    let touches = Rc::new(RefCell::new(Vec::new()));
    let record = touches.clone();
    ui.on_touch(move |phase, x, y| record.borrow_mut().push((phase, x, y)));
    ui.set_phone(PhoneState::Streaming);
    ui.set_stream_width(1600);
    ui.set_stream_height(600);
    ui.set_page(Page::Projection);
    render();
    for (x, y) in [(10.0, 10.0), (800.0, 300.0), (1590.0, 590.0)] {
        click(ui, x, y);
        let events = touches.borrow();
        let &(phase, normalized_x, normalized_y) =
            events.last().expect("projection receives touches");
        assert_eq!(phase, TouchPhase::Up);
        assert!(
            (normalized_x - x / 1600.0).abs() < 0.001,
            "projection width includes hidden native navigation"
        );
        assert!(
            (normalized_y - y / 600.0).abs() < 0.001,
            "projection height includes hidden native chrome"
        );
    }
    assert_eq!(
        touches.borrow().len(),
        6,
        "all projection press/release pairs arrive"
    );
    println!(
        "Passed native UI pointer smoke checks: home tile actions, paging, disabled media, full-screen CarPlay touches"
    );
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args().skip(1);
    let requested_page = args.next().unwrap_or_else(|| "home".into());
    let light = requested_page.ends_with("-light");
    let page = requested_page
        .strip_suffix("-light")
        .unwrap_or(&requested_page);
    let output = args
        .next()
        .unwrap_or_else(|| format!("/tmp/opennbt-{page}.png"));
    let width: u32 = args.next().map_or(Ok(1600), |v| v.parse())?;
    let height: u32 = args.next().map_or(Ok(600), |v| v.parse())?;
    let window = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
    slint::platform::set_platform(Box::new(PreviewPlatform(window.clone())))?;
    let ui = AppWindow::new()?;
    window.set_size(PhysicalSize::new(width, height));
    ui.set_clock("10:08".into());
    ui.set_calendar_day("31".into());
    ui.set_calendar_month("October".into());
    ui.set_calendar_year("2026".into());
    ui.set_track_title("Midnight City".into());
    ui.set_artist("M83".into());
    ui.set_album("Hurry Up, We're Dreaming".into());
    ui.set_app("Music".into());
    ui.set_playing(true);
    ui.set_total_ms(243_000.0);
    ui.set_elapsed_ms(61_000.0);
    ui.set_progress(61.0 / 243.0);
    ui.set_can_seek(true);
    ui.set_phone(PhoneState::Connected);
    ui.set_projection_icon(icon("CropPortraitOutlined"));
    ui.set_tabs(ModelRc::from(Rc::new(VecModel::from(vec![
        TabData {
            page: Page::Home,
            icon: icon("HomeOutlined"),
        },
        TabData {
            page: Page::Projection,
            icon: icon("CropPortraitOutlined"),
        },
        TabData {
            page: Page::Media,
            icon: icon("PlayCircleOutlined"),
        },
        TabData {
            page: Page::Telemetry,
            icon: icon("SpeedOutlined"),
        },
        TabData {
            page: Page::Settings,
            icon: icon("SettingsOutlined"),
        },
    ]))));
    ui.global::<Theme>().set_dark(!light);
    ui.set_page(match page {
        "home" | "home2" | "check" | "map-ready" | "home-drag" | "home-spring" | "swipe-demo" => {
            Page::Home
        }
        "media" | "dab" => Page::Media,
        "settings" => Page::Settings,
        "projection" => Page::Projection,
        "telemetry" => Page::Telemetry,
        "camera" => Page::Camera,
        _ => return Err(format!("unknown preview page: {page}").into()),
    });
    if page == "dab" {
        let dab = ui.global::<DabState>();
        dab.set_receiver(true);
        dab.set_view_dab(true);
        dab.set_has_tuned(true);
        dab.set_playing(true);
        dab.set_title("BBC Radio 6 Music".into());
        dab.set_text("Music beyond the mainstream".into());
        dab.set_ensemble("BBC National DAB".into());
        dab.set_initials("6".into());
        dab.set_badge(slint::Color::from_rgb_u8(56, 58, 64));
    }
    if page == "home2" {
        ui.set_home_page(1);
    }
    if page == "settings" {
        use livi_ui_model::i18n::Strings;
        use livi_ui_model::settings::{
            schema,
            view::{Screen, Settings},
        };

        let model = Settings::new(schema());
        let Some(Screen::List { title, rows, .. }) = model.screen(
            &[],
            &serde_json::json!({ "config": {} }),
            &Strings::new("en"),
        ) else {
            return Err("settings root is not a list".into());
        };
        let settings = ui.global::<SettingsState>();
        settings.set_title(title.into());
        settings.set_rows(ModelRc::from(Rc::new(VecModel::from(
            rows.into_iter()
                .enumerate()
                .map(|(i, row)| RowData {
                    label: row.label.into(),
                    icon: row.icon.map(icon).unwrap_or_default(),
                    has_icon: row.icon.is_some(),
                    kind: RowKind::Route,
                    enabled: row.enabled,
                    node: row.node as i32,
                    focus_at: i as i32,
                    tool_offset: -1,
                    ..Default::default()
                })
                .collect::<Vec<_>>(),
        ))));
        ui.global::<Focus>().set_zone(FocusZone::Main);
        ui.global::<Focus>().set_main_index(0);
    }
    ui.show()?;
    settle_home(&ui);
    if page == "swipe-demo" {
        return swipe_demo(&ui, &window, Path::new(&output), width, height);
    }
    if matches!(page, "home-drag" | "home-spring") {
        let x = width as f32 / 1600.0;
        let y = height as f32 / 600.0;
        pointer_down(&ui, 1000.0 * x, 330.0 * y);
        advance(16);
        pointer_move(&ui, 980.0 * x, 330.0 * y);
        advance(16);
        pointer_move(&ui, 600.0 * x, 330.0 * y);
        if page == "home-spring" {
            pointer_up(&ui, 600.0 * x, 330.0 * y);
            advance(96);
        }
    }
    if page == "map-ready" {
        slint::platform::update_timers_and_animations();
        ui.set_map_preview_confirmed(ui.get_map_viewport());
        ui.set_map_preview_ready(true);
    }
    if page == "check" {
        if (width, height) != (1600, 600) {
            return Err("pointer smoke checks require 1600x600".into());
        }
        check_interactions(&ui, &window);
        return Ok(());
    }
    save_frame(&window, &output, width, height)?;
    println!("Rendered {page} at {width}x{height}: {output}");
    Ok(())
}
