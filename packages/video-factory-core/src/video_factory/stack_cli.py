"""Compose lifecycle commands run on the customer's Linux Docker host."""
import json
import hashlib
from pathlib import Path
import tarfile

from cryptography.fernet import Fernet

from .runtime_cli import secret_input
from .runtime_store import RuntimeFault, exclusive_write, private_directory
from .stack import Stack, images, run, preflight, admin_host, local_engine


def register_stack(commands):
    p=commands.add_parser('stack',help='pinned runtime + n8n + PostgreSQL container deployment')
    p.add_argument('action',choices=('install','preflight','prepare','fetch','build','up','status','stop','down','keygen','backup','restore','upgrade'))
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--deployment')
    p.add_argument('--wheelhouse',type=Path)
    p.add_argument('--password-file',type=Path)
    p.add_argument('--runtime-port',type=int)
    p.add_argument('--n8n-port',type=int)
    p.add_argument('--backup-key-file',type=Path)
    p.add_argument('--output',type=Path)
    p.add_argument('--source',type=Path)
    p.add_argument('--candidate-root',type=Path)


def install_stack(args, *, password=None, expected_wheels=None):
    admin_host()
    local_engine()
    if not args.deployment or not args.wheelhouse:
        raise RuntimeFault('DEPLOYMENT_AND_RELEASE_REQUIRED')
    if not args.root.exists():
        if not args.root.is_absolute() or args.root.parent.resolve()!=args.root.parent:
            raise RuntimeFault('STACK_ABSOLUTE_DIRECTORY_REQUIRED')
        info=args.root.parent.stat()
        if info.st_uid!=0 or info.st_mode & 0o022:
            raise RuntimeFault('STACK_PARENT_NOT_ROOT_OWNED')
        args.root.mkdir(mode=0o700)
    if (args.root/'stack.json').exists():
        stack=Stack(args.root,repair_derived=True)
        incoming={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in args.wheelhouse.glob('*.whl')}
        if stack.config['deployment']!=args.deployment or incoming!=stack.config['wheels']:
            raise RuntimeFault('INSTALL_RESUME_TARGET_OR_RELEASE_CHANGED_USE_UPGRADE')
        if args.runtime_port and args.runtime_port!=stack.config['runtime_port'] or args.n8n_port and args.n8n_port!=stack.config['n8n_port']:
            raise RuntimeFault('INSTALL_RESUME_PORTS_CHANGED')
    else:
        preflight(args.root,args.runtime_port or 8787,args.n8n_port or 5678)
        stack=Stack.prepare(args.root,args.deployment,args.wheelhouse,password if password is not None else secret_input(args.password_file,'设置产品管理员密码: '),
                            args.runtime_port or 8787,args.n8n_port or 5678)
    if expected_wheels is not None and stack.config['wheels'] != expected_wheels:
        raise RuntimeFault('SETUP_RELEASE_CHANGED_BEFORE_EXECUTION')
    for name in ('python','postgres','n8n','gateway') + (('ffmpeg',) if stack.config['schema']==2 else ()):
        run(['docker','pull','--platform','linux/amd64',stack.config['images'][name]],timeout=600)
    if not stack.config['runtime_image']:
        stack.build()
    return stack.up()


def run_stack(args):
    try:
        if args.action=='install':
            result=install_stack(args)
        elif args.action=='preflight':
            result=preflight(args.root,args.runtime_port or 8787,args.n8n_port or 5678)
        elif args.action=='prepare':
            if not args.wheelhouse or not args.deployment:
                raise RuntimeFault('DEPLOYMENT_AND_RELEASE_REQUIRED')
            stack=Stack.prepare(args.root,args.deployment,args.wheelhouse,secret_input(args.password_file,'设置产品管理员密码: '),
                                args.runtime_port or 8787,args.n8n_port or 5678)
            result={'prepared':True,'deployment':stack.config['deployment'],'execute_paid':False,'components_started':False}
        elif args.action=='restore':
            if not args.source or not args.backup_key_file:
                raise RuntimeFault('BACKUP_SOURCE_AND_KEY_REQUIRED')
            stack=Stack.restore(args.source,args.root,secret_input(args.backup_key_file,''),deployment=args.deployment,
                                runtime_port=args.runtime_port,n8n_port=args.n8n_port)
            result={'restored':True,'deployment':stack.config['deployment'],'build_and_up_required':True,'sessions_revoke_on_start':True}
        else:
            stack=Stack(args.root)
            if args.action=='fetch':
                local_engine()
                for name in ('python','postgres','n8n','gateway') + (('ffmpeg',) if stack.config['schema']==2 else ()):
                    run(['docker','pull','--platform','linux/amd64',stack.config['images'][name]],timeout=600)
                result={'pinned_images_fetched':True}
            elif args.action=='build':result=stack.build()
            elif args.action=='up':result=stack.up()
            elif args.action=='status':result=stack.status()
            elif args.action=='stop':result=stack.stop()
            elif args.action=='down':
                with stack.lock():stack.compose('down','--timeout','60',timeout=200)
                result={'containers_removed':True,'customer_data_preserved':True}
            elif args.action=='keygen':
                if not args.output or args.output.is_relative_to(args.root):
                    raise RuntimeFault('BACKUP_KEY_OUTPUT_OUTSIDE_STACK_REQUIRED')
                private_directory(args.output.parent);exclusive_write(args.output,Fernet.generate_key())
                result={'backup_key_file':str(args.output),'store_recovery_copy_separately':True}
            elif args.action=='upgrade':
                if not args.candidate_root or not args.wheelhouse or not args.output or not args.backup_key_file:
                    raise RuntimeFault('CANDIDATE_RELEASE_CHECKPOINT_AND_KEY_REQUIRED')
                result=stack.upgrade(args.candidate_root,args.wheelhouse,args.output,secret_input(args.backup_key_file,''))
            elif args.action=='backup':
                if not args.output or not args.backup_key_file:
                    raise RuntimeFault('BACKUP_OUTPUT_AND_KEY_REQUIRED')
                result=stack.backup(args.output,secret_input(args.backup_key_file,''))
            else:raise RuntimeFault('STACK_ACTION_UNSUPPORTED')
        print(json.dumps(result,ensure_ascii=False,sort_keys=True))
        return 2 if result.get('status') in ('rolled_back','needs_attention') else 0
    except RuntimeFault as error:
        print(json.dumps({'error':str(error)}));return 2
    except (OSError,ValueError,TypeError,KeyError,tarfile.TarError):
        print(json.dumps({'error':'STACK_OPERATION_FAILED','check_status_before_retry':True}));return 2
