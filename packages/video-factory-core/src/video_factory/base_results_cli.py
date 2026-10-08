"""Private administrator entry; shares the existing setup and secret channel."""
import getpass
import json
from pathlib import Path
import sys

from .onboarding import SessionStore
from .runtime_store import RuntimeFault
from .setup_admin import rpc
from .stack import Stack
from .setup_run import choice


def register(commands):
    parser = commands.add_parser('base-results', help='opt-in append-only Feishu result table')
    parser.add_argument('action', choices=['enable', 'pause', 'status', 'sync', 'repair', 'recover'])
    parser.add_argument('--stack-root', type=Path, required=True)
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--event')
    parser.add_argument('--step')
    parser.add_argument('--browser-input', action='store_true')
    parser.add_argument('--input-port', type=int, default=8792)


def welcome(args, read=input, hidden=getpass.getpass, write=print):
    stack = Stack(args.stack_root); session = SessionStore(args.session).read()
    project = session['configuration']['project']['id']
    write('Video Factory · 飞书结果同步 · '+project)
    opened = rpc(stack, {'action': 'open', 'session': session, 'password': hidden('产品管理员密码: ')})
    token = opened['token']
    def call(operation, expected=None):
        return rpc(stack, {'action': 'base_results', 'token': token, 'session': session,
                           'operation': operation, 'expected_plan': expected,
                           'event': getattr(args, 'event', None), 'step': getattr(args, 'step', None)})
    try:
        if args.action == 'repair':
            prepared = call('repair_plan')
            write('仅授权一次重试：'+prepared['plan']['task']+' / '+prepared['plan']['step'])
            write('重试前会先查远端记录；记录沿用原幂等号，已知上传沿用原事务。预上传无回执时可能留下未使用的旧上传事务。')
            if not choice('已核对状态，允许本步骤再尝试一次', read, write): return {'status': 'unchanged'}
            return call('repair', prepared['plan_sha256'])
        if args.action == 'recover':
            prepared = call('recovery_plan')
            write('已回读 '+str(prepared['plan']['verified_rows'])+' 条结果；清除恢复保护后仍保持暂停。')
            if not choice('确认上述恢复核对结果', read, write): return {'status': 'unchanged'}
            return call('recover', prepared['plan_sha256'])
        if args.action != 'enable': return call(args.action)
        write('在本项目绑定的 Base 中新增专用结果表，追加任务状态、脚本和视频附件。原始任务表不会改写。')
        write('请先在目标 Base 的「添加文档应用」中授予本项目应用可编辑权限；若启用了高级权限，须按该 Base 的规则授予可管理及所需的数据可见范围。')
        write('应用需开放表创建、字段读取、记录读取/创建和素材上传权限；不需要记录编辑或删除权限。')
        prepared = call('plan')
        write('目标 Base：'+prepared['plan']['context']['target']['base_token'])
        write('后续由已启用的 n8n 调度自动推进；提交结果不明时暂停该条同步，不重复提交。')
        if not choice('确认开启本项目结果同步', read, write): return {'status': 'unchanged'}
        return call('enable', prepared['plan_sha256'])
    finally: rpc(stack, {'action': 'close', 'token': token})


def cli(args):
    browser = None
    try:
        if args.browser_input:
            from .setup_browser import BrowserInput
            browser = BrowserInput(args.input_port)
            print(json.dumps({'status': 'awaiting_human_input', 'url': browser.url, 'expires_in': 1800}), flush=True)
            result = welcome(args, browser.read, browser.hidden, browser.write)
        else:
            if not all(stream.isatty() for stream in (sys.stdin, sys.stdout, sys.stderr)):
                raise RuntimeFault('PRIVATE_INPUT_REQUIRED')
            result = welcome(args)
        print(json.dumps(result, ensure_ascii=False)); return 0
    except (KeyboardInterrupt, EOFError): return 130
    except Exception as error:
        print(json.dumps({'error': str(error) if isinstance(error, RuntimeFault) else 'BASE_RESULTS_OPERATION_FAILED'})); return 2
    finally:
        if browser: browser.close()
