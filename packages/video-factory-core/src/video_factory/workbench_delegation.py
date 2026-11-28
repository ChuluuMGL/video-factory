"""Issue and revoke project-scoped keys for the workbench service."""
import hashlib
import json
from pathlib import Path
import secrets
import time

from .feishu_bridge import meta, save
from .runtime_cli import secret_input
from .runtime_store import RuntimeFault, RuntimeStore, exclusive_write, identifier, private_directory
from .workbench_actions import PRINCIPAL, pattern

ROLES = ('repair', 'edit', 'review', 'cost', 'video_admin')


def delegation(store, admin, project, principal, roles=None, ttl_hours=24, output=None):
    identifier(project)
    if not pattern(principal, PRINCIPAL):
        raise RuntimeFault('WORKBENCH_PRINCIPAL_INVALID')
    if roles is not None:
        if not roles or set(roles) - set(ROLES) or not 1 <= ttl_hours <= 168:
            raise RuntimeFault('WORKBENCH_GRANT_INVALID')
        private_directory(output.parent)
    with store.connect() as db:
        actor = store.authorize(db, admin, 'admin')
        if not db.execute('SELECT 1 FROM projects WHERE id=?', (project,)).fetchone():
            raise RuntimeFault('PROJECT_MISSING')
        if meta(db, 'local:fake-only:v1'):
            raise RuntimeFault('LOCAL_FAKE_PROFILE_NO_PAID_OR_OWNER_PLAN_ROUTE')
        policy_key = 'workbench:delegation:' + project
        policy = meta(db, policy_key) or {}
        for row in db.execute("SELECT key,value FROM meta WHERE key LIKE 'automation:key:%'").fetchall():
            grant = json.loads(row[1])
            if grant.get('project') == project and grant.get('workbench_principal') == principal:
                db.execute('DELETE FROM meta WHERE key=?', (row[0],))
        if roles is None:
            policy.pop(principal, None)
            save(db, policy_key, policy)
            store.audit(db, actor, 'workbench_grant_revoked', project)
            return {'revoked': True, 'project': project}
        raw = secrets.token_urlsafe(32)
        key = hashlib.sha256(raw.encode()).hexdigest()
        roles = sorted(set(roles))
        expires = time.time() + ttl_hours * 3600
        save(db, 'automation:key:' + key, {'scope': 'agent_workbench', 'project': project,
                                        'expires_at': expires, 'workbench_principal': principal,
                                        'workbench_roles': roles})
        save(db, policy_key, {**policy, principal: roles})
        store.audit(db, actor, 'workbench_grant_issued', project)
        exclusive_write(output, raw.encode())
        return {'project': project, 'key_id': key, 'roles': roles, 'expires_at': expires}


def register(commands):
    parser = commands.add_parser('workbench-delegate', help='manage project-scoped workbench grants')
    actions = parser.add_subparsers(dest='action', required=True)
    for action in ('issue', 'revoke'):
        command = actions.add_parser(action)
        command.add_argument('--root', required=True, type=Path)
        command.add_argument('--project', required=True)
        command.add_argument('--principal', required=True)
        command.add_argument('--admin-token-file', required=True, type=Path)
        if action == 'issue':
            command.add_argument('--role', action='append', choices=ROLES, required=True)
            command.add_argument('--ttl-hours', type=int, default=24)
            command.add_argument('--output', required=True, type=Path)


def cli(args):
    try:
        result = delegation(RuntimeStore(args.root), secret_input(args.admin_token_file, ''),
                            args.project, args.principal, getattr(args, 'role', None),
                            getattr(args, 'ttl_hours', 24), getattr(args, 'output', None))
    except (RuntimeFault, OSError) as error:
        print(json.dumps({'error': str(error) if isinstance(error, RuntimeFault) else 'WORKBENCH_PRIVATE_FILE_ERROR'}))
        return 2
    print(json.dumps(result))
    return 0
