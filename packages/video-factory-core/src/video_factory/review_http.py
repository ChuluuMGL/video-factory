"""Short-lived employee browser session over loopback / an SSH tunnel.

No admin routes, model calls, CORS, persistent tokens or arbitrary media paths.
"""
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import math
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import urlsplit, parse_qs

from .onboarding import read_json
from .runtime_store import RuntimeFault, canonical

ASSETS = Path(__file__).with_name('review_assets')


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, address, service, oauth, *, seconds=360, container_network=False, clock=time.monotonic, handler=None):
        if address[0] != '127.0.0.1' and not (address[0] == '0.0.0.0' and container_network):
            raise RuntimeFault('LOOPBACK_BIND_REQUIRED')
        if type(seconds) is not int or not 1 <= seconds <= 360:
            raise RuntimeFault('REVIEW_SESSION_MAX_360_SECONDS')
        self.service, self.oauth, self.clock = service, oauth, clock
        self.deadline = clock() + seconds
        self.sessions, self.session_lock = {}, threading.Lock()
        self.capacity = threading.BoundedSemaphore(32)
        self.cookie_seconds = 360
        self.persistent = False
        self.login_count = 0
        super().__init__(address, handler or ReviewHandler)
        self.origin = 'http://127.0.0.1:' + str(self.server_port)

    def process_request(self, request, client_address):
        if not self.capacity.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try: super().process_request(request, client_address)
        except BaseException:
            self.capacity.release()
            raise

    def process_request_thread(self, request, client_address):
        try: super().process_request_thread(request, client_address)
        finally: self.capacity.release()

    def session(self, sid, create=False):
        with self.session_lock:
            now = self.clock()
            if now >= self.deadline:
                self.sessions.clear()
                raise RuntimeFault('REVIEW_WINDOW_EXPIRED')
            if sid not in self.sessions:
                if not create: raise RuntimeFault('AUTH_REQUIRED')
                if len(self.sessions) >= 32: raise RuntimeFault('REVIEW_SESSION_CAPACITY')
                sid = secrets.token_urlsafe(32)
                self.sessions[sid] = {'csrf': secrets.token_urlsafe(32), 'lock': threading.Lock(),
                                      'token': None, 'device': None, 'pending': None, 'attempts': 0}
            return sid, self.sessions[sid]

    def authorize(self, session):
        if self.clock() >= self.deadline:
            raise RuntimeFault('REVIEW_WINDOW_EXPIRED')
        if not session['token'] or self.clock() >= session.get('expires', 0):
            session['token'], session['pending'] = None, None
            raise RuntimeFault('AUTH_REQUIRED')
        return session['token']

    def operation(self, session, action, body):
        now = self.clock()
        if action == '/api/logout':
            if body: raise RuntimeFault('FIELDS_INVALID')
            session.update(token=None, device=None, pending=None)
            return {'status': 'signed_out'}
        if action == '/api/login':
            if body: raise RuntimeFault('FIELDS_INVALID')
            with self.session_lock:
                if session['attempts'] >= 3 or self.login_count >= 64:
                    raise RuntimeFault('REVIEW_LOGIN_LIMIT')
                session['attempts'] += 1; self.login_count += 1
            session.update(token=None, device=None, pending=None)
            device = self.oauth.start()
            device.update(deadline=min(self.deadline, now+device['expires_in']), next_poll=now+device['interval'])
            session['device'] = device
            return {'status': 'authorization_pending', 'verification_uri': device['verification_uri'],
                    'user_code': device['user_code'], 'retry_after': device['interval']}
        if action == '/api/poll':
            if body: raise RuntimeFault('FIELDS_INVALID')
            device = session['device']
            if not device or now >= device['deadline']:
                session['device'] = None
                raise RuntimeFault('FEISHU_OAUTH_DENIED_OR_EXPIRED')
            if now < device['next_poll']:
                return {'status': 'authorization_pending', 'retry_after': max(1, math.ceil(device['next_poll']-now))}
            try:
                result = self.oauth.poll(device['device_code'])
                if result['status'] in ('authorization_pending', 'slow_down'):
                    if result['status'] == 'slow_down': device['interval'] = min(60, device['interval']+5)
                    device['next_poll'] = self.clock()+device['interval']
                    return {'status': 'authorization_pending', 'retry_after': device['interval']}
                identity = self.service.identity(result['access_token'])
                if self.clock() >= device['deadline']:
                    raise RuntimeFault('REVIEW_WINDOW_EXPIRED')
                session.update(token=result['access_token'], identity=identity, device=None,
                               expires=min(self.deadline, self.clock()+result['expires_in']-15))
                return {'status': 'authorized', 'identity': identity}
            except Exception:
                session.update(token=None, device=None, pending=None)
                raise
        user = self.authorize(session)
        service, bridge, project = self.service, self.service.bridge, self.service.project
        if action == '/api/video/prepare':
            if set(body) != {'task','revision'}: raise RuntimeFault('FIELDS_INVALID')
            from .video_jobs import prepare
            _,_,prepared=prepare(service,user,**body)
            plan=prepared['plan'];session['pending']={'kind':'video_job','hash':prepared['request_plan_sha256'],'args':body.copy()}
            detail=service.task(user,**body)
            return {'kind':'video_job','plan_sha256':prepared['request_plan_sha256'],'task':plan['task'],'revision':plan['revision'],
                    'script':detail['input']['script'],'source_record':'SKU '+detail['input']['sku_id'],'model':plan['model'],
                    'billing_owner':plan['billing_owner'],'duration':plan['specification']['duration'],'max_submissions':1}
        if action == '/api/script/prepare':
            if set(body) != {'task','sku_id','brief','expected_revision'}: raise RuntimeFault('FIELDS_INVALID')
            from .script_jobs import ScriptJobs
            prepared = ScriptJobs(service.store, client_factory=bridge.client_factory).prepare(user, project, **body)
            plan = prepared['plan']
            session['pending'] = {'kind':'script_job','hash':prepared['plan_sha256'],'args':body.copy()}
            return {'kind':'script_job','plan_sha256':prepared['plan_sha256'],'task':plan['task'],'revision':plan['revision'],
                    'script':plan['brief'],'source_record':'SKU '+plan['sku']['sku_id'],'model':plan['profile']['model'],
                    'billing_owner':plan['profile']['billing_owner'],'max_submissions':1}
        if action == '/api/task':
            if set(body) != {'task', 'revision'}: raise RuntimeFault('FIELDS_INVALID')
            return service.task(user, **body)
        if action == '/api/import/prepare':
            if set(body) != {'record', 'expected_revision'}: raise RuntimeFault('FIELDS_INVALID')
            prepared = bridge.prepare_import(user, project, **body)
            kind = 'import'
        elif action == '/api/review/prepare':
            if set(body) != {'task', 'revision', 'stage', 'decision', 'feedback'}: raise RuntimeFault('FIELDS_INVALID')
            prepared = bridge.prepare_review(user, project, **body)
            kind = 'review'
        elif action == '/api/commit':
            pending = session['pending']
            if set(body) != {'plan_sha256'} or not pending or body['plan_sha256'] != pending['hash']:
                raise RuntimeFault('REVIEW_PLAN_REQUIRED')
            if pending['kind'] == 'video_job':
                from .video_jobs import prepare
                worker,values,_=prepare(service,user,**pending['args'])
                worker.approve('member-verified',pending['hash'],**values)
                result={'project':project,**pending['args'],'state':'ready','video_submission_approved':True}
            elif pending['kind'] == 'script_job':
                from .script_jobs import ScriptJobs
                result = ScriptJobs(service.store, client_factory=bridge.client_factory).submit(user, pending['hash'], project=project, **pending['args'])
            elif pending['kind'] == 'import':
                result = bridge.import_task(user, project, expected_plan=pending['hash'], **pending['args'])
            else:
                result = bridge.review(user, project, event=pending['event'], expected_plan=pending['hash'], **pending['args'])
            return {'receipt': result, 'model_calls': 0, 'feishu_writes': 0}
        else:
            raise RuntimeFault('ROUTE_NOT_FOUND')
        plan = prepared['plan']
        session['pending'] = {'kind': kind, 'hash': prepared['plan_sha256'], 'args': body.copy(), 'event': 'web_'+secrets.token_hex(16)}
        return {'kind': kind, 'plan_sha256': prepared['plan_sha256'], 'script': plan['snapshot']['input']['script'],
                'task': plan['snapshot']['input']['task'], 'source_record': plan['snapshot']['record_id'],
                'revision': plan.get('revision', plan.get('expected_revision', 0)+1),
                'stage': plan.get('stage'), 'decision': plan.get('decision'), 'feedback': plan.get('feedback', ''),
                'artifact_sha256': (plan.get('artifact') or {}).get('sha256'), 'model_calls': 0}


class ReviewHandler(BaseHTTPRequestHandler):
    server_version = 'VideoFactoryReview'
    assets = ASSETS
    scope = 'employee_review_window'
    cookie_name = 'vf_review'

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *args): pass

    def headers_out(self, status, mime, length, extra=()):
        self.send_response(status)
        for name, value in [('Content-Type', mime), ('Content-Length', str(length)), ('Cache-Control', 'no-store'),
                            ('X-Content-Type-Options', 'nosniff'), ('Referrer-Policy', 'no-referrer'),
                            ('X-Frame-Options', 'DENY'), ('Cross-Origin-Resource-Policy', 'same-origin'),
                            ('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; media-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"), *extra]:
            self.send_header(name, value)
        self.end_headers()

    def reply(self, status, value, extra=()):
        data = canonical(value).encode()
        self.headers_out(status, 'application/json; charset=utf-8', len(data), extra)
        self.wfile.write(data)

    def gate(self):
        if self.headers.get_all('Host', []) != [urlsplit(self.server.origin).netloc]:
            raise RuntimeFault('REVIEW_HOST_DENIED')
        if len(self.path) > 2048: raise RuntimeFault('REQUEST_INVALID')
        cookie = self.headers.get('Cookie', '')
        if len(cookie) > 2048: raise RuntimeFault('AUTH_REQUIRED')
        parsed = SimpleCookie(); parsed.load(cookie)
        return parsed[self.cookie_name].value if self.cookie_name in parsed else None

    def failure(self, error):
        if isinstance(error, RuntimeFault):
            code = str(error)
            self.reply(401 if code.startswith('AUTH_') else 409, {'error': code})
        else:
            self.reply(400, {'error': 'REVIEW_REQUEST_FAILED'})

    def do_GET(self):
        try:
            sid = self.gate()
            path = urlsplit(self.path)
            if self.path == '/healthz':
                if self.server.clock() >= self.server.deadline: raise RuntimeFault('REVIEW_WINDOW_EXPIRED')
                self.reply(200, {'scope': self.scope, 'project': self.server.service.project})
            elif path.path in ('/', '/api/session'):
                if path.query: raise RuntimeFault('REQUEST_INVALID')
                sid, session = self.server.session(sid, create=True)
                cookie = [('Set-Cookie', self.cookie_name+'='+sid+'; Path=/; HttpOnly; SameSite=Strict; Max-Age='+str(self.server.cookie_seconds)+('; Secure' if self.server.origin.startswith('https://') else ''))]
                if path.path == '/':
                    data = (self.assets/'index.html').read_bytes()
                    self.headers_out(200, 'text/html; charset=utf-8', len(data), cookie); self.wfile.write(data)
                else:
                    with session['lock']:
                        authenticated = bool(session['token']) and self.server.clock() < session.get('expires', 0)
                        self.reply(200, {'csrf': session['csrf'], 'project': self.server.service.project,
                                         'app_id': self.server.oauth.app_id, 'authenticated': authenticated,
                                         'persistent': self.server.persistent,
                                         'remaining_seconds': self.server.cookie_seconds if self.server.persistent else max(0, int(self.server.deadline-self.server.clock()))}, cookie)
            elif self.path in ('/app.js', '/style.css'):
                name, mime = ('app.js', 'text/javascript') if self.path == '/app.js' else ('style.css', 'text/css')
                data = (self.assets/name).read_bytes(); self.headers_out(200, mime, len(data)); self.wfile.write(data)
            else:
                _, session = self.server.session(sid)
                with session['lock']:
                    user = self.server.authorize(session)
                    if path.path == '/api/tasks':
                        query = parse_qs(path.query, keep_blank_values=True)
                        if set(query)-{'after'} or any(len(v)!=1 for v in query.values()): raise RuntimeFault('REQUEST_INVALID')
                        self.reply(200, self.server.service.tasks(user, query.get('after', [''])[0]))
                    else:
                        match = re.fullmatch(r'/media/([A-Za-z0-9_-]{1,96})/([1-9][0-9]{0,8})/([a-f0-9]{64})\.mp4', self.path)
                        if not match: raise RuntimeFault('ROUTE_NOT_FOUND')
                        task, revision, digest = match.groups()
                        stream, size = self.server.service.media(user, task, int(revision), digest)
                        with stream:
                            start, end, status = 0, size-1, 200
                            extra = [('Accept-Ranges', 'bytes')]
                            if self.headers.get('Range'):
                                interval = re.fullmatch(r'bytes=(\d+)-(\d*)', self.headers['Range'])
                                if not interval: raise RuntimeFault('REVIEW_MEDIA_RANGE_INVALID')
                                start = int(interval[1]); end = min(size-1, int(interval[2])) if interval[2] else size-1
                                if not 0 <= start <= end < size: raise RuntimeFault('REVIEW_MEDIA_RANGE_INVALID')
                                status = 206; extra.append(('Content-Range', f'bytes {start}-{end}/{size}'))
                            self.headers_out(status, 'video/mp4', end-start+1, extra)
                            stream.seek(start); remaining = end-start+1
                            while remaining:
                                chunk = stream.read(min(65536, remaining))
                                if not chunk: break
                                self.wfile.write(chunk); remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError): pass
        except Exception as error: self.failure(error)

    def do_POST(self):
        try:
            sid = self.gate()
            if self.headers.get_all('Origin', []) != [self.server.origin]: raise RuntimeFault('REVIEW_ORIGIN_DENIED')
            _, session = self.server.session(sid)
            with session['lock']:
                csrf = self.headers.get_all('X-VF-CSRF', [])
                if len(csrf) != 1 or not secrets.compare_digest(csrf[0], session['csrf']): raise RuntimeFault('REVIEW_CSRF_DENIED')
                if self.headers.get('Transfer-Encoding') or self.headers.get_content_type() != 'application/json':
                    raise RuntimeFault('JSON_CONTENT_REQUIRED')
                lengths = self.headers.get_all('Content-Length', [])
                if len(lengths)!=1 or not lengths[0].isdigit() or not 0 < int(lengths[0]) <= 65536:
                    raise RuntimeFault('BODY_LENGTH_INVALID')
                raw = self.rfile.read(int(lengths[0]))
                if len(raw) != int(lengths[0]): raise RuntimeFault('BODY_INCOMPLETE')
                body = read_json(io.StringIO(raw.decode()))
                if not isinstance(body, dict): raise RuntimeFault('BODY_OBJECT_REQUIRED')
                self.reply(200, self.server.operation(session, self.path, body))
        except (BrokenPipeError, ConnectionResetError): pass
        except Exception as error: self.failure(error)
