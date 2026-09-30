import copy
import json
from pathlib import Path
import tempfile
import unittest
from video_factory.runtime_store import RuntimeStore,RuntimeFault
from video_factory.feishu_bridge import FeishuBridge
from video_factory.feishu_client import FeishuClient
from video_factory import automation

BINDING={'tenant_key':'fixture_tenant','base_token':'bascnFixture','table_id':'tblFixture',
    'fields':{'task':'fldTask','sku_id':'fldSkuId','script':'fldScript','source_revision':'fldSource'},
    'submitters':['ou_submitter'],'reviewers':['ou_reviewer']}


class FixtureClient:
    def __init__(self,token,fixture):self.token=token;self.fixture=fixture
    def identity(self):
        if self.token=='expired':raise RuntimeFault('FEISHU_READ_OR_USER_AUTH_FAILED')
        return {'tenant_key':'other_tenant' if self.token=='outsider' else 'fixture_tenant','open_id':'ou_'+self.token}
    def fields(self,base,table):return copy.deepcopy(self.fixture.schema)
    def record(self,base,table,record):
        if self.fixture.on_read:self.fixture.on_read()
        return {'record_id':record,'fields':copy.deepcopy(self.fixture.fields)}


class FeishuTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=RuntimeStore.install(self.root,'fixture','fixture-admin-password')
        self.admin=self.store.login('admin','fixture-admin-password')['token']
        self.store.put_project(self.admin,'brand',{'video_route':'deferred','credential_ref':'secret:fixture','billing_owner':'fixture'})
        self.schema=[{'field_id':value,'field_name':key,'type':1} for key,value in BINDING['fields'].items()]
        self.fields={'task':'task_one','sku_id':'sku_one','script':'Approved script','source_revision':'source_one'};self.on_read=None
        self.bridge=FeishuBridge(self.store,client_factory=lambda token:FixtureClient(token,self))
        self.binding=self.bridge.bind(self.admin,'brand',copy.deepcopy(BINDING),'submitter')['binding_sha256']

    def tearDown(self):self.temp.cleanup()

    def import_task(self,expected=0):
        plan=self.bridge.prepare_import('submitter','brand','recFixture',expected)
        return self.bridge.import_task('submitter','brand','recFixture',plan['plan_sha256'],expected)

    def review(self,decision='accept',feedback='',stage='script',revision=1,event='event_one'):
        plan=self.bridge.prepare_review('reviewer','brand','task_one',revision,stage,decision,feedback)
        return self.bridge.review('reviewer','brand','task_one',revision,stage,decision,event,plan['plan_sha256'],feedback),plan

    def test_import_review_reject_repair_preserves_history(self):
        self.assertEqual(self.import_task()['state'],'awaiting_script_review')
        self.assertTrue(self.import_task()['replayed'])
        rejected,_=self.review('reject','Fix product name')
        self.assertEqual(rejected['actor'],'feishu:fixture_tenant:ou_reviewer')
        self.fields['script']='Correct product';self.fields['source_revision']='source_two'
        self.assertEqual(self.import_task(1)['revision'],2)
        approved,_=self.review(revision=2,event='second_event')
        self.assertEqual(approved['state'],'ready')
        history=self.store.inspect_task(self.admin,'brand','task_one')
        self.assertEqual(len(history['versions']),2)
        self.assertEqual(history['reviews'][0]['feedback'],'Fix product name')

    def test_wrong_tenant_role_and_expired_user_never_import(self):
        for token in ('outsider','reviewer','expired'):
            with self.assertRaises(RuntimeFault):self.bridge.prepare_import(token,'brand','recFixture')
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)

    def test_record_change_between_plan_and_commit_rejected(self):
        plan=self.bridge.prepare_import('submitter','brand','recFixture');self.fields['script']='Changed'
        with self.assertRaisesRegex(RuntimeFault,'PLAN_CHANGED'):self.bridge.import_task('submitter','brand','recFixture',plan['plan_sha256'])

    def test_source_change_blocks_review_without_overwriting_input(self):
        self.import_task();self.fields['script']='Unreviewed replacement'
        with self.assertRaisesRegex(RuntimeFault,'SOURCE_CHANGED'):self.review()
        stored=self.store.inspect_task(self.admin,'brand','task_one')['versions'][0]
        self.assertEqual(stored['state'],'awaiting_script_review')
        self.assertEqual(json.loads(stored['input'])['script'],'Approved script')

    def test_membership_revocation_during_external_read_is_fenced(self):
        changed=copy.deepcopy(BINDING);changed['submitters']=['ou_someone_else']
        self.on_read=lambda:self.bridge.bind(self.admin,'brand',changed,'submitter',self.binding)
        with self.assertRaisesRegex(RuntimeFault,'BINDING_CHANGED'):self.bridge.prepare_import('submitter','brand','recFixture')

    def test_duplicate_review_replayed_but_changed_decision_denied(self):
        self.import_task();receipt,plan=self.review()
        replay=self.bridge.review('reviewer','brand','task_one',1,'script','accept','event_one',plan['plan_sha256'])
        self.assertTrue(replay['replayed']);self.assertEqual(replay['actor'],receipt['actor'])
        with self.assertRaisesRegex(RuntimeFault,'EVENT_ID_CONFLICT'):
            self.bridge.review('reviewer','brand','task_one',1,'script','reject','event_one',plan['plan_sha256'],'Changed decision')

    def test_old_review_permission_revoked_even_for_replay(self):
        self.import_task();_,plan=self.review()
        changed=copy.deepcopy(BINDING);changed['reviewers']=['ou_someone_else']
        self.bridge.bind(self.admin,'brand',changed,'submitter',self.binding)
        with self.assertRaisesRegex(RuntimeFault,'ROLE_DENIED'):
            self.bridge.review('reviewer','brand','task_one',1,'script','accept','event_one',plan['plan_sha256'])

    def test_same_task_cannot_move_record_or_reuse_source_revision(self):
        self.import_task();self.review('reject','Fix text')
        with self.assertRaisesRegex(RuntimeFault,'MUST_ADVANCE'):self.import_task(1)
        self.fields['source_revision']='source_two'
        plan=self.bridge.prepare_import('submitter','brand','recOther',1)
        with self.assertRaisesRegex(RuntimeFault,'SOURCE_CONFLICT'):self.bridge.import_task('submitter','brand','recOther',plan['plan_sha256'],1)

    def test_schema_formula_and_missing_field_rejected(self):
        self.schema[0]['type']=20
        with self.assertRaisesRegex(RuntimeFault,'TEXT_FIELD'):self.bridge.prepare_import('submitter','brand','recFixture')

    def test_existing_binding_requires_compare_and_swap_and_fixed_target(self):
        with self.assertRaisesRegex(RuntimeFault,'BINDING_CONFLICT'):self.bridge.bind(self.admin,'brand',BINDING,'submitter')
        changed=copy.deepcopy(BINDING);changed['table_id']='tblDifferent'
        with self.assertRaisesRegex(RuntimeFault,'TARGET_IMMUTABLE'):self.bridge.bind(self.admin,'brand',changed,'submitter',self.binding)

    def test_video_review_binds_exact_artifact_and_feedback(self):
        self.import_task();self.review();self.store.claim(self.admin,'brand','task_one',1)
        self.store.attach_provider(self.admin,'brand','task_one',1,'22345678901234')
        self.store.record_artifact(self.admin,'brand','task_one',1,'22345678901234',{'sha256':'a'*64,'location':'/media/fixture.mp4','verification':'full_decode_passed'})
        plan=self.bridge.prepare_review('reviewer','brand','task_one',1,'video','reject','Change the final shot')
        self.assertEqual(plan['plan']['artifact']['sha256'],'a'*64)
        with self.assertRaisesRegex(RuntimeFault,'PLAN_CHANGED'):
            self.bridge.review('reviewer','brand','task_one',1,'video','reject','video_event',plan['plan_sha256'],'Different feedback')
        result=self.bridge.review('reviewer','brand','task_one',1,'video','reject','video_event',plan['plan_sha256'],'Change the final shot')
        self.assertEqual(result['state'],'rejected')

    def test_setup_catalog_rejects_unrelated_sku(self):
        from video_factory.feishu_bridge import save
        with self.store.connect() as db:
            save(db,'setup:project:brand',{'configuration':{'project':{'products':[{'sku_id':'different_sku'}]}}})
        with self.assertRaisesRegex(RuntimeFault,'SKU_NOT_IN_PROJECT'):self.import_task()

    def test_queue_token_cannot_review_or_read_another_project_and_restore_revokes(self):
        self.import_task();issued=automation.issue(self.store,self.admin,'brand',1)
        self.assertEqual(automation.queue(self.store,issued['token'],'brand')['items'][0]['state'],'awaiting_script_review')
        with self.assertRaises(RuntimeFault):automation.queue(self.store,issued['token'],'other_brand')
        with self.assertRaises(RuntimeFault):self.store.review(issued['token'],'forged','brand','task_one',1,'script','accept')
        with self.store.connect() as db:self.store.invalidate_worker_approvals(db)
        with self.assertRaises(RuntimeFault):automation.queue(self.store,issued['token'],'brand')

    def test_restored_membership_requires_admin_reconfirmation(self):
        with self.store.connect() as db:self.store.invalidate_worker_approvals(db)
        with self.assertRaisesRegex(RuntimeFault,'RECONFIRM'):self.import_task()
        self.bridge.bind(self.admin,'brand',BINDING,'submitter',self.binding)
        self.assertEqual(self.import_task()['state'],'awaiting_script_review')

    def test_revocation_and_queue_template_are_non_executing(self):
        issued=automation.issue(self.store,self.admin,'brand')
        automation.revoke(self.store,self.admin,issued['key_id'])
        with self.assertRaises(RuntimeFault):automation.queue(self.store,issued['token'],'brand')
        draft=automation.template('brand','fixture_credential')
        self.assertFalse(draft['active']);self.assertIn('/v1/automation/queue',draft['nodes'][1]['parameters']['url'])


class ClientTests(unittest.TestCase):
    def test_pagination_is_complete_and_loops_fail_closed(self):
        client=FeishuClient('synthetic-token');calls=[]
        def get(path):
            calls.append(path)
            return {'items':[{'field_id':'fldOne'}],'has_more':True,'page_token':'next'} if len(calls)==1 else {'items':[{'field_id':'fldTwo'}],'has_more':False}
        client.get=get;self.assertEqual(len(client.fields('bascnFixture','tblFixture')),2)
        client.get=lambda path:{'items':[],'has_more':True,'page_token':'repeated'}
        with self.assertRaisesRegex(RuntimeFault,'PAGINATION'):client.fields('bascnFixture','tblFixture')

    def test_identity_and_resource_validation_do_not_accept_names_or_urls(self):
        client=FeishuClient('synthetic-token');client.get=lambda path:{'name':'Administrator'}
        with self.assertRaises(RuntimeFault):client.identity()
        with self.assertRaises(RuntimeFault):client.fields('https://example.com','tblFixture')
