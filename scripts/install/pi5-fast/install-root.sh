#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
# Native compositor and bundled GStreamer: no X11 server or desktop environment.
apt-get install -y --no-install-recommends \
  bluez libspa-0.2-bluetooth hostapd dnsmasq-base iw rfkill iproute2 \
  avahi-daemon avahi-utils pulseaudio-utils libva2 libva-drm2 libva-x11-2 \
  libssh-4 libfontconfig1 libxkbcommon0 libseat1 libinput10 libgbm1 \
  libegl1 libgles2 libgl1 libxrender1 libxcb-render0 libwayland-client0 libwayland-cursor0 libwayland-egl1 \
  libgl1-mesa-dri libegl-mesa0 mesa-libgallium libpam-systemd dbus-user-session \
  seatd pipewire pipewire-pulse wireplumber xdg-user-dirs sudo openssh-server \
  ca-certificates fonts-dejavu-core libssl3t64 file fuse3
if ! id livi >/dev/null 2>&1; then
  LIVI_BASE_USER=$(getent passwd 1000 | cut -d: -f1 || true)
  if [[ -n "$LIVI_BASE_USER" ]]; then
    usermod --login livi --home /home/livi --move-home --shell /bin/bash "$LIVI_BASE_USER"
    LIVI_BASE_GROUP=$(getent group 1000 | cut -d: -f1)
    if [[ "$LIVI_BASE_GROUP" != livi ]]; then groupmod --new-name livi "$LIVI_BASE_GROUP"; fi
  else
    useradd --create-home --uid 1000 --user-group --shell /bin/bash livi
  fi
fi
for group in video render input plugdev audio adm sudo; do
  if getent group "$group" >/dev/null; then usermod -aG "$group" livi; fi
done
ssh-keygen -A
ln -snf /usr/share/zoneinfo/Australia/Sydney /etc/localtime
printf 'Australia/Sydney\n' > /etc/timezone
apt-get clean
