import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("profile_trial", Path(__file__).with_name("compositor-profile-trial.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def elf(payload):
    header = bytearray(64)
    header[:7] = b"\x7fELF\x02\x01\x01"
    header[16:18] = (3).to_bytes(2, "little")
    header[18:20] = (183).to_bytes(2, "little")
    return bytes(header) + payload


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.trial = m.CompositorProfileTrial(Path(self.tmp.name))
        self.original = elf(b"original compositor")
        self.candidate = elf(b"instrumented compositor")
        self.trial.target.parent.mkdir(parents=True)
        self.trial.target.write_bytes(self.original)
        self.trial.target.chmod(0o751)
        self.trial.candidate.parent.mkdir(parents=True)
        self.trial.candidate.write_bytes(self.candidate)
        self.sha = hashlib.sha256(self.candidate).hexdigest()
        self.trial.core.parent.mkdir(parents=True)
        self.trial.core.write_bytes(elf(b"working core unchanged"))
        self.trial.marker.parent.mkdir(parents=True)
        self.trial.marker.write_bytes(b"cloud marker")
        self.trial.override.parent.mkdir(parents=True)
        self.trial.override.write_bytes(b"network override")
        self.trial.at(m.ui.REAP_MASK).symlink_to("/dev/null")
        self.config = self.trial.at("/home/livi/.config/LIVI/config.json")
        self.config.parent.mkdir(parents=True)
        self.config.write_text('{"password":"super-secret"}')
        self.auth = self.config.parent / "mfi" / "identity.pk8"
        self.auth.parent.mkdir()
        self.auth.write_bytes(b"untouched private key")
        self.calls = []
        self.fail_reload = False
        self.existing_environment = ""
        self.patcher = patch.object(m, "run", side_effect=self.fake_run)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def fake_run(self, args, **kwargs):
        self.calls.append(args)
        if args[:2] == ["systemctl", "show"]:
            return {"returncode": 0, "stdout": "LIVI_BOOT_TRACE=1" if self.trial.dropin_owned() else self.existing_environment}
        if args == ["systemctl", "daemon-reload"]:
            return {"returncode": 1 if self.fail_reload else 0, "stdout": ""}
        if args[0] in ("vcgencmd", "vclog", "/fake/rpi-bootloader-version"):
            return {"returncode": 0, "stdout": "read-only firmware fact"}
        raise AssertionError(args)

    def test_apply_only_changes_compositor_and_owned_dropin(self):
        before = self.trial.preserved_fingerprints()
        info = self.trial.target.stat()
        self.assertEqual(self.trial.apply(self.sha)["status"], "applied")
        self.assertEqual(self.trial.target.read_bytes(), self.candidate)
        after = self.trial.target.stat()
        self.assertEqual((after.st_mode & 0o777, after.st_uid, after.st_gid), (0o751, info.st_uid, info.st_gid))
        self.assertEqual(self.trial.backup.read_bytes(), self.original)
        self.assertEqual(self.trial.dropin.read_bytes(), m.DROPIN_BYTES)
        self.assertEqual(before, self.trial.preserved_fingerprints())
        self.assertEqual(self.config.read_text(), '{"password":"super-secret"}')
        self.assertEqual(self.auth.read_bytes(), b"untouched private key")
        self.assertTrue(self.trial.inspect()["profile_trial_applied"])
        self.assertFalse(any(arg in {"reboot", "rpi-eeprom-update"} for args in self.calls for arg in args))

    def test_reject_invalid_sha_and_hash_mismatch(self):
        with self.assertRaises(ValueError):
            self.trial.apply("invalid")
        with self.assertRaisesRegex(RuntimeError, "SHA256"):
            self.trial.apply("0" * 64)
        self.assertEqual(self.trial.target.read_bytes(), self.original)

    def test_reject_invalid_candidate_elf(self):
        self.trial.candidate.write_bytes(b"invalid")
        with self.assertRaisesRegex(RuntimeError, "ARM64"):
            self.trial.apply(self.sha)

    def test_reject_candidate_symlink(self):
        self.trial.candidate.unlink()
        self.trial.candidate.symlink_to(self.trial.target)
        with self.assertRaisesRegex(RuntimeError, "non-symlink"):
            self.trial.apply(self.sha)

    def test_preexisting_dropin_refused(self):
        self.trial.dropin.parent.mkdir()
        self.trial.dropin.write_bytes(b"preexisting config")
        with self.assertRaisesRegex(RuntimeError, "Preexisting"):
            self.trial.apply(self.sha)
        self.assertEqual(self.trial.dropin.read_bytes(), b"preexisting config")

    def test_preexisting_dropin_symlink_refused(self):
        self.trial.dropin.parent.mkdir()
        self.trial.dropin.symlink_to("/nonexistent")
        with self.assertRaisesRegex(RuntimeError, "Preexisting"):
            self.trial.apply(self.sha)

    def test_existing_trace_environment_refused(self):
        self.existing_environment = "OTHER=value LIVI_BOOT_TRACE=0"
        with self.assertRaisesRegex(RuntimeError, "environment"):
            self.trial.apply(self.sha)

    def test_apply_idempotent(self):
        self.trial.apply(self.sha)
        self.trial.candidate.unlink()
        self.assertEqual(self.trial.apply(self.sha)["status"], "already-applied")
        self.assertEqual(self.calls.count(["systemctl", "daemon-reload"]), 1)

    def test_rollback_restores_binary_and_removes_only_owned_dropin(self):
        self.trial.dropin.parent.mkdir()
        other = self.trial.dropin.parent / "10-existing.conf"
        other.write_bytes(b"unrelated")
        before = self.trial.preserved_fingerprints()
        self.trial.apply(self.sha)
        self.assertEqual(self.trial.rollback()["status"], "rolled-back")
        self.assertEqual(self.trial.target.read_bytes(), self.original)
        self.assertEqual(self.trial.target.stat().st_mode & 0o777, 0o751)
        self.assertFalse(self.trial.dropin.exists())
        self.assertEqual(other.read_bytes(), b"unrelated")
        self.assertEqual(self.trial.preserved_fingerprints(), before)
        self.assertEqual(self.trial.rollback()["status"], "already-rolled-back")

    def test_rollback_removes_new_empty_dropin_directory(self):
        self.trial.apply(self.sha)
        self.trial.rollback()
        self.assertFalse(self.trial.dropin.parent.exists())

    def test_external_dropin_edit_prevents_partial_rollback(self):
        self.trial.apply(self.sha)
        self.trial.dropin.write_bytes(b"external edit")
        with self.assertRaisesRegex(RuntimeError, "drop-in changed"):
            self.trial.rollback()
        self.assertEqual(self.trial.target.read_bytes(), self.candidate)

    def test_external_binary_edit_prevents_rollback(self):
        self.trial.apply(self.sha)
        self.trial.target.write_bytes(elf(b"external"))
        with self.assertRaisesRegex(RuntimeError, "externally"):
            self.trial.rollback()
        self.assertTrue(self.trial.dropin_owned())

    def test_working_core_change_detected_and_never_restored(self):
        self.trial.apply(self.sha)
        self.trial.core.write_bytes(elf(b"external working core"))
        with self.assertRaisesRegex(RuntimeError, "Working core"):
            self.trial.apply(self.sha)
        self.trial.rollback()
        self.assertEqual(self.trial.core.read_bytes(), elf(b"external working core"))

    def test_partial_failure_before_binary_copy_recovers(self):
        real = m.ui.atomic_binary
        def interrupted(path, *args, **kwargs):
            if path == self.trial.target:
                raise OSError("interrupted")
            return real(path, *args, **kwargs)
        with patch.object(m.ui, "atomic_binary", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.trial.apply(self.sha)
        self.assertEqual(self.trial.metadata()["status"], "applying")
        self.assertEqual(self.trial.rollback()["status"], "rolled-back")

    def test_partial_failure_after_binary_before_dropin_recovers(self):
        with patch.object(m.network, "atomic_bytes", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.trial.apply(self.sha)
        self.assertEqual(self.trial.target.read_bytes(), self.candidate)
        self.trial.rollback()
        self.assertEqual(self.trial.target.read_bytes(), self.original)
        self.assertFalse(self.trial.dropin.exists())

    def test_reload_failure_and_rollback_resumption(self):
        self.fail_reload = True
        with self.assertRaises(RuntimeError):
            self.trial.apply(self.sha)
        with self.assertRaises(RuntimeError):
            self.trial.rollback()
        self.assertEqual(self.trial.metadata()["status"], "rolling-back")
        self.assertEqual(self.trial.target.read_bytes(), self.original)
        self.fail_reload = False
        self.assertEqual(self.trial.rollback()["status"], "rolled-back")

    def test_trace_filter_and_read_only_firmware_commands(self):
        log = self.config.parent / "log" / "compositor.log"
        log.parent.mkdir()
        log.write_text("secret=super-secret\n[boot-trace] phase=drm-ready elapsed_ms=10\nordinary line\n[boot-trace] phase=first-frame elapsed_ms=50\n")
        self.assertEqual(len(self.trial.trace_lines()), 2)
        self.assertNotIn("super-secret", json.dumps(self.trial.trace_lines()))
        bootloader = self.trial.at("/proc/device-tree/chosen/bootloader")
        bootloader.mkdir(parents=True)
        (bootloader / "timestamp").write_bytes(b"raw timestamp")
        with patch.object(m.shutil, "which", return_value=None):
            facts = self.trial.firmware_facts()
        self.assertIn("bootloader_config", facts)
        self.assertIn("vclog_msg", facts)
        self.assertEqual(facts["chosen_bootloader_child_property_names"], ["timestamp"])
        self.assertEqual(self.calls, [["vcgencmd", "bootloader_version"], ["vcgencmd", "bootloader_config"],
                                     ["vcgencmd", "get_throttled"], ["vclog", "--msg"], ["vclog", "--assert"]])

    def test_optional_read_only_bootloader_version(self):
        with patch.object(m.shutil, "which", return_value="/fake/rpi-bootloader-version"):
            result = self.trial.firmware_facts()
        self.assertIn("rpi_bootloader_version", result)
        self.assertIn(["/fake/rpi-bootloader-version"], self.calls)

    def test_snapshot_inherits_metrics_and_reports_actual_core(self):
        self.trial.prepare()
        output = self.trial.directory / "snapshot.json"
        inherited = {"path": str(output), "audio_health": {"both_active": True}, "config_fingerprint": {"sha256": "safe"}}
        with patch.object(m.network.NetworkTrial, "snapshot", return_value=inherited):
            result = self.trial.snapshot("profile")
        self.assertEqual(result["core_binary_sha256"], hashlib.sha256(self.trial.core.read_bytes()).hexdigest())
        self.assertFalse(result["profile_trial_applied"])
        self.assertTrue(result["audio_health"]["both_active"])
        self.assertEqual(json.loads(output.read_text()), result)


if __name__ == "__main__":
    unittest.main()
