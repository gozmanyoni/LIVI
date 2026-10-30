import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("scrub_trial", Path(__file__).with_name("scrub-boot-trial.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
UNIT = b"[Unit]\nDescription=Remove Stale Online ext4 Metadata Check Snapshots\n[Service]\nType=oneshot\nExecStart=/sbin/e2scrub_all -A -r\n[Install]\nWantedBy=multi-user.target\n"


class ScrubTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.trial = m.ScrubTrial(Path(self.tmp.name))
        self.vendor = self.trial.at("/usr/lib/systemd/system/" + m.UNIT)
        self.vendor.parent.mkdir(parents=True)
        self.vendor.write_bytes(UNIT)
        self.trial.marker.parent.mkdir(parents=True)
        self.trial.marker.write_bytes(b"cloud-init remains disabled")
        self.trial.override.parent.mkdir(parents=True)
        self.trial.override.write_bytes(b"unrelated network override")
        self.wants = self.trial.at("/etc/systemd/system/multi-user.target.wants/" + m.UNIT)
        self.wants.parent.mkdir(parents=True)
        self.wants.symlink_to("/usr/lib/systemd/system/" + m.UNIT)
        self.blocks = {"blockdevices": [{"name": "mmcblk0", "type": "disk", "fstype": None, "children": [{"name": "mmcblk0p2", "type": "part", "fstype": "ext4"}]}]}
        self.rootfs = {"filesystems": [{"source": "/dev/mmcblk0p2", "fstype": "ext4"}]}
        self.calls = []
        self.fail_reload = False
        self.force_unmasked = False
        self.patcher = patch.object(m, "run", side_effect=self.fake_run)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def fake_run(self, args, **kwargs):
        self.calls.append(args)
        if args[0] == "lsblk":
            return {"returncode": 0, "stdout": json.dumps(self.blocks)}
        if args[0] == "findmnt":
            return {"returncode": 0, "stdout": json.dumps(self.rootfs)}
        if args[:2] == ["systemctl", "show"]:
            masked = self.trial.mask_owned() and not self.force_unmasked
            return {"returncode": 0, "stdout": "LoadState=" + ("masked" if masked else "loaded") + "\nUnitFileState=" + ("masked" if masked else "enabled") + "\nFragmentPath=" + (m.MASK if masked else "/usr/lib/systemd/system/" + m.UNIT) + "\n"}
        if args[:2] == ["systemctl", "cat"]:
            return {"returncode": 0, "stdout": UNIT.decode()}
        if args[0] == "journalctl":
            return {"returncode": 0, "stdout": "reaper finished\n"}
        if args == ["systemctl", "daemon-reload"]:
            return {"returncode": 1 if self.fail_reload else 0, "stdout": "", "stderr": "reload failure" if self.fail_reload else ""}
        raise AssertionError(args)

    def test_apply_only_masks_and_preserves_wants_other_changes(self):
        before = self.trial.preserved_fingerprints()
        result = self.trial.apply()
        self.assertEqual(result["status"], "applied")
        self.assertTrue(self.trial.mask_owned())
        self.assertEqual(self.vendor.read_bytes(), UNIT)
        self.assertEqual(self.trial.preserved_fingerprints(), before)
        self.assertEqual(self.wants.readlink(), Path("/usr/lib/systemd/system/" + m.UNIT))
        self.assertEqual(self.trial.metadata()["storage_evidence"]["root_is_plain_ext4_partition"], True)
        self.assertEqual((self.trial.directory / "unit.before").read_bytes(), UNIT)
        self.assertFalse(any(arg in {"reboot", "disable", "fsck", "tune2fs"} for args in self.calls for arg in args))

    def test_lvm_anywhere_rejected(self):
        self.blocks["blockdevices"].append({"name": "unrelated-lvm", "type": "lvm", "fstype": "ext4"})
        with self.assertRaisesRegex(RuntimeError, "LVM"):
            self.trial.apply()
        self.assertFalse(self.trial.reaper_mask.is_symlink())

    def test_stale_mapper_snapshot_rejected(self):
        stale = self.trial.at("/dev/mapper/test.e2scrub-old")
        stale.parent.mkdir(parents=True)
        stale.touch()
        with self.assertRaisesRegex(RuntimeError, "snapshots"):
            self.trial.apply()

    def test_root_not_plain_partition_rejected(self):
        self.blocks["blockdevices"][0]["children"][0]["type"] = "crypt"
        with self.assertRaisesRegex(RuntimeError, "plain ext4"):
            self.trial.apply()

    def test_root_not_ext4_rejected(self):
        self.rootfs["filesystems"][0]["fstype"] = "btrfs"
        with self.assertRaisesRegex(RuntimeError, "plain ext4"):
            self.trial.apply()

    def test_malformed_storage_evidence_rejected(self):
        self.blocks = {"unexpected": []}
        with self.assertRaisesRegex(RuntimeError, "Unrecognized"):
            self.trial.apply()

    def test_preexisting_mask_rejected(self):
        self.trial.reaper_mask.symlink_to("/dev/null")
        with self.assertRaisesRegex(RuntimeError, "Preexisting"):
            self.trial.apply()

    def test_preexisting_regular_override_rejected(self):
        self.trial.reaper_mask.write_bytes(b"external")
        with self.assertRaisesRegex(RuntimeError, "Preexisting"):
            self.trial.apply()

    def test_idempotent_apply(self):
        self.trial.apply()
        self.assertEqual(self.trial.apply()["status"], "already-applied")
        self.assertEqual(self.calls.count(["systemctl", "daemon-reload"]), 1)

    def test_rollback_restores_initial_enabled_unit_and_wants(self):
        self.trial.apply()
        self.assertEqual(self.trial.rollback()["status"], "rolled-back")
        self.assertFalse(self.trial.reaper_mask.is_symlink())
        self.assertEqual(self.trial.reaper_state()["properties"]["UnitFileState"], "enabled")
        self.assertTrue(self.wants.is_symlink())
        self.assertEqual(self.trial.rollback()["status"], "already-rolled-back")
        self.assertEqual(self.trial.apply()["status"], "applied")

    def test_external_regular_file_edit_refused(self):
        self.trial.apply()
        self.trial.reaper_mask.unlink()
        self.trial.reaper_mask.write_bytes(b"external override")
        with self.assertRaisesRegex(RuntimeError, "externally"):
            self.trial.rollback()
        with self.assertRaisesRegex(RuntimeError, "externally"):
            self.trial.apply()
        self.assertEqual(self.trial.reaper_mask.read_bytes(), b"external override")

    def test_external_symlink_edit_refused(self):
        self.trial.apply()
        self.trial.reaper_mask.unlink()
        self.trial.reaper_mask.symlink_to("/external")
        with self.assertRaisesRegex(RuntimeError, "externally"):
            self.trial.rollback()

    def test_interrupted_before_mask_creation_recovers(self):
        with patch.object(Path, "symlink_to", side_effect=OSError("interrupted")):
            with self.assertRaisesRegex(OSError, "interrupted"):
                self.trial.apply()
        self.assertEqual(self.trial.metadata()["status"], "applying")
        self.assertEqual(self.trial.rollback()["status"], "rolled-back")

    def test_reload_failure_recovers(self):
        self.fail_reload = True
        with self.assertRaisesRegex(RuntimeError, "reload failure"):
            self.trial.apply()
        self.fail_reload = False
        self.assertEqual(self.trial.rollback()["status"], "rolled-back")

    def test_loadstate_verification_failure_recovers(self):
        self.force_unmasked = True
        with self.assertRaisesRegex(RuntimeError, "not masked"):
            self.trial.apply()
        self.assertEqual(self.trial.metadata()["status"], "applying")
        self.assertEqual(self.trial.rollback()["status"], "rolled-back")

    def test_preserved_fingerprints_change_detected(self):
        self.trial.apply()
        self.trial.marker.write_text("external marker edit")
        with self.assertRaisesRegex(RuntimeError, "Unrelated"):
            self.trial.apply()
        self.trial.rollback()
        self.assertEqual(self.trial.marker.read_text(), "external marker edit")

    def test_inspect_does_not_change_system(self):
        result = self.trial.inspect()
        self.assertFalse(result["scrub_reaper_masked"])
        self.assertFalse(self.trial.directory.exists())
        self.assertNotIn(["systemctl", "daemon-reload"], self.calls)

    def test_snapshot_extends_network_metrics(self):
        self.trial.prepare()
        path = self.trial.directory / "snapshot.json"
        inherited = {"path": str(path), "audio_health": {"both_active": True}, "process_start_times": {"processes": []}}
        with patch.object(m.network.NetworkTrial, "snapshot", return_value=inherited):
            result = self.trial.snapshot("test")
        self.assertFalse(result["scrub_reaper_masked"])
        self.assertTrue(result["audio_health"]["both_active"])
        self.assertEqual(json.loads(path.read_text()), result)
        self.assertEqual(result["scrub_reaper_unit"]["journal"]["returncode"], 0)


if __name__ == "__main__":
    unittest.main()
