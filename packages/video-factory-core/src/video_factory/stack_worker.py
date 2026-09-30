"""One-shot container worker; no scheduler or automatic paid activation."""
import json
import secrets
from pathlib import Path

from .runtime_store import RuntimeFault
from .stack import Stack, run


def execute_once(stack,command,*,egress):
    name='vf-worker-'+stack.config['instance']+'-'+secrets.token_hex(6)
    with stack.lock():
        if not stack.status()['infrastructure_ready']:raise RuntimeFault('STACK_WORKER_REQUIRES_HEALTHY_STACK')
        try:
            if egress:
                stack.compose('up','-d','--pull','never','--wait','--wait-timeout','40','egress',timeout=60)
            result=json.loads(stack.compose('run','--rm','--name',name,'-T','--no-deps','worker',*command,timeout=435))
        finally:
            # Stop even on rejection/uncertain result; relay also has a hard
            # 10-minute lifetime if the operator process is killed outright.
            try:
                remaining=run(['docker','container','ls','--all','--filter','name=^/'+name+'$','--format','{{.ID}}'],timeout=20)
                if remaining.strip():run(['docker','container','rm','--force',name],timeout=20)
            finally:
                if egress:stack.compose('stop','--timeout','5','egress',timeout=20)
    return result


def run_stack_worker(args):
    try:
        stack=Stack(args.stack_root)
        if stack.config['schema']!=2:raise RuntimeFault('STACK_WORKER_REQUIRES_UPGRADE')
        command=['worker',args.action,'--root','/state','--project',args.project,'--task',args.task,'--revision',str(args.revision)]
        for name in ('token_file','assets_root','specification'):
            value=getattr(args,name)
            if value is not None:
                path=Path(value)
                if not path.is_absolute() or not path.is_relative_to('/work') or '..' in path.parts:
                    raise RuntimeFault('WORKER_INPUTS_REQUIRE_CONTAINER_WORK_DIRECTORY')
                command+=['--'+name.replace('_','-'),str(value)]
        for name in ('credential_ref','billing_owner','region','expect_plan'):
            value=getattr(args,name)
            if value is not None:command+=['--'+name.replace('_','-'),str(value)]
        if args.action=='step':
            command+=['--master-key-file','/run/secrets/runtime_master','--media-root','/media']
            if args.allow_paid_submit:command+=['--allow-paid-submit']
        result=execute_once(stack,command,egress=args.action=='step')
        print(json.dumps(result,ensure_ascii=False,sort_keys=True));return 0
    except RuntimeFault as error:
        print(json.dumps({'error':str(error),'automatic_resubmit':False}));return 2
    except Exception:
        print(json.dumps({'error':'STACK_WORKER_UNCERTAIN_READ_STATUS','automatic_resubmit':False}));return 2
