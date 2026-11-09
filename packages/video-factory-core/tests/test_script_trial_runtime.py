import copy
import time
import unittest
from unittest.mock import patch, Mock
import test_script_jobs as script_tests
from review_fixture import BINDING
from video_factory.feishu_bridge import meta, save
from video_factory.runtime_store import RuntimeFault
from video_factory.script_trial import configure,activate,PREFIX
from video_factory.script_trial_runtime import TrialCopyReview, TrialRunner, window

class Client:
    def __init__(self):
        self.data={'task':'generated_one','input_digest':'a'*64,'source_revision':'source-v1',
                   'status':'未开始','baseline':'untouched','score':'','feedback':''}
        self.writes=[]
    def record(self,*args):return {'fields':copy.deepcopy(self.data)}
    def fields(self,*args):return [{'field_id':'fldStatus','type':3,'property':{'options':[
        {'id':'optPending','name':'脚本待审核'},{'id':'optAccept','name':'脚本通过'},{'id':'optReject','name':'脚本退回'}]}}]
    def update_record(self,base,table,record,fields):
        assert base==BINDING['base_token'] and table==BINDING['table_id']
        self.writes.append(copy.deepcopy(fields)); self.data.update(fields)

class TrialRuntimeTests(unittest.TestCase):
    setUp=script_tests.ScriptTests.setUp
    tearDown=script_tests.ScriptTests.tearDown
    queue=script_tests.ScriptTests.queue
    step=script_tests.ScriptTests.step
    def configure(self):
        configure(self.f.store,self.f.admin,self.f.project,production_base='bascnProduction',copy_base=BINDING['base_token'],copy_table=BINDING['table_id'])
        with self.f.store.connect() as db:save(db,'setup:feishu-app:'+self.f.project,{'app_id':'cli_fixture'})
        self.client=Client();self.review=TrialCopyReview(self.f.store,self.f.project,self.client)
        self.names={k:k for k in ['task','input_digest','source_revision','candidate_version','video_script','caption_voiceover_script','generation_prompt','status','feedback']}
        self.review.configure(self.f.admin,app_id='cli_fixture',names=self.names,fields={'status':'fldStatus'})
    def start(self):
        now=time.time()
        with patch('video_factory.script_trial.time.time',return_value=now):
            activate(self.f.store,self.f.admin,self.f.project,t0=now,notice_sha256='a'*64)
        self.t0=now
    def draft(self):
        self.configure();self.start();self.queue()
        result={'script':'draft','structured_fields':{'video_script':'draft','caption_voiceover_script':'voice','generation_prompt':'prompt'},
                'provider_id':'synthetic','usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20},
                'prepared_input_sha256':'a'*64,'prepared_source_revision':'source-v1'}
        with patch.object(self.provider,'generate',return_value=result):self.step()
        self.review.publish('generated_one',1,'recFixture')
    def event(self,decision='optAccept',actor='ou_employee'):
        return {'schema':'2.0','header':{'app_id':'cli_fixture','tenant_key':'fixture_tenant','event_type':'drive.file.bitable_record_changed_v1','event_id':'eventSynthetic'},
                'event':{'file_type':'bitable','file_token':BINDING['base_token'],'table_id':BINDING['table_id'],
                         'operator_id':{'open_id':actor},'action_list':[{'record_id':'recFixture','action':'record_edited',
                         'before_value':[{'field_id':'fldStatus','field_value':'optPending'}],
                         'after_value':[{'field_id':'fldStatus','field_value':decision}]}]}}
    def test_human_event_readback_commits_once_without_model_or_video(self):
        self.draft();self.client.data['status']='脚本通过'
        e=self.event();self.review.enqueue_verified(e)
        result=self.review.process_one();self.assertEqual(result['reviewed'][0]['state'],'ready')
        self.assertTrue(self.review.enqueue_verified(e)['replayed'])
        self.assertEqual(self.review.process_one()['status'],'idle')
        self.assertEqual(self.provider.calls,0)
        self.assertEqual(self.client.data['baseline'],'untouched');self.assertEqual(self.client.data['score'],'')
    def test_wrong_person_denied_even_if_cell_says_passed(self):
        self.draft();self.client.data['status']='脚本通过';self.review.enqueue_verified(self.event(actor='ou_other'))
        self.assertEqual(self.review.process_one()['status'],'denied')
        with self.f.store.connect() as db:self.assertEqual(self.f.store._current(db,self.f.project,'generated_one',1)['state'],'awaiting_script_review')
    def test_application_writes_cannot_supply_human_identity(self):
        self.configure();e=self.event();e['event']['operator_id']={}
        self.assertEqual(self.review.enqueue_verified(e)['status'],'ignored_nonhuman')
    def test_wrong_base_rejected_before_queue(self):
        self.configure();e=self.event();e['event']['file_token']='bascnProduction'
        with self.assertRaisesRegex(RuntimeFault,'SCOPE_DENIED'):self.review.enqueue_verified(e)
    def test_old_version_and_edited_script_cannot_approve(self):
        for field in ('candidate_version','video_script','input_digest','source_revision'):
            with self.subTest(field=field):
                if not hasattr(self,'review'):self.draft()
                previous=self.client.data[field];self.client.data[field]='changed';self.client.data['status']='脚本通过'
                e=self.event();e['header']['event_id']='event'+field.replace('_','')
                self.review.enqueue_verified(e);self.assertEqual(self.review.process_one()['status'],'denied')
                self.client.data[field]=previous
    def test_reject_requires_feedback_and_preserves_draft(self):
        self.draft();self.client.data['status']='脚本退回';e=self.event('optReject')
        self.review.enqueue_verified(e);self.assertEqual(self.review.process_one()['reason'],'TRIAL_REVIEW_FEEDBACK_REQUIRED')
        self.client.data['feedback']='Make the pack shot clear';e['header']['event_id']='eventSecond'
        self.review.enqueue_verified(e);self.assertEqual(self.review.process_one()['reviewed'][0]['state'],'rejected')
        self.assertEqual(self.client.data['video_script'],'draft')
    def test_delayed_event_remote_state_changed_is_denied(self):
        self.draft();self.review.enqueue_verified(self.event())
        self.assertEqual(self.review.process_one()['reason'],'TRIAL_REVIEW_STALE')
    def test_projection_restart_does_not_overwrite_human_decision(self):
        self.draft();self.client.data['status']='脚本通过';n=len(self.client.writes)
        TrialCopyReview(self.f.store,self.f.project,self.client).publish('generated_one',1,'recFixture')
        self.assertEqual(len(self.client.writes),n)
    def runner(self,jobs=None):
        return TrialRunner(self.f.store,self.f.project,jobs or self.jobs,self.review,{'generated_one':'recFixture'},token=self.cap,master_key=self.key)
    def test_unstarted_does_no_network_or_dispatch_across_restarts(self):
        self.configure();self.queue()
        for _ in range(2):self.assertEqual(self.runner().tick()['state'],'paused')
        self.assertEqual(self.provider.calls,0);self.assertEqual(self.client.writes,[])
    def test_expiry_is_durable_and_never_catches_up(self):
        self.configure();self.start();self.queue()
        with patch('video_factory.script_trial_runtime.time.time',return_value=self.t0+86400):
            self.assertEqual(self.runner().tick()['state'],'expired')
        with self.f.store.connect() as db:self.assertFalse(meta(db,PREFIX+self.f.project)['enabled'])
        self.assertEqual(self.runner().tick()['state'],'paused');self.assertEqual(self.provider.calls,0)
    def test_expired_per_task_approval_cannot_be_extended_by_scheduler(self):
        self.configure();self.start();self.queue()
        with patch('video_factory.script_jobs.time.time',return_value=time.time()+3601):
            with self.assertRaisesRegex(RuntimeFault,'APPROVAL_EXPIRED'):self.runner().tick()
        self.assertEqual(self.provider.calls,0)
    def test_unknown_result_stops_and_is_not_retried(self):
        self.configure();self.start();self.queue();self.provider.fail=True
        self.runner().tick();self.assertEqual(self.runner().tick()['state'],'attention_required')
        self.assertEqual(self.provider.calls,1)
    def test_first_draft_requires_real_review_before_next_dispatch(self):
        self.draft();jobs=Mock()
        self.assertEqual(self.runner(jobs).tick()['state'],'awaiting_review');jobs.step.assert_not_called()
    def test_production_configuration_is_rejected(self):
        self.configure()
        with self.f.store.connect() as db:
            trial=meta(db,PREFIX+self.f.project);trial['copy_base']='bascnProduction';save(db,PREFIX+self.f.project,trial)
        with self.assertRaisesRegex(RuntimeFault,'CONFIGURATION_CHANGED'):self.review.publish('generated_one',1,'recFixture')

    def test_old_rejected_revision_cannot_block_latest_ready_revision(self):
        self.configure();self.start();self.queue()
        with self.f.store.connect() as db:
            db.execute("UPDATE tasks SET state='rejected' WHERE id='generated_one'")
            db.execute("INSERT INTO tasks SELECT project,id,revision+1,input,input_digest,configuration_digest,'ready',provider_id,artifact FROM tasks WHERE id='generated_one'")
        jobs=Mock();self.assertEqual(self.runner(jobs).tick()['state'],'idle');jobs.step.assert_not_called()

    def test_application_token_renews_without_retrying_failed_writes(self):
        from video_factory.script_trial_runtime import RefreshingTrialClient
        clock=Mock(return_value=0);first=Mock();second=Mock();factory=Mock(side_effect=[first,second])
        client=RefreshingTrialClient(factory,clock=clock)
        client.record('b','t','r');client.record('b','t','r');self.assertEqual(factory.call_count,1)
        clock.return_value=1200;second.update_record.side_effect=RuntimeFault('UNKNOWN_WRITE')
        with self.assertRaisesRegex(RuntimeFault,'UNKNOWN_WRITE'):client.update_record('b','t','r',{})
        self.assertEqual(factory.call_count,2);second.update_record.assert_called_once()

    def test_no_status_transition_does_not_mislabel_application_as_denied_reviewer(self):
        self.configure();event=self.event('optPending',actor='ou_application')
        self.review.enqueue_verified(event)
        result=self.review.process_one()
        self.assertEqual(result['status'],'processed');self.assertEqual(result['reviewed'],[])
        self.assertEqual(self.provider.calls,0)

    def test_recovery_excludes_only_original_unknown_and_reports_new_failure(self):
        from video_factory.script_trial import pause,resume
        from video_factory.runtime_store import fingerprint
        self.configure();self.start();self.queue();self.provider.fail=True;self.step()
        pause(self.f.store,self.f.admin,self.f.project)
        with self.f.store.connect() as db:state=meta(db,PREFIX+self.f.project)
        now=time.time()
        with patch('video_factory.script_trial.time.time',return_value=now):
            resume(self.f.store,self.f.admin,self.f.project,t0=now,notice_sha256='b'*64,
                   expected_sha256=fingerprint(state),quarantine=list(state['requests']),checkpoint='next_task')
        runner=self.runner();runner.records['next_task']='recNext'
        self.assertEqual(runner.tick()['state'],'idle')
        self.assertEqual(self.provider.calls,1)
        # A new task/revision is never covered by the old quarantine.
        with self.f.store.connect() as db:
            db.execute("INSERT INTO tasks SELECT project,id,revision+1,input,input_digest,configuration_digest,state,provider_id,artifact FROM tasks WHERE id='generated_one'")
        self.assertEqual(runner.tick()['state'],'attention_required')
        self.assertEqual(self.provider.calls,1)
