"""The public CI summary must never echo raw command or credential text."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class CIDiagnosticPrivacy(unittest.TestCase):
    def test_qualified_fault_and_allowlisted_container_states(self):
        script = Path(__file__).resolve().parents[3] / '.github/scripts/summarize-ci-diagnostic.py'
        secret = 'synthetic-private-payload-never-export'
        raw = '\n'.join([
            'video_factory.runtime_store.RuntimeFault: BACKUP_REQUIRES_STOPPED_COMPONENTS',
            'subprocess.CalledProcessError: ' + secret,
            json.dumps({'stage': 'container_failure_components', 'components': {
                'n8n': {'state': 'running', 'exit_code': 0, 'command': secret},
                'postgres': {'state': secret, 'exit_code': secret},
                secret: {'state': 'running', 'exit_code': 0}}}),
            json.dumps({'stage': 'container_failure_components', 'components': []}),
            json.dumps({'stage': 'unrelated', 'secret': secret})])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'private.log'
            path.write_text(raw)
            result = subprocess.run([sys.executable, str(script), str(path)], check=True,
                                    capture_output=True, text=True)
        self.assertNotIn(secret, result.stdout)
        summary = json.loads(result.stdout)
        self.assertIn('BACKUP_REQUIRES_STOPPED_COMPONENTS', summary['codes'])
        self.assertIn('RuntimeFault', summary['exception_types'])
        self.assertIn('CalledProcessError', summary['exception_types'])
        self.assertEqual(summary['component_states'], [{'n8n': {'state': 'running', 'exit_code': 0},
                                                       'postgres': {'state': 'unknown', 'exit_code': None}}])
