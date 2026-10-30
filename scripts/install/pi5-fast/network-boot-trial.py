#!/usr/bin/env python3
"""Reversible user-session network ordering boot trial. Never reboots."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import tempfile
import time

_here = Path(__file__).resolve().parent
_source = _here / "boot-trial.py"
if not _source.exists():
    _source = _here / "livi-boot-trial.py"
_spec = importlib.util.spec_from_file_location("livi_cloud_boot_trial", _source)
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)
run = base.run
UNIT = "systemd-user-sessions.service"
OVERRIDE = "/etc/systemd/system/" + UNIT
TRIAL_DIR = "/var/lib/livi/network-boot-trial-20261030"
PROPERTIES = tuple(dict.fromkeys((*base.FIELDS, "After", "Before", "Wants", "Requires")))
BOOT_CONFIG_KEYS = (
    "kiosk", "autoConn", "wirelessCpEnabled", "btAdapter", "wifiInterface",
    "wifiDedicatedInterface", "country", "wifiType", "wifiChannel", "wifiChannelWidth",
    "displayMode", "projectionWidth", "projectionHeight", "projectionFps", "startPage", "uiZoomPercent",
)


def json_digest(value):
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()



def checked(args):
    result = run(args)
    if result.get("returncode") != 0:
        raise RuntimeError("Command failed: " + shlex.join(args) + ": " + result.get("stderr", result.get("error", "")))
    return result


def unit_after_spans(data):
    """Find After values in [Unit], including systemd continuation lines."""
    section = None
    offset = 0
    continuing = False
    spans = []
    for line in data.splitlines(keepends=True):
        text = line.decode("utf-8")
        stripped = text.strip()
        if not continuing and stripped.startswith("[") and stripped.endswith("]"):
            section = stripped
        value_at = None
        if continuing:
            value_at = 0
        elif section == "[Unit]" and not stripped.startswith(("#", ";")):
            match = re.match(rb"[ \t]*After[ \t]*=", line)
            if match:
                value_at = match.end()
        if value_at is not None:
            # Unit identifiers are ASCII; remove only the token, retaining whitespace.
            for match in re.finditer(rb"(?<![^ \t\r\n])network\.target(?![^ \t\r\n])", line[value_at:]):
                spans.append((offset + value_at + match.start(), offset + value_at + match.end()))
            continuing = line.rstrip(b"\r\n").endswith(b"\\")
        else:
            continuing = False
        offset += len(line)
    return spans


def without_network_after(data):
    spans = unit_after_spans(data)
    if not spans:
        raise RuntimeError("Installed unit has no network.target After token; no change applicable")
    for start, end in reversed(spans):
        data = data[:start] + data[end:]
    return data


def atomic_bytes(path, data, mode=0o644, replace=True):
    fd, name = tempfile.mkstemp(prefix=".network-trial-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(name, path)
        else:
            # Atomic, no-clobber installation also closes the precheck race.
            os.link(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class NetworkTrial(base.Trial):
    def __init__(self, root=Path("/")):
        super().__init__(root)
        self.directory = self.at(TRIAL_DIR)
        self.metadata_path = self.directory / "metadata.json"
        self.override = self.at(OVERRIDE)

    def unit_state(self):
        shown = checked(["systemctl", "show", UNIT, "--property=" + ",".join(PROPERTIES)])
        properties = dict(line.split("=", 1) for line in shown["stdout"].splitlines() if "=" in line)
        return {"properties": properties,
                "effective_unit": checked(["systemctl", "cat", UNIT])["stdout"]}

    def inspect(self):
        state = self.unit_state()
        properties = state["properties"]
        state["network_wait_removed"] = "network.target" not in properties.get("After", "").split()
        state["override_present"] = self.override.exists() or self.override.is_symlink()
        state["trial_metadata"] = self.metadata()
        state["cloud_init_disabled"] = self.marker.exists()
        return state

    def verify_applied(self, metadata):
        if self.override.is_symlink() or base.digest(self.override) != metadata["override_sha256"]:
            raise RuntimeError("Trial override changed externally; refusing modification")
        state = self.unit_state()
        properties = state["properties"]
        after = set(properties.get("After", "").split())
        if properties.get("FragmentPath") != OVERRIDE:
            raise RuntimeError("Effective fragment is not the trial override")
        if "network.target" in after:
            raise RuntimeError("Effective After still includes network.target")
        missing = set(metadata["after_before"]) - {"network.target"} - after
        if missing:
            raise RuntimeError("Unrelated effective After dependencies were lost: " + ", ".join(sorted(missing)))
        return state

    def apply(self):
        self.prepare()
        previous = self.metadata()
        if previous and previous["status"] == "applied":
            self.verify_applied(previous)
            return {"status": "already-applied", "reboot_required": True, "message": "Helper never reboots"}
        if previous and previous["status"] != "rolled-back":
            raise RuntimeError("Interrupted trial exists; run rollback first")
        if self.override.exists() or self.override.is_symlink():
            raise RuntimeError("Preexisting /etc override or symlink; refusing trial")
        state = self.unit_state()
        properties = state["properties"]
        fragment = properties.get("FragmentPath", "")
        if not fragment.startswith(("/usr/lib/systemd/system/", "/lib/systemd/system/")):
            raise RuntimeError("Expected installed vendor unit fragment")
        source = self.at(fragment)
        original = source.read_bytes()
        candidate = without_network_after(original)
        if "network.target" not in properties.get("After", "").split():
            raise RuntimeError("Effective unit does not wait for network.target")
        for dropin in shlex.split(properties.get("DropInPaths", "")):
            if unit_after_spans(self.at(dropin).read_bytes()):
                raise RuntimeError("Conflicting drop-in reintroduces network.target: " + dropin)
        atomic_bytes(self.directory / "unit.before", original, 0o600)
        metadata = {"version": 1, "status": "applying",
                    "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "source_fragment": fragment, "source_sha256": hashlib.sha256(original).hexdigest(),
                    "override_sha256": hashlib.sha256(candidate).hexdigest(),
                    "state_before": state, "after_before": properties.get("After", "").split(),
                    "only_system_change": OVERRIDE,
                    "cloud_init_marker_sha256": base.digest(self.marker),
                    "rollback": "sudo python3 " + shlex.quote(str(Path(__file__).resolve())) + " rollback"}
        base.atomic_json(self.metadata_path, metadata)
        self.override.parent.mkdir(parents=True, exist_ok=True)
        # Recheck after backup, so an existing override is never silently replaced.
        if self.override.exists() or self.override.is_symlink():
            raise RuntimeError("Override appeared during preparation; inspect before continuing")
        atomic_bytes(self.override, candidate, replace=False)
        checked(["systemctl", "daemon-reload"])
        self.verify_applied(metadata)
        metadata["status"] = "applied"
        base.atomic_json(self.metadata_path, metadata)
        return {"status": "applied", "reboot_required": True,
                "network_wait_removed": True, "message": "Helper never reboots"}

    def rollback(self):
        self.prepare()
        metadata = self.metadata()
        if not metadata:
            return {"status": "no-trial", "reboot_required": False}
        if metadata["status"] == "rolled-back":
            return {"status": "already-rolled-back", "reboot_required": False}
        if self.override.is_symlink():
            raise RuntimeError("Override replaced by symlink; refusing rollback")
        if self.override.exists():
            if not self.override.is_file() or base.digest(self.override) != metadata["override_sha256"]:
                raise RuntimeError("Override changed externally; refusing rollback")
            self.override.unlink()
        checked(["systemctl", "daemon-reload"])
        metadata["status"] = "rolled-back"
        metadata["rolled_back_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        base.atomic_json(self.metadata_path, metadata)
        return {"status": "rolled-back", "reboot_required": True, "message": "Helper never reboots"}

    def process_start_times(self):
        names = {"livi-ui", "livi-core", "livi-compositor", "pipewire", "wireplumber"}
        ticks = os.sysconf("SC_CLK_TCK")
        processes = []
        for proc in self.at("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            try:
                name = (proc / "comm").read_text().strip()
                if name not in names:
                    continue
                fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
                processes.append({"pid": int(proc.name), "comm": name,
                                  "start_ticks_since_kernel": int(fields[19]),
                                  "start_seconds_since_kernel": int(fields[19]) / ticks})
            except (OSError, IndexError, ValueError):
                continue
        return {"clock_ticks_per_second": ticks, "processes": sorted(processes, key=lambda item: (item["comm"], item["pid"]))}

    def config_fingerprints(self):
        # Hash values locally; snapshots never contain the config or its values.
        try:
            config = json.loads(self.at("/home/livi/.config/LIVI/config.json").read_text())
            if not isinstance(config, dict):
                raise ValueError("Configuration is not an object")
            full = {"sha256": json_digest(config),
                    "fields": {key: json_digest(value) for key, value in sorted(config.items())}}
            selected = {key: config[key] for key in BOOT_CONFIG_KEYS if key in config}
            boot = {"sha256": json_digest(selected),
                    "fields": {key: json_digest(value) for key, value in selected.items()},
                    "missing_fields": [key for key in BOOT_CONFIG_KEYS if key not in config]}
            return full, boot
        except (OSError, ValueError, TypeError):
            # Parsing errors may contain portions of values, so do not serialize them.
            return {"available": False}, {"available": False}

    def snapshot(self, label):
        snapshot = super().snapshot(label)
        state = self.unit_state()
        snapshot["user_sessions_unit"] = state
        snapshot["network_wait_removed"] = "network.target" not in state["properties"].get("After", "").split()
        snapshot["process_start_times"] = self.process_start_times()
        audio = run(["runuser", "-u", "livi", "--", "env", "XDG_RUNTIME_DIR=/run/user/1000",
                     "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus",
                     "systemctl", "--user", "is-active", "pipewire.service", "wireplumber.service"])
        audio["both_active"] = audio.get("returncode") == 0 and audio.get("stdout", "").splitlines() == ["active", "active"]
        snapshot["audio_health"] = audio
        snapshot["config_fingerprint"], snapshot["boot_config_fingerprint"] = self.config_fingerprints()
        base.atomic_json(Path(snapshot["path"]), snapshot)
        return snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inspect", "snapshot", "apply", "rollback"))
    parser.add_argument("label", nargs="?")
    args = parser.parse_args()
    if args.command == "snapshot" and not args.label:
        parser.error("snapshot requires a label")
    if args.command != "snapshot" and args.label:
        parser.error("only snapshot takes a label")
    trial = NetworkTrial()
    try:
        result = trial.snapshot(args.label) if args.command == "snapshot" else getattr(trial, args.command)()
    except (RuntimeError, ValueError, OSError) as exc:
        parser.exit(1, "Error: " + str(exc) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
