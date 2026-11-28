"""Per-project lifetime provider budget in the runtime ledger."""

import json
from pathlib import Path

from .feishu_bridge import meta, save
from .runtime_store import RuntimeFault, RuntimeStore, identifier


def _key(project):
    return 'script:budget:' + project


def policy(db, project):
    return meta(db, _key(project)) or {'max_calls': None, 'used_calls': 0, 'paid_video_disabled': False}


def require_script_slot(db, project):
    value = policy(db, project)
    if value['max_calls'] is not None and value['used_calls'] >= value['max_calls']:
        raise RuntimeFault('SCRIPT_BUDGET_EXHAUSTED')
    return value


def reserve_script_call(db, project):
    # The caller commits this counter in the same BEGIN IMMEDIATE transaction
    # as the submission intent. Never refund an attempted or uncertain call.
    value = require_script_slot(db, project)
    save(db, _key(project), {**value, 'used_calls': value['used_calls'] + 1})


def require_paid_video(db, project):
    if policy(db, project)['paid_video_disabled']:
        raise RuntimeFault('SCRIPT_BUDGET_PAID_VIDEO_DISABLED')


def configure(store, token, project, max_calls, *, disable_paid_video=False):
    identifier(project)
    if type(max_calls) is not int or not 0 <= max_calls <= 2**31 - 1 or type(disable_paid_video) is not bool:
        raise RuntimeFault('SCRIPT_BUDGET_INVALID')
    with store.connect() as db:
        actor = store.authorize(db, token, 'admin')
        if not db.execute('SELECT 1 FROM projects WHERE id=?', (project,)).fetchone():
            raise RuntimeFault('PROJECT_MISSING')
        current = policy(db, project)
        limit = current['max_calls']
        if limit is not None and max_calls > limit:
            raise RuntimeFault('SCRIPT_BUDGET_CANNOT_INCREASE')
        disabled = disable_paid_video or current['paid_video_disabled']
        if disabled and not current['paid_video_disabled'] and db.execute(
                "SELECT 1 FROM tasks WHERE project=? AND state='submission_unknown' LIMIT 1", (project,)).fetchone():
            raise RuntimeFault('SCRIPT_BUDGET_VIDEO_SUBMISSION_RECONCILE_FIRST')
        result = {'max_calls': max_calls, 'used_calls': current['used_calls'], 'paid_video_disabled': disabled}
        if result != current:
            save(db, _key(project), result)
            store.audit(db, actor, 'script_budget_configured', project)
    return {'project': project, **result}


def register(commands):
    parser = commands.add_parser('script-budget', help='manage the project script provider hard limit')
    actions = parser.add_subparsers(dest='budget_action', required=True)
    for action in ('configure', 'status'):
        command = actions.add_parser(action)
        command.add_argument('--root', type=Path, required=True)
        command.add_argument('--project', required=True)
        command.add_argument('--admin-token-file', type=Path, required=True)
        if action == 'configure':
            command.add_argument('--max-calls', type=int, required=True)
            command.add_argument('--disable-paid-video', action='store_true')


def cli(args):
    from .runtime_cli import secret_input
    try:
        store = RuntimeStore(args.root)
        token = secret_input(args.admin_token_file, '')
        project = identifier(args.project)
        if args.budget_action == 'configure':
            result = configure(store, token, project, args.max_calls, disable_paid_video=args.disable_paid_video)
        else:
            with store.connect() as db:
                store.authorize(db, token, 'admin')
                if not db.execute('SELECT 1 FROM projects WHERE id=?', (project,)).fetchone():
                    raise RuntimeFault('PROJECT_MISSING')
                result = {'project': project, **policy(db, project)}
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, RuntimeFault) as error:
        print(json.dumps({'error': str(error) if isinstance(error, RuntimeFault) else 'SCRIPT_BUDGET_INPUT_ERROR'}))
        return 2
