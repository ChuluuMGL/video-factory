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
import signal
import secrets
import socket
import subprocess
import sys
import time
import tempfile
import venv
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
    if value==':quit':raise Fault('SETUP_PAUSED_CONFIG_PRESERVED')
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
        state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {
            'schema': 1, 'origin': api.url, 'project': project,
            'template_sha256': config_hash, 'credentials': {}, 'workflow': None}
        if state['origin'] != api.url or state['template_sha256'] != config_hash:
            raise Fault('BINDING_CHANGED_USE_NEW_PROJECT_OR_EXPLICIT_MIGRATION')
        if state.get('pending'):
            raise Fault('PREVIOUS_MUTATION_UNCERTAIN_RECONCILE_RESOURCE_FIRST')
        api.preflight()
        for kind, spec in credentials.items():
            if kind in state['credentials']:
                if 'existing' in spec and state['credentials'][kind] != spec['existing']:
                    raise Fault('CREDENTIAL_REFERENCE_CHANGED_EXPLICIT_MIGRATION_REQUIRED')
                continue
            if 'existing' in spec:
                ref=spec['existing']
                identifier(ref.get('id'))
                if not isinstance(ref.get('name'),str) or not ref['name']:
                    raise Fault('CREDENTIAL_REFERENCE_INVALID')
                state['credentials'][kind]={'id':ref['id'],'name':ref['name']}
                private_write(path,state)
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
                    role = 'relay' if node['credentials'][kind].get('id') == 'RELAY_UNBOUND' else kind
                    node['credentials'][kind] = state['credentials'][role]
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
                role = 'relay' if node['credentials'][kind].get('id') == 'RELAY_UNBOUND' else kind
                node['credentials'][kind] = state['credentials'][role]
        if (saved.get('nodes') != expected['nodes'] or saved.get('connections') != expected['connections']
                or any(saved.get('settings',{}).get(k) != v for k,v in expected['settings'].items())):
            raise Fault('WORKFLOW_READBACK_MISMATCH')
        state['verified_disabled'] = True
        state['owned_template'] = {k: expected[k] for k in ('nodes','connections','settings')}
        private_write(path, state)
        return {k: state[k] for k in ('project', 'workflow', 'verified_disabled')}


def ensure_ledger(api, root, project, base, table):
    """Provision one project mutex/result row in n8n's existing database."""
    identifier(project)
    with locked(root) as root:
        path=root/('ledger-'+project+'.json')
        saved=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'origin':api.url,'project':project,'base':base,'task_table':table}
        if saved.get('base')!=base or saved.get('task_table')!=table: raise Fault('LEDGER_BASE_SCOPE_CHANGED')
        if saved['origin']!=api.url: raise Fault('LEDGER_ORIGIN_CHANGED')
        if saved.get('pending'): raise Fault('LEDGER_MUTATION_UNCERTAIN_RECONCILE_FIRST')
        if not saved.get('table'):
            # Capability/read scope is checked before recording a create intent.
            try:
                inventory=api.request('GET','/api/v1/data-tables?limit=1')
            except Fault as error:
                if str(error) in ('N8N_API_HTTP_404','N8N_API_PERMISSION_REQUIRED'):
                    raise Fault('N8N_DATA_TABLE_API_AND_SCOPES_REQUIRED_EXISTING_INSTANCE_UNCHANGED') from None
                raise
            if not isinstance(inventory.get('data'),list): raise Fault('N8N_DATA_TABLE_API_SHAPE_INVALID')
            saved['pending']='table'; private_write(path,saved)
            result=api.request('POST','/api/v1/data-tables',{
                'name':'Video Factory '+project+' execution',
                'columns':[{'name':name,'type':'string'} for name in ('status','owner','payload','result')]})
            saved['table']=identifier(result.get('id'))
            saved.pop('pending'); private_write(path,saved)
        if not saved.get('row'):
            saved['pending']='row'; private_write(path,saved)
            result=api.request('POST','/api/v1/data-tables/'+saved['table']+'/rows',{
                'data':[{'status':'idle','owner':'','payload':'','result':''}],'returnType':'all'})
            if not isinstance(result,list) or len(result)!=1 or type(result[0].get('id')) is not int:
                raise Fault('LEDGER_ROW_RECEIPT_INVALID')
            saved['row']=result[0]['id']; saved.pop('pending'); private_write(path,saved)
        rows=api.request('GET','/api/v1/data-tables/'+saved['table']+'/rows?limit=100')['data']
        if len(rows)!=1 or rows[0].get('id')!=saved['row']:
            raise Fault('LEDGER_CHANGED_REQUIRES_REVIEW')
        return {'table':saved['table'],'row':saved['row']}


def run_checked(command, cwd, env=None, timeout=1200):
    # Raw dependency output stays private. Progress contains only elapsed time.
    # On timeout/interruption, stop the process tree before releasing the setup
    # lock, so a resumed installer cannot race an abandoned native build.
    options={'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {'start_new_session':True}
    with tempfile.TemporaryFile() as output:
        process=subprocess.Popen(command,cwd=cwd,env=env,stdout=output,stderr=subprocess.STDOUT,**options)
        started=time.monotonic()
        def stop_build():
            if process.poll() is None:
                if os.name=='nt':
                    subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
                else:
                    os.killpg(process.pid,signal.SIGKILL)
                process.wait(timeout=30)
        try:
            while process.poll() is None:
                remaining=timeout-(time.monotonic()-started)
                if remaining<=0:
                    stop_build()
                    output.seek(0)
                    error=Fault('DEPENDENCY_INSTALL_TIMEOUT_PARTIAL_FILES_PRESERVED_RESUME_SAME_DIRECTORY')
                    error.diagnostics=output.read()[-12000:]
                    raise error
                try: process.wait(timeout=min(30,remaining))
                except subprocess.TimeoutExpired:
                    print('依赖安装仍在运行（'+str(int(time.monotonic()-started))+' 秒）…',file=sys.stderr,flush=True)
        except BaseException:
            stop_build()
            raise
        if process.returncode:
            output.seek(0)
            error=Fault('DEPENDENCY_COMMAND_FAILED_'+Path(command[0]).stem.upper())
            error.diagnostics=output.read()[-12000:]
            raise error


def environment(root, port):
    # A fresh owned instance must never inherit another n8n database/queue or
    # encryption configuration from the operator's shell. Existing instances
    # are reached via API and do not use this process environment.
    prefixes=('N8N_','DB_','QUEUE_','EXECUTIONS_','CREDENTIALS_')
    excluded={'NODE_OPTIONS','NODE_PATH','WEBHOOK_URL','DATABASE_URL'}
    env = {key:value for key,value in os.environ.items()
           if not key.startswith(prefixes) and key not in excluded}
    receipt=root/'local.json'
    if receipt.exists():
        saved=json.loads(receipt.read_text(encoding='utf-8'))
        if saved.get('node_executable'): env['PATH']=str(Path(saved['node_executable']).parent)+os.pathsep+env.get('PATH','')
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


def ready(url):
    """Database readiness, distinct from a live HTTP listener."""
    try:
        with build_opener(NoRedirect).open(origin(url) + '/healthz/readiness', timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def install_local(root, mode, port=5678, managed_node=False, dependency_timeout=1200):
    if type(dependency_timeout) is not int or not 60 <= dependency_timeout <= 7200:
        raise Fault('DEPENDENCY_TIMEOUT_MUST_BE_60_TO_7200_SECONDS')
    if mode not in ('node', 'docker') or not 1024 <= port <= 65535:
        raise Fault('INSTALL_MODE_OR_PORT_INVALID')
    with locked(root) as root:
        receipt_path = root / 'local.json'
        if receipt_path.exists():
            saved = json.loads(receipt_path.read_text(encoding='utf-8'))
            if saved['mode'] != mode or saved['port'] != port:
                raise Fault('LOCAL_INSTALL_EXISTS_REUSE_OR_CHOOSE_EMPTY_DIRECTORY')
            return saved
        plan = {'mode': mode, 'version': N8N_VERSION, 'port': port}
        plan_path = root / 'install-plan.json'
        if plan_path.exists():
            if json.loads(plan_path.read_text(encoding='utf-8')) != plan:
                raise Fault('INSTALL_PLAN_CHANGED_USE_ORIGINAL_PARAMETERS')
        elif any(p.name != '.provision.lock' for p in root.iterdir()):
            raise Fault('INSTALL_DIRECTORY_NOT_EMPTY_EXISTING_FILES_PRESERVED')
        else:
            private_write(plan_path, plan)
        # New installs must not take over an existing listener, even if not n8n.
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', port))
            except OSError:
                raise Fault('PORT_IN_USE_REUSE_EXISTING_N8N_OR_SELECT_ANOTHER_PORT') from None
        (root / 'data').mkdir(mode=0o700, exist_ok=True)
        if mode == 'node':
            node = None if managed_node else shutil.which('node')
            version = subprocess.check_output([node, '--version'], text=True).strip() if node else ''
            if not re.fullmatch(r'v24\.\d+\.\d+',version):
                from node_runtime import prepare
                node=prepare(root/'tools')
            node_path=Path(node).resolve()
            candidates=[node_path.parent/'node_modules/npm/bin/npm-cli.js',
                        node_path.parent.parent/'lib/node_modules/npm/bin/npm-cli.js']
            npm_cli=next((p for p in candidates if p.is_file()),None)
            if npm_cli is None:
                from node_runtime import prepare
                node=prepare(root/'tools'); node_path=Path(node)
                npm_cli=(node_path.parent/'node_modules/npm/bin/npm-cli.js' if os.name=='nt' else node_path.parent.parent/'lib/node_modules/npm/bin/npm-cli.js')
            # n8n's native dependencies may fall back to node-gyp. Keep its
            # Python/distutils compatibility tooling private, not in system Python.
            toolenv = root / 'build-tools'
            venv.EnvBuilder(with_pip=True).create(toolenv)
            python = toolenv / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            run_checked([str(python), '-m', 'pip', 'install', 'setuptools==80.9.0'], root)
            build_env = {key:value for key,value in os.environ.items() if key not in ('NODE_OPTIONS','NODE_PATH')}
            build_env['PATH']=str(Path(node).parent)+os.pathsep+build_env.get('PATH','')
            build_env['npm_config_python'] = str(python)
            build_env['npm_config_jobs'] = '2'
            # Keep cache and dependency writes on the selected installation volume.
            # Hosted Windows system-temp/cache volumes can be much slower than
            # the target disk; do not inherit a cache on another volume.
            build_env={key:value for key,value in build_env.items() if key.lower()!='npm_config_cache'}
            build_env['npm_config_cache']=str(root/'npm-cache')
            private_write(root / 'package.json', {'name': 'video-factory-local', 'private': True,
                                                  'dependencies': {'n8n': N8N_VERSION}})
            run_checked([node, str(npm_cli), 'install', '--global=false', '--prefix', str(root), '--no-audit', '--no-fund', '--foreground-scripts'], root, env=build_env, timeout=dependency_timeout)
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
        if mode=='node': saved['node_executable']=str(Path(node).resolve())
        private_write(receipt_path, saved)
        return saved


def process_identity(pid):
    if not isinstance(pid, int) or pid <= 0:
        raise Fault('PROCESS_ID_INVALID')
    if os.name == 'nt':
        command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                   f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={pid}'; if($p){{$p.CreationDate.ToUniversalTime().Ticks}}"]
    else:
        command = ['ps', '-p', str(pid), '-o', 'lstart=']
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20)
    return result.stdout.decode(errors='replace').strip() if result.returncode == 0 else ''


def local_control(root, action):
    service=Path(root)/'.vf-n8n-service/service.json'
    if service.exists() and json.loads(service.read_text(encoding='utf-8')).get('installed'):
        from portable_service import control
        return control(root,'n8n',action)
    with locked(root) as root:
        saved = json.loads((root / 'local.json').read_text(encoding='utf-8'))
        if saved['mode'] == 'docker':
            command = [shutil.which('docker') or 'docker', 'compose', '-p', saved['project'], '-f', 'compose.json']
            run_checked(command + (['up', '-d'] if action == 'start' else ['stop']), root)
        elif action == 'start':
            if health(saved['origin']):
                if saved.get('pid') and saved.get('process_identity') == process_identity(saved['pid']):
                    return {'status': 'reachable', 'origin': saved['origin']}
                raise Fault('PORT_IN_USE_NOT_STARTED_BY_THIS_INSTALLER')
            executable = root / 'node_modules/n8n/bin/n8n'
            if not executable.is_file():
                raise Fault('LOCAL_N8N_NOT_INSTALLED')
            flags = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
            log_path = root / 'n8n-local.log'
            if log_path.is_symlink(): raise Fault('SYMLINK_REJECTED')
            log_fd = os.open(log_path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
            with os.fdopen(log_fd, 'w') as sink:
                process = subprocess.Popen([saved['node_executable'], str(executable), 'start'],
                    cwd=root, env=environment(root, saved['port']), stdin=subprocess.DEVNULL,
                    stdout=sink, stderr=sink, **flags)
            saved['pid'] = process.pid
            saved['process_identity'] = process_identity(process.pid)
            if not saved['process_identity']:
                raise Fault('START_PROCESS_NOT_RUNNING')
            private_write(root / 'local.json', saved)
        else:
            pid = saved.get('pid')
            if not pid:
                return {'status': 'stopped', 'data_preserved': True}
            current = process_identity(pid)
            if current and current != saved.get('process_identity'):
                raise Fault('PID_REUSED_REFUSING_TO_STOP_UNRELATED_PROCESS')
            if current:
                if os.name == 'nt':
                    run_checked(['taskkill.exe', '/PID', str(pid), '/T', '/F'], root)
                else:
                    if os.getpgid(pid) != pid:
                        raise Fault('PROCESS_GROUP_CHANGED_REFUSING_TO_STOP')
                    os.killpg(pid, signal.SIGTERM)
                for _ in range(30):
                    if not health(saved['origin']): break
                    time.sleep(1)
                else: raise Fault('STOP_TIMEOUT_DATA_PRESERVED')
            saved.pop('pid', None)
            saved.pop('process_identity', None)
            private_write(root / 'local.json', saved)
        if action == 'start':
            for _ in range(120):
                if health(saved['origin']):
                    return {'status': 'reachable', 'origin': saved['origin'], 'owner_setup': 'complete_in_n8n_editor'}
                if saved['mode']=='node' and not process_identity(saved['pid']):
                    raise Fault('START_FAILED_INSPECT_PRIVATE_N8N_LOG')
                time.sleep(1)
            raise Fault('START_TIMEOUT_INSPECT_OWNED_N8N_PROCESS')
        return {'status': 'stopped', 'data_preserved': True}


def verify_setup(api, root, project, template, credentials):
    """Run a zero-model setup workflow through a temporary authenticated hook."""
    from n8n_workflows import setup_hook
    hook_path = 'vf-setup-' + identifier(project)
    token = secrets.token_urlsafe(32)
    credentials = {**credentials, 'httpHeaderAuth': {'type':'httpHeaderAuth',
                   'data': {'name':'X-VF-Setup', 'value':token}}}
    template = setup_hook(template, hook_path)
    # A unique receipt namespace is required for each temporary hook token.
    # Keep the durable regular workflow binding separate and disabled.
    unique = project + '-' + secrets.token_hex(4)
    saved = provision(api, root, unique, template, credentials)
    workflow_id = saved['workflow']
    activated = False
    try:
        activated = True  # Mark intent before POST: timeout is also deactivated.
        api.request('POST','/api/v1/workflows/'+workflow_id+'/activate',{})
        request = Request(api.url+'/webhook/'+hook_path, method='POST',data=b'{}',
                          headers={'X-VF-Setup':token,'Content-Type':'application/json'})
        try:
            # Publishing registers webhooks asynchronously. Only a definite
            # 404 may be retried, and this helper rejects all model workflows.
            for attempt in range(10):
                try:
                    with build_opener(NoRedirect).open(request,timeout=180) as response:
                        raw=response.read(65537)
                        if len(raw)>65536: raise Fault('SETUP_CHECK_RESPONSE_TOO_LARGE')
                        value=json.loads(raw)
                    break
                except HTTPError as error:
                    if error.code!=404 or attempt==9: raise
                    time.sleep(2)
        except (OSError,ValueError):
            raise Fault('SETUP_CHECK_INCOMPLETE_READ_N8N_EXECUTION') from None
        entries = value if isinstance(value,list) else [value]
        if not entries or any(not isinstance(item,dict) or not (item.get('base_read')=='verified' or item.get('schema_change')=='accepted') for item in entries):
            raise Fault('SETUP_CHECK_RECEIPT_INVALID')
        return {'workflow':workflow_id,'result':value,'provider_requests':0}
    finally:
        if activated:
            api.request('POST','/api/v1/workflows/'+workflow_id+'/deactivate',{})
            if api.request('GET','/api/v1/workflows/'+workflow_id).get('active'):
                raise Fault('TEMPORARY_SETUP_HOOK_STILL_ACTIVE_DISABLE_IN_N8N')


def provider_secret(prompt, ask_secret):
    """Normalize copied header decoration, never guess an embedded key repair."""
    for attempt in range(3):
        try:
            key=ask_secret(prompt).strip()
        except Fault as error:
            if str(error)!='SECRET_EMPTY_OR_MULTILINE': raise
            key=''
        if key==':quit': raise Fault('CREDENTIAL_INPUT_CANCELLED_EXISTING_CONFIG_PRESERVED')
        if key.lower().startswith('bearer '): key=key[7:].strip()
        if key and all(33<=ord(c)<=126 for c in key): return key
        if attempt<2:
            print('Key 格式未通过：请重新粘贴单行原始 Key，内部不能含空格或控制字符；输入 :quit 保存已有配置并退出。',flush=True)
    raise Fault('RAW_PROVIDER_KEY_REQUIRED_EXISTING_CONFIG_PRESERVED')


def project_credentials(api, root, project, app, roles, ask_secret=None):
    """One n8n-encrypted credential per role; local checkpoints contain only IDs.

    A resumed setup reuses these exact IDs. Credential rotation is an explicit
    operation in n8n, never a silently ignored new key typed into this wizard.
    """
    ask_secret=ask_secret or secret
    identifier(project); identifier(app)
    with locked(root) as root:
        path=root/('credentials-'+project+'.json')
        state=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {
            'origin':api.url,'project':project,'app':app,'roles':{}}
        if (state['origin'],state['project'],state['app'])!=(api.url,project,app):
            raise Fault('PROJECT_CREDENTIAL_SCOPE_CHANGED')
        if state.get('pending'): raise Fault('CREDENTIAL_CREATE_UNKNOWN_RECONCILE_IN_N8N')
        for role in roles:
            if role not in ('feishu','script','video'): raise Fault('CREDENTIAL_ROLE_INVALID')
            if role in state['roles']: continue
            if role=='feishu':
                kind='httpCustomAuth'
                data={'json':json.dumps({'body':{'app_id':app,'app_secret':ask_secret('飞书 App Secret（隐藏输入）: ')}})}
            else:
                key=provider_secret(('DeepSeek' if role=='script' else 'MiniMax 中国区')+' API Key（隐藏输入，:quit 退出）: ',ask_secret)
                kind='httpHeaderAuth'; data={'name':'Authorization','value':'Bearer '+key}
            name='Video Factory '+project+' '+role
            state['pending']={'role':role,'name':name}; private_write(path,state)
            created=api.request('POST','/api/v1/credentials',{'name':name,'type':kind,'data':data})
            state['roles'][role]={'type':kind,'existing':{'id':identifier(created.get('id')),'name':name}}
            state.pop('pending'); private_write(path,state)
        return {role:state['roles'][role] for role in roles}


def configure_flows(api, root, project, base, table, credentials, *, feishu_origin='https://open.feishu.cn',
                    script_origin='https://api.deepseek.com',video_origin='https://api.minimax.cn'):
    """Import a complete disabled bundle, with one shared project execution row."""
    from n8n_workflows import production_workflow,guard_workflow,recovery_workflow
    ledger=ensure_ledger(api,root,project,base,table)
    result={}
    for flow,role in [('script','script'),('video-submit','video'),('video-poll','video'),('recover',None)]:
        if role and role not in credentials: continue
        template=(recovery_workflow(base,table,ledger,feishu_origin=feishu_origin) if flow=='recover' else
                  production_workflow(base,table,flow,feishu_origin=feishu_origin,
                                      script_origin=script_origin,video_origin=video_origin))
        if flow in ('script','video-submit'): template=guard_workflow(template,ledger)
        selected={'httpCustomAuth':credentials['feishu']}
        if role: selected['httpHeaderAuth']=credentials[role]
        result[flow]=provision(api,root,project+'-'+flow,template,selected)
    return result


def console_utf8():
    if os.name=='nt':
        for stream in (sys.stdout,sys.stderr):
            if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')


def ask_value(prompt, validate=lambda value:value, default=None):
    console_utf8()
    while True:
        answer=input(prompt).strip()
        if answer==':quit':raise Fault('SETUP_PAUSED_CONFIG_PRESERVED')
        answer=answer or default
        try:
            if answer is None:raise ValueError()
            return validate(answer)
        except (ValueError,Fault):print('格式不正确，请重输；输入 :quit 保存退出。')


def ask_choice(prompt, choices, default=None):
    console_utf8()
    while True:
        answer=input(prompt).strip()
        if answer==':quit':raise Fault('SETUP_PAUSED_CONFIG_PRESERVED')
        answer=answer.lower() or default
        if answer in choices:return answer
        print('请选择 '+ '/'.join(choices)+'；输入 :quit 保存退出。')


def welcome(root, dependency_timeout=1200):
    console_utf8()
    import platform
    from n8n_workflows import probe_workflow, schema_workflow
    print('Video Factory · 本机 / 已有 n8n 接入（开发候选）')
    print('检测到系统：'+platform.system()+'；Node.js：'+str(bool(shutil.which('node')))+'；Docker：'+str(bool(shutil.which('docker'))))
    print('1. 已有 n8n（本机或服务器）  2. 新装本机 Node.js n8n  3. 新装 Docker n8n  4. n8n Cloud')
    root=Path(root)
    print('输入 :quit 可退出；已完成的安装和项目配置会保留。')
    choice=ask_choice('选择 1/2/3/4: ',('1','2','3','4'))
    if choice in ('2','3'):
        previous=root/'local.json'
        def port_value(value):
            number=int(value)
            if not 1024<=number<=65535:raise ValueError()
            return number
        port=json.loads(previous.read_text(encoding='utf-8'))['port'] if previous.exists() else ask_value('本机端口 [5678]: ',port_value,'5678')
        saved=install_local(root,'node' if choice=='2' else 'docker',port,dependency_timeout=dependency_timeout)
        local_control(root,'start')
        url=saved['origin']
        print('n8n 已启动：'+url+'。首次请在 n8n 编辑器设置自己的账号并创建 API Key。')
    else:
        url=ask_value('已有 n8n 地址: ',origin)
    for attempt in range(3):
        key=secret('n8n API Key（隐藏输入，本轮验证后复用；:quit 退出）: ')
        if key==':quit':raise Fault('SETUP_PAUSED_CONFIG_PRESERVED')
        api=API(url,key)
        try:api.preflight();break
        except Fault as error:
            if str(error)!='N8N_API_PERMISSION_REQUIRED' or attempt==2:raise
            print('API Key 未通过只读认证，请核对实例和权限后重新输入。已有配置保留。')
    project=ask_value('项目标识（字母、数字、短横线；续接用原标识）: ',identifier)
    bindings=root/'bindings'
    path=bindings/('project-'+project+'.json')
    if path.exists():
        config=json.loads(path.read_text(encoding='utf-8'))
        if config['origin']!=url: raise Fault('PROJECT_ORIGIN_CHANGED')
        base,table,app=config['base'],config['table'],config['app']
        print('续接已保存的项目：'+project+'；Base：'+base+'；任务表：'+table)
    else:
        base=ask_value('飞书 Base token: ',identifier)
        table=ask_value('任务表 table ID: ',identifier)
        app=ask_value('飞书 App ID: ',identifier)
        with locked(bindings):
            private_write(path,{'origin':url,'base':base,'table':table,'app':app})
    credentials=project_credentials(api,bindings,project,app,['feishu'])
    selected={'httpCustomAuth':credentials['feishu']}
    receipt=verify_setup(api,bindings,project+'-probe',probe_workflow(base,table),selected)
    print('飞书读取检查完成。')
    if ask_choice('为这张表补齐审核字段（保留原字段与选项）？y/N: ',('y','n'),'n')=='y':
        receipt['schema']=verify_setup(api,bindings,project+'-schema',schema_workflow(base,table),selected)
    roles=[]
    print('接下来可导入脚本、视频和恢复流程，均保持关闭，不会调用模型。')
    if ask_choice('配置 DeepSeek 脚本流程？y/N: ',('y','n'),'n')=='y': roles.append('script')
    if ask_choice('配置 MiniMax H3 视频流程？y/N: ',('y','n'),'n')=='y': roles.append('video')
    if roles:
        credentials=project_credentials(api,bindings,project,app,['feishu',*roles])
        receipt['flows']=configure_flows(api,bindings,project,base,table,credentials)
    if choice=='2' and ask_choice('为这套新装 n8n 配置后台自启？桌面登录后启动，Linux root 开机启动。y/N: ',('y','n'),'n')=='y':
        from portable_service import install
        receipt['background']=install(root,'n8n')
    elif choice=='3':
        print('Docker 容器已设置 unless-stopped；请让 Docker Desktop/Engine 随系统启动。')
    receipt.update(project=project,production_ready=False,next='接入和关闭状态的流程已保存；人工审核在飞书。下一步配置指定负责人并做零费用确认验收，不会自动开启收费。')
    private_write(bindings/('handoff-'+project+'.json'),{'project':project,'origin':url,'base':base,'table':table,
        'credentials_saved_in':'n8n','production_ready':False,'next':receipt['next'],
        'background':receipt.get('background',{'managed':False,'reason':'existing_instance_or_not_selected'})})
    print('交接记录已保存：'+str(bindings/('handoff-'+project+'.json')))
    return receipt


def main(argv=None):
    console_utf8()
    parser = argparse.ArgumentParser(description='Video Factory 本机 n8n / 已有实例接入')
    parser.add_argument('--root', type=Path, default=Path.home() / '.video-factory/n8n')
    parser.add_argument('--dependency-timeout', type=int, default=1200, help='原生 npm 安装时限（秒，60–7200）；超时保留目录，可原地续接')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('welcome')
    owner=commands.add_parser('approval-setup',help='配置飞书指定负责人确认流程，默认零费用验收')
    for name in ('url','project','base','table','app','tenant','billing-owner','billing-account','configuration'):
        owner.add_argument('--'+name,required=True)
    owner.add_argument('--paid-workflows',action='store_true',help='导入有模型节点的关闭流程；不会激活')
    for name in ('approval-enable','approval-disable','approval-promote','approval-cancel'):
        command=commands.add_parser(name)
        command.add_argument('--url',required=True)
        command.add_argument('--project',required=True)
        if name=='approval-cancel':command.add_argument('--expect-plan',required=True)
    for name in ('upgrade-plan','upgrade-apply'):
        command=commands.add_parser(name,help='预览或显式升级已记录的关闭工作流，保留凭据和账本')
        command.add_argument('--url',required=True)
        command.add_argument('--project',required=True)
        if name=='upgrade-apply':command.add_argument('--expect-plan',required=True)
    install = commands.add_parser('install')
    install.add_argument('--mode', choices=['node', 'docker'], required=True)
    install.add_argument('--port', type=int, default=5678)
    install.add_argument('--managed-node', action='store_true', help='使用安装目录内校验过的 Node.js，不修改系统版本')
    for name in ('start', 'stop'):
        commands.add_parser(name)
    check = commands.add_parser('check')
    check.add_argument('--url', required=True)
    bind = commands.add_parser('connect')
    bind.add_argument('--url', required=True)
    bind.add_argument('--project', required=True)
    bind.add_argument('--base', required=True)
    bind.add_argument('--table', required=True)
    bind.add_argument('--flow', choices=['probe','schema','script','video-submit','video-poll','recover'], default='probe')
    bind.add_argument('--experimental-manual', action='store_true', help='允许导入人工逐条测试工作流；不表示支持并发/自动生产')
    export = commands.add_parser('export')
    export.add_argument('--base', required=True)
    export.add_argument('--table', required=True)
    export.add_argument('--flow', choices=['probe','schema','script','video-submit','video-poll'], required=True)
    export.add_argument('--out', type=Path, required=True)
    export.add_argument('--experimental-manual',action='store_true')
    export.add_argument('--ledger-table',help='手动创建的 n8n 执行锁数据表 ID')
    export.add_argument('--ledger-row',type=int,help='该数据表中项目执行锁行 ID')
    args = parser.parse_args(argv)
    try:
        if args.command == 'welcome':
            value = welcome(args.root,args.dependency_timeout)
        elif args.command == 'approval-setup':
            from portable_approval import configure_approval
            api=API(args.url,secret('n8n API Key（隐藏输入）: '));api.preflight()
            value=configure_approval(api,args.root,args.project,args.base,args.table,
                {'app':args.app,'tenant':args.tenant,'billing_owner':args.billing_owner,
                 'billing_account':args.billing_account,'configuration':args.configuration},
                verification_only=not args.paid_workflows)
        elif args.command in ('approval-enable','approval-disable','approval-promote','approval-cancel'):
            from portable_approval import set_approval_active,promote_approval,cancel_plan
            api=API(args.url,secret('n8n API Key（隐藏输入）: '));api.preflight()
            if args.command=='approval-promote':value=promote_approval(api,args.root,args.project)
            elif args.command=='approval-cancel':value=cancel_plan(api,args.root,args.project,args.expect_plan)
            else:
                if args.command=='approval-enable' and input('启用本项目计划与确认入口？负责人确认有效计划后才执行，输入 yes: ').strip()!='yes':
                    raise Fault('ACTIVATION_NOT_CONFIRMED')
                value=set_approval_active(api,args.root,args.project,args.command=='approval-enable')
        elif args.command in ('upgrade-plan','upgrade-apply'):
            from portable_upgrade import preview,apply
            api=API(args.url,secret('n8n API Key（隐藏输入）: '));api.preflight()
            value=(preview(api,args.root,args.project) if args.command=='upgrade-plan' else
                   apply(api,args.root,args.project,args.expect_plan))
        elif args.command == 'export':
            from n8n_workflows import probe_workflow, schema_workflow, production_workflow, guard_workflow
            if args.flow not in ('probe','schema') and not args.experimental_manual:
                raise Fault('MANUAL_TEST_OPT_IN_REQUIRED_NO_AUTOMATIC_PAID_EXECUTION')
            if args.flow in ('script','video-submit') and (not args.ledger_table or not args.ledger_row or args.ledger_row<1):
                raise Fault('EXPORT_REQUIRES_EXISTING_N8N_LEDGER_TABLE_AND_ROW')
            template = (probe_workflow(args.base,args.table) if args.flow=='probe' else
                        schema_workflow(args.base,args.table) if args.flow=='schema' else
                        production_workflow(args.base,args.table,args.flow))
            if args.flow in ('script','video-submit'):
                template=guard_workflow(template,{'table':identifier(args.ledger_table),'row':args.ledger_row})
            if args.out.exists(): raise Fault('EXPORT_EXISTS_CHOOSE_NEW_PATH')
            private_write(args.out,template)
            value={'exported':str(args.out),'credentials_included':False,'active':False,'production_ready':False}
        elif args.command == 'install':
            value = install_local(args.root, args.mode, args.port,args.managed_node,args.dependency_timeout)
        elif args.command in ('start', 'stop'):
            value = local_control(args.root, args.command)
        else:
            api = API(args.url, secret('n8n API Key（隐藏输入）: '))
            if args.command == 'check':
                value = api.preflight()
            else:
                from n8n_workflows import probe_workflow, schema_workflow, production_workflow, guard_workflow, recovery_workflow
                if args.flow not in ('probe','schema','recover') and not args.experimental_manual:
                    raise Fault('MANUAL_TEST_OPT_IN_REQUIRED_NO_AUTOMATIC_PAID_EXECUTION')
                app_id = input('飞书 App ID: ').strip()
                identifier(app_id)
                role='script' if args.flow=='script' else 'video' if args.flow in ('video-submit','video-poll') else None
                pool=project_credentials(api,args.root,args.project,app_id,['feishu']+([role] if role else []))
                credentials={'httpCustomAuth':pool['feishu']}
                if role: credentials['httpHeaderAuth']=pool[role]
                template = (probe_workflow(args.base,args.table) if args.flow=='probe' else
                            schema_workflow(args.base,args.table) if args.flow=='schema' else
                            recovery_workflow(args.base,args.table,ensure_ledger(api,args.root,args.project,args.base,args.table)) if args.flow=='recover' else
                            production_workflow(args.base,args.table,args.flow))
                if args.flow in ('script','video-submit'):
                    template=guard_workflow(template,ensure_ledger(api,args.root,args.project,args.base,args.table))
                value = provision(api, args.root, args.project + '-' + args.flow,template,credentials)
                value['next'] = '在 n8n 查看并逐条运行测试；未激活定时执行。人工审核在飞书 Base。'
                value['production_ready'] = False
        print(json.dumps(value, ensure_ascii=False))
        return 0
    except Fault as error:
        result={'error': str(error)}
        if str(error).startswith('DEPENDENCY_INSTALL_TIMEOUT'):
            result['next']='安装已停止，原目录与下载缓存保留。检查磁盘和网络后，用同一目录、模式和端口重试；慢盘可在子命令前设置 --dependency-timeout 2400。不要删除数据或重复启动另一份安装。'
        print(json.dumps(result, ensure_ascii=False))
        return 2
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        print(json.dumps({'error': 'SETUP_FAILED_NO_SECRETS_LOGGED'}))
        return 2


if __name__ == '__main__':
    # Keep Chinese prompts/JSON usable when an Agent pipes Windows output.
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'): stream.reconfigure(encoding='utf-8')
    raise SystemExit(main())
