"""One-session private CLI for the opt-in original-Base production entry."""
import getpass
import json
from pathlib import Path
import sys

from .onboarding import SessionStore, SetupError
from .runtime_store import RuntimeFault
from .setup_admin import rpc
from .setup_run import choice
from .stack import Stack


def register(commands):
    parser = commands.add_parser('feishu-production', help='preview or enable original-Base production fields')
    parser.add_argument('action', choices=['enable', 'status', 'pause'])
    parser.add_argument('--stack-root', type=Path, required=True)
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--browser-input', action='store_true', help='private loopback input if no TTY exists')
    parser.add_argument('--input-port', type=int, default=8792)


def cli(args):
    if not args.browser_input and not all(stream.isatty() for stream in (sys.stdin, sys.stdout, sys.stderr)):
        print(json.dumps({'error': 'PRIVATE_INPUT_REQUIRED'}))
        return 2
    token = None
    browser = None
    read, hidden, write = input, getpass.getpass, print
    try:
        if args.browser_input:
            from .setup_browser import BrowserInput
            browser = BrowserInput(args.input_port)
            print(json.dumps({'status': 'awaiting_human_input', 'url': browser.url,
                              'expires_in': 1800}), flush=True)
            read, hidden, write = browser.read, browser.hidden, browser.write
        stack = Stack(args.stack_root)
        session = SessionStore(args.session).read()
        project = session['configuration']['project']['id']
        token = rpc(stack, {'action': 'open', 'session': session,
                            'password': hidden('产品管理员密码: ')})['token']

        def call(operation, approvers=None, cost_enabled=False, expected_plan=None):
            return rpc(stack, {'action': 'production_entry', 'session': session, 'token': token,
                               'operation': operation, 'approvers': approvers or [],
                               'cost_enabled': cost_enabled, 'expected_plan': expected_plan})

        status = call('status')
        if args.action == 'status':
            result = status
        elif args.action == 'pause':
            write('暂停后不再处理新的生产计划或费用确认；飞书字段与历史记录保留。')
            result = call('pause') if choice('确认暂停本项目飞书生产入口', read, write) else {
                'status': 'unchanged', 'provider_requests': 0}
        else:
            from .runner import status as runner_status
            if runner_status(stack, project).get('status') != 'running':
                raise RuntimeFault('PRODUCTION_RUNNER_NOT_READY')
            members = status['eligible_approvers']
            write('当前项目：' + project)
            write('费用负责人须先在本项目飞书绑定中。候选 open_id：')
            for index, member in enumerate(members, 1):
                write(str(index) + '. ' + member)
            raw = read('选择负责人编号；多人用逗号分隔> ').strip()
            try:
                indices = [int(item.strip()) for item in raw.split(',')]
                if (not indices or len(set(indices)) != len(indices)
                        or any(index < 1 or index > len(members) for index in indices)):
                    raise ValueError
                approvers = [members[index - 1] for index in indices]
            except (ValueError, IndexError):
                raise RuntimeFault('PRODUCTION_APPROVERS_INVALID') from None
            cost_enabled = choice('同时启用负责人逐条费用确认（确认后可触发付费脚本生成）', read, write)
            plan = call('plan', approvers, cost_enabled)
            write('目标 Base：' + plan['plan']['base_token'] + '，表：' + plan['plan']['table_id'])
            write('将新增字段：' + ('、'.join(plan['plan']['fields_to_create']) or '无'))
            write('费用负责人：' + '、'.join(approvers))
            write('状态：' + ('脚本付费确认可用' if cost_enabled else '只准备零费用脚本计划'))
            write('现有任务、脚本审核和视频审核保留；未产生模型调用。')
            if not choice('确认按以上范围修改原任务表并启用入口', read, write):
                result = {'status': 'unchanged', 'provider_requests': 0}
            else:
                result = call('enable', approvers, cost_enabled, plan['plan_sha256'])
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (KeyboardInterrupt, EOFError):
        return 130
    except Exception as error:
        code = str(error) if isinstance(error, (RuntimeFault, SetupError)) else 'PRODUCTION_SETUP_FAILED'
        print(json.dumps({'error': code}))
        return 2
    finally:
        try:
            if token:
                rpc(stack, {'action': 'close', 'token': token})
        finally:
            if browser:
                browser.close()
