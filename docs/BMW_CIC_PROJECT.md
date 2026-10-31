# OpenNBT — BMW E90 / CIC interface project

Build a modern BMW E9x interface on native LIVI, with wireless CarPlay on a
Raspberry Pi 5. **Retain the original CIC without its screen.** CIC remains the
OEM MOST network master and provides existing audio and vehicle services. This
project replaces the visible interface; it does not replace the MOST master.

The fork's product name is **OpenNBT**. The source interface now follows NBT Evo
ID6's two-page tile layout and list styling, with an E90 M3 identity and existing
native feature destinations. See [the UI design and menu mapping](OPENNBT_DESIGN.md).
This visual shell does not establish live BMW telemetry or OEM control support.

Repository: [gozmanyoni/LIVI](https://github.com/gozmanyoni/LIVI).
Development baseline: `main` in `/Users/yonigozman/Development/PiMost/LIVI`.
Read [SESSION_HANDOFF.md](SESSION_HANDOFF.md) before starting implementation.

## Architecture

```text
PiMOST USB -> native mostd -> BMW vehicle adapter -> LIVI telemetry / BMW UI
Vehicle K-CAN <-> future dual-channel Pi CAN gateway <-> CIC CAN branch
                                  |-> LIVI / CarPlay controller input
CIC <-> existing MOST ring / OEM modules
```

- Keep native vehicle services independent of LIVI's UI process. An app restart
  must not restart vehicle interfaces. Start services in parallel where their
  real dependencies permit it; preserve the working fast-boot configuration.
- Use the existing LIVI telemetry model before inventing another transport.
  The local HTTP input is `POST http://127.0.0.1:4000/telemetry`; payloads can be
  partial. See `native/crates/livi-core-proto/src/telemetry.rs` for units, merge
  rules and downstream consumers. Widget support is a subset of the schema.
- Normalize unknown and stale input deliberately. The MOST daemon uses sentinel
  values in some fields; never display an unknown sensor as a valid zero or
  claim freshness after transport loss. LIVI partial updates retain old values,
  so define and test expiry/disconnect semantics before connecting live feeds.
- UI intent is not confirmed vehicle state. Add command support only with an
  observed protocol, a specific capability and a way to confirm the result.

## Reuse boundaries

The current native MOST implementation lives in the sibling CarPodGo repository:
`/Users/yonigozman/Development/carpodgo/tools/pimost-native-mostd/`.
**`mostd.cpp` and `protocol_test.cpp` are the implementation authority; its README
has stale passive-only claims that conflict with current code.**
It exposes `MOSTD/1` on loopback TCP port 4040 and covers vehicle/radio/amplifier/
source state, plus read-only discovery of climate/CD targets. Existing packaging
cross-compiles for the older CarPodGo target; Pi 5 needs ARM64 packaging.

**Current mostd accepts explicit radio/amplifier/source intents and contains a
source-allocation state machine.** This is newer than its passive-only README.
Existing writer code is experimental/capability-gated: its presence and host
protocol tests do not prove successful audio routing on the vehicle. An earlier
acknowledged source allocation was silent in the car. Keep the first LIVI bridge
read-only: subscribe to state and emit **no `INTENT` commands**. Audit automatic
startup traffic separately before deploying the daemon on Pi. Do not expose
working-looking OEM controls until their physical effects and recovery are tested.

The CAN reference is
[superwofy/E9X-M-CAN-Integration-Module](https://github.com/superwofy/E9X-M-CAN-Integration-Module),
with a local checkout at
`/Users/yonigozman/Development/E9X-M-CAN-Integration-Module`.
Use it as protocol research. Its author targets a different vehicle configuration
and includes drivetrain/chassis writes; importing the firmware wholesale is not
part of this project. Validate relevant frames against this vehicle's captures.

## Independent implementation lanes

| Lane | Initial deliverable | Suggested ownership |
| --- | --- | --- |
| MOST telemetry | ARM64 daemon packaging/audit and tested read-only MOSTD-to-LIVI adapter, with recorded fixtures and explicit stale-state behaviour | New vehicle-service module and its tests; coordinate schema changes |
| BMW native interface | 1600x600 Slint pages for vehicle/PDC/status, initially using fixtures through existing telemetry | `livi-ui`, `livi-ui-model`; avoid changing protocol types without agreement |
| iDrive and CAN gateway | Wiring/hardware validation, capture/replay decoder, input ownership and forwarding policy | New CAN service, fixtures and deployment docs |
| OEM audio/source control | Capability inventory grounded in current mostd/firmware/captures; read-only status first | Separate investigation and confirmed command adapter |

The Pi-only CAN gateway is the agreed prototype direction; no extra Teensy is
required. A true filter needs two separated CAN segments around CIC's branch.
An OBD connection or passive tap cannot suppress frames already received by CIC.
Correct low-speed K-CAN electrical interfaces are still a hardware prerequisite;
a generic high-speed CAN HAT set to 100 kbit/s is not a validated substitute.
No CAN hardware or live filtering has been installed or tested yet.

The future gateway should prevent hidden CIC menu movement from iDrive/steering
inputs while LIVI owns those controls. Required status, handshake, wake/sleep and
non-infotainment traffic must continue. Determine exact frames, counters and
neutral-event handling from captures; do not invent a broad drop list. Define
volume/mute ownership to avoid double actions. Test CIC recovery after the Pi's
initial boot gap before relying on inline operation.

## Completion gates

1. Feed recorded and then live MOST telemetry into LIVI without audio changes.
2. Demonstrate BMW UI operation and unavailable/stale states on the target panel.
3. Validate CAN wiring, passive capture and replay before enabling inline filters.
4. Confirm each OEM control and its resulting state individually.
5. Retest ignition wake/sleep, audio/chimes, display/touch and wireless CarPlay
   after integration changes. Keeping CIC preserves the backend, but each
   screenless OEM function still needs a verified UI/control path.

No BMW bridge, CAN gateway, BMW-specific UI or OEM source-control integration is
implemented by this handoff. Wireless CarPlay and the Pi boot/image foundation
are already working; see the handoff for evidence and exact limitations.
