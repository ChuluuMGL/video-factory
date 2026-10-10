"""One Feishu user grant for a reviewed terminal import or script/video decision.

The user token exists only in this short-lived worker's memory. No Video Factory
web listener, token file, or model call is involved.
"""
import json
import secrets
import sys
import time
from pathlib import Path

from .feishu_bridge import meta
from .script_jobs import GeneratedReviewBridge
from .feishu_oauth import DeviceOAuth
from .postgres_store import selected_store
from .runtime_cli import secret_input
from .runtime_store import RuntimeFault, identifier
from .stack import Stack
from .stack_worker import execute_interactive


def register(commands):
    for container in (False, True):
        parser = commands.add_parser('stack-feishu-session' if container else 'feishu-session',
                                     help='terminal Feishu authorization and reviewed task import/review')
        parser.add_argument('action', choices=('import', 'review'))
        parser.add_argument('--stack-root' if container else '--root', type=Path, required=True)
        parser.add_argument('--project', required=True)
        if not container: parser.add_argument('--master-key-file', type=Path, required=True)
        parser.add_argument('--record')
        parser.add_argument('--expected-revision', type=int, default=0)
        parser.add_argument('--task')
        parser.add_argument('--revision', type=int, default=1)
        parser.add_argument('--stage', choices=('script', 'video'), default='script')
        parser.add_argument('--decision', choices=('accept', 'reject'), default='accept')
        parser.add_argument('--feedback', default='')
        parser.add_argument('--event')


def arguments(args):
    identifier(args.project)
    if args.action == 'import':
        if not args.record or args.task or args.event or args.feedback or args.stage != 'script' or args.decision != 'accept':
            raise RuntimeFault('FEISHU_SESSION_ARGUMENTS_INVALID')
    elif not args.task or args.record or args.expected_revision != 0 or (args.decision == 'reject' and not args.feedback.strip()):
        raise RuntimeFault('FEISHU_SESSION_ARGUMENTS_INVALID')
    if args.event: identifier(args.event)


def terminal_operation(bridge, oauth, args, *, read=input, write=print,
                       clock=time.monotonic, sleep=time.sleep):
    grant = oauth.start()
    write('请由本人在飞书完成授权：' + grant['verification_uri'])
    write('飞书授权码：' + grant['user_code'])
    deadline = clock() + min(240, grant['expires_in'])
    interval = grant['interval']
    user = None
    while clock() < deadline:
        sleep(min(interval, max(0, deadline-clock())))
        response = oauth.poll(grant['device_code'])
        if response['status'] == 'authorized':
            user = response['access_token']
            break
        if response['status'] == 'slow_down': interval = min(interval+5, 60)
    if user is None: raise RuntimeFault('FEISHU_OAUTH_DENIED_OR_EXPIRED')
    if args.action == 'import':
        prepared = bridge.prepare_import(user, args.project, args.record, args.expected_revision)
    else:
        prepared = bridge.prepare_review(user, args.project, args.task, args.revision,
                                         args.stage, args.decision, args.feedback)
    write('请核对本次操作：' + json.dumps(prepared['plan'], ensure_ascii=False, sort_keys=True))
    write('计划校验值：' + prepared['plan_sha256'])
    if read('确认上述操作？输入 yes 后执行，其余输入取消> ').strip() != 'yes':
        return {'status': 'not_confirmed', 'feishu_writes': 0, 'model_calls': 0}
    if args.action == 'import':
        result = bridge.import_task(user, args.project, args.record,
                                    prepared['plan_sha256'], args.expected_revision)
    else:
        event = args.event or 'terminal_' + secrets.token_hex(16)
        write('审核事件 ID：' + event)
        result = bridge.review(user, args.project, args.task, args.revision,
                               args.stage, args.decision, event, prepared['plan_sha256'], args.feedback)
    return {**result, 'model_calls': 0}


def run(args):
    try:
        arguments(args)
        if not all(stream.isatty() for stream in (sys.stdin, sys.stdout, sys.stderr)):
            raise RuntimeFault('FEISHU_SESSION_REQUIRES_PRIVATE_TTY')
        from .setup_admin import SetupAdmin
        store = selected_store()(args.root)
        bridge = GeneratedReviewBridge(store)
        with store.connect() as db:
            bridge._binding(db, args.project)
            setup = meta(db, 'setup:project:' + args.project)
            profile = SetupAdmin(store).profile(db, args.project, setup['plan_sha256'] if setup else '')
        if not profile or not profile.get('credential_ref', '').startswith('secret:'):
            raise RuntimeFault('FEISHU_APP_PROFILE_MISSING')
        app_secret = store.resolve_secret(profile['credential_ref'][7:], secret_input(args.master_key_file, ''))
        oauth = DeviceOAuth(profile['app_id'], app_secret)
        result = terminal_operation(bridge, oauth, args)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result.get('status') != 'not_confirmed' else 2
    except (RuntimeFault, KeyboardInterrupt, EOFError) as error:
        code = str(error) if isinstance(error, RuntimeFault) else 'FEISHU_SESSION_INTERRUPTED'
        print(json.dumps({'error': code, 'feishu_writes': 0, 'model_calls': 0}))
        return 2
    except Exception:
        print(json.dumps({'error': 'FEISHU_SESSION_UNCERTAIN_READ_LEDGER', 'model_calls': 0}))
        return 2


def run_stack(args):
    try:
        arguments(args)
        stack = Stack(args.stack_root)
        if stack.config['schema'] != 2: raise RuntimeFault('STACK_FEISHU_REQUIRES_UPGRADE')
        command = ['feishu-session', args.action, '--root', '/state', '--project', args.project,
                   '--master-key-file', '/run/secrets/runtime_master']
        for name in ('record', 'task', 'event'):
            value = getattr(args, name)
            if value is not None: command += ['--' + name, value]
        for name in ('expected_revision', 'revision', 'stage', 'decision', 'feedback'):
            command += ['--' + name.replace('_', '-'), str(getattr(args, name))]
        return execute_interactive(stack, command)
    except RuntimeFault as error:
        print(json.dumps({'error': str(error), 'model_calls': 0}))
        return 2
    except Exception:
        print(json.dumps({'error': 'STACK_FEISHU_SESSION_UNCERTAIN_READ_LEDGER', 'model_calls': 0}))
        return 2
