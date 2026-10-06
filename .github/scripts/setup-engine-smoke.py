"""Cloud-only stopped-daemon check through the installed private Setup entry."""
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

from video_factory.stack import engine_ready

assert os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux' and os.getuid() == 0
engine_ready()
try:
    subprocess.run(['systemctl','stop','docker.service','docker.socket'],check=True,capture_output=True,timeout=90)
    with tempfile.TemporaryDirectory(prefix='vf-engine-ci-') as directory:
        root=Path(directory)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        command=[sys.executable,'-m','video_factory.cli','setup-run','--session',str(root/'setup.json'),
                 '--root',str(root/'stack'),'--wheelhouse',str(root/'wheels'),
                 '--browser-input','--input-port',str(port)]
        child=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            ready=json.loads(child.stdout.readline())
            assert ready['status']=='awaiting_human_input'
            url=urlsplit(ready['url']);origin=f'http://127.0.0.1:{port}'
            cookie='';csrf=''
            def request(path,body=None):
                connection=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
                headers={'Origin':origin,'Content-Type':'application/json','Cookie':cookie,'X-VF-CSRF':csrf}
                connection.request('GET' if body is None else 'POST',path,
                                   None if body is None else json.dumps(body),headers)
                response=connection.getresponse();value=json.loads(response.read());status=response.status
                response_headers=dict(response.getheaders());connection.close()
                assert status==200
                return value,response_headers
            _,headers=request('/api/unlock',{'key':url.fragment})
            cookie=headers['Set-Cookie'].split(';')[0]
            deadline=time.monotonic()+10
            while True:
                state,_=request('/api/prompt');csrf=state['csrf']
                assert state['prompt'] is None, 'Stopped daemon must not request customer input'
                if state['completion']:break
                assert time.monotonic()<deadline
                time.sleep(.1)
            assert state['completion']['failed']
            assert 'Docker' in state['completion']['message'] and '无需因此重置' in state['completion']['message']
            request('/api/ack',{})
            output,errors=child.communicate(timeout=10)
            receipt=json.loads(output)
            assert child.returncode==2 and receipt['error']=='DOCKER_ENGINE_UNAVAILABLE_CHECK_SERVICE'
            assert receipt['resume_same_command'] and not receipt['business_ready']
            assert not (root/'setup.json').exists() and not (root/'stack').exists()
            assert not errors
        finally:
            if child.poll() is None:child.terminate();child.communicate(timeout=10)
finally:
    subprocess.run(['systemctl','start','docker.service'],check=True,capture_output=True,timeout=90)
engine_ready()
print(json.dumps({'status':'PASS','scope':'installed_setup_with_real_stopped_docker',
                  'password_requested':False,'customer_configuration_created':False,
                  'safe_browser_receipt':True,'docker_recovered':True,'provider_requests':0}))
