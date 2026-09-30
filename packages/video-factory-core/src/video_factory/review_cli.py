"""Explicit, short-lived review listener. Foreground ownership and cleanup."""
import json
import os
from pathlib import Path
import secrets
import re
import socket
import time
from urllib.request import build_opener, ProxyHandler

from .feishu_oauth import DeviceOAuth
from .postgres_store import selected_store
from .review_http import ReviewServer
from .review_service import ReviewService
from .review_forward import Forward, container_address
from .runtime_cli import secret_input
from .runtime_store import RuntimeFault, identifier
from .stack import Stack, run as docker_run


def register(commands):
    for container in (False, True):
        p = commands.add_parser('stack-review' if container else 'review-ui', help='limited employee review window; no model execution')
        p.add_argument('--stack-root' if container else '--root', type=Path, required=True)
        p.add_argument('--project', required=True)
        p.add_argument('--app-id', required=True)
        credential = p.add_mutually_exclusive_group(required=True)
        credential.add_argument('--app-secret-file', type=Path)
        credential.add_argument('--app-secret-ref')
        if not container: p.add_argument('--master-key-file', type=Path)
        p.add_argument('--port', type=int, default=8790)
        p.add_argument('--seconds', type=int, default=360)
        if not container:
            p.add_argument('--media-root', type=Path, required=True)
            p.add_argument('--container-network', action='store_true')


def emit_json(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def app_secret(args):
    reference = getattr(args, 'app_secret_ref', None)
    if reference:
        if args.app_secret_file or not re.fullmatch(r'secret:[A-Za-z0-9_-]{1,96}', reference):
            raise RuntimeFault('FEISHU_APP_SECRET_REFERENCE_INVALID')
        key = getattr(args, 'master_key_file', None)
        if key is None: raise RuntimeFault('FEISHU_APP_MASTER_KEY_REQUIRED')
        return selected_store()(args.root).resolve_secret(reference[7:], secret_input(key, ''))
    if not args.app_secret_file: raise RuntimeFault('FEISHU_APP_SECRET_REQUIRED')
    if getattr(args, 'master_key_file', None): raise RuntimeFault('FEISHU_MASTER_KEY_ONLY_WITH_REFERENCE')
    return secret_input(args.app_secret_file, '')


def validate(args):
    identifier(args.project)
    if not 1024 <= args.port <= 65535 or not 1 <= args.seconds <= 360:
        raise RuntimeFault('REVIEW_PORT_OR_DURATION_INVALID')


def run_review(args):
    server = None
    try:
        validate(args)
        if args.container_network and os.environ.get('VF_CONTAINER_MODE') != '1':
            raise RuntimeFault('CONTAINER_MODE_REQUIRED')
        oauth = DeviceOAuth(args.app_id, app_secret(args))
        service = ReviewService(selected_store()(args.root), args.project, args.media_root)
        server = ReviewServer(('0.0.0.0' if args.container_network else '127.0.0.1', args.port), service, oauth,
                              seconds=args.seconds, container_network=args.container_network)
        server.timeout = 1
        print(json.dumps({'status': 'ready', 'url': server.origin, 'project': args.project,
                          'expires_in': args.seconds, 'model_calls': 0}), flush=True)
        while server.clock() < server.deadline: server.handle_request()
        return 0
    except KeyboardInterrupt:
        return 130
    except RuntimeFault as error:
        print(json.dumps({'error': str(error), 'model_calls': 0})); return 2
    except Exception:
        print(json.dumps({'error': 'REVIEW_LISTENER_FAILED', 'model_calls': 0})); return 2
    finally:
        if server:
            server.sessions.clear()
            server.server_close()


def run_stack(args):
    return run_window(args)


def run_window(args, *, setup=False, emit=emit_json):
    try:
        validate(args)
        reference = getattr(args, 'app_secret_ref', None)
        if reference and (args.app_secret_file or not re.fullmatch(r'secret:[A-Za-z0-9_-]{1,96}', reference)):
            raise RuntimeFault('FEISHU_APP_SECRET_REFERENCE_INVALID')
        if not reference and (not args.app_secret_file or not args.app_secret_file.is_absolute() or not args.app_secret_file.is_relative_to('/work')
                or '..' in args.app_secret_file.parts):
            raise RuntimeFault('FEISHU_INPUTS_REQUIRE_CONTAINER_WORK_DIRECTORY')
        if setup:
            for path in (args.session, args.token_file):
                if not path.is_absolute() or not path.is_relative_to('/work') or '..' in path.parts:
                    raise RuntimeFault('FEISHU_INPUTS_REQUIRE_CONTAINER_WORK_DIRECTORY')
        if getattr(args, 'master_key_file', None): raise RuntimeFault('STACK_USES_OWN_MASTER_KEY')
        stack = Stack(args.stack_root)
        if stack.config['schema'] != 2: raise RuntimeFault('STACK_REVIEW_REQUIRES_UPGRADE')
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(('127.0.0.1', args.port))
        name = ('vf-setup-' if setup else 'vf-review-')+stack.config['instance']+'-'+secrets.token_hex(6)
        forward = None
        with stack.lock():
            if not stack.status()['infrastructure_ready']: raise RuntimeFault('STACK_WORKER_REQUIRES_HEALTHY_STACK')
            try:
                stack.compose('up', '-d', '--pull', 'never', '--wait', '--wait-timeout', '40', 'egress', timeout=60)
                started = time.monotonic()
                command = (['setup-feishu', 'connect', '--session', str(args.session), '--token-file', str(args.token_file)]
                           if setup else ['review-ui', '--media-root', '/media'])
                command += ['--root', '/state', '--project', args.project, '--app-id', args.app_id,
                            '--container-network',
                            '--port', str(args.port), '--seconds', str(args.seconds)]
                command += (['--app-secret-ref', reference, '--master-key-file', '/run/secrets/runtime_master']
                            if reference else ['--app-secret-file', str(args.app_secret_file)])
                stack.compose('run', '-d', '--name', name, '--no-deps', 'worker', *command, timeout=45)
                # Internal-only Docker bridges do not reliably publish a host
                # port. Keep the worker isolated and forward from host loopback
                # to this exact container's inspected private-network address.
                address = container_address(stack, name, docker_run)
                forward = Forward(args.port, (address, args.port), args.seconds+30)
                origin = f'http://127.0.0.1:{args.port}'
                ready = False
                for _ in range(15):
                    try:
                        with build_opener(ProxyHandler({})).open(origin+'/healthz', timeout=1) as response:
                            value = json.load(response)
                            ready = value.get('scope') == ('administrator_setup_window' if setup else 'employee_review_window') and value.get('project') == args.project
                    except Exception: pass
                    if ready: break
                    time.sleep(1)
                if not ready:
                    # The listener only emits fixed-code JSON. Never relay raw
                    # Docker stderr, arbitrary log text or credential values.
                    try:
                        lines = docker_run(['docker', 'container', 'logs', '--tail', '5', name], timeout=10).decode().splitlines()
                        for line in lines:
                            value = json.loads(line)
                            code = value.get('error', '')
                            if isinstance(code, str) and code and len(code)<=100 and all(c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789' for c in code):
                                raise RuntimeFault('STACK_REVIEW_START_'+code)
                    except RuntimeFault: raise
                    except Exception: pass
                    raise RuntimeFault('STACK_REVIEW_NOT_READY')
                if setup:
                    # Only the CLI opener receives the one-use unlock capability;
                    # never expose it through health or browser session routes.
                    lines = docker_run(['docker', 'container', 'logs', '--tail', '5', name], timeout=10).decode().splitlines()
                    urls = [v.get('url', '') for v in (json.loads(line) for line in lines) if v.get('status') == 'ready']
                    if len(urls) != 1 or not re.fullmatch(re.escape(origin)+r'/#[A-Za-z0-9_-]{43}', urls[0]):
                        raise RuntimeFault('SETUP_WINDOW_UNLOCK_UNAVAILABLE')
                    origin = urls[0]
                emit({'status': 'ready', 'url': origin, 'project': args.project, 'expires_in': args.seconds,
                      'model_calls': 0, 'access': 'loopback_or_same_port_ssh_tunnel'})
                saved = False
                deadline = time.monotonic()+args.seconds
                while time.monotonic() < deadline:
                    state = docker_run(['docker', 'container', 'ls', '--filter', 'name=^/'+name+'$', '--format', '{{.ID}}'], timeout=10)
                    if not state.strip():
                        if setup:
                            lines = docker_run(['docker', 'container', 'logs', '--tail', '5', name], timeout=10).decode().splitlines()
                            saved = any(v.get('status') == 'connection_binding_saved' and v.get('project') == args.project
                                        for v in (json.loads(line) for line in lines))
                        if time.monotonic() < started + args.seconds and not saved:
                            raise RuntimeFault('STACK_REVIEW_EXITED_EARLY')
                        break
                    time.sleep(1)
            finally:
                try:
                    if forward: forward.close()
                finally:
                    try:
                        state = docker_run(['docker', 'container', 'ls', '--all', '--filter', 'name=^/'+name+'$', '--format', '{{.ID}}'], timeout=10)
                        if state.strip(): docker_run(['docker', 'container', 'rm', '--force', name], timeout=20)
                    finally: stack.compose('stop', '--timeout', '5', 'egress', timeout=20)
        emit({'status': 'closed', 'model_calls': 0, 'connection_saved': saved})
        return 0
    except KeyboardInterrupt: return 130
    except RuntimeFault as error:
        emit({'error': str(error), 'model_calls': 0}); return 2
    except Exception:
        emit({'error': 'STACK_REVIEW_FAILED_CHECK_STATUS', 'model_calls': 0}); return 2
