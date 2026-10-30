# BMW LIVI project instructions

Read `docs/SESSION_HANDOFF.md` and `docs/BMW_CIC_PROJECT.md` first. They record the
working hardware baseline, evidence and boundaries for new sessions.

## Project and implementation

- This is LIVI 10.2's **native Rust/Slint** codebase under `native/`, not the old
  Electron implementation. Preserve upstream notices, licensing and provenance. Retain the attribution
  required by `NOTICE` in README and About/legal notices.
- Retain the headless OEM CIC as MOST master/audio backend. The Pi replaces its
  visible interface and adds CarPlay. Do not start implementing a replacement
  MOST network master or import unrelated drivetrain/chassis modifications.
- Prefer the current native CarPodGo mostd implementation over old Electron
  integration assumptions. Its README is stale: current code accepts explicit
  control intents. The first LIVI adapter must emit no intents; existing writer
  code remains capability-gated until its physical effects are verified.
- Reuse `livi-core-proto` telemetry and native UI patterns. Model unknown/stale
  values and confirmed command state explicitly. Do not invent BMW frame IDs or
  payload mappings without source/capture evidence.
- No new dependencies without explicit request. Keep changes small and
  reversible. Do not revert other sessions' edits.

## Multiple sessions

Claim a bounded lane and file ownership at the start. Suggested lanes are MOST
telemetry/service, BMW Slint UI, CAN input/gateway, and OEM audio research.
Coordinate shared `livi-core-proto` types, core wiring, root build files and image
configuration before editing them. Use separate branches/worktrees for
independent implementation; integrate tested slices into the fork's `main`.
The lane list is a work split, not evidence that those features already exist.

## Credentials and hardware

- Never commit certificates, private keys, pairing databases, passwords, access
  files, raw identifying captures, or private flash images. Approved test auth
  files remain private outside source. No public CI/release artifact may embed
  them. Redact logs and inspect staged files before committing/pushing.
- `dist/` and `.cache/` contain private generated runtime/image artifacts. They
  are local inputs, not source. Do not broadly copy them into tracked folders.
- The current Pi works. Do not reflash, change EEPROM, or disrupt its network
  just to inspect source. A hardware experiment needs a narrowly defined change,
  rollback and reconnect plan. Do not guess credentials or print access files.
- CAN filtering and writes are not validated. Start from capture/replay and
  explicit capability tests. Keep CarPlay and vehicle services independent.

## Verification and handoff

Use the pinned Rust toolchain in `rust-toolchain.toml`. Run relevant crate tests,
formatting and Clippy for touched Rust crates, plus the Pi helper suite for image
or diagnostic changes. Tests should verify behaviour, including failure/rollback
paths. Do not report host tests as physical Pi verification.

```sh
python3 -m unittest discover -s scripts/install/pi5-fast -p 'test_*.py'
cargo fmt --manifest-path native/Cargo.toml --check
# Select packages appropriate to the change; Linux multimedia dependencies apply.
cargo test --manifest-path native/Cargo.toml -p livi-compositor
cargo test --manifest-path native/Cargo.toml -p livi-media
```

The existing ARM64 build caches may be outside this checkout; inspect the handoff
before rebuilding. Keep experimental binaries separate from the retained image.
Update `docs/SESSION_HANDOFF.md` with tested results, limitations and rollback
paths when a lane lands. Do not re-run rejected boot experiments without new
evidence. Commit messages should explain intent and use applicable Lore trailers
(`Tested:`, `Not-tested:`, `Constraint:`, `Directive:`) for durable decisions.
