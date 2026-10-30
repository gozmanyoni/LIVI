#!/usr/bin/env python3
"""Transactional temporary compositor boot profiling. Never reboots or changes EEPROM."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import time

_here = Path(__file__).resolve().parent
_source = _here / "ui-boot-trial.py"
if not _source.exists():
    _source = _here / "livi-ui-boot-trial.py"
_spec = importlib.util.spec_from_file_location("livi_ui_boot_trial", _source)
ui = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ui)
network = ui.network
base = ui.base
run = base.run
TARGET = "/opt/LIVI/resources/compositor/livi-compositor"
CANDIDATE = "/home/livi/.cache/livi-compositor-profile"
DROPIN = "/etc/systemd/system/livi-kiosk.service.d/90-boot-trace.conf"
DROPIN_BYTES = b"[Service]\nEnvironment=LIVI_BOOT_TRACE=1\n"
TRIAL_DIR = "/var/lib/livi/compositor-profile-20261030"


def checked(args):
    result = run(args)
    if result.get("returncode") != 0:
        raise RuntimeError("Command failed: " + shlex.join(args))
    return result


class CompositorProfileTrial(network.NetworkTrial):
    def __init__(self, root=Path("/")):
        super().__init__(root)
        self.directory = self.at(TRIAL_DIR)
        self.metadata_path = self.directory / "metadata.json"
        self.target = self.at(TARGET)
        self.candidate = self.at(CANDIDATE)
        self.backup = self.directory / "livi-compositor.before"
        self.dropin = self.at(DROPIN)
        self.core = self.at(ui.TARGET)

    def preserved_fingerprints(self):
        # Reuse the UI trial's well-defined startup fingerprints, but retain the core too.
        preserved = ui.UiBootTrial.preserved_fingerprints(self)
        preserved["core_binary_sha256"] = base.digest(self.core)
        return preserved

    def dropin_owned(self):
        return not self.dropin.is_symlink() and self.dropin.is_file() and self.dropin.read_bytes() == DROPIN_BYTES

    def inspect(self):
        metadata = self.metadata()
        current = base.digest(self.target) if not self.target.is_symlink() else None
        return {"compositor_binary_sha256": current, "core_binary_sha256": base.digest(self.core),
                "profile_trial_applied": bool(metadata and metadata["status"] == "applied" and current == metadata["candidate_sha256"] and self.dropin_owned()),
                "trace_dropin_owned": self.dropin_owned(), "preserved_fingerprints": self.preserved_fingerprints(),
                "trial_metadata": metadata}

    def verify_applied(self, metadata):
        ui.regular(self.target)
        if base.digest(self.target) != metadata["candidate_sha256"]:
            raise RuntimeError("Compositor changed externally")
        info = self.target.stat()
        expected = metadata["original_stat"]
        if (stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid) != (expected["mode"], expected["uid"], expected["gid"]):
            raise RuntimeError("Compositor mode or ownership changed externally")
        if not self.dropin_owned():
            raise RuntimeError("Trace drop-in changed externally")
        if self.preserved_fingerprints() != metadata["preserved_fingerprints"]:
            raise RuntimeError("Working core or unrelated startup configuration changed")
        env = checked(["systemctl", "show", "livi-kiosk.service", "--property=Environment", "--value"])["stdout"]
        if "LIVI_BOOT_TRACE=1" not in shlex.split(env):
            raise RuntimeError("Trace environment did not become effective")

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
            return {"status": "already-applied", "reboot_required": True}
        if previous and previous["status"] != "rolled-back":
            raise RuntimeError("Interrupted trial exists; run rollback first")
        if self.dropin.exists() or self.dropin.is_symlink():
            raise RuntimeError("Preexisting trace drop-in; refusing trial")
        if not previous and (self.backup.exists() or self.backup.is_symlink()):
            raise RuntimeError("Preexisting backup without metadata")
        ui.validate_elf(self.target)
        ui.validate_elf(self.candidate)
        ui.regular(self.core)
        env = checked(["systemctl", "show", "livi-kiosk.service", "--property=Environment", "--value"])["stdout"]
        if any(value.startswith("LIVI_BOOT_TRACE=") for value in shlex.split(env)):
            raise RuntimeError("Preexisting trace environment; refusing conflict")
        candidate_bytes = self.candidate.read_bytes()
        if hashlib.sha256(candidate_bytes).hexdigest() != expected_sha256:
            raise RuntimeError("Candidate SHA256 mismatch")
        ldd = {"skipped": "fake-root test"}
        if self.root == Path("/"):
            ldd = run(["ldd", str(self.candidate)])
            if ldd.get("returncode") != 0 or "not found" in (ldd.get("stdout", "") + ldd.get("stderr", "")).lower():
                raise RuntimeError("Candidate shared-library validation failed")
        info = self.target.stat()
        original = self.target.read_bytes()
        original_sha = hashlib.sha256(original).hexdigest()
        if original_sha == expected_sha256:
            raise RuntimeError("Candidate is identical to current compositor")
        original_stat = {"mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid}
        ui.atomic_binary(self.backup, original, 0o755, os.geteuid(), os.getegid())
        metadata = {"version": 1, "status": "applying",
                    "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "original_sha256": original_sha, "candidate_sha256": expected_sha256,
                    "original_stat": original_stat, "candidate_ldd": ldd,
                    "dropin_sha256": hashlib.sha256(DROPIN_BYTES).hexdigest(),
                    "dropin_parent_existed": self.dropin.parent.exists(),
                    "preserved_fingerprints": self.preserved_fingerprints(),
                    "only_system_changes": [TARGET, DROPIN],
                    "rollback": "sudo python3 " + shlex.quote(str(Path(__file__).resolve())) + " rollback"}
        base.atomic_json(self.metadata_path, metadata)
        ui.regular(self.target)
        if base.digest(self.target) != original_sha:
            raise RuntimeError("Compositor changed during preparation")
        ui.atomic_binary(self.target, candidate_bytes, **original_stat)
        self.dropin.parent.mkdir(parents=True, exist_ok=True)
        network.atomic_bytes(self.dropin, DROPIN_BYTES, replace=False)
        checked(["systemctl", "daemon-reload"])
        self.verify_applied(metadata)
        metadata["status"] = "applied"
        base.atomic_json(self.metadata_path, metadata)
        return {"status": "applied", "reboot_required": True,
                "compositor_binary_sha256": expected_sha256, "message": "Helper never reboots"}

    def rollback(self):
        self.prepare()
        metadata = self.metadata()
        if not metadata:
            return {"status": "no-trial", "reboot_required": False}
        if metadata["status"] == "rolled-back":
            return {"status": "already-rolled-back", "reboot_required": False}
        ui.regular(self.target)
        current = base.digest(self.target)
        if current not in (metadata["candidate_sha256"], metadata["original_sha256"]):
            raise RuntimeError("Compositor changed externally; refusing rollback")
        if current == metadata["original_sha256"] and metadata["status"] not in ("applying", "rolling-back"):
            raise RuntimeError("Applied compositor was replaced externally")
        if self.dropin.exists() or self.dropin.is_symlink():
            if not self.dropin_owned():
                raise RuntimeError("Trace drop-in changed externally; refusing rollback")
        if current == metadata["candidate_sha256"]:
            info = self.target.stat()
            expected = metadata["original_stat"]
            if (stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid) != (expected["mode"], expected["uid"], expected["gid"]):
                raise RuntimeError("Compositor mode or ownership changed externally")
            ui.regular(self.backup)
            if base.digest(self.backup) != metadata["original_sha256"]:
                raise RuntimeError("Original compositor backup changed")
        metadata["status"] = "rolling-back"
        base.atomic_json(self.metadata_path, metadata)
        if current == metadata["candidate_sha256"]:
            ui.atomic_binary(self.target, self.backup.read_bytes(), **metadata["original_stat"])
            if base.digest(self.target) != metadata["original_sha256"]:
                raise RuntimeError("Restored compositor verification failed")
        if self.dropin_owned():
            self.dropin.unlink()
        if not metadata["dropin_parent_existed"]:
            try:
                self.dropin.parent.rmdir()
            except OSError:
                pass
        checked(["systemctl", "daemon-reload"])
        metadata["status"] = "rolled-back"
        metadata["rolled_back_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        base.atomic_json(self.metadata_path, metadata)
        return {"status": "rolled-back", "reboot_required": True, "message": "Helper never reboots"}

    def trace_lines(self):
        log = self.at("/home/livi/.config/LIVI/log/compositor.log")
        try:
            with log.open("rb") as stream:
                stream.seek(0, os.SEEK_END)
                stream.seek(max(0, stream.tell() - 524288))
                text = stream.read().decode("utf-8", errors="replace")
            return [line for line in text.splitlines() if "[boot-trace]" in line][-300:]
        except OSError:
            return []

    def firmware_facts(self):
        result = {"bootloader_version": run(["vcgencmd", "bootloader_version"]),
                  "bootloader_config": run(["vcgencmd", "bootloader_config"]),
                  "get_throttled": run(["vcgencmd", "get_throttled"]),
                  "vclog_msg": run(["vclog", "--msg"]),
                  "vclog_assert": run(["vclog", "--assert"])}
        optional_version = shutil.which("rpi-bootloader-version")
        if optional_version:
            result["rpi_bootloader_version"] = run([optional_version])
        try:
            result["kernel_cmdline"] = self.at("/proc/cmdline").read_text().strip()
        except OSError:
            pass
        chosen = self.at("/proc/device-tree/chosen")
        result["chosen_bootloader_property_names"] = sorted(path.name for path in chosen.glob("*bootloader*"))
        bootloader = chosen / "bootloader"
        try:
            result["chosen_bootloader_child_property_names"] = sorted(path.name for path in bootloader.iterdir())
        except OSError:
            result["chosen_bootloader_child_property_names"] = []
        return result

    def snapshot(self, label):
        result = super().snapshot(label)
        inspection = self.inspect()
        for key in ("compositor_binary_sha256", "core_binary_sha256", "profile_trial_applied"):
            result[key] = inspection[key]
        result["trace_lines"] = self.trace_lines()
        result["firmware_facts"] = self.firmware_facts()
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
    trial = CompositorProfileTrial()
    try:
        result = getattr(trial, args.command)(args.argument) if args.command in ("snapshot", "apply") else getattr(trial, args.command)()
    except (RuntimeError, ValueError, OSError) as exc:
        parser.exit(1, "Error: " + str(exc) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
