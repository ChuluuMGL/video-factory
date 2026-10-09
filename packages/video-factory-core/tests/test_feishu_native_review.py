"""Cloud-only synthetic tests for Base-native human review trust boundaries."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from video_factory.feishu_bridge import save
from video_factory.feishu_native_review import NativeReview
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
        return [{'field_id':'fldStatus','field_name':'状态','type':3,
                 'property':{'options':[{'id':key,'name':name} for key,name in (
                     ('optPending','脚本待审核'),('optApproved','脚本通过'),
                     ('optRejected','脚本退回'),('optVideoPending','视频待审核'),
                     ('optVideoApproved','视频通过'),('optVideoRejected','视频退回'))]}}]
    def record(self,base,table,record):
        assert (base,table,record)==('bascnFixture','tblFixture','recFixture')
        return {'record_id':record,'fields':dict(self.fixture.remote)}


class NativeReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.store=RuntimeStore.install(Path(self.tmp.name),'fixture','fixture-admin-password')
        self.admin=self.store.login('admin','fixture-admin-password')['token']
        self.store.put_project(self.admin,'brand',{'video_route':'deferred','credential_ref':'secret:fixture','billing_owner':'fixture'})
        self.store.create_task(self.admin,'brand','task_one',{'sku_id':'sku_one','script':'Generated script','source_revision':'source_one'})
        self.remote={'任务编号':'task_one','SKU':'sku_one','来源版本':'source_one',
                     '脚本':'Generated script','脚本摘要':hashlib.sha256(b'Generated script').hexdigest(),
                     '状态':'脚本通过','审核目标版本':'1','审核意见':''}
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


if __name__=='__main__':unittest.main()
