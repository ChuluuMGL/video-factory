"""Called by the cloud stack smoke after its synthetic Feishu profile exists."""
from datetime import datetime,timezone,timedelta
import http.client
import json
from pathlib import Path
import socket
import ssl
import subprocess
import sys
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from video_factory.workspace import plan,apply,compose,stop_all,status
from video_factory.stack import Stack
from video_factory.runtime_store import RuntimeFault


def certificate_files(root, prefix):
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME,'localhost')]);now=datetime.now(timezone.utc)
    cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=30)).add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]),False).sign(key,hashes.SHA256())
    c,k=root/(prefix+'-cert'),root/(prefix+'-key')
    c.write_bytes(cert.public_bytes(serialization.Encoding.PEM));k.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));c.chmod(0o600);k.chmod(0o600)
    return c,k


def smoke(stack,root,session):
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    c,k=certificate_files(root,'workspace-test')
    value=plan(stack,'fs_brand','https://localhost:'+str(port),c,k)
    context=ssl.create_default_context(cafile=str(c))
    def request(path,headers=None):
        conn=http.client.HTTPSConnection('localhost',port,context=context,timeout=5)
        conn.request('GET',path,headers=headers or {});r=conn.getresponse();raw=r.read();result=r.status,raw,dict(r.getheaders());conn.close();return result
    try:
        result=apply(stack,value,c,k);assert result['status']=='running',result
        code,raw,headers=request('/api/session');assert code==200,raw
        assert json.loads(raw)['persistent'] is True
        assert '__Host-vf_workspace=' in headers['Set-Cookie'] and 'Secure' in headers['Set-Cookie']
        assert request('/api/tasks')[0]==401
        assert request('/v1/login')[0]!=200
        assert request('/api/session',{'Host':'wrong.example'})[0]!=200
        assert request('/healthz')[0]==200
        compose(stack,'fs_brand','restart','workspace')
        compose(stack,'fs_brand','up','-d','--wait','--wait-timeout','90')
        assert request('/api/session')[0]==200
        # Add a second synthetic project in the real PostgreSQL stack, then
        # migrate the existing single entry to a portal without a second edge.
        stack.compose('exec','-T','runtime','python','-c',"""
from video_factory.runtime_cli import selected_store
from video_factory.feishu_bridge import meta,save
s=selected_store()('/state');a=s.login('admin','cloud-stack-fixture-password')['token']
s.put_project(a,'fs_secondary',{'video_route':'deferred','credential_ref':'secret:fixture','billing_owner':'fixture'})
with s.connect() as db:
    for prefix in ('setup:feishu-app:','feishu:binding:'):
        save(db,prefix+'fs_secondary',meta(db,prefix+'fs_brand'))
""")
        value=plan(stack,'fs_brand','https://localhost:'+str(port),c,k,['fs_brand','fs_secondary'])
        assert apply(stack,value,c,k)['status']=='running'
        info=json.loads(request('/api/session')[1]);assert info['portal'] and info['project']==''
        assert request('/api/projects')[0]==401 and request('/p/fs_secondary/api/tasks')[0]==401
        assert len(status(stack,'fs_brand')['components'])==5
        # Exercise the actual installed n8n HTTP node against the persistent
        # executor. The fixture has no newly approved paid tasks.
        from video_factory.dispatch import template
        from urllib.request import Request,urlopen
        def post(path,body,token=None):
            headers={'Content-Type':'application/json'}
            if token:headers['Authorization']='Bearer '+token
            req=Request('http://127.0.0.1:'+str(stack.config['runtime_port'])+path,data=json.dumps(body).encode(),headers=headers)
            with urlopen(req,timeout=10) as response:return json.load(response)
        admin=post('/v1/login',{'name':'admin','password':'cloud-stack-fixture-password'})['token']
        cap=post('/v1/automation/execution-keys',{'project':'fs_brand','ttl_hours':1},admin)
        draft=template('fs_brand','vfApprovedExecution');draft['id']='VfApprovedDispatch001'
        draft['nodes'][0].update(type='n8n-nodes-base.manualTrigger',typeVersion=1,parameters={})
        credentials=[{'id':'vfApprovedExecution','name':draft['nodes'][1]['credentials']['httpHeaderAuth']['name'],'type':'httpHeaderAuth','data':{'name':'Authorization','value':'Bearer '+cap['token']}}]
        for name,data,kind in [('dispatch-credentials',credentials,'credentials'),('dispatch-workflow',draft,'workflow')]:
            path=root/(name+'.json');path.write_text(json.dumps(data));path.chmod(0o644)
            stack.compose('cp',str(path),'n8n:/tmp/'+name+'.json')
            stack.compose('exec','-T','n8n','n8n','import:'+kind,'--input=/tmp/'+name+'.json')
        execution=subprocess.run(['docker','compose','--project-directory',str(stack.root),'-f',str(stack.root/'compose.json'),
            'run','--rm','--no-deps','-e','N8N_RUNNERS_BROKER_PORT=5689','n8n','execute','--id=VfApprovedDispatch001','--rawOutput'],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=180)
        if execution.returncode:
            # n8n may generate tokens absent from our secrets directory.
            # Preserve the failure code without archiving raw execution output.
            print(json.dumps({'stage':'n8n_approved_dispatch','returncode':execution.returncode}),file=sys.stderr)
            raise AssertionError('N8N_APPROVED_DISPATCH_EXECUTION_FAILED')
        output=execution.stdout.decode()
        assert '"idle"' in output and '"provider_requests": 0' in output, 'N8N_APPROVED_DISPATCH_FAILED'
        post('/v1/automation/revoke',{'key_id':cap['key_id']},admin)
        from video_factory.production_setup import schedule
        scheduled=schedule(stack,session,admin,activate=True)
        assert scheduled['status']=='published_restart_verified'
        renewed=schedule(stack,session,admin,activate=False)
        assert renewed['workflow_id']==scheduled['workflow_id'] and renewed['status']=='imported_disabled'
        assert renewed['key_id']!=scheduled['key_id']
        # Real nginx, mounted private generations, SNI readback and rollback.
        from video_factory import workspace_tls as tls
        def identities():
            ids=compose(stack,'fs_brand','ps','-q').decode().split()
            rows=json.loads(subprocess.check_output(['docker','inspect',*ids]))
            return sorted((row['Id'],row['State']['StartedAt']) for row in rows)
        before=identities()
        nextcert,nextkey=certificate_files(root,'workspace-renewed')
        rotation=tls.apply(stack,tls.plan(stack,'fs_brand',nextcert,nextkey),nextcert,nextkey)
        assert rotation['status']=='rotated'
        context=ssl.create_default_context(cafile=str(nextcert))
        assert request('/api/session')[0]==200
        assert identities()==before, 'TLS_ROTATION_RESTARTED_COMPONENT'
        reads=[]
        def refuse_first_readback(value, expected):
            # Both candidate and rollback must actually serve the expected leaf.
            assert tls.served_leaf(value,expected)
            reads.append(expected)
            return len(reads)>1
        try:
            tls.apply(stack,tls.plan(stack,'fs_brand',c,k),c,k,readback=refuse_first_readback)
        except RuntimeFault as error:assert str(error)=='TLS_ROTATION_ROLLED_BACK'
        else:raise AssertionError('TLS_ROLLBACK_NOT_EXERCISED')
        assert len(reads)==2 and request('/api/session')[0]==200
        assert identities()==before, 'TLS_ROLLBACK_RESTARTED_COMPONENT'
        current=tls.generation_target(tls.current(stack,'fs_brand')[0])
        # A real cold backup stops both public ingress and the DB consumers.
        from cryptography.fernet import Fernet
        backup=root/'workspace-complete.vfb';backup_key=Fernet.generate_key();stack.backup(backup,backup_key)
        with socket.socket() as s:assert s.connect_ex(('127.0.0.1',port))!=0
        restored=root/'workspace-restored';restored.mkdir(mode=0o700)
        recovered=Stack.restore(backup,restored,backup_key)
        pointer=restored/'data/workspaces/fs_brand/tls/current'
        assert pointer.is_symlink() and pointer.readlink().as_posix()==current
        assert json.loads((restored/'data/workspaces/fs_brand/workspace.json').read_text())['projects']==['fs_brand','fs_secondary']
        assert (pointer/'key.pem').read_bytes()==nextkey.read_bytes()
        assert (pointer/'certificate.pem').read_bytes()==nextcert.read_bytes()
        try:status(recovered,'fs_brand')
        except RuntimeFault as e:assert str(e)=='WORKSPACE_REAPPLY_AFTER_STACK_CHANGE'
        else:raise AssertionError('STALE_COMPANION_STARTED_AFTER_RESTORE')
        return {'status':'PASS','multi_project_manifest_restore':True,'single_edge_two_executors':True,'https_certificate_verified':True,'secure_cookie':True,'anonymous_and_bad_host_denied':True,'container_restart':'PASS','cold_backup_stops_ingress':'PASS','certificate_restore':'PASS','live_certificate_rotation':'PASS','live_certificate_rollback':'PASS','no_component_restart_on_rotation':True,'generation_restore':'PASS','n8n_approved_dispatch':'PASS','guided_scheduler_publish_renew_disable':'PASS','human_acceptance':'not_run'}
    except Exception:
        try:print('SERVICE_LOGS_OMITTED_FROM_CI_EVIDENCE',file=__import__('sys').stderr)
        except Exception:pass
        raise
    finally:stop_all(stack)
