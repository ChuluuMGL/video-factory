import copy
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from test_script_jobs import ScriptTests
from video_factory.feishu_bridge import meta, save
from video_factory.runtime_store import RuntimeFault, fingerprint
from video_factory.script_jobs import profile, key
from video_factory import script_week as week
from video_factory.script_week_archive import review_export, save_archive, OUTPUTS


class WeekTests(unittest.TestCase):
    setUp = ScriptTests.setUp
    tearDown = ScriptTests.tearDown

    def prepare(self):
        profile(self.f.store, self.f.admin, self.f.project, 'secret:script', 'customer_account', max_validation_rewrites=1)
        return week.prepare(self.f.store, self.f.admin, self.f.project,
            source_base='bascnProductionFixture', source_table='tblProductionFixture',
            copy_base='bascnFixture', copy_table='tblFixture',
            claims_sha256='a'*64, policy_sha256='b'*64, source_readonly_evidence_sha256='c'*64,
            history_exposure_micro=869806, notice_sha256='d'*64)

    def start(self):
        prepared = self.prepare(); self.t0 = time.time()
        with patch('video_factory.script_week.time.time', return_value=self.t0):
            result = week.activate(self.f.store, self.f.admin, self.f.project,
                expected_contract=prepared['contract_sha256'], t0=self.t0, start_notice_sha256='e'*64)
        self.token = result['token']
        return result

    def snapshot(self, n=1):
        value = {'source_base':'bascnProductionFixture', 'source_table':'tblProductionFixture',
                 'record_id':'recExample'+str(n), 'source_version':'v1', 'eligible_at':self.t0,
                 'sku_id':'sku_one', 'brief':'Explain the package '+str(n), 'input_summary':'Approved box facts only'}
        return {**value, 'input_sha256':fingerprint(value)}

    def queue(self, n=1):return week.queue_snapshot(self.jobs, self.token, self.f.project, self.snapshot(n))

    def generate(self, task):
        result = {'script':'Safe fixture draft','provider_id':'receipt-'+task,
                  'usage':{'prompt_tokens':100,'completion_tokens':50,'total_tokens':150},
                  'structured_fields':dict.fromkeys(OUTPUTS,'Safe fixture output')}
        with patch.object(self.provider,'generate',return_value=result):
            return self.jobs.step(self.token,self.f.project,task,1,self.key)

    def state(self):
        with self.f.store.connect() as db:
            return meta(db,week.PREFIX+self.f.project), meta(db,'script:trial:'+self.f.project)

    def test_prepare_is_disabled_no_token_no_trial(self):
        result=self.prepare(); self.assertEqual(result['state'],'prepared')
        w,t=self.state(); self.assertIsNone(w['t0']); self.assertIsNone(t); self.assertNotIn('token_key',w)
        self.assertEqual(self.provider.calls,0)

    def test_start_requires_future_notice_and_immutable_contract(self):
        result=self.prepare()
        with self.assertRaisesRegex(RuntimeFault,'START_NOTICE'):
            week.activate(self.f.store,self.f.admin,self.f.project,expected_contract=result['contract_sha256'],t0=time.time()-1,start_notice_sha256='e'*64)
        with self.assertRaisesRegex(RuntimeFault,'START_CONFLICT'):
            week.activate(self.f.store,self.f.admin,self.f.project,expected_contract='f'*64,t0=time.time()+1,start_notice_sha256='e'*64)
        self.assertEqual(self.state()[0]['state'],'prepared')

    def test_two_samples_run_without_waiting_for_human_review_and_dedupe(self):
        self.start()
        for n in (1,2):
            q=self.queue(n); self.assertEqual(self.generate(q['task'])['state'],'awaiting_script_review')
        self.assertTrue(self.queue(1)['replayed'])
        self.assertEqual(len(self.state()[1]['requests']),2)
        s=self.snapshot(1);s['brief']='Changed same version';s['input_sha256']=fingerprint({k:v for k,v in s.items() if k!='input_sha256'})
        with self.assertRaisesRegex(RuntimeFault,'SOURCE_VERSION_CHANGED'):week.queue_snapshot(self.jobs,self.token,self.f.project,s)

    def test_service_token_is_not_a_video_or_admin_token(self):
        self.start()
        from video_factory.dispatch import authorize
        with self.f.store.connect() as db:
            with self.assertRaisesRegex(RuntimeFault,'AUTH_EXECUTION'):authorize(db,self.token,self.f.project)
            with self.assertRaises(RuntimeFault):self.f.store.authorize(db,self.token,'admin')
        q=self.queue()
        with self.assertRaisesRegex(RuntimeFault,'AUTH_WEEK'):self.jobs.step(self.cap,self.f.project,q['task'],1,self.key)
        self.assertEqual(self.provider.calls,0)

    def test_wrong_source_old_tasks_and_production_target_denied(self):
        self.start()
        for field,value in [('source_base','bascnOther'),('eligible_at',self.t0-1)]:
            s=self.snapshot();s[field]=value;s['input_sha256']=fingerprint({k:v for k,v in s.items() if k!='input_sha256'})
            with self.assertRaises(RuntimeFault):week.queue_snapshot(self.jobs,self.token,self.f.project,s)
        q=self.queue()
        with self.f.store.connect() as db:
            b=meta(db,'feishu:binding:'+self.f.project);b['base_token']='bascnProductionFixture';save(db,'feishu:binding:'+self.f.project,b)
        with self.assertRaisesRegex(RuntimeFault,'CONFIGURATION_CHANGED'):self.generate(q['task'])

    def test_restore_revokes_week_token(self):
        self.start();q=self.queue()
        with self.f.store.connect() as db:self.f.store.invalidate_worker_approvals(db)
        with self.assertRaises(RuntimeFault):self.generate(q['task'])
        self.assertEqual(self.provider.calls,0)

    def test_exact_168_hour_expiry_and_idempotent_close(self):
        start=self.start();q=self.queue()
        with patch('video_factory.script_week.time.time',return_value=start['t1']):
            with self.assertRaisesRegex(RuntimeFault,'WEEK_CLOSED'):self.generate(q['task'])
            receipt=week.close(self.f.store,self.f.project)
            self.assertEqual(week.close(self.f.store,self.f.project),receipt)
        w,t=self.state();self.assertFalse(t['enabled']);self.assertEqual(w['state'],'closed')
        with self.f.store.connect() as db:self.assertEqual(meta(db,w['token_key'])['expires_at'],0)

    def test_unknown_stops_future_samples_even_next_day(self):
        self.start();q=self.queue()
        with patch.object(self.provider,'generate',side_effect=TimeoutError):
            self.assertEqual(self.jobs.step(self.token,self.f.project,q['task'],1,self.key)['state'],'script_submission_unknown')
        with patch('video_factory.script_week.time.time',return_value=self.t0+week.DAY):
            with self.assertRaisesRegex(RuntimeFault,'PENDING_OR_UNKNOWN'):self.queue(2)
        self.assertEqual(len(self.state()[1]['requests']),1)

    def test_ten_samples_per_bucket_and_unreviewed_do_not_block_next_day(self):
        self.start()
        for n in range(10):self.generate(self.queue(n)['task'])
        with self.assertRaisesRegex(RuntimeFault,'DAILY_SAMPLE_LIMIT'):self.queue(11)
        with patch('video_factory.script_week.time.time',return_value=self.t0+week.DAY):
            self.generate(self.queue(11)['task'])
        self.assertEqual(len(self.state()[1]['requests']),11)

    def test_daily_and_weekly_money_include_reservation_and_old_exposure(self):
        self.start();w,t=self.state()
        def row(cost,when=None,rewrite=False):
            r={'state':'usage_accounted','submitted_at':self.t0 if when is None else when,'cost_upper_micro':cost}
            if rewrite:r['rewrite_parent']='parent'
            return r
        t['requests']={'x':row(2800001)}
        with self.assertRaisesRegex(RuntimeFault,'DAILY_BUDGET'):week.check_limits(w,t,self.t0,rewrite_parent='x')
        t['requests']={'x':row(20800001)}
        with self.assertRaisesRegex(RuntimeFault,'WEEK_BUDGET'):week.check_limits(w,t,self.t0+week.DAY,rewrite_parent='x')
        # Weekly max is checked separately; historical reservations cannot be reset.
        w['contract']['history_exposure_micro']=9500000
        t['requests']={'x':row(20400000)}
        with self.assertRaisesRegex(RuntimeFault,'COMBINED_BUDGET'):week.check_limits(w,t,self.t0+week.DAY,rewrite_parent='x')

    def test_request_limits_include_rewrites_and_no_carry_over_budget(self):
        self.start();w,t=self.state()
        t['requests']={str(n):{'state':'usage_accounted','submitted_at':self.t0,'cost_upper_micro':1,'rewrite_parent':'p'} for n in range(20)}
        with self.assertRaisesRegex(RuntimeFault,'DAILY_REQUEST_LIMIT'):week.check_limits(w,t,self.t0,rewrite_parent='p')
        week.check_limits(w,t,self.t0+week.DAY,rewrite_parent='p')
        t['requests']={str(n):{'state':'usage_accounted','submitted_at':self.t0,'cost_upper_micro':1,'rewrite_parent':'p'} for n in range(140)}
        with self.assertRaisesRegex(RuntimeFault,'WEEK_REQUEST_LIMIT'):week.check_limits(w,t,self.t0+week.DAY,rewrite_parent='p')

    def test_cross_day_usage_is_charged_to_submission_bucket(self):
        self.start();w,t=self.state()
        t['requests']={'old':{'state':'usage_accounted','submitted_at':self.t0,'cost_upper_micro':2999999}}
        week.check_limits(w,t,self.t0+week.DAY,rewrite_parent=None)
        t['requests']['today']={'state':'usage_accounted','submitted_at':self.t0+week.DAY,'cost_upper_micro':2800001}
        with self.assertRaisesRegex(RuntimeFault,'DAILY_BUDGET'):week.check_limits(w,t,self.t0+week.DAY,rewrite_parent=None)

    def test_exports_redact_and_preserve_late_versions_without_table_writes(self):
        self.start();q=self.queue();self.generate(q['task'])
        data=review_export(self.f.store,self.f.project,day=0)
        self.assertEqual(data['rows'][0]['status'],'awaiting_script_review')
        self.assertEqual(data['table_writes'],0);self.assertEqual(data['provider_requests'],0)
        self.assertEqual(len(review_export(self.f.store,self.f.project,day=1)['rows']),0)
        with tempfile.TemporaryDirectory() as directory:
            first=save_archive(directory,data);self.assertTrue(save_archive(directory,data)['replayed'])
            changed=copy.deepcopy(data);changed['rows'][0]['status']='ready'
            second=save_archive(directory,changed);self.assertNotEqual(first['path'],second['path'])
            self.assertEqual(__import__('json').loads(Path(first['path']).read_text())['rows'][0]['status'],'awaiting_script_review')
        source_key=next(iter(self.state()[0]['snapshots']))
        snapshot=self.snapshot()
        baseline={source_key:{'source_version':'v1','input_sha256':snapshot['input_sha256'], 'unmodified':True,
            'outputs':dict.fromkeys(OUTPUTS,'mail fixture@example.com key sk-abcdefgh12345678 private-name')}}
        exported=review_export(self.f.store,self.f.project,baselines=baseline,redact_values=('private-name',))
        self.assertEqual(exported['rows'][0]['pairing_status'],'matched')
        raw=__import__('json').dumps(exported)
        for secret in ('fixture@example.com','sk-abcdefgh12345678','private-name'):self.assertNotIn(secret,raw)

    def test_three_final_failures_stop_but_pending_rewrite_is_not_final_failure(self):
        self.start();w,t=self.state()
        t['requests']={str(n):{'state':'usage_accounted','submitted_at':self.t0+n,'cost_upper_micro':1,'outcome':'validation_failed'} for n in range(3)}
        with self.assertRaisesRegex(RuntimeFault,'CONSECUTIVE_FAILURE'):week.check_limits(w,t,self.t0+5,rewrite_parent=None)
        t['requests']['2']['outcome']='rewrite_pending'
        week.check_limits(w,t,self.t0+5,rewrite_parent='2')


if __name__ == '__main__':unittest.main()
