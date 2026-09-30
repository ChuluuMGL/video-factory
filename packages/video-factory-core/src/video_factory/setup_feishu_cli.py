"""Terminal/Agent connection wizard and explicit customer-host application."""
import json
from pathlib import Path
import sys

from .onboarding import SessionStore, SetupError, read_input_file, read_json
from .runtime_cli import secret_input
from .runtime_store import RuntimeFault
from .postgres_store import selected_store
from .setup_feishu import ConnectionSession, SetupFeishu, describe, interactive
from .stack import Stack
from .stack_worker import execute_once


def register(commands):
    p = commands.add_parser('setup-feishu', help='resume connection questions; explicitly verify/apply an installed Setup')
    p.add_argument('action', choices=('configure', 'plan', 'apply', 'status', 'connect'))
    p.add_argument('--session', type=Path, required=True, help='private connection session; /work path for stack operations')
    p.add_argument('--setup-session', type=Path, help='original completed Setup; required for configure')
    target = p.add_mutually_exclusive_group()
    target.add_argument('--root', type=Path)
    target.add_argument('--stack-root', type=Path)
    view = p.add_mutually_exclusive_group()
    view.add_argument('--json', action='store_true')
    view.add_argument('--interactive', action='store_true')
    p.add_argument('--answers', help='JSON answers file or -; no raw credentials')
    p.add_argument('--expect-revision', type=int)
    p.add_argument('--token-file', type=Path, help='private local administrator token')
    p.add_argument('--user-token-file', type=Path, help='private Feishu user access token; never raw CLI input')
    p.add_argument('--expect-plan')
    p.add_argument('--project', help='exact installed project for the connection window')
    p.add_argument('--app-id')
    credential = p.add_mutually_exclusive_group()
    credential.add_argument('--app-secret-file', type=Path)
    credential.add_argument('--app-secret-ref')
    p.add_argument('--master-key-file', type=Path)
    p.add_argument('--port', type=int, default=8791)
    p.add_argument('--seconds', type=int, default=360)
    p.add_argument('--container-network', action='store_true')


def run(args):
    try:
        if args.action == 'connect':
            from .setup_connect import run_connect
            return run_connect(args)
        if args.project or args.app_id or args.app_secret_file or args.app_secret_ref or args.master_key_file or args.container_network or args.port != 8791 or args.seconds != 360:
            raise SetupError('SETUP_FEISHU_WINDOW_OPTIONS_ONLY_FOR_CONNECT')
        if args.action == 'configure':
            if not args.setup_session or args.root or args.stack_root or args.token_file or args.user_token_file or args.expect_plan:
                raise SetupError('SETUP_FEISHU_CONFIGURE_ARGUMENTS_INVALID')
            if (args.answers is None) != (args.expect_revision is None):
                raise SetupError('SETUP_FEISHU_ANSWERS_REQUIRE_REVISION')
            if args.interactive and (args.answers is not None or not (sys.stdin.isatty() and sys.stdout.isatty())):
                raise SetupError('SETUP_FEISHU_INTERACTIVE_REQUIRES_TTY')
            source = SessionStore(args.setup_session).read()
            session = ConnectionSession(args.session)
            draft = session.start(source)
            if args.answers is not None:
                answers = read_json(sys.stdin) if args.answers == '-' else read_input_file(args.answers)
                result = session.answer(answers, args.expect_revision)
            elif args.interactive or (not args.json and sys.stdin.isatty() and sys.stdout.isatty()):
                result = interactive(session)
            else:
                result = describe(draft)
        else:
            if args.answers is not None or args.expect_revision is not None or args.setup_session or args.interactive:
                raise SetupError('SETUP_FEISHU_OPERATION_ARGUMENTS_INVALID')
            if not args.token_file or (args.action != 'status' and not args.user_token_file):
                raise RuntimeFault('SETUP_FEISHU_PRIVATE_TOKEN_FILES_REQUIRED')
            if args.action == 'apply' and not args.expect_plan:
                raise RuntimeFault('SETUP_FEISHU_REVIEWED_PLAN_REQUIRED')
            if args.action != 'apply' and args.expect_plan:
                raise RuntimeFault('SETUP_FEISHU_PLAN_ONLY_FOR_APPLY')
            if args.stack_root:
                stack = Stack(args.stack_root)
                if stack.config['schema'] != 2:
                    raise RuntimeFault('STACK_FEISHU_REQUIRES_UPGRADE')
                command = ['setup-feishu', args.action, '--root', '/state']
                for key in ('session', 'token_file', 'user_token_file'):
                    value = getattr(args, key)
                    if value is not None:
                        if not value.is_absolute() or not value.is_relative_to('/work') or '..' in value.parts:
                            raise RuntimeFault('FEISHU_INPUTS_REQUIRE_CONTAINER_WORK_DIRECTORY')
                        command += ['--' + key.replace('_', '-'), str(value)]
                if args.expect_plan:
                    command += ['--expect-plan', args.expect_plan]
                result = execute_once(stack, command, egress=args.action != 'status')
            else:
                if not args.root:
                    raise RuntimeFault('SETUP_FEISHU_RUNTIME_TARGET_REQUIRED')
                session = ConnectionSession(args.session)
                draft = session.snapshot()
                admin = secret_input(args.token_file, '')
                service = SetupFeishu(selected_store()(args.root))
                if args.action == 'status':
                    result = service.status(admin, draft)
                else:
                    user = secret_input(args.user_token_file, '')
                    result = service.prepare(admin, draft, user) if args.action == 'plan' else service.apply(admin, draft, user, args.expect_plan)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 130 if result.get('interrupted') else (2 if 'error' in result else 0)
    except (SetupError, RuntimeFault) as error:
        print(json.dumps({'error': str(error), 'business_ready': False, 'feishu_writes': 0, 'model_calls': 0})); return 2
    except Exception:
        print(json.dumps({'error': 'SETUP_FEISHU_OPERATION_FAILED_READ_STATUS', 'business_ready': False,
                          'feishu_writes': 0, 'model_calls': 0})); return 2
