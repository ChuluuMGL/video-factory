"""Administrator setup for human review in the original Feishu task table."""
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
    parser = commands.add_parser('feishu-review', help='enable review in the original Feishu task table')
    parser.add_argument('action', choices=['enable','status'])
    parser.add_argument('--stack-root', type=Path, required=True)
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--configure-models',action='store_true',help='continue model and schedule configuration with the same private admin session')
    parser.add_argument('--video-assets',type=Path,help='non-secret SKU asset mapping for continuous configuration')
    parser.add_argument('--browser-input',action='store_true',help='temporary loopback private input when no private TTY is available')
    parser.add_argument('--input-port',type=int,default=8792)


def cli(args):
    if getattr(args,'configure_models',False) and args.action!='enable':
        print(json.dumps({'error':'FEISHU_CONTINUOUS_ENABLE_REQUIRED'}));return 2
    if not getattr(args,'browser_input',False) and not all(stream.isatty() for stream in (sys.stdin,sys.stdout,sys.stderr)):
        print(json.dumps({'error':'PRIVATE_INPUT_REQUIRED'})); return 2
    token=None;browser=None
    read,hidden,write=input,getpass.getpass,print
    try:
        if getattr(args,'browser_input',False):
            from .setup_browser import BrowserInput
            browser=BrowserInput(args.input_port)
            print(json.dumps({'status':'awaiting_human_input','url':browser.url,'expires_in':1800}),flush=True)
            read,hidden,write=browser.read,browser.hidden,browser.write
        stack=Stack(args.stack_root)
        session=SessionStore(args.session).read()
        if args.action=='enable':
            from .runner import status as runner_status
            project=session['configuration']['project']['id']
            runner=runner_status(stack,project)
            if runner['status']=='not_configured': raise RuntimeFault('FEISHU_NATIVE_RUNNER_REQUIRED')
            if runner['status']!='running': raise RuntimeFault('FEISHU_NATIVE_RUNNER_NOT_READY')
        token=rpc(stack,{'action':'open','session':session,'password':hidden('产品管理员密码: ')})['token']
        def call(operation,expected=None):
            return rpc(stack,{'action':'native_review','session':session,'token':token,
                              'operation':operation,'expected_plan':expected})
        if args.action=='status': result=call('status')
        else:
            plan=call('plan')
            write('将在原飞书任务表补充状态、审核版本、脚本摘要、审核意见、视频摘要和视频附件字段，并订阅该 Base 的变更事件。')
            write('目标 Base：'+plan['plan']['context']['target']['base_token'])
            write('该应用必须专供本项目事件接收，不得与其他机器人或长连接客户端共用。')
            write('需先给本项目飞书应用授予目标 Base 管理/编辑权限，并开通应用身份和用户身份的 bitable:app、应用身份的 docs:event:subscribe。')
            write('首次配置时，本命令订阅 Base 后，再到飞书开发者后台添加记录变更事件并发布；最后用测试记录验证实际送达。')
            if not choice('确认启用原任务表审核', read, write): result={'status':'unchanged'}
            else: result=call('enable',plan['plan_sha256'])
            if getattr(args,'configure_models',False) and result.get('status')=='enabled':
                from .production_setup import welcome
                write('原表审核已配置。继续模型和调度配置；沿用本次短期认证，不再输入管理员密码。')
                production=welcome(args,read=read,hidden=hidden,write=write,admin_token=token)
                result={'review':result,'production':production,'business_ready':False}
        print(json.dumps(result,ensure_ascii=False));return 0
    except (KeyboardInterrupt,EOFError):return 130
    except Exception as error:
        code=str(error) if isinstance(error,(RuntimeFault,SetupError)) else 'FEISHU_NATIVE_OPERATION_FAILED'
        print(json.dumps({'error':code}));return 2
    finally:
        try:
            if token: rpc(stack,{'action':'close','token':token})
        finally:
            if browser:browser.close()
