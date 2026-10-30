#!/usr/bin/env python3
"""Collect Pi diagnostics offline, then restore the Mac's original Wi-Fi."""
import argparse
import datetime
import getpass
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time

PI_SSID = 'LIVI-Pi5'
PI_IP = '10.10.0.1'
NETWORKSETUP = '/usr/sbin/networksetup'
REMOTE_SCRIPT = r'''#!/bin/bash
set +e
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
section() { printf '\n[%s]\n' "$1"; }
section 'Board and boot'
date --iso-8601=seconds
cat /proc/device-tree/model; printf '\n'
uname -a
uptime
systemd-analyze time
systemd-analyze critical-chain livi-kiosk.service bluetooth.service livi-wifi-ap.service
section 'Services'
systemctl --no-pager --full status livi-kiosk.service bluetooth.service livi-wifi-ap.service
systemctl cat livi-kiosk.service bluetooth.service
section 'Bluetooth adapter'
timeout 10s bluetoothctl show
rfkill list
ls -l /sys/class/bluetooth/
section 'Network'
ip -brief address
iw dev wlan0 info
section 'Effective LIVI configuration, passwords excluded'
python3 - <<'PY'
import json
from pathlib import Path
p = Path.home()/'.config/LIVI/config.json'
d = json.loads(p.read_text())
keys = ['carName','wirelessCpEnabled','wirelessAaEnabled','btAdapter',
        'wifiInterface','wifiDedicatedInterface','country','wifiType','wifiChannel',
        'wifiChannelWidth','autoConn','projectionWidth','projectionHeight',
        'projectionFps','displayMode','mainScreenWidth','mainScreenHeight',
        'uiZoomPercent','kiosk']
print(json.dumps({k:d.get(k) for k in keys}, indent=2))
PY
section 'Display connectors and EDID'
cat /proc/cmdline
for connector in /sys/class/drm/card*-*; do
  [ -f "$connector/status" ] || continue
  printf '\n%s: ' "$connector"; cat "$connector/status"
  cat "$connector/modes" 2>/dev/null
  if [ -r "$connector/edid" ]; then
    printf 'EDID base64: '; base64 -w0 "$connector/edid"; printf '\n'
  fi
done
section 'Boot journal'
journalctl -b --no-pager -o short-monotonic -n 450 \
  -u livi-kiosk.service -u bluetooth.service -u livi-wifi-ap.service
section 'Kernel radio and display events'
journalctl -b -k --no-pager | grep -Ei 'bluetooth|hci|brcm|firmware|drm|hdmi|edid|vc4' | tail -160
section 'LIVI logs'
for logfile in "$HOME"/.config/LIVI/log/*.log; do
  [ -f "$logfile" ] || continue
  printf '\n%s\n' "${logfile##*/}"
  tail -220 "$logfile"
done
section 'Diagnostics complete'
'''


def query(command):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None


def current_ssid(interface):
    result = query([NETWORKSETUP, '-getairportnetwork', interface])
    if result and result.returncode == 0:
        match = re.search(r'^Current Wi-Fi Network:\s*(.+)$', result.stdout, re.M)
        if match:
            return match.group(1).strip()
    return None


def current_router(interface):
    result = query(['/usr/sbin/ipconfig', 'getoption', interface, 'router'])
    if result and result.returncode == 0:
        match = re.search(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', result.stdout)
        if match:
            return match.group(0)
    return None


def join_network(interface, ssid, password):
    command = [NETWORKSETUP, '-setairportnetwork', interface, ssid]
    if password:
        command.append(password)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=35)
    except subprocess.TimeoutExpired:
        raise RuntimeError('Wi-Fi association command timed out.') from None
    output = (result.stdout + result.stderr).strip()
    if result.returncode or re.search(r'error|could not|cannot|failed|not authorized', output, re.I):
        if password:
            output = output.replace(password, '[redacted]')
        raise RuntimeError('Wi-Fi association failed: ' + (output or 'no details'))


def wait_for_ssh(timeout=35):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((PI_IP, 22), timeout=2):
                return
        except OSError:
            time.sleep(1)
    raise RuntimeError('The Pi SSH service was not reachable at ' + PI_IP)


def collect_ssh(credentials, host_key, report_dir):
    parts = host_key.read_text().split()
    if len(parts) < 2 or parts[0] != 'ssh-ed25519':
        raise RuntimeError('The pinned Pi SSH public key is invalid.')
    with tempfile.TemporaryDirectory(prefix='livi-ssh-') as temp:
        private = Path(temp)
        secret = private / 'password'
        secret.write_text(credentials['password'])
        secret.chmod(0o600)
        askpass = private / 'askpass'
        askpass.write_text('#!/bin/sh\nexec /bin/cat "$LIVI_PASSWORD_FILE"\n')
        askpass.chmod(0o700)
        known = private / 'known_hosts'
        known.write_text(PI_IP + ' ' + ' '.join(parts[:2]) + '\n')
        env = os.environ.copy()
        env.update(SSH_ASKPASS=str(askpass), SSH_ASKPASS_REQUIRE='force',
                   DISPLAY='livi-diagnostics', LIVI_PASSWORD_FILE=str(secret))
        command = ['/usr/bin/ssh', '-T', '-o', 'ConnectTimeout=8',
                   '-o', 'ConnectionAttempts=1', '-o', 'NumberOfPasswordPrompts=1',
                   '-o', 'PreferredAuthentications=password', '-o', 'PubkeyAuthentication=no',
                   '-o', 'StrictHostKeyChecking=yes', '-o', 'HostKeyAlgorithms=ssh-ed25519',
                   '-o', 'UserKnownHostsFile=' + str(known),
                   credentials.get('user', 'livi') + '@' + PI_IP, 'bash -s']
        logfile = report_dir / 'pi-diagnostics.txt'
        with logfile.open('w') as log:
            logfile.chmod(0o600)
            try:
                result = subprocess.run(command, input=REMOTE_SCRIPT, text=True,
                                        stdout=log, stderr=subprocess.STDOUT,
                                        env=env, timeout=100)
            except subprocess.TimeoutExpired:
                raise RuntimeError('SSH diagnostics timed out; partial logs were saved.') from None
        if result.returncode:
            raise RuntimeError('SSH diagnostics failed; details are in pi-diagnostics.txt.')


def restore_network(interface, ssid, password, router):
    print('Restoring your original Wi-Fi. Please leave this Terminal window open.', flush=True)
    # Finish restoration even if Ctrl+C, hangup or TERM arrives during this step.
    handlers = {sig: signal.signal(sig, signal.SIG_IGN)
                for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        for attempt in range(2):
            try:
                join_network(interface, ssid, password)
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    if current_ssid(interface) == ssid or (router and current_router(interface) == router):
                        return True
                    time.sleep(1)
            except RuntimeError:
                pass
            if attempt == 0 and not password and sys.stdin.isatty():
                print('Saved credentials did not restore Wi-Fi. Enter its password to retry.')
                password = getpass.getpass('Original Wi-Fi password: ')
        return False
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


def run_session(interface, original, original_password, router, credentials, host_key, report_dir, no_switch=False):
    result = {'started': datetime.datetime.now().astimezone().isoformat(),
              'logs_collected': False, 'wifi_restored': no_switch}
    attempted_switch = False
    try:
        if not no_switch:
            attempted_switch = True  # A failed join may still change the association.
            print('Joining LIVI-Pi5. Internet may disconnect until restoration completes.', flush=True)
            join_network(interface, PI_SSID, credentials['wifi_password'])
        wait_for_ssh()
        print('Collecting Pi diagnostics over its local Wi-Fi connection.', flush=True)
        collect_ssh(credentials, host_key, report_dir)
        result['logs_collected'] = True
    except KeyboardInterrupt:
        result['error'] = 'Interrupted; attempted to restore Wi-Fi.'
    except Exception as error:
        result['error'] = str(error)
    finally:
        if attempted_switch:
            try:
                result['wifi_restored'] = restore_network(interface, original, original_password, router)
            except Exception:
                result['wifi_restored'] = False
        result['finished'] = datetime.datetime.now().astimezone().isoformat()
        (report_dir / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    root = Path(__file__).resolve().parents[3]
    build = root / 'dist/pi5-fastboot'
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check prerequisites without changing networks.')
    parser.add_argument('--no-switch', action='store_true', help='Collect only; you already joined the Pi manually.')
    parser.add_argument('--interface', default='en0')
    parser.add_argument('--original-ssid', help='Original Wi-Fi name, if macOS hides it.')
    parser.add_argument('--credentials', type=Path, default=build / 'access.json')
    parser.add_argument('--host-key', type=Path, default=build / 'pi-ssh-hostkey.pub')
    parser.add_argument('--output', type=Path, default=root / 'dist/pi5-diagnostics')
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('Run this on the Mac that holds the image credentials.')
    credentials = json.loads(args.credentials.read_text())
    if not all(credentials.get(key) for key in ['password', 'wifi_password']):
        parser.error('The Pi credentials file is incomplete.')
    if not args.host_key.is_file():
        parser.error('The pinned Pi public host key is missing.')
    original = args.original_ssid or current_ssid(args.interface)
    router = current_router(args.interface)
    if args.check:
        print('Credentials and pinned Pi SSH key: available.')
        print('Current Wi-Fi name: ' + ('available' if original else 'hidden by macOS; script will ask before switching.'))
        print('Current Wi-Fi router: ' + ('available' if router else 'unavailable'))
        print('Check complete. No networks changed.')
        return 0
    original_password = None
    if not args.no_switch:
        if not sys.stdin.isatty():
            parser.error('Run interactively in Terminal so restoration details can be confirmed.')
        if not original:
            print('macOS hid the current Wi-Fi name. Enter it so the script can reconnect afterward.')
            original = input('Original Wi-Fi network name: ').strip()
        if not original or original == PI_SSID:
            parser.error('Provide your original internet Wi-Fi name, or use --no-switch if already on the Pi.')
        # Read this before disconnecting; blank means try the Mac's saved credentials.
        original_password = getpass.getpass('Original Wi-Fi password (Enter to try saved credentials): ') or None
        print('Original Wi-Fi recorded. The script restores it after success, failure or Ctrl+C.')
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    report_dir = args.output / stamp
    report_dir.mkdir(parents=True, mode=0o700)
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupted)
    result = run_session(args.interface, original, original_password, router,
                         credentials, args.host_key, report_dir, args.no_switch)
    if result.get('error'):
        print(result['error'])
    print('Logs saved to: ' + str(report_dir))
    if not result['wifi_restored']:
        print('Automatic Wi-Fi restoration failed. Use the macOS Wi-Fi menu to reconnect to: ' + original)
        return 2
    print('Network restoration complete.' if not args.no_switch else 'Collection finished without changing Wi-Fi.')
    return 0 if result['logs_collected'] else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\nCancelled before switching networks.')
        sys.exit(130)
