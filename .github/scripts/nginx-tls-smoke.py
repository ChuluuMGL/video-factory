"""Repeat actual nginx certificate reload/rollback with strict trusted clients."""
import hashlib
import json
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import time
from unittest.mock import patch

sys.path.insert(0,str(Path('packages/video-factory-core/tests').resolve()))
from test_workspace_tls import WorkspaceTLSTests
from video_factory import workspace, workspace_tls as tls
from video_factory.stack import images, write_json
from video_factory.runtime_store import RuntimeFault

fixture=WorkspaceTLSTests('test_rotate_pair_once_validate_reload_and_readback_without_service_restart')
fixture.setUp()
name='vf-tls-'+fixture.stack.config['instance']
def docker(*args):
    return subprocess.check_output(['docker',*args],stderr=subprocess.PIPE,timeout=180)
def trusted(cert,port):
    context=ssl.create_default_context(cafile=str(cert))
    with socket.create_connection(('127.0.0.1',port),timeout=3) as raw:
        with context.wrap_socket(raw,server_hostname='review.example') as connection:
            connection.sendall(b'GET / HTTP/1.1\r\nHost: review.example\r\nConnection: close\r\n\r\n')
            assert connection.recv(4096).startswith(b'HTTP/1.1 200')
    time.sleep(.1)  # Stay below the product edge request-rate limit.
original_nginx=workspace.nginx
def standalone(value):
    return original_nginx(value).replace('proxy_pass http://workspace:8790;','return 200 "ready";')
def command(stack,project,*args,**kwargs):
    assert args[:3]==('exec','-T','edge')
    return docker('exec',name,*args[3:])
try:
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    value=workspace.plan(fixture.stack,fixture.project,'https://review.example:'+str(port),fixture.oldcert,fixture.oldkey)
    write_json(fixture.root/'workspace.json',value)
    write_json(fixture.root/'compose.json',workspace.document(fixture.stack,value))
    (fixture.root/'nginx.conf').write_text(standalone(value));(fixture.root/'nginx.conf').chmod(0o644)
    docker('pull',images()['gateway'])
    docker('run','-d','--name',name,'--user','10001:10001','--cap-drop','ALL',
           '-p','127.0.0.1:'+str(port)+':8443','-v',str(fixture.root/'nginx.conf')+':/etc/nginx/nginx.conf:ro',
           '-v',str(fixture.root/'tls')+':/tls:ro','--entrypoint','nginx',images()['gateway'],'-g','daemon off;')
    for attempt in range(30):
        try:trusted(fixture.oldcert,port);break
        except (OSError,ssl.SSLError):
            if attempt==29:raise
            time.sleep(.2)
    before=docker('inspect','--format','{{.State.StartedAt}}',name)
    current_cert,current_key=fixture.oldcert,fixture.oldkey
    with patch.object(workspace,'nginx',side_effect=standalone),patch.object(workspace,'compose',side_effect=command):
        for index in range(6):
            new_cert,new_key=fixture.certificate('rotate-'+str(index))
            tls.apply(fixture.stack,tls.plan(fixture.stack,fixture.project,new_cert,new_key),new_cert,new_key)
            for _ in range(4):trusted(new_cert,port)
            reads=[]
            def refuse_candidate(plan,expected):
                assert tls.served_leaf(plan,expected)
                reads.append(expected)
                return len(reads)>1
            try:tls.apply(fixture.stack,tls.plan(fixture.stack,fixture.project,current_cert,current_key),current_cert,current_key,readback=refuse_candidate)
            except RuntimeFault as error:assert str(error)=='TLS_ROTATION_ROLLED_BACK'
            else:raise AssertionError('ROLLBACK_NOT_EXERCISED')
            for _ in range(4):trusted(new_cert,port)
            current_cert,current_key=new_cert,new_key
    assert docker('inspect','--format','{{.State.StartedAt}}',name)==before
    print(json.dumps({'status':'PASS','rotations':6,'rollbacks':6,'strict_tls_requests':48,'container_restarted':False}))
finally:
    try:docker('rm','-f',name)
    finally:fixture.tearDown()
