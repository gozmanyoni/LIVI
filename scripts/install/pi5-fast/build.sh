#!/bin/bash
set -Eeuo pipefail

# Runs only in the build container. /output holds ordinary image files, never disks.
IMAGE=/output/LIVI-Pi5-10.2.0-fastboot.img
ROOT=/mnt/livi-root
# Never silently omit the regression-tested display fixes.
for binary in livi-core livi-compositor; do
  if [[ ! -x "/inputs/${binary}-patched" ]]; then
    echo "Missing tested native fix: ${binary}-patched. See pi5-fast/README.md." >&2
    exit 1
  fi
done
LOOP=
cleanup() {
  local status=$?
  trap - EXIT
  if mountpoint -q "$ROOT"; then
    umount -R "$ROOT" || true
  fi
  if [[ -n "$LOOP" ]]; then losetup -d "$LOOP"; fi
  exit "$status"
}
trap cleanup EXIT

mkdir -p /output "$ROOT"
if [[ ! -f "$IMAGE" ]]; then
  cp --sparse=always /inputs/base.img "$IMAGE"
  truncate -s 6G "$IMAGE"
  parted -s "$IMAGE" resizepart 2 100%
fi
LOOP=$(losetup --find --show --partscan "$IMAGE")
for part in 1 2; do
  node="${LOOP}p${part}"
  if [[ ! -b "$node" ]]; then
    dev=$(cat "/sys/class/block/$(basename "$node")/dev")
    mknod "$node" b "${dev%:*}" "${dev#*:}"
  fi
done
e2fsck -fy "${LOOP}p2" || [[ $? -eq 1 ]]
resize2fs "${LOOP}p2"
mount "${LOOP}p2" "$ROOT"
mount "${LOOP}p1" "$ROOT/boot/firmware"
mount --bind /dev "$ROOT/dev"
mount -t proc proc "$ROOT/proc"
mount -t sysfs -o ro sysfs "$ROOT/sys"
mkdir -p "$ROOT/run"
if [[ ! -e "$ROOT/etc/resolv.conf.livi-build-original" && ! -L "$ROOT/etc/resolv.conf.livi-build-original" ]]; then
  cp -a "$ROOT/etc/resolv.conf" "$ROOT/etc/resolv.conf.livi-build-original"
fi
rm -f "$ROOT/etc/resolv.conf"
cp -L /etc/resolv.conf "$ROOT/etc/resolv.conf"
printf '#!/bin/sh\nexit 101\n' > "$ROOT/usr/sbin/policy-rc.d"
chmod 755 "$ROOT/usr/sbin/policy-rc.d"

echo 'Installing runtime packages into the image (not at first boot).'
cp /builder/install-root.sh "$ROOT/tmp/livi-install-root.sh"
chroot "$ROOT" /bin/bash /tmp/livi-install-root.sh > /output/package-install.log 2>&1
rm "$ROOT/tmp/livi-install-root.sh"

if [[ ! -x "$ROOT/opt/LIVI/AppRun" ]]; then
  chmod +x /inputs/LIVI-10.2.0-linux-arm64.AppImage
  offset=$(/inputs/LIVI-10.2.0-linux-arm64.AppImage --appimage-offset)
  mkdir -p "$ROOT/opt"
  unsquashfs -no-progress -o "$offset" -d "$ROOT/opt/LIVI" /inputs/LIVI-10.2.0-linux-arm64.AppImage > /output/app-extract.log
fi
# Local regression-tested core override fixes canonical displayMode persistence.
if [[ -f /inputs/livi-core-patched ]]; then
  install -m 0755 /inputs/livi-core-patched "$ROOT/opt/LIVI/resources/core/livi-core"
fi
if [[ -f /inputs/livi-compositor-patched ]]; then
  install -m 0755 /inputs/livi-compositor-patched "$ROOT/opt/LIVI/resources/compositor/livi-compositor"
fi
python3 /builder/configure.py "$ROOT"
python3 /builder/validate.py "$ROOT" | tee /output/validation.json

rm -f "$ROOT/usr/sbin/policy-rc.d" "$ROOT/etc/resolv.conf"
mv "$ROOT/etc/resolv.conf.livi-build-original" "$ROOT/etc/resolv.conf"
rm -rf "$ROOT/var/lib/apt/lists/"* "$ROOT/var/cache/apt/archives/"*.deb
truncate -s 0 "$ROOT/etc/machine-id"
rm -f "$ROOT/var/lib/dbus/machine-id"
ln -s /etc/machine-id "$ROOT/var/lib/dbus/machine-id"
sync
umount -R "$ROOT"
e2fsck -fn "${LOOP}p2" > /output/rootfs-check.txt 2>&1
fsck.vfat -n "${LOOP}p1" > /output/bootfs-check.txt 2>&1
sfdisk -d "$IMAGE" > /output/partition-table.txt
losetup -d "$LOOP"
LOOP=
echo 'Compressing the validated flash image.'
xz -T2 -3 --keep --force "$IMAGE"
cd /output
sha256sum "$(basename "$IMAGE").xz" > SHA256SUMS
echo 'Flash image complete.'
