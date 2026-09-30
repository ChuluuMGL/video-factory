"""Synthetic wire fixtures, shared by cloud unit and browser checks only."""
import base64
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlsplit

from video_factory.feishu_bridge import FeishuBridge
from video_factory.feishu_client import FeishuClient
from video_factory.feishu_oauth import DeviceOAuth, SCOPES, CREATE_SCOPES
from video_factory.review_http import ReviewServer
from video_factory.review_service import ReviewService
from video_factory.runtime_store import RuntimeStore

BINDING = {'tenant_key': 'fixture_tenant', 'base_token': 'bascnFixture', 'table_id': 'tblFixture',
           'fields': {'task': 'fldTask', 'sku_id': 'fldSkuId', 'script': 'fldScript', 'source_revision': 'fldSource'},
           'submitters': ['ou_employee'], 'reviewers': ['ou_employee']}


class Fixture:
    def __init__(self, root, store=None, admin=None, clock=None, project='ui_brand', create=False, user_token='synthetic-user-token'):
        self.root = Path(root)
        if store is None: (self.root/'state').mkdir(mode=0o700)
        self.store = store or RuntimeStore.install(self.root/'state', 'fixture', 'fixture-admin-password')
        self.admin = admin or self.store.login('admin', 'fixture-admin-password')['token']
        self.project = project
        self.user_token = user_token
        self.store.put_project(self.admin, self.project, {'video_route': 'deferred', 'credential_ref': 'secret:fixture', 'billing_owner': 'fixture'})
        self.fields = {'task': 'task_one', 'sku_id': 'sku_one', 'script': 'Original <img src=x onerror=alert(1)> script', 'source_revision': 'source_one'}
        self.identity = {'tenant_key': 'fixture_tenant', 'open_id': 'ou_employee'}
        self.granted = False; self.polls = 0; self.starts = 0; self.reads = 0; self.upstream_error = None
        self.scopes = CREATE_SCOPES if create else SCOPES
        self.created_app = None; self.created_tables = {}; self.created_records = {}; self.writes = []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def reply(self, status, body):
                raw = json.dumps(body).encode(); self.send_response(status)
                self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(raw)))
                self.end_headers(); self.wfile.write(raw)
            def do_POST(self):
                raw = self.rfile.read(int(self.headers['Content-Length'])).decode()
                if self.path.startswith('/open-apis/bitable/'):
                    assert create and self.headers['Authorization'] == 'Bearer '+fixture.user_token
                    assert self.headers.get_content_type() == 'application/json'
                    body = json.loads(raw); path = urlsplit(self.path).path
                    fixture.writes.append(path)
                    if path == '/open-apis/bitable/v1/apps':
                        assert fixture.created_app is None
                        fixture.created_app = {'app_token': 'bascnCreated', **body}
                        data = {'app': fixture.created_app}
                    elif path == '/open-apis/bitable/v1/apps/bascnCreated/tables':
                        tid = 'tblCreated'+str(len(fixture.created_tables))
                        fixture.created_tables[tid] = [{'field_id': 'fldCreated'+str(len(fixture.created_tables))+str(i), **f}
                                                       for i, f in enumerate(body['table']['fields'])]
                        data = {'table_id': tid}
                    else:
                        import uuid
                        assert path.endswith('/records/batch_create')
                        assert uuid.UUID(parse_qs(urlsplit(self.path).query)['client_token'][0]).version == 4
                        tid = path.split('/')[-3]; assert tid in fixture.created_tables
                        records = []
                        for row in body['records']:
                            rid = 'recCreated'+str(len(fixture.created_records))
                            item = {'record_id': rid, **row}
                            fixture.created_records[rid] = item; records.append(item)
                        data = {'records': list(reversed(records))}
                    return self.reply(200, {'code': 0, 'data': data})
                body = parse_qs(raw)
                assert self.headers.get_content_type() == 'application/x-www-form-urlencoded'
                if fixture.upstream_error:
                    return self.reply(400, fixture.upstream_error)
                if self.path == '/oauth/v1/device_authorization':
                    assert self.headers['Authorization'] == 'Basic '+base64.b64encode(b'cli_fixture:synthetic-app-secret').decode()
                    assert body == {'client_id': ['cli_fixture'], 'scope': [' '.join(fixture.scopes)]}
                    fixture.starts += 1
                    return self.reply(200, {'device_code': 'private-device-code', 'user_code': 'ABCD-EFGH', 'verification_uri': 'https://accounts.feishu.cn/oauth/fixture', 'expires_in': 300, 'interval': 5})
                assert self.path == '/open-apis/authen/v2/oauth/token'
                assert body == {'client_id': ['cli_fixture'], 'client_secret': ['synthetic-app-secret'], 'device_code': ['private-device-code'], 'grant_type': ['urn:ietf:params:oauth:grant-type:device_code']}
                fixture.polls += 1
                if not fixture.granted: return self.reply(400, {'error': 'authorization_pending'})
                self.reply(200, {'access_token': fixture.user_token, 'refresh_token': 'must-discard-refresh', 'expires_in': 3600, 'scope': ' '.join(fixture.scopes)})
            def do_GET(self):
                assert self.headers['Authorization'] == 'Bearer '+fixture.user_token
                fixture.reads += 1; path = urlsplit(self.path).path
                if path == '/open-apis/authen/v1/user_info': data = fixture.identity
                elif path == '/open-apis/bitable/v1/apps/bascnCreated':
                    data = {'app': {k: v for k, v in fixture.created_app.items() if k != 'folder_token'}}
                elif '/bascnCreated/tables/' in path:
                    if path.endswith('/fields'):
                        data = {'items': fixture.created_tables[path.split('/')[-2]], 'has_more': False}
                    else:
                        data = {'record': fixture.created_records[path.split('/')[-1]]}
                elif path.endswith('/fields'):
                    data = {'items': [{'field_id': v, 'field_name': k, 'type': 1} for k, v in BINDING['fields'].items()], 'has_more': False}
                else:
                    assert path.endswith('/records/recFixture')
                    data = {'record': {'record_id': 'recFixture', 'fields': fixture.fields}}
                self.reply(200, {'code': 0, 'data': data})

        self.wire = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.wire_thread = threading.Thread(target=self.wire.serve_forever, daemon=True); self.wire_thread.start()
        origin = 'http://127.0.0.1:'+str(self.wire.server_port)
        # Code-owned fixture subclasses; no product environment or CLI override.
        class Client(FeishuClient): pass
        Client.origin = origin
        self.oauth = DeviceOAuth('cli_fixture', 'synthetic-app-secret', scopes=self.scopes)
        self.oauth.accounts_origin = self.oauth.token_origin = origin
        self.bridge = FeishuBridge(self.store, client_factory=Client)
        self.binding = self.bridge.bind(self.admin, self.project, copy.deepcopy(BINDING), self.user_token)['binding_sha256']
        self.media = self.root/'media'; self.media.mkdir(mode=0o700)
        self.service = ReviewService(self.store, self.project, self.media, client_factory=Client)
        self.server = ReviewServer(('127.0.0.1', 0), self.service, self.oauth, **({'clock': clock} if clock else {}))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()

    def import_task(self, previous=0):
        plan = self.bridge.prepare_import(self.user_token, self.project, 'recFixture', previous)
        return self.bridge.import_task(self.user_token, self.project, 'recFixture', plan['plan_sha256'], previous)

    def artifact(self, path, revision):
        digest = hashlib.sha256(path.read_bytes()).hexdigest(); path.chmod(0o600)
        self.store.claim(self.admin, self.project, 'task_one', revision)
        provider = str(73345678901230+revision)
        self.store.attach_provider(self.admin, self.project, 'task_one', revision, provider)
        self.store.record_artifact(self.admin, self.project, 'task_one', revision, provider,
                                  {'sha256': digest, 'location': str(path), 'verification': 'full_decode_passed'})
        return digest

    def close(self):
        for server in (self.server, self.wire): server.shutdown(); server.server_close()
        self.thread.join(3); self.wire_thread.join(3)
