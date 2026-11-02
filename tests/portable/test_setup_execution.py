"""Unknown setup executions must survive restarts without replay; cloud only."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'skills/video-factory-setup/scripts'))
from portable_n8n import Fault, locked, private_write, verify_setup
from portable_session import WelcomeSession, inspect_session
from n8n_workflows import probe_workflow


class SetupExecutionTests(unittest.TestCase):
    def run_check(self, root, response=None, error=None, cleanup=None):
        api = Mock(url='http://127.0.0.1:5678')
        api.request.return_value = {'active': False} if cleanup is None else cleanup

        def provision(api, directory, project, template, credentials):
            private_write(Path(directory) / ('binding-' + project + '.json'),
                          {'origin': api.url, 'workflow': 'flowFixture', 'verified_disabled': True})
            return {'workflow': 'flowFixture'}

        opener = Mock()
        if error is not None:
            opener.open.side_effect = error
        else:
            opener.open.return_value.__enter__ = Mock(return_value=Mock(read=Mock(
                return_value=json.dumps(response or {'base_read': 'verified'}).encode())))
            opener.open.return_value.__exit__ = Mock(return_value=False)
        with patch('portable_n8n.provision', side_effect=provision) as created, \
             patch('portable_n8n.build_opener', return_value=opener), \
             patch('portable_n8n.time.sleep'):
            try:
                result = verify_setup(api, root, 'fixture-probe', probe_workflow('baseFixture', 'tableFixture'), {})
            except Fault:
                self.last_api, self.last_created, self.last_opener = api, created, opener
                raise
        self.last_api, self.last_created, self.last_opener = api, created, opener
        return result

    def receipts(self, root):
        return [json.loads(p.read_text()) for p in root.glob('binding-fixture-probe-*.json')]

    def test_timeout_is_durable_and_blocks_second_execution_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(Fault, 'INCOMPLETE'):
                self.run_check(root, error=TimeoutError('PRIVATE_RESPONSE'))
            state = self.receipts(root)[0]
            self.assertEqual(state['setup_execution']['phase'], 'execution_pending')
            self.assertTrue(state['setup_execution']['hook_disabled'])
            self.assertTrue(state['verified_disabled'])
            self.assertNotIn('PRIVATE_RESPONSE', json.dumps(state))
            with self.assertRaisesRegex(Fault, 'EXECUTION_UNKNOWN'):
                self.run_check(root)
            self.last_created.assert_not_called()
            self.last_api.request.assert_not_called()
            self.last_opener.open.assert_not_called()
            self.assertEqual(len(self.receipts(root)), 1)

    def test_success_requires_receipt_and_explicit_inactive_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.run_check(root)
            self.assertEqual(self.receipts(root)[0]['setup_execution'],
                             {'phase': 'complete', 'receipt_verified': True, 'hook_disabled': True})
            self.run_check(root)
            self.assertEqual(len(self.receipts(root)), 2)

    def test_missing_or_active_cleanup_blocks_even_with_successful_response(self):
        for cleanup in ({}, {'active': True}):
            with self.subTest(cleanup=cleanup), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with self.assertRaisesRegex(Fault, 'HOOK_STILL_ACTIVE'):
                    self.run_check(root, cleanup=cleanup)
                state = self.receipts(root)[0]
                self.assertEqual(state['setup_execution']['phase'], 'receipt_verified')
                self.assertFalse(state['verified_disabled'])
                with self.assertRaisesRegex(Fault, 'EXECUTION_UNKNOWN'):
                    self.run_check(root)

    def test_invalid_receipt_is_not_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(Fault, 'RECEIPT_INVALID'):
                self.run_check(root, response={'private_error': 'DO_NOT_STORE'})
            state = self.receipts(root)[0]
            self.assertFalse(state['setup_execution']['receipt_verified'])
            self.assertNotIn('DO_NOT_STORE', json.dumps(state))

    def test_definite_404_exhaustion_is_not_an_automatic_new_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(Fault, 'INCOMPLETE'):
                self.run_check(root, error=HTTPError('http://localhost', 404, 'not registered', {}, None))
            self.assertEqual(self.last_opener.open.call_count, 10)
            with self.assertRaisesRegex(Fault, 'EXECUTION_UNKNOWN'):
                self.run_check(root)

    def test_activation_timeout_preserves_intent_and_attempts_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'binding-fixture-probe-11223344.json'
            private_write(path, {'origin': 'http://127.0.0.1:5678', 'workflow': 'flowFixture'})
            api = Mock(url='http://127.0.0.1:5678')
            api.request.side_effect = [Fault('ACTIVATION_UNKNOWN'), {}, {'active': False}]
            with patch('portable_n8n.secrets.token_hex', return_value='11223344'), \
                 patch('portable_n8n.provision', return_value={'workflow': 'flowFixture'}), \
                 patch('portable_n8n.build_opener') as opener:
                with self.assertRaisesRegex(Fault, 'ACTIVATION_UNKNOWN'):
                    verify_setup(api, root, 'fixture-probe', probe_workflow('b', 't'), {})
                opener.assert_not_called()
            state = json.loads(path.read_text())
            self.assertEqual(state['setup_execution']['phase'], 'activation_pending')
            self.assertTrue(state['setup_execution']['hook_disabled'])

    def test_handoff_exposes_unresolved_execution_even_after_hook_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = WelcomeSession(root)
            session.remember('project', 'fixture')
            session.remember('origin', 'http://127.0.0.1:5678')
            with self.assertRaises(Fault):
                self.run_check(root / 'bindings', error=TimeoutError())
            result = inspect_session(root)
            self.assertEqual(result['next'], 'reconcile_unknown_resources')
            self.assertEqual(len(result['pending_resource_receipts']), 1)
            self.assertEqual(result['network_requests'], 0)

    def test_simultaneous_check_is_rejected_before_resource_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with locked(root / '.setup-check'):
                with self.assertRaisesRegex(Fault, 'SETUP_LOCKED'):
                    self.run_check(root)
                self.last_created.assert_not_called()


if __name__ == '__main__':
    unittest.main()
