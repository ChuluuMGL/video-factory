"""Synthetic-only local-kernel acceptance. Run on cloud Linux and macOS."""
from functools import partial
import hashlib
import http.client
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
from video_factory import automation
from video_factory.agent_api import AgentAPI, AgentServer
from video_factory.dispatch import dispatch_one
from video_factory.local_runtime import serve
from video_factory.runtime_store import RuntimeStore, RuntimeFault, canonical, exclusive_write
from video_factory.scheduler import Scheduler, runtime_lock
from video_factory.worker import Worker, job_key


class Provider:
    def __init__(self):
        self.calls = 0
        self.mode = 'ok'

    def submit(self, body, secret):
        self.calls += 1
        if self.mode == 'unknown':
            raise TimeoutError('synthetic-sensitive-provider-body')
        return str(12345678901230 + self.calls)

    def poll(self, provider_id, secret):
        return {'id': provider_id, 'status': 'running' if self.mode == 'pending' else 'succeeded',
                'url': 'fixture://synthetic-media'}


class LocalKernelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = RuntimeStore.install(self.root, 'fixture', 'synthetic-admin-password')
        self.admin = self.store.login('admin', 'synthetic-admin-password')['token']
        self.key = Fernet.generate_key()
        self.store.put_secret(self.admin, 'fixture', 'synthetic-provider-value', self.key)
        self.store.put_project(self.admin, 'brand', {'video_route': 'deferred',
                               'credential_ref': 'secret:fixture', 'billing_owner': 'fixture'})
        self.assets = self.root / 'assets'
        self.assets.mkdir(mode=0o700)
        (self.assets / 'image.png').write_bytes(b'synthetic-image')
        self.spec = {'duration': 5, 'references': [{'path': 'image.png',
                     'sha256': hashlib.sha256(b'synthetic-image').hexdigest()}]}
        self.provider = Provider()
        self.execution = automation.issue_execution(self.store, self.admin, 'brand')['token']
        self.reader = automation.issue(self.store, self.admin, 'brand', scope='agent_read')['token']
        self.dispatch = partial(dispatch_one, worker_factory=self.worker)
        # Accidental use of a real transport fails before any network request.
        self.addCleanup(patch.stopall)
        patch('video_factory.h3_provider.H3Provider.call', side_effect=AssertionError('real provider forbidden')).start()

    def worker(self, store):
        return Worker(store, provider_factory=lambda _: self.provider, media_preflight=lambda: None,
                      media_collector=lambda *_: {'sha256': 'a' * 64,
                      'location': str(self.root / 'private.mp4'), 'verification': 'full_decode_passed'})

    def task(self, name='one', approve=True):
        self.store.create_task(self.admin, 'brand', name,
                               {'sku_id': 'sku', 'script': 'synthetic-private-script', 'source_revision': '1'})
        self.store.review(self.admin, 'review-' + name, 'brand', name, 1, 'script', 'accept')
        if approve:
            worker = self.worker(self.store)
            args = dict(project='brand', task=name, revision=1, assets_root=self.assets,
                        specification=self.spec, credential_ref='secret:fixture',
                        billing_owner='fixture', region='global')
            plan = worker.prepare(self.admin, **args)
            worker.approve(self.admin, plan['request_plan_sha256'], **args)

    def scheduler(self, **kwargs):
        return Scheduler(self.store, 'brand', execution_token=self.execution, master_key=self.key,
                         media_root=self.root, dispatch=self.dispatch, **kwargs)

    def state(self, name='one'):
        return self.store.inspect_task(self.admin, 'brand', name)['versions'][-1]['state']

    def test_readonly_default_does_not_consume_even_approved_job(self):
        self.task()
        scheduler = Scheduler(self.store, 'brand', dispatch=lambda *_: self.fail('dispatched'))
        scheduler.tick()
        self.assertEqual(scheduler.status()['state'], 'read_only')
        self.assertEqual(self.state(), 'ready')
        self.assertEqual(self.provider.calls, 0)

    def test_unapproved_and_expired_never_submit(self):
        self.task(approve=False)
        scheduler = self.scheduler()
        scheduler.tick()
        self.assertEqual(self.provider.calls, 0)
        self.task('expired')
        with self.store.connect() as db:
            key = job_key('brand', 'expired', 1)
            job = json.loads(db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()[0])
            job['expires_at'] = 0
            db.execute('UPDATE meta SET value=? WHERE key=?', (canonical(job), key))
        scheduler.tick()
        self.assertEqual(self.provider.calls, 0)

    def test_restart_continues_receipt_once_and_stops_at_human_review(self):
        self.task()
        self.scheduler().tick()
        self.assertEqual(self.state(), 'submitted')
        self.store = RuntimeStore(self.root)
        scheduler = self.scheduler()
        for _ in range(4):
            scheduler.tick()
        self.assertEqual(self.state(), 'awaiting_video_review')
        self.assertEqual(self.provider.calls, 1)

    def test_unknown_submission_survives_restart_without_resubmit(self):
        self.task()
        self.provider.mode = 'unknown'
        self.scheduler().tick()
        self.store = RuntimeStore(self.root)
        scheduler = self.scheduler()
        for _ in range(4):
            scheduler.tick()
        self.assertEqual(self.state(), 'submission_unknown')
        self.assertEqual(self.provider.calls, 1)

    def test_pending_first_task_does_not_starve_next_task(self):
        self.task('a')
        self.task('b')
        self.provider.mode = 'pending'
        scheduler = self.scheduler()
        scheduler.tick()
        scheduler.tick()
        self.assertEqual(self.state('a'), 'submitted')
        self.assertEqual(self.state('b'), 'submitted')
        self.assertEqual(self.provider.calls, 2)

    def test_invalid_first_task_is_checkpointed_before_error(self):
        self.task('a')
        self.task('b')
        with self.store.connect() as db:
            db.execute("UPDATE tasks SET input_digest='changed' WHERE id='a'")
        scheduler = self.scheduler()
        self.assertEqual(scheduler.tick()['last_error'], 'SCHEDULER_STEP_FAILED')
        scheduler.tick()
        self.assertEqual(self.state('b'), 'submitted')
        self.assertEqual(self.provider.calls, 1)

    def test_cursor_pages_past_more_than_one_thousand_unapproved_tasks(self):
        self.task('zz-approved')
        with self.store.connect() as db:
            db.executemany("INSERT INTO tasks(project,id,revision,input,input_digest,configuration_digest,state) "
                           "VALUES('brand',?,1,'{}','fixture','fixture','ready')",
                           [('t%04d' % i,) for i in range(1005)])
        scheduler = self.scheduler()
        for _ in range(12):
            scheduler.tick()
        self.assertEqual(self.state('zz-approved'), 'submitted')
        self.assertEqual(self.provider.calls, 1)

    def test_revoked_execution_key_and_changed_credential_prevent_calls(self):
        self.task()
        scheduler = self.scheduler()
        self.store.put_secret(self.admin, 'fixture', 'synthetic-changed-value', self.key)
        self.assertEqual(scheduler.tick()['state'], 'attention_required')
        automation.revoke(self.store, self.admin, hashlib.sha256(self.execution.encode()).hexdigest())
        self.assertEqual(scheduler.tick()['state'], 'attention_required')
        self.assertEqual(self.provider.calls, 0)

    def test_single_flight_tick_and_sanitized_failure(self):
        entered, release = threading.Event(), threading.Event()
        def dispatch(*args, **kwargs):
            entered.set()
            release.wait(5)
            raise RuntimeError('synthetic-sensitive-provider-body')
        scheduler = self.scheduler()
        scheduler.dispatch = dispatch
        thread = threading.Thread(target=scheduler.tick)
        thread.start()
        try:
            self.assertTrue(entered.wait(3))
            self.assertEqual(scheduler.tick()['state'], 'busy')
        finally:
            release.set()
            thread.join(5)
        self.assertNotIn('sensitive', json.dumps(scheduler.status()))
        self.assertEqual(scheduler.status()['last_error'], 'SCHEDULER_STEP_FAILED')

    def test_stop_prevents_tick_and_missed_intervals_do_not_burst(self):
        scheduler = self.scheduler()
        stop = threading.Event()
        stop.set()
        with patch.object(scheduler, 'tick') as tick:
            scheduler.run(stop)
            tick.assert_not_called()
        class WakeOnce:
            stopped = False
            def is_set(self): return self.stopped
            def wait(self, interval): self.stopped = True
        with patch.object(scheduler, 'tick') as tick:
            scheduler.run(WakeOnce())
            self.assertEqual(tick.call_count, 1)

    def test_script_jobs_reuse_actual_worker_and_preserve_unknown_state(self):
        from review_fixture import Fixture
        from video_factory.feishu_bridge import save
        from video_factory.script_jobs import ScriptJobs, profile
        for unknown in (False, True):
            with self.subTest(unknown=unknown):
                root = self.root / ('script-unknown' if unknown else 'script-ok')
                root.mkdir(mode=0o700)
                fixture = Fixture(root)
                try:
                    store, project = fixture.store, fixture.project
                    with store.connect() as db:
                        save(db, 'setup:project:' + project, {'configuration': {'project': {
                            'base_mode': 'bind', 'products': [{'sku_id': 'sku_one',
                            'name': 'Example', 'truth_source': 'Synthetic packaging size'}]}}})
                    store.put_secret(fixture.admin, 'script', 'synthetic-script-key', self.key)
                    profile(store, fixture.admin, project, 'secret:script', 'fixture')
                    calls = []
                    class ScriptProvider:
                        def generate(self, *_):
                            calls.append(True)
                            if unknown: raise TimeoutError('synthetic-sensitive-provider-body')
                            return {'script': 'Synthetic generated script', 'provider_id': 'synthetic-receipt'}
                    jobs = ScriptJobs(store, client_factory=fixture.bridge.client_factory, provider=ScriptProvider())
                    values = dict(project=project, task='generated_one', sku_id='sku_one',
                                  brief='Synthetic brief', expected_revision=0)
                    plan = jobs.prepare('synthetic-user-token', **values)
                    jobs.submit('synthetic-user-token', plan['plan_sha256'], **values)
                    token = automation.issue_execution(store, fixture.admin, project)['token']
                    with patch('video_factory.script_jobs.ScriptJobs', return_value=jobs):
                        scheduler = Scheduler(store, project, execution_token=token, master_key=self.key, media_root=root)
                        scheduler.tick()
                        scheduler = Scheduler(store, project, execution_token=token, master_key=self.key, media_root=root)
                        for _ in range(4): scheduler.tick()
                    self.assertEqual(len(calls), 1)
                    state = store.inspect_task(fixture.admin, project, 'generated_one')['versions'][-1]['state']
                    self.assertEqual(state, 'script_submission_unknown' if unknown else 'awaiting_script_review')
                finally:
                    fixture.close()

    def api(self):
        return AgentAPI(self.store, 'brand', Scheduler(self.store, 'brand'))

    def test_agent_capabilities_and_metadata_have_no_secrets_or_paths(self):
        self.task()
        api = self.api()
        caps = api.call('/v1/agent/capabilities', self.reader, {'project': 'brand'})
        self.assertFalse(caps['mutations_allowed'])
        self.assertIn('submit_plan', caps['unavailable'])
        data = api.call('/v1/agent/task', self.reader, {'project': 'brand', 'task': 'one', 'revision': 1})
        text = json.dumps(data)
        for forbidden in ('private-script', 'secret:', str(self.root), self.reader, self.admin):
            self.assertNotIn(forbidden, text)
        self.assertEqual(data['task']['state'], 'ready')

    def test_agent_auth_scope_project_expiry_and_revocation(self):
        api = self.api()
        queue = automation.issue(self.store, self.admin, 'brand')['token']
        for token in (queue, self.execution, self.admin, 'invalid'):
            with self.assertRaisesRegex(RuntimeFault, 'AUTH_AGENT'):
                api.call('/v1/agent/status', token, {'project': 'brand'})
        with self.assertRaisesRegex(RuntimeFault, 'PROJECT_DENIED'):
            api.call('/v1/agent/status', self.reader, {'project': 'other'})
        key = hashlib.sha256(self.reader.encode()).hexdigest()
        with self.store.connect() as db:
            value = json.loads(db.execute('SELECT value FROM meta WHERE key=?', ('automation:key:' + key,)).fetchone()[0])
            value['expires_at'] = 0
            db.execute('UPDATE meta SET value=? WHERE key=?', (canonical(value), 'automation:key:' + key))
        with self.assertRaisesRegex(RuntimeFault, 'AUTH_AGENT_DENIED'):
            api.call('/v1/agent/status', self.reader, {'project': 'brand'})
        automation.revoke(self.store, self.admin, key)
        with self.assertRaisesRegex(RuntimeFault, 'AUTH_AGENT_DENIED'):
            api.call('/v1/agent/status', self.reader, {'project': 'brand'})

    def test_agent_cannot_dispatch_or_review_and_rejects_stale_revision(self):
        self.task()
        with self.assertRaisesRegex(RuntimeFault, 'AUTH_EXECUTION'):
            dispatch_one(self.store, self.reader, 'brand', self.key, self.root)
        with self.assertRaisesRegex(RuntimeFault, 'AUTH_REQUIRED'):
            self.store.review(self.reader, 'bad-review', 'brand', 'one', 1, 'video', 'accept')
        for route in ('/v1/reviews', '/v1/agent/submit', '/v1/dispatch'):
            with self.assertRaisesRegex(RuntimeFault, 'NOT_AVAILABLE'):
                self.api().call(route, self.reader, {'project': 'brand'})
        with self.assertRaisesRegex(RuntimeFault, 'VERSION_CHANGED'):
            self.api().call('/v1/agent/task', self.reader, {'project': 'brand', 'task': 'one', 'revision': 2})
        with self.assertRaisesRegex(RuntimeFault, 'FIELDS_INVALID'):
            self.api().call('/v1/agent/status', self.reader, {'project': 'brand', 'approve': True})

    def test_agent_pagination_excludes_other_projects_and_old_versions(self):
        with self.store.connect() as db:
            for project in ('brand', 'other'):
                db.executemany("INSERT INTO tasks(project,id,revision,input,input_digest,configuration_digest,state) "
                               "VALUES(?,?,1,'{}','fixture','fixture','ready')",
                               [(project, 't%03d' % i) for i in range(105)])
            db.execute("INSERT INTO tasks(project,id,revision,input,input_digest,configuration_digest,state) "
                       "VALUES('brand','t000',2,'{}','new','fixture','awaiting_script_review')")
        api = self.api()
        page = api.call('/v1/agent/tasks', self.reader, {'project': 'brand'})
        self.assertEqual(len(page['items']), 100)
        self.assertEqual(page['items'][0]['revision'], 2)
        last = api.call('/v1/agent/tasks', self.reader, {'project': 'brand', 'after': page['next_after']})
        self.assertEqual(len(last['items']), 5)
        self.assertFalse(last['has_more'])

    def test_http_contract_refuses_cross_origin_and_mutation_routes(self):
        with AgentServer(('127.0.0.1', 0), self.api()) as server:
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                def call(route, extra=None):
                    conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                    headers = {'Authorization': 'Bearer ' + self.reader, 'Content-Type': 'application/json'}
                    headers.update(extra or {})
                    try:
                        conn.request('POST', route, json.dumps({'project': 'brand'}), headers)
                        response = conn.getresponse()
                        return response.status, json.loads(response.read())
                    finally:
                        conn.close()
                self.assertEqual(call('/v1/agent/status')[0], 200)
                self.assertEqual(call('/v1/agent/status', {'Origin': 'https://example.invalid'})[0], 403)
                self.assertEqual(call('/v1/agent/status', {'Host': 'attacker.invalid'})[0], 403)
                self.assertEqual(call('/v1/agent/submit')[1]['error'], 'AGENT_ROUTE_NOT_AVAILABLE')
            finally:
                server.shutdown()
                thread.join(5)

    def test_lock_excludes_second_owner_and_releases_without_unlinking(self):
        with runtime_lock(self.root):
            inode = (self.root / 'local-runtime.lock').stat().st_ino
            with self.assertRaisesRegex(RuntimeFault, 'ALREADY_RUNNING'):
                with runtime_lock(self.root): pass
        with runtime_lock(self.root):
            self.assertEqual((self.root / 'local-runtime.lock').stat().st_ino, inode)

    def test_lock_rejects_symlink_or_shared_permissions(self):
        path = self.root / 'local-runtime.lock'
        path.symlink_to(self.assets / 'target')
        with self.assertRaises(OSError):
            with runtime_lock(self.root): pass
        path.unlink()
        path.write_text('')
        path.chmod(0o644)
        with self.assertRaisesRegex(RuntimeFault, 'PRIVATE_FILE'):
            with runtime_lock(self.root): pass

    def test_start_failure_releases_lock_and_never_dispatches(self):
        stop = threading.Event()
        with patch('video_factory.local_runtime.AgentServer', side_effect=OSError('occupied')):
            with self.assertRaises(OSError):
                serve(self.store, 'brand', 0, stop)
        with runtime_lock(self.root): pass
        self.assertEqual(self.provider.calls, 0)

    def test_cli_start_stop_restart_and_second_process_lock(self):
        self.task()
        command = [sys.executable, '-c', 'from video_factory.cli import main; raise SystemExit(main())',
                   'up', '--root', str(self.root), '--project', 'brand', '--port', '0']
        for _ in range(2):
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertTrue(select.select([process.stdout], [], [], 20)[0], 'startup timed out')
                ready = json.loads(process.stdout.readline())
                self.assertEqual(ready['scope'], 'local_kernel_candidate')
                self.assertFalse(ready['execution_enabled'])
                duplicate = subprocess.run(command, capture_output=True, text=True, timeout=20)
                self.assertEqual(duplicate.returncode, 2)
                self.assertIn('LOCAL_RUNTIME_ALREADY_RUNNING', duplicate.stdout)
                process.terminate()
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, stderr)
                self.assertIn('stopped', stdout)
                self.assertEqual(stderr, '')
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)
            self.assertEqual(self.state(), 'ready')

    def test_agent_key_cli_writes_private_file_without_printing_token(self):
        admin_file, output = self.root / 'admin.token', self.root / 'agent.token'
        exclusive_write(admin_file, self.admin.encode())
        command = [sys.executable, '-c', 'from video_factory.cli import main; raise SystemExit(main())',
                   'agent-key', '--root', str(self.root), '--project', 'brand',
                   '--admin-token-file', str(admin_file), '--output', str(output)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        token = output.read_text()
        self.assertNotIn(token, result.stdout + result.stderr)
        self.assertNotIn(self.admin, result.stdout + result.stderr)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.api().call('/v1/agent/status', token, {'project': 'brand'})['state'], 'read_only')
        again = subprocess.run(command, capture_output=True, text=True, timeout=20)
        self.assertEqual(again.returncode, 2)
        self.assertEqual(output.read_text(), token)


if __name__ == '__main__':
    unittest.main()
