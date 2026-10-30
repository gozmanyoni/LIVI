import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('pi_logs', Path(__file__).with_name('collect-pi-logs.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RestorationTests(unittest.TestCase):
    def run_case(self, join_error=None, collect_error=None, no_switch=False, restore_ok=True):
        with tempfile.TemporaryDirectory() as temp:
            report = Path(temp)
            with patch.object(module, 'join_network', side_effect=join_error) as join, \
                 patch.object(module, 'wait_for_ssh'), \
                 patch.object(module, 'collect_ssh', side_effect=collect_error) as collect, \
                 patch.object(module, 'restore_network', return_value=restore_ok) as restore:
                result = module.run_session('en0', 'Original', 'not-logged', '192.168.86.1',
                                            {'password': 'secret', 'wifi_password': 'pi-secret'},
                                            Path('hostkey'), report, no_switch)
                summary = (report / 'summary.json').read_text()
                self.assertNotIn('secret', summary)
                self.assertNotIn('not-logged', summary)
                return result, join.call_count, collect.call_count, restore.call_count

    def test_success_restores_original_network(self):
        result, joins, collects, restores = self.run_case()
        self.assertTrue(result['logs_collected'])
        self.assertTrue(result['wifi_restored'])
        self.assertEqual((joins, collects, restores), (1, 1, 1))

    def test_failed_association_still_restores(self):
        result, _, collects, restores = self.run_case(join_error=RuntimeError('Join failed'))
        self.assertFalse(result['logs_collected'])
        self.assertTrue(result['wifi_restored'])
        self.assertEqual((collects, restores), (0, 1))

    def test_ssh_failure_restores(self):
        result, _, _, restores = self.run_case(collect_error=RuntimeError('SSH failed'))
        self.assertIn('SSH failed', result['error'])
        self.assertTrue(result['wifi_restored'])
        self.assertEqual(restores, 1)

    def test_ctrl_c_restores(self):
        result, _, _, restores = self.run_case(collect_error=KeyboardInterrupt())
        self.assertTrue(result['wifi_restored'])
        self.assertIn('Interrupted', result['error'])
        self.assertEqual(restores, 1)

    def test_failed_restoration_is_reported(self):
        result, _, _, _ = self.run_case(restore_ok=False)
        self.assertFalse(result['wifi_restored'])

    def test_manual_network_mode_never_changes_wifi(self):
        result, joins, collects, restores = self.run_case(no_switch=True)
        self.assertTrue(result['logs_collected'])
        self.assertEqual((joins, collects, restores), (0, 1, 0))


if __name__ == '__main__':
    unittest.main()
