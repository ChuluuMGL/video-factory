"""Regenerated per-project Compose companion for a persistent HTTPS workspace.

It shares only the customer's DB/media and uses a separate allowlisted relay.
No public administrator or n8n port, no Docker socket in an application container.
"""
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
from urllib.parse import urlsplit
from urllib.request import build_opener, ProxyHandler, HTTPSHandler, Request

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from .runtime_store import RuntimeFault, canonical, fingerprint, private_file, identifier
from .stack import Stack, compose_document, run, write_json, local_engine
from .workspace_http import origin


def register(commands):
    p = commands.add_parser('workspace', help='persistent HTTPS employee workspace, separate from administrator access')
    p.add_argument('action', choices=['plan','apply','status','stop'])
    p.add_argument('--stack-root', type=Path, required=True)
    p.add_argument('--project', required=True)
    p.add_argument('--origin')
    p.add_argument('--certificate', type=Path)
    p.add_argument('--private-key', type=Path)
    p.add_argument('--expect-plan')


def certificate(cert, key, host):
    for path in (cert, key):
        if not path or not path.is_absolute() or path.resolve() != path: raise RuntimeFault('TLS_PRIVATE_FILE_REQUIRED')
        private_file(path)
        if path.stat().st_size > 128*1024: raise RuntimeFault('TLS_FILE_TOO_LARGE')
    raw, keyraw = cert.read_bytes(), key.read_bytes()
    parsed = x509.load_pem_x509_certificate(raw)
    private = serialization.load_pem_private_key(keyraw, password=None)
    public = lambda value: value.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if public(parsed.public_key()) != public(private.public_key()): raise RuntimeFault('TLS_KEY_MISMATCH')
    names = parsed.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    # Require exact SAN to avoid accidentally deploying a certificate for another host.
    if host not in names.get_values_for_type(x509.DNSName): raise RuntimeFault('TLS_HOST_MISMATCH')
    now = datetime.now(timezone.utc)
    if parsed.not_valid_before_utc > now or parsed.not_valid_after_utc < now+timedelta(days=7):
        raise RuntimeFault('TLS_VALIDITY_UNDER_SEVEN_DAYS')
    return raw, keyraw


def plan(stack, project, public_origin, cert, key):
    identifier(project); origin(public_origin)
    raw, keyraw = certificate(cert, key, urlsplit(public_origin).hostname)
    value = {'schema':1, 'project':project, 'origin':public_origin, 'stack_instance':stack.config['instance'],
             'runtime_image':stack.config['runtime_image'], 'certificate_sha256':hashlib.sha256(raw).hexdigest(),
             'private_key_sha256':hashlib.sha256(keyraw).hexdigest()}
    return value


def directory(stack, project):
    identifier(project)
    return stack.root/'data/workspaces'/project


def document(stack, value):
    project=value['project']; identifier(project); origin(value['origin'])
    root=directory(stack, project)
    prefix='vf-'+stack.config['deployment']+'-'+stack.config['instance']
    base=compose_document(stack.config)
    runtime=base['services']['runtime']; gateway=base['services']['gateway']; relay=base['services']['egress']
    common={'restart':'unless-stopped','read_only':True,'cap_drop':['ALL'],'init':True,
            'security_opt':['no-new-privileges:true'],'logging':{'driver':'json-file','options':{'max-size':'10m','max-file':'3'}},
            'pull_policy':'never','tmpfs':['/tmp:rw,noexec,nosuid,size=64m']}
    mount=lambda part: str(stack.root/part)
    workspace={**common,'image':stack.config['runtime_image'],'user':'10001:10001','networks':['ledger','review'],
               'environment':runtime['environment']|{'VF_WORKER_EGRESS':'1'},'secrets':['runtime_dsn','runtime_master'],
               'entrypoint':['python','-m','video_factory.workspace_http'], 'command':['--project',project,'--origin',value['origin']],
               'volumes':[mount('data/runtime')+':/state',mount('data/media')+':/media:ro',mount('data/worker')+':/work:ro'],
               'healthcheck':{'test':['CMD','python','-c',"from urllib.request import Request,urlopen; import sys; r=urlopen(Request('http://127.0.0.1:8790/healthz',headers={'Host':sys.argv[1]}),timeout=3); assert r.status==200",urlsplit(value['origin']).netloc],'interval':'5s','timeout':'5s','retries':12}}
    edge={**common,'image':gateway['image'],'user':'10001:10001','networks':['review','public'],
          'ports':[str(urlsplit(value['origin']).port or 443)+':8443'],
          'entrypoint':['nginx','-g','daemon off;'],
          'volumes':[str(root/'nginx.conf')+':/etc/nginx/nginx.conf:ro',str(root/'tls')+':/tls:ro'],
          'depends_on':{'workspace':{'condition':'service_healthy'}},
          'healthcheck':{'test':['CMD','wget','-q','-O','/dev/null','http://127.0.0.1:8082/healthz'],'interval':'2s','timeout':'3s','retries':30}}
    egress={**common,'image':relay['image'],'user':'10001:10001','networks':['review','outbound'],
            'entrypoint':['python','-m','video_factory.worker_egress'],'command':['--persistent'],
            'healthcheck':relay['healthcheck'],'pids_limit':64,'mem_limit':'128m'}
    executor={**workspace,'entrypoint':['python','-m','video_factory.dispatch'],'command':['--project',project],
              'networks':{'ledger':{'aliases':['vf-executor-'+hashlib.sha256(project.encode()).hexdigest()[:12]]},'review':{}},
              'volumes':[mount('data/runtime')+':/state',mount('data/media')+':/media',mount('data/worker')+':/work:ro'],
              'healthcheck':{'test':['CMD','python','-c',"from urllib.request import urlopen; assert urlopen('http://127.0.0.1:8793/healthz',timeout=3).status==200"],'interval':'5s','timeout':'5s','retries':12}}
    events={**workspace,'entrypoint':['python','-m','video_factory.feishu_native_events'],'command':['--project',project],
            'environment':workspace['environment']|{'HTTPS_PROXY':'http://egress:8443','https_proxy':'http://egress:8443'},
            'depends_on':{'egress':{'condition':'service_healthy'}}}
    events.pop('healthcheck')
    return {'name':prefix+'-ws-'+hashlib.sha256(project.encode()).hexdigest()[:12],
            'services':{'workspace':workspace,'edge':edge,'egress':egress,'executor':executor,'events':events},
            'networks':{'ledger':{'external':True,'name':prefix+'_private'},'review':{'internal':True},'public':{},'outbound':{}},
            'secrets':{name:{'file':mount('secrets/'+name)} for name in ('runtime_dsn','runtime_master')}}


def nginx(value):
    host=urlsplit(value['origin']).netloc
    return '''pid /tmp/nginx.pid;
error_log /dev/stderr warn;
events { worker_connections 128; }
http {
 access_log off;
 client_body_temp_path /tmp/body; proxy_temp_path /tmp/proxy;
 fastcgi_temp_path /tmp/fastcgi; uwsgi_temp_path /tmp/uwsgi; scgi_temp_path /tmp/scgi;
 limit_req_zone $binary_remote_addr zone=requests:1m rate=5r/s;
 limit_conn_zone $binary_remote_addr zone=connections:1m;
 server { listen 127.0.0.1:8082; location = /healthz { return 200 "ready"; } }
 server {
  listen 8443 ssl; server_name HOST;
  ssl_certificate /tls/certificate.pem; ssl_certificate_key /tls/key.pem;
  ssl_protocols TLSv1.2 TLSv1.3;
  client_max_body_size 64k; client_body_timeout 10s; keepalive_timeout 15s;
  limit_req zone=requests burst=20 nodelay; limit_conn connections 12;
  location / {
   proxy_pass http://workspace:8790;
   proxy_set_header Host $http_host;
   proxy_set_header Connection "";
   proxy_read_timeout 30s; proxy_buffering off;
  }
 }
}
'''.replace('HOST',host)


def compose(stack, project, *args, data=None):
    root=directory(stack,project); private_file(root/'workspace.json')
    value=json.loads((root/'workspace.json').read_text())
    if value['stack_instance'] != stack.config['instance'] or value['runtime_image'] != stack.config['runtime_image']:
        raise RuntimeFault('WORKSPACE_REAPPLY_AFTER_STACK_CHANGE')
    expected=document(stack,value)
    if json.loads((root/'compose.json').read_text()) != expected or (root/'nginx.conf').read_text()!=nginx(value):
        raise RuntimeFault('WORKSPACE_GENERATED_FILES_CHANGED')
    local_engine()
    return run(['docker','compose','--project-directory',str(root),'-f',str(root/'compose.json'),*args],timeout=180,data=data)


def stop_all(stack):
    base=stack.root/'data/workspaces'
    if base.exists():
        for path in sorted(base.iterdir()):
            if path.is_dir() and (path/'workspace.json').exists():
                value=json.loads((path/'workspace.json').read_text())
                # Restored copies carry old instance IDs. They must never stop
                # the source host's companion or block a backup before reapply.
                if value['stack_instance']!=stack.config['instance']:continue
                compose(stack,path.name,'down','--timeout','15')


def apply(stack, value, cert, key):
    project=value['project']; root=directory(stack,project)
    raw, keyraw=certificate(cert,key,urlsplit(value['origin']).hostname)
    if plan(stack,project,value['origin'],cert,key)!=value: raise RuntimeFault('WORKSPACE_PLAN_CHANGED')
    if not stack.status()['infrastructure_ready']: raise RuntimeFault('WORKSPACE_HEALTHY_STACK_REQUIRED')
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    if root.resolve()!=root: raise RuntimeFault('WORKSPACE_DIRECTORY_UNSAFE')
    if (root/'workspace.json').exists():
        previous=json.loads((root/'workspace.json').read_text())
        if previous['stack_instance']==stack.config['instance'] and previous['runtime_image']==stack.config['runtime_image']:
            compose(stack,project,'down','--timeout','15')
    tls=root/'tls';tls.mkdir(mode=0o700,exist_ok=True);os.chown(tls,10001,10001)
    for name,data in [('certificate.pem',raw),('key.pem',keyraw)]:
        path=tls/name
        if path.is_symlink(): raise RuntimeFault('TLS_FILE_UNSAFE')
        path.write_bytes(data);path.chmod(0o600);os.chown(path,10001,10001)
    write_json(root/'workspace.json',value)
    write_json(root/'compose.json',document(stack,value))
    (root/'nginx.conf').write_text(nginx(value));(root/'nginx.conf').chmod(0o644)
    compose(stack,project,'up','-d','--pull','never','--wait','--wait-timeout','90')
    return status(stack, project)


def status(stack, project):
    root=directory(stack, project)
    if not (root/'workspace.json').exists(): return {'status':'not_configured','project':project}
    value=json.loads((root/'workspace.json').read_text())
    raw=compose(stack,project,'ps','--all','--format','json').decode()
    rows=json.loads(raw) if raw.lstrip().startswith('[') else [json.loads(row) for row in raw.splitlines() if row]
    ready=len(rows)==5 and all(row['State']=='running' and row.get('Health','') in ('','healthy') for row in rows)
    return {'status':'running' if ready else 'incomplete', 'project':project,'url':value['origin'],
            'components':[{k:row.get(k) for k in ('Service','State','Health')} for row in rows],
            'https_external_readback':'required', 'human_acceptance':'not_run'}


def cli(args):
    try:
        stack=Stack(args.stack_root)
        with stack.lock():
            if args.action in ('plan','apply'):
                value=plan(stack,args.project,args.origin,args.certificate,args.private_key)
                result={'plan':value,'plan_sha256':fingerprint(value),'business_ready':False}
                if args.action=='apply':
                    if args.expect_plan != result['plan_sha256']: raise RuntimeFault('WORKSPACE_REVIEWED_PLAN_REQUIRED')
                    result=apply(stack,value,args.certificate,args.private_key)
            elif args.action=='stop':
                compose(stack,args.project,'down','--timeout','15');result={'status':'stopped','project':args.project}
            else: result=status(stack,args.project)
        print(json.dumps(result));return 0
    except Exception as error:
        code=str(error) if isinstance(error,RuntimeFault) else 'WORKSPACE_OPERATION_FAILED'
        print(json.dumps({'error':code,'business_ready':False}));return 2
