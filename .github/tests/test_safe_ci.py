"""Check failure classification without publishing diagnostic bodies."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/safe-ci.py'


class SafeDiagnostics(unittest.TestCase):
    def run_stage(self, diagnostic, exit_code):
        with tempfile.TemporaryDirectory() as temporary:
            summary = Path(temporary) / 'summary.md'
            command = "printf '%s\\n' " + shlex.quote(diagnostic) + " >&2\nexit " + str(exit_code)
            result = subprocess.run([sys.executable, str(SCRIPT), 'diagnostic-test'], input=command,
                                    text=True, capture_output=True,
                                    env={**os.environ, 'GITHUB_STEP_SUMMARY': str(summary)})
            self.assertEqual(result.returncode, exit_code)
            self.assertEqual(result.stderr, '')
            published = result.stdout + summary.read_text()
            self.assertNotIn('SYNTHETIC_PRIVATE_VALUE', published)
            self.assertNotIn('private.example.invalid', published)
            return json.loads(result.stdout)

    def test_qualified_and_unqualified_errors_keep_only_type(self):
        for spelling, expected in [
            ('playwright._impl._errors.TimeoutError', 'TimeoutError'),
            ('playwright._impl._errors.Error', 'Error'),
            ('video_factory.runtime_store.RuntimeFault', 'RuntimeFault'),
            ('AssertionError', 'AssertionError'), ('Exception', 'Exception')
        ]:
            with self.subTest(spelling=spelling):
                value = self.run_stage(spelling + ': https://private.example.invalid/?key=SYNTHETIC_PRIVATE_VALUE', 1)
                self.assertEqual(value['exception_types'], [expected])
                self.assertEqual(value['status'], 'FAIL')

    def test_duplicate_errors_deduplicate_and_unstructured_output_stays_private(self):
        diagnostic = '\n'.join(['Error: SYNTHETIC_PRIVATE_VALUE', 'vendor.Error: SYNTHETIC_PRIVATE_VALUE',
                                'request body: SYNTHETIC_PRIVATE_VALUE', 'Error without a colon'])
        self.assertEqual(self.run_stage(diagnostic, 2)['exception_types'], ['Error'])

    def test_success_retains_counts_without_diagnostic_text(self):
        value = self.run_stage('Ran 81 tests in 2.3s\nSYNTHETIC_PRIVATE_VALUE', 0)
        self.assertEqual(value['unit_test_counts'], [81])
        self.assertEqual(value['status'], 'PASS')
        self.assertNotIn('exception_types', value)


if __name__ == '__main__':
    unittest.main()
