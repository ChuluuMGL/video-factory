"""Operator CLI; approving an exact request and enabling its submit are explicit."""
import json
from pathlib import Path
from .onboarding import read_input_file, SetupError
from .postgres_store import selected_store
from .runtime_cli import secret_input
from .runtime_store import RuntimeFault
from .worker import Worker


def register_worker(commands, *, container=False):
    p=commands.add_parser('stack-worker' if container else 'worker',help='one-task H3 candidate; prepare/approve are offline, step may contact provider')
    p.add_argument('action',choices=('prepare','approve','step','status','recover-auth'))
    if container:
        p.add_argument('--stack-root',type=Path,required=True)
    else:
        p.add_argument('--root',type=Path,required=True)
    p.add_argument('--token-file',type=Path,required=True)
    p.add_argument('--project',required=True)
    p.add_argument('--task',required=True)
    p.add_argument('--revision',type=int,required=True)
    p.add_argument('--assets-root',type=Path)
    p.add_argument('--specification',type=Path)
    p.add_argument('--credential-ref')
    p.add_argument('--billing-owner')
    p.add_argument('--region',choices=('global','cn'),default='global')
    p.add_argument('--expect-plan')
    if not container:
        p.add_argument('--master-key-file',type=Path)
        p.add_argument('--media-root',type=Path)
    p.add_argument('--allow-paid-submit',action='store_true')


def run_worker(args):
    try:
        worker=Worker(selected_store()(args.root));token=secret_input(args.token_file,'')
        identity={'project':args.project,'task':args.task,'revision':args.revision}
        if args.action=='status':result=worker.status(token,**identity)
        elif args.action=='recover-auth':
            result=worker.recover_auth(token,**identity,expected_plan=args.expect_plan,
                                      credential_ref=args.credential_ref,region=args.region)
        elif args.action in ('prepare','approve'):
            if not all((args.assets_root,args.specification,args.credential_ref,args.billing_owner)):
                raise RuntimeFault('WORKER_INPUTS_CREDENTIAL_AND_BILLING_REQUIRED')
            values={**identity,'assets_root':args.assets_root,'specification':read_input_file(args.specification),
                    'credential_ref':args.credential_ref,'billing_owner':args.billing_owner,'region':args.region}
            if args.action=='prepare':result=worker.prepare(token,**values)
            else:result=worker.approve(token,args.expect_plan,**values)
        else:
            if not args.master_key_file or not args.media_root:raise RuntimeFault('WORKER_MASTER_KEY_AND_MEDIA_DIRECTORY_REQUIRED')
            result=worker.step(token,**identity,master_key=secret_input(args.master_key_file,''),media_root=args.media_root,allow_paid=args.allow_paid_submit)
        print(json.dumps(result,ensure_ascii=False,sort_keys=True));return 0
    except (RuntimeFault,SetupError) as error:
        print(json.dumps({'error':str(error),'automatic_resubmit':False}));return 2
    except Exception:
        print(json.dumps({'error':'WORKER_OPERATION_UNCERTAIN_READ_STATUS','automatic_resubmit':False}));return 2
