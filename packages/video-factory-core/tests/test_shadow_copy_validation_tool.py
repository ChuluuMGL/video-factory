import copy
import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_factory.feishu_client import FeishuClient
from video_factory.feishu_results_client import ResultClient
from video_factory.runtime_store import RuntimeFault

SPEC = spec_from_file_location(
    'shadow_validation', Path(__file__).resolve().parents[1] / 'tools/validate_shadow_copy_write.py'
)
tool = module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


class ValidationToolTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.ledger = tool.Ledger(Path(folder.name) / 'fixture.sqlite3')
        self.client = tool.BoundedClient('fixture-token', 'basCopy', 'tblCopy', 'ID', 'Output')
        self.rows = {'recExisting': {'ID': tool.EXISTING, 'Output': 'A', 'Review': tool.SENTINEL}}
        self.events = []

        def get(_, path):
            route = path.split('?', 1)[0]
            if route == self.client.schema_path:
                return {'has_more': False, 'items': [
                    {'field_name': name, 'type': 1} for name in ('ID', 'Output', 'Review')
                ]}
            if route == self.client.path:
                return {'has_more': False, 'items': [self.row(rid) for rid in self.rows]}
            return {'record': self.row(route.rsplit('/', 1)[1])}

        def post(_, path, body):
            self.rows['recNewFixture'] = body['records'][0]['fields']
            if self.fail_append:
                raise RuntimeFault('BASE_RESULTS_REQUEST_UNKNOWN_FEISHU_CODE_1254002')
            return {'records': [{'record_id': 'recNewFixture'}]}

        def put(_, path, body):
            rid = path.split('?', 1)[0].rsplit('/', 1)[1]
            self.rows[rid].update(body['fields'])
            if self.bad_readback:
                self.rows[rid]['Output'] = 'unexpected'
            if self.rows[rid]['Output'] is None:
                del self.rows[rid]['Output']
            return {'record': {'record_id': rid}}

        self.fail_append = False
        self.bad_readback = False
        self.enterContext(patch.object(FeishuClient, '_get_once', get))
        self.enterContext(patch.object(ResultClient, 'post', post))
        self.enterContext(patch.object(ResultClient, 'put', put))

    def row(self, rid):
        return {'record_id': rid, 'fields': copy.deepcopy(self.rows[rid])}

    def validate(self):
        return tool.validate(self.client, self.ledger, 'basCopy', 'tblCopy', 'basProd',
                             'ID', 'Output', 'Review', self.events.append)

    def test_success_and_exhausted_write_budget(self):
        run = subprocess.run

        def fixture_child(argv, **kwargs):
            # Inject the fake transport in the new interpreter itself.
            fixture = '''
import runpy, sys
from unittest.mock import patch
from video_factory.feishu_client import FeishuClient
path = sys.argv[1]
sys.argv = [path, '--restart-child']
def get(self, path):
    return {'record': {'record_id': 'recExisting', 'fields': {
        'ID': 'HD01-1254002-TEST-UPDATE', 'Output': [{'type': 'text', 'text': ''}]}}}
with patch.object(FeishuClient, '_get_once', get):
    runpy.run_path(path, run_name='__main__')
'''
            return run([sys.executable, '-c', fixture, argv[1]], **kwargs)

        with patch.object(tool.subprocess, 'run', fixture_child):
            report = self.validate()
        self.assertEqual(report['state'], 'passed')
        self.assertEqual(self.client.counts['POST'], 1)
        self.assertEqual(self.client.counts['PUT'], 2)
        self.assertNotIn('Output', self.rows['recExisting'])
        self.assertEqual(self.rows['recExisting']['Review'], tool.SENTINEL)
        self.assertEqual(sum(event['writes'] for event in self.events), 3)
        for method in ('POST', 'PUT'):
            with self.assertRaisesRegex(RuntimeFault, 'REQUEST_LIMIT'):
                self.client.reserve(method)

    def test_unknown_append_stops_without_update(self):
        self.fail_append = True
        with self.assertRaises(RuntimeFault) as caught:
            self.validate()
        self.assertEqual(caught.exception.copy_receipt['unknown_writes'], 1)
        self.assertEqual(self.client.counts['POST'], 1)
        self.assertEqual(self.client.counts['PUT'], 0)

    def test_failed_readback_stops_before_clear(self):
        self.bad_readback = True
        with self.assertRaisesRegex(RuntimeFault, 'READBACK_MISMATCH'):
            self.validate()
        self.assertEqual(self.client.counts['PUT'], 1)

    def test_wire_allowlist_rejects_human_column_without_spending_budget(self):
        for method, path, body in [
            (self.client.post, self.client.path + '/batch_create',
             {'records': [{'fields': {'ID': tool.NEW, 'Review': 'forbidden'}}]}),
            (self.client.put, self.client.path + '/recExisting',
             {'fields': {'Output': 'B', 'Review': 'forbidden'}}),
        ]:
            with self.assertRaisesRegex(RuntimeFault, 'VALIDATION_HUMAN_FIELD_DENIED'):
                method(path, body)
        self.assertEqual(self.client.counts, {'GET': 0, 'POST': 0, 'PUT': 0})

    def test_stopped_output_retains_only_safe_transport_diagnostics(self):
        error = RuntimeFault('upstream secret https://example.invalid')
        error.request_context = {'method': 'PUT', 'http_status': 200, 'feishu_code': 1254002,
                                 'request_id': 'abc123', 'body': 'private'}
        error.copy_stage = 'update_request'
        expected = {'state': 'stopped', 'error': 'COPY_OPERATION_FAILED', 'stage': 'update_request',
                    'method': 'PUT', 'http_status': 200, 'feishu_code': 1254002, 'request_id': 'abc123'}
        self.assertEqual(tool.stopped(error, 'update'), expected)
        self.assertEqual(tool.stopped(RuntimeFault('VALIDATION_REQUEST_LIMIT'), 'clear')['error'],
                         'VALIDATION_REQUEST_LIMIT')

    def test_crash_and_timeout_keep_journaled_get_count(self):
        run = subprocess.run
        for body, code in [('os.kill(os.getpid(), signal.SIGKILL)', 'CRASHED'),
                           ('time.sleep(10)', 'TIMEOUT')]:
            script = (
                'import json, os, signal, sys, time\n'
                'path = json.load(sys.stdin)[-2]\n'
                'with open(path, "a") as f:\n'
                '    f.write("GET\\n"); f.flush(); os.fsync(f.fileno())\n'
                + body
            )

            def fixture_child(argv, **kwargs):
                return run([sys.executable, '-c', script], **kwargs)

            self.client.counts['GET'] = 0
            with patch.object(tool.subprocess, 'run', fixture_child), \
                 patch.object(tool, 'RESTART_LIMIT_SECONDS', 1):
                with self.assertRaisesRegex(RuntimeFault, 'VALIDATION_RESTART_' + code):
                    tool.verify_restart(self.client, self.ledger, 'basProd', 'Review')
            self.assertEqual(self.client.counts, {'GET': 1, 'POST': 0, 'PUT': 0})
