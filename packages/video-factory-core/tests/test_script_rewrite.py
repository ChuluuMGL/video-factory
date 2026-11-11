import copy
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from test_script_validation_policy import SettlementTests
from test_prepared_script_provider import PreparedScriptProviderTests
from video_factory.script_jobs import profile, key
from video_factory.script_trial import PREFIX, CAP, consecutive_validation_failures
from video_factory.feishu_bridge import meta,save
from video_factory.runtime_store import RuntimeFault,RuntimeStore
from video_factory.script_receipts import observe,observe_validation,ValidationFailure

class RewriteTests(SettlementTests):
    def enable_rewrite(self):
        self.enable()
        profile(self.f.store,self.f.admin,self.f.project,'secret:script','customer_account',max_validation_rewrites=1)
        self.provider.validate_rewrite=lambda *a:None
        def rewrite(*args):
            self.provider.calls+=1
            return {'script':'Corrected synthetic draft','provider_id':'second',
                    'usage':{'prompt_tokens':200,'completion_tokens':20,'total_tokens':220}}
        self.provider.rewrite=rewrite
    def first_failure(self):
        self.enable_rewrite();self.queue()
        with patch.object(self.provider,'generate',side_effect=lambda *a:self.fail_validation()) as gen:
            out=self.step();self.assertTrue(out['rewrite_queued']);self.assertEqual(gen.call_count,1)
        with self.f.store.connect() as db:return meta(db,key(self.f.project,'generated_one',1))
    def test_once_durable_independent_accounting_and_no_third_call(self):
        first=self.first_failure()
        self.assertEqual(first['plan']['max_submissions'],2)
        self.assertNotEqual(first['request_id'],first['plan_sha256'])
        self.assertEqual(first['rewrite_context']['validation']['code'],'SHOT_COUNT_OUTSIDE_3_TO_8')
        self.assertEqual(first['rewrite_context']['model_content_redacted'],'synthetic content')
        self.jobs.store=RuntimeStore(self.f.store.root)
        with self.f.store.connect() as db:history=copy.deepcopy(meta(db,'script:receipt:'+first['plan_sha256']))
        self.assertEqual(self.step()['state'],'awaiting_script_review');self.step()
        rows=self.snapshot()['requests'];self.assertEqual(len(rows),2)
        self.assertEqual(sum(x['cost_upper_micro'] for x in rows.values()),920)
        self.assertEqual(self.provider.calls,1)
        with self.f.store.connect() as db:self.assertEqual(history,meta(db,'script:receipt:'+first['plan_sha256']))
    def test_second_validation_failure_final_no_retry_and_one_failed_sample(self):
        self.first_failure()
        with patch.object(self.provider,'rewrite',side_effect=lambda *a:self.fail_validation()) as rw:
            out=self.step();self.assertEqual(out['state'],'failed');self.assertFalse(out['automatic_resubmit'])
            self.step();self.assertEqual(rw.call_count,1)
        self.assertEqual(len(self.snapshot()['requests']),2)
        self.assertEqual(consecutive_validation_failures(self.snapshot()),1)
    def test_network_auth_truncation_evidence_and_unknown_are_not_rewrite_triggers(self):
        for code in ('SCRIPT_NETWORK_FAILED','SCRIPT_AUTH_REJECTED','SCRIPT_MODEL_TRUNCATED','SCRIPT_SUBMISSION_UNCERTAIN_NO_RETRY','SCRIPT_DECODER_FAILED'):
            with self.subTest(code=code):
                # separate fixture per failure to avoid resetting an uncertain intent
                self.tearDown();self.setUp();self.enable_rewrite();self.queue()
                with patch.object(self.provider,'generate',side_effect=RuntimeFault(code)),patch.object(self.provider,'rewrite') as rw:
                    self.assertFalse(self.step()['automatic_resubmit']);self.step();rw.assert_not_called()
                self.assertEqual(len(self.snapshot()['requests']),1)
    def test_second_attempt_ttl_auth_binding_and_budget_are_rechecked(self):
        for kind,error in [('ttl','APPROVAL_EXPIRED'),('auth','AUTH_EXECUTION'),('binding','PLAN_CHANGED'),('budget','BUDGET_LIMIT'),('count','REQUEST_LIMIT')]:
            with self.subTest(kind=kind):
                self.tearDown();self.setUp();first=self.first_failure()
                with self.f.store.connect() as db:
                    if kind=='ttl':
                        first['expires_at']=0;save(db,key(self.f.project,'generated_one',1),first)
                    elif kind=='auth':self.f.store.invalidate_worker_approvals(db)
                    elif kind=='binding':
                        b=meta(db,'feishu:binding:'+self.f.project);b['table_id']='tblChanged';save(db,'feishu:binding:'+self.f.project,b)
                    else:
                        t=meta(db,PREFIX+self.f.project)
                        if kind=='budget':t['requests'][first['plan_sha256']]['cost_upper_micro']=CAP
                        else:
                            for i in range(29):t['requests']['historic'+str(i)]={'state':'usage_accounted','input_digest':str(i),'submitted_at':i,'cost_upper_micro':1}
                        save(db,PREFIX+self.f.project,t)
                with patch.object(self.provider,'rewrite') as rw:
                    with self.assertRaisesRegex(RuntimeFault,error):self.step()
                    rw.assert_not_called()
    def test_concurrent_rewrite_is_one_request(self):
        self.first_failure()
        def run(_):
            try:return self.step()
            except RuntimeFault:return None
        with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(run,range(4)))
        self.assertEqual(self.provider.calls,1);self.assertEqual(len(self.snapshot()['requests']),2)
    def test_changed_retry_context_and_parent_receipt_rejected(self):
        job=self.first_failure()
        with self.f.store.connect() as db:
            job['rewrite_context']['validation']['reason']='changed';save(db,key(self.f.project,'generated_one',1),job)
        with self.assertRaisesRegex(RuntimeFault,'REWRITE_CONTEXT_CHANGED'):self.step()
        self.assertEqual(self.provider.calls,0)
    def test_input_limit_preserves_failure_no_second_request(self):
        self.enable_rewrite();self.queue()
        with patch.object(self.provider,'generate',side_effect=lambda *a:self.fail_validation()),patch.object(self.provider,'validate_rewrite',side_effect=RuntimeFault('SCRIPT_REWRITE_INPUT_LIMIT')):
            self.assertEqual(self.step()['state'],'failed')
        self.assertEqual(len(self.snapshot()['requests']),1)

class RewriteProviderTests(PreparedScriptProviderTests):
    def context(self):return {'validation':{'field':'storyboard.shots[0]','code':'WORD_LIMIT','reason':'16 words exceeds 10'},'model_content_redacted':'{"synthetic":"old draft"}','parent_request_id':'a'*64}
    def test_exact_error_prior_output_and_original_constraints_sent_once(self):
        context=self.context();before=copy.deepcopy(self.sample)
        with patch('video_factory.script_provider.build_opener') as opener:
            opener.return_value.open.return_value=self.response()
            self.provider().rewrite(self.sku,self.ref,'','synthetic-key',context)
            import json
            body=json.loads(opener.return_value.open.call_args.args[0].data)
            self.assertEqual(body['messages'][:-1],before['messages']);self.assertEqual(self.sample,before)
            self.assertIn('16 words exceeds 10',body['messages'][-1]['content'])
            self.assertIn('old draft',body['messages'][-1]['content'])
            self.assertEqual(opener.return_value.open.call_count,1)
    def test_rewrite_cannot_truncate_original_constraints_to_fit(self):
        context=self.context();context['model_content_redacted']='x'*33000
        with patch('video_factory.script_provider.build_opener') as opener:
            with self.assertRaisesRegex(RuntimeFault,'REWRITE_INPUT_LIMIT'):
                self.provider().rewrite(self.sku,self.ref,'','synthetic-key',context)
            opener.assert_not_called()

class RewriteContinuationTests(RewriteTests):
    def test_success_does_not_block_next_sample_and_dispatch_recognizes_two_receipts(self):
        from video_factory.script_trial_dispatch import continuation
        self.first_failure();self.step()
        self.review()
        with self.f.store.connect() as db:
            t=meta(db,PREFIX+self.f.project);t['checkpoint_task']='generated_one'
            job=meta(db,key(self.f.project,'generated_one',1))
            row=dict(self.f.store._current(db,self.f.project,'generated_one',1))
        import time
        self.assertEqual(continuation(t,[row],{'generated_one':job},['generated_one','next_sample'],now=time.time())['state'],'prepare_next')
        self.args.update(task='next_sample',brief='A distinct new task')
        p=self.jobs.prepare('synthetic-user-token',**self.args)
        self.jobs.submit('synthetic-user-token',p['plan_sha256'],**self.args)
        self.jobs.step(self.cap,self.f.project,'next_sample',1,self.key)
        self.assertEqual(len(self.snapshot()['requests']),3)
    def test_evidence_decision_is_final_not_an_invented_fact_rewrite(self):
        self.enable_rewrite();self.queue()
        with patch.object(self.provider,'generate',return_value={'decision':'needs_evidence','missing_facts':['Unknown shade'],
            'provider_id':'evidence','usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120}}),patch.object(self.provider,'rewrite') as rw:
            self.assertEqual(self.step()['error'],'SCRIPT_EVIDENCE_REQUIRED');self.step();rw.assert_not_called()
        self.assertEqual(len(self.snapshot()['requests']),1)
    def test_rewrite_transport_failure_keeps_second_intent_unknown_and_never_third(self):
        self.first_failure()
        with patch.object(self.provider,'rewrite',side_effect=RuntimeFault('SCRIPT_NETWORK_FAILED')) as rw:
            self.assertEqual(self.step()['state'],'script_submission_unknown');self.step();self.assertEqual(rw.call_count,1)
        self.assertEqual(len(self.snapshot()['requests']),2)
    def test_old_plan_cannot_gain_retry_by_profile_change(self):
        self.enable();self.queue()
        profile(self.f.store,self.f.admin,self.f.project,'secret:script','customer_account',max_validation_rewrites=1)
        with self.assertRaisesRegex(RuntimeFault,'SCRIPT_PLAN_CHANGED'):self.step()
        self.assertEqual(len(self.snapshot()['requests']),0)
