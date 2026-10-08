"""Private per-project execution companion for approved n8n jobs.

There is no employee HTTP service, TLS certificate, or published port here.
"""
import hashlib
import json
from pathlib import Path

from .runtime_store import RuntimeFault, fingerprint, identifier, private_file
from .stack import Stack, compose_document, local_engine, run, write_json


def register(commands):
    parser = commands.add_parser('runner', help='private n8n execution companion; no web workspace')
    parser.add_argument('action', choices=['plan', 'apply', 'status', 'stop'])
    parser.add_argument('--stack-root', type=Path, required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--expect-plan')


def directory(stack, project):
    identifier(project)
    return stack.root / 'data/runners' / project


def plan(stack, project):
    identifier(project)
    return {'schema': 1, 'project': project, 'stack_instance': stack.config['instance'],
            'runtime_image': stack.config['runtime_image']}


def document(stack, value):
    project = value['project']
    identifier(project)
    base = compose_document(stack.config)
    runtime = base['services']['runtime']
    relay = base['services']['egress']
    prefix = 'vf-' + stack.config['deployment'] + '-' + stack.config['instance']
    common = {'restart': 'unless-stopped', 'read_only': True, 'cap_drop': ['ALL'], 'init': True,
              'security_opt': ['no-new-privileges:true'], 'pull_policy': 'never',
              'logging': {'driver': 'json-file', 'options': {'max-size': '10m', 'max-file': '3'}},
              'tmpfs': ['/tmp:rw,noexec,nosuid,size=64m']}
    mount = lambda part: str(stack.root / part)
    executor = {**common, 'image': stack.config['runtime_image'], 'user': '10001:10001',
                'environment': runtime['environment'] | {'VF_WORKER_EGRESS': '1'},
                'secrets': ['runtime_dsn', 'runtime_master'],
                'entrypoint': ['python', '-m', 'video_factory.dispatch'], 'command': ['--project', project],
                'networks': {'ledger': {'aliases': ['vf-executor-' + hashlib.sha256(project.encode()).hexdigest()[:12]]},
                             'review': {}},
                'volumes': [mount('data/runtime') + ':/state', mount('data/media') + ':/media',
                            mount('data/worker') + ':/work:ro'],
                'healthcheck': {'test': ['CMD', 'python', '-c',
                                         "from urllib.request import urlopen; assert urlopen('http://127.0.0.1:8793/healthz',timeout=3).status==200"],
                                'interval': '5s', 'timeout': '5s', 'retries': 12}}
    egress = {**common, 'image': relay['image'], 'user': '10001:10001',
              'networks': ['review', 'outbound'], 'entrypoint': ['python', '-m', 'video_factory.worker_egress'],
              'command': ['--persistent'], 'healthcheck': relay['healthcheck'],
              'pids_limit': 64, 'mem_limit': '128m'}
    events = {**executor, 'entrypoint': ['python','-m','video_factory.feishu_native_events'],
              'environment': executor['environment'] | {'HTTPS_PROXY':'http://egress:8443',
                                                          'https_proxy':'http://egress:8443'},
              'depends_on': {'egress': {'condition':'service_healthy'}}}
    events.pop('healthcheck')
    events['networks'] = ['ledger','review']
    return {'name': prefix + '-runner-' + hashlib.sha256(project.encode()).hexdigest()[:12],
            'services': {'executor': executor, 'egress': egress, 'events': events},
            'networks': {'ledger': {'external': True, 'name': prefix + '_private'},
                         'review': {'internal': True}, 'outbound': {}},
            'secrets': {name: {'file': mount('secrets/' + name)} for name in ('runtime_dsn', 'runtime_master')}}


def compose(stack, project, *args, data=None):
    root = directory(stack, project)
    private_file(root / 'runner.json')
    value = json.loads((root / 'runner.json').read_text())
    if value != plan(stack, project):
        raise RuntimeFault('RUNNER_REAPPLY_AFTER_STACK_CHANGE')
    if json.loads((root / 'compose.json').read_text()) != document(stack, value):
        raise RuntimeFault('RUNNER_GENERATED_FILES_CHANGED')
    local_engine()
    return run(['docker', 'compose', '--project-directory', str(root), '-f', str(root / 'compose.json'), *args],
               timeout=180, data=data)


def status(stack, project):
    root = directory(stack, project)
    if not (root / 'runner.json').exists():
        return {'status': 'not_configured', 'project': project}
    raw = compose(stack, project, 'ps', '--all', '--format', 'json').decode()
    rows = json.loads(raw) if raw.lstrip().startswith('[') else [json.loads(line) for line in raw.splitlines() if line]
    ready = (len(rows) == 3 and {row['Service'] for row in rows} == {'executor', 'egress', 'events'}
             and all(row['State'] == 'running' and row.get('Health') in ('','healthy') for row in rows))
    return {'status': 'running' if ready else 'incomplete', 'project': project,
            'components': [{key: row.get(key) for key in ('Service', 'State', 'Health')} for row in rows],
            'published_ports': [], 'human_acceptance': 'not_run'}


def apply(stack, value):
    project = value['project']
    if value != plan(stack, project):
        raise RuntimeFault('RUNNER_PLAN_CHANGED')
    if not stack.status()['infrastructure_ready']:
        raise RuntimeFault('RUNNER_HEALTHY_STACK_REQUIRED')
    from .workspace import directory as workspace_directory
    if (workspace_directory(stack, project) / 'workspace.json').exists():
        raise RuntimeFault('RUNNER_LEGACY_WORKSPACE_CONFLICT')
    root = directory(stack, project)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.resolve() != root:
        raise RuntimeFault('RUNNER_DIRECTORY_UNSAFE')
    if (root / 'runner.json').exists():
        previous = json.loads((root / 'runner.json').read_text())
        if previous == value:
            compose(stack, project, 'down', '--timeout', '15')
        elif (not isinstance(previous, dict) or set(previous) != set(value)
              or previous.get('schema') != 1 or previous.get('project') != project
              or previous.get('stack_instance') == stack.config['instance']
              or not (root / 'compose.json').is_file()):
            raise RuntimeFault('RUNNER_REAPPLY_AFTER_STACK_CHANGE')
        # A restored stack has a new instance and image. Its old runner was
        # stopped by the cold backup; never execute the copied compose file,
        # whose mounts still point at the source root.
    write_json(root / 'runner.json', value)
    write_json(root / 'compose.json', document(stack, value))
    compose(stack, project, 'up', '-d', '--pull', 'never', '--wait', '--wait-timeout', '90')
    return status(stack, project)


def stop_all(stack):
    base = stack.root / 'data/runners'
    if base.exists():
        for path in sorted(base.iterdir()):
            if path.is_dir() and (path / 'runner.json').exists():
                value = json.loads((path / 'runner.json').read_text())
                if value['stack_instance'] == stack.config['instance']:
                    compose(stack, path.name, 'down', '--timeout', '15')


def cli(args):
    try:
        stack = Stack(args.stack_root)
        with stack.lock():
            if args.action in ('plan', 'apply'):
                value = plan(stack, args.project)
                result = {'plan': value, 'plan_sha256': fingerprint(value), 'business_ready': False}
                if args.action == 'apply':
                    if args.expect_plan != result['plan_sha256']:
                        raise RuntimeFault('RUNNER_REVIEWED_PLAN_REQUIRED')
                    result = apply(stack, value)
            elif args.action == 'stop':
                compose(stack, args.project, 'down', '--timeout', '15')
                result = {'status': 'stopped', 'project': args.project}
            else:
                result = status(stack, args.project)
        print(json.dumps(result)); return 0
    except Exception as error:
        code = str(error) if isinstance(error, RuntimeFault) else 'RUNNER_OPERATION_FAILED'
        print(json.dumps({'error': code, 'business_ready': False})); return 2
