# Pi 5 fast-boot CarPlay test image

Builds a private flash image for Raspberry Pi 5 and the Waveshare 9.3-inch
1600×600 HDMI/USB capacitive display. LIVI 10.2.0 starts directly under its native
DRM/Wayland compositor. There is no desktop or display manager.

## Build

This is a private image assembly tool, not a source-to-binary builder. It requires
the two verified ARM64 native overrides described below. The main local checkout
retains those binaries and all cached inputs; a fresh clone does not include them.

From the repository root, with Docker running and Python 3 installed:

```sh
scripts/install/pi5-fast/build-image.sh
```

The existing approved `certificate.p7b` and `identity.pk8` are read from LIVI's
user-data directory. Set `LIVI_MFI_DIR` to use another explicitly selected directory.
Identity bytes are never copied into source or Docker image layers. They are
embedded in the final SD image with directory mode 0700 and file mode 0600.

The upstream source baseline and download checksums are pinned. Descendant
commits are accepted by the ancestry guard; review image-template compatibility
when upgrading the baseline. The rolling nightly URL is
checked against the expected hash and fails if upstream replaces it. Inputs are
cached under `.cache/pi5-fast`; output goes under `dist/pi5-fastboot`. A rerun
resumes the local image rather than deleting it. Remove only that build's output
image to start fresh. Both cache and output paths are ignored by Git.

The Debian container tag and apt repositories are not snapshot-pinned, so a new
assembly can differ from the retained image even with pinned download hashes.
Keep the recorded image/binary manifests when comparing builds.

The privileged container mounts image files and the selected authentication
folder; it never receives an SD card or a physical disk. Do not publish the
resulting private test image or its access files.

## Flash and connect

- Use Raspberry Pi Imager's custom-image option with the resulting `.img.xz`.
  Skip OS customization: the user, region, kiosk and Wi-Fi are already configured.
- Use an 8 GB or larger SD card. The fixed image is 6 GiB and avoids a first-boot
  resize/reboot. Remaining card capacity is unused until explicitly expanded.
- Attach the display to HDMI0, nearest the Pi's USB-C power input, and attach its
  USB touch/power connection. Display mode and CarPlay video are 1600×600.
- Pair the iPhone with **LIVI-Pi5** in Bluetooth and approve CarPlay.
- `START-HERE.txt` alongside the image contains the generated login and AP
  passwords. SSH is available over Ethernet at `livi@livi-pi5.local`.

## Startup choices

- Raspberry Pi OS Lite 64-bit Trixie, with all dependencies installed offline.
- LIVI pre-extracted under `/opt/LIVI`; no FUSE mount or extraction at startup.
- UI and dedicated Wi-Fi AP start in parallel without waiting for internet.
- Native onboard `wlan0` and `hci0`; Australian regulatory domain, non-DFS channel
  36, 5 GHz, conservative 20 MHz channel width.
- Bluetooth and WirePlumber are preconfigured to avoid first-launch restarts.
- Cloud-init is disabled after offline image provisioning; no boot-time reconfiguration.
- No user wizard, disk-swap allocation, first-boot resize, automatic EEPROM
  rewrite, Plymouth splash, serial console, or background apt/man-db jobs.
- Filesystem checks, ext4 journaling, device discovery, audio, IPv6, Bluetooth
  firmware and accelerated graphics remain available. No overclock is applied.
- Kiosk restarts on exit, so a slowly powered screen can recover. Stop it
  deliberately with `sudo systemctl stop livi-kiosk`.

`sudo livi-boot-report` reports systemd timings, critical chains, radio state and
logs. Actual power-on-to-UI and power-on-to-CarPlay timings require a physical Pi
and the intended SD card, screen and iPhone. No numerical boot-time claim has
been made from the container checks.

The builder validates ARM64 executable dependencies, H.264/H.265 parsers and
decoders, Wayland sink loading, systemd units, sudoers, authentication permissions,
display configuration, and both filesystems. The complete package inventory and
verification reports accompany the output.

## Physical boot-speed trial, 2026-10-30

Four reboots of the same Pi 5, SD card and LIVI configuration compared the
existing image against adding `/etc/cloud/cloud-init.disabled`:

| Measurement | Existing | Cloud-init disabled |
| --- | --- | --- |
| Linux startup, run 1 | 5.636 s | 4.457 s |
| Linux startup, run 2 | 5.615 s | 4.435 s |
| Mean | 5.626 s | 4.446 s |

Mean improvement was 1.180 s (21%). These are `systemd-analyze time` kernel plus
userspace measurements from reboots; firmware startup, first visible frame and
phone connection time are not included. This is not a cold power-on timing.
LIVI core/UI/compositor, display connector, Bluetooth and Wi-Fi AP checks passed
after each reboot; LIVI's config hash stayed unchanged. The user confirmed that
the display and wireless CarPlay still work. The marker remains on the Pi.
The temporary recovery service/timer was removed and the Mac's Wi-Fi restored.

`boot-trial.py` provides root-only snapshots, marker-only apply, and rollback;
it never reboots or changes networks itself. The eight marker/snapshot tests run
with `python3 -m unittest discover -s scripts/install/pi5-fast -p 'test_boot_trial.py'`.
On the tested Pi, undo the change with
`sudo python3 /home/livi/.cache/livi-boot-trial.py rollback` and then reboot.
Private measurements are saved under `dist/pi5-boot-trials/20261030-115528/`.

## Follow-up software boot trials, 2026-10-30

No further startup change was retained. Cloud-init remains disabled.

| Measurement, seconds from kernel start | Current setup, mean of 2 | Without session/network ordering, mean of 2 |
| --- | --- | --- |
| Linux startup complete | 4.470 | 6.232 |
| LIVI core process launched | 4.450 | 3.475 |
| LIVI compositor process launched | 4.760 | 3.850 |
| LIVI UI process launched | 7.275 | 6.860 |

The session/network experiment copied the exact installed user-session unit and
removed only its `After=network.target` token. All local-filesystem, NSS and
audio/session ordering was preserved. Audio, radios and display process checks
passed, but networking and total Linux startup regressed. The experiment was
rejected and the vendor unit restored; it is not part of the image configuration.
Process launch is not the time of the first displayed frame. These reboot
measurements exclude firmware and CarPlay handover.

A separate, guarded snapshot-reaper experiment verified plain ext4 storage,
no LVM devices and no leftover e2scrub mapper snapshots. Its one exploratory
measurement was 4.517 s, without a clear gain; the service mask was removed.
The collection guard stopped after a normal `huVolume` update. All startup-critical
configuration hashes matched. Normal filesystem checks and journaling were retained.

The manual `network-boot-trial.py` and `scrub-boot-trial.py` helpers preserve
backups and offer `inspect`, `snapshot LABEL`, `apply` and `rollback`. Neither
reboots nor changes networks itself. They are diagnostic tools, not enabled image
features. The unit-copy experiment must be reviewed against the installed vendor
unit on every system upgrade; it is intentionally absent from normal startup.
Run all 87 Python regression checks with
`python3 -m unittest discover -s scripts/install/pi5-fast -p 'test_*.py'`.
Private reports are under `dist/pi5-network-boot-trials/20261030-123347/` and
`dist/pi5-scrub-boot-trials/20261030-124034/`.

Final restored reboot: 4.452 s Linux startup. Software health checks passed;
the user confirmed display and wireless CarPlay. Both experiment overrides
and temporary recovery timers were removed; cloud-init remains disabled.
The Mac returned to its original home network. No flash-image startup
configuration changed during these follow-up trials.

## Verified compositor readiness improvement, 2026-10-30

The core now retries the compositor control socket every 50 ms for at most the
first five seconds, then returns to the normal 500 ms cadence. The first successful
connection also ends fast polling. Graphics initialization and UI readiness gating
remain unchanged. The three new paused-clock socket tests cover delayed startup,
the five-second limit and reconnect cadence; all 29 media tests, formatting and
Clippy checks passed.

Two baseline and two candidate reboots on the physical Pi measured:

| Measurement, seconds from kernel start | Baseline mean | Faster initial polling mean |
| --- | --- | --- |
| LIVI UI process launched | 7.310 | 7.060 |
| Linux startup complete | 4.493 | 4.488 |
| Dedicated Wi-Fi AP started | 4.337 | 4.327 |

UI process launch improved by 0.250 s. This is not a measurement of the first
visible frame, power-on time or CarPlay connection time. The user confirmed
display, touch and wireless CarPlay after the change. Audio/radio/display checks
passed on both candidate boots; only a runtime `huVolume` update is permitted
without invalidating startup-critical configuration comparisons.

The working Pi retains the candidate core. The image builder uses the same verified
binary from `.cache/pi5-fast/livi-core-patched` and verifies installed bytes against
both native overrides. The original core is kept in
`.cache/pi5-fast/livi-core-before-ui-readiness` and on the Pi under
`/var/lib/livi/ui-boot-trial-20261030/livi-core.before`.
Rollback on the tested Pi is
`sudo python3 /home/livi/.cache/livi-ui-boot-trial.py rollback`, followed by reboot.
The manual `ui-boot-trial.py` validates ARM64 ELF, SHA and libraries, makes an atomic
replacement, and protects against overwriting unrelated changes during rollback.
All 68 Python helper tests passed. Reports are in
`dist/pi5-ui-boot-trials/20261030-133951/`.

## Compositor initialization profiling, 2026-10-30

Optional `LIVI_BOOT_TRACE=1` instrumentation now records bounded one-shot monotonic
initialization phases and the first main UI-containing DRM frame completion.
It does not count blank/video/decoration-only frames as UI. Six frame/trace tests
bring the compositor suite to 26 tests; formatting and Clippy passed.

Two temporary instrumented reboots measured GBM device creation at **1.194 s mean**.
State setup was 0.109 s, input setup 0.124 s, renderer/shaders 0.082 s, and connector
scanning 0.001 s. The first UI-containing DRM frame completed at **7.472 s after
Linux started**, not at an externally measured power-on time.

Retained firmware logs reported OS handoff at 9191/9239 ms on their own clock.
The installed EEPROM dates from 2025-08-27 and has BOOT_UART=1. SD is already first
in its boot order. No firmware setting or EEPROM version was changed. The firmware
and Linux clock origins were not correlated, so their times are not combined.

The original compositor and normal environment were restored, followed by a healthy
normal reboot. Display, touch and wireless CarPlay were confirmed. The faster core
and working flash image remain unchanged. Trace logs are opt-in only; no additional
recording service remains running.

The manual `compositor-profile-trial.py` helper makes atomic compositor/environment
changes and safely restores both. Its 19 fake-root tests bring all helper tests to
87. Detailed measurements and the temporary binary are under
`dist/pi5-compositor-profiles/20261030-140229/`, including `REPORT.md` and `findings.json`.

## Sources

- [LIVI source](https://github.com/f-io/LIVI/tree/e7b20b818f052958afc95dcf91acc25c70643c02)
- [Raspberry Pi OS Lite](https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2026-10-06/)
- [Waveshare panel instructions](https://www.waveshare.com/wiki/9.3inch_1600x600_LCD)
- [Raspberry Pi boot configuration](https://www.raspberrypi.com/documentation/computers/config_txt.html)

## Verified fixes and diagnostic collection

The corrected image includes loadable sudo-rule filenames, preserved display modes,
and progressive 1600x600 selection. Verification passed 13 core tests, 20 compositor
tests and 44 image checks. Wireless CarPlay was confirmed on the physical Pi 5
with iPhone Personal Hotspot off and USB disconnected.

The tested native overrides are required in .cache/pi5-fast/ as
livi-core-patched and livi-compositor-patched. Copies and the source patch are
provided in dist/pi5-fastboot/native-fixes/; restore them from there if the cache
is cleared. The builder refuses to silently use the affected upstream binaries.

The Mac fallback collector is scripts/collect-pi-logs.command. It saves local
Bluetooth/display/boot logs and restores the original Wi-Fi on success, failure
or interruption. Use --check for a prerequisite-only check or --no-switch when
already connected to the Pi. Reports go under dist/pi5-diagnostics/.
