import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("ui_trial", Path(__file__).with_name("ui-boot-trial.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def elf(payload):
    header = bytearray(64)
    header[:7] = b"\x7fELF\x02\x01\x01"
    header[16:18] = (3).to_bytes(2, "little")
    header[18:20] = (183).to_bytes(2, "little")
    return bytes(header) + payload


class UiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.trial = m.UiBootTrial(Path(self.tmp.name))
        self.trial.target.parent.mkdir(parents=True)
        self.original = elf(b"original")
        self.candidate = elf(b"candidate")
        self.trial.target.write_bytes(self.original)
        self.trial.target.chmod(0o751)
        self.trial.candidate.parent.mkdir(parents=True)
        self.trial.candidate.write_bytes(self.candidate)
        self.sha = hashlib.sha256(self.candidate).hexdigest()
        self.trial.marker.parent.mkdir(parents=True)
        self.trial.marker.write_bytes(b"existing marker")
        self.trial.override.parent.mkdir(parents=True)
        self.trial.override.write_bytes(b"existing override")
        self.trial.at(m.REAP_MASK).symlink_to("/dev/null")
        self.config = self.trial.at("/home/livi/.config/LIVI/config.json")
        self.config.parent.mkdir(parents=True)
        self.config.write_text('{"wifiPassword":"secret-do-not-return"}')
        self.auth = self.config.parent / "mfi" / "identity.pk8"
        self.auth.parent.mkdir()
        self.auth.write_bytes(b"private key untouched")

    def test_apply_transaction_preserves_mode_owner_config_and_startup(self):
        before = self.trial.target.stat()
        fingerprints = self.trial.preserved_fingerprints()
        result = self.trial.apply(self.sha)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(self.trial.target.read_bytes(), self.candidate)
        self.assertEqual(self.trial.backup.read_bytes(), self.original)
        self.assertEqual(self.trial.backup.stat().st_mode & 0o777, 0o755)
        after = self.trial.target.stat()
        self.assertEqual((after.st_mode & 0o777, after.st_uid, after.st_gid), (0o751, before.st_uid, before.st_gid))
        self.assertEqual(self.trial.preserved_fingerprints(), fingerprints)
        self.assertEqual(self.auth.read_bytes(), b"private key untouched")
        self.assertEqual(self.config.read_text(), '{"wifiPassword":"secret-do-not-return"}')
        self.assertTrue(self.trial.inspect()["ui_binary_trial_applied"])

    def test_invalid_sha_rejected_before_trial(self):
        with self.assertRaisesRegex(ValueError, "hexadecimal"):
            self.trial.apply("x" * 64)
        self.assertFalse(self.trial.directory.exists())

    def test_ldd_missing_dependency_rejected_without_system_mutation(self):
        self.trial.root = Path("/")
        with patch.object(self.trial, "prepare"), patch.object(m, "run", return_value={"returncode": 0, "stdout": "libmissing.so => not found"}):
            with self.assertRaisesRegex(RuntimeError, "shared-library"):
                self.trial.apply(self.sha)
        self.assertEqual(self.trial.target.read_bytes(), self.original)

    def test_preexisting_backup_without_metadata_refused(self):
        self.trial.prepare()
        self.trial.backup.write_bytes(b"external backup")
        with self.assertRaisesRegex(RuntimeError, "Preexisting trial"):
            self.trial.apply(self.sha)
        self.assertEqual(self.trial.backup.read_bytes(), b"external backup")

    def test_hash_mismatch_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "SHA256"):
            self.trial.apply("0" * 64)
        self.assertEqual(self.trial.target.read_bytes(), self.original)
        self.assertIsNone(self.trial.metadata())

    def test_invalid_candidate_elf_rejected(self):
        self.trial.candidate.write_bytes(b"not an executable")
        with self.assertRaisesRegex(RuntimeError, "ARM64"):
            self.trial.apply(self.sha)

    def test_wrong_architecture_rejected(self):
        header = bytearray(self.candidate)
        header[18:20] = (62).to_bytes(2, "little")
        self.trial.candidate.write_bytes(header)
        with self.assertRaisesRegex(RuntimeError, "ARM64"):
            self.trial.apply(self.sha)

    def test_invalid_existing_target_rejected(self):
        self.trial.target.write_bytes(b"unexpected old binary")
        with self.assertRaisesRegex(RuntimeError, "ARM64"):
            self.trial.apply(self.sha)

    def test_candidate_symlink_rejected(self):
        self.trial.candidate.unlink()
        self.trial.candidate.symlink_to(self.trial.target)
        with self.assertRaisesRegex(RuntimeError, "non-symlink"):
            self.trial.apply(self.sha)

    def test_target_symlink_rejected(self):
        self.trial.target.unlink()
        self.trial.target.symlink_to(self.trial.candidate)
        with self.assertRaisesRegex(RuntimeError, "non-symlink"):
            self.trial.apply(self.sha)

    def test_apply_idempotent_without_staged_file(self):
        self.trial.apply(self.sha)
        self.trial.candidate.unlink()
        self.assertEqual(self.trial.apply(self.sha.upper())["status"], "already-applied")
        with self.assertRaisesRegex(RuntimeError, "Different trial"):
            self.trial.apply("a" * 64)

    def test_rollback_restores_original_mode_and_binary(self):
        self.trial.apply(self.sha)
        self.assertEqual(self.trial.rollback()["status"], "rolled-back")
        self.assertEqual(self.trial.target.read_bytes(), self.original)
        self.assertEqual(self.trial.target.stat().st_mode & 0o777, 0o751)
        self.assertFalse(self.trial.inspect()["ui_binary_trial_applied"])
        self.assertEqual(self.trial.rollback()["status"], "already-rolled-back")
        self.assertEqual(self.trial.apply(self.sha)["status"], "applied")

    def test_interrupted_before_binary_replacement_recovers(self):
        real = m.atomic_binary
        def interrupted(path, *args, **kwargs):
            if path == self.trial.target:
                raise OSError("interrupted")
            return real(path, *args, **kwargs)
        with patch.object(m, "atomic_binary", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "interrupted"):
                self.trial.apply(self.sha)
        self.assertEqual(self.trial.metadata()["status"], "applying")
        with self.assertRaisesRegex(RuntimeError, "Interrupted"):
            self.trial.apply(self.sha)
        self.assertEqual(self.trial.rollback()["status"], "rolled-back")
        self.assertEqual(self.trial.target.read_bytes(), self.original)

    def test_interrupted_after_replacement_recovers(self):
        with patch.object(self.trial, "verify_applied", side_effect=RuntimeError("interrupted")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self.trial.apply(self.sha)
        self.assertEqual(self.trial.metadata()["status"], "applying")
        self.assertEqual(self.trial.target.read_bytes(), self.candidate)
        self.trial.rollback()
        self.assertEqual(self.trial.target.read_bytes(), self.original)

    def test_external_binary_edit_refused(self):
        self.trial.apply(self.sha)
        self.trial.target.write_bytes(elf(b"external"))
        with self.assertRaisesRegex(RuntimeError, "externally"):
            self.trial.rollback()
        with self.assertRaisesRegex(RuntimeError, "externally"):
            self.trial.apply(self.sha)

    def test_external_mode_edit_refused_on_reapply(self):
        self.trial.apply(self.sha)
        self.trial.target.chmod(0o700)
        with self.assertRaisesRegex(RuntimeError, "mode"):
            self.trial.apply(self.sha)
        with self.assertRaisesRegex(RuntimeError, "mode"):
            self.trial.rollback()

    def test_corrupt_backup_refuses_rollback(self):
        self.trial.apply(self.sha)
        self.trial.backup.write_bytes(b"corrupt")
        with self.assertRaisesRegex(RuntimeError, "backup changed"):
            self.trial.rollback()

    def test_startup_fingerprint_change_refused(self):
        self.trial.apply(self.sha)
        self.trial.marker.write_bytes(b"external marker")
        with self.assertRaisesRegex(RuntimeError, "Unrelated"):
            self.trial.apply(self.sha)
        self.trial.rollback()
        self.assertEqual(self.trial.marker.read_bytes(), b"external marker")

    def test_filtered_milestones_do_not_include_unrelated_secrets(self):
        log = self.config.parent / "log" / "LIVI.log"
        log.parent.mkdir()
        log.write_text("config password=secret-do-not-return\nlistening on /run/user/1000/core.sock\nUI started\nother noise\n")
        result = self.trial.startup_milestones()
        self.assertEqual(len(result["lines"]), 2)
        self.assertNotIn("secret-do-not-return", json.dumps(result))

    def test_snapshot_retains_inherited_metrics(self):
        self.trial.prepare()
        path = self.trial.directory / "snapshot.json"
        inherited = {"path": str(path), "audio_health": {"both_active": True}, "network_wait_removed": False}
        with patch.object(m.network.NetworkTrial, "snapshot", return_value=inherited):
            result = self.trial.snapshot("baseline")
        self.assertEqual(result["core_binary_sha256"], hashlib.sha256(self.original).hexdigest())
        self.assertFalse(result["ui_binary_trial_applied"])
        self.assertTrue(result["audio_health"]["both_active"])
        self.assertEqual(json.loads(path.read_text()), result)


if __name__ == "__main__":
    unittest.main()
