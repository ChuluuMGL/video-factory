"""Cloud-only synthetic tests for Base-native human review trust boundaries."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from cryptography.fernet import Fernet

from video_factory.feishu_bridge import save
from video_factory.feishu_native_review import NativeReview
from video_factory.feishu_production_entry import plan_key
from video_factory.script_jobs import check_production_gate, key as script_key
from video_factory.feishu_native_sync import item_key
from video_factory.runtime_store import RuntimeFault, RuntimeStore, canonical, fingerprint


BINDING={'tenant_key':'fixture_tenant','base_token':'bascnFixture','table_id':'tblFixture',
         'fields':{'task':'fldTask','sku_id':'fldSkuId','script':'fldScript','source_revision':'fldSource'},
         'submitters':['ou_submitter'],'reviewers':['ou_reviewer'],
         'script_reviewers':['ou_reviewer'],'video_reviewers':['ou_reviewer']}
NAMES={'task':'任务编号','sku_id':'SKU','script':'脚本','source_revision':'来源版本',
       'status':'状态','review_revision':'审核目标版本','script_digest':'脚本摘要','feedback':'审核意见',
       'video_digest':'视频摘要','video':'视频'}
IDS={'status':'fldStatus','review_revision':'fldReviewRevision','script_digest':'fldScriptDigest','feedback':'fldFeedback',
     'video_digest':'fldVideoDigest','video':'fldVideo'}


class Client:
    def __init__(self, fixture): self.fixture=fixture
    def fields(self,base,table):
        return [{'field_id':'fldOperation','field_name':'生产操作','type':3,
                 'property':{'options':[{'id':'optRequestScript','name':'申请脚本计划'}]}},
                {'field_id':'fldCost','field_name':'费用确认','type':3,
                 'property':{'options':[{'id':'optNotConfirmed','name':'未确认'},
                                         {'id':'optCostConfirm','name':'确认此计划的一次生成'}]}},
                {'field_id':'fldStatus','field_name':'状态','type':3,
                 'property':{'options':[{'id':key,'name':name} for key,name in (
                     ('optPending','脚本待审核'),('optApproved','脚本通过'),
                     ('optRejected','脚本退回'),('optVideoPending','视频待审核'),
                     ('optVideoApproved','视频通过'),('optVideoRejected','视频退回'))]}}]
    def record(self,base,table,record):
        assert (base,table,record)==('bascnFixture','tblFixture','recFixture')
        return {'record_id':record,'fields':dict(self.fixture.remote)}
    def update_record(self,base,table,record,fields):
        assert (base,table,record)==('bascnFixture','tblFixture','recFixture')
        self.fixture.writes+=1
        self.fixture.remote.update(fields)
        if getattr(self.fixture,'lose_write_response',False):
            raise RuntimeError('synthetic lost response')
        return record


class NativeReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.store=RuntimeStore.install(Path(self.tmp.name),'fixture','fixture-admin-password')
        self.admin=self.store.login('admin','fixture-admin-password')['token']
        self.store.put_project(self.admin,'brand',{'video_route':'deferred','credential_ref':'secret:fixture','billing_owner':'fixture'})
        self.store.create_task(self.admin,'brand','task_one',{'sku_id':'sku_one','script':'Generated script','source_revision':'source_one'})
        self.remote={'任务编号':'task_one','SKU':'sku_one','来源版本':'source_one',
                     '脚本':'Generated script','脚本摘要':hashlib.sha256(b'Generated script').hexdigest(),
                     '状态':'脚本通过','审核目标版本':'1','审核意见':''}
        self.writes=0
        self.reducer=NativeReview(self.store,client_factory=lambda _:Client(self))
        with self.store.connect() as db:
            save(db,'feishu:binding:brand',BINDING)
            save(db,'native:config:brand',{'enabled':True,'app_id':'cli_fixture','fields':IDS,'names':NAMES})
            save(db,'native:record:brand:recFixture',{'record_id':'recFixture','task':'task_one','revision':1})
            save(db,item_key('brand','task_one',1,'awaiting_script_review'),{'complete':True,'steps':{},'snapshot':{}})
    def tearDown(self):self.tmp.cleanup()
    def event(self,operator='ou_reviewer',before='脚本待审核',after='脚本通过',event_id='evt_fixture'):
        return {'schema':'2.0','header':{'event_id':event_id,'event_type':'drive.file.bitable_record_changed_v1',
            'app_id':'cli_fixture','tenant_key':'fixture_tenant'},'event':{'file_type':'bitable','file_token':'bascnFixture',
            'table_id':'tblFixture','operator_id':{'open_id':operator},'action_list':[{'record_id':'recFixture',
            'action':'record_edited','before_value':[{'field_id':'fldStatus','field_value':json.dumps(before)}],
            'after_value':[{'field_id':'fldStatus','field_value':json.dumps(after)}]}]}}
    def test_verified_base_event_approves_once(self):
        self.assertEqual(self.reducer.enqueue_verified('brand',self.event())['status'],'queued')
        self.assertEqual(self.reducer.process_one('brand')['result']['reviewed'][0]['state'],'ready')
        self.assertEqual(self.reducer.process_one('brand')['status'],'idle')
        self.assertEqual(self.store.inspect_task(self.admin,'brand','task_one')['versions'][0]['state'],'ready')

    def test_rejected_script_revision_uses_same_row_and_never_approves_old_version(self):
        self.remote.update({'状态':'脚本退回','审核意见':'角色发色应为棕色'})
        self.reducer.enqueue_verified('brand',self.event(after='脚本退回',event_id='evt_reject_v1'))
        rejected=self.reducer.process_one('brand')['result']['reviewed'][0]
        self.assertEqual((rejected['revision'],rejected['state'],rejected['feedback']),
                         (1,'rejected','角色发色应为棕色'))
        self.store.create_task(self.admin,'brand','task_one',
                               {'sku_id':'sku_one','script':'Revised brown-haired role',
                                'source_revision':'source_two'},expected_revision=1)
        with self.store.connect() as db:
            save(db,'native:record:brand:recFixture',
                 {'record_id':'recFixture','task':'task_one','revision':2})
            save(db,item_key('brand','task_one',2,'awaiting_script_review'),
                 {'complete':True,'steps':{},'snapshot':{}})
        self.remote.update({'来源版本':'source_two','脚本':'Revised brown-haired role',
                            '脚本摘要':hashlib.sha256(b'Revised brown-haired role').hexdigest(),
                            '审核目标版本':'2','审核意见':'','状态':'脚本通过'})
        stale=self.event(before='脚本待审核',after='脚本通过',event_id='evt_stale_v1')
        self.remote['审核目标版本']='1'
        with self.assertRaisesRegex(RuntimeFault,'FEISHU_REVIEW_REMOTE_CHANGED'):
            self.reducer.consume_verified('brand',stale)
        self.remote['审核目标版本']='2'
        self.reducer.enqueue_verified('brand',self.event(event_id='evt_approve_v2'))
        approved=self.reducer.process_one('brand')['result']['reviewed'][0]
        self.assertEqual((approved['revision'],approved['state']),(2,'ready'))
        versions=self.store.inspect_task(self.admin,'brand','task_one')['versions']
        self.assertEqual([(v['revision'],v['state']) for v in versions],[(1,'rejected'),(2,'ready')])

    def test_single_select_one_item_event_and_record_are_accepted(self):
        self.remote['状态']=['脚本通过']
        event=self.event(before=['脚本待审核'],after=['脚本通过'],event_id='evt_select')
        self.assertEqual(self.reducer.enqueue_verified('brand',event)['status'],'queued')
        self.assertEqual(self.reducer.process_one('brand')['result']['reviewed'][0]['state'],'ready')

    def test_single_select_option_ids_from_real_event_shape_are_accepted(self):
        event=self.event(before='optPending',after='optApproved',event_id='evt_option_id')
        action=event['event']['action_list'][0]
        action['before_value'][0]['field_value']='optPending'
        action['after_value'][0]['field_value']='optApproved'
        self.assertEqual(self.reducer.enqueue_verified('brand',event)['status'],'queued')
        self.assertEqual(self.reducer.process_one('brand')['result']['reviewed'][0]['state'],'ready')

    def test_unrecognized_option_id_cannot_approve(self):
        event=self.event(before='optPending',after='optUnknown',event_id='evt_unknown_option')
        event['event']['action_list'][0]['after_value'][0]['field_value']='optUnknown'
        self.reducer.enqueue_verified('brand',event)
        self.assertEqual(self.reducer.process_one('brand')['reason'],'FEISHU_REVIEW_STATUS_OPTION_UNKNOWN')
        self.assertEqual(self.store.inspect_task(self.admin,'brand','task_one')['versions'][0]['state'],'awaiting_script_review')
    def test_cleared_status_event_does_not_block_later_approval(self):
        cleared=self.event(before='unrelated',after='',event_id='evt_cleared')
        cleared['event']['action_list'][0]['after_value'][0]['field_value']=''
        self.reducer.enqueue_verified('brand',cleared)
        self.assertEqual(self.reducer.process_one('brand')['result']['reviewed'],[])
        self.reducer.enqueue_verified('brand',self.event(event_id='evt_after_clear'))
        self.assertEqual(self.reducer.process_one('brand')['result']['reviewed'][0]['state'],'ready')

    def test_malformed_status_event_is_denied_without_blocking_approval(self):
        invalid=self.event(event_id='evt_invalid_status')
        invalid['event']['action_list'][0]['after_value'][0]['field_value']='not json'
        self.reducer.enqueue_verified('brand',invalid)
        self.assertEqual(self.reducer.process_one('brand')['reason'],'FEISHU_EVENT_FIELD_INVALID')
        self.reducer.enqueue_verified('brand',self.event(event_id='evt_after_invalid'))
        self.assertEqual(self.reducer.process_one('brand')['result']['reviewed'][0]['state'],'ready')
    def test_wrong_actor_and_old_revision_never_approve(self):
        with self.assertRaisesRegex(RuntimeFault,'ROLE_DENIED'):
            self.reducer.consume_verified('brand',self.event(operator='ou_outsider'))
        self.remote['审核目标版本']='0'
        with self.assertRaisesRegex(RuntimeFault,'REMOTE_CHANGED'):
            self.reducer.consume_verified('brand',self.event())
        self.assertEqual(self.store.inspect_task(self.admin,'brand','task_one')['versions'][0]['state'],'awaiting_script_review')
    def test_unauthorized_event_is_denied_without_blocking_next_one(self):
        self.reducer.enqueue_verified('brand',self.event(operator='ou_outsider',event_id='evt_outsider'))
        self.assertEqual(self.reducer.process_one('brand')['status'],'denied')
        self.reducer.enqueue_verified('brand',self.event(event_id='evt_authorized'))
        self.assertEqual(self.reducer.process_one('brand')['result']['reviewed'][0]['state'],'ready')
    def test_unbound_row_event_does_not_block_bound_row(self):
        unrelated=self.event(event_id='evt_unbound')
        unrelated['event']['action_list'][0]['record_id']='recOther'
        self.reducer.enqueue_verified('brand',unrelated)
        self.assertEqual(self.reducer.process_one('brand')['reason'],'FEISHU_REVIEW_SOURCE_NOT_BOUND')
        self.reducer.enqueue_verified('brand',self.event(event_id='evt_bound'))
        self.assertEqual(self.reducer.process_one('brand')['result']['reviewed'][0]['state'],'ready')
    def test_script_edit_and_forged_transition_never_approve(self):
        self.remote['脚本']='Changed in Base'
        with self.assertRaisesRegex(RuntimeFault,'SCRIPT_CHANGED'):
            self.reducer.consume_verified('brand',self.event())
        self.remote['脚本']='Generated script'
        self.remote['来源版本']='source_two'
        with self.assertRaisesRegex(RuntimeFault,'REMOTE_CHANGED'):
            self.reducer.consume_verified('brand',self.event())
        self.remote['来源版本']='source_one'
        with self.assertRaisesRegex(RuntimeFault,'TRANSITION_INVALID'):
            self.reducer.consume_verified('brand',self.event(before='任意状态'))
        self.assertEqual(self.store.inspect_task(self.admin,'brand','task_one')['versions'][0]['state'],'awaiting_script_review')
    def test_video_review_requires_the_video_stage_receipt_and_attachment(self):
        digest='a'*64
        artifact={'sha256':digest,'location':'/private/fixture.mp4','verification':'full_decode_passed'}
        with self.store.connect() as db:
            db.execute("UPDATE tasks SET state='awaiting_video_review',artifact=? WHERE project='brand' AND id='task_one' AND revision=1",
                       (canonical(artifact),))
            save(db,item_key('brand','task_one',1,'awaiting_video_review'),
                 {'complete':True,'steps':{'finish':{'status':'done','receipt':'fileFixture'}},'snapshot':{}})
        self.remote.update({'状态':'视频通过','视频摘要':digest,'视频':[{'file_token':'fileFixture'}]})
        event=self.event(before='视频待审核',after='视频通过',event_id='evt_video')
        self.assertEqual(self.reducer.consume_verified('brand',event)['reviewed'][0]['state'],'accepted')

    def enable_free_script_plans(self, *, cost_enabled=False):
        self.remote.update({'任务编号':'new_task','来源版本':'input_v1',
                            '生产操作':'申请脚本计划','创作要求':'Show only the stated packaging facts',
                            '生成计划':'','生产提示':'','费用确认':'未确认','状态':''})
        master=Fernet.generate_key()
        self.store.put_secret(self.admin,'script','synthetic-script-key',master)
        with self.store.connect() as db:
            save(db,'setup:project:brand',{'configuration':{'project':{'products':[
                {'sku_id':'sku_one','name':'Synthetic product','truth_source':'Synthetic package facts'}]}}})
            save(db,'script:profile:brand',{'model':'deepseek-chat','credential_ref':'secret:script',
                                            'credential_revision':1,'billing_owner':'fixture_account'})
            save(db,'production:config:brand',{
                'enabled':True,'cost_enabled':cost_enabled,'cost_approvers':['ou_approver'],
                'app_id':'cli_fixture','tenant_key':'fixture_tenant',
                'base_token':'bascnFixture','table_id':'tblFixture',
                'fields':{'operation':'fldOperation','cost':'fldCost'},
                'names':{'operation':'生产操作','brief':'创作要求','plan':'生成计划',
                         'hint':'生产提示','cost':'费用确认'}})

    def production_event(self, *, operator='ou_submitter', event_id='evt_plan'):
        event=self.event(operator=operator,event_id=event_id)
        action=event['event']['action_list'][0]
        action['before_value']=[{'field_id':'fldOperation','field_value':''}]
        action['after_value']=[{'field_id':'fldOperation','field_value':'optRequestScript'}]
        return event

    def cost_event(self, *, operator='ou_approver', event_id='evt_cost'):
        event=self.event(operator=operator,event_id=event_id)
        action=event['event']['action_list'][0]
        action['before_value']=[{'field_id':'fldCost','field_value':'optNotConfirmed'}]
        action['after_value']=[{'field_id':'fldCost','field_value':'optCostConfirm'}]
        return event

    def test_verified_request_prepares_one_zero_cost_plan_and_replays(self):
        self.enable_free_script_plans()
        event=self.production_event()
        self.reducer.enqueue_verified('brand',event)
        result=self.reducer.process_one('brand')['result']
        self.assertEqual(result['provider_requests'],0)
        self.assertEqual(result['plans'][0]['status'],'plan_ready')
        self.assertEqual(self.writes,1)
        self.assertIn('费用确认尚未启用',self.remote['生成计划'])
        with self.store.connect() as db:
            from video_factory.feishu_bridge import meta
            saved=meta(db,plan_key('brand','recFixture'))
            self.assertEqual(saved['state'],'ready')
            self.assertIsNone(db.execute("SELECT id FROM tasks WHERE id='new_task'").fetchone())
        self.assertTrue(self.reducer.enqueue_verified('brand',event)['replayed'])
        self.assertEqual(self.reducer.process_one('brand')['status'],'idle')
        self.assertEqual(self.writes,1)

    def test_plan_requires_submitter_and_refuses_changed_input(self):
        self.enable_free_script_plans()
        self.reducer.enqueue_verified('brand',self.production_event(operator='ou_outsider',event_id='evt_outsider'))
        self.assertEqual(self.reducer.process_one('brand')['status'],'denied')
        self.assertEqual(self.writes,0)
        self.reducer.enqueue_verified('brand',self.production_event())
        self.reducer.process_one('brand')
        self.remote['创作要求']='Changed after plan'
        self.reducer.enqueue_verified('brand',self.production_event(event_id='evt_changed'))
        self.assertEqual(self.reducer.process_one('brand')['reason'],'PRODUCTION_PLAN_REQUIRES_RECONCILIATION')
        self.assertEqual(self.writes,1)

    def test_confirmation_before_plan_is_denied_without_charging_or_resetting_cell(self):
        self.enable_free_script_plans(cost_enabled=True)
        self.remote['费用确认']='确认此计划的一次生成'
        self.reducer.enqueue_verified('brand',self.production_event())
        self.assertEqual(self.reducer.process_one('brand')['reason'],'PRODUCTION_COST_PREMATURE')
        self.assertEqual(self.remote['费用确认'],'确认此计划的一次生成')
        self.assertEqual(self.writes,0)
        with self.store.connect() as db:
            self.assertIsNone(db.execute("SELECT id FROM tasks WHERE id='new_task'").fetchone())

    def test_lost_write_response_requires_readback_and_does_not_repeat_write(self):
        self.enable_free_script_plans()
        self.lose_write_response=True
        self.reducer.enqueue_verified('brand',self.production_event())
        with self.assertRaisesRegex(RuntimeFault,'PRODUCTION_PLAN_WRITE_UNKNOWN'):
            self.reducer.process_one('brand')
        self.lose_write_response=False
        self.assertEqual(self.reducer.process_one('brand')['result']['plans'][0]['status'],'plan_ready')
        self.assertEqual(self.writes,1)

    def test_cost_event_queues_one_job_and_replay_does_not_requeue(self):
        self.enable_free_script_plans(cost_enabled=True)
        self.reducer.enqueue_verified('brand',self.production_event())
        self.assertEqual(self.reducer.process_one('brand')['result']['plans'][0]['status'],'plan_ready')
        self.remote['费用确认']='确认此计划的一次生成'
        event=self.cost_event()
        self.reducer.enqueue_verified('brand',event)
        result=self.reducer.process_one('brand')['result']
        self.assertEqual(result['approvals'][0]['state'],'script_queued')
        self.assertEqual(result['provider_requests'],0)
        with self.store.connect() as db:
            from video_factory.feishu_bridge import meta
            self.assertEqual(meta(db,plan_key('brand','recFixture'))['state'],'confirmed')
            job=meta(db,script_key('brand','new_task',1))
            self.assertEqual(job['state'],'queued')
            self.assertEqual(db.execute("SELECT count(*) FROM tasks WHERE project='brand' AND id='new_task'").fetchone()[0],1)
            check_production_gate(db,job['plan'])
        # The event may be processed again after the task was committed but
        # before its queue receipt was marked done.
        self.assertTrue(self.reducer.consume_verified('brand',event)['approvals'][0]['replayed'])
        self.assertTrue(self.reducer.enqueue_verified('brand',event)['replayed'])

    def test_cost_approval_rejects_wrong_actor_changed_input_and_revocation(self):
        self.enable_free_script_plans(cost_enabled=True)
        self.reducer.enqueue_verified('brand',self.production_event())
        self.reducer.process_one('brand')
        self.remote['费用确认']='确认此计划的一次生成'
        self.reducer.enqueue_verified('brand',self.cost_event(operator='ou_outsider',event_id='evt_wrong'))
        self.assertEqual(self.reducer.process_one('brand')['reason'],'PRODUCTION_COST_APPROVER_DENIED')
        self.remote['创作要求']='Changed after cost plan'
        self.reducer.enqueue_verified('brand',self.cost_event(event_id='evt_changed'))
        self.assertEqual(self.reducer.process_one('brand')['reason'],'PRODUCTION_PLAN_REMOTE_CHANGED')
        self.remote['创作要求']='Show only the stated packaging facts'
        self.reducer.enqueue_verified('brand',self.cost_event())
        self.reducer.process_one('brand')
        with self.store.connect() as db:
            from video_factory.feishu_bridge import meta
            job=meta(db,script_key('brand','new_task',1))
            policy=meta(db,'production:config:brand')
            policy['cost_approvers']=[]
            save(db,'production:config:brand',policy)
            with self.assertRaisesRegex(RuntimeFault,'PRODUCTION_APPROVAL_CHANGED'):
                check_production_gate(db,job['plan'])

    def test_closed_revision_can_request_new_plan_on_same_original_row(self):
        self.enable_free_script_plans(cost_enabled=True)
        self.reducer.enqueue_verified('brand',self.production_event())
        self.reducer.process_one('brand')
        self.remote['费用确认']='确认此计划的一次生成'
        self.reducer.enqueue_verified('brand',self.cost_event())
        self.reducer.process_one('brand')
        old_summary=self.remote['生成计划']
        with self.store.connect() as db:
            db.execute("UPDATE tasks SET state='awaiting_script_review' WHERE project='brand' AND id='new_task' AND revision=1")
            self.store._review(db,'feishu:fixture_tenant:ou_reviewer','evt_revision_reject',
                               'brand','new_task',1,'script','reject','Change the product description')
        self.remote.update({'状态':'脚本退回','来源版本':'input_v2',
                            '创作要求':'Use the revised product description',
                            '费用确认':'未确认'})
        self.reducer.enqueue_verified('brand',self.production_event(event_id='evt_plan_v2'))
        self.assertEqual(self.reducer.process_one('brand')['result']['plans'][0]['status'],'plan_ready')
        self.assertNotEqual(self.remote['生成计划'],old_summary)
        self.assertIn('版本 2',self.remote['生成计划'])
        self.remote['费用确认']='确认此计划的一次生成'
        self.reducer.enqueue_verified('brand',self.cost_event(event_id='evt_cost_v2'))
        self.assertEqual(self.reducer.process_one('brand')['result']['approvals'][0]['revision'],2)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM tasks WHERE project='brand' AND id='new_task'").fetchone()[0],2)
            from video_factory.feishu_bridge import meta
            self.assertIn('Change the product description',
                          meta(db,script_key('brand','new_task',2))['plan']['feedback'])


if __name__=='__main__':unittest.main()
