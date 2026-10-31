# Session handoff — 2026-10-30

## Start here

Main checkout: `/Users/yonigozman/Development/PiMost/LIVI`, branch `main`.
Fork: [gozmanyoni/LIVI](https://github.com/gozmanyoni/LIVI), created 2026-10-31.
`origin` targets the fork; `upstream` targets `f-io/LIVI`.
Upstream native baseline is LIVI 10.2.0 commit
`e7b20b818f052958afc95dcf91acc25c70643c02` from `f-io/LIVI`.
Read [BMW_CIC_PROJECT.md](BMW_CIC_PROJECT.md) for architecture and independent lanes.
The user is opening additional sessions on this repository; coordinate ownership
before editing shared types, build files, core wiring or image configuration.

The agreed target is a **Pi 5 BMW interface with CarPlay and a retained headless
OEM CIC**. CIC remains MOST master and provides OEM services. The BMW telemetry
bridge, CAN gateway/filter, native BMW pages and OEM control integration have not
yet been implemented here. The existing Pi/CarPlay/image foundation works.

## Working hardware and runtime

- Raspberry Pi 5 Model B, 8 GB; Raspberry Pi OS Lite 64-bit Trixie, 2026-10-06.
- Waveshare 9.3-inch HDMI/USB capacitive panel, native progressive 1600x600 at
  60 Hz, connected to HDMI0 (nearest USB-C); USB supplies touch/power.
- Native LIVI 10.2.0, DRM/Wayland compositor, no desktop/display manager.
- Wireless CarPlay works using onboard Bluetooth/Wi-Fi and privately approved
  experimental software authentication. No MFi chip or CarPlay dongle was used.
  The tested clean connection uses iPhone Personal Hotspot off and USB unplugged.
- Pi runtime: `/opt/LIVI`; user config `/home/livi/.config/LIVI`; kiosk unit
  `livi-kiosk.service`; independent AP unit `livi-wifi-ap.service`.
- AP/Bluetooth name is `LIVI-Pi5`, Wi-Fi AU/channel 36/20 MHz. Pi AP address is
  `10.10.0.1`, SSH account `livi`. Access secrets are private generated artifacts.
  The development Mac normally uses its home network, not the Pi AP.
- Final physical checks confirmed display, touch and wireless CarPlay. The
  profiling compositor/environment was restored; optional tracing is off.
  Normal filesystem checks/journaling remain. No extra recorder remains running.

## Retained source and image improvements

1. Preserve canonical per-screen display-mode objects in core config migration.
2. Prefer progressive DRM modes over interlaced alternatives at the same size.
3. Correct sudo-rule filenames so installed helper permissions actually load.
4. Disable cloud-init after offline provisioning. Two reboots per condition:
   Linux startup mean 5.626 -> 4.446 s, saving 1.180 s (21%).
5. Poll compositor readiness every 50 ms only during initial startup, at most
   five seconds, then use normal 500 ms reconnect cadence. First successful
   connection ends fast polling. Two reboots per condition: UI process launch
   mean 7.310 -> 7.060 s; Linux/AP timing effectively unchanged.
6. Add opt-in `LIVI_BOOT_TRACE=1` phase and first-UI-frame instrumentation, plus
   guarded diagnostic/rollback helpers. This instrumentation is not enabled on
   the working Pi or in the retained image.

Rejected experiments are documented in `scripts/install/pi5-fast/README.md`:
removing network ordering worsened Linux startup (4.470 -> 6.232 s) despite a
slightly earlier UI launch. Skipping the snapshot reaper had no clear gain.
Both changes were rolled back. Do not repeat without new evidence.

No EEPROM, overclock or firmware debugging change was made. The user elected to
stop boot optimization and begin BMW integration. Further boot work is optional,
not a prerequisite for vehicle/UI development.

## Measurement evidence and limits

Two temporary profiled boots measured GBM device creation at **1.194 s mean**;
input setup was 0.124 s, renderer/shaders 0.082 s, connector scanning 0.001 s.
The first UI-containing frame completed at DRM at **7.472 s after Linux started**.
These are not camera-observed or cold power-on times. Nested phases must not be
summed. Firmware separately reported OS handoff at 9191/9239 ms on its own clock;
the clocks were not correlated. `BOOT_UART=1` and the installed EEPROM date were
observed, but no firmware optimization was tested.

The detailed local report remains under
`dist/pi5-compositor-profiles/20261030-140229/REPORT.md` in the retained private
artifacts (see paths below). Raw logs may contain identifiers; do not publish them.

Verification recorded before integration:

- 13 core config regression tests.
- 29 media tests, including paused-clock readiness/reconnect tests.
- 26 compositor tests after optional trace/frame instrumentation (the retained
  uninstrumented image compositor was built at the earlier 20-test stage).
- 87 Python Pi helper/transaction regression tests.
- Rust formatting and relevant Clippy checks passed.
- 44 offline image checks, filesystem/XZ/hash/library checks passed for the
  retained image; physical display/touch/wireless CarPlay were user-confirmed.

These are scoped checks, not a claim that every upstream crate or all possible
vehicle configurations were tested. Re-run checks relevant to subsequent changes.

## Private flash image and rebuild boundary

Latest private image: `dist/pi5-fastboot/LIVI-Pi5-10.2.0-fastboot.img.xz`.
SHA-256: `082dacb7e2f1cab6c9f31d72f4970ab8c537709e17dfa0f83427cff6844b1249`.
Uncompressed size: 6 GiB, fixed layout, 8 GB+ card required. No reflash is needed
for the already-updated working Pi.

Retained ARM64 binaries:

| Binary | SHA-256 |
| --- | --- |
| livi-core | `02ed6025ebdd7652b5709dc11c281d8e9a7167d9619b05212da24156606ded37` |
| livi-compositor | `b9f3a6d1c6124cdfc7f03d93b586d97a23dcf964cae1537248c71fce5fa26792` |

`scripts/install/pi5-fast/build-image.sh` builds the private image. It consumes
verified native overrides, a pinned base OS/AppImage and externally selected auth
files. Follow its README: replacing affected upstream binaries silently is not
allowed. Do not commit/upload the image, cert/key, `access.json`, `START-HERE.txt`,
pairing data or raw logs. They are private local artifacts, even on a public fork.
A source build with optional trace code does not mean the instrumented binary was
retained on hardware. Keep source and deployed binary provenance distinct.

## Consolidation verification

All 157 legacy source files were preserved byte-for-byte on the local archive
branch. The old `main` baseline is also retained as
`codex/legacy-main-before-native-20261030`. No legacy source branch was published.
The 216 private worktree artifacts were copied here and SHA-256 verified; the
original worktree remains available. Copy manifests, archive fingerprints and
local session tooling are in ignored `.cache/migration-20261030/`.

Native source passed 13 config, 29 media and 26 compositor tests. All 87 Python
helper tests passed. Rust formatting, media/compositor Clippy with warnings denied,
shell syntax and whitespace checks passed during consolidation. Existing legacy
Electron lint errors were recorded in its archive commit rather than altering
user source to make an archival snapshot pass.

## Existing local references and caches

Private generated artifacts are copied into this main checkout under `dist/` and
`.cache/`; a clean clone does not include them. The original worktree remains as
a second local copy until separately retired.

- Original native working tree:
  `/Users/yonigozman/.codex/worktrees/livi-carplay-local/LIVI`.
  Main now also contains the ignored `dist/` and `.cache/pi5-fast/` image inputs,
  private reports and tested overrides. `dist/pi5-fastboot/image-manifest.json` is artifact authority.
- ARM64 native build/test cache: `/private/tmp/livi-core-pi5-build/`.
  Its pinned Rust toolchain and Linux multimedia build environment were required;
  consult scripts/records before replacing them with host-only commands.
- Earlier Electron PiMOST integration and user edits are preserved at
  local branch `codex/legacy-electron-pimost-20261030`, commit
  `281d33f5dcc1c45319b29a8c3de951ca792dd650`. This is historical reference, not
  the native build target.
- Current native MOST implementation:
  `/Users/yonigozman/Development/carpodgo/tools/pimost-native-mostd/mostd.cpp` and
  `protocol_test.cpp`. Existing packaging targets older ARM; port packaging for
  Pi ARM64. MOSTD/1 listens at loopback port 4040.
- **MOST README is stale:** actual current code accepts explicit radio, amplifier
  and source intents and has allocation/connection state transitions. Host tests
  do not prove audible routing. Make the first LIVI bridge emit no intents and
  audit automatic daemon startup traffic before live deployment.
- Current native MOST bootstrap/settings and decode tests supersede old Electron
  or early JLR notes. Do not transplant conflicting scaling/frame assumptions;
  trace every mapping to current code and replay fixtures.
- CAN research checkout:
  `/Users/yonigozman/Development/E9X-M-CAN-Integration-Module`.
  This is a reference project for another vehicle configuration, not drop-in M3
  firmware. No adapter/HAT or inline gateway is validated on this Pi yet.
- Additional historical research:
  `/Users/yonigozman/Development/PiMost/JLR-HU/` and sibling PiMOST repositories.
  Prefer current source/tests when historical notes disagree.

## Suggested first sessions

1. **Read-only MOST adapter:** establish exact MOSTD framing and startup traffic;
   map a minimal validated sensor subset through LIVI's existing telemetry input,
   with fixtures, reconnect and expiry semantics. No INTENT writes.
2. **BMW native UI:** build 1600x600 vehicle/status/PDC views with fixture-fed
   telemetry, focus/iDrive navigation and explicit unavailable states. Avoid
   claiming the presence of a control means it works against the vehicle.
3. **CAN input/gateway:** validate two-channel low-speed K-CAN hardware and CIC
   branch isolation; capture and replay inputs/status before filtering. Pi-only
   gateway is the agreed prototype, with early standalone service startup.
4. **OEM audio/control research:** inventory actual current mostd writer behaviour,
   PiMOST routing responsibilities and confirmed effects. Separate decoded state,
   transmitted intent, acknowledgement and audible outcome in every result.

Coordinate protocol/schema changes across lanes. Keep telemetry, UI and gateway
services independently restartable. Update this document with concrete delivered
features, tests, physical outcomes and rollback paths as each slice lands.
