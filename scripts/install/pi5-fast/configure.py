#!/usr/bin/env python3
"""Preconfigure the offline rootfs. Identity inputs are mounted read-only, not in source."""
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys

root = Path(sys.argv[1])
output = Path('/output')
source = Path('/source')
uid = 1000

def write(path, text, mode=0o644):
    target = root / path.lstrip('/')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    target.chmod(mode)
    return target

def chroot(*args, **kwargs):
    return subprocess.run(['chroot', str(root), *args], check=True, **kwargs)

credentials_file = output / 'access.json'
if credentials_file.exists():
    credentials = json.loads(credentials_file.read_text())
else:
    credentials = {'user': 'livi', 'password': secrets.token_urlsafe(18),
                   'wifi_password': secrets.token_urlsafe(15)}
    credentials_file.write_text(json.dumps(credentials, indent=2) + '\n')
    credentials_file.chmod(0o600)
chroot('/usr/sbin/chpasswd', input=f'livi:{credentials["password"]}\n', text=True)
write('/etc/hostname', 'livi-pi5\n')
hosts = (root / 'etc/hosts').read_text()
hosts = re.sub(r'^127\.0\.1\.1.*$', '127.0.1.1\tlivi-pi5', hosts, flags=re.M)
if '127.0.1.1' not in hosts:
    hosts += '\n127.0.1.1\tlivi-pi5\n'
write('/etc/hosts', hosts)

config = {
    'carName': 'LIVI-Pi5', 'wirelessCpEnabled': True, 'wirelessAaEnabled': False,
    'wifiDedicatedInterface': True, 'wifiInterface': 'wlan0', 'btAdapter': 'hci0',
    'wifiType': '5ghz', 'wifiChannel': 36, 'wifiChannelWidth': 20, 'country': 'AU',
    'wifiPassword': credentials['wifi_password'], 'autoConn': True,
    'timezone': 'Australia/Sydney', 'startPage': '/',
    'kiosk': {'main': True, 'cluster': False, 'aux': False},
    'projectionWidth': 1600, 'projectionHeight': 600, 'projectionFps': 60,
    'mainScreenWidth': 1600, 'mainScreenHeight': 600,
    'displayMode': {'main': '1600x600', 'cluster': '', 'aux': ''},
    'gpsEnabled': False, 'debugLogging': False,
}
data = root / 'home/livi/.config/LIVI'
data.mkdir(parents=True, exist_ok=True)
write('/home/livi/.config/LIVI/config.json', json.dumps(config, indent=2) + '\n', 0o600)
auth = data / 'mfi'
auth.mkdir(exist_ok=True, mode=0o700)
for name in ('certificate.p7b', 'identity.pk8'):
    shutil.copyfile(Path('/auth') / name, auth / name)
    (auth / name).chmod(0o600)
os.chown(auth, uid, uid)
driver = data / 'driver'
driver.mkdir(exist_ok=True)
helper = root / 'opt/LIVI/resources/driver/livi-helperd'
shutil.copy2(helper, driver / 'livi-helperd')
shutil.copy2(helper, root / 'usr/local/sbin/livi-helperd')
for path in [data.parent, data, *data.rglob('*')]:
    os.chown(path, uid, uid)

assets = source / 'assets/linux'
for name in ('99-LIVI-helper.sudoers.template', '99-LIVI-wifi-ap.sudoers.template'):
    content = (assets / name).read_text().replace('__USERNAME__', 'livi').replace('__SYSTEMCTL__', '/usr/bin/systemctl')
    # sudo ignores drop-in basenames containing a dot, even when visudo accepts them.
    (root / 'etc/sudoers.d' / name.replace('.template', '')).unlink(missing_ok=True)
    write('/etc/sudoers.d/' + name.replace('.sudoers.template', ''), content, 0o440)
write('/etc/udev/rules.d/99-LIVI.rules', (assets / '99-LIVI.rules.template').read_text())
(root / 'usr/local/lib/livi').mkdir(parents=True, exist_ok=True)
shutil.copy2(assets / 'livi-touch-filter', root / 'usr/local/lib/livi/livi-touch-filter')
write('/etc/pam.d/livi-kiosk', 'auth required pam_permit.so\n@include common-account\n@include common-session\n')
write('/etc/security/limits.d/99-livi.conf', 'livi - rtprio 70\nlivi - memlock unlimited\n')
write('/home/livi/.config/wireplumber/wireplumber.conf.d/99-livi-hfp.conf', 'monitor.bluez.properties = {\n  bluez5.roles = [ a2dp_sink a2dp_source ]\n}\n')
write('/usr/local/lib/livi/livi-power.sh', '''#!/bin/bash
set -euo pipefail
case "${1:-}" in
  poweroff) systemctl poweroff ;;
  reboot) systemctl reboot ;;
  *) exit 2 ;;
esac
''', 0o755)
write('/etc/sudoers.d/99-LIVI-power', 'livi ALL=(root) NOPASSWD: /usr/local/lib/livi/livi-power.sh *\n', 0o440)
write('/usr/local/lib/livi/livi-set-time.sh', '''#!/bin/bash
set -euo pipefail
case "${1:-}" in
  tz)
    zone="${2:-}"
    [[ "$zone" =~ ^[A-Za-z0-9_+/-]{1,64}$ ]] || exit 2
    [[ -f "/usr/share/zoneinfo/$zone" ]] || exit 3
    timedatectl set-timezone "$zone"
    ;;
  *)
    arg="${1:-}"
    [[ "$arg" =~ ^[0-9]{10,11}$ ]] || exit 2
    date -u -s "@$arg" >/dev/null
    command -v fake-hwclock >/dev/null 2>&1 && fake-hwclock save || true
    ;;
esac
''', 0o755)
write('/etc/sudoers.d/99-LIVI-time', 'livi ALL=(root) NOPASSWD: /usr/local/lib/livi/livi-set-time.sh *\n', 0o440)

write('/etc/systemd/system/livi-kiosk.service', '''# LIVI-KIOSK-VERSION=2
[Unit]
Description=LIVI fast-boot kiosk
After=systemd-user-sessions.service seatd.service user@1000.service
Wants=seatd.service user@1000.service
Conflicts=getty@tty1.service
StartLimitIntervalSec=0

[Service]
Type=simple
User=livi
PAMName=livi-kiosk
WorkingDirectory=/home/livi
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
StandardInput=tty-fail
StandardOutput=journal
StandardError=inherit
Environment=LIVI_KIOSK=1
Environment=LIVI_HELPER_BIN=/home/livi/.config/LIVI/driver/livi-helperd
ExecStart=/opt/LIVI/AppRun
Restart=always
RestartSec=1
TimeoutStopSec=8

[Install]
WantedBy=multi-user.target
''')
ap_unit = (assets / 'livi-wifi-ap.service.template').read_text().replace('__USERNAME__', 'livi')
write('/etc/systemd/system/livi-wifi-ap.service', ap_unit)
# Preserve the exact upstream unit, so LIVI's reconciler does not reinstall it.
write('/etc/systemd/system/livi-wifi-ap.service.d/10-radio-ready.conf', '''[Unit]
After=sys-subsystem-net-devices-wlan0.device
Requires=sys-subsystem-net-devices-wlan0.device
''')
write('/etc/NetworkManager/conf.d/99-livi-ap-unmanaged.conf', '[keyfile]\nunmanaged-devices=interface-name:wlan0\n')
write('/etc/NetworkManager/conf.d/99-livi-wifi-powersave.conf', '[connection]\nwifi.powersave=2\nwifi-sec.pmf=2\n')
write('/etc/modprobe.d/99-livi-regdom.conf', 'options cfg80211 ieee80211_regdom=AU\n')
write('/etc/modules-load.d/livi.conf', 'cdc_ncm\ncdc_ether\nipheth\n')
write('/var/lib/systemd/linger/livi', '')
generator = root / 'etc/systemd/system-generators/rpi-swap-generator'
generator.parent.mkdir(parents=True, exist_ok=True)
if not generator.is_symlink():
    generator.symlink_to('/dev/null')

# Match bluetoothd::setup exactly to avoid its five-second restart on first boot.
bluetooth_conf = (root / 'etc/bluetooth/main.conf').read_text()
original_class = next((line for line in bluetooth_conf.splitlines() if re.match(r'^\s*#?\s*Class\s*=', line)), '')
bluetooth_conf = re.sub(r'^\s*#?\s*Class\s*=.*$', 'Class = 0x200418', bluetooth_conf, count=1, flags=re.M)
if 'Class = 0x200418' not in bluetooth_conf:
    bluetooth_conf = bluetooth_conf.replace('[General]', '[General]\nClass = 0x200418', 1)
write('/etc/bluetooth/main.conf', bluetooth_conf)
write('/var/lib/livi/main-conf-class', original_class)
bluetoothd = next(p for p in ['/usr/libexec/bluetooth/bluetoothd', '/usr/lib/bluetooth/bluetoothd', '/usr/sbin/bluetoothd'] if (root / p.lstrip('/')).exists())
write('/etc/systemd/system/bluetooth.service.d/livi-no-sap.conf', f'[Service]\nExecStart=\nExecStart={bluetoothd} --noplugin=midi\n')

# Provisioning is complete in this image. Physical A/B boot tests showed that
# leaving cloud-init enabled delays every boot by about 1.18 seconds.
write('/etc/cloud/cloud-init.disabled', '')

# No first-boot package installation, user wizard, swap allocation or EEPROM writes.
disabled = ['NetworkManager-wait-online.service', 'systemd-networkd-wait-online.service',
            'userconfig.service', 'sshswitch.service', 'rpi-resize.service',
            'rpi-resize-swap-file.service', 'rpi-eeprom-update.service', 'hostapd.service',
            'apt-daily.timer', 'apt-daily-upgrade.timer', 'man-db.timer', 'e2scrub_all.timer',
            'getty@tty1.service', 'serial-getty@ttyAMA10.service']
for unit in disabled:
    subprocess.run(['chroot', str(root), '/usr/bin/systemctl', 'disable', unit],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    unit_path = root / 'etc/systemd/system' / unit
    if unit_path.is_symlink():
        unit_path.unlink()
    elif unit_path.exists():
        unit_path.rename(unit_path.with_name(unit + '.livi-original'))
    chroot('/usr/bin/systemctl', 'mask', unit, stdout=subprocess.DEVNULL)
for unit in ['livi-kiosk.service', 'livi-wifi-ap.service', 'seatd.service', 'bluetooth.service', 'avahi-daemon.service', 'ssh.service', 'NetworkManager.service']:
    chroot('/usr/bin/systemctl', 'enable', unit, stdout=subprocess.DEVNULL)
if (root / 'usr/lib/systemd/system/hciuart.service').exists() or (root / 'lib/systemd/system/hciuart.service').exists():
    chroot('/usr/bin/systemctl', 'enable', 'hciuart.service', stdout=subprocess.DEVNULL)
chroot('/usr/bin/systemctl', 'set-default', 'multi-user.target', stdout=subprocess.DEVNULL)
user_units = root / 'home/livi/.config/systemd/user'
for target, unit in [('sockets.target', 'pipewire.socket'), ('sockets.target', 'pipewire-pulse.socket'), ('pipewire.service', 'wireplumber.service')]:
    folder = user_units / (target + '.wants')
    folder.mkdir(parents=True, exist_ok=True)
    link = folder / unit
    if not link.exists() and not link.is_symlink():
        link.symlink_to('/usr/lib/systemd/user/' + unit)
for path in [root / 'home/livi/.config', *(root / 'home/livi/.config').rglob('*')]:
    os.lchown(path, uid, uid)
write('/etc/ssh/sshd_config.d/50-livi.conf', 'PermitRootLogin no\nPasswordAuthentication yes\n')
write('/etc/systemd/journald.conf.d/livi.conf', '[Journal]\nStorage=volatile\nRuntimeMaxUse=32M\n')
# Seed the initial offline clock from build time instead of the OS image date.
# This prevents future-password warnings and stale certificate-validation time.
clock = root / 'var/lib/systemd/timesync/clock'
clock.parent.mkdir(parents=True, exist_ok=True)
clock.touch()

boot = root / 'boot/firmware'
config_txt = (boot / 'config.txt').read_text()
config_txt = re.sub(r'^camera_auto_detect=1$', 'camera_auto_detect=0', config_txt, flags=re.M)
config_txt = re.sub(r'^display_auto_detect=1$', 'display_auto_detect=0', config_txt, flags=re.M)
config_txt += '\n[all]\n# LIVI Pi 5 fast boot, HDMI Waveshare 1600x600\ndisable_splash=1\nboot_delay=0\ndisable_poe_fan=1\n'
(boot / 'config.txt').write_text(config_txt)
cmdline = (boot / 'cmdline.txt').read_text().split()
cmdline = [arg for arg in cmdline if arg != 'resize' and not arg.startswith(('console=', 'init=', 'video=', 'systemd.run='))]
cmdline += ['quiet', 'loglevel=3', 'vt.global_cursor_default=0', 'console=tty3', 'video=HDMI-A-1:1600x600@60D']
(boot / 'cmdline.txt').write_text(' '.join(cmdline) + '\n')
# Debian's resize kernel flag was removed above; the ready image already has 5.5 GiB root.
write('/etc/motd', 'LIVI Pi 5 wireless CarPlay test image\nRun livi-boot-report for boot timing and diagnostics.\n')
shutil.copy2('/builder/livi-boot-report', root / 'usr/local/bin/livi-boot-report')
(root / 'usr/local/bin/livi-boot-report').chmod(0o755)
chroot('/usr/sbin/visudo', '-c', stdout=subprocess.DEVNULL)

access = f'''LIVI Pi 5 local test image
Flash LIVI-Pi5-10.2.0-fastboot.img.xz as a custom image; skip Imager OS customization.
8 GB or larger SD card. Raspberry Pi 5, HDMI0 (port nearest USB-C), Waveshare 1600x600.
Connect the screen's USB power/touch connection and HDMI. Use a suitable Pi 5 power supply.

Verified wireless test: turn iPhone Personal Hotspot OFF and disconnect USB tethering.
Wireless CarPlay: pair the iPhone with LIVI-Pi5 in Bluetooth and allow CarPlay.
Wi-Fi AP: LIVI-Pi5 (5 GHz, channel 36, 20 MHz, country AU)
Wi-Fi password: {credentials['wifi_password']}

SSH over Ethernet: ssh livi@livi-pi5.local
Login password: {credentials['password']}
Run: sudo livi-boot-report
Stop kiosk: sudo systemctl stop livi-kiosk
Restart kiosk: sudo systemctl restart livi-kiosk
Power off: sudo poweroff

Authentication: the experimental DiPlay identity approved for this local test is embedded.
This is a private test image. Physical Pi 5 boot time and wireless CarPlay remain to be measured.
LIVI app and AP start in parallel, with no internet/network-online dependency.
No desktop, login wizard, first-boot installation, first-boot resize or swap-file allocation.
Runtime diagnostics: ~/.config/LIVI/log/ and journalctl -b -u livi-kiosk -u livi-wifi-ap.
No Mac or phone pairing records are embedded: pair this Pi as a new receiver.
'''
(output / 'START-HERE.txt').write_text(access)
(output / 'START-HERE.txt').chmod(0o600)
write('/boot/firmware/LIVI-START.txt', 'LIVI Pi 5 fastboot image. See the accompanying START-HERE.txt for credentials and setup.\nHDMI0, Waveshare 1600x600, USB touch. Bluetooth name: LIVI-Pi5.\n')
print('Offline LIVI, radio, display, user and kiosk configuration completed.')
