#!/usr/bin/env python3
"""Transactional ARM64 LIVI core startup trial. Never reboots or changes networking."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import stat
import tempfile
import time

_here = Path(__file__).resolve().parent
_source = _here / "network-boot-trial.py"
if not _source.exists():
    _source = _here / "livi-network-boot-trial.py"
_spec = importlib.util.spec_from_file_location("livi_network_boot_trial", _source)
network = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(network)
base = network.base
run = base.run
TARGET = "/opt/LIVI/resources/core/livi-core"
CANDIDATE = "/home/livi/.cache/livi-core-startup-candidate"
REAP_MASK = "/etc/systemd/system/e2scrub_reap.service"
TRIAL_DIR = "/var/lib/livi/ui-boot-trial-20261030"
MILESTONES = ("listening on /run/", "compositor display", "UI started", "LIVI connected", "[ui] core")


def regular(path):
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("Expected a regular non-symlink file: " + str(path))


def validate_elf(path):
    regular(path)
    with path.open("rb") as stream:
        header = stream.read(64)
    if (len(header) != 64 or header[:4] != b"\x7fELF" or header[4:7] != b"\x02\x01\x01"
            or int.from_bytes(header[16:18], "little") not in (2, 3)
            or int.from_bytes(header[18:20], "little") != 183):
        raise RuntimeError("Expected a 64-bit little-endian ARM64 ELF executable")


def atomic_binary(path, data, mode, uid, gid):
    fd, name = tempfile.mkstemp(prefix=".livi-ui-trial-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            os.fchown(stream.fileno(), uid, gid)
            os.fchmod(stream.fileno(), mode)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class UiBootTrial(network.NetworkTrial):
    def __init__(self, root=Path("/")):
        super().__init__(root)
        self.directory = self.at(TRIAL_DIR)
        self.metadata_path = self.directory / "metadata.json"
        self.target = self.at(TARGET)
        self.candidate = self.at(CANDIDATE)
        self.backup = self.directory / "livi-core.before"

    def preserved_fingerprints(self):
        mask = self.at(REAP_MASK)
        if mask.is_symlink():
            mask_state = {"kind": "symlink", "target": os.readlink(mask)}
        elif mask.exists():
            mask_state = {"kind": "file", "sha256": base.digest(mask)}
        else:
            mask_state = {"kind": "absent"}
        return {"cloud_init_marker_sha256": base.digest(self.marker),
                "user_sessions_override_sha256": base.digest(self.override),
                "scrub_reaper_mask": mask_state}

    def inspect(self):
        metadata = self.metadata()
        current = base.digest(self.target) if not self.target.is_symlink() else None
        return {"core_binary_sha256": current,
                "ui_binary_trial_applied": bool(metadata and metadata["status"] == "applied" and current == metadata["candidate_sha256"]),
                "preserved_fingerprints": self.preserved_fingerprints(), "trial_metadata": metadata}

    def verify_applied(self, metadata):
        regular(self.target)
        if base.digest(self.target) != metadata["candidate_sha256"]:
            raise RuntimeError("Installed binary changed externally; refusing modification")
        info = self.target.stat()
        if stat.S_IMODE(info.st_mode) != metadata["original_stat"]["mode"] or info.st_uid != metadata["original_stat"]["uid"] or info.st_gid != metadata["original_stat"]["gid"]:
            raise RuntimeError("Installed binary ownership or mode changed externally")
        if self.preserved_fingerprints() != metadata["preserved_fingerprints"]:
            raise RuntimeError("Unrelated startup configuration changed during trial")

    def apply(self, expected_sha256):
        if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
            raise ValueError("apply requires a 64-character hexadecimal SHA256")
        expected_sha256 = expected_sha256.lower()
        self.prepare()
        previous = self.metadata()
        if previous and previous["status"] == "applied":
            if previous["candidate_sha256"] != expected_sha256:
                raise RuntimeError("Different trial already applied; rollback first")
            self.verify_applied(previous)
            return {"status": "already-applied", "reboot_required": True, "message": "Helper never reboots"}
        if previous and previous["status"] != "rolled-back":
            raise RuntimeError("Interrupted trial exists; run rollback first")
        if not previous and (self.backup.exists() or self.backup.is_symlink()):
            raise RuntimeError("Preexisting trial backup without metadata; inspect before continuing")
        validate_elf(self.target)
        validate_elf(self.candidate)
        candidate_bytes = self.candidate.read_bytes()
        if hashlib.sha256(candidate_bytes).hexdigest() != expected_sha256:
            raise RuntimeError("Candidate SHA256 does not match expected value")
        ldd = {"skipped": "fake-root test"}
        if self.root == Path("/"):
            ldd = run(["ldd", str(self.candidate)])
            output = ldd.get("stdout", "") + ldd.get("stderr", "")
            if ldd.get("returncode") != 0 or "not found" in output.lower():
                raise RuntimeError("Candidate shared-library validation failed")
        info = self.target.stat()
        original = self.target.read_bytes()
        original_sha = hashlib.sha256(original).hexdigest()
        if original_sha == expected_sha256:
            raise RuntimeError("Candidate is identical to current binary; no trial needed")
        original_stat = {"mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid}
        atomic_binary(self.backup, original, 0o755, os.geteuid(), os.getegid())
        metadata = {"version": 1, "status": "applying",
                    "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "original_sha256": original_sha, "candidate_sha256": expected_sha256,
                    "original_stat": original_stat, "candidate_ldd": ldd,
                    "preserved_fingerprints": self.preserved_fingerprints(),
                    "only_system_change": TARGET,
                    "rollback": "sudo python3 " + shlex.quote(str(Path(__file__).resolve())) + " rollback"}
        base.atomic_json(self.metadata_path, metadata)
        regular(self.target)
        if base.digest(self.target) != original_sha:
            raise RuntimeError("Installed binary changed during preparation")
        atomic_binary(self.target, candidate_bytes, **original_stat)
        self.verify_applied(metadata)
        metadata["status"] = "applied"
        base.atomic_json(self.metadata_path, metadata)
        return {"status": "applied", "reboot_required": True, "core_binary_sha256": expected_sha256,
                "message": "Helper never reboots"}

    def rollback(self):
        self.prepare()
        metadata = self.metadata()
        if not metadata:
            return {"status": "no-trial", "reboot_required": False}
        if metadata["status"] == "rolled-back":
            return {"status": "already-rolled-back", "reboot_required": False}
        regular(self.target)
        current = base.digest(self.target)
        if current not in (metadata["candidate_sha256"], metadata["original_sha256"]):
            raise RuntimeError("Installed binary changed externally; refusing rollback")
        if current == metadata["original_sha256"] and metadata["status"] != "applying":
            raise RuntimeError("Applied binary was replaced externally; refusing rollback")
        if current == metadata["candidate_sha256"]:
            info = self.target.stat()
            expected = metadata["original_stat"]
            if (stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid) != (expected["mode"], expected["uid"], expected["gid"]):
                raise RuntimeError("Installed binary ownership or mode changed externally; refusing rollback")
            regular(self.backup)
            if base.digest(self.backup) != metadata["original_sha256"]:
                raise RuntimeError("Original binary backup changed; refusing rollback")
            atomic_binary(self.target, self.backup.read_bytes(), **metadata["original_stat"])
            if base.digest(self.target) != metadata["original_sha256"]:
                raise RuntimeError("Restored binary verification failed")
        metadata["status"] = "rolled-back"
        metadata["rolled_back_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        base.atomic_json(self.metadata_path, metadata)
        return {"status": "rolled-back", "reboot_required": True, "message": "Helper never reboots"}

    def startup_milestones(self):
        log_dir = self.at("/home/livi/.config/LIVI/log")
        result = {"lines": []}
        log = log_dir / "LIVI.log"
        try:
            # Only read a bounded tail; never return unrelated config/auth lines.
            with log.open("rb") as stream:
                stream.seek(0, os.SEEK_END)
                stream.seek(max(0, stream.tell() - 262144))
                text = stream.read().decode("utf-8", errors="replace")
            result["lines"] = [line for line in text.splitlines() if any(token in line for token in MILESTONES)][-100:]
            result["log_mtime_ns"] = log.stat().st_mtime_ns
        except OSError:
            result["log_available"] = False
        compositor = log_dir / "compositor.log"
        try:
            result["compositor_log_mtime_ns"] = compositor.stat().st_mtime_ns
        except OSError:
            pass
        return result

    def snapshot(self, label):
        result = super().snapshot(label)
        inspection = self.inspect()
        result["core_binary_sha256"] = inspection["core_binary_sha256"]
        result["ui_binary_trial_applied"] = inspection["ui_binary_trial_applied"]
        result["startup_milestones"] = self.startup_milestones()
        base.atomic_json(Path(result["path"]), result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inspect", "snapshot", "apply", "rollback"))
    parser.add_argument("argument", nargs="?")
    args = parser.parse_args()
    if args.command in ("snapshot", "apply") and not args.argument:
        parser.error(args.command + " requires " + ("a label" if args.command == "snapshot" else "a SHA256"))
    if args.command in ("inspect", "rollback") and args.argument:
        parser.error(args.command + " takes no argument")
    trial = UiBootTrial()
    try:
        result = getattr(trial, args.command)(args.argument) if args.command in ("snapshot", "apply") else getattr(trial, args.command)()
    except (RuntimeError, ValueError, OSError) as exc:
        parser.exit(1, "Error: " + str(exc) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
