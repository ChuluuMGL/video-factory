import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch
from video_factory.runtime_store import RuntimeFault
from video_factory.stack_worker import run_stack_worker


class StackWorkerTests(unittest.TestCase):
    def args(self,action='step'):
        return SimpleNamespace(stack_root=Path('/customer'),action=action,project='brand',task='one',revision=1,
            token_file=Path('/work/admin.token'),assets_root=None,specification=None,credential_ref=None,
            billing_owner=None,region='global',expect_plan=None,allow_paid_submit=False)

    def stack(self):
        stack=MagicMock();stack.config={'schema':2,'instance':'0123456789ab'}
        stack.status.return_value={'infrastructure_ready':True}
        return stack

    def test_client_timeout_removes_exact_one_shot_container_and_stops_relay(self):
        stack=self.stack()
        def command(*args,**kwargs):
            if args[0]=='run':raise RuntimeFault('STACK_COMMAND_UNCERTAIN_CHECK_STATUS')
            return b''
        stack.compose.side_effect=command
        with patch('video_factory.stack_worker.Stack',return_value=stack),patch('video_factory.stack_worker.run',side_effect=[b'fixture-container-id\n',b'']) as docker,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_stack_worker(self.args()),2)
        launched=[call.args for call in stack.compose.call_args_list if call.args[0]=='run'][0]
        name=launched[launched.index('--name')+1]
        self.assertTrue(name.startswith('vf-worker-0123456789ab-'))
        self.assertEqual(docker.call_args_list[0].args[0],['docker','container','ls','--all','--filter','name=^/'+name+'$','--format','{{.ID}}'])
        self.assertEqual(docker.call_args_list[1].args[0],['docker','container','rm','--force',name])
        self.assertEqual(stack.compose.call_args_list[-1].args,('stop','--timeout','5','egress'))

    def test_offline_status_does_not_start_relay_and_container_inputs_cannot_escape(self):
        stack=self.stack();stack.compose.return_value=b'{"state":"ready"}'
        with patch('video_factory.stack_worker.Stack',return_value=stack),patch('video_factory.stack_worker.run',return_value=b'') as docker,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_stack_worker(self.args('status')),0)
            self.assertEqual([c.args[0] for c in stack.compose.call_args_list],['run'])
            stack.compose.reset_mock()
            bad=self.args();bad.token_file=Path('/work/../run/secrets/runtime_master')
            self.assertEqual(run_stack_worker(bad),2)
            stack.compose.assert_not_called()
