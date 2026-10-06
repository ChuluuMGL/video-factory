import json
from contextlib import nullcontext
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from video_factory import runner
from video_factory import production_setup
from video_factory.runtime_store import RuntimeFault


class StackFixture:
    def __init__(self, root):
        self.root = Path(root)
        self.config = {'deployment': 'customer', 'instance': 'a' * 12,
                       'runtime_image': 'sha256:' + 'b' * 64}
    def status(self):
        return {'infrastructure_ready': True}


class RunnerTests(unittest.TestCase):
    def test_companion_has_only_private_executor_and_egress(self):
        with tempfile.TemporaryDirectory() as tmp:
            stack = StackFixture(tmp)
            base = {'services': {'runtime': {'environment': {'VF_CONTAINER_MODE': '1'}},
                                 'egress': {'image': 'sha256:' + 'c' * 64,
                                            'healthcheck': {'test': ['CMD', 'true']}}}}
            with patch.object(runner, 'compose_document', return_value=base):
                value = runner.document(stack, runner.plan(stack, 'brand'))
            self.assertEqual(set(value['services']), {'executor', 'egress'})
            self.assertEqual(value['services']['executor']['networks']['ledger']['aliases'],
                             ['vf-executor-' + __import__('hashlib').sha256(b'brand').hexdigest()[:12]])
            self.assertNotIn('ports', value['services']['executor'])
            self.assertNotIn('ports', value['services']['egress'])
            self.assertNotIn('public', value['networks'])
            self.assertEqual(value['networks']['ledger']['name'], 'vf-customer-' + 'a' * 12 + '_private')
            self.assertEqual(set(value['secrets']), {'runtime_dsn', 'runtime_master'})

    def test_apply_refuses_legacy_web_companion_for_same_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            stack = StackFixture(tmp)
            legacy = stack.root / 'data/workspaces/brand'
            legacy.mkdir(parents=True)
            (legacy / 'workspace.json').write_text('{}')
            with self.assertRaisesRegex(RuntimeFault, 'RUNNER_LEGACY_WORKSPACE_CONFLICT'):
                runner.apply(stack, runner.plan(stack, 'brand'))
            self.assertFalse((stack.root / 'data/runners/brand').exists())

    def test_status_requires_both_healthy_components(self):
        with tempfile.TemporaryDirectory() as tmp:
            stack = StackFixture(tmp)
            root = runner.directory(stack, 'brand')
            root.mkdir(parents=True)
            (root / 'runner.json').write_text(json.dumps(runner.plan(stack, 'brand')))
            rows = [{'Service': name, 'State': 'running', 'Health': 'healthy'}
                    for name in ('executor', 'egress')]
            with patch.object(runner, 'compose', return_value=json.dumps(rows).encode()):
                self.assertEqual(runner.status(stack, 'brand')['status'], 'running')
                rows[1]['Health'] = 'unhealthy'
                self.assertEqual(runner.status(stack, 'brand')['status'], 'incomplete')

    def test_production_setup_accepts_private_runner_without_web(self):
        stack = SimpleNamespace(lock=nullcontext, status=lambda: {'infrastructure_ready': True})
        session = {'configuration': {'project': {'id': 'brand'}}}
        args = SimpleNamespace(stack_root=Path('/synthetic/stack'), session=Path('/synthetic/session'))
        with (patch.object(production_setup, 'Stack', return_value=stack),
              patch.object(production_setup, 'SessionStore') as sessions,
              patch.object(production_setup, 'rpc', side_effect=[{'token': 'synthetic-token'}, None]),
              patch.object(production_setup, 'runner_status', return_value={'status': 'running'}),
              patch.object(production_setup, 'workspace_status', side_effect=AssertionError('web route used')),
              patch.object(production_setup, 'choice', side_effect=[False, False, False])):
            sessions.return_value.read.return_value = session
            result = production_setup.welcome(args, hidden=lambda _: 'synthetic-password', write=lambda _: None)
        self.assertEqual(result['status'], 'credentials_saved_schedule_not_changed')
