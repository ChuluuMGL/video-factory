import copy
import http.client
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from review_fixture import Fixture, BINDING
from video_factory.feishu_oauth import DeviceOAuth, verification_url, OAuthTunnel
from video_factory.runtime_store import RuntimeFault


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.now = 100
        self.f = Fixture(self.temp.name, clock=lambda: self.now)
        self.cookie = ''; self.csrf = ''
        status, value, headers = self.request('/api/session')
        self.assertEqual(status, 200); self.csrf = value['csrf']
        self.cookie = headers['Set-Cookie'].split(';')[0]

    def tearDown(self): self.f.close(); self.temp.cleanup()

    def request(self, path, body=None, extra=None, raw=None):
        client = http.client.HTTPConnection('127.0.0.1', self.f.server.server_port, timeout=5)
        headers = {'Cookie': self.cookie}
        if body is not None or raw is not None:
            headers.update({'Content-Type': 'application/json', 'Origin': self.f.server.origin, 'X-VF-CSRF': self.csrf})
        headers.update(extra or {})
        client.request('POST' if body is not None or raw is not None else 'GET', path,
                       raw if raw is not None else (json.dumps(body) if body is not None else None), headers)
        response = client.getresponse(); data = response.read(); result = (response.status, json.loads(data) if 'application/json' in response.getheader('Content-Type', '') else data, dict(response.getheaders()))
        client.close(); return result

    def login(self):
        self.assertEqual(self.request('/api/login', {})[0], 200)
        self.f.granted = True; self.now += 5
        status, value, _ = self.request('/api/poll', {})
        self.assertEqual(status, 200); self.assertEqual(value['status'], 'authorized')
        self.assertNotIn('synthetic-user-token', json.dumps(value))

    def prepare(self, decision='accept', feedback='', stage='script', revision=1):
        return self.request('/api/review/prepare', {'task': 'task_one', 'revision': revision, 'stage': stage, 'decision': decision, 'feedback': feedback})

    def commit(self, plan): return self.request('/api/commit', {'plan_sha256': plan['plan_sha256']})

    def test_wire_oauth_poll_throttle_and_private_credentials(self):
        status, start, _ = self.request('/api/login', {})
        self.assertEqual(status, 200); self.assertNotIn('device_code', start)
        for _ in range(3): self.assertEqual(self.request('/api/poll', {})[1]['status'], 'authorization_pending')
        self.assertEqual(self.f.polls, 0)
        self.now += 5; self.request('/api/poll', {}); self.assertEqual(self.f.polls, 1)
        self.now += 5; self.f.granted = True
        status, value, _ = self.request('/api/poll', {})
        self.assertEqual(status, 200)
        self.assertNotIn('token', json.dumps(value)); self.assertNotIn('must-discard-refresh', repr(self.f.server.sessions))
        self.assertEqual(self.request('/api/session')[1]['authenticated'], True)
        self.assertEqual(self.request('/api/logout', {})[0], 200)
        self.assertEqual(self.request('/api/tasks')[0], 401)
        self.assertNotIn('synthetic-user-token', repr(self.f.server.sessions))

    def test_host_origin_csrf_duplicate_json_and_unauthenticated_reads(self):
        self.assertEqual(self.request('/api/tasks')[0], 401)
        for path, body, headers in [('/api/session', None, {'Host': 'evil.example'}),
                                    ('/api/login', {}, {'Origin': 'https://evil.example'}),
                                    ('/api/login', {}, {'X-VF-CSRF': 'wrong'})]:
            self.assertEqual(self.request(path, body, headers)[0], 409)
        self.assertNotEqual(self.request('/api/login', raw='{"a":1,"a":2}')[0], 200)
        self.assertEqual(self.f.starts, 0)
        _, _, headers = self.request('/')
        self.assertIn('HttpOnly', headers['Set-Cookie']); self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        self.assertEqual(headers['Cache-Control'], 'no-store'); self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])

    def test_login_identity_and_membership_are_live(self):
        self.f.identity['tenant_key'] = 'wrong_tenant'
        self.request('/api/login', {}); self.now += 5; self.f.granted = True
        self.assertEqual(self.request('/api/poll', {})[0], 409)
        self.assertEqual(self.request('/api/tasks')[0], 401)
        self.f.identity['tenant_key'] = 'fixture_tenant'; self.login()
        self.f.identity['open_id'] = 'ou_outsider'
        self.assertEqual(self.request('/api/tasks')[0], 409)

    def test_two_stage_confirmation_idempotence_history_and_source_change(self):
        self.login()
        status, plan, _ = self.request('/api/import/prepare', {'record': 'recFixture', 'expected_revision': 0})
        self.assertEqual(status, 200); self.assertEqual(self.request('/api/tasks')[1]['items'], [])
        self.assertEqual(self.commit(plan)[0], 200)
        self.assertTrue(self.commit(plan)[1]['receipt']['replayed'])
        status, review, _ = self.prepare('reject', 'Correct product name')
        self.assertEqual(status, 200); self.assertEqual(self.commit(review)[1]['receipt']['state'], 'rejected')
        self.assertTrue(self.commit(review)[1]['receipt']['replayed'])
        self.f.fields.update(script='Corrected script', source_revision='source_two'); self.f.import_task(1)
        detail = self.request('/api/task', {'task': 'task_one', 'revision': 2})
        self.assertEqual(detail[0], 200); self.assertEqual(detail[1]['history'][0]['feedback'], 'Correct product name')
        status, review, _ = self.prepare(revision=2); self.assertEqual(status, 200)
        self.f.fields['script'] = 'Changed after confirmation'
        self.assertEqual(self.commit(review)[0], 409)
        self.assertEqual(self.request('/api/task', {'task': 'task_one', 'revision': 1})[0], 409)

    def test_role_revoked_after_prepare_blocks_commit(self):
        self.f.import_task(); self.login(); _, plan, _ = self.prepare()
        binding = copy.deepcopy(BINDING); binding['reviewers'] = ['ou_another']
        self.f.bridge.bind(self.f.admin, self.f.project, binding, 'synthetic-user-token', self.f.binding)
        self.assertEqual(self.commit(plan)[0], 409)
        self.assertEqual(self.request('/api/task', {'task': 'task_one', 'revision': 1})[1]['review_stages'], [])

    def test_multibyte_feedback_fits_documented_limit_but_body_is_bounded(self):
        self.f.import_task(); self.login()
        feedback = '请修改'*2000
        status, plan, _ = self.prepare('reject', feedback)
        self.assertEqual(status, 200); self.assertEqual(plan['feedback'], feedback)
        self.assertEqual(self.commit(plan)[1]['receipt']['feedback'], feedback)
        self.assertEqual(self.request('/api/review/prepare', raw=' '*65537)[1]['error'], 'BODY_LENGTH_INVALID')

    def test_video_ranges_integrity_path_and_review(self):
        self.f.import_task(); self.login(); _, plan, _ = self.prepare(); self.commit(plan)
        path = self.f.media/'fixture.mp4'; path.write_bytes(b'synthetic-unit-media')
        digest = self.f.artifact(path, 1)
        status, detail, _ = self.request('/api/task', {'task': 'task_one', 'revision': 1}); self.assertEqual(status, 200)
        route = detail['media_url']
        self.assertEqual(self.request(route, extra={'Range': 'bytes=0-3'})[:2], (206, b'synt'))
        self.assertEqual(self.request(route, extra={'Range': 'bytes=999-'})[0], 409)
        path.write_bytes(b'replaced'); self.assertEqual(self.request(route)[1]['error'], 'REVIEW_MEDIA_HASH_CHANGED')
        path.unlink(); outside = self.f.root/'outside'; outside.write_bytes(b'synthetic-unit-media'); outside.chmod(0o600); path.symlink_to(outside)
        self.assertEqual(self.request(route)[1]['error'], 'REVIEW_MEDIA_PATH_INVALID')
        path.unlink(); path.write_bytes(b'synthetic-unit-media'); path.chmod(0o600)
        _, plan, _ = self.prepare(stage='video')
        self.assertEqual(plan['artifact_sha256'], digest); self.assertEqual(self.commit(plan)[1]['receipt']['state'], 'accepted')

    def test_expiry_no_admin_routes_and_login_limits(self):
        self.login(); self.assertEqual(self.request('/v1/login', {})[0], 409)
        for _ in range(2): self.assertEqual(self.request('/api/login', {})[0], 200)
        self.assertEqual(self.request('/api/login', {})[1]['error'], 'REVIEW_LOGIN_LIMIT')
        self.now += 361
        self.assertEqual(self.request('/api/tasks')[1]['error'], 'REVIEW_WINDOW_EXPIRED')
        self.assertEqual(self.f.server.sessions, {})


class OAuthValidationTests(unittest.TestCase):
    def test_fixed_hosts_and_verification_links(self):
        for url in ('https://accounts.feishu.cn.evil.test/x', 'http://accounts.feishu.cn/x', 'https://user@accounts.feishu.cn/x', 'https://accounts.feishu.cn:8443/x', 'https://accounts.feishu.cn/x#fragment'):
            with self.assertRaises(RuntimeFault): verification_url(url)
        with self.assertRaises(RuntimeFault): OAuthTunnel('accounts.feishu.cn.evil.test')

    def test_errors_scope_and_refresh_are_not_exposed(self):
        client = DeviceOAuth('cli_fixture', 'synthetic-app-secret')
        for value in ({'error': 'access_denied', 'error_description': 'private upstream data'},
                      {'access_token': 'secret', 'expires_in': 3600, 'scope': 'wrong'},
                      {'access_token': 'secret', 'expires_in': True}):
            with patch.object(client, 'post', return_value=value):
                with self.assertRaises(RuntimeFault) as caught: client.poll('device')
                self.assertNotIn('private upstream', str(caught.exception))
        with patch.object(client, 'post', return_value={'error': 'slow_down'}):
            self.assertEqual(client.poll('device'), {'status': 'slow_down'})
