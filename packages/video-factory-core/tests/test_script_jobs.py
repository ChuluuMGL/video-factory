import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from cryptography.fernet import Fernet
from review_fixture import Fixture
from video_factory.feishu_bridge import save,meta
from video_factory.feishu_native_review import source_key as native_source_key
from video_factory.runtime_store import RuntimeFault
from video_factory.script_jobs import ScriptJobs,profile,key
from video_factory.automation import issue_execution
from video_factory.video_jobs import prepare as prepare_video


class Provider:
    calls=0
    fail=False
    def generate(self,*_):
        self.calls+=1
        if self.fail:raise TimeoutError('synthetic-no-retry')
        return {'script':'Safe generated script','provider_id':'script-receipt-'+str(self.calls)}


class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.f=Fixture(self.temp.name)
        self.key=Fernet.generate_key();self.provider=Provider()
        with self.f.store.connect() as db:save(db,'setup:project:'+self.f.project,{'configuration':{'project':{'base_mode':'bind','products':[{'sku_id':'sku_one','name':'Example','truth_source':'Only package size is known'}]}}})
        self.f.store.put_secret(self.f.admin,'script','synthetic-script-key',self.key)
        profile(self.f.store,self.f.admin,self.f.project,'secret:script','customer_account')
        self.jobs=ScriptJobs(self.f.store,client_factory=self.f.bridge.client_factory,provider=self.provider)
        self.cap=issue_execution(self.f.store,self.f.admin,self.f.project)['token']
        self.args={'project':self.f.project,'task':'generated_one','sku_id':'sku_one','brief':'Show the packaging','expected_revision':0}
    def tearDown(self):self.f.close();self.temp.cleanup()
    def queue(self):
        value=self.jobs.prepare('synthetic-user-token',**self.args)
        return self.jobs.submit('synthetic-user-token',value['plan_sha256'],**self.args)
    def step(self,revision=1):return self.jobs.step(self.cap,self.f.project,'generated_one',revision,self.key)
    def review(self,decision='accept',feedback='',revision=1):
        args=dict(project=self.f.project,task='generated_one',revision=revision,stage='script',decision=decision,feedback=feedback)
        bridge=self.f.service.bridge
        plan=bridge.prepare_review('synthetic-user-token',**args)
        return bridge.review('synthetic-user-token',event='review-'+str(revision),expected_plan=plan['plan_sha256'],**args)
    def test_queue_generate_reject_repair_and_approve(self):
        self.assertEqual(self.queue()['state'],'script_queued');self.assertEqual(self.provider.calls,0)
        self.assertEqual(self.step()['state'],'awaiting_script_review')
        self.assertEqual(self.review('reject','Make it shorter')['state'],'rejected')
        self.args['expected_revision']=1
        planned=self.jobs.prepare('synthetic-user-token',**self.args)
        self.assertIn('Make it shorter',planned['plan']['feedback'])
        self.queue();self.step(2)
        self.assertEqual(self.review(revision=2)['state'],'ready');self.assertEqual(self.provider.calls,2)
        with self.assertRaisesRegex(RuntimeFault,'VIDEO_SETUP'):prepare_video(self.f.service,'synthetic-user-token','generated_one',2)
    def test_unknown_is_never_resent_and_wrong_project_denied(self):
        self.queue();self.provider.fail=True
        self.assertEqual(self.step()['state'],'script_submission_unknown')
        self.step();self.assertEqual(self.provider.calls,1)
        with self.assertRaisesRegex(RuntimeFault,'AUTH_EXECUTION'):self.jobs.step(self.cap,'other','generated_one',1,self.key)
    def test_input_or_credential_drift_refused(self):
        planned=self.jobs.prepare('synthetic-user-token',**self.args)
        with self.assertRaisesRegex(RuntimeFault,'SCRIPT_PLAN_CHANGED'):self.jobs.submit('synthetic-user-token',planned['plan_sha256'],**(self.args|{'brief':'Changed'}))
        self.queue();self.f.store.put_secret(self.f.admin,'script','changed-synthetic-key',self.key)
        with self.assertRaisesRegex(RuntimeFault,'CREDENTIAL_CHANGED'):self.step()
        self.assertEqual(self.provider.calls,0)
    def test_concurrent_queue_steps_generate_once(self):
        self.queue()
        def run(_):
            try:return self.step()
            except RuntimeFault:return None
        with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(run,range(4)))
        self.assertEqual(self.provider.calls,1)
    def test_receipt_replay_cannot_change_request(self):
        plan=self.jobs.prepare('synthetic-user-token',**self.args)
        self.jobs.submit('synthetic-user-token',plan['plan_sha256'],**self.args)
        self.assertTrue(self.jobs.submit('synthetic-user-token',plan['plan_sha256'],**self.args)['replayed'])
        with self.assertRaisesRegex(RuntimeFault,'SUBMIT_CONFLICT'):
            self.jobs.submit('synthetic-user-token',plan['plan_sha256'],**(self.args|{'brief':'changed'}))

    def test_stale_capability_after_restore_blocks_script(self):
        self.queue()
        with self.f.store.connect() as db:self.f.store.invalidate_worker_approvals(db)
        with self.assertRaisesRegex(RuntimeFault,'AUTH_EXECUTION'):self.step()
        self.assertEqual(self.provider.calls,0)

    def test_native_review_binds_base_row_before_paid_script_request(self):
        class Source:
            fields={'任务编号':'generated_one','SKU':'sku_one','脚本':'Base brief',
                    '来源版本':'source_v1','状态':''}
            def find_task(self,base,table,name,task):
                return {'record_id':'recFixture','fields':dict(self.fields)}
            def record(self,base,table,record):
                return {'record_id':record,'fields':dict(self.fields)}
        source=Source()
        names={'task':'任务编号','sku_id':'SKU','script':'脚本','source_revision':'来源版本','status':'状态'}
        with self.f.store.connect() as db:
            save(db,'native:config:'+self.f.project,{'enabled':True,'context':{'target':{
                'base_token':'bascnFixture','table_id':'tblFixture'}},'names':names})
        jobs=ScriptJobs(self.f.store,client_factory=self.f.bridge.client_factory,
                        source_client_factory=lambda _:source,native_app_client_factory=lambda _:source,
                        provider=self.provider)
        plan=jobs.prepare('synthetic-user-token',**self.args)
        self.assertEqual(plan['plan']['native_source']['record_id'],'recFixture')
        jobs.submit('synthetic-user-token',plan['plan_sha256'],**self.args)
        with self.f.store.connect() as db:
            self.assertEqual(meta(db,native_source_key(self.f.project,'generated_one'))['record_id'],'recFixture')
        self.assertEqual(self.provider.calls,0)
        source.fields['来源版本']='source_v2'
        with self.assertRaisesRegex(RuntimeFault,'SCRIPT_NATIVE_SOURCE_CHANGED'):
            jobs.step(self.cap,self.f.project,'generated_one',1,self.key)
        self.assertEqual(self.provider.calls,0)
        source.fields['来源版本']='source_v1'
        self.assertEqual(jobs.step(self.cap,self.f.project,'generated_one',1,self.key)['state'],'awaiting_script_review')
        row=self.f.store.inspect_task(self.f.admin,self.f.project,'generated_one')['versions'][0]
        self.assertEqual(json.loads(row['input'])['source_revision'],'source_v1')
