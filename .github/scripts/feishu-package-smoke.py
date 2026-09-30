"""Cloud-only installed CLI: official-shaped loopback reads, repair and media QA.

No live tenant/model. In the container job the same flow uses real PostgreSQL.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from cryptography.fernet import Fernet
from video_factory.feishu_client import FeishuClient
from video_factory.runtime_store import RuntimeStore
from video_factory.postgres_store import PostgresStore
from video_factory.worker import Worker
from video_factory import automation
from video_factory.onboarding import SessionStore
from video_factory.setup_project import import_project

if len(sys.argv)>1 and sys.argv[1]=='rpc':
    # Fixture-only injection; the shipped CLI offers no origin override.
    FeishuClient.origin=sys.argv[2]
    from video_factory.cli import main
    raise SystemExit(main(sys.argv[3:]))

with tempfile.TemporaryDirectory(prefix='vf-feishu-') as temporary:
    root=Path(temporary);state=root/'state';state.mkdir(mode=0o700)
    postgres=bool(os.environ.get('VF_DATABASE_URL_FILE'))
    store=PostgresStore('/state') if postgres else RuntimeStore.install(state,'fixture','fixture-admin-password')
    token=store.login('admin','cloud-stack-fixture-password' if postgres else 'fixture-admin-password')['token']
    if postgres:state=Path('/state')
    project='fs_brand'
    setup_answers={'organization.id':'fixture_org','organization.name':'Synthetic customer','organization.admin_ref':'feishu:ou_owner',
        'deployment.id':'fixture','deployment.host':'fixture.invalid','deployment.ssh_user':'ubuntu','deployment.ssh_identity_ref':'secret:fixture_ssh',
        'deployment.feishu_tenant':'fixture_tenant','deployment.feishu_credential_ref':'env:FIXTURE_FEISHU','deployment.n8n_mode':'bundled',
        'project.id':project,'project.name':'Synthetic brand','project.product_category':'Synthetic','project.target_market':'US',
        'project.language':'English','project.business_goal':'Fixture only','project.category_rule_source':'Synthetic specification',
        'project.base_mode':'bind','project.base_target':'bascnFixture','project.script_reviewer':'feishu:ou_script','project.video_reviewer':'feishu:ou_video',
        'project.products':[{'sku_id':'fixture_sku','name':'Fixture','variant':'one','truth_source':'Synthetic specification'}],
        'project.video_route':'deferred','project.spend_policy':'manual_per_run'}
    with store.connect() as db:
        deployment=db.execute("SELECT value FROM meta WHERE key='deployment'").fetchone()[0]
        existing=db.execute("SELECT value FROM meta WHERE key='setup:binding'").fetchone()
    setup_answers['deployment.id']=deployment
    if existing:
        for group,values in json.loads(existing[0]).items():
            for name,value in values.items():setup_answers[group+'.'+name]=value
    tenant=setup_answers['deployment.feishu_tenant']
    setup=SessionStore(root/'setup.json');setup.start();setup.answer(setup_answers,0)
    import_project(store,token,setup.read())
    user_tokens={'synthetic-submit-user':'ou_submitter','synthetic-script-user':'ou_script','synthetic-video-user':'ou_video'}
    for name,value in (('admin.token',token),('submit.token','synthetic-submit-user'),('script.token','synthetic-script-user'),('video.token','synthetic-video-user')):
        (root/name).write_text(value);(root/name).chmod(0o600)
    binding={'tenant_key':tenant,'base_token':'bascnFixture','table_id':'tblFixture',
             'fields':{'task':'fldTask','sku_id':'fldSkuId','script':'fldScript','source_revision':'fldSource'},
             'submitters':['ou_submitter'],'reviewers':['ou_script','ou_video']}
    config=root/'binding.json';config.write_text(json.dumps(binding));config.chmod(0o600)
    fields={'task':'repair_task','sku_id':'fixture_sku','script':'First script','source_revision':'source_one'}
    counts={'identity':0,'schema_pages':0,'records':0,'writes':0}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            auth=self.headers.get('Authorization','').removeprefix('Bearer ')
            assert auth in user_tokens
            if self.path=='/open-apis/authen/v1/user_info':
                counts['identity']+=1;data={'open_id':user_tokens[auth],'tenant_key':tenant,'name':'UNTRUSTED_DISPLAY_NAME'}
            elif '/fields?' in self.path:
                counts['schema_pages']+=1
                schema=[{'field_id':v,'field_name':k,'type':1} for k,v in binding['fields'].items()]
                second='page_token=next' in self.path
                data={'items':schema[2:] if second else schema[:2],'has_more':not second,'page_token':None if second else 'next'}
            elif '/records/recFixture?' in self.path:
                counts['records']+=1;data={'record':{'record_id':'recFixture','fields':copy.deepcopy(fields)}}
            else:raise AssertionError('UNEXPECTED_FIXTURE_ROUTE')
            raw=json.dumps({'code':0,'data':data}).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def do_POST(self):counts['writes']+=1;self.send_error(405)
        do_PUT=do_POST
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    origin='http://127.0.0.1:'+str(server.server_port);processes=[0]
    def cli(arguments,input_value=None,expected_error=None):
        processes[0]+=1
        command=[sys.executable,str(Path(__file__).resolve()),'rpc',origin,*arguments]
        result=subprocess.run(command,input=json.dumps(input_value).encode() if input_value is not None else None,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=40)
        if expected_error:
            assert result.returncode==2 and json.loads(result.stdout)['error']==expected_error
            return
        if result.returncode:
            diagnostic=result.stdout.decode(errors='replace')+result.stderr.decode(errors='replace')
            for secret in (token,*user_tokens):diagnostic=diagnostic.replace(secret,'<redacted>')
            raise AssertionError('FEISHU_CLI: '+diagnostic)
        return json.loads(result.stdout)
    def call(action,user='submit',*extra,expected_error=None):
        return cli(['feishu',action,'--root',str(state),'--project',project,'--user-token-file',str(root/(user+'.token')),*extra],expected_error=expected_error)
    def connect(action,*extra,input_value=None):
        command=['setup-feishu',action,'--session',str(root/'connection.json')]
        command+=['--setup-session',str(root/'setup.json'),'--json'] if action=='configure' else ['--root',str(state),'--token-file',str(root/'admin.token')]
        if action in ('plan','apply'):command+=['--user-token-file',str(root/'submit.token')]
        return cli([*command,*extra],input_value=input_value)
    def import_record(expected):
        args=['--record','recFixture','--expected-revision',str(expected)]
        plan=call('prepare-import','submit',*args)
        return call('import','submit',*args,'--expect-plan',plan['plan_sha256'])
    def review(revision,stage,decision,feedback=''):
        args=['--task','repair_task','--revision',str(revision),'--stage',stage,'--decision',decision,'--feedback',feedback]
        wrong='video' if stage=='script' else 'script'
        call('prepare-review',wrong,*args,expected_error='FEISHU_TENANT_OR_ROLE_DENIED')
        plan=call('prepare-review',stage,*args)
        result=call('review',stage,*args,'--event',f'{stage}_{revision}','--expect-plan',plan['plan_sha256'])
        replay=call('review',stage,*args,'--event',f'{stage}_{revision}','--expect-plan',plan['plan_sha256'])
        assert replay['replayed'] and result['actor']=='feishu:'+tenant+':ou_'+stage
        return result
    try:
        question=connect('configure')
        answers={'table_id':binding['table_id'],**binding['fields'],'submitters':['ou_submitter']}
        for name,value in answers.items():
            assert question['next_question']['field']==name
            assert question['next_question']['input_schema']['type']==('array' if name=='submitters' else 'string')
            question=connect('configure','--answers','-','--expect-revision',str(question['revision']),input_value={name:value})
        assert connect('configure')['status']=='connection_draft_ready'
        assert not connect('status')['binding_matches_draft']
        connection=connect('plan')
        assert connect('apply','--expect-plan',connection['plan_sha256'])['field_schema_verified']
        assert connect('status')['binding_matches_draft']
        assert import_record(0)['state']=='awaiting_script_review'
        assert review(1,'script','reject','Fix product wording')['state']=='rejected'
        fields.update(script='Corrected wording',source_revision='source_two')
        assert import_record(1)['revision']==2
        assert review(2,'script','accept')['state']=='ready'
        for name in ('assets','media'):(root/name).mkdir(mode=0o700)
        media=root/'source.mp4';reference=root/'assets/reference.png'
        subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=c=blue:s=432x768:r=24:d=5',
                        '-f','lavfi','-i','sine=frequency=440:duration=5','-c:v','libx264','-threads','1','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(media)],check=True)
        subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(media),'-frames:v','1','-threads','1',str(reference)],check=True)
        key=Path('/run/secrets/runtime_master').read_bytes() if postgres else Fernet.generate_key();store.put_secret(token,'fs_fixture','synthetic-provider-key',key)
        provider_calls=[0]
        class Provider:
            def __init__(self,region):pass
            def submit(self,body,secret):
                assert secret=='synthetic-provider-key' and body['model']=='MiniMax-H3'
                provider_calls[0]+=1;return str(42345678901230+provider_calls[0])
            def poll(self,provider_id,secret):return {'id':provider_id,'status':'succeeded','url':'https://cdn.hailuoai.com/fixture.mp4'}
            def download(self,url,stream):stream.write(media.read_bytes())
        worker=Worker(store,provider_factory=Provider)
        spec={'duration':5,'references':[{'path':'reference.png','sha256':hashlib.sha256(reference.read_bytes()).hexdigest()}]}
        def generate(revision):
            args={'project':project,'task':'repair_task','revision':revision,'assets_root':root/'assets','specification':spec,'credential_ref':'secret:fs_fixture','billing_owner':'fixture','region':'global'}
            plan=worker.prepare(token,**args);worker.approve(token,plan['request_plan_sha256'],**args)
            assert worker.step(token,project,'repair_task',revision,key,root/'media',allow_paid=True)['state']=='submitted'
            assert worker.step(token,project,'repair_task',revision,key,root/'media')['state']=='awaiting_video_review'
        generate(2);assert review(2,'video','reject','Change final shot')['state']=='rejected'
        fields.update(script='Corrected final shot',source_revision='source_three')
        assert import_record(2)['revision']==3
        assert review(3,'script','accept')['state']=='ready'
        generate(3);assert review(3,'video','accept')['state']=='accepted'
        history=store.inspect_task(token,project,'repair_task')
        assert len(history['versions'])==3 and len(history['reviews'])==5 and provider_calls[0]==2
        fields.update(task='queued_task',script='Pending script',source_revision='queue_one')
        import_record(0)
        issued=automation.issue(store,token,project,1)
        assert automation.queue(store,issued['token'],project)['items']==[{'id':'queued_task','revision':1,'state':'awaiting_script_review'}]
        if postgres:
            output=state/'feishu-queue-token';output.write_text(issued['token']);output.chmod(0o600)
            output=state/'feishu-connection.json';output.write_bytes((root/'connection.json').read_bytes());output.chmod(0o600)
        with store.connect() as db:
            saved='\n'.join(row['value'] for row in db.execute('SELECT value FROM meta').fetchall())
            assert all(raw not in saved for raw in user_tokens) and issued['token'] not in saved
        assert counts['writes']==0
        print(json.dumps({'status':'PASS','backend':'postgresql' if postgres else 'sqlite','scope':'installed_cli_with_loopback_feishu_and_synthetic_provider',
            'cli_processes':processes[0],'feishu_fixture_reads':counts,'task_revisions':3,'review_events':5,'fixture_submissions':2,
            'full_media_decode':'PASS','queue_project':project,'raw_tokens_in_ledger':False,
            'setup_connection_questions_and_apply':'PASS','scoped_reviewer_denials':5,
            'paid_model_requests':0,'real_feishu_acceptance':'not_run','human_acceptance':'not_run'}))
    finally:server.shutdown();server.server_close()
