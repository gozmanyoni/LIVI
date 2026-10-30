import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("boot_trial", str(Path(__file__).with_name("boot-trial.py")))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TrialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.trial = module.Trial(Path(self.temp.name))
        self.trial.marker.parent.mkdir(parents=True)
        self.mock_observe = patch.object(self.trial, "observe_cloud_init", return_value={"units": "unchanged"})
        self.mock_observe.start()
        self.addCleanup(self.mock_observe.stop)

    def test_new_marker_apply_idempotence_and_rollback(self):
        self.assertTrue(self.trial.apply()["marker_created"])
        self.assertEqual(self.trial.marker.read_bytes(), b"")
        metadata_before = self.trial.metadata_path.read_bytes()
        self.assertEqual(self.trial.apply()["status"], "already-applied")
        self.assertEqual(self.trial.metadata_path.read_bytes(), metadata_before)
        self.assertEqual(stat.S_IMODE(self.trial.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.trial.metadata_path.stat().st_mode), 0o600)
        self.assertEqual(self.trial.rollback()["status"], "rolled-back")
        self.assertFalse(self.trial.marker.exists())
        self.assertEqual(self.trial.rollback()["status"], "already-rolled-back")

    def test_existing_marker_preserves_contents_mode_and_mtime(self):
        self.trial.marker.write_bytes(b"preexisting reason\n")
        self.trial.marker.chmod(0o640)
        before = self.trial.marker.stat()
        self.assertFalse(self.trial.apply()["marker_created"])
        self.assertEqual((self.trial.directory / "marker.before").read_bytes(), b"preexisting reason\n")
        self.trial.rollback()
        self.assertEqual(self.trial.marker.read_bytes(), b"preexisting reason\n")
        after = self.trial.marker.stat()
        self.assertEqual(stat.S_IMODE(after.st_mode), 0o640)
        self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)

    def test_external_marker_edits_never_deleted(self):
        self.trial.apply()
        self.trial.marker.write_text("new owner")
        with self.assertRaisesRegex(RuntimeError, "changed externally"):
            self.trial.rollback()
        with self.assertRaisesRegex(RuntimeError, "changed externally"):
            self.trial.apply()
        self.assertEqual(self.trial.marker.read_text(), "new owner")

    def test_interrupted_apply_requires_rollback(self):
        self.trial.apply()
        data = self.trial.metadata()
        data["status"] = "applying"
        module.atomic_json(self.trial.metadata_path, data)
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            self.trial.apply()
        self.trial.rollback()
        self.assertFalse(self.trial.marker.exists())

    def test_reapply_after_rollback(self):
        self.trial.apply()
        self.trial.rollback()
        self.assertTrue(self.trial.apply()["marker_created"])
        self.trial.rollback()

    def test_symlink_refused(self):
        target = Path(self.temp.name) / "elsewhere"
        target.write_text("keep")
        self.trial.marker.symlink_to(target)
        with self.assertRaisesRegex(RuntimeError, "non-regular"):
            self.trial.apply()
        self.assertEqual(target.read_text(), "keep")

    def test_snapshot_redacts_config_and_keeps_health(self):
        config = self.trial.at("/home/livi/.config/LIVI/config.json")
        config.parent.mkdir(parents=True)
        config.write_text('{"wifiPassword":"do-not-print"}')
        boot_id = self.trial.at("/proc/sys/kernel/random/boot_id")
        boot_id.parent.mkdir(parents=True)
        boot_id.write_text("test-boot")
        with patch.object(module, "run", return_value={"returncode": 0, "stdout": "ready"}):
            snapshot = self.trial.snapshot("baseline")
        self.assertEqual(snapshot["boot_id"], "test-boot")
        self.assertEqual(snapshot["config_sha256"], module.digest(config))
        saved = Path(snapshot["path"]).read_text()
        self.assertNotIn("do-not-print", saved)
        self.assertEqual(json.loads(saved), snapshot)

    def test_invalid_label_refused(self):
        with self.assertRaises(ValueError):
            self.trial.snapshot("../../outside")


if __name__ == "__main__":
    unittest.main()
