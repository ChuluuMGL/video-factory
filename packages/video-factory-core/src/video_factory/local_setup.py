"""Owner-only local business configuration; no containers, model calls or grants.

A non-secret plan is immutable for a project. Configuration and encrypted keys
commit together. Binding is a separate read-only Feishu gate and can be resumed.
The fake trial ledger is never adopted. No execution capability is issued here.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

from cryptography.fernet import Fernet

from .feishu_bridge import FeishuBridge, configuration, meta, save
from .feishu_client import resource
from .runtime_cli import secret_input
from .runtime_store import (RuntimeFault, RuntimeStore, canonical, exclusive_write,
                            fingerprint, identifier, private_directory, private_file)
from .scheduler import runtime_lock
from .script_jobs import profile
from .script_provider import MODEL
from .setup_project import DISABLED_CONFIGURATION
from .video_jobs import configure

MARKER = 'local:business-setup:v1'


def validate(plan):
    fields = {'deployment', 'project', 'billing_owner', 'products', 'app_id', 'binding', 'script_model', 'video'}
    if not isinstance(plan, dict) or set(plan) != fields:
        raise RuntimeFault('LOCAL_SETUP_PLAN_FIELDS_INVALID')
    for name in ('deployment', 'project', 'billing_owner'): identifier(plan[name])
    resource(plan['app_id'], 'cli_'); configuration(plan['binding'])
    if plan['script_model'] != MODEL:
        raise RuntimeFault('LOCAL_SETUP_SCRIPT_MODEL_UNSUPPORTED')
    products = plan['products']
    if not isinstance(products, list) or not 1 <= len(products) <= 100:
        raise RuntimeFault('LOCAL_SETUP_PRODUCTS_REQUIRED')
    seen = set()
    for product in products:
        if not isinstance(product, dict) or set(product) != {'sku_id', 'name', 'truth_source'}:
            raise RuntimeFault('LOCAL_SETUP_PRODUCT_FIELDS_INVALID')
        identifier(product['sku_id'])
        if product['sku_id'] in seen: raise RuntimeFault('LOCAL_SETUP_DUPLICATE_SKU')
        seen.add(product['sku_id'])
        for name in ('name', 'truth_source'):
            if not isinstance(product[name], str) or not product[name].strip() or len(product[name]) > 12000:
                raise RuntimeFault('LOCAL_SETUP_PRODUCT_TEXT_REQUIRED')
    video = plan['video']
    if video is not None:
        if (not isinstance(video, dict) or set(video) != {'model', 'region', 'assets'}
                or video['model'] != 'MiniMax-H3' or video['region'] not in ('cn', 'global')):
            raise RuntimeFault('LOCAL_SETUP_VIDEO_MODEL_UNSUPPORTED')
        if not isinstance(video['assets'], dict) or set(video['assets']) != seen:
            raise RuntimeFault('LOCAL_SETUP_ALL_SKU_ASSETS_REQUIRED')
    return fingerprint(plan)


class TransactionStore(RuntimeStore):
    """Reuse core validators with a single outer transaction, never partial writes."""
    def __init__(self, store, db): self.root, self.path, self.db = store.root, store.path, db

    @contextmanager
    def connect(self): yield self.db


def snapshots(db, project):
    row = db.execute('SELECT configuration,digest FROM projects WHERE id=?', (project,)).fetchone()
    names = ['setup:project:', 'setup:feishu-app:', 'script:profile:', 'video:profile:']
    return {'project': list(row) if row else None,
            'metadata': {name: meta(db, name + project) for name in names},
            'keys': [list(row) for row in db.execute('SELECT alias,revision FROM vault WHERE alias GLOB ? ORDER BY alias',
                                                    ('ls_' + fingerprint(project)[:24] + '_*',))]}


def provision(store, admin, plan, master_key, secrets):
    digest = validate(plan); project = plan['project']; receipt_key = 'local:setup:' + project
    with store.connect() as db:
        actor = store.authorize(db, admin, 'admin')
        if meta(db, 'local:fake-only:v1'): raise RuntimeFault('LOCAL_FAKE_PROFILE_NO_EXTERNAL_IO')
        if meta(db, MARKER) != {'deployment': plan['deployment']}:
            raise RuntimeFault('LOCAL_SETUP_RUNTIME_NOT_OWNED')
        if db.execute("SELECT value FROM meta WHERE key='deployment'").fetchone()[0] != plan['deployment']:
            raise RuntimeFault('LOCAL_SETUP_DEPLOYMENT_CHANGED')
        previous = meta(db, receipt_key)
        if previous:
            if previous['plan_sha256'] != digest or previous['snapshot'] != snapshots(db, project):
                raise RuntimeFault('LOCAL_SETUP_CHANGED_USE_NEW_PROJECT')
            for alias, revision in previous['snapshot']['keys']:
                TransactionStore(store, db).resolve_secret(alias, master_key)
            return {'reused': True, 'plan_sha256': digest}
        if db.execute('SELECT 1 FROM projects WHERE id=?', (project,)).fetchone():
            raise RuntimeFault('LOCAL_SETUP_EXISTING_PROJECT_NOT_OWNED')
        expected = {'app', 'script'} | ({'video'} if plan['video'] else set())
        if set(secrets) != expected: raise RuntimeFault('LOCAL_SETUP_KEYS_REQUIRED')
        for value in secrets.values():
            if not isinstance(value, str) or not 16 <= len(value) <= 4096 or any(c.isspace() for c in value):
                raise RuntimeFault('LOCAL_SETUP_RAW_KEY_REQUIRED')
        from .feishu_oauth import DeviceOAuth
        DeviceOAuth(plan['app_id'], secrets['app'])
        tx = TransactionStore(store, db)
        aliases = {name: 'ls_' + fingerprint(project)[:24] + '_' + name for name in expected}
        for name, alias in aliases.items():
            if db.execute('SELECT 1 FROM vault WHERE alias=?', (alias,)).fetchone():
                raise RuntimeFault('LOCAL_SETUP_SECRET_ALREADY_PRESENT')
            tx.put_secret(admin, alias, secrets[name], master_key)
        tx.put_project(admin, project, DISABLED_CONFIGURATION)
        save(db, 'setup:project:' + project, {'schema': 1, 'plan_sha256': digest,
             'runtime_configuration_digest': fingerprint(DISABLED_CONFIGURATION),
             'configuration': {'project': {'id': project, 'base_mode': 'bind', 'products': plan['products']}}})
        save(db, 'setup:feishu-app:' + project, {'app_id': plan['app_id'], 'credential_ref': 'secret:' + aliases['app']})
        profile(tx, admin, project, 'secret:' + aliases['script'], plan['billing_owner'])
        if plan['video']:
            configure(tx, admin, project, 'secret:' + aliases['video'], plan['billing_owner'],
                      plan['video']['region'], plan['video']['assets'], local_assets=True)
        save(db, receipt_key, {'plan_sha256': digest, 'snapshot': snapshots(db, project)})
        store.audit(db, actor, 'local_business_setup', project)
    return {'reused': False, 'plan_sha256': digest}


def bind(store, admin, plan, user_token, *, client_factory=None):
    validate(plan)
    with store.connect() as db:
        store.authorize(db, admin, 'admin')
        saved = meta(db, 'local:setup:' + plan['project'])
        if not saved or saved['plan_sha256'] != fingerprint(plan) or saved['snapshot'] != snapshots(db, plan['project']):
            raise RuntimeFault('LOCAL_SETUP_CHANGED_USE_NEW_PROJECT')
        old = meta(db, 'feishu:binding:' + plan['project'])
        if old is not None and old != plan['binding']: raise RuntimeFault('LOCAL_SETUP_BINDING_CHANGED')
    bridge = FeishuBridge(store, **({'client_factory': client_factory} if client_factory else {}))
    return bridge.bind(admin, plan['project'], plan['binding'], user_token, fingerprint(old) if old else None)


def interactive_plan(read=input):
    """Non-secret questions only. Agent mode supplies this same JSON contract."""
    ask = lambda label: read(label + ': ').strip()
    plan = {name: ask(label) for name, label in (
        ('deployment', '本机部署标识（英文、数字或下划线）'),
        ('project', '新项目标识'), ('billing_owner', '费用负责人标识'), ('app_id', '专用飞书 App ID'))}
    plan['products'] = []
    while True:
        plan['products'].append({name: ask(label) for name, label in (
            ('sku_id', 'SKU 标识'), ('name', '商品名称'), ('truth_source', '真实规格、卖点和禁用表述'))})
        if ask('继续添加 SKU？输入 yes，其余结束') != 'yes': break
    binding = {name: ask(label) for name, label in (
        ('tenant_key', '飞书组织 tenant_key'), ('base_token', '已有测试 Base app_token'),
        ('table_id', '任务表 table_id'))}
    binding['fields'] = {name: ask(label + '的 field_id') for name, label in (
        ('task', '任务编号'), ('sku_id', 'SKU'), ('script', '脚本'), ('source_revision', '来源版本'))}
    binding['submitters'] = ask('提交人的 open_id（多个用逗号分隔）').split(',')
    binding['reviewers'] = ask('审核人的 open_id（多个用逗号分隔）').split(',')
    plan['binding'] = binding
    chosen = ask('脚本模型：输入 ' + MODEL)
    plan['script_model'] = chosen
    chosen = ask('视频模型：输入 MiniMax-H3；输入 skip 暂不配置视频')
    if chosen == 'skip': plan['video'] = None
    elif chosen == 'MiniMax-H3':
        assets = {}
        region = ask('MiniMax 区域：cn 或 global')
        for product in plan['products']:
            path = Path(ask(product['sku_id'] + ' 的本机参考图片绝对路径'))
            if not path.is_absolute() or path.resolve() != path: raise RuntimeFault('H3_REFERENCE_PATH_INVALID')
            private_directory(path.parent)
            if not path.is_file() or path.stat().st_size > 30 * 1024 * 1024:
                raise RuntimeFault('H3_REFERENCE_FORMAT_OR_SIZE_INVALID')
            assets[product['sku_id']] = {'assets_root': str(path.parent), 'specification': {
                'duration': int(ask('视频秒数（4–15）')), 'references': [
                    {'path': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]}}
        plan['video'] = {'model': chosen, 'region': region, 'assets': assets}
    else: raise RuntimeFault('LOCAL_SETUP_VIDEO_MODEL_UNSUPPORTED')
    validate(plan)
    return plan


def authorize_feishu(store, plan, master_key, *, oauth_factory=None, write=print,
                     clock=time.monotonic, sleep=time.sleep):
    from .feishu_oauth import DeviceOAuth
    with store.connect() as db: app = meta(db, 'setup:feishu-app:' + plan['project'])
    oauth = (oauth_factory or DeviceOAuth)(app['app_id'], store.resolve_secret(app['credential_ref'][7:], master_key))
    grant = oauth.start()
    # Only official verification URL/user code are displayed, never device/token values.
    write('请本人打开飞书授权页：' + grant['verification_uri'])
    write('授权码：' + grant['user_code'])
    deadline = clock() + min(240, grant['expires_in']); interval = grant['interval']
    while clock() < deadline:
        sleep(min(interval, max(0, deadline - clock())))
        if clock() >= deadline: break
        response = oauth.poll(grant['device_code'])
        if response['status'] == 'authorized': return response['access_token']
        if response['status'] == 'slow_down': interval = min(interval + 5, 60)
    raise RuntimeFault('FEISHU_OAUTH_DENIED_OR_EXPIRED')


def register(commands):
    parser = commands.add_parser('local-setup', help='terminal/Agent setup of a separate local business project')
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True, help='private JSON; no passwords or keys')
    parser.add_argument('--interactive', action='store_true', help='create a private non-secret plan through terminal questions')
    parser.add_argument('--native-plan', action='store_true', help='read the proposed original-table changes')
    parser.add_argument('--enable-native', help='explicitly approve the exact native plan hash')
    parser.add_argument('--dedicated-app', action='store_true', help='confirm no other receiver uses this app')
    parser.add_argument('--check', action='store_true', help='validate plan offline; no writes')
    parser.add_argument('--bind', action='store_true', help='verify existing Feishu table via read-only API')
    for name in ('password', 'app-secret', 'script-key', 'video-key', 'user-token'):
        parser.add_argument('--' + name + '-file', type=Path)


def cli(args):
    admin = store = None
    try:
        if os.environ.get('VF_DATABASE_URL_FILE') or os.environ.get('VF_CONTAINER_MODE') == '1':
            raise RuntimeFault('LOCAL_SQLITE_ENVIRONMENT_REQUIRED')
        if not args.plan.is_absolute() or args.plan.resolve() != args.plan:
            raise RuntimeFault('LOCAL_SETUP_PRIVATE_PLAN_REQUIRED')
        if args.interactive and not args.plan.exists():
            if not sys.stdin.isatty() or not sys.stderr.isatty():
                raise RuntimeFault('PRIVATE_INTERACTIVE_TERMINAL_REQUIRED')
            private_directory(args.plan.parent)
            exclusive_write(args.plan, canonical(interactive_plan()).encode())
        private_file(args.plan)
        if args.plan.stat().st_size > 1024 * 1024: raise RuntimeFault('LOCAL_SETUP_PLAN_TOO_LARGE')
        plan = json.loads(args.plan.read_text()); digest = validate(plan)
        if args.check:
            print(canonical({'plan_sha256': digest, 'provider_requests': 0, 'writes': 0})); return 0
        root = args.root
        if not root.is_absolute() or root.resolve() != root:
            raise RuntimeFault('LOCAL_SETUP_PRIVATE_ROOT_REQUIRED')
        if not root.exists(): root.mkdir(mode=0o700)
        private_directory(root)
        with runtime_lock(root):
            password = secret_input(args.password_file, '本机业务管理员密码（至少 14 位，隐藏输入）: ')
            if not (root / 'runtime.sqlite3').exists():
                if set(p.name for p in root.iterdir()) != {'local-runtime.lock'}:
                    raise RuntimeFault('LOCAL_SETUP_FRESH_DIRECTORY_REQUIRED')
                if not args.password_file:
                    if password != secret_input(None, '再次输入管理员密码: '):
                        raise RuntimeFault('LOCAL_SETUP_PASSWORD_MISMATCH')
                store = RuntimeStore.install(root, plan['deployment'], password)
                with store.connect() as db: save(db, MARKER, {'deployment': plan['deployment']})
            else: store = RuntimeStore(root)
            admin = store.login('admin', password)['token']; del password
            with store.connect() as db:
                if meta(db, MARKER) != {'deployment': plan['deployment']}:
                    raise RuntimeFault('LOCAL_SETUP_RUNTIME_NOT_OWNED')
                existing = meta(db, 'local:setup:' + plan['project'])
                if existing and existing['plan_sha256'] != digest:
                    raise RuntimeFault('LOCAL_SETUP_CHANGED_USE_NEW_PROJECT')
            key_path = root / 'master.key'
            if not key_path.exists():
                with store.connect() as db:
                    if db.execute('SELECT 1 FROM vault LIMIT 1').fetchone():
                        raise RuntimeFault('LOCAL_SETUP_MASTER_KEY_MISSING_RESTORE_BACKUP')
                exclusive_write(key_path, Fernet.generate_key())
            key = secret_input(key_path, '').encode()
            values = {}
            if not existing:
                values['app'] = secret_input(args.app_secret_file, '飞书 App Secret（隐藏输入）: ')
                values['script'] = secret_input(args.script_key_file, 'DeepSeek 官方 Key（隐藏输入）: ')
                if plan['video']: values['video'] = secret_input(args.video_key_file, 'MiniMax 原始 Key（隐藏输入）: ')
            result = provision(store, admin, plan, key, values); values.clear()
            result.update({'project': plan['project'], 'configuration_saved': True, 'provider_requests': 0,
                           'paid_execution_enabled': False, 'feishu_binding_verified': False,
                           'native_review_enabled': False, 'next_step': 'bind_existing_feishu_table'})
            if args.bind:
                user_token = secret_input(args.user_token_file, '') if args.user_token_file else authorize_feishu(store, plan, key)
                bind(store, admin, plan, user_token)
                del user_token
                result['feishu_binding_verified'] = True
                result['next_step'] = 'local-feishu plan; review exact field/subscription plan before enable'
            if args.native_plan or args.enable_native:
                from .feishu_native_sync import NativeSync
                native = NativeSync(store, key)
                if args.enable_native:
                    if not args.dedicated_app: raise RuntimeFault('LOCAL_FEISHU_DEDICATED_APP_REQUIRED')
                    native.enable(admin, plan['project'], args.enable_native)
                    result['native_review_enabled'] = True
                    result['next_step'] = 'explicit execution capability and per-task approval required before dispatch'
                else: result['native_plan'] = native.prepare(admin, plan['project'])
            print(canonical(result)); return 0
    except RuntimeFault as error:
        code = str(error)
        print(canonical({'error': code if re.fullmatch('[A-Z0-9_]+', code) else 'LOCAL_SETUP_INCOMPLETE',
                         'automatic_retry': False, 'recovery': 'Keep the original root and plan. Correct the reported input or permission, then rerun. Never delete an existing ledger.'})); return 2
    except Exception:
        print(canonical({'error': 'LOCAL_SETUP_INCOMPLETE', 'automatic_retry': False,
                         'recovery': 'Preserve the root and plan; inspect private local configuration before resuming.'})); return 2
    finally:
        if store and admin:
            with store.connect() as db:
                db.execute('DELETE FROM sessions WHERE digest=?', (hashlib.sha256(admin.encode()).hexdigest(),))
