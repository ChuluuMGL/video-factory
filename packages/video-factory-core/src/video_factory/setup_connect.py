"""Admin-owned Feishu binding; OAuth tokens live only in memory."""
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time

from .feishu_oauth import DeviceOAuth, SCOPES, CREATE_SCOPES
from .onboarding import SetupError
from .postgres_store import selected_store
from .review_http import ReviewServer, ReviewHandler
from .review_cli import validate, run_window, app_secret, emit_json
from .runtime_cli import secret_input
from .runtime_store import RuntimeFault
from .setup_feishu import ConnectionSession, SetupFeishu, describe, fingerprint
from .feishu_provision import is_create


def terminal_binding(service, oauth, *, read=input, write=print, clock=time.monotonic, sleep=time.sleep, seconds=240):
    """Review a Feishu device grant and its exact Base plan on a private TTY."""
    draft = service.context()
    grant = oauth.start()
    write('请由本人在飞书完成授权：' + grant['verification_uri'])
    write('飞书授权码：' + grant['user_code'])
    deadline = clock() + min(seconds, grant['expires_in'])
    interval = grant['interval']
    user = None
    while clock() < deadline:
        sleep(min(interval, max(0, deadline-clock())))
        response = oauth.poll(grant['device_code'])
        if response['status'] == 'authorized':
            user = response['access_token']
            break
        if response['status'] == 'slow_down': interval = min(interval+5, 60)
    if user is None:
        raise RuntimeFault('FEISHU_OAUTH_DENIED_OR_EXPIRED')
    identity = service.identity(user)
    prepared = service.service.prepare(service.admin, draft, user)
    write('已验证飞书本人身份：' + json.dumps(identity, ensure_ascii=False, sort_keys=True))
    write('请核对即将连接或创建的 Base：' + json.dumps(prepared['plan'], ensure_ascii=False, sort_keys=True))
    binding = prepared['plan'].get('binding')
    if binding and identity['open_id'] not in (set(binding['submitters']) | set(binding['reviewers'])):
        write('注意：当前飞书账号不在本项目导入或审核名单中。若预计由本人操作，请先取消并使用此应用返回的 open_id 修正连接草稿。')
    write('计划校验值：' + prepared['plan_sha256'])
    if read('确认上述飞书操作？输入 yes 后执行，其余输入取消> ').strip() != 'yes':
        return {'status': 'connection_not_confirmed', 'business_ready': False, 'feishu_writes': 0}
    result = service.service.apply(service.admin, draft, user, prepared['plan_sha256'])
    status = service.service.status(service.admin, draft)
    if not status['binding_matches_draft'] or status['requires_reconfirmation']:
        raise RuntimeFault('SETUP_FEISHU_READBACK_INCOMPLETE')
    write('飞书绑定已保存并回读。')
    return result


def run_terminal(args):
    """Use the worker's attached TTY; no Video Factory web listener is opened."""
    try:
        if (not args.project or not args.app_id or not args.token_file or not args.session
                or not (args.app_secret_file or args.app_secret_ref) or args.answers is not None
                or args.expect_plan or args.user_token_file or args.setup_session or args.json
                or args.interactive or args.container_network or not 1 <= args.seconds <= 360):
            raise RuntimeFault('SETUP_FEISHU_TERMINAL_ARGUMENTS_INVALID')
        if not all(stream.isatty() for stream in (sys.stdin, sys.stdout, sys.stderr)):
            raise RuntimeFault('SETUP_RUN_REQUIRES_PRIVATE_TTY')
        if args.stack_root:
            from .stack import Stack
            from .stack_worker import execute_interactive
            if not args.app_secret_ref or args.app_secret_file:
                raise RuntimeFault('FEISHU_APP_SECRET_REFERENCE_INVALID')
            stack = Stack(args.stack_root)
            if stack.config['schema'] != 2: raise RuntimeFault('STACK_FEISHU_REQUIRES_UPGRADE')
            for path in (args.session, args.token_file):
                if not path.is_absolute() or not path.is_relative_to('/work') or '..' in path.parts:
                    raise RuntimeFault('FEISHU_INPUTS_REQUIRE_CONTAINER_WORK_DIRECTORY')
            command = ['setup-feishu', 'terminal', '--root', '/state', '--project', args.project,
                       '--session', str(args.session), '--token-file', str(args.token_file),
                       '--app-id', args.app_id, '--app-secret-ref', args.app_secret_ref,
                       '--master-key-file', '/run/secrets/runtime_master', '--seconds', str(args.seconds)]
            return execute_interactive(stack, command)
        if not args.root or os.environ.get('VF_CONTAINER_MODE') != '1':
            raise RuntimeFault('CONTAINER_MODE_REQUIRED')
        from .review_cli import app_secret
        service = ConnectionService(SetupFeishu(selected_store()(args.root)),
                                    secret_input(args.token_file, ''), ConnectionSession(args.session), args.project)
        draft = service.context()
        oauth = DeviceOAuth(args.app_id, app_secret(args), scopes=CREATE_SCOPES if is_create(draft) else SCOPES)
        result = terminal_binding(service, oauth, seconds=min(args.seconds, 240))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result.get('status') == 'connection_binding_saved' else 2
    except (RuntimeFault, SetupError) as error:
        print(json.dumps({'error': str(error), 'business_ready': False, 'feishu_writes': 'unknown_read_status'}))
        return 2
    except (KeyboardInterrupt, EOFError):
        print(json.dumps({'status': 'interrupted', 'business_ready': False, 'feishu_writes': 'unknown_read_status'}))
        return 130
    except Exception:
        print(json.dumps({'error': 'SETUP_FEISHU_TERMINAL_UNCERTAIN_READ_STATUS', 'business_ready': False,
                          'feishu_writes': 'unknown_read_status'}))
        return 2


class ConnectionService:
    def __init__(self, service, admin, session, project):
        self.service, self.admin, self.session, self.project = service, admin, session, project
        self.digest = fingerprint(session.snapshot())
        self.context()  # authenticate before opening a listener or using OAuth

    def context(self):
        draft = self.session.snapshot()
        if fingerprint(draft) != self.digest:
            raise RuntimeFault('SETUP_FEISHU_DRAFT_CHANGED_REOPEN_WINDOW')
        if describe(draft)['status'] != 'connection_draft_ready':
            raise RuntimeFault('SETUP_FEISHU_QUESTIONS_INCOMPLETE')
        with self.service.store.connect() as db:
            context = self.service.context(db, self.admin, draft)
        if context['project'] != self.project:
            raise RuntimeFault('SETUP_FEISHU_PROJECT_MISMATCH')
        return draft

    def identity(self, user):
        draft = self.context()
        factory = self.service.provision_client_factory if is_create(draft) else self.service.client_factory
        identity = factory(user).identity()
        if identity['tenant_key'] != draft['setup']['configuration']['deployment']['feishu_tenant']:
            raise RuntimeFault('FEISHU_TENANT_OR_ROLE_DENIED')
        return identity


class ConnectionHandler(ReviewHandler):
    assets = Path(__file__).with_name('setup_assets')
    scope = 'administrator_setup_window'
    cookie_name = 'vf_setup'

    def do_POST(self):
        super().do_POST()
        if self.server.saved: self.server.completed.set()

    def do_GET(self):
        if self.path not in ('/', '/healthz', '/api/session', '/app.js', '/style.css'):
            self.failure(RuntimeFault('ROUTE_NOT_FOUND'))
            return
        super().do_GET()


class ConnectionServer(ReviewServer):
    def __init__(self, address, service, oauth, **kwargs):
        self.saved = None
        self.completed = threading.Event()
        self.unlock = secrets.token_urlsafe(32)
        super().__init__(address, service, oauth, handler=ConnectionHandler, **kwargs)

    def operation(self, session, action, body):
        if action == '/api/unlock':
            capability = body.get('capability')
            if set(body) != {'capability'} or not isinstance(capability, str):
                raise RuntimeFault('AUTH_SETUP_UNLOCK_REQUIRED')
            with self.session_lock:
                if not self.unlock or not secrets.compare_digest(capability, self.unlock):
                    raise RuntimeFault('AUTH_SETUP_UNLOCK_REQUIRED')
                self.unlock = None
                session['unlocked'] = True
            return {'status': 'unlocked'}
        if not session.get('unlocked'):
            raise RuntimeFault('AUTH_SETUP_UNLOCK_REQUIRED')
        try:
            draft = self.service.context()
            if action == '/api/mode':
                if body: raise RuntimeFault('FIELDS_INVALID')
                return {'create': is_create(draft), 'scopes': list(CREATE_SCOPES if is_create(draft) else SCOPES)}
            if action in ('/api/login', '/api/poll', '/api/logout'):
                result = super().operation(session, action, body)
                if action == '/api/logout': session['unlocked'] = False
                return result
            user = self.authorize(session)
            if action == '/api/cancel':
                if body: raise RuntimeFault('FIELDS_INVALID')
                session['pending'] = None
                return {'status': 'cancelled'}
            if action == '/api/prepare':
                if body: raise RuntimeFault('FIELDS_INVALID')
                session['pending'] = None
                prepared = self.service.service.prepare(self.service.admin, draft, user)
                session['pending'] = prepared['plan_sha256']
                return prepared
            if action == '/api/commit':
                if (set(body) != {'plan_sha256'} or not session['pending']
                        or body['plan_sha256'] != session['pending']):
                    raise RuntimeFault('SETUP_FEISHU_REVIEWED_PLAN_REQUIRED')
                result = self.service.service.apply(self.service.admin, draft, user, session['pending'])
                session.update(token=None, device=None, pending=None, unlocked=False)
                self.saved = result
                return result
            raise RuntimeFault('ROUTE_NOT_FOUND')
        except Exception as error:
            session['pending'] = None
            if str(error).startswith('AUTH_') or 'DRAFT_CHANGED' in str(error):
                session.update(token=None, device=None, unlocked=False)
            raise


def run_connect(args, *, emit=emit_json):
    server = None
    try:
        if (args.answers is not None or args.expect_revision is not None or args.setup_session or args.interactive
                or args.user_token_file or args.expect_plan or not args.project or not args.app_id
                or not (args.app_secret_file or getattr(args, 'app_secret_ref', None)) or not args.token_file or not (args.root or args.stack_root)):
            raise RuntimeFault('SETUP_FEISHU_CONNECT_ARGUMENTS_INVALID')
        validate(args)
        if args.stack_root:
            if args.container_network: raise RuntimeFault('CONTAINER_MODE_REQUIRED')
            return run_window(args, setup=True, emit=emit)
        if args.container_network and os.environ.get('VF_CONTAINER_MODE') != '1':
            raise RuntimeFault('CONTAINER_MODE_REQUIRED')
        service = ConnectionService(SetupFeishu(selected_store()(args.root)), secret_input(args.token_file, ''),
                                    ConnectionSession(args.session), args.project)
        oauth = DeviceOAuth(args.app_id, app_secret(args), scopes=CREATE_SCOPES if is_create(service.context()) else SCOPES)
        server = ConnectionServer(('0.0.0.0' if args.container_network else '127.0.0.1', args.port), service, oauth,
                                  seconds=args.seconds, container_network=args.container_network)
        server.timeout = 1
        emit({'status': 'ready', 'url': server.origin+'/#'+server.unlock, 'project': service.project,
                          'expires_in': args.seconds, 'access': 'private_one_use_link_loopback_or_same_port_ssh_tunnel',
                          'model_calls': 0, 'feishu_writes': 0})
        while server.clock() < server.deadline and not server.completed.is_set(): server.handle_request()
        if server.completed.is_set(): emit(server.saved)
        return 0
    except KeyboardInterrupt: return 130
    except (SetupError, RuntimeFault) as error:
        emit({'error': str(error), 'business_ready': False, 'model_calls': 0}); return 2
    except Exception:
        emit({'error': 'SETUP_CONNECTION_WINDOW_FAILED', 'business_ready': False, 'model_calls': 0}); return 2
    finally:
        if server:
            server.unlock = None
            server.sessions.clear()
            server.server_close()
