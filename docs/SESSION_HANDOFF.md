# Session handoff — updated 2026-10-31

## User-accepted OpenNBT revision — 2026-10-31

After the combined `20261031-video-isolation` runtime update, the user confirmed
that all of the changes work well and requested merging the OpenNBT branch into
`main`. This accepts the tested swipe/spring interaction, map tile fit, larger
touch controls and map/playback isolation on the current Pi/phone setup.
The source below is the same native source fingerprint as that installed build.
Keep the recorded rollback binaries and private image/auth/pairing artifacts
outside Git. This acceptance does not establish long-term reconnect reliability,
rapid phone handover, unrelated BMW/MOST features or other device compatibility.

## Navigation tile isolation from CarPlay playback — 2026-10-31

The user reports that starting CarPlay video replaces the map inside Navigation.
Source tracing shows separate inputs: maps use the secondary cluster receiver,
while URL playback uses the `video` plane. Playback intentionally ranks above
the map, but previously stayed visible behind native Home and showed through its
transparent map cutout. This is a presentation defect, not evidence of mixed
phone video streams.

Changed `livi-media/src/planes.rs`, `livi-core/src/services.rs` and
`livi-compositor/src/render.rs`. Services now shares the playback plane with
CarPlay and presents its picture only while `front.main == Projection`. A
default-hidden presentation gate intersects the existing parked-video allowance;
mutex-serialized state/commands and rechecking after an asynchronous claim prevent
late starts/allowance changes from restoring old visibility. Hiding the picture
does not stop the URL, alter playback permission or stop the map. The compositor
also suppresses playback on an output with an explicitly placed `cluster-main`
map, covering the independent state/placement command handoff before a first map
buffer. Camera layering and full-screen video rank are unchanged.

403 affected-crate tests pass: 324 core, 37 media and 42 compositor. New checks
exercise default-hidden startup, allowance/presentation independence, playback
continuation without URL restart, delayed claims, Home/Projection/Camera/Cluster
state transitions and compositor reservation/clearing. Focused independent source
review found no blocker. Strict all-target Clippy with warnings denied, pinned
formatting and diff checks pass. Matching optimized ARM64 binaries were built;
the native source fingerprint stayed unchanged across the build. These are ARM64
container checks, not physical Pi/phone
acceptance. Repeat video start/stop/restart while navigating, return Home and
re-enter full-screen CarPlay, then reconnect the phone on the Pi.

**Installed on the Pi:** after the user unlocked the Mac, native System Settings
confirmed the original Wi-Fi before and after the temporary Pi connection. The
guarded transaction replaced UI, core and compositor together, verified hashes
and required the same three runtime processes/executable paths to remain stable
for at least three seconds. Receiver identity, authentication, config and phone
pairings were retained. No reflash was performed. The UI includes swipe paging
and the map/video isolation fix. The user subsequently confirmed the combined
changes work well; see the acceptance record above.

The private three-binary updater is `.cache/opennbt-video-isolation/`; all 16
transaction/rollback tests pass, including rejection of a changed/corrupt core and
restoration after a partial core installation. An independent source review and
all 87 Pi helper tests pass. Backup is
`/opt/LIVI/.opennbt-backups/20261031-video-isolation`; the older touch-fit backup
remains intact. Rollback stops the kiosk, restores **all three** saved binaries to
their original paths, then starts it, retaining current config/auth/phone data.
The earlier UI/compositor-only updater must not be used for this revision.

Built candidate: `dist/opennbt-pi5/20261031-video-isolation/`, with a build-only
manifest and source fingerprint. SHA-256: UI
`e4745cec96e2dc63913a1eb684cba3fb9506b8c4be99334c28ee2a82f09dfb00`, core
`51b10ad028800bd7e36c4f4e891cfb5759ccb633c14a82d72b6bc19d221a6aee`, compositor
`216f6899e8fe0b7bb18c11019b228aeeb256e4404c68174744d9fea74b12e89a`.
The package manifests record the installed state. Private installation evidence is
`dist/opennbt-video-isolation-updates/20261031-183008/transaction.json`.
CLI Wi-Fi reporting again returned false despite successful restoration; native
System Settings confirmed the original network Connected, recorded separately
in the private summary. The original SD image remains older than this runtime.

## Home touch swipe and spring paging — 2026-10-31

The user requested horizontal swiping and a springy transition for the two Home
tile sets. Source now uses the pinned toolkit's native `SwipeGestureHandler` and
spring animation; no dependency or compositor/core change was needed.

Changed files are UI `home.slint`, `app.slint` and
`examples/opennbt_preview.rs`, plus design documentation/previews. A stable strip
replaces page-dependent tile identities. It follows the finger, supports distance
and recent flick-velocity paging, resists the two end stops and springs into place.
Arrows/controller selection use the same motion. All four gesture directions are
recognized so vertical drags cancel taps without paging. Native recognition also
cancels an already-held tile press; swiping the map cannot open navigation.

Map projection demand and the transparent hole close throughout dragging and
settling. Stationary confirmed video can reopen afterward. Drawer/page exit and
resize explicitly cancel input; interrupted springs retain their visible pose,
including edge overshoot. The left gutter uses an explicitly positioned backdrop
mask, preserving the original settled layout.

Keep `target-slide`'s explicit floating-point conditional: the pinned Slint Rust
generator inlined a raw `-current-page` integer through a ternary and truncated
fractional movement. Native rendered tracking checks caught it. Edge capture also
inverts existing resistance before applying new drag, avoiding a jump when a
finger catches overshoot. Both regressions are protected by actual input/motion
checks; temporary diagnostics were removed.

Verification: 9 UI + 90 UI-model tests passed. Final native pointer/render tests
pass small-jitter taps, slow rebound, fast flicks, real spring overshoot, both end
stops, grabbing a running/overshooting spring, vertical/exit cancellation,
drawer/page changes, disabled-tile swipes, resizing, map opacity and existing
media/settings/full-screen projection checks. Strict Clippy and formatting/diff
checks passed. A deterministic native clock drives the tests and `swipe-demo`
frame export. Settled/motion screenshots at 1600×600 and compact/day renders
received visual verdict98/pass. `docs/media/opennbt-home-swipe.gif` is a synthetic
native animation preview, not a Pi/phone capture.

Optimized UI SHA-256:
`e4745cec96e2dc63913a1eb684cba3fb9506b8c4be99334c28ee2a82f09dfb00`.
The guarded package is `dist/opennbt-pi5/20261031-home-swipe/`; it carries the
unchanged touch-fit compositor and pins the existing core. The reusable private
updater supports a distinct validated backup revision; all 13 fake-root
transaction/rollback tests pass. Planned backup is
`/opt/LIVI/.opennbt-backups/20261031-home-swipe`; the earlier touch-fit backup must
remain intact.

The Mac initially locked before Wi-Fi verification. After the user unlocked it,
this exact UI binary was installed as part of the newer three-binary
`20261031-video-isolation` transaction above. The standalone `home-swipe` package
was never installed and is superseded; do not deploy its older compositor/core
combination. The localhost preview also runs this release UI on Home.
The user subsequently confirmed the combined changes work well on the Pi.

## Map tile fill and touch controls revision — 2026-10-31

The user confirms the secondary map really projects on the Pi. The first
candidate left blank bands above/below its wide source in the tall tile; media
controls were also too small on the physical display.

Changed source: compositor `layout.rs`; UI `media.slint`, `media_parts.slint`,
`dab.slint`, `theme.slint`, settings `metrics.slint`/`actions.slint`/`controls.slint`/
`row.slint`, and the native preview example. The shared settings touch metric
keeps action, toggle and trailing-tool targets aligned instead of separate sizes.

- Only explicit `cluster-main` tile placement now uses centered, aspect-preserving
  cover, clipped by the existing renderer. Other/full-screen projection and
  physical dashboards retain contain behavior. Excess map edges are cropped in
  the preview; tapping still opens full-screen navigation. Source geometry is
  validated before constructing constrained Smithay geometry.
- At the native preview size, phone/DAB transport targets are 96px, play/pause is
  108px, seek has a broad independent hit band and larger time/handle graphics,
  and source selectors are larger. Shared settings actions/toggles/tool buttons,
  header controls and native menu icons also have larger touch regions.
- Graphite button surfaces and burgundy focus styling remain, with light glyphs
  readable in both dark and day mode. Compact/narrow layouts were rendered.

Verification: 41 compositor tests, 9 UI tests and 90 UI-model tests passed (140).
Actual native Slint pointer checks pass transport corners at 1600/800/320 widths,
seek-band/disabled behavior, source/DAB controls, settings action/toggle/tool/header
corners, existing map readiness/fallback and full-screen normalized CarPlay touch.
Strict touched-package Clippy with all targets/warnings denied passed. Visual
verdict96/pass and synthetic screenshots are recorded locally under
`.omx/state/opennbt-media-touch/`, `.omx/state/opennbt-touch-fit/` and
`.cache/opennbt-touch-fit/`. Updated media/settings screenshots replace the
documentation previews. No dependencies or phone/network/auth configuration were
changed by this source revision.

The optimized in-place UI/compositor update **was installed on the Pi**. The
existing core matched its expected hash and was retained. The private updater
verified expected current/new hashes and ARM64
architecture, retains both previous binaries, atomically installs verified copies
and requires stable runtime processes before success. Eleven fake-root update
tests cover success, refusal, copy/start/stop failure and crash-loop handling.
Installed UI SHA-256:
`e56b78528d183bd9897ef7dc275771ecfb9696cd2a503c5423696876ec4f6e22`.
Installed compositor SHA-256:
`376e4c6b85ad18bd7e2b7553b440609aeb17f06f0924215f2c707fe5fb217d01`.
Both previous binaries and the manifest remain at
`/opt/LIVI/.opennbt-backups/20261031-touch-fit`. No configuration, authentication,
pairing or vehicle-service files were replaced. The independent Pi AP stayed up;
Mac Wi-Fi restoration was verified in System Settings after the CLI again could
not confirm it. The existing localhost preview now runs this release UI and opens
Media. Private package/transaction evidence is under
`dist/opennbt-pi5/20261031-touch-fit/` and
`dist/opennbt-touch-fit-updates/20261031-171659/`.

Rollback restores **both** saved binaries to their original UI/compositor paths
with the kiosk stopped, then starts the kiosk; retain the unchanged core and
current phone/config data. The first flashed SD image was deliberately preserved
and does not include this later runtime update. Physical map fit/touch comfort and
phone reconnection after the update still require user confirmation; stable
process checks do not prove those outcomes.

## First candidate Pi trial: Bluetooth and CarPlay working — 2026-10-31

The user confirms the OpenNBT candidate boots and, after the phone-side reset
below, **Bluetooth and CarPlay now work**. Initial pairing failed without a
displayed code even though the iPhone saw `LIVI-Pi5` under Other Devices.

Read-only SSH diagnostics over the existing Pi AP confirmed active kiosk,
Bluetooth and AP services; `hci0` is powered, discoverable, pairable and unblocked.
The helper starts its BlueZ profile, imported authentication backend and CarPlay
listener without a startup error. No new auth/network configuration was applied.
The Mac's original Wi-Fi was verified restored through System Settings; CLI SSID
reporting is hidden and incorrectly reported restoration as unverified.

A controlled restart of only `livi-kiosk.service` retained the independent AP and
did not resolve pairing. A bounded Bluetooth capture shows successful ACL
connection and service discovery, then IO capability exchange and local User
Confirmation Reply success. About 30 seconds later, Simple Pairing Complete fails
with authentication status `0x05`, followed by disconnect reason `0x10` (Connection
Accept Timeout Exceeded). This is before CarPlay/iAP session negotiation; it does
not establish a secondary-video defect or prove which peer caused the timeout.
No owned diagnostic monitor remained after the bounded capture.

The fresh image omits phone pairings while retaining the `LIVI-Pi5` name.
The user reports that the Pi service restart alone still failed, but removing
the old receiver under iPhone Settings → General → CarPlay, restarting the iPhone
and pairing again resolved it: “Bluetooth and CarPlay now work.” This supports
stale phone-side pairing/receiver state as the cause; the individual effect of
forgetting versus restarting was not isolated. No runtime rollback or application
code change was needed. Retained comparison binaries were prepared locally but
were never deployed.

Physical boot, Bluetooth/CarPlay connectivity and live map video inside the Home
Navigation tile are user-confirmed for this candidate. The user reported map
letterboxing and undersized touch controls, addressed by the subsequent source
revision above. Touch/audio details, reconnection reliability and rapid phone
handover still need explicit acceptance checks.

Private raw diagnostics live under `dist/opennbt-connectivity/`; they contain
identifiers and must not be published. Local scripts and redacted summaries are
under `.cache/opennbt-connectivity-20261031/` and the private report folders.
The first capture hit its SSH timeout; the second used line buffering and a hard
termination deadline and completed, including an empty monitor cleanup check.

## OpenNBT SD candidate flashed and verified — 2026-10-31

At the user's request, built optimized ARM64 `LIVI`, `livi-core` and
`livi-compositor` from the current local `codex/opennbt-id6` source with the pinned
toolchain. The recorded native source fingerprint stayed unchanged during build.
Candidate-only inputs and scripts are under `.cache/opennbt-flash-20261031/`;
the retained image and native overrides were not modified.

Private candidate:
`dist/opennbt-pi5/20261031-map-tile/OpenNBT-Pi5-10.2.0-map-tile.img` (6 GiB).
SHA-256: `8bd5666bfd02c18ed11eb8c2757d6aa8d7819fd87b77b7f8119efbe8f5fc2524`.
Its manifest records the exact local source and three installed binary hashes.
Only those binaries replace files in a separate copy of the retained image;
existing boot, network and approved authentication configuration is preserved.
The map preview is enabled by the backward-compatible config default.

The baseline compressed checksum and its complete uncompressed contents matched.
All 44 offline runtime/image checks, installed binary comparisons, ext4/FAT checks
and all 87 Pi helper tests passed. Flash-target guards were checked against the
identified external removable 31.9 GB ALOGIC SD card and tested to reject missing
safety fields. The narrow local helper writes only that card, compares all 6 GiB
of image bytes, then ejects it; `--check` performs no disk writes.

The first raw-device attempt obtained administrator authentication but macOS
denied opening `/dev/rdisk9` (`Operation not permitted`), before writing any data.
That failure is recorded separately as `flash-raw-attempt-failed.json`.
The same validated candidate was successfully written through installed Raspberry
Pi Imager v2.0.12 to the identified ALOGIC 31.9 GB SD card, with no OS
customization. Verification was left enabled and observed through 100%, followed
by **Write complete!** and confirmation that the card was automatically ejected
and safe to remove. `flash-status.json`, `imager-completion.txt` and the candidate
manifest record this successful flash separately from the first failed attempt.
Subsequent physical boot and Bluetooth/CarPlay connectivity were user-confirmed
after the phone-side reset described above. Live iPhone map delivery inside the
native tile was subsequently confirmed, with letterboxing on this first image.

## OpenNBT Navigation tile video source — 2026-10-31

The local `codex/opennbt-id6` source now wires the existing CarPlay secondary
instrument-cluster map stream into the Home Navigation tile. See
[OPENNBT_MAP_PREVIEW.md](OPENNBT_MAP_PREVIEW.md) for behavior, implementation,
candidate build requirements, rollback and physical acceptance checks.

- `carPlayNavTile` defaults on, including older configs. Secondary display
  advertisement no longer requires an enabled dashboard. Apply/reconnect is
  required when changing it; false is the feature opt-out.
- UI-owned normalized geometry drives stream demand and compositor placement.
  The native globe covers the tile until a current, correctly sized buffer is
  committed. Headers/borders remain native. Paging, menus, resize, stream loss
  and phone changes restore fallback; retired surfaces cannot regain readiness.
- Per-connection preview leases and per-tag control generations protect newer
  requests from stale cleanup/acknowledgments. An active pending claim follows
  resize while retaining its original binding acknowledgment token.
- Tapping Navigation opens full-screen phone navigation with ordered core state
  changes, avoiding a later Home request overwriting the Maps request. Existing
  full-screen touch and dashboard behavior are retained.

Pinned Linux ARM64 verification: **532 tests passed** across core (323),
compositor (36), media (35), protocol/config (39), UI (9), UI-model (90) and
core-client (0). Tests include stale ownership/commands/readiness, pending-window
resize, map demand and navigation ordering. This source uses the existing
CarPlay stream implementation; it does not establish CarPlay Ultra support.
Strict Clippy (`--all-targets -- -D warnings`), pinned formatting and diff checks
passed. The native `opennbt_preview check` passed alpha-cutout/opaque-fallback,
page/menu/disabled-demand, immediate resize and pointer/full-screen touch checks.
The checked RGBA preview is a transparent video cutout, not an iPhone map capture.

No Pi, SD card, retained binary/image, authentication or vehicle service was
changed. The currently running browser preview uses older local binaries. The
next separate candidate must include rebuilt **LIVI UI, livi-core and
livi-compositor**; existing core/compositor image overrides alone are insufficient.
Container buffers/renderer checks do not prove phone map delivery or Pi scanout.
Anonymous video-window association retains an upstream late-window limitation;
rapid phone handover before the first frame needs explicit physical testing.

## ID6 visual fidelity revision — 2026-10-31

The user requested a closer NBT Evo ID6 appearance after reviewing the first
skin. The current local `codex/opennbt-id6` changes now include:

- A compact status strip with the 1997–2020 BMW roundel, M3 identity and actual
  time; the former permanent sidebar and large Main menu heading are gone.
- Three separate glass-header tiles plus a clipped preview of the next tile,
  graphite materials and dark red/burgundy selection with warm edge highlights.
- Calendar/phone composition, recessed metallic media artwork, rendered E90 M3,
  navigation globe and settings gear. Calendar data comes from the local clock;
  decorative graphics do not represent live vehicle or mapping data.
- Split settings lists and illustration panels, larger white labels, square
  selection controls and restrained media transport styling. Day-mode contrast
  and compact layouts were checked as well.
- The roundel returns to Home; tapping it on Home reveals the native menu.
  Keyboard/controller focus can still reveal that menu. Streaming projection
  keeps the full display and normalized touch coordinates.

Latest verification: 7 UI tests + 90 UI-model tests passed, plus actual pointer
checks for all six tiles, side paging, disabled media, roundel Home/menu actions
and full-screen CarPlay touches. Pinned Rust formatting and strict UI/model
Clippy passed. Native screenshots were inspected at 1600×600 and 800×300, and
home/settings day-mode renders were inspected. Final screenshots replace the
four `docs/media/opennbt-*.png` previews. The more limited first-pass visual
rating was superseded after the user's feedback; the latest manual comparison
is recorded in `.omx/state/opennbt/ralph-progress.json`.

BMW logo source and the built-in imagegen prompts for the three rendered PNGs
are recorded in `native/crates/livi-ui/ui/assets/README.md`. No Cargo dependency
was added. The development cache/image described below are reused. This source
revision has not been committed, pushed or installed on the Pi; retained image
and physical baselines below are unchanged. Font/artwork differences and phone-
provided navigation remain intentional adaptations rather than OEM assets.

## Initial OpenNBT interface source — 2026-10-31

The fork's product name is now **OpenNBT**, targeting the BMW E90 M3 first.
The GitHub repository remains `gozmanyoni/LIVI` at the user's request. The new
source changes are in local branch `codex/opennbt-id6`; the retained `main` and
Pi/image baseline below remain identifiable separately.

- Added a native ID6-inspired Home with three tiles on each of two pages:
  Media/Radio, Communication, Navigation, My Vehicle, Connected Apps and Settings.
  Tiles route to existing media, device management, projection and vehicle
  settings; no OEM radio, navigation, service status or vehicle data is invented.
- Restyled native lists, media and idle projection with dark panels, orange
  selection, thinner typography and an original E90-inspired vector profile.
  The source preview targets 1600×600 and was also checked at 800×300.
- Preserved full-screen streaming projection, normalized touch coordinates,
  configured input shortcuts and nested settings/editing behavior. Home/Back
  navigation uses existing controller focus; physical CAN/iDrive input is still
  a separate integration lane.
- Renamed visible application/package metadata and About, preserving NOTICE
  attribution. Internal binary/config/service/app IDs and paired wireless names
  stay compatible. Update checks now default to this fork; a missing release
  channel reports an error rather than falling back to upstream.
  New configurations use OpenNBT for the phone-facing UI label; saved labels
  and wireless identities are not migrated automatically.
- The preview example uses the actual Slint software renderer and synthetic
  media/settings state. It accesses no Pi, authentication identity or live core.
  Four selected screenshots under `docs/media/opennbt-*.png` are synthetic
  development previews, not private captures.

Verification using the pinned `beta-2026-10-18` Rust toolchain in a local Linux
ARM64 Docker environment: 7 UI tests, 90 UI-model tests, 36 core protocol/config
tests, 2 packaging-tool tests and 3 core updater tests passed (138 total).
The pointer smoke check verifies all
six tile callbacks, both page arrows, disabled-media suppression, and six full-
screen CarPlay touch events. Formatting and strict Clippy for touched Rust
packages passed. All 87 Pi helper tests and the AppStream XML parse/name/ID check
passed. Screenshots were visually checked; verdict history is local under
`.omx/state/opennbt/ralph-progress.json`.

The build reuses `/private/tmp/livi-core-pi5-build/{cargo,rustup,target}`. Local
development image `opennbt-ui-verify:local` adds the fontconfig development library
needed for the existing Slint dependency; no Cargo dependencies were added.
See [OPENNBT_DESIGN.md](OPENNBT_DESIGN.md) for reproduction commands and boundaries.

**Not deployed:** the working Pi, its retained binaries and private flash image
were not changed. These tests are container checks, not physical display/touch,
CarPlay reconnection, cold-boot performance or BMW service validation. New shell
labels are currently English. Before deployment, build a separate candidate,
retain the existing binaries for rollback, then verify display, touch, phone
handover and OEM audio on the actual setup. Reverting this local source slice
does not require a Pi rollback because no hardware change was made.

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
