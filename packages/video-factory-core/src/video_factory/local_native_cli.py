"""Owner-only opt-in configuration for a separate local business runtime."""
import json
import os
from pathlib import Path

from .feishu_bridge import meta, save
from .feishu_client import resource
from .feishu_native_sync import NativeSync
from .runtime_cli import secret_input
from .runtime_store import RuntimeFault, RuntimeStore, fingerprint, identifier


def register(commands):
    parser = commands.add_parser('local-feishu', help='configure original-table review for a local business runtime')
    parser.add_argument('action', choices=('configure-app', 'plan', 'enable', 'status'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--admin-token-file', type=Path, required=True)
    parser.add_argument('--master-key-file', type=Path, required=True)
    parser.add_argument('--app-id')
    parser.add_argument('--app-secret-file', type=Path)
    parser.add_argument('--dedicated-app', action='store_true',
                        help='confirm this app has no other event receiver')
    parser.add_argument('--expect-plan')


def configure_app(store, admin, project, app_id, secret, key):
    from .scheduler import runtime_lock
    with runtime_lock(store.root):
        return _configure_app(store, admin, project, app_id, secret, key)


def _configure_app(store, admin, project, app_id, secret, key):
    resource(app_id, 'cli_')
    alias = 'native_app_' + fingerprint(project)[:24]
    with store.connect() as db:
        store.authorize(db, admin, 'admin')
        if meta(db, 'setup:feishu-app:' + project) or meta(db, 'native:config:' + project):
            raise RuntimeFault('LOCAL_FEISHU_APP_ALREADY_CONFIGURED')
        if db.execute('SELECT 1 FROM vault WHERE alias=?', (alias,)).fetchone():
            raise RuntimeFault('LOCAL_FEISHU_APP_SECRET_ALREADY_PRESENT')
    store.put_secret(admin, alias, secret, key)
    with store.connect() as db:
        actor = store.authorize(db, admin, 'admin')
        if meta(db, 'setup:feishu-app:' + project):
            raise RuntimeFault('LOCAL_FEISHU_APP_ALREADY_CONFIGURED')
        save(db, 'setup:feishu-app:' + project,
             {'app_id': app_id, 'credential_ref': 'secret:' + alias})
        store.audit(db, actor, 'local_feishu_app_configured', project)
    return {'project': project, 'configured': True, 'model_calls': 0, 'feishu_writes': 0}


def cli(args):
    try:
        identifier(args.project)
        if os.environ.get('VF_DATABASE_URL_FILE') or os.environ.get('VF_CONTAINER_MODE') == '1':
            raise RuntimeFault('LOCAL_SQLITE_ENVIRONMENT_REQUIRED')
        store = RuntimeStore(args.root)
        admin = secret_input(args.admin_token_file, '')
        with store.connect() as db:
            store.authorize(db, admin, 'admin')
            if meta(db, 'local:fake-only:v1'):
                raise RuntimeFault('LOCAL_FAKE_PROFILE_NO_EXTERNAL_IO')
            if not db.execute('SELECT 1 FROM projects WHERE id=?', (args.project,)).fetchone():
                raise RuntimeFault('PROJECT_MISSING')
        key = secret_input(args.master_key_file, '')
        native = NativeSync(store, key)
        if args.action == 'configure-app':
            if not args.dedicated_app or not args.app_secret_file:
                raise RuntimeFault('LOCAL_FEISHU_DEDICATED_APP_REQUIRED')
            result = configure_app(store, admin, args.project, args.app_id,
                                   secret_input(args.app_secret_file, ''), key)
        elif args.action == 'enable':
            if not args.dedicated_app or not args.expect_plan:
                raise RuntimeFault('LOCAL_FEISHU_REVIEW_PLAN_REQUIRED')
            result = native.enable(admin, args.project, args.expect_plan)
        elif args.action == 'plan':
            result = native.prepare(admin, args.project)
        else:
            result = native.status(admin, args.project)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except RuntimeFault as error:
        print(json.dumps({'error': str(error), 'automatic_resubmit': False}))
        return 2
    except Exception:
        print(json.dumps({'error': 'LOCAL_FEISHU_OPERATION_INCOMPLETE_READ_STATUS', 'automatic_resubmit': False}))
        return 2
