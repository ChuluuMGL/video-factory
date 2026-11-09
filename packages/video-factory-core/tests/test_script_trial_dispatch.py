import copy
import unittest
from video_factory.runtime_store import RuntimeFault
from video_factory.script_trial_dispatch import continuation


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.plans = ['sample_one', 'sample_two', 'sample_three', 'sample_four']
        self.trial = {'enabled': True, 't0': 900, 't1': 2000, 'checkpoint_task': 'sample_two',
                      'requests': {}, 'quarantined_requests': {}}
        self.rows, self.jobs = [], {}
        self.add('sample_one', 'script_submission_unknown', 'submission_unknown', 'reserved')
        self.trial['quarantined_requests']['sample_one_digest'] = {'task': 'sample_one', 'revision': 1}
        self.add('sample_two', 'ready', 'received', 'usage_accounted')

    def add(self, task, state, job_state, request_state=None):
        self.rows.append({'id': task, 'revision': 1, 'state': state})
        self.jobs[task] = {'plan': {'revision': 1}, 'plan_sha256': task + '_digest',
                           'state': job_state, 'expires_at': 1500}
        if request_state:
            self.trial['requests'][task + '_digest'] = {'task': task, 'revision': 1,
                'state': request_state, 'submitted_at': 995, 'cost_upper_micro': 200000}

    def decide(self):
        return continuation(self.trial, self.rows, self.jobs, self.plans, now=self.now)

    def test_live_intent_waits_then_success_continues_without_review_or_retry(self):
        self.add('sample_three', 'script_submission_unknown', 'submission_unknown', 'reserved')
        before = copy.deepcopy((self.trial, self.rows, self.jobs))
        self.assertEqual(self.decide(), {'state': 'waiting_completion', 'task': 'sample_three'})
        self.assertEqual(before, (self.trial, self.rows, self.jobs))
        self.jobs['sample_three']['state'] = 'received'
        self.trial['requests']['sample_three_digest']['state'] = 'usage_accounted'
        self.assertEqual(self.decide()['state'], 'waiting_completion')
        self.rows[-1]['state'] = 'awaiting_script_review'
        self.assertEqual(self.decide(), {'state': 'prepare_next', 'task': 'sample_four'})

    def test_network_truncation_format_and_unknown_failures_stop(self):
        self.add('sample_three', 'script_submission_unknown', 'submission_unknown', 'reserved')
        for code in ('SCRIPT_NETWORK_FAILED', 'SCRIPT_MODEL_TRUNCATED', 'SCRIPT_FORMAT_INVALID',
                     'SCRIPT_SUBMISSION_UNCERTAIN_NO_RETRY'):
            with self.subTest(code=code):
                self.jobs['sample_three']['submission_error'] = code
                with self.assertRaisesRegex(RuntimeFault, 'TERMINAL_FAILURE'): self.decide()

    def test_abandoned_intent_stops_without_changing_or_resubmitting_it(self):
        self.add('sample_three', 'script_submission_unknown', 'submission_unknown', 'reserved')
        self.now = 1595
        before = copy.deepcopy((self.trial, self.rows, self.jobs))
        with self.assertRaisesRegex(RuntimeFault, 'UNCERTAIN_REQUIRES_INSPECTION'): self.decide()
        self.assertEqual(before, (self.trial, self.rows, self.jobs))

    def test_terminal_failure_not_hidden_by_other_inflight(self):
        self.add('sample_three', 'script_submission_unknown', 'submission_unknown', 'reserved')
        self.add('sample_four', 'failed', 'received', 'usage_accounted')
        with self.assertRaisesRegex(RuntimeFault, 'TERMINAL_FAILURE'): self.decide()

    def test_waiting_for_first_real_review_only(self):
        self.rows[1]['state'] = 'awaiting_script_review'
        self.assertEqual(self.decide()['state'], 'waiting_first_real_review')
        self.rows[1]['state'] = 'ready'
        self.assertEqual(self.decide()['task'], 'sample_three')

    def test_restart_selects_first_missing_not_existing_third(self):
        self.add('sample_three', 'awaiting_script_review', 'received', 'usage_accounted')
        self.assertEqual(self.decide()['task'], 'sample_four')

    def test_queue_waits_then_expires(self):
        self.add('sample_three', 'script_queued', 'queued')
        self.assertEqual(self.decide()['state'], 'waiting_worker')
        self.now = 1500
        with self.assertRaisesRegex(RuntimeFault, 'QUEUE_EXPIRED'): self.decide()

    def test_usage_unknown_stops(self):
        self.add('sample_three', 'awaiting_script_review', 'received', 'usage_unknown')
        with self.assertRaisesRegex(RuntimeFault, 'USAGE_REQUIRES_INSPECTION'): self.decide()

    def test_window_and_budget_fences(self):
        self.now = 2000
        self.assertEqual(self.decide()['state'], 'closed')
        self.now = 1000
        self.trial['requests']['sample_one_digest']['cost_upper_micro'] = 30000000
        with self.assertRaisesRegex(RuntimeFault, 'BUDGET_LIMIT'): self.decide()

    def test_duplicate_or_unknown_tasks_rejected(self):
        self.plans.append('sample_two')
        with self.assertRaisesRegex(RuntimeFault, 'PLAN_INVALID'): self.decide()
        self.plans.pop()
        self.add('unregistered', 'script_queued', 'queued')
        with self.assertRaisesRegex(RuntimeFault, 'TASK_CONFLICT'): self.decide()

    def test_complete_after_all_generated_no_later_review_gate(self):
        for task in self.plans[2:]: self.add(task, 'awaiting_script_review', 'received', 'usage_accounted')
        self.assertEqual(self.decide(), {'state': 'complete', 'requests': 4})

    def test_bad_quarantine_or_orphan_request_stops(self):
        self.trial['quarantined_requests']['sample_one_digest']['task'] = 'sample_two'
        with self.assertRaisesRegex(RuntimeFault, 'QUARANTINE_CONFLICT'): self.decide()
        self.trial['quarantined_requests']['sample_one_digest']['task'] = 'sample_one'
        self.trial['requests']['orphan'] = {'task': 'missing', 'revision': 1, 'state': 'reserved'}
        with self.assertRaisesRegex(RuntimeFault, 'ORPHAN_REQUEST'): self.decide()
