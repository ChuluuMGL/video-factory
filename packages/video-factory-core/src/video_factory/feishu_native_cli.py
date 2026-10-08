"""Administrator setup for human review in the original Feishu task table."""
import getpass
import json
from pathlib import Path
import sys

from .onboarding import SessionStore
from .runtime_store import RuntimeFault
from .setup_admin import rpc
from .setup_run import choice
from .stack import Stack


def register(commands):
    parser = commands.add_parser('feishu-review', help='enable review in the original Feishu task table')
    parser.add_argument('action', choices=['enable','status'])
    parser.add_argument('--stack-root', type=Path, required=True)
    parser.add_argument('--session', type=Path, required=True)


def cli(args):
    if not all(stream.isatty() for stream in (sys.stdin,sys.stdout,sys.stderr)):
        print(json.dumps({'error':'PRIVATE_INPUT_REQUIRED'})); return 2
    stack=Stack(args.stack_root);session=SessionStore(args.session).read()
    token=None
    try:
        token=rpc(stack,{'action':'open','session':session,'password':getpass.getpass('产品管理员密码: ')})['token']
        def call(operation,expected=None):
            return rpc(stack,{'action':'native_review','session':session,'token':token,
                              'operation':operation,'expected_plan':expected})
        if args.action=='status': result=call('status')
        else:
            plan=call('plan')
            print('将在原飞书任务表补充状态、审核版本、脚本摘要、审核意见、视频摘要和视频附件字段，并订阅该 Base 的变更事件。')
            print('目标 Base：'+plan['plan']['context']['target']['base_token'])
            print('需先给本项目飞书应用授予 Base 文档管理/编辑和记录变更事件权限。')
            if not choice('确认启用原任务表审核', input, print): result={'status':'unchanged'}
            else: result=call('enable',plan['plan_sha256'])
        print(json.dumps(result,ensure_ascii=False));return 0
    except (KeyboardInterrupt,EOFError):return 130
    except Exception as error:
        print(json.dumps({'error':str(error) if isinstance(error,RuntimeFault) else 'FEISHU_NATIVE_OPERATION_FAILED'}));return 2
    finally:
        if token: rpc(stack,{'action':'close','token':token})
