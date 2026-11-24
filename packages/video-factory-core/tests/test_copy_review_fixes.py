"""Synthetic gates for the three follow-up review findings."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from test_copy_update_recovery import Client, Store
from video_factory.script_week_io import CopyOnlyWeekWriter
from video_factory.script_week_projection import WeekProjection
from video_factory.runtime_store import RuntimeFault, RuntimeStore, fingerprint


class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name); self.client = Client()
        self.writer = CopyOnlyWeekWriter(self.client, source_base='basSource', copy_base='basCopy',
            table='tblCopy', machine_fields=['Sample','Output','Failure'], human_fields=['Review'], identity_field='Sample')
        self.projection = WeekProjection(self.store,'fixture',self.writer,lambda _: [self.client.remote])
    def fields(self, value):return {'Sample':'sample','Output':value,'Failure':''}
    def test_first_append_noop_and_delta_counts_are_actual(self):
        first=self.projection.publish('sample',self.fields('old'))
        self.assertEqual((first['writes'],first['write_attempts'],first['unknown_writes']),(1,1,0))
        same=self.projection.publish('sample',self.fields('old'))
        self.assertEqual((same['writes'],same['write_attempts']),(0,0))
        changed=self.projection.publish('sample',self.fields('new'))
        self.assertEqual((changed['writes'],changed['write_attempts']),(1,1))
        self.assertEqual(self.client.appends+len(self.client.updates),2)
    def test_unknown_update_separates_attempt_from_confirmed_write(self):
        self.projection.publish('sample',self.fields('old'))
        self.client.fail='BASE_RESULTS_REQUEST_UNKNOWN_FEISHU_CODE_1254002'
        with self.assertRaises(RuntimeFault) as caught:self.projection.publish('sample',self.fields('new'))
        self.assertEqual(caught.exception.copy_receipt,{'writes':0,'write_attempts':1,'unknown_writes':1})
        with self.assertRaises(RuntimeFault) as reconciled:self.projection.publish('sample',self.fields('third'))
        self.assertEqual(reconciled.exception.copy_receipt,{'writes':0,'write_attempts':0,'unknown_writes':0})
        self.assertTrue(self.store.row()['last_failure']['unresolved_prior_write'])
        self.assertEqual(len(self.client.updates),1)
    def test_successful_update_then_read_failure_uses_current_fields_and_attempt(self):
        self.projection.publish('sample',self.fields('old'))
        self.projection.publish('sample',self.fields('new'))
        previous=self.store.row()['update_ticket'];self.client.read_fail='FEISHU_READ_TIMEOUT'
        with self.assertRaises(RuntimeFault):self.projection.publish('sample',self.fields('third'))
        f=self.store.row()['last_failure']
        self.assertEqual(f['fields_sha256'],fingerprint(self.fields('third')))
        self.assertNotEqual(f['operation_id'],previous)
        self.assertEqual(f['operation_id'],f['attempt_id'])
        self.assertEqual(f['write_counts']['write_attempts'],0)
    def test_first_update_preflight_failure_does_not_reuse_append_ticket(self):
        self.projection.publish('sample',self.fields('old'));ticket=self.store.row()['ticket']
        self.client.read_fail='FEISHU_READ_TIMEOUT'
        with self.assertRaises(RuntimeFault):self.projection.publish('sample',self.fields('new'))
        f=self.store.row()['last_failure']
        self.assertNotEqual(f['operation_id'],ticket)
        self.assertEqual(f['fields_sha256'],fingerprint(self.fields('new')))
    def test_pending_write_receipt_keeps_original_ticket_and_frozen_fields(self):
        self.projection.publish('sample',self.fields('old'))
        self.client.fail='BASE_RESULTS_REQUEST_UNKNOWN_FEISHU_CODE_1254002'
        with self.assertRaises(RuntimeFault):self.projection.publish('sample',self.fields('new'))
        original=self.store.row()['first_failure']
        with self.assertRaises(RuntimeFault):self.projection.publish('sample',self.fields('third'))
        latest=self.store.row()['last_failure']
        self.assertEqual(latest['operation_id'],original['operation_id'])
        self.assertNotEqual(latest['attempt_id'],original['attempt_id'])
        self.assertEqual(latest['fields_sha256'],fingerprint(self.fields('new')))
        self.assertEqual(self.store.row()['pending_fields'],self.fields('new'))
    def test_append_unknown_counts_and_preflight_denial(self):
        def fail(*args):raise RuntimeFault('BASE_RESULTS_REQUEST_UNKNOWN_FEISHU_CODE_1254002')
        self.client.append=fail
        with self.assertRaises(RuntimeFault) as caught:self.projection.publish('sample',self.fields('new'))
        self.assertEqual(caught.exception.copy_receipt,{'writes':0,'write_attempts':1,'unknown_writes':1})
        callback=[]
        with self.assertRaises(RuntimeFault):self.writer.append({'Review':'forbidden'},'ticket',before_write=lambda:callback.append(1))
        self.assertEqual(callback,[])
    def test_failure_context_drops_untrusted_body_and_invalid_types(self):
        from video_factory.feishu_diagnostics import failure_context
        e=RuntimeFault('private body token')
        e.request_context={'method':'DELETE','operation':'secret','http_status':True,'feishu_code':'1254002',
            'request_id':'private\nbody','target_sha256':'private','token':'secret'}
        self.assertEqual(failure_context(e),{'error':'COPY_OPERATION_FAILED'})
    def test_process_exit_after_committed_intent_never_replays(self):
        # Real RuntimeStore transaction and a real child exit after intent commit.
        root=Path(self.tmp.name)/'crash';root.mkdir(mode=0o700)
        db=root/'runtime.sqlite3'
        with sqlite3.connect(db) as d:
            d.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
            d.execute("INSERT INTO meta VALUES('schema','1')")
        db.chmod(0o600)
        script="""import os,sys
from test_copy_update_recovery import Client
from video_factory.runtime_store import RuntimeStore
from video_factory.script_week_io import CopyOnlyWeekWriter
from video_factory.script_week_projection import WeekProjection
c=Client();w=CopyOnlyWeekWriter(c,source_base='basSource',copy_base='basCopy',table='tblCopy',machine_fields=['Sample','Output','Failure'],human_fields=['Review'],identity_field='Sample');p=WeekProjection(RuntimeStore(sys.argv[1]),'fixture',w,lambda _: [c.remote])
p.publish('sample',{'Sample':'sample','Output':'old','Failure':''})
def crash(*a):os._exit(73)
c.update_record=crash
p.publish('sample',{'Sample':'sample','Output':'new','Failure':''})
"""
        done=subprocess.run([sys.executable,'-c',script,str(root)],capture_output=True,timeout=15)
        self.assertEqual(done.returncode,73,done.stderr.decode())
        self.client.remote['fields']['Output']='old'
        restarted=WeekProjection(RuntimeStore(root),'fixture',self.writer,lambda _: [])
        with self.assertRaisesRegex(RuntimeFault,'READBACK_MISMATCH'):restarted.publish('sample',self.fields('third'))
        self.assertEqual(self.client.updates,[]);self.assertEqual(self.client.appends,0)
        self.client.remote['fields']['Output']='new'
        reconciled=restarted.publish('sample',self.fields('new'))
        self.assertEqual((reconciled['writes'],reconciled['write_attempts']),(0,0))
        self.assertEqual(self.client.updates,[])

if __name__=='__main__':unittest.main()
