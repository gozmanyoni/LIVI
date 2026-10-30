#!/usr/bin/env python3
"""Offline checks that can be proved without Raspberry Pi hardware."""
import json
from pathlib import Path
import stat
import subprocess
import sys

root = Path(sys.argv[1])
checks = {}

def require(name, condition):
    checks[name] = bool(condition)
    assert condition, name

def run(*args, **kwargs):
    return subprocess.run(['chroot', str(root), *args], text=True, capture_output=True, **kwargs)

config = json.loads((root / 'home/livi/.config/LIVI/config.json').read_text())
require('wireless_carplay_enabled', config['wirelessCpEnabled'] and config['autoConn'])
require('dedicated_onboard_radios', config['wifiDedicatedInterface'] and config['wifiInterface'] == 'wlan0' and config['btAdapter'] == 'hci0')
require('non_dfs_5ghz_AU', config['country'] == 'AU' and config['wifiChannel'] == 36 and config['wifiChannelWidth'] == 20)
require('waveshare_native_resolution', (config['projectionWidth'], config['projectionHeight']) == (1600, 600))
auth = root / 'home/livi/.config/LIVI/mfi'
require('identity_directory_private', stat.S_IMODE(auth.stat().st_mode) == 0o700)
for name in ['certificate.p7b', 'identity.pk8']:
    path = auth / name
    require(name + '_matches_approved_input', path.read_bytes() == (Path('/auth') / name).read_bytes())
    require(name + '_owner_only', stat.S_IMODE(path.stat().st_mode) == 0o600 and path.stat().st_uid == 1000)
helper = root / 'opt/LIVI/resources/driver/livi-helperd'
require('helpers_identical', helper.read_bytes() == (root / 'usr/local/sbin/livi-helperd').read_bytes() == (root / 'home/livi/.config/LIVI/driver/livi-helperd').read_bytes())
for binary in ['livi-ui', 'resources/core/livi-core', 'resources/driver/livi-helperd', 'resources/gst-host/livi-gst-host', 'resources/compositor/livi-compositor']:
    path = '/opt/LIVI/' + binary
    report = run('/usr/bin/env', 'LD_LIBRARY_PATH=/opt/LIVI/resources/gstreamer/linux-arm64/lib', '/usr/bin/ldd', path)
    if report.returncode or 'not found' in report.stdout:
        print(report.stdout + report.stderr, file=sys.stderr)
    require(binary + '_linked', report.returncode == 0 and 'not found' not in report.stdout)

gst = '/opt/LIVI/resources/gstreamer/linux-arm64'
gst_env = ['/usr/bin/env', 'LD_LIBRARY_PATH=' + gst + '/lib',
           'GST_PLUGIN_SYSTEM_PATH=', 'GST_PLUGIN_PATH=' + gst + '/lib/gstreamer-1.0',
           'GST_PLUGIN_SCANNER=' + gst + '/libexec/gstreamer-1.0/gst-plugin-scanner',
           'GST_REGISTRY=/tmp/livi-build-gst-registry.bin']
for element in ['h264parse', 'h265parse', 'avdec_h264', 'avdec_h265', 'waylandsink']:
    report = run(*gst_env, gst + '/bin/gst-inspect-1.0', element)
    (Path('/output') / ('gst-' + element + '.txt')).write_text(report.stdout + report.stderr)
    require(element + '_plugin_loads', report.returncode == 0)
(root / 'tmp/livi-build-gst-registry.bin').unlink(missing_ok=True)

units = ['livi-kiosk.service', 'livi-wifi-ap.service', 'seatd.service', 'bluetooth.service', 'avahi-daemon.service', 'ssh.service']
for unit in units:
    report = run('/usr/bin/systemctl', 'is-enabled', unit)
    require(unit + '_enabled', report.returncode == 0)
report = run('/usr/bin/systemd-analyze', 'verify', '/etc/systemd/system/livi-kiosk.service', '/etc/systemd/system/livi-wifi-ap.service')
(Path('/output') / 'systemd-validation.txt').write_text(report.stdout + report.stderr)
require('systemd_units_valid', report.returncode == 0)
require('sudoers_valid', run('/usr/sbin/visudo', '-c').returncode == 0)
for name in ['99-LIVI-helper', '99-LIVI-wifi-ap']:
    require(name + '_loaded_filename', (root / 'etc/sudoers.d' / name).is_file())
helper_permission = run('/usr/sbin/runuser', '-u', 'livi', '--', '/usr/bin/sudo', '-n', '-E',
                        '/home/livi/.config/LIVI/driver/livi-helperd', '--wifi-ap-status')
require('helper_executes_without_password', helper_permission.returncode == 0)
ap_permission = run('/usr/sbin/runuser', '-u', 'livi', '--', '/usr/bin/sudo', '-n',
                    '/usr/bin/systemctl', 'enable', 'livi-wifi-ap.service')
require('ap_management_without_password', ap_permission.returncode == 0)
cmdline = (root / 'boot/firmware/cmdline.txt').read_text().split()
require('no_firstboot_resize', 'resize' not in cmdline and not any(s.startswith('init=') for s in cmdline))
require('hdmi0_1600x600', 'video=HDMI-A-1:1600x600@60D' in cmdline)
require('pi5_kernel_present', (root / 'boot/firmware/kernel_2712.img').is_file())
require('kms_driver_retained', 'dtoverlay=vc4-kms-v3d' in (root / 'boot/firmware/config.txt').read_text())
require('ipv6_retained', not any('ipv6.disable=1' == s for s in cmdline))
require('native_apprun_installed', (root / 'opt/LIVI/AppRun').is_file())
for binary, installed in [('livi-core', 'core'), ('livi-compositor', 'compositor')]:
    require(binary + '_matches_verified_override',
            (root / ('opt/LIVI/resources/' + installed + '/' + binary)).read_bytes()
            == (Path('/inputs') / (binary + '-patched')).read_bytes())
require('no_host_phone_pairings', not (root / 'home/livi/.config/LIVI/cp/pairings.json').exists())
require('no_display_manager', not (root / 'etc/systemd/system/display-manager.service').exists())
require('cloud_init_disabled_after_image_provisioning', (root / 'etc/cloud/cloud-init.disabled').is_file())
require('bluetooth_no_five_second_reconfigure', 'Class = 0x200418' in (root / 'etc/bluetooth/main.conf').read_text() and '--noplugin=midi\n' in (root / 'etc/systemd/system/bluetooth.service.d/livi-no-sap.conf').read_text())
print(json.dumps({'passed': len(checks), 'checks': checks, 'physical_boot_test': 'requires Raspberry Pi 5'}, indent=2))
