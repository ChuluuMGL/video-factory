import copy
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from cryptography.fernet import Fernet

from video_factory.runtime_store import RuntimeStore, RuntimeFault, canonical
from video_factory.worker import Worker, job_key
from video_factory.h3_provider import H3Provider, ProviderRejected, request_body

PASSWORD='worker-test-only-password'
CONFIG={'video_route':'deferred','credential_ref':'secret:fixture','billing_owner':'fixture'}


class Provider:
    calls=0
    mode='ok'
    def submit(self,body,secret):
        self.calls+=1
        if self.mode=='unknown':raise TimeoutError('not logged')
        if self.mode=='reject':raise ProviderRejected('rejected')
        return str(12345678901233+self.calls)
    def poll(self,provider_id,secret):return {'id':provider_id,'status':'succeeded','url':'fixture://media'}


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=RuntimeStore.install(self.root,'fixture',PASSWORD)
        self.token=self.store.login('admin',PASSWORD)['token'];self.key=Fernet.generate_key()
        self.store.put_secret(self.token,'fixture','synthetic-provider-key',self.key)
        self.store.put_project(self.token,'brand',CONFIG)
        self.assets=self.root/'assets';self.assets.mkdir(mode=0o700)
        self.image=self.assets/'image.png';self.image.write_bytes(b'unit-fixture-not-real-media')
        self.spec={'duration':5,'references':[{'path':'image.png','sha256':hashlib.sha256(self.image.read_bytes()).hexdigest()}]}
        self.provider=Provider()
        self.worker=Worker(self.store,provider_factory=lambda _:self.provider,media_preflight=lambda:None,
            media_collector=lambda *args:{'sha256':'a'*64,'location':str(self.root/'fake.mp4'),'verification':'full_decode_passed'})
        self.ready('brand','one')

    def tearDown(self):self.temp.cleanup()

    def ready(self,project,task):
        self.store.create_task(self.token,project,task,{'sku_id':'sku','script':'Approved script','source_revision':'1'})
        self.store.review(self.token,'review-'+project+'-'+task,project,task,1,'script','accept')

    def values(self,project='brand',task='one'):
        return dict(project=project,task=task,revision=1,assets_root=self.assets,specification=self.spec,
                    credential_ref='secret:fixture',billing_owner='fixture-account',region='global')

    def approve(self,project='brand',task='one'):
        prepared=self.worker.prepare(self.token,**self.values(project,task))
        return self.worker.approve(self.token,prepared['request_plan_sha256'],**self.values(project,task))

    def step(self,task='one',project='brand',**kwargs):
        return self.worker.step(self.token,project,task,1,self.key,self.root,**kwargs)

    def test_approval_and_explicit_submit_both_required(self):
        with self.assertRaisesRegex(RuntimeFault,'APPROVAL_REQUIRED'):self.step(allow_paid=True)
        self.approve()
        with self.assertRaisesRegex(RuntimeFault,'EXPLICIT_PAID'):self.step()
        self.assertEqual(self.provider.calls,0)

    def test_success_waits_for_human_and_replays_without_submit(self):
        self.approve();self.assertEqual(self.step(allow_paid=True)['state'],'submitted')
        self.assertEqual(self.step()['state'],'awaiting_video_review')
        self.assertTrue(self.step(allow_paid=True)['replayed'])
        self.assertEqual(self.provider.calls,1)

    def test_unknown_submit_survives_reconstruction_without_retry(self):
        self.approve();self.provider.mode='unknown'
        self.assertEqual(self.step(allow_paid=True)['state'],'submission_unknown')
        self.worker=Worker(RuntimeStore(self.root),provider_factory=lambda _:self.provider)
        self.assertTrue(self.step(allow_paid=True)['reconciliation_required'])
        self.assertEqual(self.provider.calls,1)

    def test_definite_rejection_is_terminal_and_new_revision_needs_review(self):
        self.approve();self.provider.mode='reject'
        self.assertEqual(self.step(allow_paid=True)['state'],'failed')
        self.assertEqual(self.step(allow_paid=True)['state'],'failed')
        created=self.store.create_task(self.token,'brand','one',{'sku_id':'sku','script':'Revised','source_revision':'2'},1)
        self.assertEqual(created['state'],'awaiting_script_review')
        self.assertEqual(self.provider.calls,1)

    def test_provider_auth_rejection_keeps_safe_diagnostic_without_retry(self):
        self.approve()
        self.provider.submit=lambda *args:(_ for _ in ()).throw(
            ProviderRejected('PROVIDER_AUTH_REJECTED_CHECK_REGION_OR_KEY'))
        result=self.step(allow_paid=True)
        self.assertEqual(result['failure_code'],'PROVIDER_AUTH_REJECTED_CHECK_REGION_OR_KEY')
        self.assertEqual(self.worker.status(self.token,'brand','one',1)['failure_code'],result['failure_code'])
        replay=self.step(allow_paid=True)
        self.assertEqual(replay['provider_requests'],0)
        self.assertEqual(replay['failure_code'],result['failure_code'])

    def test_expired_approval_and_changed_key_stop_before_network(self):
        self.approve()
        with self.store.connect() as db:
            key=job_key('brand','one',1);value=json.loads(db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()[0])
            value['expires_at']=time.time()-1;db.execute('UPDATE meta SET value=? WHERE key=?',(canonical(value),key))
        with self.assertRaisesRegex(RuntimeFault,'EXPIRED'):self.step(allow_paid=True)
        self.approve();self.store.put_secret(self.token,'fixture','changed-key',self.key)
        with self.assertRaisesRegex(RuntimeFault,'CREDENTIAL_CHANGED'):self.step(allow_paid=True)
        self.assertEqual(self.provider.calls,0)

    def test_invalid_key_format_does_not_consume_submit_intent(self):
        self.store.put_secret(self.token,'fixture','Bearer synthetic-key',self.key)
        self.approve()
        with self.assertRaisesRegex(RuntimeFault,'KEY_FORMAT'):self.step(allow_paid=True)
        self.assertEqual(self.worker.status(self.token,'brand','one',1)['state'],'ready')
        self.assertEqual(self.provider.calls,0)

    def test_asset_and_plan_changes_fail_before_submission(self):
        plan=self.worker.prepare(self.token,**self.values())
        values=self.values();values['billing_owner']='other-account'
        with self.assertRaisesRegex(RuntimeFault,'PLAN_CHANGED'):self.worker.approve(self.token,plan['request_plan_sha256'],**values)
        self.approve();self.image.write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeFault,'REFERENCE_CHANGED'):self.step(allow_paid=True)
        self.assertEqual(self.provider.calls,0)

    def test_multiple_tasks_and_cross_project_same_id_are_independent(self):
        self.ready('brand','two');self.store.put_project(self.token,'other',CONFIG);self.ready('other','one')
        for project,task in [('brand','one'),('brand','two'),('other','one')]:
            self.approve(project,task);self.step(project=project,task=task,allow_paid=True)
        self.assertEqual(self.provider.calls,3)

    def test_concurrent_workers_submit_once(self):
        self.approve()
        def step(_):
            try:return self.step(allow_paid=True)
            except RuntimeFault:return {'blocked':True}
        with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(step,range(4)))
        self.assertEqual(self.provider.calls,1)

    def test_persisted_receipt_recovers_after_attach_failure(self):
        self.approve();original=self.store.attach_provider
        self.store.attach_provider=lambda *args:(_ for _ in ()).throw(RuntimeFault('INJECTED_CRASH'))
        with self.assertRaises(RuntimeFault):self.step(allow_paid=True)
        self.assertEqual(self.worker.status(self.token,'brand','one',1)['provider_id'],'12345678901234')
        self.store.attach_provider=original
        self.assertEqual(self.step()['state'],'awaiting_video_review')
        self.assertEqual(self.provider.calls,1)

    def test_bad_media_does_not_mark_task_review_ready_or_resubmit(self):
        self.approve();self.step(allow_paid=True)
        self.worker.media_collector=lambda *a:(_ for _ in ()).throw(RuntimeFault('BAD_MEDIA'))
        with self.assertRaisesRegex(RuntimeFault,'BAD_MEDIA'):self.step()
        self.assertEqual(self.worker.status(self.token,'brand','one',1)['state'],'submitted')
        self.assertEqual(self.provider.calls,1)

    def test_provider_receipt_cannot_be_reused_for_another_task(self):
        self.approve();self.step(allow_paid=True)
        self.ready('brand','two');self.approve(task='two')
        self.provider.submit=lambda *args:'12345678901234'
        with self.assertRaisesRegex(RuntimeFault,'ALREADY_BOUND'):self.step(task='two',allow_paid=True)
        self.assertEqual(self.worker.status(self.token,'brand','two',1)['state'],'submission_unknown')

    def test_restore_expires_unused_permission_but_preserves_submitted_receipt(self):
        from video_factory.runtime_ops import backup, restore
        self.approve();self.ready('brand','two');self.approve(task='two');self.step(task='two',allow_paid=True)
        backup_key=Fernet.generate_key();archive=self.root/'backup.vfb';backup(self.store,archive,backup_key)
        target=self.root/'restored';target.mkdir(mode=0o700);restore(archive,target,backup_key)
        self.store=RuntimeStore(target);self.token=self.store.login('admin',PASSWORD)['token']
        self.worker=Worker(self.store,provider_factory=lambda _:self.provider,media_preflight=lambda:None,
            media_collector=lambda *a:{'sha256':'b'*64,'location':str(self.root/'restored.mp4'),'verification':'full_decode_passed'})
        with self.assertRaisesRegex(RuntimeFault,'EXPIRED'):self.step(allow_paid=True)
        self.assertEqual(self.worker.status(self.token,'brand','two',1)['provider_id'],'12345678901234')
        self.assertEqual(self.step(task='two')['state'],'awaiting_video_review')
        self.assertEqual(self.provider.calls,1)

    def test_provider_parser_rejects_mismatched_id_and_uncertain_success(self):
        p=H3Provider('global');p.call=lambda *a:(200,{'task_id':'not-an-id'})
        with self.assertRaisesRegex(RuntimeFault,'UNCERTAIN'):p.submit({},'fixture')
        p.call=lambda *a:(200,{'task':{'id':'wrong','model':'MiniMax-H3','status':'succeeded'}})
        with self.assertRaisesRegex(RuntimeFault,'UNVERIFIED'):p.poll('12345678901234','fixture')
        p.call=lambda *a:(402,{'error':{'type':'insufficient_balance_error'}})
        with self.assertRaisesRegex(ProviderRejected,'PROVIDER_BALANCE_REQUIRED'):p.submit({},'fixture')

    def test_reference_paths_and_unapproved_media_origins_are_rejected(self):
        bad=copy.deepcopy(self.spec);bad['references'][0]['path']='../runtime.sqlite3'
        with self.assertRaisesRegex(RuntimeFault,'PATH_INVALID'):request_body('script',self.assets,bad)
        import io
        p=H3Provider('global')
        for url in ('http://127.0.0.1/a','https://cdn.hailuoai.com.evil/a','https://key@cdn.hailuoai.com/a'):
            with self.assertRaisesRegex(RuntimeFault,'HOST_NOT_APPROVED'):p.download(url,io.BytesIO())


if __name__=='__main__':unittest.main()
