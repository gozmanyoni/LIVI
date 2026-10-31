# OpenNBT Navigation tile video

The Home Navigation tile can show the existing CarPlay secondary
instrument-cluster map video behind the native Slint interface. This uses the
existing CarPlay display/stream implementation; it does not implement CarPlay
Ultra or a local navigation engine. Apple Maps on the target iPhone remains the
first physical acceptance target. The user confirmed live secondary map video
projects on the Pi; other navigation applications remain unverified.

## Behavior

- `carPlayNavTile` defaults to `true`, including when loading an older config.
  The secondary CarPlay display is advertised even if dashboard screens are off.
  Changing this option requires Apply/reconnecting the CarPlay session.
- Home page one reports the Navigation body rectangle in normalized content
  coordinates. The core requests the secondary stream only for an active CarPlay
  phone while the native Home tile is visible. Existing dashboard demand still
  works independently; Android Auto demand is unchanged.
- The compositor fills and clips the map inside that rectangle using a centered,
  aspect-preserving crop. Existing `clusterWidth`/`clusterHeight` defaults remain
  1280×720. A wide source loses its outer sides in the taller preview, rather than
  leaving blank bands above/below it. Full-screen projection and physical
  dashboards retain their original fit; tap the tile for the complete map.
- Slint leaves the tile body transparent only after a matching buffer is
  committed for the confirmed placement. The tile header, selection border and
  adjacent tiles remain native and opaque. Before readiness, during resize, on
  disconnect or with the feature disabled, the globe fallback covers the body.
- Tapping the tile requests full-screen projection and phone navigation in a
  single ordered core operation. The tile itself does not forward map gestures.
  Full-screen CarPlay touch mapping remains unchanged.
- CarPlay playback video belongs to full-screen projection. Returning to native
  menus hides its picture without stopping playback or changing the vehicle's
  playback permission. Its higher video layer cannot show through the Navigation
  cutout; the compositor also reserves an explicitly placed map's output against
  delayed playback visibility commands.

## Implementation

`livi-core-proto` carries the viewport and readiness state. Each UI connection
owns a preview lease, so an old connection's cleanup cannot clear a newer UI's
request. Projection owns placement and secondary-stream demand; services mirrors
visibility. The UI checks exact confirmed geometry as well as readiness, including
the redraw before a deferred resize callback reaches the core.

`livi-media` sends normalized `videobox` commands independently of source crop
configuration. Readiness is invalidated synchronously when placement or ownership
changes. Per-tag generations on control commands and readiness replies reject
stale acknowledgments across rapid changes; socket epochs reject replies from
old connections. The compositor requires the configured logical buffer size and
clips the video render elements to the destination. Readiness means a committed
buffer, not proof of physical scanout or a usable route from the phone.

## Candidate build and rollback

Fresh image builds must package all three matching components using
`rust-toolchain.toml`:

- `LIVI` (`livi-ui`)
- `livi-core`
- `livi-compositor`

Keep each candidate separate from retained images and binaries. The current
image helper's core/compositor overrides alone are insufficient: its candidate
must also include the rebuilt UI. No authentication, Wi-Fi, EEPROM, CAN or MOST
changes are part of this feature. The GStreamer host protocol is unchanged.

The first flashed candidate contained the live preview with aspect-fit bands.
The subsequent tile-fill and larger-touch-control revision changes the UI and
compositor only. Updating those binaries on the existing Pi preserves phone
pairing and receiver identity; reflashing a clean image resets them.

To disable the tile stream, set `carPlayNavTile` to `false`, Apply and reconnect
the phone. Existing configured dashboards may still request the secondary
display. Full rollback restores the retained matching binaries/image and config.

## Physical acceptance checklist

1. Boot the separate candidate and verify Home layout, normal projection, touch
   and OEM audio before testing the map.
2. Connect the iPhone, start Apple Maps navigation, then return to OpenNBT Home.
   Confirm the Navigation body shows real changing map video and stays clipped
   within the native tile. An active connection alone may not produce map video.
3. Page away/back and open/close the native menu. Verify the fallback appears
   until the stream is ready, with no leaked full-screen video behind other pages.
4. Tap Navigation and verify phone navigation opens full screen. Return Home;
   verify selection and input still work. Check call/projection interruptions.
5. Disconnect/reconnect and switch phones repeatedly. Verify no stale map remains,
   no wrong-phone preview appears and full-screen CarPlay recovers normally.
6. Test `carPlayNavTile: false`, Apply/reconnect, then restore it. Retain startup
   and reconnection observations without collecting identifying captures.

Anonymous Wayland video windows retain the upstream FIFO claim association.
Generation checks retire known old surfaces, but cannot prove ownership of a
window that arrives late after its claim was cancelled. Rapid phone switching
before window creation therefore remains an explicit hardware acceptance case.

Container tests and Slint software-renderer checks cover configuration migration,
lease ownership, stream demand, navigation ordering, clipping, readiness,
disconnect/reconnect and immediate resize fallback. They do not establish iPhone
interoperability, Pi DRM scanout or hardware performance.

Playback isolation regression checks exercise default-hidden video startup,
Home/full-screen transitions, permission changes, a delayed window claim and
continued player control without a URL restart. State-follower tests also cover
camera/dashboard transitions. Compositor checks reserve the map output before
its first buffer and restore playback after tile placement is cleared. On the
Pi, repeat the playback-start → native Home → full-screen CarPlay sequence while
navigation is active, including playback stop/restart and reconnect.
