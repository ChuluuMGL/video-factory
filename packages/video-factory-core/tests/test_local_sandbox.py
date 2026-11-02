"""Cloud-only CLI acceptance of the installed no-n8n fake profile."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time
import unittest
from video_factory.local_sandbox import install, plan, owner, fake_dispatch
from video_factory.runtime_store import RuntimeStore, RuntimeFault
from video_factory import automation
from video_factory.scheduler import Scheduler


class LocalSandboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'fresh'
        install(self.root, 'local_trial')
        self.store = RuntimeStore(self.root)
        self.admin = owner(self.store)

    def cli(self, *args):
        cmd = [sys.executable, '-c', 'from video_factory.cli import main; raise SystemExit(main())',
               'local', *args, '--root', str(self.root)]
        return subprocess.run(cmd, text=True, capture_output=True, timeout=20)

    def tick(self):
        token = automation.issue_execution(self.store, self.admin, 'local_trial')['token']
        return Scheduler(self.store, 'local_trial', execution_token=token, master_key=b'fake-only',
                         media_root=self.root, dispatch=fake_dispatch).tick()

    def test_actual_cli_process_plan_reject_revise_accept_and_restart(self):
        r = self.cli('plan', '--request-id', 'first', '--brief', '合成场景')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        command = [sys.executable, '-c', 'from video_factory.cli import main; raise SystemExit(main())',
                   'local', 'up', '--root', str(self.root), '--port', '0']
        for revision in (1, 2):
            proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertTrue(select.select([proc.stdout], [], [], 15)[0])
                ready = json.loads(proc.stdout.readline())
                self.assertEqual(ready['scope'], 'local_fake_only')
                self.assertFalse(ready['n8n_required'])
                second = subprocess.run(command, capture_output=True, text=True, timeout=15)
                self.assertEqual(second.returncode, 2)
                self.assertIn('LOCAL_RUNTIME_ALREADY_RUNNING', second.stdout)
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    status = json.loads(self.cli('status').stdout)
                    if len(status['tasks']) == revision: break
                    time.sleep(.1)
                self.assertEqual(len(status['tasks']), revision, status)
                self.assertEqual(status['tasks'][-1]['state'], 'awaiting_script_review')
                if revision == 2: self.assertIn('减少产品特写', status['tasks'][-1]['input'])
                proc.terminate(); out, err = proc.communicate(timeout=10)
                self.assertEqual(proc.returncode, 0, out + err)
            finally:
                if proc.poll() is None: proc.kill(); proc.communicate(timeout=5)
            result = self.cli('review', '--expected-revision', str(revision), '--request-id', 'review-' + str(revision),
                              '--decision', 'reject' if revision == 1 else 'accept', '--feedback', '减少产品特写')
            self.assertEqual(result.returncode, 0, result.stdout)
            if revision == 1:
                result = self.cli('plan', '--expected-revision', '1', '--request-id', 'revision-2')
                self.assertEqual(result.returncode, 0, result.stdout)
        final = json.loads(self.cli('status').stdout)
        self.assertEqual([t['state'] for t in final['tasks']], ['rejected', 'ready'])
        self.assertEqual(len(final['reviews']), 2)
        self.assertEqual(final['real_model_calls'], 0)
        self.assertEqual(final['video_generation'], 'not_enabled')
        self.tick(); self.assertEqual(len(json.loads(self.cli('status').stdout)['tasks']), 2)

    def test_existing_directory_credentials_and_unrelated_ledger_are_preserved(self):
        files = {p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()}
        with self.assertRaises(FileExistsError): install(self.root, 'other')
        self.assertEqual(files, {p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()})
        for name in ('owner.password', 'agent-read.token'):
            self.assertEqual((self.root / name).stat().st_mode & 0o777, 0o600)
            self.assertNotIn((self.root / name).read_text(), self.cli('status').stdout)
        self.assertNotEqual(self.cli('status', '--project', 'other').returncode, 0)

    def test_idempotency_and_stale_revision_and_paid_route_denied(self):
        p = plan(self.store, self.admin, 'local_trial', 'trial_001', 0, 'brief', 'same')
        self.assertEqual(p, plan(self.store, self.admin, 'local_trial', 'trial_001', 0, 'brief', 'same'))
        with self.assertRaisesRegex(RuntimeFault, 'CONFLICT'):
            plan(self.store, self.admin, 'local_trial', 'trial_001', 0, 'changed', 'same')
        self.tick(); self.tick()
        self.assertEqual(len(self.store.inspect_task(self.admin, 'local_trial', 'trial_001')['versions']), 1)
        self.assertNotEqual(self.cli('review', '--expected-revision', '2', '--request-id', 'stale', '--decision', 'accept').returncode, 0)
        cmd = [sys.executable, '-c', 'from video_factory.cli import main; raise SystemExit(main())', 'up',
               '--root', str(self.root), '--project', 'local_trial', '--execute-approved']
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        self.assertIn('LOCAL_FAKE_PROFILE_NO_PAID_OR_OWNER_PLAN_ROUTE', r.stdout)


if __name__ == '__main__': unittest.main()
