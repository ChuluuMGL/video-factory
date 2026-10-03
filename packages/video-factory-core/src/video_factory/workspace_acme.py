"""Dedicated Certbot standalone issuance; no system nginx or global CA state.

Only normalized account files and deployed certificates enter the cold backup.
Interrupted orders remain fenced until their private job output is recovered.
"""
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys
import tempfile
from urllib.parse import urlsplit

from .runtime_store import RuntimeFault, fingerprint, private_file, identifier
from .stack import Stack, write_json, run
from . import workspace, workspace_tls

DIRECTORIES = {'staging':'https://acme-staging-v02.api.letsencrypt.org/directory',
               'production':'https://acme-v02.api.letsencrypt.org/directory'}
CLIENT = Path('/usr/bin/certbot')


def register(commands):
    p=commands.add_parser('workspace-acme',help='isolated certificate issue, recovery and scheduled renewal')
    p.add_argument('action',choices=['plan','issue','recover','deploy','renew','status','enable','disable'])
    p.add_argument('--stack-root',type=Path,required=True);p.add_argument('--project',required=True)
    p.add_argument('--origin');p.add_argument('--email');p.add_argument('--expected-ip',action='append',default=[])
    p.add_argument('--environment',choices=list(DIRECTORIES),default='staging')
    p.add_argument('--accept-ca-terms',action='store_true');p.add_argument('--expect-plan')


def folder(stack,project):
    identifier(project)
    path=stack.root/'data/acme'/project
    if path.resolve()!=path:raise RuntimeFault('ACME_DIRECTORY_UNSAFE')
    return path


def load(path):
    private_file(path)
    return json.loads(path.read_text())


def client_digest():
    try:info=CLIENT.lstat()
    except FileNotFoundError:raise RuntimeFault("ACME_TRUSTED_CERTBOT_REQUIRED") from None
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022:
        raise RuntimeFault('ACME_TRUSTED_CERTBOT_REQUIRED')
    return hashlib.sha256(CLIENT.read_bytes()).hexdigest()


def plan(stack,project,public_origin,email,ips,environment):
    identifier(project);workspace.origin(public_origin)
    host=urlsplit(public_origin).hostname
    if not re.fullmatch(r'(?=.{1,253}$)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?',host) or '.' not in host:
        raise RuntimeFault('ACME_PUBLIC_DOMAIN_REQUIRED')
    try:ipaddress.ip_address(host)
    except ValueError:pass
    else:raise RuntimeFault('ACME_DOMAIN_NOT_IP_REQUIRED')
    if not isinstance(email,str) or not re.fullmatch(r'[^\s@\x00-\x1f]{1,128}@[^\s@\x00-\x1f]{1,128}\.[a-zA-Z]{2,63}',email):
        raise RuntimeFault('ACME_EMAIL_REQUIRED')
    expected=sorted(set(str(ipaddress.ip_address(v)) for v in ips))
    if not expected or not all(ipaddress.ip_address(v).is_global and ipaddress.ip_address(v).version==4 for v in expected):raise RuntimeFault('ACME_PUBLIC_IPV4_REQUIRED')
    if environment not in DIRECTORIES:raise RuntimeFault('ACME_ENVIRONMENT_INVALID')
    return {'schema':1,'project':project,'origin':public_origin,'email':email,'expected_ips':expected,
            'environment':environment,'stack_instance':stack.config['instance'],
            'client_sha256':client_digest(),'challenge':'http-01-standalone','challenge_port':80}


def check_context(stack,value):
    if plan(stack,value['project'],value['origin'],value['email'],value['expected_ips'],value['environment'])!=value:
        raise RuntimeFault('ACME_CONTEXT_CHANGED')


def check_network(value):
    host=urlsplit(value['origin']).hostname
    addresses=sorted(set(row[4][0] for row in socket.getaddrinfo(host,80,type=socket.SOCK_STREAM)))
    if addresses!=value['expected_ips']:raise RuntimeFault('ACME_DNS_TARGET_CHANGED')
    # Never stop an existing web server to make room for the challenge.
    with socket.socket() as sock:
        try:sock.bind(('0.0.0.0',80))
        except OSError:raise RuntimeFault('ACME_PORT_80_IN_USE') from None


def copy_accounts(source,target):
    if not source.exists():return
    if source.resolve()!=source or not source.is_dir():raise RuntimeFault('ACME_ACCOUNT_PATH_UNSAFE')
    total=0
    for path in sorted(source.rglob('*')):
        info=path.lstat();relative=path.relative_to(source);out=target/relative
        if stat.S_ISDIR(info.st_mode):out.mkdir(parents=True,exist_ok=True,mode=0o700)
        elif stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_uid==0 and info.st_size<=128*1024:
            total+=info.st_size
            if total>2*1024*1024:raise RuntimeFault('ACME_ACCOUNT_TOO_LARGE')
            out.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            if out.is_symlink():raise RuntimeFault('ACME_ACCOUNT_PATH_UNSAFE')
            out.write_bytes(path.read_bytes());out.chmod(0o600)
        else:raise RuntimeFault('ACME_ACCOUNT_PATH_UNSAFE')


def command(value,job):
    host=urlsplit(value['origin']).hostname
    return [str(CLIENT),'certonly','--config','/dev/null','--non-interactive','--agree-tos',
            '--email',value['email'],'--standalone','--preferred-challenges','http-01',
            '--http-01-address','0.0.0.0','--server',DIRECTORIES[value['environment']],
            '--config-dir',str(job/'config'),'--work-dir',str(job/'work'),'--logs-dir',str(job/'logs'),
            '--cert-name','workspace','--key-type','ecdsa','--no-directory-hooks','-d',host]


def job_path(stack,receipt):
    name=receipt['job']
    if not re.fullmatch(r'job-[a-z0-9_]{8}',name):raise RuntimeFault('ACME_JOB_INVALID')
    path=stack.root/'acme-work'/name
    if path.resolve()!=path:raise RuntimeFault('ACME_JOB_UNSAFE')
    return path


def collect(stack,receipt):
    value=receipt['plan'];check_context(stack,value)
    root=folder(stack,value['project'])/value['environment'];job=job_path(stack,receipt)
    archive=job/'config/archive/workspace'
    gathered={}
    for name in ('fullchain','privkey'):
        link=job/'config/live/workspace'/(name+'.pem')
        target=link.resolve(strict=True)
        if target.parent!=archive or not re.fullmatch(name+r'\d+\.pem',target.name):raise RuntimeFault('ACME_CERTIFICATE_PATH_UNSAFE')
        info=target.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=0 or not 0<info.st_size<=128*1024:raise RuntimeFault('ACME_CERTIFICATE_FILE_UNSAFE')
        gathered[name]=target.read_bytes()
    # Normalize Certbot links into bounded private regular files.
    cert,key=job/'certificate.pem',job/'key.pem'
    for path,data in ((cert,gathered['fullchain']),(key,gathered['privkey'])):
        if path.is_symlink():raise RuntimeFault('ACME_CERTIFICATE_PATH_UNSAFE')
        path.write_bytes(data);path.chmod(0o600)
    workspace.certificate(cert,key,urlsplit(value['origin']).hostname)
    copy_accounts(job/'config/accounts',root/'accounts')
    for source,name in ((cert,'certificate.pem'),(key,'key.pem')):
        target=root/name
        if target.is_symlink():raise RuntimeFault('ACME_CERTIFICATE_PATH_UNSAFE')
        target.write_bytes(source.read_bytes());target.chmod(0o600)
    receipt.update(status='issued',certificate_sha256=hashlib.sha256(cert.read_bytes()).hexdigest(),
                   completed_at=datetime.now(timezone.utc).isoformat())
    write_json(root/'receipt.json',receipt)
    return {'status':'issued','environment':value['environment'],'project':value['project'],
            'certificate':str(root/'certificate.pem'),'private_key':str(root/'key.pem'),
            'external_https_verified':False}


def issue(stack,value,*,accept_terms=False,runner=run,network_check=check_network):
    if not accept_terms:raise RuntimeFault('ACME_TERMS_ACCEPTANCE_REQUIRED')
    check_context(stack,value);root=folder(stack,value['project'])/value['environment']
    receiptpath=root/'receipt.json'
    if receiptpath.exists() and load(receiptpath)['status']=='in_flight':raise RuntimeFault('ACME_UNKNOWN_ORDER_RECOVER_REQUIRED')
    if value['environment']=='production':
        stagepath=folder(stack,value['project'])/'staging/receipt.json'
        if not stagepath.exists():raise RuntimeFault('ACME_MATCHING_STAGING_REQUIRED')
        staging=load(stagepath)
        expected={**value,'environment':'staging'}
        if staging['status']!='issued' or staging['plan']!=expected:raise RuntimeFault('ACME_MATCHING_STAGING_REQUIRED')
    network_check(value)
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    work=stack.root/'acme-work';work.mkdir(exist_ok=True,mode=0o700)
    if work.resolve()!=work:raise RuntimeFault('ACME_JOB_UNSAFE')
    job=Path(tempfile.mkdtemp(prefix='job-',dir=work))
    for part in ('config','work','logs'):(job/part).mkdir(mode=0o700)
    copy_accounts(root/'accounts',job/'config/accounts')
    receipt={'status':'in_flight','plan':value,'job':job.name,'terms_accepted':True,
             'started_at':datetime.now(timezone.utc).isoformat()}
    write_json(receiptpath,receipt)
    # On failure preserve the fence and private diagnostics; never auto-submit twice.
    runner(command(value,job),timeout=180)
    return collect(stack,receipt)


def recover(stack,project,environment):
    receipt=load(folder(stack,project)/environment/'receipt.json')
    if receipt['status']!='in_flight':raise RuntimeFault('ACME_NO_PENDING_ORDER')
    return collect(stack,receipt)


def deploy(stack,project):
    root=folder(stack,project)/'production'
    if not (root/'receipt.json').exists():raise RuntimeFault('ACME_ISSUED_CERTIFICATE_REQUIRED')
    receipt=load(root/'receipt.json')
    if receipt['status']!='issued':raise RuntimeFault('ACME_ISSUED_CERTIFICATE_REQUIRED')
    value=receipt['plan'];check_context(stack,value)
    cert,key=root/'certificate.pem',root/'key.pem'
    if hashlib.sha256(cert.read_bytes()).hexdigest()!=receipt['certificate_sha256']:raise RuntimeFault('ACME_CERTIFICATE_CHANGED')
    current=workspace.directory(stack,project)/'workspace.json'
    if current.exists():
        if load(current)['origin']!=value['origin']:raise RuntimeFault('ACME_WORKSPACE_ORIGIN_CHANGED')
        tlsplan=workspace_tls.plan(stack,project,cert,key)
        return workspace_tls.apply(stack,tlsplan,cert,key)
    return workspace.apply(stack,workspace.plan(stack,project,value['origin'],cert,key),cert,key)


def _renew(stack,project,*,issuer=issue,deployer=deploy):
    root=folder(stack,project)/'production';receipt=load(root/'receipt.json');check_context(stack,receipt['plan'])
    if receipt['status']!='issued':raise RuntimeFault('ACME_UNKNOWN_ORDER_RECOVER_REQUIRED')
    if workspace.status(stack,project)['status']!='running':raise RuntimeFault('ACME_RUNNING_WORKSPACE_REQUIRED')
    state=workspace_tls.status(stack,project)
    if state.get('recovery_required'):raise RuntimeFault('TLS_RECOVERY_REQUIRED')
    if not state['renewal_due']:return {'status':'not_due','project':project}
    # If an earlier issuance succeeded but deployment failed, reuse that exact
    # certificate instead of paying the CA rate-limit cost of another order.
    current=state['certificate']['sha256']
    if current==receipt['certificate_sha256']:
        issuer(stack,receipt['plan'],accept_terms=receipt['terms_accepted'])
    result=deployer(stack,project)
    write_json(root/'renewal.json',{'status':'renewed','at':datetime.now(timezone.utc).isoformat()})
    return result


def renew(stack,project,*,issuer=issue,deployer=deploy):
    root=folder(stack,project)/'production'
    try:
        result=_renew(stack,project,issuer=issuer,deployer=deployer)
        write_json(root/'renewal.json',{'status':result.get('status','completed'),'at':datetime.now(timezone.utc).isoformat()})
        return result
    except Exception as error:
        # Only fixed fault codes, never provider output, addresses or secrets.
        code=str(error) if isinstance(error,RuntimeFault) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,100}',str(error)) else 'ACME_RENEWAL_FAILED'
        if root.is_dir():write_json(root/'renewal.json',{'status':'failed','error':code,'at':datetime.now(timezone.utc).isoformat()})
        raise


def unit_name(stack,project):
    identifier(project)
    return 'vf-acme-'+hashlib.sha256((str(stack.root)+stack.config['instance']+project).encode()).hexdigest()[:20]


def unit_text(stack,project):
    # systemd is not a shell; reject percent expansion and unsafe executable paths.
    executable=str(Path(sys.executable).absolute())
    if not all(re.fullmatch(r'/[A-Za-z0-9_./-]+',p) for p in (executable,str(stack.root))):raise RuntimeFault('ACME_SERVICE_PATH_UNSAFE')
    return ('[Unit]\nDescription=Video Factory workspace certificate renewal\nAfter=network-online.target\nWants=network-online.target\n\n'
            '[Service]\nType=oneshot\nUMask=0077\nTimeoutStartSec=300\nNoNewPrivileges=true\n'
            f'ExecStart={executable} -m video_factory.workspace_acme renew --stack-root {stack.root} --project {project}\n')


def schedule(stack,project,enable,*,units=Path('/etc/systemd/system'),runner=run):
    name=unit_name(stack,project);root=folder(stack,project)
    service=unit_text(stack,project)
    timer='[Unit]\nDescription=Video Factory daily certificate check\n\n[Timer]\nOnCalendar=daily\nRandomizedDelaySec=12h\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n'
    if enable:
        receipt=load(root/'production/receipt.json');check_context(stack,receipt['plan'])
        if receipt['status']!='issued':raise RuntimeFault('ACME_ISSUED_CERTIFICATE_REQUIRED')
        _,current=workspace_tls.current(stack,project)
        if current['origin']!=receipt['plan']['origin']:raise RuntimeFault('ACME_WORKSPACE_ORIGIN_CHANGED')
    for suffix,text in (('service',service),('timer',timer)):
        path=units/(name+'.'+suffix)
        if path.exists() or path.is_symlink():
            if path.is_symlink() or path.read_text()!=text:raise RuntimeFault('ACME_SYSTEMD_UNIT_CHANGED')
        elif enable:path.write_text(text);path.chmod(0o644)
    runner(['systemctl','daemon-reload'])
    runner(['systemctl','enable','--now',name+'.timer'] if enable else ['systemctl','disable','--now',name+'.timer'])
    if enable:runner(['systemctl','is-active','--quiet',name+'.timer'])
    write_json(root/'schedule.json',{'enabled':enable,'unit':name,'stack_instance':stack.config['instance']})
    return {'scheduled':enable,'project':project,'unit':name+'.timer'}


def status(stack,project):
    root=folder(stack,project);result={'project':project,'environments':{},'external_https_verified':False}
    for name in DIRECTORIES:
        path=root/name/'receipt.json'
        if path.exists():
            receipt=load(path)
            try:check_context(stack,receipt['plan']);changed=False
            except Exception:changed=True
            result['environments'][name]={'status':receipt['status'],'requires_context_review':changed}
    path=root/'schedule.json'
    result['schedule']=load(path) if path.exists() else {'enabled':False}
    result['schedule']['active']=False
    if result['schedule']['enabled'] and result['schedule']['stack_instance']==stack.config['instance']:
        try:
            run(['systemctl','is-active','--quiet',result['schedule']['unit']+'.timer'])
            result['schedule']['active']=True
        except RuntimeFault:pass
    renewal=root/'production/renewal.json'
    result['last_renewal']=load(renewal) if renewal.exists() else None
    return result


def cli(args):
    try:
        stack=Stack(args.stack_root)
        with stack.lock():
            if args.action in ('plan','issue'):
                value=plan(stack,args.project,args.origin,args.email,args.expected_ip,args.environment)
                result={'plan':value,'plan_sha256':fingerprint(value)}
                if args.action=='issue':
                    if args.expect_plan!=fingerprint(value):raise RuntimeFault('ACME_REVIEWED_PLAN_REQUIRED')
                    result=issue(stack,value,accept_terms=args.accept_ca_terms)
            elif args.action=='recover':result=recover(stack,args.project,args.environment)
            elif args.action=='deploy':result=deploy(stack,args.project)
            elif args.action=='renew':result=renew(stack,args.project)
            elif args.action in ('enable','disable'):result=schedule(stack,args.project,args.action=='enable')
            else:result=status(stack,args.project)
        print(json.dumps(result));return 0
    except Exception as error:
        code=str(error) if isinstance(error,RuntimeFault) else 'ACME_OPERATION_FAILED_CHECK_PRIVATE_STATUS'
        print(json.dumps({'error':code}));return 2


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();commands=parser.add_subparsers(dest='command',required=True)
    register(commands)
    # systemd uses this module directly while operators use vfctl.
    raise SystemExit(cli(parser.parse_args(['workspace-acme',*sys.argv[1:]])))
