"""Cloud-only synthetic script proposal, Feishu identity and paid-intent tests."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import hashlib
import http.client
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import select
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
from review_fixture import Fixture
from video_factory import automation
from video_factory.agent_api import AgentAPI, AgentServer
from video_factory.agent_script_plans import ScriptPlans
from video_factory.cli import main
from video_factory.feishu_bridge import meta, save
from video_factory.local_runtime import configured_script_plans
from video_factory.runtime_store import RuntimeFault, RuntimeStore, exclusive_write
from video_factory.scheduler import Scheduler
from video_factory.script_jobs import ScriptJobs, profile, key as job_key


class Provider:
    def __init__(self): self.calls = []; self.fail = False
    def generate(self, sku, brief, feedback, secret):
        self.calls.append((brief, feedback))
        if self.fail: raise TimeoutError('synthetic-private-provider-error')
        return {'script': 'Synthetic generated private script', 'provider_id': 'synthetic-private-provider-id'}


class ScriptPlanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.f = Fixture(self.root); self.addCleanup(self.f.close)
        self.store, self.admin, self.project = self.f.store, self.f.admin, self.f.project
        self.key = Fernet.generate_key(); self.provider = Provider()
        with self.store.connect() as db:
            save(db, 'setup:project:' + self.project, {'configuration': {'project': {'base_mode': 'bind',
                 'products': [{'sku_id': 'sku_one', 'name': 'Synthetic product', 'truth_source': 'Synthetic package'}]}}})
        self.store.put_secret(self.admin, 'script', 'synthetic-script-secret', self.key)
        profile(self.store, self.admin, self.project, 'secret:script', 'fixture')
        self.jobs = ScriptJobs(self.store, client_factory=self.f.bridge.client_factory, provider=self.provider)
        self.planner = automation.issue(self.store, self.admin, self.project, scope='agent_plan')['token']
        self.reader = automation.issue(self.store, self.admin, self.project, scope='agent_read')['token']
        self.execution = automation.issue_execution(self.store, self.admin, self.project)['token']
        self.plans = self.service()
        self.request = {'task': 'generated_one', 'sku_id': 'sku_one', 'brief': 'Synthetic private brief', 'expected_revision': 0}
        self.addCleanup(patch.stopall)
        patch('video_factory.script_provider.ScriptProvider.generate', side_effect=AssertionError('real provider forbidden')).start()

    def service(self, jobs=None, store=None):
        return ScriptPlans(store or self.store, self.project, user_token_provider=lambda: self.f.user_token,
                           open_id='ou_employee', jobs=jobs or self.jobs)

    def prepare(self, request_id='request_one', request=None):
        return self.plans.prepare(self.planner, request_id, request or self.request)

    def approve(self, plan):
        return self.plans.owner(self.admin, 'approve', plan['plan_id'], plan['plan_sha256'])

    def submit(self, plan):
        return self.plans.submit(self.planner, plan['plan_id'], plan['plan_sha256'])

    def step(self, revision=1):
        return self.jobs.step(self.execution, self.project, 'generated_one', revision, self.key)

    def review(self, revision, decision, feedback=''):
        args = dict(project=self.project, task='generated_one', revision=revision,
                    stage='script', decision=decision, feedback=feedback)
        bridge = self.f.service.bridge
        plan = bridge.prepare_review(self.f.user_token, **args)
        return bridge.review(self.f.user_token, event='review_' + str(revision),
                             expected_plan=plan['plan_sha256'], **args)

    def native(self):
        class Source:
            fields = {'task': 'generated_one', 'sku': 'sku_one', 'brief': 'Original private Base brief',
                      'version': 'source_one', 'status': ''}
            def record(self, *args): return {'record_id': args[-1], 'fields': dict(self.fields)}
            def find_task(self, *args): return {'record_id': 'recFixture', 'fields': dict(self.fields)}
        source = Source()
        with self.store.connect() as db:
            save(db, 'native:config:' + self.project, {'enabled': True, 'context': {'target': {
                'base_token': 'bascnFixture', 'table_id': 'tblFixture'}}, 'names': {
                'task': 'task', 'sku_id': 'sku', 'script': 'brief', 'source_revision': 'version', 'status': 'status'}})
        self.jobs = ScriptJobs(self.store, client_factory=self.f.bridge.client_factory, provider=self.provider,
                              source_client_factory=lambda _: source, native_app_client_factory=lambda _: source)
        self.plans = self.service()
        return source

    def test_prepare_approve_and_submit_are_distinct(self):
        plan = self.prepare(); self.assertEqual(plan['phase'], 'prepared')
        self.assertEqual(plan['task_state'], 'not_created')
        self.assertFalse(plan['cost']['quote_available'])
        with self.assertRaisesRegex(RuntimeFault, 'OWNER_APPROVAL_REQUIRED'): self.submit(plan)
        self.approve(plan)
        with self.store.connect() as db:
            self.assertIsNone(meta(db, job_key(self.project, 'generated_one', 1)))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 0)
        self.assertEqual(self.submit(plan)['task_state'], 'script_queued')
        self.assertEqual(self.provider.calls, [])

    def test_revision_feedback_and_script_review_never_approve_video(self):
        plan = self.prepare(); self.approve(plan); self.submit(plan)
        self.assertEqual(self.step()['state'], 'awaiting_script_review')
        self.review(1, 'reject', 'Make the ending shorter')
        request = {**self.request, 'expected_revision': 1, 'brief': 'Revised private brief'}
        revised = self.prepare('request_two', request)
        self.assertEqual(revised['revision'], 2)
        inspected = self.plans.owner(self.admin, 'inspect', revised['plan_id'])
        self.assertIn('Make the ending shorter', inspected['owner_review']['feedback'])
        self.approve(revised); self.submit(revised); self.step(2); self.review(2, 'accept')
        receipt = self.plans.receipt(self.reader, revised['plan_id'])
        self.assertEqual(receipt['task_state'], 'ready')
        self.assertTrue(receipt['draft_attached']); self.assertFalse(receipt['video_spending_approved'])
        self.assertEqual(len(self.provider.calls), 2)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM meta WHERE key LIKE 'worker:%'").fetchone()[0], 0)

    def test_identity_pin_tenant_and_removed_membership_fail_closed(self):
        plan = self.prepare()
        self.f.identity['open_id'] = 'ou_other'
        with self.store.connect() as db:
            binding = meta(db, 'feishu:binding:' + self.project); binding['submitters'].append('ou_other')
            save(db, 'feishu:binding:' + self.project, binding)
        with self.assertRaisesRegex(RuntimeFault, 'IDENTITY_CHANGED'): self.prepare('second')
        self.f.identity['open_id'] = 'ou_employee'; self.f.identity['tenant_key'] = 'other_tenant'
        with self.assertRaisesRegex(RuntimeFault, 'TENANT_OR_ROLE_DENIED'): self.approve(plan)
        self.f.identity['tenant_key'] = 'fixture_tenant'
        with self.store.connect() as db:
            binding['submitters'] = ['ou_other']; save(db, 'feishu:binding:' + self.project, binding)
        with self.assertRaisesRegex(RuntimeFault, 'TENANT_OR_ROLE_DENIED'): self.approve(plan)

    def test_scope_isolation_and_authenticate_before_external_reads(self):
        before = self.f.reads
        for token in (self.reader, self.admin, self.execution):
            with self.assertRaisesRegex(RuntimeFault, 'AUTH_AGENT_DENIED'):
                self.plans.prepare(token, 'bad', self.request)
        self.assertEqual(before, self.f.reads)
        plan = self.prepare()
        for token in (self.reader, self.planner, self.execution):
            with self.assertRaisesRegex(RuntimeFault, 'AUTH_REQUIRED'):
                self.plans.owner(token, 'approve', plan['plan_id'], plan['plan_sha256'])
        with self.assertRaisesRegex(RuntimeFault, 'AUTH_EXECUTION'):
            self.jobs.step(self.planner, self.project, 'generated_one', 1, self.key)
        self.store.put_project(self.admin, 'other', {'video_route': 'deferred', 'credential_ref': 'secret:script', 'billing_owner': 'fixture'})
        other = automation.issue(self.store, self.admin, 'other', scope='agent_plan')['token']
        with self.assertRaisesRegex(RuntimeFault, 'AUTH_AGENT_DENIED'):
            self.plans.receipt(other, plan['plan_id'])
        with self.assertRaisesRegex(RuntimeFault, 'PLAN_MISSING'):
            ScriptPlans(self.store, 'other').receipt(other, plan['plan_id'])

    def test_replay_expiry_and_request_id_conflict(self):
        plan = self.prepare(); approved = self.approve(plan)
        self.assertEqual(approved['expires_at'], self.approve(plan)['expires_at'])
        before = self.f.reads
        self.assertTrue(self.prepare()['replayed']); self.assertEqual(before, self.f.reads)
        with self.assertRaisesRegex(RuntimeFault, 'REQUEST_ID_CONFLICT'):
            self.prepare(request={**self.request, 'brief': 'changed'})
        with self.store.connect() as db:
            key = 'agent:script:plan:' + plan['plan_id']; saved = meta(db, key)
            saved['expires_at'] = 0; save(db, key, saved)
        self.assertEqual(self.prepare()['phase'], 'expired')
        with self.assertRaisesRegex(RuntimeFault, 'EXPIRED'): self.submit(plan)
        self.assertTrue(self.plans.receipt(self.reader, plan['plan_id'])['approval_required'])

    def test_concurrent_enqueue_and_restart_share_one_receipt(self):
        plan = self.prepare(); self.approve(plan)
        with ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(lambda _: self.submit(plan), range(8)))
        self.assertEqual(sum(not r['replayed'] for r in replies), 1)
        self.plans = self.service(store=RuntimeStore(self.store.root))
        self.assertTrue(self.submit(plan)['replayed'])
        self.step(); self.submit(plan); self.step()
        self.assertEqual(len(self.provider.calls), 1)

    def test_two_plans_for_one_revision_cannot_overwrite_job(self):
        first = self.prepare('first'); second = self.prepare('second', {**self.request, 'brief': 'Second brief'})
        self.approve(first); self.approve(second); self.submit(first)
        with self.assertRaises(RuntimeFault): self.submit(second)
        with self.store.connect() as db:
            self.assertEqual(meta(db, job_key(self.project, 'generated_one', 1))['agent_plan_id'], first['plan_id'])

    def test_unknown_submit_and_failed_attachment_are_never_resent(self):
        plan = self.prepare(); self.approve(plan); self.submit(plan)
        self.provider.fail = True; self.step()
        for _ in range(3): self.submit(plan); self.step()
        self.assertTrue(self.plans.receipt(self.reader, plan['plan_id'])['reconciliation_required'])
        self.assertEqual(len(self.provider.calls), 1)
        with self.assertRaisesRegex(RuntimeFault, 'ALREADY_STARTED'):
            self.plans.owner(self.admin, 'revoke', plan['plan_id'], plan['plan_sha256'])

    def test_received_script_resumes_attachment_after_restart(self):
        plan = self.prepare(); self.approve(plan); self.submit(plan)
        with patch.object(self.jobs, '_attach', side_effect=RuntimeError('synthetic-attachment-failure')):
            with self.assertRaises(RuntimeError): self.step()
        before = self.plans.receipt(self.reader, plan['plan_id'])
        self.assertTrue(before['provider_receipt_recorded']); self.assertFalse(before['draft_attached'])
        self.step()
        after = self.plans.receipt(self.reader, plan['plan_id'])
        self.assertTrue(after['draft_attached']); self.assertEqual(after['task_state'], 'awaiting_script_review')
        self.assertEqual(len(self.provider.calls), 1)

    def test_native_record_is_required_and_source_changes_block_enqueue(self):
        source = self.native()
        with self.assertRaisesRegex(RuntimeFault, 'USE_NATIVE_RECORD'): self.prepare()
        request = {'record': 'recFixture', 'expected_revision': 0}
        plan = self.prepare(request=request); self.approve(plan)
        source.fields['brief'] = 'Changed original Base brief'
        with self.assertRaisesRegex(RuntimeFault, 'PLAN_CHANGED'): self.submit(plan)
        source.fields['brief'] = 'Original private Base brief'
        self.submit(plan)
        source.fields['version'] = 'source_two'
        with self.assertRaisesRegex(RuntimeFault, 'NATIVE_SOURCE_CHANGED'): self.step()
        self.assertEqual(self.provider.calls, [])
        source.fields['version'] = 'source_one'; self.step()
        self.assertTrue(self.plans.receipt(self.reader, plan['plan_id'])['draft_attached'])

    def test_native_record_mismatch_and_disabled_binding_are_rejected(self):
        with self.assertRaisesRegex(RuntimeFault, 'NATIVE_REVIEW_REQUIRED'):
            self.prepare(request={'record': 'recFixture', 'expected_revision': 0})
        self.native()
        with self.assertRaisesRegex(RuntimeFault, 'NATIVE_SOURCE_CONFLICT'):
            self.prepare(request={'record': 'recDifferent', 'expected_revision': 0})

    def test_credential_config_and_sku_drift_block_old_approval(self):
        plan = self.prepare(); self.approve(plan)
        self.store.put_secret(self.admin, 'script', 'synthetic-new-secret', self.key)
        with self.assertRaisesRegex(RuntimeFault, 'CREDENTIAL_CHANGED'): self.submit(plan)
        profile(self.store, self.admin, self.project, 'secret:script', 'fixture')
        with self.assertRaisesRegex(RuntimeFault, 'PLAN_CHANGED'): self.submit(plan)
        with self.store.connect() as db:
            setup = meta(db, 'setup:project:' + self.project)
            setup['configuration']['project']['products'] = []; save(db, 'setup:project:' + self.project, setup)
        with self.assertRaisesRegex(RuntimeFault, 'SKU_NOT_IN_PROJECT'): self.submit(plan)

    def test_paid_intent_rechecks_project_facts_after_external_reads(self):
        plan = self.prepare(); self.approve(plan); self.submit(plan)
        resolve = self.store.resolve_secret
        for change in ('sku', 'configuration', 'deployment'):
            with self.subTest(change=change):
                with self.store.connect() as db:
                    setup = meta(db, 'setup:project:' + self.project)
                    config = db.execute('SELECT digest FROM projects WHERE id=?', (self.project,)).fetchone()[0]
                    deployment = db.execute("SELECT value FROM meta WHERE key='deployment'").fetchone()[0]
                def mutate(*args):
                    secret = resolve(*args)
                    with self.store.connect() as db:
                        if change == 'sku':
                            modified = json.loads(json.dumps(setup))
                            modified['configuration']['project']['products'] = []
                            save(db, 'setup:project:' + self.project, modified)
                        elif change == 'configuration':
                            db.execute('UPDATE projects SET digest=? WHERE id=?', ('changed', self.project))
                        else:
                            db.execute("UPDATE meta SET value='changed' WHERE key='deployment'")
                    return secret
                with patch.object(self.store, 'resolve_secret', side_effect=mutate):
                    with self.assertRaisesRegex(RuntimeFault, 'PROJECT_CONFIGURATION_CHANGED' if change == 'configuration' else 'SCRIPT_PLAN_CHANGED'): self.step()
                with self.store.connect() as db:
                    self.assertEqual(meta(db, job_key(self.project, 'generated_one', 1))['state'], 'queued')
                    save(db, 'setup:project:' + self.project, setup)
                    db.execute('UPDATE projects SET digest=? WHERE id=?', (config, self.project))
                    db.execute("UPDATE meta SET value=? WHERE key='deployment'", (deployment,))
                self.assertEqual(self.provider.calls, [])

    def test_commit_rechecks_token_and_credentials_after_external_reads(self):
        original = self.jobs.prepare
        def rotate(*args, **kwargs):
            prepared = original(*args, **kwargs)
            self.store.put_secret(self.admin, 'script', 'synthetic-rotated-secret', self.key)
            return prepared
        with patch.object(self.jobs, 'prepare', side_effect=rotate):
            with self.assertRaisesRegex(RuntimeFault, 'CREDENTIAL_CHANGED'): self.prepare()
        profile(self.store, self.admin, self.project, 'secret:script', 'fixture')
        def revoke(*args, **kwargs):
            prepared = original(*args, **kwargs)
            automation.revoke(self.store, self.admin, hashlib.sha256(self.planner.encode()).hexdigest())
            return prepared
        with patch.object(self.jobs, 'prepare', side_effect=revoke):
            with self.assertRaisesRegex(RuntimeFault, 'AUTH_AGENT_DENIED'): self.prepare()

    def test_atomic_enqueue_rolls_back_task_job_and_plan(self):
        plan = self.prepare(); self.approve(plan)
        with patch('video_factory.agent_script_plans.save', side_effect=RuntimeError('synthetic-write-failure')):
            with self.assertRaises(RuntimeError): self.submit(plan)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 0)
            self.assertIsNone(meta(db, job_key(self.project, 'generated_one', 1)))
        self.assertFalse(self.submit(plan)['replayed'])

    def test_revocation_before_and_after_enqueue_and_restore(self):
        plan = self.prepare(); self.approve(plan)
        self.plans.owner(self.admin, 'revoke', plan['plan_id'], plan['plan_sha256'])
        with self.assertRaisesRegex(RuntimeFault, 'REVOKED'): self.submit(plan)
        plan = self.prepare('second'); self.approve(plan); self.submit(plan)
        receipt = self.plans.owner(self.admin, 'revoke', plan['plan_id'], plan['plan_sha256'])
        self.assertEqual(receipt['phase'], 'revoked'); self.assertEqual(receipt['task_state'], 'failed')
        self.step(); self.assertEqual(self.provider.calls, [])
        next_plan = self.prepare('third', {**self.request, 'expected_revision': 1})
        self.approve(next_plan)
        with self.store.connect() as db: self.store.invalidate_worker_approvals(db)
        self.planner = automation.issue(self.store, self.admin, self.project, scope='agent_plan')['token']
        with self.assertRaisesRegex(RuntimeFault, 'REVOKED'): self.submit(next_plan)

    def test_responses_redact_content_identity_credentials_and_provider_data(self):
        plan = self.prepare(); self.approve(plan); self.submit(plan); self.step()
        data = json.dumps([plan, self.plans.receipt(self.reader, plan['plan_id'])])
        for secret in ('Synthetic private brief', 'Synthetic generated private script', 'secret:script',
                       'ou_employee', self.f.user_token, self.admin, self.planner, str(self.root),
                       'synthetic-private-provider-id'):
            self.assertNotIn(secret, data)
        owner = self.plans.owner(self.admin, 'inspect', plan['plan_id'])['owner_review']
        self.assertEqual(owner['brief'], self.request['brief'])
        self.assertFalse(owner['feishu_cost_owner_verified'])

    def test_http_flags_scopes_no_owner_route_and_long_unicode_brief(self):
        disabled = AgentAPI(self.store, self.project, Scheduler(self.store, self.project))
        body = {'project': self.project, 'request_id': 'unicode_request', **self.request, 'brief': '商品描述' * 1200}
        with self.assertRaisesRegex(RuntimeFault, 'PLANS_DISABLED'):
            disabled.call('/v1/agent/script/prepare', self.planner, body)
        api = AgentAPI(self.store, self.project, Scheduler(self.store, self.project), script_plans=self.plans)
        with AgentServer(('127.0.0.1', 0), api) as server:
            thread = threading.Thread(target=server.serve_forever); thread.start()
            try:
                def call(route, data, token=self.planner):
                    conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
                    try:
                        conn.request('POST', '/v1/agent/' + route, json.dumps(data), {
                            'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
                        response = conn.getresponse(); return response.status, json.loads(response.read())
                    finally: conn.close()
                self.assertTrue(call('capabilities', {'project': self.project})[1]['script_plan_available'])
                self.assertFalse(call('capabilities', {'project': self.project}, self.reader)[1]['script_plan_available'])
                self.assertEqual(call('script/prepare', body, self.reader)[0], 401)
                self.assertEqual(call('script/prepare', {**body, 'user_token': 'forbidden'})[0], 409)
                code, plan = call('script/prepare', body); self.assertEqual(code, 200)
                self.assertEqual(call('script/approve', {'project': self.project})[1]['error'], 'AGENT_ROUTE_NOT_AVAILABLE')
                with self.assertRaisesRegex(RuntimeFault, 'DIGEST_REQUIRED'):
                    self.plans.owner(self.admin, 'approve', plan['plan_id'], 'wrong')
            finally: server.shutdown(); thread.join(5)

    def test_cli_and_private_identity_configuration_fail_closed(self):
        token_file = self.root / 'feishu.token'; exclusive_write(token_file, self.f.user_token.encode())
        with self.assertRaisesRegex(RuntimeFault, 'EXPLICIT_ENABLE'):
            configured_script_plans(self.store, self.project, False, token_file, 'ou_employee')
        with self.assertRaisesRegex(RuntimeFault, 'IDENTITY_INPUTS'):
            configured_script_plans(self.store, self.project, True, None, 'ou_employee')
        configured = configured_script_plans(self.store, self.project, True, token_file, 'ou_employee')
        self.assertEqual(configured.user_token_provider(), self.f.user_token)
        token_file.chmod(0o644)
        with self.assertRaises(RuntimeFault): configured.user_token_provider()
        plan = self.prepare(); admin_file = self.root / 'admin.token'; exclusive_write(admin_file, self.admin.encode())
        cmd = [sys.executable, '-c', 'from video_factory.cli import main; raise SystemExit(main())',
               'agent-script-plan', 'approve', '--root', str(self.store.root), '--project', self.project,
               '--admin-token-file', str(admin_file), '--plan-id', plan['plan_id'], '--plan-sha256', plan['plan_sha256']]
        denied = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        self.assertEqual(denied.returncode, 2); self.assertIn('EXPLICIT_PROVIDER_CHARGES_REQUIRED', denied.stdout)
        missing = subprocess.run(cmd + ['--accept-provider-charges'], capture_output=True, text=True, timeout=20)
        self.assertEqual(missing.returncode, 2); self.assertIn('IDENTITY_INPUTS_REQUIRED', missing.stdout)
        self.assertNotIn(self.admin, denied.stdout + missing.stdout)
        token_file.chmod(0o600)
        output = io.StringIO()
        with patch('video_factory.agent_script_plans.ScriptJobs', return_value=self.jobs), redirect_stdout(output):
            result = main(cmd[3:] + ['--accept-provider-charges', '--script-user-token-file', str(token_file),
                                    '--script-open-id', 'ou_employee'])
        self.assertEqual(result, 0, output.getvalue())
        self.assertEqual(json.loads(output.getvalue())['phase'], 'approved')
        for secret in (self.admin, self.f.user_token, self.request['brief']):
            self.assertNotIn(secret, output.getvalue())

    def test_real_cli_enables_script_capabilities_without_dispatch_or_identity_calls(self):
        token_file = self.root / 'delegated.token'; exclusive_write(token_file, self.f.user_token.encode())
        cmd = [sys.executable, '-c', 'from video_factory.cli import main; raise SystemExit(main())',
               'up', '--root', str(self.store.root), '--project', self.project, '--port', '0',
               '--agent-script-plans', '--script-user-token-file', str(token_file), '--script-open-id', 'ou_employee']
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(select.select([process.stdout], [], [], 20)[0], 'startup timed out')
            ready = json.loads(process.stdout.readline())
            self.assertFalse(ready['execution_enabled'])
            conn = http.client.HTTPConnection('127.0.0.1', ready['port'], timeout=5)
            try:
                conn.request('POST', '/v1/agent/capabilities', json.dumps({'project': self.project}),
                             {'Authorization': 'Bearer ' + self.planner, 'Content-Type': 'application/json'})
                response = conn.getresponse(); caps = json.loads(response.read())
                self.assertEqual(response.status, 200); self.assertTrue(caps['script_plan_available'])
            finally: conn.close()
            process.terminate(); stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertEqual(stderr, '')
            self.assertNotIn(self.f.user_token, stdout)
        finally:
            if process.poll() is None: process.kill(); process.communicate(timeout=5)
        self.assertEqual(self.provider.calls, [])


if __name__ == '__main__': unittest.main()
