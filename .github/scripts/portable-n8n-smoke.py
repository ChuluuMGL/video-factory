"""Cloud CI only. Real n8n; synthetic Feishu/model endpoints, no paid calls."""
import http.cookiejar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
for stream in (sys.stdout,sys.stderr):
    if hasattr(stream,'reconfigure'): stream.reconfigure(encoding='utf-8')
import tempfile
import threading
import time
import uuid
import base64
from urllib.parse import urlsplit, parse_qs
from urllib.error import HTTPError
from urllib.request import build_opener, HTTPCookieProcessor, Request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'skills/video-factory-setup/scripts'))
from portable_n8n import API, environment, install_local, local_control, provision as provision_disabled, verify_setup, ensure_ledger
from n8n_workflows import probe_workflow, production_workflow, schema_workflow, FIELDS, guard_workflow, recovery_workflow

state = {'status': '请求脚本', 'script': '', 'model_calls': 0, 'token_calls': 0, 'writes': 0, 'video_calls':0, 'polls':0, 'fields':[], 'job':'','fail_writeback':False,'feedback':'','edit_during_model':False}
image_bytes=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a7V0AAAAASUVORK5CYII=')
def task_fields():
    return {'执行编号':state.get('execution'),'状态':state['status'],'商品资料':'A synthetic product',
            '脚本':state['script'],'脚本历史':state.get('history',''),'视频链接':state.get('video_link'),
            '审核意见':state['feedback'],'视频任务ID':state['job'],
            '参考图链接':state.get('references','https://media.example.invalid/reference.png'),
            '参考图附件':state.get('attachments',[]),'生成计划':state.get('plan',''),'费用确认编号':state.get('confirmation','')}
class Fixture(BaseHTTPRequestHandler):
    protocol_version="HTTP/1.1"
    def log_message(self, *_): pass
    def reply(self, body):
        raw=json.dumps(body).encode(); self.send_response(200)
        self.send_header('Content-Type','application/json'); self.send_header('Content-Length', str(len(raw)))
        self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        if '/open-apis/drive/v1/medias/' in self.path:
            assert self.headers['Authorization']=='Bearer synthetic-token'
            extra=json.loads(parse_qs(urlsplit(self.path).query)['extra'][0])
            field=next(f for f in state['fields'] if f['field_name']=='参考图附件')
            assert extra=={'bitablePerm':{'tableId':'tableFixture','attachments':{field['field_id']:{'recFixture':['fileFixture']}}}}
            raw=b'x'*len(image_bytes) if state.get('bad_image') else image_bytes
            self.send_response(403 if state.get('deny_image') else 200)
            self.send_header('Content-Type','image/png');self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
        elif '/fields?' in self.path:
            self.reply({'code':0,'data':{'items':state['fields'],'has_more':False}})
        elif '/v2/query/video_generation/' in self.path:
            state['polls']+=1
            if state.get('edit_during_poll'): state['status']='视频退回'
            self.reply({'task':{'id':state['job'],'model':'MiniMax-H3','status':'succeeded','content':{'url':'https://media.example.invalid/synthetic.mp4'}}})
        elif self.path.endswith('/recFixture'):
            self.reply({'code':0,'data':{'record':{'record_id':'recFixture','fields':task_fields()}}})
        else:
            self.reply({'code':0,'data':{'items':[]}})
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
        if self.path.endswith('/tenant_access_token/internal'):
            assert body == {'app_id':'cli_fixture','app_secret':'synthetic-feishu-secret'}, body.keys()
            state['token_calls']+=1; self.reply({'code':0,'tenant_access_token':'synthetic-token'})
        elif self.path.endswith('/fields'):
            field={**body,'field_id':'fld'+uuid.uuid4().hex}
            if not state.get('ack_without_field_write'): state['fields'].append(field)
            self.reply({'code':0,'data':{'field':field}})
        elif self.path.endswith('/v2/video_generation'):
            assert body['model']=='MiniMax-H3' and len(body['content'])==1+(len(state.get('attachments',[])) or 1)
            if state.get('attachments'):
                assert all(item['image_url']['url']=='data:image/png;base64,'+base64.b64encode(image_bytes).decode() for item in body['content'][1:])
            assert self.headers['Authorization']=='Bearer synthetic-model-key'
            state['video_calls']+=1
            self.reply({'task_id':'123456789012345'})
        elif '/search?' in self.path:
            assert self.headers['Authorization']=='Bearer synthetic-token'
            items=[]
            if state['status'] in [c['value'][0] for c in body['filter']['conditions']]:
                items=[{'record_id':'recFixture','fields':task_fields()}]
            self.reply({'code':0,'data':{'items':items,'has_more':False}})
        elif self.path.endswith('/chat/completions'):
            assert self.headers['Authorization']=='Bearer synthetic-model-key'
            state['model_calls']+=1
            if state['edit_during_model']: state['feedback']='New reviewer feedback'
            self.reply({'choices':[{'message':{'content':'Synthetic reviewable script'},'finish_reason':'stop'}]})
        else: raise AssertionError('unknown fixture route')
    def do_PUT(self):
        assert self.headers['Authorization']=='Bearer synthetic-token'
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if '/fields/' in self.path:
            field=next(f for f in state['fields'] if f['field_id']==self.path.rsplit('/',1)[1])
            field.update(body)
            self.reply({'code':0,'data':{'field':field}}); return
        fields=body['fields']
        state['writes']+=1
        if state['fail_writeback'] and '脚本' in fields:
            self.reply({'code':999,'msg':'synthetic writeback failure'}); return
        if state.get('ack_without_record_write') and '脚本' in fields:
            self.reply({'code':0,'data':{'record':{'record_id':'recFixture','fields':fields}}}); return
        if '生成计划' in fields: state['plan']=fields['生成计划']
        if '费用确认编号' in fields: state['confirmation']=fields['费用确认编号']
        if '状态' in fields: state['status']=fields['状态']
        if '脚本' in fields: state['script']=fields['脚本']
        if '脚本历史' in fields: state['history']=fields['脚本历史']
        if '视频链接' in fields: state['video_link']=fields['视频链接']
        if '执行编号' in fields: state['execution']=fields['执行编号']
        if '视频任务ID' in fields: state['job']=fields['视频任务ID']
        self.reply({'code':0,'data':{'record':{'record_id':'recFixture','fields':fields}}})

class FixtureServer(ThreadingHTTPServer):
    request_queue_size=128

fixture=FixtureServer(('0.0.0.0',0),Fixture)
threading.Thread(target=fixture.serve_forever,daemon=True).start()
mode=sys.argv[1] if len(sys.argv)>1 else 'node'
# Use the runner-provided scratch volume, not Windows' OS temp volume.
root=Path(tempfile.mkdtemp(prefix='vf 测试 n8n-',dir=os.environ.get('RUNNER_TEMP')))
process=None
phase='install'
try:
    print('Installing isolated '+mode+' n8n',flush=True)
    install_local(root,mode,15678,managed_node=mode=='node')
    assert install_local(root,mode,15678)['installed']
    if mode=='docker':
        compose=json.loads((root/'compose.json').read_text(encoding='utf-8'))
        compose['services']['n8n']['extra_hosts']=['host.docker.internal:host-gateway']
        (root/'compose.json').write_text(json.dumps(compose))
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
    for _ in range(60):
        try:
            request('/rest/settings')
            break
        except (ValueError, OSError): time.sleep(1)
    phase='fixture owner'
    request('/rest/owner/setup',{'email':'fixture@example.invalid','firstName':'CI','lastName':'Fixture','password':'Synthetic-Only-Pass123!'})
    scopes=request('/rest/api-keys/scopes')
    if isinstance(scopes,dict): scopes=scopes.get('scopes',[])
    key=request('/rest/api-keys',{'label':'Synthetic CI','expiresAt':None,'scopes':scopes})['rawApiKey']
    api=API(api_origin,key)
    fixture_origin=f'http://127.0.0.1:{fixture.server_port}'
    if mode=='docker':
        fixture_origin=f'http://host.docker.internal:{fixture.server_port}'
    credentials={'httpCustomAuth':{'type':'httpCustomAuth','data':{'json':json.dumps({'body':{'app_id':'cli_fixture','app_secret':'synthetic-feishu-secret'}})}}}
    # Execute against the actual server, where n8n exposes DataTable proxies.
    # CLI `n8n execute` lacks that module. These hooks exist only in the synthetic
    # CI fixture; the product does not expose a paid-model webhook.
    hooks={}
    def provision(api, bindings, project, workflow, credentials):
        workflow=json.loads(json.dumps(workflow))
        workflow['settings'].update(saveDataSuccessExecution='all',saveDataErrorExecution='all')
        path='ci-'+project
        hook={'id':'ci-hook','name':'CI synthetic trigger','type':'n8n-nodes-base.webhook',
              'typeVersion':2,'webhookId':str(uuid.uuid5(uuid.NAMESPACE_URL,path)),
              'position':[0,200],'parameters':{'httpMethod':'POST','path':path,
              'responseMode':'lastNode','authentication':'headerAuth','options':{}},
              'credentials':{'httpHeaderAuth':{'id':'UNBOUND','name':'UNBOUND'}}}
        start=workflow['nodes'][0]['name']
        workflow['nodes'].append(hook)
        workflow['connections'][hook['name']]=workflow['connections'][start]
        credentials={**credentials,'httpHeaderAuth':{'type':'httpHeaderAuth',
                     'data':{'name':'Authorization','value':'Bearer synthetic-model-key'}}}
        if any(n.get('credentials',{}).get('httpHeaderAuth',{}).get('id')=='RELAY_UNBOUND' for n in workflow['nodes']):
            credentials['relay']={'type':'httpHeaderAuth','data':{'name':'X-VF-Event-Relay','value':'synthetic-distinct-relay-token'}}
        saved=provision_disabled(api,bindings,project,workflow,credentials)
        hooks[saved['workflow']]=path
        return saved
    # Prove connection does not take over unrelated existing workflows.
    unrelated=api.request('POST','/api/v1/workflows',{
        'name':'Existing unrelated user workflow','nodes':[
            {'id':'existing-manual','name':'Existing manual trigger','type':'n8n-nodes-base.manualTrigger','typeVersion':1,'position':[0,0],'parameters':{}}],
        'connections':{},'settings':{'executionOrder':'v1'}})
    unrelated_before=api.request('GET','/api/v1/workflows/'+unrelated['id'])
    phase='public api import'
    workflow=probe_workflow('baseFixture','tableFixture',feishu_origin=fixture_origin)
    saved=provision(api,root/'bindings','probe',workflow,credentials)
    assert provision(api,root/'bindings','probe',workflow,credentials)==saved
    def execute(workflow_id, expect_failure=False, body=None):
        before=api.request('GET','/api/v1/executions?workflowId='+workflow_id+'&limit=1')['data']
        previous=before[0]['id'] if before else None
        try:
            api.request('POST','/api/v1/workflows/'+workflow_id+'/activate',{})
            req=Request(api_origin+'/webhook/'+hooks[workflow_id],method='POST',data=json.dumps(body or {}).encode(),
                        headers={'Authorization':'Bearer synthetic-model-key','Content-Type':'application/json'})
            for attempt in range(5):
                try:
                    with opener.open(req,timeout=180) as response: response.read(65536)
                    break
                except HTTPError as error:
                    if error.code==404 and attempt<4:
                        time.sleep(2); continue
                    print('Synthetic webhook diagnostic: '+error.read(1600).decode(errors='replace'),flush=True)
                    if error.code==404: raise AssertionError('SYNTHETIC_WEBHOOK_NOT_REGISTERED') from None
                    # An empty successful workflow may return HTTP 500 with
                    # 'No item to return'. Assert the execution receipt below.
                    break
            for _ in range(30):
                entries=api.request('GET','/api/v1/executions?workflowId='+workflow_id+'&limit=1')['data']
                if entries and entries[0]['id']!=previous and entries[0]['status'] in ('success','error','crashed'):
                    break
                time.sleep(1)
            else: raise AssertionError('EXECUTION_RECEIPT_NOT_FOUND')
            expected='error' if expect_failure else 'success'
            if entries[0]['status']!=expected:
                detail=api.request('GET','/api/v1/executions/'+entries[0]['id']+'?includeData=true')
                result=detail.get('data',{}).get('resultData',{})
                error=result.get('error',{})
                print('Synthetic execution diagnostic: '+json.dumps({'node':result.get('lastNodeExecuted'),'message':error.get('message')}),flush=True)
                raise AssertionError('EXECUTION_STATUS_'+entries[0]['status']+'_EXPECTED_'+expected)
        finally:
            api.request('POST','/api/v1/workflows/'+workflow_id+'/deactivate',{})
            assert not api.request('GET','/api/v1/workflows/'+workflow_id)['active']
    phase='probe execution'
    execute(saved['workflow'])
    assert state['token_calls']==1
    phase='schema prepare and repeat'
    state['fields']=[{'field_id':'fldStatus','field_name':'状态','type':3,
                      'property':{'options':[{'id':'optExisting','name':'Existing customer option','color':2}]}},
                     {'field_id':'fldPreserved','field_name':'Existing customer field','type':1}]
    schema=provision(api,root/'bindings','schema',schema_workflow('baseFixture','tableFixture',feishu_origin=fixture_origin),credentials)
    execute(schema['workflow'])
    assert len(state['fields'])==len(FIELDS)+1,state['fields']
    preserved=json.loads(json.dumps(state['fields']))
    execute(schema['workflow'])
    assert state['fields']==preserved
    assert state['fields'][0]['property']['options'][0]=={'id':'optExisting','name':'Existing customer option','color':2}
    phase='schema acknowledgement without persisted field is rejected'
    missing=next(f for f in state['fields'] if f['field_name']=='脚本历史')
    state['fields'].remove(missing); state['ack_without_field_write']=True
    execute(schema['workflow'],expect_failure=True)
    state['ack_without_field_write']=False
    execute(schema['workflow'])
    assert len(state['fields'])==len(FIELDS)+1
    credentials['httpHeaderAuth']={'type':'httpHeaderAuth','data':{'name':'Authorization','value':'Bearer synthetic-model-key'}}
    ledger=ensure_ledger(api,root/'bindings','fixture','baseFixture','tableFixture')
    workflow=production_workflow('baseFixture','tableFixture','script',feishu_origin=fixture_origin,script_origin=fixture_origin)
    workflow=guard_workflow(workflow,ledger)
    saved=provision(api,root/'bindings','script',workflow,credentials)
    phase='script execution'
    execute(saved['workflow'])
    assert state['model_calls']==1 and state['status']=='脚本待审核' and state['script']=='Synthetic reviewable script',state
    phase='sequential replay'
    execute(saved['workflow'])
    assert state['model_calls']==1,state
    phase='video submit and poll'
    submit=provision(api,root/'bindings','video-submit',guard_workflow(production_workflow('baseFixture','tableFixture','video-submit',feishu_origin=fixture_origin,video_origin=fixture_origin),ledger),credentials)
    poll=provision(api,root/'bindings','video-poll',production_workflow('baseFixture','tableFixture','video-poll',feishu_origin=fixture_origin,video_origin=fixture_origin),credentials)
    state['status']='脚本通过'
    execute(submit['workflow'])
    assert state['video_calls']==0, 'SCRIPT_REVIEW_IS_NOT_BILLING_AUTHORIZATION'
    state['status']='视频生成已确认'
    execute(submit['workflow'])
    assert state['video_calls']==1 and state['status']=='视频生成中'
    execute(submit['workflow'])
    assert state['video_calls']==1
    execute(poll['workflow'])
    assert state['polls']==1 and state['status']=='视频待审核'
    execute(poll['workflow'])
    assert state['video_calls']==1 and state['polls']==1
    phase='preserve human review during video query'
    state['status']='视频生成中'; state['edit_during_poll']=True
    execute(poll['workflow'],expect_failure=True)
    assert state['status']=='视频退回' and state['video_calls']==1
    state['edit_during_poll']=False
    phase='Feishu attachment video references and download failures before provider'
    state['status']='视频生成已确认';state['references']=''
    state['attachments']=[{'file_token':'fileFixture','name':'reference.png','size':len(image_bytes)}]
    for failure in ['deny_image','bad_image']:
        state[failure]=True
        execute(submit['workflow'],expect_failure=True)
        assert state['video_calls']==1 and state['status']=='视频生成已确认'
        rows=api.request('GET','/api/v1/data-tables/'+ledger['table']+'/rows')['data']
        assert rows[0]['status']=='idle','REFERENCE_FAILURE_MUST_NOT_CLAIM_PAID_EXECUTION'
        state[failure]=False
    execute(submit['workflow'])
    assert state['video_calls']==2 and state['status']=='视频生成中'
    execute(submit['workflow'])
    assert state['video_calls']==2
    execute(poll['workflow'])
    assert state['status']=='视频待审核'
    state['status']='视频生成已确认';state['attachments']*=2
    execute(submit['workflow'])
    assert state['video_calls']==3 and state['status']=='视频生成中','MULTIPLE_ATTACHMENTS_MUST_MAKE_ONE_MODEL_CALL'
    execute(poll['workflow'])
    assert state['status']=='视频待审核'
    state['attachments']=[];state['references']='https://media.example.invalid/reference.png'
    phase='writeback outage and no-charge recovery'
    recovery=provision(api,root/'bindings','recover',recovery_workflow('baseFixture','tableFixture',ledger,feishu_origin=fixture_origin),{'httpCustomAuth':credentials['httpCustomAuth']})
    state['status']='请求脚本'; state['fail_writeback']=True
    execute(saved['workflow'],expect_failure=True)
    assert state['model_calls']==2 and state['status']=='脚本生成中'
    rows=api.request('GET','/api/v1/data-tables/'+ledger['table']+'/rows')['data']
    assert rows[0]['status']=='result_ready' and 'Synthetic reviewable script' in rows[0]['result']
    state['status']='请求脚本'
    execute(saved['workflow'])
    assert state['model_calls']==2, 'LOCKED_RESULT_MUST_NOT_RESUBMIT'
    state['status']='脚本生成中'; state['fail_writeback']=False
    execute(recovery['workflow'])
    assert state['model_calls']==2 and state['status']=='脚本待审核'
    phase='preserve concurrent human edit'
    state['status']='请求脚本'; state['edit_during_model']=True
    execute(saved['workflow'],expect_failure=True)
    assert state['model_calls']==3 and state['status']=='脚本生成中' and state['feedback']=='New reviewer feedback'
    execute(recovery['workflow'],expect_failure=True)
    assert state['model_calls']==3 and state['feedback']=='New reviewer feedback'
    # Synthetic fixture explicitly restores its original input to exercise recovery.
    state['feedback']=''; state['edit_during_model']=False
    execute(recovery['workflow'])
    assert state['status']=='脚本待审核' and state['model_calls']==3
    phase='successful write response without persisted result preserves recovery lock'
    state['status']='请求脚本';state['ack_without_record_write']=True
    execute(saved['workflow'],expect_failure=True)
    assert state['model_calls']==4 and state['status']=='脚本生成中'
    rows=api.request('GET','/api/v1/data-tables/'+ledger['table']+'/rows')['data']
    assert rows[0]['status']=='result_ready'
    execute(recovery['workflow'],expect_failure=True)
    assert state['model_calls']==4 and state['status']=='脚本生成中'
    rows=api.request('GET','/api/v1/data-tables/'+ledger['table']+'/rows')['data']
    assert rows[0]['status']=='result_ready','ACK_ALONE_MUST_NOT_RELEASE_SAVED_RESULT'
    state['ack_without_record_write']=False
    execute(recovery['workflow'])
    assert state['model_calls']==4 and state['status']=='脚本待审核'
    phase='atomic concurrent claims'
    from concurrent.futures import ThreadPoolExecutor
    def claim(index):
        return api.request('PATCH','/api/v1/data-tables/'+ledger['table']+'/rows/update',{
            'filter':{'type':'and','filters':[{'columnName':'id','condition':'eq','value':ledger['row']},{'columnName':'status','condition':'eq','value':'idle'}]},
            'data':{'status':'busy','owner':'contender-'+str(index)},'returnData':True})
    with ThreadPoolExecutor(max_workers=6) as pool:
        claims=list(pool.map(claim,range(6)))
    assert sum(len(rows) for rows in claims)==1,claims
    all_workflows=api.request('GET','/api/v1/workflows')['data']
    assert len(all_workflows)==7 and all(not w['active'] for w in all_workflows)
    assert api.request('GET','/api/v1/workflows/'+unrelated['id'])==unrelated_before
    for receipt in (root/'bindings').glob('*.json'):
        assert 'synthetic-feishu-secret' not in receipt.read_text(encoding='utf-8') and key not in receipt.read_text(encoding='utf-8') and 'synthetic-model-key' not in receipt.read_text(encoding='utf-8')
    phase='temporary authenticated setup hook'
    report=verify_setup(api,root/'bindings','temporary-probe',probe_workflow('baseFixture','tableFixture',feishu_origin=fixture_origin),{'httpCustomAuth':credentials['httpCustomAuth']})
    assert report['result']['base_read']=='verified',report
    assert not api.request('GET','/api/v1/workflows/'+report['workflow'])['active']
    report=verify_setup(api,root/'bindings','temporary-schema',schema_workflow('baseFixture','tableFixture',feishu_origin=fixture_origin),{'httpCustomAuth':credentials['httpCustomAuth']})
    assert not api.request('GET','/api/v1/workflows/'+report['workflow'])['active']
    phase='one-session project bundle and credential reuse'
    from portable_n8n import project_credentials,configure_flows
    def fixture_secret(prompt):
        return 'synthetic-feishu-secret' if 'App Secret' in prompt else 'synthetic-model-key'
    pool=project_credentials(api,root/'bindings','bundle','cli_fixture',['feishu','script','video'],fixture_secret)
    assert project_credentials(api,root/'bindings','bundle','cli_fixture',['feishu','script','video'],lambda _: (_ for _ in ()).throw(AssertionError('REPEATED_KEY_PROMPT')))==pool
    report=verify_setup(api,root/'bindings','bundle-probe',probe_workflow('baseFixture','tableFixture',feishu_origin=fixture_origin),{'httpCustomAuth':pool['feishu']})
    assert report['result']['base_read']=='verified'
    bundle=configure_flows(api,root/'bindings','bundle','baseFixture','tableFixture',pool,feishu_origin=fixture_origin,script_origin=fixture_origin,video_origin=fixture_origin)
    assert bundle==configure_flows(api,root/'bindings','bundle','baseFixture','tableFixture',pool,feishu_origin=fixture_origin,script_origin=fixture_origin,video_origin=fixture_origin)
    assert set(bundle)=={'script','video-submit','video-poll','recover'}
    for flow,item in bundle.items():
        imported=api.request('GET','/api/v1/workflows/'+item['workflow'])
        assert not imported['active']
        for node in imported['nodes']:
            for kind,ref in node.get('credentials',{}).items():
                role='feishu' if kind=='httpCustomAuth' else 'script' if flow=='script' else 'video'
                assert ref==pool[role]['existing']
    assert state['model_calls']==4 and state['video_calls']==3, 'SETUP_MUST_NOT_CALL_MODELS'
    for receipt in (root/'bindings').glob('*.json'):
        assert 'synthetic-feishu-secret' not in receipt.read_text(encoding='utf-8') and 'synthetic-model-key' not in receipt.read_text(encoding='utf-8')
    phase='guided setup and interrupted-session continuation'
    from unittest.mock import patch
    import portable_n8n as portable
    def guided_secret(prompt):
        guided_prompts.append(prompt)
        return key if 'n8n API Key' in prompt else fixture_secret(prompt)
    def guided_bundle(*args,**kwargs):
        return configure_flows(*args,**kwargs,feishu_origin=fixture_origin,script_origin=fixture_origin,video_origin=fixture_origin)
    guided_prompts=[]
    with patch('portable_n8n.secret',side_effect=guided_secret), patch('portable_n8n.configure_flows',side_effect=guided_bundle), patch('n8n_workflows.probe_workflow',side_effect=lambda b,t:probe_workflow(b,t,feishu_origin=fixture_origin)), patch('n8n_workflows.schema_workflow',side_effect=lambda b,t:schema_workflow(b,t,feishu_origin=fixture_origin)):
        with patch('builtins.input',side_effect=['1',api_origin,'guided','baseFixture','tableFixture','cli_fixture','y','y','y']):
            first=portable.welcome(root/'guided-setup')
        assert len(guided_prompts)==4,guided_prompts
        guided_prompts.clear()
        with patch('builtins.input',side_effect=['1',api_origin,'guided','n','y','y']):
            resumed=portable.welcome(root/'guided-setup')
        assert len(guided_prompts)==1 and 'n8n API Key' in guided_prompts[0],guided_prompts
        assert first['flows']==resumed['flows']
        assert not resumed['production_ready']
    assert state['model_calls']==4 and state['video_calls']==3,'GUIDED_SETUP_MUST_NOT_CALL_MODELS'
    phase='designated billing owner and exact plan confirmation'
    from portable_approval import plan_workflow,confirmation_workflow
    approval=ensure_ledger(api,root/'bindings','owner-approval','baseFixture','tableFixture')
    owner_execution=ensure_ledger(api,root/'bindings','owner-execution','baseFixture','tableFixture')
    policy={'app':'cli_fixture','tenant':'tenant_fixture','billing_owner':'ou_owner','billing_account':'test-account','configuration':'fixture-v1'}
    owner_plan=provision(api,root/'bindings','owner-plan',plan_workflow('baseFixture','tableFixture','script',approval,policy,feishu_origin=fixture_origin),{'httpCustomAuth':credentials['httpCustomAuth']})
    owner_flow=provision(api,root/'bindings','owner-confirm',confirmation_workflow('baseFixture','tableFixture','script',owner_execution,approval,policy,'owner-confirm',feishu_origin=fixture_origin,script_origin=fixture_origin),credentials)
    state['status']='申请脚本计划'
    execute(owner_plan['workflow'])
    assert state['status']=='费用待确认' and state['model_calls']==4
    protected=api.request('GET','/api/v1/data-tables/'+approval['table']+'/rows')['data'][0]
    plan=json.loads(protected['payload'])['plan'];assert protected['status']=='awaiting'
    field=next(f['field_id'] for f in state['fields'] if f['field_name']=='费用确认编号')
    def confirmation_event(actor='ou_owner',token=None):
        return {'schema':'2.0','header':{'event_id':'event-fixture','app_id':'cli_fixture','tenant_key':'tenant_fixture','event_type':'drive.file.bitable_record_changed_v1'},
                'event':{'file_type':'bitable','file_token':'baseFixture','table_id':'tableFixture','operator_id':{'open_id':actor},
                         'action_list':[{'action':'record_edited','record_id':'recFixture','after_value':[{'field_id':field,'field_value':json.dumps(token or plan['id'])}]}]}}
    state['confirmation']=plan['id']
    for bad in [confirmation_event('ou_other'),confirmation_event(token='vf-stale-plan')]:
        execute(owner_flow['workflow'],True,bad)
        assert state['model_calls']==4 and state['status']=='费用待确认'
    original_script=state['script'];state['script']='Changed after plan'
    execute(owner_flow['workflow'],True,confirmation_event())
    assert state['model_calls']==4
    state['script']=original_script
    state['plan']+=' edited'
    execute(owner_flow['workflow'],True,confirmation_event())
    assert state['model_calls']==4
    state['plan']=json.loads(protected['payload'])['display']
    execute(owner_flow['workflow'],False,confirmation_event())
    assert state['model_calls']==5 and state['status']=='脚本待审核'
    execute(owner_flow['workflow'],False,confirmation_event())
    assert state['model_calls']==5,'APPROVAL_REPLAY_MUST_NOT_CALL_MODEL'
    assert api.request('GET','/api/v1/data-tables/'+approval['table']+'/rows')['data'][0]['status']=='idle'
    assert api.request('GET','/api/v1/data-tables/'+owner_execution['table']+'/rows')['data'][0]['status']=='idle'
    phase='stop and restart'
    local_control(root,'stop')
    local_control(root,'start')
    for _ in range(30):
        try:
            assert api.request('GET','/api/v1/workflows/'+saved['workflow'])['id']==saved['workflow']
            break
        except Exception: time.sleep(1)
    else: raise AssertionError('RESTART_DID_NOT_PRESERVE_WORKFLOW')
    print(json.dumps({'platform':sys.platform,'mode':mode,'real_n8n':True,'install_resume':'pass','api_import_readback':'pass','feishu_credential_binding':'pass','script_writeback':'pass','schema_resume':'pass','durable_writeback_recovery':'pass','atomic_claim':'pass','video_submit_poll':'pass','script_approval_not_payment':'pass','sequential_duplicate':'blocked','paid_calls':0,'real_feishu':'not_run','real_cloud_saas':'not_run','designated_owner_plan_gate':'pass'}),flush=True)
except Exception as error:
    if phase=='install' and isinstance(error,subprocess.TimeoutExpired):
        print((error.output or b'').decode(errors='replace')[-5000:],flush=True)
    if phase=='install' and hasattr(error,'diagnostics'):
        print(error.diagnostics.decode(errors='replace')[-4000:],flush=True)
    if phase=='start' and (root/'n8n-local.log').exists():
        print((root/'n8n-local.log').read_text(errors='replace')[-5000:],flush=True)
    print(json.dumps({'phase':phase,'error_type':type(error).__name__,'message':str(error)[:300]}),flush=True)
    raise
finally:
    fixture.shutdown()
    if mode=='docker':
        try: local_control(root,'stop')
        except Exception: pass
    else:
        try: local_control(root,'stop')
        except Exception: pass
