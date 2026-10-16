"""Cloud CI only. Real n8n; synthetic Feishu/model endpoints, no paid calls."""
import http.cookiejar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from urllib.request import build_opener, HTTPCookieProcessor, Request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'skills/video-factory-setup/scripts'))
from portable_n8n import API, environment, install_local, local_control, provision
from n8n_workflows import probe_workflow, production_workflow

state = {'status': '请求脚本', 'script': '', 'model_calls': 0, 'token_calls': 0, 'writes': 0}
class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def reply(self, body):
        raw=json.dumps(body).encode(); self.send_response(200)
        self.send_header('Content-Type','application/json'); self.send_header('Content-Length', str(len(raw)))
        self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        if self.path.endswith('/recFixture'):
            self.reply({'code':0,'data':{'record':{'fields':{'执行编号':state.get('execution')}}}})
        else:
            self.reply({'code':0,'data':{'items':[]}})
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
        if self.path.endswith('/tenant_access_token/internal'):
            assert body == {'app_id':'cli_fixture','app_secret':'synthetic-feishu-secret'}, body.keys()
            state['token_calls']+=1; self.reply({'code':0,'tenant_access_token':'synthetic-token'})
        elif '/search?' in self.path:
            assert self.headers['Authorization']=='Bearer synthetic-token'
            items=[]
            if state['status']=='请求脚本':
                items=[{'record_id':'recFixture','fields':{'状态':state['status'],'商品资料':'A synthetic product','脚本':state['script']}}]
            self.reply({'code':0,'data':{'items':items,'has_more':False}})
        elif self.path.endswith('/chat/completions'):
            assert self.headers['Authorization']=='Bearer synthetic-model-key'
            state['model_calls']+=1
            self.reply({'choices':[{'message':{'content':'Synthetic reviewable script'}}]})
        else: raise AssertionError('unknown fixture route')
    def do_PUT(self):
        assert self.headers['Authorization']=='Bearer synthetic-token'
        fields=json.loads(self.rfile.read(int(self.headers['Content-Length'])))['fields']
        state['writes']+=1
        if '状态' in fields: state['status']=fields['状态']
        if '脚本' in fields: state['script']=fields['脚本']
        if '执行编号' in fields: state['execution']=fields['执行编号']
        self.reply({'code':0,'data':{'record':{'record_id':'recFixture','fields':fields}}})

fixture=ThreadingHTTPServer(('0.0.0.0',0),Fixture)
threading.Thread(target=fixture.serve_forever,daemon=True).start()
mode=sys.argv[1] if len(sys.argv)>1 else 'node'
root=Path(tempfile.mkdtemp(prefix='vf-n8n-ci-'))
process=None
phase='install'
try:
    print('Installing isolated '+mode+' n8n',flush=True)
    install_local(root,mode,15678)
    assert install_local(root,mode,15678)['installed']
    phase='start'
    print('Starting real n8n',flush=True)
    local_control(root,'start')
    api_origin='http://127.0.0.1:15678'
    # Only fixture bootstrap uses n8n's private owner UI API. The shipped
    # installer never creates/replaces users or consumes browser cookies.
    opener=build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def request(path,body=None):
        req=Request(api_origin+path, data=json.dumps(body).encode() if body is not None else None,
                    headers={'Content-Type':'application/json'})
        with opener.open(req,timeout=60) as response:return json.load(response)['data']
    phase='fixture owner'
    request('/rest/owner/setup',{'email':'fixture@example.invalid','firstName':'CI','lastName':'Fixture','password':'Synthetic-Only-Pass123!'})
    scopes=request('/rest/api-keys/scopes')
    if isinstance(scopes,dict): scopes=scopes.get('scopes',[])
    key=request('/rest/api-keys',{'label':'Synthetic CI','expiresAt':None,'scopes':scopes})['rawApiKey']
    api=API(api_origin,key)
    fixture_origin=f'http://127.0.0.1:{fixture.server_port}'
    if mode=='docker':
        # Linux Docker host-gateway is configured in this CI fixture only.
        compose=json.loads((root/'compose.json').read_text())
        compose['services']['n8n']['extra_hosts']=['host.docker.internal:host-gateway']
        (root/'compose.json').write_text(json.dumps(compose))
        local_control(root,'start')
        fixture_origin=f'http://host.docker.internal:{fixture.server_port}'
    credentials={'httpCustomAuth':{'type':'httpCustomAuth','data':{'json':json.dumps({'body':{'app_id':'cli_fixture','app_secret':'synthetic-feishu-secret'}})}}}
    phase='public api import'
    workflow=probe_workflow('baseFixture','tableFixture',feishu_origin=fixture_origin)
    saved=provision(api,root/'bindings','probe',workflow,credentials)
    assert provision(api,root/'bindings','probe',workflow,credentials)==saved
    def execute(workflow_id):
        if mode=='docker':
            saved_local=json.loads((root/'local.json').read_text())
            command=['docker','compose','-p',saved_local['project'],'-f','compose.json','exec','-T','n8n','n8n','execute','--id',workflow_id,'--rawOutput']
        else:
            import shutil
            command=[shutil.which('node'),str(root/'node_modules/n8n/bin/n8n'),'execute','--id',workflow_id,'--rawOutput']
        env=environment(root,15678)
        env['N8N_RUNNERS_BROKER_PORT']='15679'
        result=subprocess.run(command,cwd=root,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
        (root/'synthetic-execution.log').write_bytes(result.stdout)
        if result.returncode:
            # Synthetic fixture logs only; print node error, never API credentials.
            lines=result.stdout.decode(errors='replace').splitlines()
            errors=[line for line in lines if 'Error' in line or 'errorMessage' in line]
            print('Execution diagnostic: '+str(errors[-3:])[:1200],flush=True)
            raise AssertionError('N8N_EXECUTION_FAILED')
    phase='probe execution'
    execute(saved['workflow'])
    assert state['token_calls']==1
    credentials['httpHeaderAuth']={'type':'httpHeaderAuth','data':{'name':'Authorization','value':'Bearer synthetic-model-key'}}
    workflow=production_workflow('baseFixture','tableFixture','script',feishu_origin=fixture_origin,script_origin=fixture_origin)
    saved=provision(api,root/'bindings','script',workflow,credentials)
    phase='script execution'
    execute(saved['workflow'])
    assert state['model_calls']==1 and state['status']=='脚本待审核' and state['script']=='Synthetic reviewable script',state
    phase='sequential replay'
    execute(saved['workflow'])
    assert state['model_calls']==1,state
    all_workflows=api.request('GET','/api/v1/workflows')['data']
    assert len(all_workflows)==2 and all(not w['active'] for w in all_workflows)
    for receipt in (root/'bindings').glob('*.json'):
        assert 'synthetic-feishu-secret' not in receipt.read_text() and key not in receipt.read_text()
    print(json.dumps({'platform':sys.platform,'mode':mode,'real_n8n':True,'install_resume':'pass','api_import_readback':'pass','feishu_credential_binding':'pass','script_writeback':'pass','sequential_duplicate':'blocked','paid_calls':0,'real_feishu':'not_run','real_cloud_saas':'not_run'}),flush=True)
except Exception as error:
    print(json.dumps({'phase':phase,'error_type':type(error).__name__,'message':str(error)[:300]}),flush=True)
    raise
finally:
    fixture.shutdown()
    if mode=='docker':
        try: local_control(root,'stop')
        except Exception: pass
    else:
        saved=json.loads((root/'local.json').read_text()) if (root/'local.json').exists() else {}
        if saved.get('pid'):
            import signal
            try: os.kill(saved['pid'],signal.SIGTERM)
            except OSError: pass
