#!/usr/bin/env python3
"""Reversible, marker-only cloud-init boot trial. Never reboots the machine."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import socket
import subprocess
import tempfile
import time

TRIAL_DIR = "/var/lib/livi/boot-trial-20261030"
MARKER = "/etc/cloud/cloud-init.disabled"
UNITS = ("livi-kiosk.service", "livi-wifi-ap.service", "bluetooth.service",
         "user@1000.service", "NetworkManager.service", "cloud-init-local.service",
         "cloud-init-network.service", "cloud-init-main.service", "cloud-init.service", "cloud-config.service",
         "cloud-final.service")
FIELDS = ("Id", "LoadState", "ActiveState", "SubState", "Result", "UnitFileState",
          "ActiveEnterTimestampMonotonic", "InactiveExitTimestampMonotonic",
          "ExecMainStartTimestampMonotonic", "ExecMainExitTimestampMonotonic",
          "FragmentPath", "DropInPaths")


def run(args, timeout=5):
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return {"returncode": proc.returncode, "stdout": proc.stdout[:65536],
                "stderr": proc.stderr[:4096]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"returncode": None, "error": str(exc)}


def digest(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def atomic_json(path, value):
    fd, name = tempfile.mkstemp(prefix=".trial-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Trial:
    def __init__(self, root=Path("/")):
        self.root = Path(root)
        self.directory = self.at(TRIAL_DIR)
        self.marker = self.at(MARKER)
        self.metadata_path = self.directory / "metadata.json"

    def at(self, absolute):
        return self.root / absolute.lstrip("/")

    def prepare(self):
        if self.root == Path("/") and os.geteuid() != 0:
            raise RuntimeError("Run as root; no changes made")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)

    def metadata(self):
        if not self.metadata_path.exists():
            return None
        return json.loads(self.metadata_path.read_text())

    def observe_cloud_init(self):
        units = [unit for unit in UNITS if unit.startswith("cloud-")]
        observed = run(["systemctl", "show", *units,
                        "--property=" + ",".join(FIELDS)])
        fingerprints = {}
        for line in observed.get("stdout", "").splitlines():
            if line.startswith(("FragmentPath=", "DropInPaths=")):
                for filename in shlex.split(line.partition("=")[2]):
                    if filename.startswith("/"):
                        fingerprints[filename] = digest(self.at(filename))
        return {"units": observed, "unit_config_sha256": fingerprints}

    def apply(self):
        self.prepare()
        previous = self.metadata()
        if previous and previous["status"] == "applied":
            if not self.marker.is_file() or digest(self.marker) != previous["applied_marker_sha256"]:
                raise RuntimeError("Applied marker changed externally; inspect before continuing")
            return {"status": "already-applied", "reboot_required": True,
                    "message": "No changes; helper never reboots"}
        if previous and previous["status"] != "rolled-back":
            raise RuntimeError("An interrupted trial exists; run rollback first")
        if self.marker.is_symlink() or (self.marker.exists() and not self.marker.is_file()):
            raise RuntimeError("Refusing non-regular cloud-init marker")
        existed = self.marker.exists()
        if existed:
            shutil.copy2(self.marker, self.directory / "marker.before")
            (self.directory / "marker.before").chmod(0o600)
        before_stat = self.marker.stat() if existed else None
        metadata = {
            "version": 1, "status": "applying", "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "marker_existed": existed, "marker_sha256_before": digest(self.marker),
            "marker_stat_before": ({"mode": before_stat.st_mode & 0o7777,
                                    "uid": before_stat.st_uid, "gid": before_stat.st_gid} if existed else None),
            "cloud_init_units_before": self.observe_cloud_init(),
            "only_system_change": MARKER,
            "rollback": "sudo python3 " + shlex.quote(str(Path(__file__).resolve())) + " rollback",
            "applied_marker_sha256": digest(self.marker) if existed else hashlib.sha256(b"").hexdigest(),
        }
        atomic_json(self.metadata_path, metadata)
        if not existed:
            if not self.marker.parent.is_dir():
                raise RuntimeError("/etc/cloud is missing; rollback trial metadata before retrying")
            fd = os.open(self.marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            os.close(fd)
        metadata["status"] = "applied"
        atomic_json(self.metadata_path, metadata)
        return {"status": "applied", "marker_created": not existed,
                "reboot_required": not existed, "message": "Helper never reboots"}

    def rollback(self):
        self.prepare()
        metadata = self.metadata()
        if not metadata:
            return {"status": "no-trial", "reboot_required": False}
        if metadata["status"] == "rolled-back":
            return {"status": "already-rolled-back", "reboot_required": False}
        if self.marker.is_symlink():
            raise RuntimeError("Marker replaced by symlink; refusing rollback")
        if metadata["marker_existed"]:
            # Existing markers were never edited. Do not overwrite later external edits.
            if digest(self.marker) != metadata["marker_sha256_before"]:
                raise RuntimeError("Original marker changed externally; backup retained for manual recovery")
        elif self.marker.exists():
            if not self.marker.is_file() or digest(self.marker) != metadata["applied_marker_sha256"]:
                raise RuntimeError("Trial marker changed externally; refusing to delete it")
            self.marker.unlink()
        metadata["status"] = "rolled-back"
        metadata["rolled_back_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        atomic_json(self.metadata_path, metadata)
        return {"status": "rolled-back", "reboot_required": not metadata["marker_existed"],
                "message": "Helper never reboots"}

    def snapshot(self, label):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", label):
            raise ValueError("Invalid snapshot label")
        self.prepare()
        def read(path):
            try:
                return self.at(path).read_text().strip()
            except OSError:
                return None
        deadline = time.monotonic() + 12
        timing = None
        while time.monotonic() < deadline:
            timing = run(["systemd-analyze", "time"], timeout=max(0.1, min(3, deadline - time.monotonic())))
            if timing.get("returncode") == 0:
                break
            time.sleep(min(0.3, max(0, deadline - time.monotonic())))
        config = self.at("/home/livi/.config/LIVI/config.json")
        connector_states = {}
        for status in self.at("/sys/class/drm").glob("card*-*/status"):
            try:
                connector_states[str(status.relative_to(self.root))] = status.read_text().strip()
            except OSError:
                pass
        core_socket = self.at("/run/user/1000/livi/core.sock")
        core_socket_accepting = False
        if core_socket.exists():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(1)
                    probe.connect(str(core_socket))
                    core_socket_accepting = True
            except OSError:
                pass
        boot_id = read("/proc/sys/kernel/random/boot_id")
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        path = self.directory / (stamp + "-" + label + "-" + (boot_id or "unknown") + ".json")
        snapshot = {
            "label": label, "captured_utc": stamp, "path": str(path),
            "boot_id": boot_id, "uptime": read("/proc/uptime"),
            "os_release": read("/etc/os-release"), "kernel": read("/proc/version"),
            "pi_issue": read("/etc/rpi-issue"),
            "systemd_analyze_time": timing,
            "critical_chain": run(["systemd-analyze", "critical-chain"]),
            "livi_critical_chain": run(["systemd-analyze", "critical-chain", "livi-kiosk.service"]),
            "blame": run(["systemd-analyze", "blame"]),
            "manager_timestamps": run(["systemctl", "show", "--property=" + ",".join(
                name + "TimestampMonotonic" for name in ("Firmware", "Loader", "Kernel", "InitRD", "Userspace", "Finish"))]),
            "services": run(["systemctl", "show", *UNITS, "--property=" + ",".join(FIELDS)]),
            "core_socket_exists": core_socket.exists(),
            "core_socket_accepting": core_socket_accepting,
            "processes": run(["ps", "-C", "livi-core,livi-ui,livi-compositor", "-o", "comm=,etimes="]),
            "drm_connectors": connector_states,
            "config_sha256": digest(config),
            "cloud_init_disabled": self.marker.exists(),
        }
        atomic_json(path, snapshot)
        return snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("snapshot", "apply", "rollback"))
    parser.add_argument("label", nargs="?")
    args = parser.parse_args()
    if args.command == "snapshot" and not args.label:
        parser.error("snapshot requires a label")
    if args.command != "snapshot" and args.label:
        parser.error("only snapshot takes a label")
    trial = Trial()
    try:
        result = trial.snapshot(args.label) if args.command == "snapshot" else getattr(trial, args.command)()
    except (RuntimeError, ValueError, OSError) as exc:
        parser.exit(1, "Error: " + str(exc) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
