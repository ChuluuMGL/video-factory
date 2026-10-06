"""Customer-host terminal welcome, explicit install and Feishu handoff."""
from contextlib import contextmanager
import argparse
import getpass
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import sys
import tempfile
from types import SimpleNamespace

from .onboarding import SessionStore, SetupError
from .runtime_store import RuntimeFault, RuntimeStore, canonical, exclusive_write
from .setup_cli import interactive as setup_questions
from .setup_feishu import ConnectionSession, interactive as connection_questions, describe, source_plan
from .setup_deploy import execution_plan, apply_setup, project_operation
from .setup_admin import rpc
from .setup_connect import run_connect
from . import image_bundle
from .stack import Stack, admin_host, local_engine


def register(commands):
    p = commands.add_parser('setup-run', help='interactive customer-host installation, login and Feishu connection')
    p.add_argument('--session', type=Path, required=True)
    p.add_argument('--root', type=Path, required=True)
    image_bundle.add_arguments(p)
    p.add_argument('--wheelhouse', type=Path, required=True)
    p.add_argument('--from-session', type=Path)
    p.add_argument('--runtime-port', type=int)
    p.add_argument('--n8n-port', type=int)
    p.add_argument('--connection-port', type=int, default=8791)
    # Accepted for existing scripts; Setup no longer launches a review page.
    p.add_argument('--review-port', type=int, default=8790, help=argparse.SUPPRESS)
    p.add_argument('--seconds', type=int, default=360)
    p.add_argument('--browser-input', action='store_true', help='human input via loopback browser, never Agent chat')
    p.add_argument('--input-port', type=int, default=8792)


def choice(prompt, read, write):
    while True:
        answer = read(prompt+' [y/N，:quit 退出]> ').strip().lower()
        if answer in ('y', 'yes'): return True
        if answer == ':quit': raise EOFError
        if answer in ('', 'n', 'no'): return False
        write('请输入 y 或 n。')


@contextmanager
def private_inputs(stack, draft, token):
    parent = stack.root/'data/worker'
    info = parent.lstat()
    if (parent.resolve() != parent or not stat.S_ISDIR(info.st_mode) or info.st_uid != 10001
            or stat.S_IMODE(info.st_mode) & 0o077):
        raise RuntimeFault('SETUP_WORK_DIRECTORY_UNSAFE')
    temporary = Path(tempfile.mkdtemp(prefix='.setup-run-', dir=parent))
    try:
        # Only a bounded admin session and non-secret frozen draft cross this
        # read-only mount. Passwords and application secrets never touch it.
        for name, value in [('connection.json', canonical(draft)), ('admin.token', token)]:
            path = temporary/name
            exclusive_write(path, value.encode()); os.chown(path, 10001, 10001)
        os.chown(temporary, 10001, 10001)
        yield Path('/work')/temporary.name
    finally:
        shutil.rmtree(temporary)


def install(args, store, session, reviewed, password):
    with store.locked():
        latest = store._read()
        current = execution_plan(args, latest)
        if current['execution_sha256'] != reviewed['execution_sha256']:
            raise RuntimeFault('SETUP_EXECUTION_PLAN_CHANGED_REVIEW_REQUIRED')
        if (args.root/'stack.json').exists():
            stack = Stack(args.root)
            with stack.lock():
                if stack.status()['infrastructure_ready']:
                    result = project_operation(stack, latest, password, 'apply')
                    readback = project_operation(stack, latest, password, 'status')
                    if result['plan_sha256'] != readback['plan_sha256']: raise RuntimeFault('SETUP_PROJECT_READBACK_MISMATCH')
                    return readback
        return apply_setup(args, latest, current, password)['project']


def welcome(args, *, read=input, hidden=getpass.getpass, write=print, read_products=None):
    admin_host(); local_engine()
    if (not 1 <= args.seconds <= 360 or not 1024 <= args.connection_port <= 65535):
        raise RuntimeFault('SETUP_WINDOW_PORT_OR_DURATION_INVALID')
    write('欢迎使用 Video Factory 安装与接入向导')
    write('请在客户目标服务器运行。每个阶段先核对再执行；输入 :quit 可保存退出。')
    write('配置会自动保存；密码和应用密钥使用隐藏输入，不能交给 Agent 对话记录。')
    write('可新建测试/正式 Base，或连接已有 Base。新建需本人授权并确认后执行。')
    store = SessionStore(args.session)
    source = SessionStore(args.from_session).read() if args.from_session else None
    store.start(source)
    result = setup_questions(store, read=read, read_reference=hidden, write=write,
                            next_step='接下来在本向导核对安装计划，再决定是否执行。', existing_base_only=False, read_products=read_products)
    if result.get('interrupted') or result['status'] != 'plan_ready':
        return {'status': 'configuration_saved', 'business_ready': False}
    session = store.read()
    plan = source_plan(session)
    args.host = plan['configuration']['deployment']['host']
    reviewed = execution_plan(args, session)
    target = reviewed['target']
    if args.connection_port in (target['runtime_port'], target['n8n_port']):
        raise RuntimeFault('SETUP_WINDOW_PORT_CONFLICT')
    write('\n请核对安装计划：')
    write('声明服务器：'+target['declared_host']+'；当前主机：'+target['local_machine']['hostname'])
    write('本机标识摘要：'+target['local_machine']['machine_id_sha256'])
    write('安装目录：'+target['root']+'；项目：'+target['project']+'；SKU 数量：'+str(reviewed['sku_count']))
    write('产品端口：'+str(target['runtime_port'])+'；n8n 端口：'+str(target['n8n_port']))
    write('发行文件及校验值：'+canonical(target['wheels']))
    write('镜像来源：'+('已核验离线包 '+target['image_bundle_manifest_sha256'] if 'image_bundle_manifest_sha256' in target else '固定摘要在线镜像；已有离线部署续接会复用本地镜像'))
    write('计划校验值：'+reviewed['execution_sha256'])
    if not choice('确认在这台机器安装或继续', read, write):
        return {'status': 'installation_not_started', 'business_ready': False}
    fresh = not (args.root/'initialized.json').exists()
    password = hidden('设置产品管理员密码（至少 14 位）: ' if fresh else '产品管理员密码: ')
    RuntimeStore.validate_password(password)
    if fresh and hidden('再次输入管理员密码: ') != password:
        raise RuntimeFault('SETUP_PASSWORD_CONFIRMATION_MISMATCH')
    installed = install(args, store, session, reviewed, password)
    write('基础服务和项目配置已回读。下一步配置飞书工作区。')
    connection = ConnectionSession(args.session.with_name(args.session.name+'.feishu.json'))
    connection.start(session)
    result = connection_questions(connection, read=read, write=write, next_step='连接草稿已保存。接下来登录管理员，配置本项目的飞书应用。')
    if result.get('interrupted') or result['status'] != 'connection_draft_ready':
        return {'status': 'installed_connection_questions_saved', 'business_ready': False}
    draft = connection.snapshot()
    stack = Stack(args.root)
    with stack.lock(): opened = rpc(stack, {'action': 'open', 'session': session, 'password': password})
    password = None
    token = opened['token']
    try:
        profile = opened['profile']
        if profile:
            write('本项目已保存飞书应用：'+profile['app_id'])
        if not profile or not choice('沿用已保存的应用密钥', read, write):
            app_id = read('飞书应用 App ID（:quit 保存退出）> ').strip()
            if app_id == ':quit': return {'status': 'installed_credentials_pending', 'business_ready': False}
            from .feishu_oauth import DeviceOAuth
            secret = hidden('飞书应用 App Secret（隐藏输入）: ')
            DeviceOAuth(app_id, secret)
            write('将为项目 '+target['project']+' 保存应用 '+app_id+'，密钥加密保存在这台客户服务器。')
            if not choice('确认保存应用凭据', read, write):
                return {'status': 'installed_credentials_pending', 'business_ready': False}
            with stack.lock():
                saved = rpc(stack, {'action': 'configure', 'token': token, 'session': session, 'app_id': app_id,
                                    'app_secret': secret, 'expected_profile': opened['profile_sha256']})
            secret = None
            profile = saved['profile']
        with stack.lock(): status = rpc(stack, {'action': 'status', 'token': token, 'draft': draft})
        connected = status['binding_matches_draft'] and not status['requires_reconfirmation']
        def event(value):
            if value.get('status') == 'ready':
                write('请打开本次私有入口：'+value['url'])
                write('远程访问使用同端口 SSH 隧道；不要分享管理员授权链接。窗口最多 '+str(args.seconds)+' 秒。')
            if value.get('error'): write('入口未完成：'+value['error'])
        options = SimpleNamespace(stack_root=args.root, root=None, project=target['project'],
            app_id=profile['app_id'], app_secret_ref=profile['credential_ref'], app_secret_file=None, master_key_file=None,
            port=args.connection_port, seconds=args.seconds, answers=None, expect_revision=None, setup_session=None,
            interactive=False, user_token_file=None, expect_plan=None, container_network=False)
        if not connected:
            if not choice('现在打开飞书授权与连接确认页面', read, write):
                return {'status': 'installed_credentials_saved', 'business_ready': False}
            with private_inputs(stack, draft, token) as directory:
                options.session = directory/'connection.json'; options.token_file = directory/'admin.token'
                code = run_connect(options, emit=event)
                if code == 130: raise KeyboardInterrupt
                if code: raise RuntimeFault('SETUP_CONNECTION_WINDOW_INCOMPLETE')
            with stack.lock(): status = rpc(stack, {'action': 'status', 'token': token, 'draft': draft})
            connected = status['binding_matches_draft'] and not status['requires_reconfirmation']
        if not connected:
            write('尚未确认绑定。已保存配置与加密应用凭据；重新运行同一命令继续。')
            return {'status': 'connection_confirmation_pending', 'business_ready': False}
        write('飞书绑定已回读。请在项目 Base 核对资料与任务，再单独验收生成、审核和结果。')
        return {'status': 'connection_ready', 'project': installed['project'], 'business_ready': False}
    finally:
        # Revoke only the session created by this wizard; never other users.
        with stack.lock(): rpc(stack, {'action': 'close', 'token': token})


def run(args):
    previous = {}
    browser = None
    try:
        if not getattr(args, 'browser_input', False) and not (sys.stdin.isatty() and sys.stdout.isatty() and sys.stderr.isatty()):
            raise RuntimeFault('SETUP_RUN_REQUIRES_PRIVATE_TTY')
        def interrupted(*_): raise KeyboardInterrupt
        for signum in (signal.SIGTERM, signal.SIGHUP):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, interrupted)
        if getattr(args, 'browser_input', False):
            if args.input_port in (args.connection_port, args.runtime_port or 8787, args.n8n_port or 5678):
                raise RuntimeFault('SETUP_INPUT_PORT_CONFLICT')
            from .setup_browser import BrowserInput
            browser = BrowserInput(args.input_port)
            print(json.dumps({'status': 'awaiting_human_input', 'url': browser.url, 'access': 'same_port_ssh_tunnel', 'expires_in': 1800}), flush=True)
            from .onboarding import read_json
            import io
            result = welcome(args, read=browser.read, hidden=browser.hidden, write=browser.write,
                             read_products=lambda raw: read_json(io.StringIO(raw)))
        else:
            result = welcome(args)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (KeyboardInterrupt, EOFError):
        print(json.dumps({'status': 'interrupted', 'resume_same_command': True, 'business_ready': False})); return 130
    except (RuntimeFault, SetupError) as error:
        print(json.dumps({'error': str(error), 'resume_same_command': True, 'business_ready': False})); return 2
    except Exception:
        print(json.dumps({'error': 'SETUP_RUN_INCOMPLETE_READ_STATUS', 'business_ready': False})); return 2
    finally:
        if browser: browser.close()
        for signum, handler in previous.items(): signal.signal(signum, handler)
