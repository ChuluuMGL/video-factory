import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    'audit_policy', Path(__file__).resolve().parents[1] / 'scripts/audit_policy.py')
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class AuditPolicyTests(unittest.TestCase):
    def test_unreviewed_finding_blocks_an_otherwise_completed_scan(self):
        self.assertEqual(policy.verdict({'findings': [{'classification': 'requires-review'}]}),
                         ('review_required', 1, 3))

    def test_missing_or_new_classification_fails_closed(self):
        self.assertEqual(policy.verdict({'findings': [{}, {'classification': 'unknown'}]}),
                         ('review_required', 2, 3))

    def test_only_recognized_fixture_findings_are_non_blocking(self):
        self.assertEqual(policy.verdict({'findings': [
            {'classification': 'explicit-synthetic-fixture'},
            {'classification': 'runtime-secret-file-reference'},
            {'classification': 'reviewed-test-command'}]}),
                         ('inspection_completed', 0, 0))

    def test_scanner_failure_cannot_pass_even_without_findings(self):
        self.assertEqual(policy.verdict({'errors': [{'reason': 'SCANNER_FAILED'}]}),
                         ('inspection_incomplete', 0, 2))

    def test_clean_scan_passes(self):
        self.assertEqual(policy.verdict({}), ('inspection_completed', 0, 0))

    def test_reviewed_fixture_command_exception_is_exact_and_file_scoped(self):
        name = '.github/scripts/container-stack-smoke.py'
        source = Path(__file__).resolve().parents[2] / name
        line = source.read_text().splitlines()[42]
        self.assertTrue(policy.reviewed_fixture_command('generic-api-key', name, line))
        self.assertFalse(policy.reviewed_fixture_command('generic-api-key', 'other.py', line))
        self.assertFalse(policy.reviewed_fixture_command('different-rule', name, line))

    def test_changing_the_fixture_value_removes_the_exception(self):
        name = '.github/scripts/container-stack-smoke.py'
        source = Path(__file__).resolve().parents[2] / name
        line = source.read_text().splitlines()[42]
        changed = line.replace('--id=vfFixtureHeader', '--id=another-fixture')
        self.assertNotEqual(line, changed)
        self.assertFalse(policy.reviewed_fixture_command('generic-api-key', name, changed))
