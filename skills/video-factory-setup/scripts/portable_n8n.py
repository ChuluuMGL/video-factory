#!/usr/bin/env python3
"""Portable n8n lifecycle and public API provisioning. Python 3.11+, stdlib only.

No shell interpolation, global npm install, Docker prune, or credential export.
The local receipt contains resource IDs and digests, never credential values.
"""
import argparse
from contextlib import contextmanager
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

N8N_VERSION = '2.40.7'
N8N_IMAGE = 'n8nio/n8n:2.40.7@sha256:ffeb52485f78b1b06c9a832205853cf75da72a07a514c9a27724df85979d6c34'


class Fault(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def origin(value):
    parsed = urlsplit(value)
    if (parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ('', '/') or not parsed.hostname
            or not (parsed.scheme == 'https' or parsed.scheme == 'http'
                    and parsed.hostname in ('localhost', '127.0.0.1', '::1'))):
        raise Fault('HTTPS_OR_LOOPBACK_ORIGIN_REQUIRED')
    return value.rstrip('/')


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', value):
        raise Fault('IDENTIFIER_INVALID')
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def private_write(path, value):
    path = Path(path)
    if path.is_symlink():
        raise Fault('SYMLINK_REJECTED')
    temp = path.with_name(path.name + '.new')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@contextmanager
def locked(root):
    root = Path(root).resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = root / '.provision.lock'
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise Fault('SETUP_LOCKED_CHECK_RUNNING_PROCESS_BEFORE_REMOVING_LOCK') from None
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield root
    finally:
        path.unlink(missing_ok=True)


def secret(prompt):
    if not sys.stdin.isatty():
        raise Fault('PRIVATE_TERMINAL_REQUIRED')
    value = getpass.getpass(prompt)
    if not value or '\n' in value or '\r' in value:
        raise Fault('SECRET_EMPTY_OR_MULTILINE')
    return value


class API:
    def __init__(self, url, key):
        self.url = origin(url)
        self.key = key
        self.opener = build_opener(NoRedirect)

    def request(self, method, path, value=None):
        if not path.startswith('/api/v1/'):
            raise Fault('API_PATH_INVALID')
        request = Request(self.url + path, method=method,
                          headers={'X-N8N-API-KEY': self.key, 'Content-Type': 'application/json'},
                          data=None if value is None else json.dumps(value).encode())
        try:
            with self.opener.open(request, timeout=45) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise Fault('API_RESPONSE_TOO_LARGE')
                return json.loads(raw) if raw else {}
        except HTTPError as error:
            if error.code in (401, 403):
                raise Fault('N8N_API_PERMISSION_REQUIRED') from None
            raise Fault('N8N_API_HTTP_' + str(error.code)) from None
        except (OSError, ValueError):
            raise Fault('N8N_MUTATION_UNKNOWN_DO_NOT_RETRY' if method != 'GET'
                        else 'N8N_API_UNREACHABLE') from None

    def preflight(self):
        value = self.request('GET', '/api/v1/workflows?limit=1')
        if not isinstance(value.get('data'), list):
            raise Fault('N8N_API_SHAPE_INVALID')
        return {'api': 'reachable', 'workflow_count_at_least': len(value['data'])}


def provision(api, root, project, workflow, credentials):
    """Create only owned resources, persist intent before every uncertain POST.

    credentials = {node credential type: {name, type, data}}. Secrets stay in
    memory and are encrypted by n8n. A failed/unknown mutation blocks resubmit.
    """
    identifier(project)
    with locked(root) as root:
        path = root / ('binding-' + project + '.json')
        config_hash = digest(workflow)
        state = json.loads(path.read_text()) if path.exists() else {
            'schema': 1, 'origin': api.url, 'project': project,
            'template_sha256': config_hash, 'credentials': {}, 'workflow': None}
        if state['origin'] != api.url or state['template_sha256'] != config_hash:
            raise Fault('BINDING_CHANGED_USE_NEW_PROJECT_OR_EXPLICIT_MIGRATION')
        if state.get('pending'):
            raise Fault('PREVIOUS_MUTATION_UNCERTAIN_RECONCILE_RESOURCE_FIRST')
        api.preflight()
        for kind, spec in credentials.items():
            if kind in state['credentials']:
                continue
            name = f'Video Factory {project} {kind}'
            state['pending'] = {'kind': 'credential', 'name': name}
            private_write(path, state)
            result = api.request('POST', '/api/v1/credentials',
                                 {'name': name, 'type': spec['type'], 'data': spec['data']})
            rid = identifier(result.get('id'))
            state['credentials'][kind] = {'id': rid, 'name': name}
            state.pop('pending')
            private_write(path, state)
        if not state['workflow']:
            body = json.loads(json.dumps(workflow))
            body.pop('active', None)
            body['name'] = f'Video Factory {project}'
            for node in body['nodes']:
                for kind in node.get('credentials', {}):
                    node['credentials'][kind] = state['credentials'][kind]
            state['pending'] = {'kind': 'workflow', 'name': body['name']}
            private_write(path, state)
            result = api.request('POST', '/api/v1/workflows', body)
            state['workflow'] = identifier(result.get('id'))
            state.pop('pending')
            private_write(path, state)
        saved = api.request('GET', '/api/v1/workflows/' + state['workflow'])
        if saved.get('active'):
            raise Fault('WORKFLOW_ALREADY_ACTIVE_REVIEW_BEFORE_CHANGING')
        expected = json.loads(json.dumps(workflow))
        for node in expected['nodes']:
            for kind in node.get('credentials', {}):
                node['credentials'][kind] = state['credentials'][kind]
        if saved.get('nodes') != expected['nodes'] or saved.get('connections') != expected['connections']:
            raise Fault('WORKFLOW_READBACK_MISMATCH')
        state['verified_disabled'] = True
        private_write(path, state)
        return {k: state[k] for k in ('project', 'workflow', 'verified_disabled')}


def run_checked(command, cwd, env=None):
    # Never print subprocess output: npm/n8n may include environment/config data.
    result = subprocess.run(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=1200)
    if result.returncode:
        error = Fault('DEPENDENCY_COMMAND_FAILED_' + Path(command[0]).stem.upper())
        error.diagnostics = result.stdout
        raise error


def environment(root, port):
    env = os.environ.copy()
    env.update({'N8N_USER_FOLDER': str(root / 'data'), 'N8N_LISTEN_ADDRESS': '127.0.0.1',
                'N8N_PORT': str(port), 'N8N_SECURE_COOKIE': 'false',
                'N8N_DIAGNOSTICS_ENABLED': 'false', 'N8N_VERSION_NOTIFICATIONS_ENABLED': 'false',
                'N8N_CONCURRENCY_PRODUCTION_LIMIT': '1',
                'EXECUTIONS_DATA_SAVE_ON_ERROR': 'none', 'EXECUTIONS_DATA_SAVE_ON_SUCCESS': 'none'})
    return env


def health(url):
    try:
        with build_opener(NoRedirect).open(origin(url) + '/healthz', timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def install_local(root, mode, port=5678):
    if mode not in ('node', 'docker') or not 1024 <= port <= 65535:
        raise Fault('INSTALL_MODE_OR_PORT_INVALID')
    with locked(root) as root:
        receipt_path = root / 'local.json'
        if receipt_path.exists():
            saved = json.loads(receipt_path.read_text())
            if saved['mode'] != mode or saved['port'] != port:
                raise Fault('LOCAL_INSTALL_EXISTS_REUSE_OR_CHOOSE_EMPTY_DIRECTORY')
            return saved
        # New installs must not take over an existing listener, even if not n8n.
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', port))
            except OSError:
                raise Fault('PORT_IN_USE_REUSE_EXISTING_N8N_OR_SELECT_ANOTHER_PORT') from None
        (root / 'data').mkdir(mode=0o700, exist_ok=True)
        if mode == 'node':
            node = shutil.which('node')
            npm = shutil.which('npm')
            if not node or not npm:
                raise Fault('INSTALL_NODE_22_LTS_THEN_RESUME')
            version = subprocess.check_output([node, '--version'], text=True).strip()
            if not re.match(r'v22\.', version):
                raise Fault('NODE_22_REQUIRED_FOR_PINNED_N8N')
            # npm's JS entry works on Windows without cmd.exe or shell=True.
            npm_path = Path(npm).resolve()
            candidates = [npm_path.parent / 'node_modules/npm/bin/npm-cli.js',
                          npm_path.parent.parent / 'lib/node_modules/npm/bin/npm-cli.js',
                          npm_path if npm_path.suffix == '.js' else npm_path.parent / 'npm-cli.js']
            npm_cli = next((p for p in candidates if p.is_file()), None)
            if npm_cli is None:
                raise Fault('NPM_ENTRY_NOT_FOUND')
            private_write(root / 'package.json', {'name': 'video-factory-local', 'private': True,
                                                  'dependencies': {'n8n': N8N_VERSION}})
            run_checked([node, str(npm_cli), 'install', '--no-audit', '--no-fund'], root)
        else:
            docker = shutil.which('docker')
            if not docker:
                raise Fault('INSTALL_DOCKER_DESKTOP_OR_ENGINE_THEN_RESUME')
            run_checked([docker, 'compose', 'version'], root)
            # Compose accepts JSON. A dedicated project name keeps existing stacks intact.
            private_write(root / 'compose.json', {'services': {'n8n': {
                'image': N8N_IMAGE, 'ports': [f'127.0.0.1:{port}:5678'],
                'restart': 'unless-stopped', 'environment': {
                    'N8N_SECURE_COOKIE': 'false', 'N8N_DIAGNOSTICS_ENABLED': 'false',
                    'N8N_CONCURRENCY_PRODUCTION_LIMIT': '1',
                    'EXECUTIONS_DATA_SAVE_ON_ERROR': 'none', 'EXECUTIONS_DATA_SAVE_ON_SUCCESS': 'none'},
                'volumes': ['n8n_data:/home/node/.n8n']}}, 'volumes': {'n8n_data': {}}})
            run_checked([docker, 'compose', '-p', 'vf-' + digest(str(root))[:12],
                         '-f', 'compose.json', 'pull'], root)
        saved = {'schema': 1, 'mode': mode, 'version': N8N_VERSION, 'port': port,
                 'origin': f'http://127.0.0.1:{port}', 'installed': True, 'project': 'vf-' + digest(str(root))[:12]}
        private_write(receipt_path, saved)
        return saved


def local_control(root, action):
    with locked(root) as root:
        saved = json.loads((root / 'local.json').read_text())
        if saved['mode'] == 'docker':
            command = [shutil.which('docker') or 'docker', 'compose', '-p', saved['project'], '-f', 'compose.json']
            run_checked(command + (['up', '-d'] if action == 'start' else ['stop']), root)
        elif action == 'start':
            if health(saved['origin']):
                if saved.get('pid'):
                    return {'status': 'reachable', 'origin': saved['origin']}
                raise Fault('PORT_IN_USE_NOT_STARTED_BY_THIS_INSTALLER')
            executable = root / 'node_modules/n8n/bin/n8n'
            if not executable.is_file():
                raise Fault('LOCAL_N8N_NOT_INSTALLED')
            flags = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
            with open(os.devnull, 'w') as sink:
                process = subprocess.Popen([shutil.which('node') or 'node', str(executable), 'start'],
                    cwd=root, env=environment(root, saved['port']), stdin=subprocess.DEVNULL,
                    stdout=sink, stderr=sink, **flags)
            saved['pid'] = process.pid
            private_write(root / 'local.json', saved)
        else:
            # PID reuse makes blind kill unsafe. Stop in the owning terminal or
            # via the OS after matching the full executable and user folder.
            raise Fault('NATIVE_STOP_REQUIRES_VERIFIED_PROCESS_USE_OS_PROCESS_MANAGER')
        if action == 'start':
            for _ in range(120):
                if health(saved['origin']):
                    return {'status': 'reachable', 'origin': saved['origin'], 'owner_setup': 'complete_in_n8n_editor'}
                time.sleep(1)
            raise Fault('START_TIMEOUT_INSPECT_OWNED_N8N_PROCESS')
        return {'status': 'stopped', 'data_preserved': True}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Video Factory 本机 n8n / 已有实例接入')
    parser.add_argument('--root', type=Path, default=Path.home() / '.video-factory/n8n')
    commands = parser.add_subparsers(dest='command', required=True)
    install = commands.add_parser('install')
    install.add_argument('--mode', choices=['node', 'docker'], required=True)
    install.add_argument('--port', type=int, default=5678)
    for name in ('start', 'stop'):
        commands.add_parser(name)
    check = commands.add_parser('check')
    check.add_argument('--url', required=True)
    bind = commands.add_parser('connect')
    bind.add_argument('--url', required=True)
    bind.add_argument('--project', required=True)
    bind.add_argument('--base', required=True)
    bind.add_argument('--table', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'install':
            value = install_local(args.root, args.mode, args.port)
        elif args.command in ('start', 'stop'):
            value = local_control(args.root, args.command)
        else:
            api = API(args.url, secret('n8n API Key（隐藏输入）: '))
            if args.command == 'check':
                value = api.preflight()
            else:
                from n8n_workflows import probe_workflow
                app_id = input('飞书 App ID: ').strip()
                identifier(app_id)
                app_secret = secret('飞书 App Secret（隐藏输入）: ')
                credential = {'type': 'httpCustomAuth', 'data': {'json': json.dumps(
                    {'body': {'app_id': app_id, 'app_secret': app_secret}})}}
                value = provision(api, args.root, args.project,
                    probe_workflow(args.base, args.table), {'httpCustomAuth': credential})
                value['next'] = '在 n8n 执行已导入的飞书连通检查；安装未激活付费生成。'
        print(json.dumps(value, ensure_ascii=False))
        return 0
    except Fault as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        return 2
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        print(json.dumps({'error': 'SETUP_FAILED_NO_SECRETS_LOGGED'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
