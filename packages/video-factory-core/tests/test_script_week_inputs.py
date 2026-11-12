import copy
import time
import unittest
from unittest.mock import Mock,patch
from test_script_week import WeekTests
from video_factory.feishu_bridge import meta,save
from video_factory.runtime_store import fingerprint,RuntimeFault
from video_factory.script_week_inputs import WeekInputAdapter,REGISTRY
from video_factory.script_week import PREFIX

class InputTests(unittest.TestCase):
    setUp=WeekTests.setUp
    tearDown=WeekTests.tearDown
    prepare=WeekTests.prepare
    start=WeekTests.start
    def adapter(self):
        self.start();self.rows=[{'record_id':'recOld','fields':{'brief':'old'}}]
        self.compiler=Mock(side_effect=self.compile)
        return WeekInputAdapter(self.f.store,self.f.project,list_rows=lambda:copy.deepcopy(self.rows),
                                eligible=lambda r:r['fields'].get('brief')!='not ready',compile_input=self.compiler)
    def compile(self,row,now):
        sample={'fixture':row['record_id']};ref='snapshot:'+fingerprint(sample)
        value={'source_base':'bascnProductionFixture','source_table':'tblProductionFixture',
               'record_id':row['record_id'],'source_version':fingerprint(row),'eligible_at':now,
               'sku_id':'sku_one','brief':ref,'input_summary':'fixture approved input'}
        value['input_sha256']=fingerprint(value)
        return {'snapshot':value,'sample':sample,'evidence':{'source':'synthetic'}}
    def test_initial_inventory_never_compiles_historical_tasks(self):
        a=self.adapter();self.assertEqual(a.read_new(),[]);self.assertEqual(a.read_new(),[])
        self.compiler.assert_not_called();self.assertEqual(a.samples(),{})
    def test_new_task_freezes_and_restart_reuses_before_queue(self):
        a=self.adapter();a.read_new();self.rows.append({'record_id':'recNew','fields':{'brief':'new'}})
        first=a.read_new();second=a.read_new();self.assertEqual(first,second)
        self.assertEqual(self.compiler.call_count,1);self.assertEqual(len(a.samples()),1)
    def test_changed_ineligible_task_never_compiles(self):
        a=self.adapter();a.read_new();self.rows[0]['fields']['brief']='not ready'
        self.assertEqual(a.read_new(),[]);self.compiler.assert_not_called()
    def test_missing_evidence_is_durable_no_auto_retry_same_input(self):
        a=self.adapter();a.read_new();self.rows[0]['fields']['brief']='new approved version'
        self.compiler.side_effect=None;self.compiler.return_value={'state':'needs_evidence','reason':'plan evidence missing'}
        self.assertEqual(a.read_new(),[]);self.assertEqual(a.read_new(),[])
        self.assertEqual(self.compiler.call_count,1)
        with self.f.store.connect() as db:self.assertEqual(len(meta(db,REGISTRY+self.f.project)['rejected']),1)
    def test_transport_failure_propagates_instead_of_skipping(self):
        a=self.adapter();a.read_new();self.rows[0]['fields']['brief']='changed'
        self.compiler.side_effect=TimeoutError
        with self.assertRaises(TimeoutError):a.read_new()
    def test_compiler_wrong_source_or_reference_refused(self):
        a=self.adapter();a.read_new();self.rows[0]['fields']['brief']='changed'
        def bad(r,n):
            out=self.compile(r,n);out['snapshot']['source_base']='basWrong';return out
        self.compiler.side_effect=bad
        with self.assertRaisesRegex(RuntimeFault,'MISMATCH'):a.read_new()
        self.assertEqual(a.samples(),{})
    def test_prepared_or_expired_performs_no_source_reads(self):
        a=self.adapter()
        with patch('video_factory.script_week_inputs.time.time',return_value=self.t0+604800):
            self.assertEqual(a.read_new(),[])
        with self.f.store.connect() as db:
            w=meta(db,PREFIX+self.f.project);w.update(state='prepared',t0=None,t1=None);save(db,PREFIX+self.f.project,w)
        self.assertEqual(a.read_new(),[]);self.compiler.assert_not_called()
    def test_deadline_during_compilation_does_not_save_ready_input(self):
        a=self.adapter();a.read_new();self.rows[0]['fields']['brief']='changed'
        def compile_close(r,n):
            value=self.compile(r,n)
            with self.f.store.connect() as db:
                w=meta(db,PREFIX+self.f.project);w['state']='closed';save(db,PREFIX+self.f.project,w)
            return value
        self.compiler.side_effect=compile_close
        with self.assertRaisesRegex(RuntimeFault,'WEEK_CLOSED'):a.read_new()
        self.assertEqual(a.samples(),{})

if __name__=='__main__':unittest.main()
