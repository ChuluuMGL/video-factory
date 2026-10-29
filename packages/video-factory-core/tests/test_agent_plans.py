"""Cloud-only synthetic contracts: no real provider or Feishu transport."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import json
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

import test_local_kernel as fixture
from video_factory import automation
from video_factory.agent_api import AgentAPI, AgentServer
from video_factory.agent_plans import VideoPlans
from video_factory.feishu_bridge import meta, save
from video_factory.runtime_store import RuntimeFault, RuntimeStore, exclusive_write
from video_factory.scheduler import Scheduler
from video_factory.worker import job_key


class AgentPlanTests(unittest.TestCase):
    worker = fixture.LocalKernelTests.worker
    task = fixture.LocalKernelTests.task
    scheduler = fixture.LocalKernelTests.scheduler
    state = fixture.LocalKernelTests.state

    def setUp(self):
        fixture.LocalKernelTests.setUp(self)
        self.planner = automation.issue(self.store, self.admin, 'brand', scope='agent_plan')['token']
        # A server-owned synthetic profile. Agent requests can never set paths,
        # account references, model, duration, or region.
        with self.store.connect() as db:
            save(db, 'video:profile:brand', {'credential_ref': 'secret:fixture', 'billing_owner': 'fixture',
                'region': 'global', 'assets': {'sku': {'assets_root': str(self.assets), 'specification': self.spec}}})
        self.task(approve=False)
        self.plans = VideoPlans(self.store, 'brand')

    def prepare(self, request_id='request_one'):
        return self.plans.prepare(self.planner, 'one', 1, request_id)

    def approve(self, plan):
        return self.plans.owner(self.admin, 'approve', plan['plan_id'], plan['plan_sha256'])

    def submit(self, plan):
        return self.plans.submit(self.planner, plan['plan_id'], plan['plan_sha256'])

    def change_plan(self, plan, **values):
        with self.store.connect() as db:
            key = 'agent:video:plan:' + plan['plan_id']
            saved = meta(db, key); saved.update(values); save(db, key, saved)

    def test_prepare_and_owner_approval_do_not_create_executable_job(self):
        plan = self.prepare()
        self.assertEqual(plan['phase'], 'prepared')
        self.assertFalse(plan['cost']['quote_available'])
        self.approve(plan)
        self.scheduler().tick()
        with self.store.connect() as db:
            self.assertIsNone(meta(db, job_key('brand', 'one', 1)))
        self.assertEqual(self.provider.calls, 0)
        self.assertEqual(self.state(), 'ready')

    def test_owner_approval_and_exact_digest_required(self):
        plan = self.prepare()
        with self.assertRaisesRegex(RuntimeFault, 'OWNER_APPROVAL_REQUIRED'): self.submit(plan)
        with self.assertRaisesRegex(RuntimeFault, 'DIGEST_REQUIRED'):
            self.plans.owner(self.admin, 'approve', plan['plan_id'], 'bad')
        for token in (self.reader, self.planner, self.execution):
            with self.assertRaisesRegex(RuntimeFault, 'AUTH_REQUIRED'):
                self.plans.owner(token, 'approve', plan['plan_id'], plan['plan_sha256'])
        self.approve(plan)
        with self.assertRaisesRegex(RuntimeFault, 'DIGEST_REQUIRED'):
            self.plans.submit(self.planner, plan['plan_id'], 'bad')

    def test_prepare_replay_and_approval_never_extend_expiry(self):
        plan = self.prepare()
        again = self.prepare()
        self.assertTrue(again['replayed'])
        self.assertEqual(plan['plan_sha256'], again['plan_sha256'])
        for _ in range(2): self.assertEqual(self.approve(plan)['expires_at'], plan['expires_at'])
        with self.assertRaisesRegex(RuntimeFault, 'REQUEST_ID_CONFLICT'):
            self.plans.prepare(self.planner, 'other', 1, 'request_one')
        self.change_plan(plan, expires_at=0)
        self.assertEqual(self.prepare()['phase'], 'expired')
        with self.assertRaisesRegex(RuntimeFault, 'EXPIRED'): self.approve(plan)
        with self.assertRaisesRegex(RuntimeFault, 'EXPIRED'): self.submit(plan)

    def test_agent_scope_project_revocation_and_expiry(self):
        plan = self.prepare()
        for token in (self.reader, self.admin, self.execution):
            with self.assertRaisesRegex(RuntimeFault, 'AUTH_AGENT_DENIED'):
                self.plans.prepare(token, 'one', 1, 'other_request')
        self.assertEqual(self.plans.receipt(self.reader, plan['plan_id'])['phase'], 'prepared')
        self.store.put_project(self.admin, 'other', {'video_route': 'deferred',
                               'credential_ref': 'secret:fixture', 'billing_owner': 'fixture'})
        other = automation.issue(self.store, self.admin, 'other', scope='agent_plan')['token']
        with self.assertRaisesRegex(RuntimeFault, 'AUTH_AGENT_DENIED'):
            self.plans.submit(other, plan['plan_id'], plan['plan_sha256'])
        with self.assertRaisesRegex(RuntimeFault, 'PLAN_MISSING'):
            VideoPlans(self.store, 'other').receipt(other, plan['plan_id'])
        key = hashlib.sha256(self.planner.encode()).hexdigest()
        with self.store.connect() as db:
            value = meta(db, 'automation:key:' + key); value['expires_at'] = 0
            save(db, 'automation:key:' + key, value)
        with self.assertRaisesRegex(RuntimeFault, 'AUTH_AGENT_DENIED'): self.submit(plan)
        automation.revoke(self.store, self.admin, key)
        with self.assertRaisesRegex(RuntimeFault, 'AUTH_AGENT_DENIED'): self.prepare()

    def test_concurrent_duplicate_submits_create_one_worker_job(self):
        plan = self.prepare(); self.approve(plan)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.submit(plan), range(8)))
        self.assertEqual(sum(not r['replayed'] for r in results), 1)
        self.assertTrue(all(r['phase'] == 'enqueued' for r in results))
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM meta WHERE key LIKE 'worker:%'").fetchone()[0], 1)
        self.assertEqual(self.provider.calls, 0)

    def test_two_approved_plans_cannot_replace_one_execution(self):
        first = self.prepare('first'); second = self.prepare('second')
        self.approve(first); self.approve(second); self.submit(first)
        with self.assertRaisesRegex(RuntimeFault, 'ALREADY_HAS_EXECUTION'): self.submit(second)
        with self.assertRaisesRegex(RuntimeFault, 'ALREADY_HAS_EXECUTION'): self.prepare('third')
        with self.store.connect() as db:
            self.assertEqual(meta(db, job_key('brand', 'one', 1))['agent_plan_id'], first['plan_id'])

    def test_atomic_enqueue_rolls_back_on_receipt_failure(self):
        plan = self.prepare(); self.approve(plan)
        real_save = save
        def fail_plan_save(db, key, value):
            if key.startswith('agent:video:plan:'): raise RuntimeError('synthetic-write-failure')
            real_save(db, key, value)
        with patch('video_factory.agent_plans.save', side_effect=fail_plan_save):
            with self.assertRaises(RuntimeError): self.submit(plan)
        with self.store.connect() as db:
            self.assertIsNone(meta(db, job_key('brand', 'one', 1)))
        self.assertEqual(self.submit(plan)['phase'], 'enqueued')

    def test_restart_after_enqueue_and_fake_result_keep_human_review(self):
        plan = self.prepare(); self.approve(plan); self.submit(plan)
        self.store = RuntimeStore(self.root)
        self.plans = VideoPlans(self.store, 'brand')
        self.assertTrue(self.submit(plan)['replayed'])
        self.scheduler().tick()
        self.assertEqual(self.state(), 'submitted')
        # The persisted fairness cursor wraps on an idle scan before polling
        # the first task again; a restart must not reset that cursor.
        self.scheduler().tick()
        self.assertEqual(self.state(), 'submitted')
        self.scheduler().tick()
        receipt = self.plans.receipt(self.reader, plan['plan_id'])
        self.assertEqual(receipt['task_state'], 'awaiting_video_review')
        self.assertTrue(receipt['provider_receipt_recorded'])
        self.assertEqual(receipt['artifact'], {'sha256': 'a' * 64, 'verification': 'full_decode_passed'})
        self.assertEqual(receipt['human_acceptance'], 'separate_review_required')
        self.submit(plan); self.scheduler().tick()
        self.assertEqual(self.provider.calls, 1)

    def test_unknown_submit_is_receipted_and_never_resubmitted(self):
        plan = self.prepare(); self.approve(plan); self.submit(plan)
        self.provider.mode = 'unknown'; self.scheduler().tick()
        self.plans = VideoPlans(RuntimeStore(self.root), 'brand')
        for _ in range(3):
            result = self.submit(plan); self.scheduler().tick()
        self.assertEqual(result['task_state'], 'submission_unknown')
        self.assertTrue(result['reconciliation_required'])
        self.assertFalse(result['provider_receipt_recorded'])
        self.assertEqual(self.provider.calls, 1)
        with self.assertRaisesRegex(RuntimeFault, 'ALREADY_STARTED'):
            self.plans.owner(self.admin, 'revoke', plan['plan_id'], plan['plan_sha256'])

    def test_revocation_before_and_after_enqueue_blocks_worker(self):
        plan = self.prepare(); self.approve(plan)
        self.plans.owner(self.admin, 'revoke', plan['plan_id'], plan['plan_sha256'])
        with self.assertRaisesRegex(RuntimeFault, 'REVOKED'): self.submit(plan)
        second = self.prepare('second'); self.approve(second); self.submit(second)
        receipt = self.plans.owner(self.admin, 'revoke', second['plan_id'], second['plan_sha256'])
        self.assertEqual(receipt['phase'], 'revoked')
        self.assertFalse(receipt['execution_approval_valid'])
        self.submit(second); self.scheduler().tick()
        self.assertEqual(self.provider.calls, 0)

    def test_input_and_task_version_drift_block_stale_approval(self):
        plan = self.prepare()
        with self.store.connect() as db:
            db.execute("UPDATE tasks SET input_digest='changed' WHERE id='one'")
        with self.assertRaisesRegex(RuntimeFault, 'PLAN_CHANGED'): self.approve(plan)
        with self.store.connect() as db:
            db.execute("UPDATE tasks SET revision=2 WHERE id='one'")
        with self.assertRaisesRegex(RuntimeFault, 'STALE_TASK_REVISION'): self.approve(plan)
        receipt = self.plans.receipt(self.reader, plan['plan_id'])
        self.assertFalse(receipt['is_current_revision'])
        self.assertEqual(receipt['current_revision'], 2)

    def test_profile_and_configuration_drift_block_enqueue(self):
        plan = self.prepare(); self.approve(plan)
        with self.store.connect() as db:
            profile = meta(db, 'video:profile:brand'); profile['billing_owner'] = 'changed'
            save(db, 'video:profile:brand', profile)
        with self.assertRaisesRegex(RuntimeFault, 'PLAN_CHANGED'): self.submit(plan)
        with self.store.connect() as db:
            db.execute("UPDATE projects SET digest='changed' WHERE id='brand'")
        with self.assertRaisesRegex(RuntimeFault, 'CONFIGURATION_CHANGED'): self.submit(plan)

    def test_owner_can_revoke_enqueued_plan_after_configuration_drift(self):
        plan = self.prepare(); self.approve(plan); self.submit(plan)
        with self.store.connect() as db:
            db.execute("UPDATE projects SET digest='changed' WHERE id='brand'")
        receipt = self.plans.owner(self.admin, 'revoke', plan['plan_id'], plan['plan_sha256'])
        self.assertEqual(receipt['phase'], 'revoked')
        with self.store.connect() as db:
            self.assertEqual(meta(db, job_key('brand', 'one', 1))['expires_at'], 0)

    def test_credential_and_asset_changes_block_enqueue(self):
        plan = self.prepare(); self.approve(plan)
        self.store.put_secret(self.admin, 'fixture', 'synthetic-rotated-key', self.key)
        with self.assertRaisesRegex(RuntimeFault, 'PLAN_CHANGED'): self.submit(plan)
        (self.assets / 'image.png').write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeFault, 'REFERENCE_CHANGED'): self.submit(plan)

    def test_unreviewed_script_is_not_preparable(self):
        with self.store.connect() as db:
            db.execute("UPDATE tasks SET state='awaiting_script_review' WHERE id='one'")
        with self.assertRaisesRegex(RuntimeFault, 'APPROVED_SCRIPT'): self.prepare()
        for revision in (True, 0, '1'):
            with self.assertRaisesRegex(RuntimeFault, 'REVISION_REQUIRED'):
                self.plans.prepare(self.planner, 'one', revision, 'request_one')

    def test_recovery_revokes_proposals_even_with_fresh_agent_key(self):
        plan = self.prepare(); self.approve(plan)
        with self.store.connect() as db: self.store.invalidate_worker_approvals(db)
        self.planner = automation.issue(self.store, self.admin, 'brand', scope='agent_plan')['token']
        self.assertEqual(self.prepare()['phase'], 'revoked')
        with self.assertRaisesRegex(RuntimeFault, 'REVOKED'): self.submit(plan)

    def test_agent_responses_are_redacted_and_owner_inspect_is_explicit(self):
        plan = self.prepare(); self.approve(plan); self.submit(plan); self.scheduler().tick()
        output = json.dumps([plan, self.plans.receipt(self.reader, plan['plan_id'])])
        for secret in (str(self.root), 'secret:fixture', 'synthetic-private-script',
                       self.admin, self.reader, self.planner, '12345678901231'):
            self.assertNotIn(secret, output)
        owner = self.plans.owner(self.admin, 'inspect', plan['plan_id'])
        self.assertEqual(owner['owner_review']['input']['script'], 'synthetic-private-script')
        self.assertFalse(owner['owner_review']['feishu_identity_verified'])

    def test_http_capabilities_flags_scopes_fields_and_no_approval_route(self):
        disabled = AgentAPI(self.store, 'brand', Scheduler(self.store, 'brand'))
        enabled = AgentAPI(self.store, 'brand', Scheduler(self.store, 'brand'), plans_enabled=True)
        body = {'project': 'brand', 'task': 'one', 'revision': 1, 'request_id': 'http_request'}
        with self.assertRaisesRegex(RuntimeFault, 'PLANS_DISABLED'):
            disabled.call('/v1/agent/video/prepare', self.planner, body)
        with AgentServer(('127.0.0.1', 0), enabled) as server:
            thread = threading.Thread(target=server.serve_forever); thread.start()
            try:
                def call(route, data, token=self.planner, extra=None):
                    conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                    try:
                        headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', **(extra or {})}
                        conn.request('POST', '/v1/agent/' + route, json.dumps(data), headers)
                        response = conn.getresponse(); return response.status, json.loads(response.read())
                    finally: conn.close()
                self.assertTrue(call('capabilities', {'project': 'brand'})[1]['mutations_allowed'])
                self.assertFalse(call('capabilities', {'project': 'brand'}, self.reader)[1]['mutations_allowed'])
                self.assertEqual(call('video/prepare', body, self.reader)[0], 401)
                self.assertEqual(call('video/prepare', {**body, 'model': 'arbitrary'})[0], 409)
                self.assertEqual(call('video/prepare', body, extra={'Origin': 'https://example.invalid'})[0], 403)
                code, plan = call('video/prepare', body); self.assertEqual(code, 200)
                submit = {'project': 'brand', 'plan_id': plan['plan_id'], 'plan_sha256': plan['plan_sha256']}
                self.assertEqual(call('video/approve', submit)[1]['error'], 'AGENT_ROUTE_NOT_AVAILABLE')
                self.assertEqual(call('video/submit', submit)[1]['error'], 'AGENT_OWNER_APPROVAL_REQUIRED')
                self.approve(plan)
                self.assertEqual(call('video/submit', submit)[1]['phase'], 'enqueued')
            finally: server.shutdown(); thread.join(5)

    def test_cli_requires_charge_acceptance_and_does_not_echo_credentials(self):
        plan = self.prepare()
        admin_file = self.root / 'owner.token'; exclusive_write(admin_file, self.admin.encode())
        base = [sys.executable, '-c', 'from video_factory.cli import main; raise SystemExit(main())',
                'agent-plan', 'approve', '--root', str(self.root), '--project', 'brand',
                '--admin-token-file', str(admin_file), '--plan-id', plan['plan_id'],
                '--plan-sha256', plan['plan_sha256']]
        denied = subprocess.run(base, capture_output=True, text=True, timeout=20)
        self.assertEqual(denied.returncode, 2)
        self.assertIn('EXPLICIT_PROVIDER_CHARGES_REQUIRED', denied.stdout)
        approved = subprocess.run(base + ['--accept-provider-charges'], capture_output=True, text=True, timeout=20)
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        self.assertEqual(json.loads(approved.stdout)['phase'], 'approved')
        self.assertNotIn(self.admin, approved.stdout + approved.stderr)
        self.assertEqual(self.provider.calls, 0)


if __name__ == '__main__': unittest.main()
