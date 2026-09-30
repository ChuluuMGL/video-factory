"""Explicit customer-host execution of a completed Setup, separately from planning.

A review digest binds the local machine, destination, release, ports and plan.
It does not prove that a DNS name belongs to this machine: the operator runs this
on the intended customer host (normally over an independently trusted SSH login).
"""
from pathlib import Path
import json
import os
import re
import socket
from types import SimpleNamespace

from .onboarding import SessionStore, SetupError
from .runtime_cli import secret_input
from .runtime_store import RuntimeFault, canonical, fingerprint, private_directory
from .setup_project import session_plan
from .stack import Stack, admin_host, local_engine, images, digest, read_stack_config
from .stack_cli import install_stack


def register_setup_deploy(commands):
    p = commands.add_parser('setup-deploy', help='review/apply Setup on the intended customer Docker host')
    p.add_argument('action', choices=('plan', 'apply', 'status'))
    p.add_argument('--session', type=Path, required=True)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--host', required=True, help='explicit Setup host label; run on that host via trusted SSH')
    p.add_argument('--wheelhouse', type=Path)
    p.add_argument('--expect-plan', help='execution_sha256 returned by setup-deploy plan')
    p.add_argument('--password-file', type=Path)
    p.add_argument('--runtime-port', type=int)
    p.add_argument('--n8n-port', type=int)


def release_manifest(wheelhouse):
    if wheelhouse is None or not wheelhouse.is_absolute() or wheelhouse.resolve() != wheelhouse or not wheelhouse.is_dir():
        raise RuntimeFault('SETUP_RELEASE_DIRECTORY_REQUIRED')
    paths = sorted(wheelhouse.glob('*.whl'))
    if not 1 <= len(paths) <= 30:
        raise RuntimeFault('SETUP_RELEASE_INVALID')
    total = 0
    manifest = {}
    for path in paths:
        if path.is_symlink() or not path.is_file() or not re.fullmatch(r'[A-Za-z0-9_.+-]+\.whl', path.name):
            raise RuntimeFault('SETUP_RELEASE_INVALID')
        total += path.stat().st_size
        if total > 100 * 1024 * 1024:
            raise RuntimeFault('SETUP_RELEASE_TOO_LARGE')
        manifest[path.name] = digest(path.read_bytes())
    if sum(name.startswith('video_factory_core-') for name in manifest) != 1:
        raise RuntimeFault('ONE_PRODUCT_WHEEL_REQUIRED')
    return manifest


def local_machine():
    value = Path('/etc/machine-id').read_text().strip()
    if not re.fullmatch(r'[a-fA-F0-9]{32}', value) or int(value, 16) == 0:
        raise RuntimeFault('LOCAL_MACHINE_ID_UNAVAILABLE')
    return {'hostname': socket.gethostname(), 'machine_id_sha256': digest(value.encode())}


def execution_plan(args, session):
    plan = session_plan(session)
    if args.host != plan['configuration']['deployment']['host']:
        raise RuntimeFault('SETUP_HOST_LABEL_MISMATCH')
    root = args.root
    if not root.is_absolute() or root.resolve() != root or not root.parent.is_dir():
        raise RuntimeFault('SETUP_DESTINATION_INVALID')
    parent = root.parent.stat()
    if parent.st_uid != os.getuid() or parent.st_mode & 0o022:
        raise RuntimeFault('SETUP_DESTINATION_PARENT_UNSAFE')
    if args.session.is_relative_to(root) or (args.wheelhouse and args.wheelhouse.is_relative_to(root)):
        raise RuntimeFault('SETUP_INPUTS_MUST_BE_OUTSIDE_DESTINATION')
    if root.exists():
        private_directory(root)
    manifest = release_manifest(args.wheelhouse)
    runtime_port, n8n_port = args.runtime_port or 8787, args.n8n_port or 5678
    deployment_images=images()
    if (root / 'stack.json').exists():
        current = read_stack_config(root)
        deployment_images=current['images']
        if current['deployment'] != plan['configuration']['deployment']['id'] or current['wheels'] != manifest:
            raise RuntimeFault('SETUP_EXISTING_TARGET_OR_RELEASE_MISMATCH')
        runtime_port = args.runtime_port or current['runtime_port']
        n8n_port = args.n8n_port or current['n8n_port']
        if (runtime_port, n8n_port) != (current['runtime_port'], current['n8n_port']):
            raise RuntimeFault('SETUP_EXISTING_PORTS_MISMATCH')
    elif root.exists() and any(root.iterdir()):
        raise RuntimeFault('SETUP_DESTINATION_NOT_EMPTY')
    if (runtime_port == n8n_port or any(type(p) is not int or not 1024 <= p <= 65535 for p in (runtime_port, n8n_port))):
        raise RuntimeFault('STACK_PORT_INVALID')
    target = {'schema': 1, 'setup_plan_sha256': plan['plan_sha256'],
              'declared_host': args.host, 'local_machine': local_machine(), 'root': str(root),
              'deployment': plan['configuration']['deployment']['id'], 'project': plan['configuration']['project']['id'],
              'wheels': manifest, 'images': deployment_images, 'runtime_port': runtime_port, 'n8n_port': n8n_port}
    return {'execution_sha256': fingerprint(target), 'target': target,
            'execution_mode': 'on_customer_host', 'host_identity': 'local_machine_bound_dns_not_verified',
            'actions': ['install_or_resume_stack', 'authenticate_local_admin', 'import_disabled_project_and_skus', 'read_back'],
            'sku_count': len(plan['configuration']['project']['products']),
            'paid_execution_enabled': False, 'feishu_connection': 'not_run'}


def project_operation(stack, session, password, action):
    response = json.loads(stack.compose('exec', '-T', 'runtime', 'python', '-m', 'video_factory.setup_project_entry',
                        data=canonical({'action': action, 'session': session, 'password': password}).encode()))
    if 'error' in response:
        # Codes only, never a remote exception/body with arbitrary data.
        code = response['error']
        raise RuntimeFault(code if isinstance(code, str) and re.fullmatch(r'[A-Z0-9_]+', code) else 'SETUP_PROJECT_OPERATION_FAILED')
    return response


def apply_setup(args, session, reviewed, password):
    options = SimpleNamespace(root=args.root, deployment=reviewed['target']['deployment'],
                              wheelhouse=args.wheelhouse, password_file=None,
                              runtime_port=reviewed['target']['runtime_port'], n8n_port=reviewed['target']['n8n_port'])
    infrastructure = install_stack(options, password=password, expected_wheels=reviewed['target']['wheels'])
    if not infrastructure['infrastructure_ready']:
        raise RuntimeFault('SETUP_RUNTIME_NOT_READY')
    stack = Stack(args.root)
    with stack.lock():
        project = project_operation(stack, session, password, 'apply')
        readback = project_operation(stack, session, password, 'status')
    if project['plan_sha256'] != readback['plan_sha256']:
        raise RuntimeFault('SETUP_PROJECT_READBACK_MISMATCH')
    return {'status': 'infrastructure_and_project_draft_ready',
              'execution_sha256': reviewed['execution_sha256'], 'infrastructure': infrastructure,
              'project': project, 'business_ready': False}


def run_setup_deploy(args):
    try:
        admin_host()
        local_engine()
        store = SessionStore(args.session)
        # Freeze the session for the entire operation, not just plan creation.
        with store.locked():
            session = store._read()
            if args.action == 'status':
                plan = session_plan(session)
                if args.host != plan['configuration']['deployment']['host']:
                    raise RuntimeFault('SETUP_HOST_LABEL_MISMATCH')
                stack = Stack(args.root)
                if stack.config['deployment'] != plan['configuration']['deployment']['id']:
                    raise RuntimeFault('SETUP_DEPLOYMENT_MISMATCH')
                result = {'infrastructure': stack.status(),
                          'project': project_operation(stack, session, secret_input(args.password_file, '产品管理员密码: '), 'status')}
            else:
                reviewed = execution_plan(args, session)
                if args.action == 'plan':
                    result = reviewed
                else:
                    if args.expect_plan != reviewed['execution_sha256']:
                        raise RuntimeFault('SETUP_EXECUTION_PLAN_CHANGED_REVIEW_REQUIRED')
                    password = secret_input(args.password_file, '产品管理员密码（新安装设置，已有安装验证）: ')
                    result = apply_setup(args, session, reviewed, password)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (RuntimeFault, SetupError) as error:
        print(json.dumps({'error': str(error), 'business_ready': False, 'read_status_before_retry': True})); return 2
    except (OSError, ValueError, TypeError, KeyError):
        print(json.dumps({'error': 'SETUP_DEPLOY_OPERATION_FAILED', 'business_ready': False, 'read_status_before_retry': True})); return 2
