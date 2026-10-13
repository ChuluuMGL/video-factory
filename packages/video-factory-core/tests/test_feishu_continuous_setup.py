from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from video_factory import feishu_native_cli


class ContinuousSetupTests(unittest.TestCase):
    def args(self):
        return SimpleNamespace(action='enable',stack_root=Path('/synthetic/stack'),
                               session=Path('/synthetic/session'),configure_models=True)

    def test_one_hidden_admin_input_and_one_revoke_after_continuation(self):
        session={'configuration':{'project':{'id':'brand'}}}
        for failed in (False,True):
            with self.subTest(failed=failed), \
                 patch.object(feishu_native_cli,'Stack'), \
                 patch.object(feishu_native_cli,'SessionStore') as sessions, \
                 patch.object(feishu_native_cli,'rpc',side_effect=[{'token':'fixture-token'},
                     {'plan':{'context':{'target':{'base_token':'fixture-base'}}},'plan_sha256':'fixture-plan'},
                     {'status':'enabled'},{}]) as calls, \
                 patch('video_factory.runner.status',return_value={'status':'running'}), \
                 patch.object(feishu_native_cli.getpass,'getpass',return_value='fixture-password') as hidden, \
                 patch.object(feishu_native_cli,'choice',return_value=True), \
                 patch('video_factory.production_setup.welcome',side_effect=RuntimeError('fixture-failure') if failed else None,return_value={'status':'configured'}) as continuation, \
                 patch('sys.stdin.isatty',return_value=True), \
                 patch('sys.stdout.isatty',return_value=True), \
                 patch('sys.stderr.isatty',return_value=True), \
                 patch('builtins.print'):
                sessions.return_value.read.return_value=session
                self.assertEqual(feishu_native_cli.cli(self.args()),2 if failed else 0)
                hidden.assert_called_once()
                self.assertEqual(continuation.call_args.kwargs['admin_token'],'fixture-token')
                self.assertIs(continuation.call_args.kwargs['hidden'],hidden)
                self.assertEqual([c.args[1]['action'] for c in calls.call_args_list],['open','native_review','native_review','close'])

    def test_status_cannot_start_continuous_mutation(self):
        args=self.args();args.action='status'
        with patch.object(feishu_native_cli,'rpc') as calls,patch('builtins.print'):
            self.assertEqual(feishu_native_cli.cli(args),2)
            calls.assert_not_called()

    def test_private_input_fallback_closes_on_continuation_failure(self):
        args=self.args();args.browser_input=True;args.input_port=8792
        with (patch.object(feishu_native_cli,'Stack'),
              patch.object(feishu_native_cli,'SessionStore') as sessions,
              patch.object(feishu_native_cli,'rpc',side_effect=[{'token':'fixture-token'},
                  {'plan':{'context':{'target':{'base_token':'fixture-base'}}},'plan_sha256':'fixture-plan'},
                  {'status':'enabled'},{}]) as calls,
              patch('video_factory.runner.status',return_value={'status':'running'}),
              patch('video_factory.setup_browser.BrowserInput') as inputs,
              patch.object(feishu_native_cli.getpass,'getpass') as terminal,
              patch.object(feishu_native_cli,'choice',return_value=True),
              patch('video_factory.production_setup.welcome',side_effect=EOFError),
              patch('sys.stdin.isatty',return_value=False),patch('builtins.print')):
            sessions.return_value.read.return_value={'configuration':{'project':{'id':'brand'}}}
            inputs.return_value.url='http://127.0.0.1:8792/fixture'
            inputs.return_value.hidden.return_value='fixture-password'
            self.assertEqual(feishu_native_cli.cli(args),130)
            terminal.assert_not_called();inputs.return_value.hidden.assert_called_once()
            inputs.return_value.close.assert_called_once()
            self.assertEqual(calls.call_args_list[-1].args[1],{'action':'close','token':'fixture-token'})
