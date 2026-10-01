import unittest
from concurrent.futures import ThreadPoolExecutor
from video_factory.automation import issue,issue_execution,revoke
from video_factory.dispatch import dispatch_one,template
from video_factory.runtime_store import RuntimeFault
from video_factory.worker import Worker
import test_worker


class DispatchTests(unittest.TestCase):
    ready=test_worker.WorkerTests.ready
    values=test_worker.WorkerTests.values
    approve=test_worker.WorkerTests.approve
    tearDown=test_worker.WorkerTests.tearDown
    def execute(self,capability=None,project='brand'):
        return dispatch_one(self.store,capability or self.capability,project,self.key,self.root,
            worker_factory=lambda store:Worker(store,provider_factory=lambda _:self.provider,media_preflight=lambda:None,
                media_collector=lambda *args:{'sha256':'a'*64,'location':str(self.root/'fake.mp4'),'verification':'full_decode_passed'}))
    def setUp(self):
        test_worker.WorkerTests.setUp(self);self.capability=issue_execution(self.store,self.token,'brand')['token']
    def test_scope_approval_unknown_and_recovery(self):
        self.assertEqual(self.execute()['status'],'idle');self.assertEqual(self.provider.calls,0)
        read=issue(self.store,self.token,'brand')['token']
        with self.assertRaisesRegex(RuntimeFault,'AUTH_EXECUTION'):self.execute(read)
        with self.assertRaisesRegex(RuntimeFault,'AUTH_EXECUTION'):self.execute(project='other')
        with self.store.connect() as db:
            with self.assertRaisesRegex(RuntimeFault,'AUTH_REQUIRED'):self.store.authorize(db,self.capability,'admin')
        self.approve();self.provider.mode='unknown';self.execute()
        self.assertEqual(self.execute()['blocked'][0]['reason'],'submission_unknown');self.assertEqual(self.provider.calls,1)
        with self.store.connect() as db:self.store.invalidate_worker_approvals(db)
        with self.assertRaisesRegex(RuntimeFault,'AUTH_EXECUTION'):self.execute()
    def test_dispatch_completes_to_review_once_under_race(self):
        self.approve()
        def go(_):
            try:return self.execute()
            except RuntimeFault:return None
        with ThreadPoolExecutor(max_workers=4) as p:list(p.map(go,range(4)))
        self.execute()
        self.assertEqual(self.provider.calls,1)
        self.assertEqual(self.worker.status(self.token,'brand','one',1)['state'],'awaiting_video_review')
        self.assertEqual(self.execute()['status'],'idle')
    def test_replacing_video_profile_revokes_unused_paid_approval(self):
        from video_factory.feishu_bridge import save
        from video_factory.video_jobs import configure
        with self.store.connect() as db:save(db,'setup:project:brand',{'configuration':{'project':{'base_mode':'bind','products':[{'sku_id':'sku'}]}}})
        self.approve()
        configure(self.store,self.token,'brand','secret:fixture','fixture-account','global',{'sku':{'assets_root':'/work/fixture','specification':self.spec}})
        self.assertEqual(self.execute()['status'],'idle')
        self.assertEqual(self.provider.calls,0)
        with self.assertRaisesRegex(RuntimeFault,'EXPIRED'):
            self.worker.step(self.token,'brand','one',1,self.key,self.root,allow_paid=True)

    def test_replacing_video_profile_preserves_submitted_receipt(self):
        from video_factory.feishu_bridge import save
        from video_factory.video_jobs import configure
        with self.store.connect() as db:save(db,'setup:project:brand',{'configuration':{'project':{'base_mode':'bind','products':[{'sku_id':'sku'}]}}})
        self.approve();self.execute()
        configure(self.store,self.token,'brand','secret:fixture','fixture-account','global',{'sku':{'assets_root':'/work/fixture','specification':self.spec}})
        self.execute()
        self.assertEqual(self.provider.calls,1)
        self.assertEqual(self.worker.status(self.token,'brand','one',1)['state'],'awaiting_video_review')

    def test_template_has_no_admin_secret_no_auto_activation(self):
        result=template('brand','approved-job-key');self.assertFalse(result['active'])
        self.assertIn('/v1/dispatch',result['nodes'][1]['parameters']['url'])
        self.assertEqual(result['connections']['Read schedule']['main'][0][0]['node'],'Dispatch approved task')
