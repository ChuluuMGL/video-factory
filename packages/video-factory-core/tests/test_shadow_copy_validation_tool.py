import copy
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
        self.client = tool.BoundedClient('fixture-token', 'basCopy', 'tblCopy')
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
            if self.rows[rid]['Output'] == '':
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
