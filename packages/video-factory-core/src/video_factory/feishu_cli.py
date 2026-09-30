"""Explicit read-only Feishu input and verified-user ledger review commands."""
import json
from pathlib import Path
from .feishu_bridge import FeishuBridge
from .onboarding import read_input_file
from .postgres_store import selected_store
from .runtime_cli import secret_input
from .runtime_store import RuntimeFault
from .stack import Stack
from .stack_worker import execute_once


def register_feishu(commands,*,container=False):
    p=commands.add_parser('stack-feishu' if container else 'feishu',help='verified Feishu user import/review; no Base writes or model calls')
    p.add_argument('action',choices=('bind','prepare-import','import','prepare-review','review'))
    p.add_argument('--stack-root' if container else '--root',type=Path,required=True)
    p.add_argument('--project',required=True)
    p.add_argument('--user-token-file',type=Path,required=True)
    p.add_argument('--token-file',type=Path,help='local administrator token, required only for binding')
    p.add_argument('--configuration',type=Path)
    p.add_argument('--expected-binding')
    p.add_argument('--record')
    p.add_argument('--expected-revision',type=int,default=0)
    p.add_argument('--expect-plan')
    p.add_argument('--task')
    p.add_argument('--revision',type=int,default=1)
    p.add_argument('--stage',choices=('script','video'),default='script')
    p.add_argument('--decision',choices=('accept','reject'),default='accept')
    p.add_argument('--feedback',default='')
    p.add_argument('--event')


def run_feishu(args):
    try:
        bridge=FeishuBridge(selected_store()(args.root))
        user=secret_input(args.user_token_file,'')
        if args.action=='bind':
            if not args.configuration or not args.token_file:raise RuntimeFault('FEISHU_BINDING_AND_ADMIN_REQUIRED')
            result=bridge.bind(secret_input(args.token_file,''),args.project,read_input_file(args.configuration),user,args.expected_binding)
        elif args.action=='prepare-import':result=bridge.prepare_import(user,args.project,args.record,args.expected_revision)
        elif args.action=='import':result=bridge.import_task(user,args.project,args.record,args.expect_plan,args.expected_revision)
        elif args.action=='prepare-review':result=bridge.prepare_review(user,args.project,args.task,args.revision,args.stage,args.decision,args.feedback)
        else:result=bridge.review(user,args.project,args.task,args.revision,args.stage,args.decision,args.event,args.expect_plan,args.feedback)
        print(json.dumps(result,ensure_ascii=False,sort_keys=True));return 0
    except RuntimeFault as error:
        print(json.dumps({'error':str(error),'feishu_writes':0,'model_calls':0}));return 2
    except Exception:
        print(json.dumps({'error':'FEISHU_OPERATION_FAILED_READ_LEDGER_BEFORE_RETRY','feishu_writes':0,'model_calls':0}));return 2


def run_stack_feishu(args):
    try:
        stack=Stack(args.stack_root)
        if stack.config['schema']!=2:raise RuntimeFault('STACK_FEISHU_REQUIRES_UPGRADE')
        command=['feishu',args.action,'--root','/state','--project',args.project]
        for name in ('user_token_file','token_file','configuration'):
            value=getattr(args,name)
            if value is not None:
                if not value.is_absolute() or not value.is_relative_to('/work') or '..' in value.parts:raise RuntimeFault('FEISHU_INPUTS_REQUIRE_CONTAINER_WORK_DIRECTORY')
                command+=['--'+name.replace('_','-'),str(value)]
        for name in ('expected_binding','record','expected_revision','expect_plan','task','revision','stage','decision','feedback','event'):
            value=getattr(args,name)
            if value is not None:command+=['--'+name.replace('_','-'),str(value)]
        result=execute_once(stack,command,egress=True)
        print(json.dumps(result,ensure_ascii=False,sort_keys=True));return 0
    except RuntimeFault as error:
        print(json.dumps({'error':str(error),'feishu_writes':0,'model_calls':0}));return 2
    except Exception:
        print(json.dumps({'error':'STACK_FEISHU_UNCERTAIN_READ_LEDGER','feishu_writes':0,'model_calls':0}));return 2
