import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from video_factory.review_cli import run_stack, run_review
from video_factory.runtime_store import RuntimeFault


class ReviewCliTests(unittest.TestCase):
    def args(self):
        return SimpleNamespace(stack_root=Path('/customer'), project='brand', app_id='cli_fixture', app_secret_file=Path('/work/secret'), port=8790, seconds=360)

    def test_uncertain_container_launch_cleans_exact_instance_and_relay(self):
        stack=MagicMock();stack.config={'schema':2,'instance':'0123456789ab'};stack.status.return_value={'infrastructure_ready':True}
        def compose(*args,**kwargs):
            if args[0]=='run':raise RuntimeFault('STACK_COMMAND_UNCERTAIN_CHECK_STATUS')
        stack.compose.side_effect=compose
        with patch('video_factory.review_cli.Stack',return_value=stack),patch('video_factory.review_cli.socket.socket'),patch('video_factory.review_cli.docker_run',side_effect=[b'container-id',b'']) as docker,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_stack(self.args()),2)
        launched=[c.args for c in stack.compose.call_args_list if c.args[0]=='run'][0]
        name=launched[launched.index('--name')+1]
        self.assertTrue(name.startswith('vf-review-0123456789ab-'))
        self.assertNotIn('--publish',launched)
        self.assertEqual(docker.call_args_list[-1].args[0],['docker','container','rm','--force',name])
        self.assertEqual(stack.compose.call_args_list[-1].args,('stop','--timeout','5','egress'))

    def test_input_escape_duration_and_public_bind_require_container(self):
        with patch('video_factory.review_cli.Stack') as stack,contextlib.redirect_stdout(io.StringIO()):
            args=self.args();args.app_secret_file=Path('/work/../run/secrets/key');self.assertEqual(run_stack(args),2)
            args=self.args();args.seconds=361;self.assertEqual(run_stack(args),2)
            stack.assert_not_called()
            args=self.args();args.container_network=True
            with patch.dict('os.environ',{'VF_CONTAINER_MODE':'0'}):self.assertEqual(run_review(args),2)

    def test_vault_reference_uses_fixed_container_key_and_never_accepts_host_key(self):
        args=self.args();args.app_secret_file=None;args.app_secret_ref='secret:fixture';args.master_key_file=Path('/host/key')
        with patch('video_factory.review_cli.Stack') as stack,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_stack(args),2);stack.assert_not_called()
        args.master_key_file=None
        stack=MagicMock();stack.config={'schema':2,'instance':'0123456789ab'};stack.status.return_value={'infrastructure_ready':True}
        def compose(*values,**kwargs):
            if values[0]=='run':raise RuntimeFault('STACK_COMMAND_UNCERTAIN_CHECK_STATUS')
        stack.compose.side_effect=compose
        with patch('video_factory.review_cli.Stack',return_value=stack),patch('video_factory.review_cli.socket.socket'),patch('video_factory.review_cli.docker_run',return_value=b''),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_stack(args),2)
        command=[c.args for c in stack.compose.call_args_list if c.args[0]=='run'][0]
        self.assertIn('secret:fixture',command);self.assertIn('/run/secrets/runtime_master',command)
        self.assertNotIn('--app-secret-file',command)
