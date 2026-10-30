#!/usr/bin/env python3
"""Reversible e2scrub snapshot-reaper boot trial. Never reboots or changes fsck."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
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
UNIT = "e2scrub_reap.service"
MASK = "/etc/systemd/system/" + UNIT
TRIAL_DIR = "/var/lib/livi/scrub-boot-trial-20261030"


def checked(args):
    result = run(args)
    if result.get("returncode") != 0:
        raise RuntimeError("Command failed: " + shlex.join(args) + ": " + result.get("stderr", result.get("error", "")))
    return result


class ScrubTrial(network.NetworkTrial):
    def __init__(self, root=Path("/")):
        super().__init__(root)
        self.directory = self.at(TRIAL_DIR)
        self.metadata_path = self.directory / "metadata.json"
        self.reaper_mask = self.at(MASK)

    def reaper_state(self):
        result = checked(["systemctl", "show", UNIT, "--property=" + ",".join(network.PROPERTIES)])
        properties = dict(line.split("=", 1) for line in result["stdout"].splitlines() if "=" in line)
        return {"properties": properties,
                "effective_unit": run(["systemctl", "cat", UNIT]),
                "journal": run(["journalctl", "-b", "-u", UNIT, "--no-pager", "-o", "short-monotonic", "-n", "100"])}

    def preserved_fingerprints(self):
        return {"cloud_init_marker_sha256": base.digest(self.marker),
                "user_sessions_override_sha256": base.digest(self.override)}

    def storage_guard(self):
        block_result = checked(["lsblk", "-J", "-o", "NAME,TYPE,FSTYPE"])
        root_result = checked(["findmnt", "-J", "-o", "SOURCE,FSTYPE", "/"])
        try:
            blocks = json.loads(block_result["stdout"])["blockdevices"]
            roots = json.loads(root_result["stdout"])["filesystems"]
            flattened = []
            def visit(entries):
                for entry in entries:
                    flattened.append(entry)
                    visit(entry.get("children", []))
            visit(blocks)
            if any(entry.get("type") == "lvm" for entry in flattened):
                raise RuntimeError("LVM device present; refusing to mask snapshot cleanup")
            if len(roots) != 1:
                raise RuntimeError("Cannot identify a unique root filesystem")
            root = roots[0]
            source = root.get("source", "")
            matches = [entry for entry in flattened if entry.get("name") == Path(source).name]
            if not source.startswith("/dev/") or root.get("fstype") != "ext4" or len(matches) != 1 or matches[0].get("type") != "part" or matches[0].get("fstype") != "ext4":
                raise RuntimeError("Root must be a plain ext4 block partition")
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise RuntimeError("Unrecognized block/root filesystem evidence") from exc
        mapper = self.at("/dev/mapper")
        snapshots = sorted(path.name for path in mapper.glob("*e2scrub*"))
        if snapshots:
            raise RuntimeError("Existing e2scrub mapper snapshots; refusing mask")
        return {"lsblk": json.loads(block_result["stdout"]),
                "findmnt": json.loads(root_result["stdout"]),
                "mapper_e2scrub_snapshots": snapshots,
                "root_is_plain_ext4_partition": True, "no_lvm_devices": True}

    def mask_owned(self):
        return self.reaper_mask.is_symlink() and os.readlink(self.reaper_mask) == "/dev/null"

    def verify_applied(self, metadata):
        if not self.mask_owned():
            raise RuntimeError("Trial mask changed externally; refusing modification")
        if self.preserved_fingerprints() != metadata["preserved_fingerprints"]:
            raise RuntimeError("Unrelated startup configuration changed during trial")
        state = self.reaper_state()
        if state["properties"].get("LoadState") != "masked":
            raise RuntimeError("Effective e2scrub_reap unit is not masked")
        return state

    def inspect(self):
        state = self.reaper_state()
        return {"reaper_unit": state,
                "scrub_reaper_masked": state["properties"].get("LoadState") == "masked",
                "mask_points_to_dev_null": self.mask_owned(),
                "preserved_fingerprints": self.preserved_fingerprints(),
                "trial_metadata": self.metadata()}

    def apply(self):
        self.prepare()
        evidence = self.storage_guard()
        previous = self.metadata()
        if previous and previous["status"] == "applied":
            self.verify_applied(previous)
            return {"status": "already-applied", "reboot_required": True, "message": "Helper never reboots"}
        if previous and previous["status"] != "rolled-back":
            raise RuntimeError("Interrupted trial exists; run rollback first")
        if self.reaper_mask.exists() or self.reaper_mask.is_symlink():
            raise RuntimeError("Preexisting e2scrub_reap override or symlink; refusing trial")
        state = self.reaper_state()
        properties = state["properties"]
        if properties.get("LoadState") != "loaded":
            raise RuntimeError("Expected loaded vendor e2scrub_reap unit")
        fragment = properties.get("FragmentPath", "")
        if not fragment.startswith(("/usr/lib/systemd/system/", "/lib/systemd/system/")):
            raise RuntimeError("Expected installed vendor e2scrub_reap fragment")
        original = self.at(fragment).read_bytes()
        network.atomic_bytes(self.directory / "unit.before", original, 0o600)
        metadata = {"version": 1, "status": "applying",
                    "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "source_fragment": fragment, "source_sha256": hashlib.sha256(original).hexdigest(),
                    "state_before": state, "storage_evidence": evidence,
                    "preserved_fingerprints": self.preserved_fingerprints(),
                    "only_system_change": MASK, "mask_target": "/dev/null",
                    "rollback": "sudo python3 " + shlex.quote(str(Path(__file__).resolve())) + " rollback"}
        base.atomic_json(self.metadata_path, metadata)
        self.reaper_mask.parent.mkdir(parents=True, exist_ok=True)
        # symlink creation is atomic and refuses an existing file or symlink.
        self.reaper_mask.symlink_to("/dev/null")
        checked(["systemctl", "daemon-reload"])
        self.verify_applied(metadata)
        metadata["status"] = "applied"
        base.atomic_json(self.metadata_path, metadata)
        return {"status": "applied", "reboot_required": True,
                "scrub_reaper_masked": True, "message": "Helper never reboots"}

    def rollback(self):
        self.prepare()
        metadata = self.metadata()
        if not metadata:
            return {"status": "no-trial", "reboot_required": False}
        if metadata["status"] == "rolled-back":
            return {"status": "already-rolled-back", "reboot_required": False}
        if self.reaper_mask.exists() or self.reaper_mask.is_symlink():
            if not self.mask_owned():
                raise RuntimeError("Mask changed externally; refusing rollback")
            self.reaper_mask.unlink()
        checked(["systemctl", "daemon-reload"])
        metadata["status"] = "rolled-back"
        metadata["rolled_back_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        base.atomic_json(self.metadata_path, metadata)
        return {"status": "rolled-back", "reboot_required": True, "message": "Helper never reboots"}

    def snapshot(self, label):
        result = super().snapshot(label)
        state = self.reaper_state()
        result["scrub_reaper_unit"] = state
        result["scrub_reaper_masked"] = state["properties"].get("LoadState") == "masked"
        result["startup_preserved_fingerprints"] = self.preserved_fingerprints()
        base.atomic_json(Path(result["path"]), result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inspect", "snapshot", "apply", "rollback"))
    parser.add_argument("label", nargs="?")
    args = parser.parse_args()
    if args.command == "snapshot" and not args.label:
        parser.error("snapshot requires a label")
    if args.command != "snapshot" and args.label:
        parser.error("only snapshot takes a label")
    trial = ScrubTrial()
    try:
        result = trial.snapshot(args.label) if args.command == "snapshot" else getattr(trial, args.command)()
    except (RuntimeError, ValueError, OSError) as exc:
        parser.exit(1, "Error: " + str(exc) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
