"""Opt-in foreground local kernel. Installation and real Feishu wiring are separate."""
import json
import os
from pathlib import Path
import signal
import sys
import threading

from . import automation
from .agent_api import AgentAPI, AgentServer
from .runtime_cli import secret_input
from .runtime_store import RuntimeFault, RuntimeStore, exclusive_write, private_directory
from .scheduler import Scheduler, runtime_lock


def default_root():
    home = Path.home()
    return (home / 'Library/Application Support/VideoFactory' if sys.platform == 'darwin'
            else home / '.local/share/video-factory')


def register(commands):
    up = commands.add_parser('up', help='foreground local kernel candidate; read-only by default')
    up.add_argument('--root', type=Path, default=default_root())
    up.add_argument('--project', required=True)
    up.add_argument('--port', type=int, default=8788)
    up.add_argument('--interval', type=float, default=5)
    up.add_argument('--execute-approved', action='store_true', help='enable existing approved jobs; may incur model costs')
    up.add_argument('--execution-key-file', type=Path)
    up.add_argument('--master-key-file', type=Path)
    up.add_argument('--media-root', type=Path)
    key = commands.add_parser('agent-key', help='save a project-scoped read-only token to a new private file')
    key.add_argument('--root', type=Path, default=default_root())
    key.add_argument('--project', required=True)
    key.add_argument('--admin-token-file', type=Path, required=True)
    key.add_argument('--output', type=Path, required=True)
    key.add_argument('--ttl-hours', type=int, default=24)


def serve(store, project, port, stop, *, execution_token=None, master_key=None,
          media_root=None, interval=5, on_ready=lambda value: None):
    if type(port) is not int or not 0 <= port <= 65535:
        raise RuntimeFault('PORT_INVALID')
    with runtime_lock(store.root):
        scheduler = Scheduler(store, project, execution_token=execution_token, master_key=master_key,
                              media_root=media_root, interval=interval)
        with AgentServer(('127.0.0.1', port), AgentAPI(store, project, scheduler)) as server:
            thread = threading.Thread(target=scheduler.run, args=(stop,), name='vf-local-scheduler')
            thread.start()
            try:
                on_ready({'listening': '127.0.0.1', 'port': server.server_port,
                          'scope': 'local_kernel_candidate', **scheduler.status()})
                while not stop.is_set():
                    server.handle_request()
            finally:
                stop.set()
                # Retain ownership until the in-flight operation has settled.
                # A crash during submit still leaves the durable uncertain intent.
                thread.join()


def cli(args):
    emit = lambda value: print(json.dumps(value, ensure_ascii=False, sort_keys=True), flush=True)
    try:
        if sys.platform not in ('darwin', 'linux'):
            raise RuntimeFault('LOCAL_PLATFORM_UNSUPPORTED')
        if os.environ.get('VF_CONTAINER_MODE') == '1' or os.environ.get('VF_DATABASE_URL_FILE'):
            raise RuntimeFault('LOCAL_SQLITE_ENVIRONMENT_REQUIRED')
        store = RuntimeStore(args.root)
        if args.command == 'agent-key':
            private_directory(args.output.parent)
            if args.output.exists() or args.output.is_symlink():
                raise RuntimeFault('AGENT_TOKEN_OUTPUT_EXISTS')
            admin = secret_input(args.admin_token_file, '')
            receipt = automation.issue(store, admin, args.project, args.ttl_hours, scope='agent_read')
            try:
                exclusive_write(args.output, receipt['token'].encode())
            except BaseException:
                automation.revoke(store, admin, receipt['key_id'])
                raise
            emit({k: receipt[k] for k in ('project', 'scope', 'expires_at', 'key_id')})
            return 0
        supplied = (args.execution_key_file, args.master_key_file, args.media_root)
        if args.execute_approved and not all(supplied):
            raise RuntimeFault('LOCAL_EXECUTION_INPUTS_REQUIRED')
        if not args.execute_approved and any(supplied):
            raise RuntimeFault('LOCAL_EXPLICIT_EXECUTION_REQUIRED')
        token = secret_input(args.execution_key_file, '') if args.execute_approved else None
        key = secret_input(args.master_key_file, '') if args.execute_approved else None
        stop = threading.Event()
        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            for sig in previous:
                signal.signal(sig, lambda *_: stop.set())
            serve(store, args.project, args.port, stop, execution_token=token, master_key=key,
                  media_root=args.media_root, interval=args.interval, on_ready=emit)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        emit({'status': 'stopped', 'scope': 'local_kernel_candidate'})
        return 0
    except RuntimeFault as error:
        emit({'error': str(error)})
        return 2
    except Exception:
        emit({'error': 'LOCAL_RUNTIME_FAILED'})
        return 2
