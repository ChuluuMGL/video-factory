"""Owner-started, bounded local service and a credential-hiding Agent client.

No owner credential reaches the Agent interface. Same-OS account access remains
trusted; project API capabilities are not an OS sandbox for arbitrary shell code.
"""
from contextlib import contextmanager
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import signal
import sys
import tempfile
import threading

from . import automation
from .feishu_bridge import meta
from .feishu_client import FeishuClient
from .local_setup import authorize_feishu, provision, validate
from .local_runtime import configured_script_plans, serve
from .runtime_cli import secret_input
from .runtime_store import RuntimeFault, RuntimeStore, canonical, exclusive_write, fingerprint, private_directory, private_file

ROUTES = {
    'capabilities': ('capabilities', set()), 'status': ('status', set()),
    'tasks': ('tasks', {'after'}), 'task': ('task', {'task', 'revision'}),
    'catalog': ('script/catalog', set()), 'plans': ('script/plans', {'after'}),
    'script-content': ('script/content', {'plan_id', 'plan_sha256'}),
    'prepare-script': ('script/prepare-record', {'record', 'request_id', 'expected_revision'}),
    'submit-script': ('script/submit', {'plan_id', 'plan_sha256'}),
    'script-receipt': ('script/receipt', {'plan_id'}),
    'prepare-video': ('video/prepare', {'task', 'revision', 'request_id'}),
    'submit-video': ('video/submit', {'plan_id', 'plan_sha256'}),
    'video-receipt': ('video/receipt', {'plan_id'}),
}


def read_plan(path):
    if not path.is_absolute() or path.resolve() != path: raise RuntimeFault('LOCAL_SETUP_PRIVATE_PLAN_REQUIRED')
    private_file(path)
    if path.stat().st_size > 1024 * 1024: raise RuntimeFault('LOCAL_SETUP_PLAN_TOO_LARGE')
    plan = json.loads(path.read_text()); validate(plan)
    return plan


def start_plan(store, admin, plan, key, *, allow_plans=False, execute=False, hours=2, employee_config=None):
    if type(hours) is not int or not 1 <= hours <= 2: raise RuntimeFault('LOCAL_RUN_MAX_TWO_HOURS')
    with store.connect() as db:
        if not meta(db, 'local:setup:' + plan['project']): raise RuntimeFault('LOCAL_RUN_SETUP_REQUIRED')
    provision(store, admin, plan, key, {})
    project = plan['project']
    with store.connect() as db:
        binding = meta(db, 'feishu:binding:' + project)
        native = meta(db, 'native:config:' + project)
        if binding != plan['binding']: raise RuntimeFault('LOCAL_RUN_BINDING_REQUIRED')
        if execute and (not native or not native.get('enabled')): raise RuntimeFault('LOCAL_RUN_NATIVE_ENABLE_REQUIRED')
        tasks = [dict(row) for row in db.execute('SELECT id,revision,state,input_digest,configuration_digest,provider_id FROM tasks WHERE project=? ORDER BY id,revision', (project,))]
        # Bind queued jobs and owner approvals too, not just visible task state.
        jobs = []
        for row in db.execute('SELECT key,value FROM meta ORDER BY key'):
            if not row[0].startswith(('script:job:', 'worker:', 'agent:script:plan:', 'agent:video:plan:')): continue
            value = json.loads(row[1])
            if not isinstance(value, dict): continue
            owner = value.get('project') or value.get('plan', {}).get('project')
            if owner == project and row[0].startswith(('script:job:', 'worker:', 'agent:script:plan:', 'agent:video:plan:')):
                jobs.append((row[0], fingerprint(value)))
    employee = None
    if employee_config:
        from .employee_access import configuration
        employee = configuration(employee_config, plan, allow_plans=allow_plans)
    value = {'schema': 'vf-local-start-v1', 'project': project, 'setup_sha256': fingerprint(plan),
             'binding_sha256': fingerprint(binding), 'native_sha256': fingerprint(native),
             'agent_scope': 'agent_plan' if allow_plans else 'agent_read',
             'execute_approved': execute, 'hours': hours, 'tasks': tasks, 'jobs': jobs,
             'requires_per_task_approval': True, 'automatic_resubmit': False,
             'employee_access': {'configuration_sha256':fingerprint(employee), 'scope':'agent_plan' if employee['allow_plans'] else 'agent_read', 'expires_at':employee['expires_at'],
                                 'repair':employee.get('repair')} if employee else None}
    return {**value, 'plan_sha256': fingerprint(value)}


@contextmanager
def capabilities(store, admin, project, hours, allow_plans, execute):
    """Revoke only grants issued by this start, including partial failures."""
    keys = []
    try:
        agent = automation.issue(store, admin, project, hours, scope='agent_plan' if allow_plans else 'agent_read')
        keys.append(agent['key_id'])
        execution = automation.issue_execution(store, admin, project, hours) if execute else None
        if execution: keys.append(execution['key_id'])
        yield agent, execution
    finally:
        with store.connect() as db:
            for key in keys: db.execute('DELETE FROM meta WHERE key=?', ('automation:key:' + key,))
            store.audit(db, 'local-owner', 'local_run_capabilities_revoked', project)


def identity(store, plan, key, token_file):
    token = secret_input(token_file, '') if token_file else authorize_feishu(store, plan, key)
    who = FeishuClient(token).identity()
    if who['tenant_key'] != plan['binding']['tenant_key'] or who['open_id'] not in plan['binding']['submitters']:
        raise RuntimeFault('FEISHU_TENANT_OR_ROLE_DENIED')
    return token, who['open_id']


def run(store, admin, plan, key, args, *, emit, stop=None):
    expected = start_plan(store, admin, plan, key, allow_plans=args.allow_plans, execute=args.execute_approved, hours=args.hours, employee_config=getattr(args, 'employee_config', None))
    if args.expect_plan != expected['plan_sha256']: raise RuntimeFault('LOCAL_RUN_START_PLAN_CHANGED')
    if args.execute_approved and not args.dedicated_app: raise RuntimeFault('LOCAL_FEISHU_DEDICATED_APP_REQUIRED')
    delegated = identity(store, plan, key, args.user_token_file) if args.allow_plans else None
    stop = stop or threading.Event()
    employees = None
    if getattr(args, 'employee_config', None):
        from .employee_access import configuration, EmployeeAccess
        config = configuration(args.employee_config, plan, allow_plans=args.allow_plans)
        config['expires_at'] = min(config['expires_at'], __import__('time').time()+args.hours*3600)
        employees = EmployeeAccess(store, admin, plan, config, key)
    with capabilities(store, admin, plan['project'], args.hours, args.allow_plans, args.execute_approved) as (agent, execution), \
            tempfile.TemporaryDirectory(prefix='agent-session-', dir=store.root) as directory:
        session = Path(directory); session.chmod(0o700)
        exclusive_write(session / 'agent.token', agent['token'].encode())
        if execution: exclusive_write(session / 'execution.token', execution['token'].encode())
        if delegated: exclusive_write(session / 'user.token', delegated[0].encode())
        scripts = configured_script_plans(store, plan['project'], args.allow_plans,
                     session / 'user.token' if delegated else None, delegated[1] if delegated else None)
        media = store.root / 'media'
        if args.execute_approved:
            if not media.exists(): media.mkdir(mode=0o700)
            private_directory(media)
        def preflight():
            current = start_plan(store, admin, plan, key, allow_plans=args.allow_plans, execute=args.execute_approved, hours=args.hours, employee_config=getattr(args, 'employee_config', None))
            if current['plan_sha256'] != args.expect_plan: raise RuntimeFault('LOCAL_RUN_START_PLAN_CHANGED')
            if employees: employees.start()
            else:
                from .employee_access import revoke_grants
                with store.connect() as db: revoke_grants(db, plan['project'])
        def ready(value):
            connection = {'schema': 'vf-agent-connection-v1', 'project': plan['project'], 'port': value['port'],
                          'expires_at': agent['expires_at'], 'scope': agent['scope']}
            exclusive_write(session / 'connection.json', canonical(connection).encode())
            emit({**value, 'connection': str(session / 'connection.json'), 'expires_at': agent['expires_at'],
                  'stop': 'Ctrl-C', 'owner_approval_exposed': False,
                  'employee_origin': 'http://127.0.0.1:'+str(value['port']) if employees else None})
        timer = threading.Timer(args.hours * 3600, stop.set); timer.daemon = True; timer.start()
        try:
            serve(store, plan['project'], args.port, stop, execution_token=execution['token'] if execution else None,
                  master_key=key if execution else None, media_root=media if execution else None,
                  plans_enabled=args.allow_plans, script_plans=scripts, on_ready=ready, on_locked=preflight,
                  native_files=(session / 'execution.token', store.root / 'master.key') if execution else None, employee_access=employees)
        finally:
            timer.cancel(); stop.set()
            if employees: employees.close()
    emit({'status': 'stopped', 'session_grants_revoked': True, 'project': plan['project']})


def agent_call(connection, operation, body):
    if operation not in ROUTES: raise RuntimeFault('AGENT_ROUTE_NOT_AVAILABLE')
    private_directory(connection.parent); private_file(connection)
    if connection.name != 'connection.json' or connection.resolve() != connection or not connection.is_absolute():
        raise RuntimeFault('AGENT_CONNECTION_INVALID')
    if connection.stat().st_size > 4096: raise RuntimeFault('AGENT_CONNECTION_INVALID')
    config = json.loads(connection.read_text())
    if set(config) != {'schema','project','port','expires_at','scope'} or config['schema'] != 'vf-agent-connection-v1':
        raise RuntimeFault('AGENT_CONNECTION_INVALID')
    import time
    if type(config['port']) is not int or not 1 <= config['port'] <= 65535 or config['expires_at'] <= time.time():
        raise RuntimeFault('AGENT_CONNECTION_EXPIRED_OR_INVALID')
    route, fields = ROUTES[operation]
    if not isinstance(body, dict) or set(body) - fields: raise RuntimeFault('AGENT_FIELDS_INVALID')
    data = canonical({'project': config['project'], **body}).encode()
    if len(data) > 65536: raise RuntimeFault('AGENT_REQUEST_TOO_LARGE')
    token = secret_input(connection.parent / 'agent.token', '')
    client = http.client.HTTPConnection('127.0.0.1', config['port'], timeout=30)
    try:
        client.request('POST', '/v1/agent/' + route, body=data,
                       headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        response = client.getresponse(); raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024: raise RuntimeFault('AGENT_RESPONSE_TOO_LARGE')
        if 300 <= response.status < 400: raise RuntimeFault('AGENT_REDIRECT_DENIED')
        result = json.loads(raw)
        if response.status != 200:
            code = result.get('error') if isinstance(result, dict) else None
            raise RuntimeFault(code if isinstance(code,str) and re.fullmatch('[A-Z0-9_]+',code) else 'AGENT_REQUEST_REJECTED')
        return result
    finally: client.close()


def register(commands):
    p = commands.add_parser('local-run', help='inspect or start an owner-approved, bounded business service')
    p.add_argument('action', choices=('inspect','start','approve-script','approve-video','revoke-script','revoke-video','employee-status','employee-suspend','employee-resume'))
    p.add_argument('--root', type=Path, required=True); p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--password-file', type=Path); p.add_argument('--user-token-file', type=Path)
    p.add_argument('--allow-plans', action='store_true'); p.add_argument('--execute-approved', action='store_true')
    p.add_argument('--dedicated-app', action='store_true'); p.add_argument('--hours', type=int, default=2)
    p.add_argument('--port', type=int, default=8788); p.add_argument('--expect-plan')
    p.add_argument('--employee-config', type=Path)
    p.add_argument('--expected-employee-version', type=int); p.add_argument('--request-id')
    p.add_argument('--plan-id'); p.add_argument('--plan-sha256'); p.add_argument('--accept-provider-charges', action='store_true')
    p = commands.add_parser('agent', help='use one scoped local connection; never displays its credential')
    p.add_argument('operation', choices=tuple(ROUTES)); p.add_argument('--connection', type=Path, required=True)
    p.add_argument('--json', default='{}', help='non-secret request fields; - reads bounded JSON from stdin')


def cli(args):
    store = admin = None
    emit = lambda value: print(canonical(value), flush=True)
    try:
        if args.command == 'agent':
            raw = sys.stdin.read(65537) if args.json == '-' else args.json
            if len(raw.encode()) > 65536: raise RuntimeFault('AGENT_REQUEST_TOO_LARGE')
            emit(agent_call(args.connection, args.operation, json.loads(raw))); return 0
        if os.environ.get('VF_DATABASE_URL_FILE') or os.environ.get('VF_CONTAINER_MODE') == '1':
            raise RuntimeFault('LOCAL_SQLITE_ENVIRONMENT_REQUIRED')
        plan = read_plan(args.plan); store = RuntimeStore(args.root)
        admin = store.login('admin', secret_input(args.password_file, '本机管理员密码（隐藏输入）: '))['token']
        if args.action.startswith('employee-'):
            from .employee_access import manage
            emit(manage(store, admin, plan['project'], args.action.removeprefix('employee-'), expected_version=args.expected_employee_version, request_id=args.request_id)); return 0
        key = secret_input(store.root / 'master.key', '').encode()
        checked = start_plan(store, admin, plan, key, allow_plans=args.allow_plans, execute=args.execute_approved, hours=args.hours, employee_config=getattr(args, 'employee_config', None))
        if args.action == 'inspect': emit(checked); return 0
        if args.action != 'start':
            action, stage = args.action.split('-')
            if action == 'approve' and not args.accept_provider_charges: raise RuntimeFault('AGENT_EXPLICIT_PROVIDER_CHARGES_REQUIRED')
            if stage == 'script':
                from .agent_script_plans import ScriptPlans
                token, open_id = identity(store, plan, key, args.user_token_file) if action == 'approve' else (None, None)
                plans = ScriptPlans(store, plan['project'], user_token_provider=(lambda:token) if token else None, open_id=open_id)
            else:
                from .agent_plans import VideoPlans
                plans = VideoPlans(store, plan['project'])
            emit(plans.owner(admin, action, args.plan_id, args.plan_sha256)); return 0
        stop = threading.Event()
        previous = {s: signal.getsignal(s) for s in (signal.SIGINT,signal.SIGTERM)}
        try:
            for s in previous: signal.signal(s, lambda *_: stop.set())
            run(store, admin, plan, key, args, emit=emit, stop=stop)
        finally:
            for s, handler in previous.items(): signal.signal(s, handler)
        return 0
    except RuntimeFault as error:
        code = str(error); emit({'error': code if re.fullmatch('[A-Z0-9_]+', code) else 'LOCAL_RUN_FAILED', 'automatic_retry': False}); return 2
    except Exception:
        emit({'error': 'LOCAL_RUN_FAILED', 'automatic_retry': False}); return 2
    finally:
        if store and admin:
            with store.connect() as db: db.execute('DELETE FROM sessions WHERE digest=?', (hashlib.sha256(admin.encode()).hexdigest(),))
