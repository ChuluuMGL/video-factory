import contextlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from setup_connect_fixture import SetupFixture
import test_review_ui
from video_factory.setup_connect import ConnectionService, run_connect
from video_factory.runtime_store import RuntimeFault


class SetupConnectTests(unittest.TestCase):
    request = test_review_ui.ReviewTests.request

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.now = 100
        self.f = SetupFixture(self.temp.name, clock=lambda: self.now)
        self.cookie = ''; self.csrf = ''
        _, value, headers = self.request('/api/session')
        self.csrf = value['csrf']; self.cookie = headers['Set-Cookie'].split(';')[0]
        self.capability = self.f.server.unlock

    def tearDown(self): self.f.close(); self.temp.cleanup()

    def unlock(self):
        self.assertEqual(self.request('/api/unlock', {'capability': self.capability})[0], 200)

    def login(self):
        self.unlock(); self.assertEqual(self.request('/api/login', {})[0], 200)
        self.f.wire.granted = True; self.now += 5
        status, value, _ = self.request('/api/poll', {})
        self.assertEqual(status, 200, value)
        self.assertEqual(value['status'], 'authorized')

    def test_first_binding_login_prepare_confirm_and_discard_credentials(self):
        self.assertFalse(self.f.service.status(self.f.admin, self.f.session.snapshot())['binding_matches_draft'])
        self.login()
        status, plan, _ = self.request('/api/prepare', {})
        self.assertEqual(status, 200, plan)
        self.assertFalse(self.f.service.status(self.f.admin, self.f.session.snapshot())['binding_matches_draft'])
        self.assertNotIn('synthetic-user-token', json.dumps(plan))
        self.assertEqual(plan['plan']['binding']['video_reviewers'], ['ou_video'])
        status, receipt, _ = self.request('/api/commit', {'plan_sha256': plan['plan_sha256']})
        self.assertEqual(status, 200, receipt)
        self.assertEqual(receipt['status'], 'connection_binding_saved')
        self.assertEqual(self.f.server.saved['project'], 'setup_brand')
        self.assertTrue(self.f.service.status(self.f.admin, self.f.session.snapshot())['binding_matches_draft'])
        self.assertNotIn('synthetic-user-token', repr(self.f.server.sessions))
        self.assertEqual(self.request('/api/prepare', {})[0], 401)

    def test_private_one_use_link_and_no_employee_or_admin_routes(self):
        self.assertEqual(self.request('/api/login', {})[0], 401)
        self.assertEqual(self.request('/api/unlock', {'capability': 'incorrect'})[0], 401)
        self.assertEqual(self.f.wire.starts, 0)
        for route in ('/healthz', '/api/session', '/'):
            self.assertNotIn(self.capability, str(self.request(route)[1]))
        self.unlock()
        self.assertEqual(self.request('/api/unlock', {'capability': self.capability})[0], 401)
        self.assertNotEqual(self.request('/api/tasks')[0], 200)
        self.assertNotEqual(self.request('/v1/login', {})[0], 200)
        self.assertEqual(self.request('/api/login', {}, {'Origin': 'https://evil.invalid'})[0], 409)
        self.assertEqual(self.request('/api/login', {}, {'X-VF-CSRF': 'wrong'})[0], 409)
        self.assertEqual(self.request('/api/session', extra={'Host': 'evil.invalid'})[0], 409)
        self.assertEqual(self.f.wire.starts, 0)

    def test_wrong_admin_and_project_rejected_before_oauth(self):
        for token, project, code in [('wrong', 'setup_brand', 'AUTH'), (self.f.admin, 'other', 'PROJECT_MISMATCH')]:
            with self.assertRaisesRegex(RuntimeFault, code):
                ConnectionService(self.f.service, token, self.f.session, project)
        self.assertEqual(self.f.wire.starts, 0)

    def test_wrong_tenant_does_not_authorize(self):
        self.unlock(); self.request('/api/login', {})
        self.f.wire.identity['tenant_key'] = 'another'; self.f.wire.granted = True; self.now += 5
        self.assertEqual(self.request('/api/poll', {})[0], 409)
        self.assertEqual(self.request('/api/prepare', {})[0], 401)

    def test_admin_revocation_after_preview_blocks_save(self):
        self.login(); _, plan, _ = self.request('/api/prepare', {})
        with self.f.store.connect() as db: db.execute('DELETE FROM sessions')
        self.assertEqual(self.request('/api/commit', {'plan_sha256': plan['plan_sha256']})[0], 401)
        self.assertNotIn('synthetic-user-token', repr(self.f.server.sessions))

    def test_changed_draft_or_remote_schema_requires_new_plan(self):
        self.login(); _, plan, _ = self.request('/api/prepare', {})
        factory = self.f.service.client_factory
        original = factory.fields
        def renamed(client, *args):
            fields = original(client, *args)
            fields[0]['field_name'] += ' changed'
            return fields
        with patch.object(factory, 'fields', renamed):
            self.assertEqual(self.request('/api/commit', {'plan_sha256': plan['plan_sha256']})[0], 409)
        self.assertFalse(self.f.service.status(self.f.admin, self.f.session.snapshot())['binding_matches_draft'])
        self.f.session.answer({'submitters': ['ou_other']}, 1)
        self.assertEqual(self.request('/api/prepare', {})[1]['error'], 'SETUP_FEISHU_DRAFT_CHANGED_REOPEN_WINDOW')

    def test_missing_confirmation_logout_expiry_and_poll_bound(self):
        self.unlock(); self.request('/api/login', {})
        for _ in range(3): self.request('/api/poll', {})
        self.assertEqual(self.f.wire.polls, 0)
        self.f.wire.granted = True; self.now += 5; self.request('/api/poll', {})
        self.assertEqual(self.request('/api/commit', {'plan_sha256': 'unreviewed'})[0], 409)
        _, plan, _ = self.request('/api/prepare', {})
        self.assertEqual(self.request('/api/cancel', {})[0], 200)
        self.assertEqual(self.request('/api/commit', {'plan_sha256': plan['plan_sha256']})[0], 409)
        self.request('/api/logout', {})
        self.assertNotIn('synthetic-user-token', repr(self.f.server.sessions))
        self.assertEqual(self.request('/api/login', {})[0], 401)
        self.now += 360
        self.assertEqual(self.request('/api/session')[1]['error'], 'REVIEW_WINDOW_EXPIRED')

    def test_cli_rejects_manual_token_mixed_mode_and_escaped_work_inputs(self):
        args = SimpleNamespace(answers=None, expect_revision=None, setup_session=None, interactive=False,
            user_token_file=None, expect_plan=None, project='setup_brand', app_id='cli_fixture',
            app_secret_file=Path('/work/secret'), token_file=Path('/work/admin'), root=None, stack_root=Path('/stack'),
            session=Path('/work/../secret'), seconds=360, port=8791, container_network=False)
        with patch('video_factory.review_cli.Stack') as stack, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_connect(args), 2); stack.assert_not_called()
            args.user_token_file = Path('/work/user')
            self.assertEqual(run_connect(args), 2); stack.assert_not_called()
