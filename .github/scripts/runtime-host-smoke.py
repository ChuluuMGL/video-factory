"""Cloud-only installed-wheel bootstrap, process restart and independent restore."""
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import Request,urlopen


def call(command,body=None):
    result=subprocess.run(command,input=json.dumps(body).encode() if body else None,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=240)
    if result.returncode:
        raise RuntimeError('CHILD_FAILED: '+result.stdout.decode()[-1000:])
    return json.loads(result.stdout)


def wait_health(port,proc):
    for _ in range(100):
        if proc.poll() is not None:raise RuntimeError('SERVER_EXITED')
        try:
            with urlopen(f'http://127.0.0.1:{port}/healthz',timeout=.2) as r:return json.load(r)
        except OSError:time.sleep(.05)
    raise RuntimeError('SERVER_NOT_READY')


with tempfile.TemporaryDirectory() as temp:
    root=Path(temp)
    wheelpaths=sorted(Path(sys.argv[1]).glob('*.whl'))+sorted(Path(sys.argv[2]).glob('*.whl'))
    wheels=[{'name':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'data':base64.b64encode(p.read_bytes()).decode()} for p in wheelpaths]
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    destination=root/'customer'
    payload={'action':'install','root':str(destination),'deployment':'ci-customer','port':port,'password':'cloud-fixture-password','wheels':wheels}
    from video_factory import host_bootstrap
    command=[sys.executable,str(Path(host_bootstrap.__file__))]
    first=call(command,payload)
    second=call(command,payload)
    assert first['installed'] and second['installed']
    vf=destination/'venv/bin/vfctl'; data=destination/'data'
    password=root/'password';password.write_text(payload['password']);password.chmod(0o600)
    token=root/'token'
    call([str(vf),'runtime','login','--root',str(data),'--password-file',str(password),'--output',str(token)])
    def post(port,path,body):
        request=Request(f'http://127.0.0.1:{port}'+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+token.read_text()})
        with urlopen(request,timeout=5) as response:return json.load(response)
    for restart in range(2):
        proc=subprocess.Popen([str(vf),'runtime','serve','--root',str(data),'--port',str(port)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            wait_health(port,proc)
            if restart==0:
                post(port,'/v1/projects',{'project':'brand','configuration':{'video_route':'deferred','credential_ref':'secret:fixture','billing_owner':'customer'}})
                post(port,'/v1/tasks',{'project':'brand','task':'one','payload':{'sku_id':'SKU1','script':'fixture','source_revision':'1'}})
                post(port,'/v1/reviews',{'event':'evt','project':'brand','task':'one','revision':1,'stage':'script','decision':'accept'})
            assert post(port,'/v1/tasks/read',{'project':'brand','task':'one'})['versions'][0]['state']=='ready'
        finally:
            proc.terminate();proc.wait(timeout=10)
    backupkey=root/'backup-key'
    call([str(vf),'runtime','keygen','--root',str(data),'--output',str(backupkey)])
    archive=root/'ledger.vfb'
    call([str(vf),'runtime','backup','--root',str(data),'--backup-key-file',str(backupkey),'--output',str(archive)])
    restored=root/'restored';restored.mkdir(mode=0o700)
    receipt=call([str(vf),'runtime','restore','--root',str(restored),'--backup-key-file',str(backupkey),'--source',str(archive)])
    assert receipt['doctor']['task_states']=={'ready':1}
    assert receipt['sessions_revoked']
    print(json.dumps({'status':'PASS','scope':'cloud_linux_native_ledger_bootstrap','install_and_resume':True,'server_process_restarts':1,'independent_directory_restore':True,'ssh_transport':'not_run','systemd_boot':'not_run','n8n':'not_installed','provider_requests':0,'human_acceptance':'not_run'}))
