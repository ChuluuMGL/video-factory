"""Human-only Setup input over loopback/SSH; answers live only in wizard memory."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
import json
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlsplit

from .runtime_store import RuntimeFault, canonical

ASSETS = Path(__file__).with_name('wizard_assets')


class InputServer(ThreadingHTTPServer):
    daemon_threads=True
    block_on_close=False
    def __init__(self,*args):
        self.capacity=threading.BoundedSemaphore(16)
        super().__init__(*args)
    def process_request(self,request,address):
        if not self.capacity.acquire(blocking=False):self.shutdown_request(request);return
        try:super().process_request(request,address)
        except BaseException:self.capacity.release();raise
    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.capacity.release()


class BrowserInput:
    def __init__(self, port=8792, seconds=1800):
        if type(port) is not int or not 1024 <= port <= 65535 or not 1 <= seconds <= 3600:
            raise RuntimeFault('SETUP_BROWSER_LIMIT_INVALID')
        self.condition = threading.Condition()
        self.unlock = secrets.token_urlsafe(32)
        self.cookie = self.csrf = None
        self.messages, self.prompt, self.answer = [], None, None
        self.closed = False
        self.completion = None
        self.acknowledged = False
        self.deadline = time.monotonic()+seconds
        self.server = InputServer(('127.0.0.1', port), Handler)
        self.server.daemon_threads = True
        self.server.owner = self
        self.server.handle_error = lambda *_: None
        self.origin = 'http://127.0.0.1:'+str(port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self): return self.origin+'/#'+self.unlock

    def write(self, value):
        with self.condition:
            self.messages.append(str(value))
            self.messages = self.messages[-80:]

    def read(self, label, *, hidden=False):
        with self.condition:
            self.prompt = {'id': secrets.token_hex(16), 'label': label, 'hidden': hidden}
            self.answer = None
            while self.answer is None and not self.closed:
                remaining = self.deadline-time.monotonic()
                if remaining <= 0:
                    self.prompt = None
                    raise EOFError
                self.condition.wait(min(remaining, 1))
            if self.closed: raise EOFError
            value, self.answer, self.prompt = self.answer, None, None
            if value == ':quit': raise EOFError
            return value

    def hidden(self, label): return self.read(label, hidden=True)

    def finish(self, result, seconds=15):
        """Deliver a safe terminal receipt before closing the private listener."""
        errors = {
            'PASSWORD_LENGTH_14_TO_256_REQUIRED': '密码需要 14–256 位。已有部署请使用原管理员密码；请让 Agent 重新打开向导。',
            'SETUP_PASSWORD_CONFIRMATION_MISMATCH': '两次密码输入不一致，尚未安装。请让 Agent 重新打开向导。',
            'AUTH_FAILED': '管理员密码未通过验证。请使用原密码；忘记密码时请让 Agent 协助恢复。',
            'AUTH_RATE_LIMITED': '登录尝试过于频繁，请稍后再试。',
            'DOCKER_ENGINE_UNAVAILABLE_CHECK_SERVICE': '无法连接服务器上的 Docker 服务。已有配置保留；请让 Agent 检查并恢复服务后续接，无需因此重置管理员密码。',
        }
        if result.get('error'):
            message = errors.get(result['error'], '本次操作未完成，已保留配置。请让 Agent 检查服务器状态后继续。')
        elif result.get('status') == 'connection_ready':
            message = '飞书连接已确认。本次向导已结束，生成与审核验收仍需继续。'
        elif result.get('status') == 'employee_window_closed':
            message = '审核窗口已关闭。实际任务结果仍需单独核验。'
        else:
            message = '本次向导已结束，已有配置保留。可让 Agent 从原部署继续。'
        with self.condition:
            self.prompt = self.answer = None
            self.completion = {'message': message, 'failed': bool(result.get('error'))}
            until = min(self.deadline, time.monotonic()+seconds)
            while not self.acknowledged and not self.closed:
                remaining = until-time.monotonic()
                if remaining <= 0: break
                self.condition.wait(remaining)

    def close(self):
        with self.condition:
            self.closed = True
            self.prompt = self.answer = self.cookie = self.csrf = self.unlock = None
            self.messages.clear()
            self.condition.notify_all()
        self.server.shutdown(); self.server.server_close(); self.thread.join(2)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup(); self.connection.settimeout(5)

    def log_message(self, *_): pass

    def reply(self, code, value, mime='application/json; charset=utf-8', cookie=None):
        raw = value if isinstance(value, bytes) else canonical(value).encode()
        self.send_response(code)
        for key, val in {'Content-Type': mime, 'Content-Length': str(len(raw)), 'Cache-Control': 'no-store',
                'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
                'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"}.items():
            self.send_header(key, val)
        if cookie: self.send_header('Set-Cookie', 'vf_setup_input='+cookie+'; HttpOnly; SameSite=Strict; Path=/; Max-Age=3600')
        self.end_headers(); self.wfile.write(raw)

    def gate(self):
        owner = self.server.owner
        if self.headers.get_all('Host', []) != [urlsplit(owner.origin).netloc]: raise RuntimeFault('HOST_DENIED')
        if owner.closed or time.monotonic() >= owner.deadline: raise RuntimeFault('INPUT_WINDOW_EXPIRED')
        return owner

    def auth(self, owner):
        raw = self.headers.get('Cookie', '')
        if len(raw) > 1024: raise RuntimeFault('AUTH_REQUIRED')
        cookies = SimpleCookie(); cookies.load(raw)
        value = cookies.get('vf_setup_input')
        if not owner.cookie or not value or not secrets.compare_digest(value.value, owner.cookie):
            raise RuntimeFault('AUTH_REQUIRED')

    def do_GET(self):
        try:
            owner = self.gate()
            if self.path in ('/', '/app.js', '/style.css'):
                name, mime = {'/': ('index.html','text/html; charset=utf-8'), '/app.js': ('app.js','text/javascript'), '/style.css': ('style.css','text/css')}[self.path]
                self.reply(200, (ASSETS/name).read_bytes(), mime); return
            if self.path != '/api/prompt': raise RuntimeFault('ROUTE_NOT_FOUND')
            with owner.condition:
                self.auth(owner)
                self.reply(200, {'messages': owner.messages, 'prompt': owner.prompt, 'csrf': owner.csrf,
                                 'completion': owner.completion})
        except Exception:
            self.reply(403, {'error': 'SETUP_INPUT_UNAVAILABLE'})

    def do_POST(self):
        try:
            owner = self.gate()
            if self.headers.get_all('Origin', []) != [owner.origin]: raise RuntimeFault('ORIGIN_DENIED')
            lengths = self.headers.get_all('Content-Length', [])
            if (self.headers.get('Transfer-Encoding') or self.headers.get_content_type() != 'application/json'
                    or len(lengths) != 1 or not lengths[0].isdigit() or not 0 < int(lengths[0]) <= 8192):
                raise RuntimeFault('BODY_INVALID')
            raw = self.rfile.read(int(lengths[0]))
            if len(raw) != int(lengths[0]): raise RuntimeFault('BODY_INVALID')
            body = json.loads(raw)
            with owner.condition:
                if self.path == '/api/unlock':
                    if (not isinstance(body, dict) or set(body) != {'key'} or not owner.unlock
                            or not isinstance(body['key'], str) or not secrets.compare_digest(body['key'], owner.unlock)):
                        raise RuntimeFault('AUTH_REQUIRED')
                    owner.unlock = None
                    owner.cookie, owner.csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                    self.reply(200, {'unlocked': True}, cookie=owner.cookie); return
                self.auth(owner)
                if self.headers.get_all('X-VF-CSRF', []) != [owner.csrf]: raise RuntimeFault('CSRF_DENIED')
                if self.path == '/api/ack':
                    if body != {} or owner.completion is None: raise RuntimeFault('RECEIPT_NOT_READY')
                    self.reply(200, {'acknowledged': True})
                    owner.acknowledged = True
                    owner.condition.notify_all()
                    return
                if (self.path != '/api/answer' or not isinstance(body, dict) or set(body) != {'id','answer'}
                        or not owner.prompt or body['id'] != owner.prompt['id'] or owner.answer is not None
                        or not isinstance(body['answer'], str) or len(body['answer']) > 4096):
                    raise RuntimeFault('ANSWER_STALE_OR_INVALID')
                owner.answer = body['answer']
                owner.condition.notify_all()
                self.reply(200, {'accepted': True})
        except Exception:
            # Never include a parsed answer, exception string or request body.
            self.reply(403, {'error': 'SETUP_INPUT_REJECTED'})
