import copy
import unittest
from unittest.mock import patch
from test_script_trial import TrialTests
from video_factory import script_trial as trial
from video_factory.script_trial_batch import activate_batch,request_limit
from video_factory.runtime_store import RuntimeFault,fingerprint
from video_factory.feishu_bridge import save,meta


class BatchTests(TrialTests):
    def batch(self,tasks=None,limit=45):
        self.arm();trial.pause(self.f.store,self.f.admin,self.f.project)
        return activate_batch(self.f.store,self.f.admin,self.f.project,tasks=tasks or ['generated_one'],
                              max_requests=limit,notice_sha256='b'*64,expected_sha256=fingerprint(self.snapshot()))
    def test_finite_batch_no_24_hour_wait_and_same_approval_path(self):
        self.batch();self.assertIsNone(self.snapshot()['t1']);self.queue()
        with patch.object(self.provider,'generate',return_value={'script':'draft','provider_id':'synthetic',
                     'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120}}):self.step()
        self.assertEqual(len(self.snapshot()['requests']),1)
        trial.pause(self.f.store,self.f.admin,self.f.project);self.assertFalse(self.snapshot()['enabled'])
    def test_unlisted_task_denied_before_paid_call(self):
        self.batch(['other']);self.queue()
        with self.assertRaisesRegex(RuntimeFault,'TASK_NOT_APPROVED'):self.step()
        self.assertEqual(self.provider.calls,0)
    def test_default_limit_and_invalid_increases(self):
        self.assertEqual(request_limit({}),30)
        for n in [46,True,0]:
            with self.subTest(limit=n),self.assertRaisesRegex(RuntimeFault,'POLICY_INVALID'):
                request_limit({'batch':{'mode':'finite','tasks':['x'],'max_requests':n,'notice_sha256':'a'*64}})
    def test_existing_ledger_never_reset(self):
        self.arm();trial.pause(self.f.store,self.f.admin,self.f.project)
        state=self.snapshot();state['requests']={'old':{'state':'reserved','task':'old','revision':1,'cost_upper_micro':200000}}
        state['quarantined_requests']={'old':{'task':'old','revision':1}}
        with self.f.store.connect() as db:save(db,trial.PREFIX+self.f.project,state)
        activate_batch(self.f.store,self.f.admin,self.f.project,tasks=['new'],max_requests=45,notice_sha256='b'*64,expected_sha256=fingerprint(state))
        self.assertEqual(self.snapshot()['requests'],state['requests'])
    def test_not_allow_reuse_existing_task_or_stale_state(self):
        self.batch();state=self.snapshot()
        with self.assertRaisesRegex(RuntimeFault,'BATCH_CONFLICT'):
            activate_batch(self.f.store,self.f.admin,self.f.project,tasks=['new'],max_requests=45,notice_sha256='c'*64,expected_sha256=fingerprint(state))
    def test_production_target_rejected(self):
        self.arm();trial.pause(self.f.store,self.f.admin,self.f.project);state=self.snapshot();state['production_base']=state['copy_base']
        with self.f.store.connect() as db:save(db,trial.PREFIX+self.f.project,state)
        with self.assertRaisesRegex(RuntimeFault,'TARGET_MISMATCH'):
            activate_batch(self.f.store,self.f.admin,self.f.project,tasks=['new'],max_requests=45,notice_sha256='b'*64,expected_sha256=fingerprint(state))

    def reject_activation_with_ledger(self,count,cost,error):
        self.arm();trial.pause(self.f.store,self.f.admin,self.f.project)
        state=self.snapshot();state['requests']={str(i):{'state':'usage_accounted','cost_upper_micro':cost} for i in range(count)}
        with self.f.store.connect() as db:save(db,trial.PREFIX+self.f.project,state)
        with self.assertRaisesRegex(RuntimeFault,error):
            activate_batch(self.f.store,self.f.admin,self.f.project,tasks=['new'],max_requests=45,notice_sha256='b'*64,expected_sha256=fingerprint(state))
        self.assertEqual(self.snapshot(),state)
    def test_cumulative_capacity_cannot_reset(self):
        self.reject_activation_with_ledger(45,1,'REQUEST_LIMIT')
    def test_cumulative_budget_cannot_reset(self):
        self.reject_activation_with_ledger(1,trial.CAP,'BUDGET_LIMIT')
    def test_runtime_stops_at_45_and_never_46(self):
        from video_factory.script_jobs import key
        from review_fixture import BINDING
        self.batch();self.queue()
        state=self.snapshot();state['requests']={str(i):{'state':'usage_accounted','cost_upper_micro':1,'input_digest':'different'+str(i)} for i in range(45)}
        with self.f.store.connect() as db:
            save(db,trial.PREFIX+self.f.project,state)
            plan=meta(db,key(self.f.project,'generated_one',1))['plan']
        with self.assertRaisesRegex(RuntimeFault,'REQUEST_LIMIT'):
            with self.f.store.connect() as db:trial.reserve(db,plan,BINDING,'new')
        self.assertEqual(self.snapshot(),state)
