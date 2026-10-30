import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("network_trial", Path(__file__).with_name("network-boot-trial.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

UNIT = b"# vendor comment\n[Unit]\nDescription=Permit User Sessions\nAfter=remote-fs.target nss-user-lookup.target network.target home.mount\n\n[Service]\nType=oneshot\nExecStart=/usr/lib/systemd/systemd-user-sessions start\nRemainAfterExit=yes\n"


class TrialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.trial = m.NetworkTrial(self.root)
        self.vendor = self.trial.at("/usr/lib/systemd/system/" + m.UNIT)
        self.vendor.parent.mkdir(parents=True)
        self.vendor.write_bytes(UNIT)
        self.trial.marker.parent.mkdir(parents=True)
        self.trial.marker.write_bytes(b"retained cloud marker")
        self.first_metadata = self.trial.at(m.base.TRIAL_DIR + "/metadata.json")
        self.first_metadata.parent.mkdir(parents=True)
        self.first_metadata.write_bytes(b'{"status":"applied"}')
        self.calls = []
        self.dropins = ""
        self.force_network = False
        self.fail_reload = False
        self.patcher = patch.object(m, "run", side_effect=self.fake_run)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def fake_run(self, args, **kwargs):
        self.calls.append(args)
        if args[:2] == ["systemctl", "show"]:
            override = self.trial.override.exists()
            after = "home.mount basic.target nss-user-lookup.target remote-fs.target"
            if not override or self.force_network:
                after += " network.target"
            fragment = m.OVERRIDE if override else "/usr/lib/systemd/system/" + m.UNIT
            return {"returncode": 0, "stdout": "FragmentPath=" + fragment + "\nDropInPaths=" + self.dropins + "\nAfter=" + after + "\n", "stderr": ""}
        if args[:2] == ["systemctl", "cat"]:
            src = self.trial.override if self.trial.override.exists() else self.vendor
            return {"returncode": 0, "stdout": src.read_text(), "stderr": ""}
        if args == ["systemctl", "daemon-reload"]:
            return {"returncode": 1 if self.fail_reload else 0, "stdout": "", "stderr": "reload failure" if self.fail_reload else ""}
        if args[0] == "runuser":
            return {"returncode": 0, "stdout": "active\nactive\n", "stderr": ""}
        raise AssertionError(args)

    def test_apply_exact_bytes_and_preserve_other_trial(self):
        result = self.trial.apply()
        self.assertEqual(result["status"], "applied")
        self.assertEqual(self.trial.override.read_bytes(), UNIT.replace(b"network.target", b""))
        self.assertEqual(self.vendor.read_bytes(), UNIT)
        self.assertEqual(self.trial.marker.read_bytes(), b"retained cloud marker")
        self.assertEqual(self.first_metadata.read_bytes(), b'{"status":"applied"}')
        self.assertEqual((self.trial.directory / "unit.before").read_bytes(), UNIT)
        self.assertEqual(self.trial.override.stat().st_mode & 0o777, 0o644)
        self.assertFalse(any("reboot" in arg for args in self.calls for arg in args))

    def test_apply_is_idempotent(self):
        self.trial.apply()
        self.assertEqual(self.trial.apply()["status"], "already-applied")
        self.assertEqual(self.calls.count(["systemctl", "daemon-reload"]), 1)

    def test_rollback_only_trial_override(self):
        self.trial.apply()
        self.assertEqual(self.trial.rollback()["status"], "rolled-back")
        self.assertFalse(self.trial.override.exists())
        self.assertEqual(self.trial.marker.read_bytes(), b"retained cloud marker")
        self.assertEqual(self.trial.rollback()["status"], "already-rolled-back")
        self.assertEqual(self.trial.apply()["status"], "applied")

    def test_preexisting_override_refused(self):
        self.trial.override.parent.mkdir(parents=True)
        self.trial.override.write_bytes(b"external")
        with self.assertRaisesRegex(RuntimeError, "Preexisting"):
            self.trial.apply()
        self.assertEqual(self.trial.override.read_bytes(), b"external")

    def test_broken_symlink_override_refused(self):
        self.trial.override.parent.mkdir(parents=True)
        self.trial.override.symlink_to("/nonexistent-target")
        with self.assertRaisesRegex(RuntimeError, "Preexisting"):
            self.trial.apply()

    def test_conflicting_dropin_refused(self):
        dropin = self.trial.at("/etc/systemd/system/systemd-user-sessions.service.d/network.conf")
        dropin.parent.mkdir(parents=True)
        dropin.write_text("[Unit]\nAfter=network.target\n")
        self.dropins = "/etc/systemd/system/systemd-user-sessions.service.d/network.conf"
        with self.assertRaisesRegex(RuntimeError, "Conflicting"):
            self.trial.apply()
        self.assertFalse(self.trial.override.exists())

    def test_external_edit_refuses_rollback_and_reapply(self):
        self.trial.apply()
        self.trial.override.write_bytes(b"external edit")
        with self.assertRaisesRegex(RuntimeError, "externally"):
            self.trial.rollback()
        with self.assertRaisesRegex(RuntimeError, "externally"):
            self.trial.apply()
        self.assertEqual(self.trial.override.read_bytes(), b"external edit")

    def test_effective_verification_failure_recoverable(self):
        self.force_network = True
        with self.assertRaisesRegex(RuntimeError, "still includes"):
            self.trial.apply()
        self.assertEqual(self.trial.metadata()["status"], "applying")
        with self.assertRaisesRegex(RuntimeError, "Interrupted"):
            self.trial.apply()
        self.assertEqual(self.trial.rollback()["status"], "rolled-back")

    def test_interrupted_before_override_write_recovers(self):
        with patch.object(m, "atomic_bytes", side_effect=[None, OSError("interrupted")]):
            with self.assertRaisesRegex(OSError, "interrupted"):
                self.trial.apply()
        self.assertEqual(self.trial.metadata()["status"], "applying")
        self.assertFalse(self.trial.override.exists())
        self.assertEqual(self.trial.rollback()["status"], "rolled-back")

    def test_reload_failure_recovers(self):
        self.fail_reload = True
        with self.assertRaisesRegex(RuntimeError, "reload failure"):
            self.trial.apply()
        self.assertEqual(self.trial.metadata()["status"], "applying")
        self.fail_reload = False
        self.assertEqual(self.trial.rollback()["status"], "rolled-back")

    def test_parser_only_unit_after_tokens_and_continuations(self):
        source = b"[Unit]\n# After=network.target\nAfter=remote-fs.target \\\n network.target home.mount\nBefore=network.target\n[Service]\nEnvironment=network.target\nAfter=network.target\n"
        expected = source.replace(b" network.target home.mount", b"  home.mount")
        self.assertEqual(m.without_network_after(source), expected)
        with self.assertRaises(RuntimeError):
            m.without_network_after(b"[Unit]\nAfter=other-network.target network.target.extra\n")

    def test_inspection_is_read_only(self):
        result = self.trial.inspect()
        self.assertFalse(result["network_wait_removed"])
        self.assertFalse(self.trial.directory.exists())
        self.assertNotIn(["systemctl", "daemon-reload"], self.calls)

    def test_proc_start_times_handles_parentheses(self):
        proc = self.trial.at("/proc/123")
        proc.mkdir(parents=True)
        (proc / "comm").write_text("livi-compositor\n")
        (proc / "stat").write_text("123 (livi (compositor)) " + " ".join(["S"] + ["0"] * 18 + ["321"] + ["0"] * 10))
        with patch.object(m.os, "sysconf", return_value=100):
            result = self.trial.process_start_times()
        self.assertEqual(result["processes"][0]["start_seconds_since_kernel"], 3.21)

    def test_config_fingerprint_ignores_format_and_order_without_values(self):
        config = self.trial.at("/home/livi/.config/LIVI/config.json")
        config.parent.mkdir(parents=True)
        secret = "private-test-password-never-in-snapshot"
        initial = {"wifiPassword": secret, "autoConn": True, "displayMode": {"main": "1600x600@60", "aux": None}}
        config.write_text(json.dumps(initial, indent=4))
        first = self.trial.config_fingerprints()
        config.write_text(json.dumps(dict(reversed(list(initial.items()))), separators=(",", ":")))
        self.assertEqual(first, self.trial.config_fingerprints())
        self.assertEqual(first[0]["fields"]["wifiPassword"], m.json_digest(secret))
        self.assertNotIn(secret, json.dumps(first))
        self.assertNotIn("wifiPassword", first[1]["fields"])
        initial["wifiPassword"] = "different-secret"
        config.write_text(json.dumps(initial))
        changed = self.trial.config_fingerprints()
        self.assertNotEqual(first[0]["sha256"], changed[0]["sha256"])
        self.assertEqual(first[1], changed[1])
        initial["autoConn"] = False
        config.write_text(json.dumps(initial))
        self.assertNotEqual(changed[1]["sha256"], self.trial.config_fingerprints()[1]["sha256"])

    def test_invalid_config_errors_do_not_disclose_values(self):
        config = self.trial.at("/home/livi/.config/LIVI/config.json")
        config.parent.mkdir(parents=True)
        config.write_text('{"private": "secret-value" broken}')
        self.assertEqual(self.trial.config_fingerprints(), ({"available": False}, {"available": False}))

    def test_snapshot_extension_audio_and_unit_state(self):
        self.trial.prepare()
        output = self.trial.directory / "fake.json"
        with patch.object(m.base.Trial, "snapshot", return_value={"path": str(output)}), patch.object(self.trial, "process_start_times", return_value={"processes": []}):
            result = self.trial.snapshot("baseline")
        self.assertTrue(result["audio_health"]["both_active"])
        self.assertFalse(result["network_wait_removed"])
        self.assertEqual(json.loads(output.read_text()), result)
        self.assertIn("After=", result["user_sessions_unit"]["effective_unit"])


if __name__ == "__main__":
    unittest.main()
