import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from video_factory.feishu_session import arguments, run_stack, terminal_operation
from video_factory.runtime_store import RuntimeFault


class OAuth:
    def start(self):
        return {'verification_uri': 'https://accounts.feishu.cn/test',
                'user_code': 'SYNTHETIC', 'device_code': 'synthetic-device',
                'expires_in': 240, 'interval': 1}

    def poll(self, device):
        assert device == 'synthetic-device'
        return {'status': 'authorized', 'access_token': 'synthetic-secret-token'}


class Bridge:
    def __init__(self): self.calls = []

    def prepare_import(self, token, project, record, revision):
        self.calls.append(('prepare-import', token, project, record, revision))
        return {'plan': {'project': project, 'record': record}, 'plan_sha256': 'plan-import'}

    def import_task(self, token, project, record, plan, revision):
        self.calls.append(('import', token, project, record, plan, revision))
        return {'state': 'awaiting_script_review', 'task': 'VF_TEST_1'}

    def prepare_review(self, token, project, task, revision, stage, decision, feedback):
        self.calls.append(('prepare-review', token, project, task, revision, stage, decision, feedback))
        return {'plan': {'project': project, 'task': task}, 'plan_sha256': 'plan-review'}

    def review(self, token, project, task, revision, stage, decision, event, plan, feedback):
        self.calls.append(('review', token, project, task, revision, stage, decision, event, plan, feedback))
        return {'state': 'needs_revision', 'task': task}


def options(**updates):
    value = dict(action='import', project='test_project', record='recSynthetic',
                 expected_revision=0, task=None, revision=1, stage='script',
                 decision='accept', feedback='', event=None)
    value.update(updates)
    return SimpleNamespace(**value)


class SessionTests(unittest.TestCase):
    def test_import_requires_review_and_keeps_user_token_out_of_output(self):
        bridge = Bridge(); output = []
        result = terminal_operation(bridge, OAuth(), options(), read=lambda _: 'yes',
                                    write=output.append, sleep=lambda _: None)
        self.assertEqual(result['state'], 'awaiting_script_review')
        self.assertEqual([row[0] for row in bridge.calls], ['prepare-import', 'import'])
        self.assertNotIn('synthetic-secret-token', json.dumps(output + [result]))

    def test_cancel_never_imports(self):
        bridge = Bridge()
        result = terminal_operation(bridge, OAuth(), options(), read=lambda _: 'no',
                                    write=lambda _: None, sleep=lambda _: None)
        self.assertEqual(result['status'], 'not_confirmed')
        self.assertEqual([row[0] for row in bridge.calls], ['prepare-import'])

    def test_review_preserves_decision_feedback_and_event(self):
        bridge = Bridge()
        args = options(action='review', record=None, task='VF_TEST_1', decision='reject',
                       feedback='请修正规格', event='review_once')
        result = terminal_operation(bridge, OAuth(), args, read=lambda _: 'yes',
                                    write=lambda _: None, sleep=lambda _: None)
        self.assertEqual(result['state'], 'needs_revision')
        self.assertEqual(bridge.calls[-1][-3:], ('review_once', 'plan-review', '请修正规格'))

    def test_arguments_reject_ambiguous_actions(self):
        with self.assertRaisesRegex(RuntimeFault, 'ARGUMENTS'):
            arguments(options(action='review', record=None, task='VF_TEST_1', decision='reject'))
        with self.assertRaisesRegex(RuntimeFault, 'ARGUMENTS'):
            arguments(options(task='VF_TEST_1'))

    def test_stack_uses_private_master_and_never_creates_a_user_token_file(self):
        args = options(stack_root='/synthetic-stack')
        with patch('video_factory.feishu_session.Stack') as stack_type, \
             patch('video_factory.feishu_session.execute_interactive', return_value=0) as execute:
            stack_type.return_value.config = {'schema': 2}
            self.assertEqual(run_stack(args), 0)
        command = execute.call_args.args[1]
        self.assertEqual(command[:2], ['feishu-session', 'import'])
        self.assertIn('/run/secrets/runtime_master', command)
        self.assertNotIn('--user-token-file', command)


if __name__ == '__main__': unittest.main()
