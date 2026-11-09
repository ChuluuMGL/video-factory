import copy,json,unittest
from unittest.mock import patch
from video_factory.runtime_store import RuntimeFault,RuntimeStore
from video_factory.feishu_bridge import meta,save
from video_factory.script_jobs import key
from video_factory.script_receipts import capture,observe,observe_validation,ValidationFailure
from video_factory.script_decoder_diagnostics import validator_failure
from video_factory.script_trial_dispatch import continuation
from video_factory import script_trial as trial
from test_script_trial import TrialTests
from test_script_trial_dispatch import ContinuationTests
from test_prepared_script_provider import PreparedScriptProviderTests

POLICY={'continue_samples':True,'consecutive_limit':3,'notice_sha256':'c'*64}

class DiagnosticTests(PreparedScriptProviderTests):
    def test_fields_reasons_redacted_content_and_no_retry(self):
        fields={**self.fields,'video_script':''}
        fields['generation_prompt']='secret-key mail@example.com http://10.0.0.1/path 13912345678'
        receipts=[]
        with capture(receipts.append,failure_details=True),patch('video_factory.script_provider.build_opener') as opener:
            opener.return_value.open.return_value=self.response(fields)
            with self.assertRaisesRegex(RuntimeFault,'SCRIPT_VALIDATION_FAILED'):
                self.provider().generate(self.sku,self.ref,'','secret-key')
            self.assertEqual(opener.return_value.open.call_count,1)
        detail=receipts[-1];self.assertEqual(detail['validation']['field'],'video_script')
        self.assertEqual(detail['validation']['code'],'OUTPUT_TEXT_INVALID')
        self.assertIn('generation_prompt',detail['model_content_redacted'])
        for private in ['secret-key','mail@example.com','10.0.0.1','13912345678']:
            self.assertNotIn(private,json.dumps(detail))

    def test_diagnostics_opt_in_and_decoder_runtime_not_swallowed(self):
        receipts=[]
        with capture(receipts.append):observe_validation(ValidationFailure('x','EMPTY','required'),'private body')
        self.assertNotIn('private body',json.dumps(receipts))
        err=validator_failure('file/path source secret\nError: VO_CAPTION_MISMATCH:3\n at stack private')
        self.assertEqual(err.field,'storyboard.shots[shot_no=3].voiceover_en/on_screen_text_en')
        self.assertEqual(err.reason,'3')
        self.assertEqual(validator_failure('SyntaxError: Unexpected token in JSON').code,'INVALID_MODEL_JSON')
        with self.assertRaisesRegex(RuntimeFault,'SCRIPT_DECODER_FAILED'):
            validator_failure('Error: ENOENT private path')

class SettlementTests(TrialTests):
    def enable(self):
        self.arm()
        with self.f.store.connect() as db:
            t=meta(db,trial.PREFIX+self.f.project);t['validation_failure_policy']=POLICY;save(db,trial.PREFIX+self.f.project,t)

    def fail_validation(self):
        observe(reason='RESPONSE_RECEIVED',status=200,elapsed_ms=2,raw=b'synthetic',value={
            'id':'synthetic-id','usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120},
            'choices':[{'finish_reason':'stop'}]})
        observe_validation(ValidationFailure('storyboard.shots','SHOT_COUNT_OUTSIDE_3_TO_8','Expected 3-8'),'synthetic content')
        raise RuntimeFault('SCRIPT_VALIDATION_FAILED')

    def test_failed_response_settled_once_durable_and_next_allowed(self):
        self.enable();self.queue()
        with patch.object(self.provider,'generate',side_effect=lambda *a:self.fail_validation()) as generate:
            result=self.step();self.assertEqual(result['state'],'failed');self.step();self.assertEqual(generate.call_count,1)
        store=RuntimeStore(self.f.store.root)
        with store.connect() as db:
            job=meta(db,key(self.f.project,'generated_one',1));t=meta(db,trial.PREFIX+self.f.project)
            request=t['requests'][job['plan_sha256']]
            self.assertEqual(request['outcome'],'validation_failed');self.assertEqual(request['cost_upper_micro'],360)
            self.assertEqual(job['state'],'validation_failed')
            before=copy.deepcopy(t)
            self.assertTrue(trial.settle_validation_failure(store,db,self.f.project,'generated_one',1))
            self.assertEqual(before,meta(db,trial.PREFIX+self.f.project))
            nextplan={**job['plan'],'task':'next_sample','brief':'distinct synthetic brief'}
            trial.reserve(db,nextplan,BINDING,'next-digest')
        self.assertEqual(len(self.snapshot()['requests']),2)

    def test_legacy_receipt_reclassification_keeps_original_history(self):
        self.arm();self.queue()
        with patch.object(self.provider,'generate',side_effect=lambda *a:self.fail_validation()):self.step()
        with self.f.store.connect() as db:
            t=meta(db,trial.PREFIX+self.f.project);t['validation_failure_policy']=POLICY;save(db,trial.PREFIX+self.f.project,t)
            j=meta(db,key(self.f.project,'generated_one',1));history=meta(db,'script:receipt:'+j['plan_sha256'])
            self.assertTrue(trial.settle_validation_failure(self.f.store,db,self.f.project,'generated_one',1))
            self.assertEqual(history,meta(db,'script:receipt:'+j['plan_sha256']))

    def test_missing_usage_not_forgiven_and_network_not_sample_failure(self):
        self.enable();self.queue()
        def bad(*args):
            observe(reason='RESPONSE_RECEIVED',status=200,value={'id':'synthetic','choices':[{'finish_reason':'stop'}]})
            observe(reason='STRUCTURE_INVALID');raise RuntimeFault('SCRIPT_VALIDATION_FAILED')
        with patch.object(self.provider,'generate',side_effect=bad):self.step()
        self.assertFalse(self.snapshot()['enabled'])
        self.assertEqual(next(iter(self.snapshot()['requests'].values()))['state'],'usage_unknown')
        self.assertEqual(next(iter(self.snapshot()['requests'].values()))['cost_upper_micro'],trial.RESERVE)

    def test_three_failures_fence_reservation_not_just_dispatcher(self):
        self.enable();self.queue()
        with self.f.store.connect() as db:
            j=meta(db,key(self.f.project,'generated_one',1));t=meta(db,trial.PREFIX+self.f.project)
            t['requests']={str(i):{'state':'usage_accounted','outcome':'validation_failed','submitted_at':i,'input_digest':str(i),'cost_upper_micro':360} for i in range(3)}
            save(db,trial.PREFIX+self.f.project,t)
            with self.assertRaisesRegex(RuntimeFault,'CONSECUTIVE_FAILURE_LIMIT'):trial.reserve(db,j['plan'],BINDING,'new')

from review_fixture import BINDING

class PolicyDispatchTests(ContinuationTests):
    def setUp(self):
        super().setUp();self.trial['validation_failure_policy']=POLICY
    def fail(self,task,at):
        self.add(task,'failed','validation_failed','usage_accounted')
        self.jobs[task].update(submission_error='SCRIPT_VALIDATION_FAILED',failure_receipt_sha256='f'*64)
        self.trial['requests'][task+'_digest'].update(outcome='validation_failed',submitted_at=at)
    def test_two_failed_samples_continue_three_stop_after_restart(self):
        self.plans+=['sample_five','sample_six']
        self.fail('sample_three',996);self.assertEqual(self.decide()['task'],'sample_four')
        self.fail('sample_four',997);self.assertEqual(self.decide()['task'],'sample_five')
        self.fail('sample_five',998)
        with self.assertRaisesRegex(RuntimeFault,'CONSECUTIVE_FAILURE_LIMIT'):self.decide()
        with self.assertRaisesRegex(RuntimeFault,'CONSECUTIVE_FAILURE_LIMIT'):
            continuation(copy.deepcopy(self.trial),copy.deepcopy(self.rows),copy.deepcopy(self.jobs),self.plans,now=self.now)
    def test_success_resets_streak_and_failed_sample_never_selected(self):
        self.plans+=['sample_five','sample_six']
        self.fail('sample_three',996)
        self.add('sample_four','awaiting_script_review','received','usage_accounted')
        self.trial['requests']['sample_four_digest']['submitted_at']=997
        self.fail('sample_five',998)
        self.assertEqual(trial.consecutive_validation_failures(self.trial),1)
        self.assertEqual(self.decide()['task'],'sample_six')
    def test_failure_without_accounting_or_receipt_does_not_continue(self):
        self.fail('sample_three',996);self.jobs['sample_three'].pop('failure_receipt_sha256')
        with self.assertRaisesRegex(RuntimeFault,'USAGE_REQUIRES_INSPECTION'):self.decide()

from test_script_trial_runtime import TrialRuntimeTests,Client
class FailureProjectionTests(TrialRuntimeTests):
    def test_settled_failure_writes_only_status_preserves_review_and_baseline(self):
        self.configure();self.start();self.queue()
        with self.f.store.connect() as db:
            t=meta(db,trial.PREFIX+self.f.project);t['validation_failure_policy']=POLICY;save(db,trial.PREFIX+self.f.project,t)
        with patch.object(self.provider,'generate',side_effect=lambda *args:SettlementTests.fail_validation(self)):
            self.step()
        fields=self.client.fields()
        fields[0]['property']['options'].append({'id':'optFailed','name':'失败待处理'})
        self.client.fields=lambda *args:fields
        before=copy.deepcopy(self.client.data)
        self.assertEqual(self.runner().tick()['state'],'idle')
        self.assertEqual(self.client.writes,[{'status':'失败待处理'}])
        self.assertEqual(self.client.data,{**before,'status':'失败待处理'})
        self.runner().tick();self.assertEqual(len(self.client.writes),1)
        self.client.data['status']='脚本通过'
        with self.assertRaisesRegex(RuntimeFault,'FAILURE_STATUS_CONFLICT'):self.runner().tick()
