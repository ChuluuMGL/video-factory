"""Admin-opened first-binding window; OAuth tokens live only in memory."""
import json
import os
from pathlib import Path
import secrets
import threading

from .feishu_oauth import DeviceOAuth, SCOPES, CREATE_SCOPES
from .onboarding import SetupError
from .postgres_store import selected_store
from .review_http import ReviewServer, ReviewHandler
from .review_cli import validate, run_window, app_secret, emit_json
from .runtime_cli import secret_input
from .runtime_store import RuntimeFault
from .setup_feishu import ConnectionSession, SetupFeishu, describe, fingerprint
from .feishu_provision import is_create


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
