import copy
import time
from unittest.mock import patch
from test_script_jobs import ScriptTests
from review_fixture import BINDING
from video_factory.runtime_store import RuntimeFault
from video_factory.feishu_bridge import meta,save
from video_factory import script_trial as trial


class TrialTests(ScriptTests):
    # Reuse normal authorization fixtures; inherited tests also ensure the default
    # non-trial execution route stays unchanged.
    def arm(self, start=True):
        trial.configure(self.f.store,self.f.admin,self.f.project,
                        production_base='bascnProduction',copy_base=BINDING['base_token'],
                        copy_table=BINDING['table_id'])
        if start:
            now=time.time()
            with patch('video_factory.script_trial.time.time',return_value=now):
                trial.activate(self.f.store,self.f.admin,self.f.project,t0=now,notice_sha256='a'*64)

    def snapshot(self):
        with self.f.store.connect() as db:return meta(db,trial.PREFIX+self.f.project)

    def test_trial_requires_notice_and_real_window(self):
        self.arm(False);self.queue()
        with self.assertRaisesRegex(RuntimeFault,'TRIAL_WINDOW_CLOSED'):self.step()
        self.assertEqual(self.provider.calls,0)
        with self.assertRaisesRegex(RuntimeFault,'TRIAL_START_NOTICE_REQUIRED'):
            trial.activate(self.f.store,self.f.admin,self.f.project,t0=time.time()-5,notice_sha256='a'*64)

    def test_usage_settles_and_no_second_charge_on_replay(self):
        self.arm();self.queue()
        with patch.object(self.provider,'generate',return_value={'script':'draft','provider_id':'receipt',
                    'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120}}) as generate:
            self.step();self.step();self.assertEqual(generate.call_count,1)
        row=next(iter(self.snapshot()['requests'].values()))
        self.assertEqual(row['state'],'usage_accounted');self.assertEqual(row['cost_upper_micro'],360)

    def test_missing_usage_keeps_reservation_and_pauses(self):
        self.arm();self.queue();self.step()
        state=self.snapshot();self.assertFalse(state['enabled'])
        self.assertEqual(next(iter(state['requests'].values()))['cost_upper_micro'],trial.RESERVE)
        self.assertEqual(next(iter(state['requests'].values()))['state'],'usage_unknown')

    def test_needs_evidence_is_known_receipt_but_not_reviewable_script(self):
        self.arm();self.queue()
        with patch.object(self.provider,'generate',return_value={'decision':'needs_evidence',
                'missing_facts':['Exact product size required.'],'provider_id':'receipt',
                'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120}}) as generate:
            first=self.step();second=self.step();self.assertEqual(generate.call_count,1)
        self.assertEqual(first['error'],'SCRIPT_EVIDENCE_REQUIRED');self.assertEqual(first['state'],'failed')
        self.assertEqual(second['state'],'failed')
        self.assertEqual(next(iter(self.snapshot()['requests'].values()))['state'],'usage_accounted')
        with self.assertRaisesRegex(RuntimeFault,'REVIEW_STATE_CONFLICT'):self.review()

    def test_timeout_survives_new_worker_and_cannot_reset_trial(self):
        self.arm();self.queue();self.provider.fail=True;self.step()
        self.assertEqual(next(iter(self.snapshot()['requests'].values()))['state'],'reserved')
        with self.assertRaisesRegex(RuntimeFault,'TRIAL_ALREADY_CONFIGURED'):self.arm()
        self.step();self.assertEqual(self.provider.calls,1)

    def test_expiry_and_target_guard_prevent_post(self):
        self.arm();self.queue()
        original=self.snapshot()
        with self.f.store.connect() as db:
            expired=copy.deepcopy(original);expired.update(t0=time.time()-86401,t1=time.time()-1)
            save(db,trial.PREFIX+self.f.project,expired)
        with self.assertRaisesRegex(RuntimeFault,'TRIAL_WINDOW_CLOSED'):self.step()
        with self.f.store.connect() as db:
            original['copy_table']='tblOther';save(db,trial.PREFIX+self.f.project,original)
        with self.assertRaisesRegex(RuntimeFault,'TRIAL_TARGET_MISMATCH'):self.step()
        self.assertEqual(self.provider.calls,0)

    def test_limits_unknown_and_duplicate_are_atomic(self):
        self.arm();self.queue()
        from video_factory.script_jobs import key
        with self.f.store.connect() as db:plan=meta(db,key(self.f.project,'generated_one',1))['plan']
        original=self.snapshot()
        for kind,error in [('pending','TRIAL_PENDING_OR_UNKNOWN'),('count','TRIAL_REQUEST_LIMIT'),
                           ('budget','TRIAL_BUDGET_LIMIT'),('duplicate','TRIAL_DUPLICATE_INPUT')]:
            state=copy.deepcopy(original)
            if kind=='duplicate':
                with self.f.store.connect() as db:
                    save(db,trial.PREFIX+self.f.project,state);trial.reserve(db,plan,BINDING,'old')
                state=self.snapshot()
                next(iter(state['requests'].values()))['state']='usage_accounted'
            else:
                count=30 if kind=='count' else 1
                state['requests']={str(i):{'input_digest':'different'+str(i),
                    'state':'reserved' if kind=='pending' else 'usage_accounted',
                    'cost_upper_micro':trial.CAP if kind=='budget' else 1} for i in range(count)}
            with self.f.store.connect() as db:save(db,trial.PREFIX+self.f.project,state)
            with self.assertRaisesRegex(RuntimeFault,error):
                with self.f.store.connect() as db:trial.reserve(db,plan,BINDING,'new')
            self.assertEqual(self.snapshot(),state)
        self.assertEqual(self.provider.calls,0)
