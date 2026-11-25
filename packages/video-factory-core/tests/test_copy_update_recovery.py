"""Synthetic regression for empty-text PUT storms and ambiguous write recovery."""
import copy
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from urllib.error import HTTPError
from unittest.mock import MagicMock, patch
from video_factory.runtime_store import RuntimeFault
from video_factory.script_week_io import CopyOnlyWeekWriter
from video_factory.script_week_projection import WeekProjection
from video_factory.feishu_results_client import request


class Store:
    def __init__(self, root):
        self.path = root + '/projection.sqlite3'
        with self.connect() as db: db.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
    @contextmanager
    def connect(self):
        with sqlite3.connect(self.path) as db: yield db
    def row(self):
        with self.connect() as db: return json.loads(db.execute('SELECT value FROM meta').fetchone()[0])


class Client:
    def __init__(self):
        self.remote = {'record_id': 'recFixture', 'fields': {'Sample': 'sample', 'Review': 'human'}}
        self.appends = 0; self.updates = []; self.fail = None; self.commit = False; self.read_fail = None
    def record(self, *args):
        if self.read_fail: raise RuntimeFault(self.read_fail)
        return copy.deepcopy(self.remote)
    def append(self, base, table, fields, ticket):
        self.appends += 1
        self.remote['fields'].update({k: v for k, v in fields.items() if v != ''})
        return 'recFixture'
    def update_record(self, base, table, record, fields):
        self.updates.append(copy.deepcopy(fields))
        if self.commit or not self.fail:
            self.remote['fields'].update(fields)
            self.remote['fields'] = {k: v for k, v in self.remote['fields'].items() if v != ''}
        if self.fail: raise RuntimeFault(self.fail)


class CopyUpdateRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name); self.client = Client()
        self.writer = CopyOnlyWeekWriter(self.client, source_base='basSource', copy_base='basCopy',
            table='tblCopy', machine_fields=['Sample','Output','Failure','Cost'], human_fields=['Review'], identity_field='Sample')
        self.projection = WeekProjection(self.store, 'fixture', self.writer, lambda _: [self.client.remote] if self.client.appends else [])
    def test_100_ticks_with_omitted_empty_fields_append_once_put_zero(self):
        for i in range(100):
            self.assertEqual(self.projection.publish('sample', {'Sample':'sample','Output':'draft','Failure':''})['writes'], 1 if i == 0 else 0)
        self.assertEqual(self.client.appends, 1); self.assertEqual(self.client.updates, [])
        self.assertEqual(self.client.remote['fields']['Review'], 'human')
    def test_empty_and_rich_text_representations_share_readback_equivalence(self):
        for empty in (None, '', [], [{'type':'text','text':''}]):
            self.client.remote['fields'].update(Output=[{'type':'text','text':'draft'}], Failure=empty)
            self.assertEqual(self.writer.update('recFixture','sample',{'Output':'draft','Failure':''})['writes'],0)
        self.assertEqual(self.client.updates, [])
    def test_real_change_puts_only_delta_and_does_not_normalize_away_spaces(self):
        self.client.remote['fields']['Output'] = 'draft'
        result=self.writer.update('recFixture','sample',{'Output':'draft ','Failure':'','Sample':'sample'})
        self.assertEqual(result['writes'],1);self.assertEqual(self.client.updates,[{'Output':'draft '}])
        self.assertEqual(self.writer.update('recFixture','sample',{'Output':'draft ','Failure':''})['writes'],0)
    def test_clearing_nonempty_field_sends_null_and_preserves_other_values(self):
        self.client.remote['fields'].update(Failure='previous', Cost=5)
        desired = {'Failure': '', 'Cost': 0, 'Output': ' '}
        self.assertEqual(self.writer.update('recFixture', 'sample', desired)['writes'], 1)
        self.assertEqual(self.writer.update('recFixture', 'sample', desired)['writes'], 0)
        self.assertEqual(self.client.updates, [{'Failure': None, 'Cost': 0, 'Output': ' '}])
        self.assertEqual(desired, {'Failure': '', 'Cost': 0, 'Output': ' '})

    def test_uncertain_clear_preserves_original_pending_empty_text(self):
        self.projection.publish('sample', {'Sample': 'sample', 'Failure': 'previous'})
        self.client.fail = 'BASE_RESULTS_REQUEST_UNKNOWN_FEISHU_CODE_1254002'
        desired = {'Sample': 'sample', 'Failure': ''}
        with self.assertRaises(RuntimeFault):
            self.projection.publish('sample', desired)
        self.assertEqual(self.store.row()['pending_fields'], desired)
        self.assertEqual(self.client.updates, [{'Failure': None}])
    def test_invalid_text_is_not_treated_as_empty_or_overwritten(self):
        for v in ({}, 0, False, [{'type':'mention','text':''}]):
            self.client.remote['fields']['Failure']=v
            with self.assertRaises(RuntimeFault):self.writer.update('recFixture','sample',{'Failure':''})
        self.assertEqual(self.client.updates,[])
    def test_numeric_zero_is_not_missing_or_boolean(self):
        self.assertFalse(self.writer._same_value(None,0));self.assertFalse(self.writer._same_value(False,0))
        self.assertTrue(self.writer._same_value(0,0));self.assertFalse(self.writer._same_value('0',0))
    def prepare_update(self):
        self.projection.publish('sample',{'Sample':'sample','Output':'old','Failure':''})
        self.client.fail='BASE_RESULTS_REQUEST_UNKNOWN_FEISHU_CODE_1254002'
    def test_1254002_not_committed_stays_pending_and_never_replays(self):
        self.prepare_update()
        with self.assertRaises(RuntimeFault):self.projection.publish('sample',{'Sample':'sample','Output':'new','Failure':''})
        self.assertEqual(self.store.row()['state'],'update_intent')
        first=self.store.row()['first_failure']
        self.assertEqual(first['stage'],'update_request')
        for wanted in ('new','different'):
            restarted=WeekProjection(self.store,'fixture',self.writer,lambda _: [])
            with self.assertRaisesRegex(RuntimeFault,'READBACK_MISMATCH'):
                restarted.publish('sample',{'Sample':'sample','Output':wanted,'Failure':''})
        self.assertEqual(len(self.client.updates),1);self.assertEqual(self.store.row()['first_failure'],first)
        self.assertEqual(self.store.row()['last_failure']['phase'],'update_reconciliation')
    def test_1254002_after_commit_read_reconciles_without_resend(self):
        self.prepare_update();self.client.commit=True
        fields={'Sample':'sample','Output':'new','Failure':''}
        with self.assertRaises(RuntimeFault):self.projection.publish('sample',fields)
        self.assertEqual(self.projection.publish('sample',fields)['writes'],0)
        self.assertEqual(len(self.client.updates),1);self.assertEqual(self.store.row()['state'],'created')
    def test_preflight_read_failure_does_not_record_write_intent(self):
        self.projection.publish('sample',{'Sample':'sample','Output':'old'})
        self.client.read_fail='FEISHU_READ_TIMEOUT'
        with self.assertRaises(RuntimeFault):self.projection.publish('sample',{'Sample':'sample','Output':'new'})
        self.assertEqual(self.store.row()['state'],'created');self.assertEqual(self.client.updates,[])
        self.assertEqual(self.store.row()['first_failure']['stage'],'update_preflight')
    def test_write_readback_failure_retains_intent_until_get_succeeds(self):
        self.projection.publish('sample',{'Sample':'sample','Output':'old'})
        original=self.client.update_record
        def put(*a):
            original(*a);self.client.read_fail='FEISHU_READ_TIMEOUT'
        self.client.update_record=put;fields={'Sample':'sample','Output':'new'}
        with self.assertRaisesRegex(RuntimeFault,'WRITE_READBACK_UNKNOWN'):self.projection.publish('sample',fields)
        self.assertEqual(self.store.row()['state'],'update_intent')
        self.client.read_fail=None
        self.assertEqual(self.projection.publish('sample',fields)['writes'],0)
        self.assertEqual(len(self.client.updates),1)
    def test_legacy_created_row_still_gets_noop_fix(self):
        self.projection.publish('sample',{'Sample':'sample','Output':'draft'})
        self.assertEqual(self.projection.publish('sample',{'Sample':'sample','Output':'draft','Failure':''})['writes'],0)
        self.assertEqual(self.client.updates,[])
    def test_operation_receipt_survives_projection_restart_without_body(self):
        self.projection.publish('sample',{'Sample':'sample','Output':'old'})
        def put(*_):
            return request('/bitable/v1/apps/basFixture/tables/tblCopy/records/recFixture',
                           b'private task text', 'private-token', method='PUT')
        self.client.update_record=put
        with patch('video_factory.feishu_results_client.build_opener') as opener:
            r=MagicMock();r.__enter__.return_value=r;r.status=200
            r.headers={'X-Tt-Logid':'LOGID_fixture_456'}
            r.read.return_value=b'{"code":1254002,"msg":"private upstream text"}'
            opener.return_value.open.return_value=r
            with self.assertRaises(RuntimeFault):self.projection.publish('sample',{'Sample':'sample','Output':'new'})
        receipt=self.store.row()['first_failure']
        self.assertEqual(receipt['operation'],'record_update')
        self.assertEqual(receipt['request_id'],'LOGID_fixture_456')
        self.assertEqual(receipt['feishu_code'],1254002)
        self.assertNotIn('private',json.dumps(receipt))
        self.assertNotIn('recFixture',json.dumps(receipt))

    def test_production_and_human_fields_remain_denied(self):
        with self.assertRaises(RuntimeFault): self.writer.update('recFixture','sample',{'Review':'overwrite'})
        with self.assertRaises(RuntimeFault):
            CopyOnlyWeekWriter(self.client,source_base='basSource',copy_base='basSource',table='tblCopy',machine_fields=['Sample'],human_fields=[],identity_field='Sample')
        self.assertEqual(self.client.updates,[])


class RequestDiagnosticsTests(unittest.TestCase):
    def test_200_error_and_500_error_keep_operation_code_logid_without_secrets(self):
        path='/bitable/v1/apps/basPrivate/tables/tblPrivate/records/recPrivate?secret=query'
        for http in (200,500):
            with patch('video_factory.feishu_results_client.build_opener') as opener:
                raw=json.dumps({'code':1254002,'msg':'secret customer body'}).encode()
                if http==200:
                    r=MagicMock();r.__enter__.return_value=r;r.status=http;r.headers={'X-Tt-Logid':'LOGID_fixture_123'};r.read.return_value=raw
                    opener.return_value.open.return_value=r
                else:
                    opener.return_value.open.side_effect=HTTPError('https://private',http,'secret',{'X-Tt-Logid':'LOGID_fixture_123'},io.BytesIO(raw))
                with self.assertRaises(RuntimeFault) as caught:request(path,b'secret body','secret-token',method='PUT')
                d=caught.exception.request_context
                self.assertEqual(d['operation'],'record_update');self.assertEqual(d['method'],'PUT')
                self.assertEqual(d['http_status'],http);self.assertEqual(d['feishu_code'],1254002)
                self.assertEqual(d['request_id'],'LOGID_fixture_123')
                self.assertNotIn('secret',json.dumps(d));self.assertNotIn('Private',json.dumps(d))
                self.assertEqual(opener.return_value.open.call_count,1)
    def test_auth_search_append_are_distinguishable_and_bad_logid_discarded(self):
        for path,operation in [('/auth/v3/tenant_access_token/internal','application_auth'),('/bitable/v1/apps/basFixture/tables/tblFixture/records/search','record_search'),('/bitable/v1/apps/basFixture/tables/tblFixture/records/batch_create','record_append')]:
            with patch('video_factory.feishu_results_client.build_opener') as opener:
                r=MagicMock();r.__enter__.return_value=r;r.status=200;r.headers={'X-Tt-Logid':'invalid\nprivate'};r.read.return_value=b'{"code":1254002}'
                opener.return_value.open.return_value=r
                with self.assertRaises(RuntimeFault) as caught:request(path,b'{}')
                self.assertEqual(caught.exception.request_context['operation'],operation)
                self.assertNotIn('request_id',caught.exception.request_context)

if __name__=='__main__':unittest.main()
