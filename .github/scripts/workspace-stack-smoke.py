"""Called by the cloud stack smoke after its synthetic Feishu profile exists."""
from datetime import datetime,timezone,timedelta
import http.client
import json
from pathlib import Path
import socket
import ssl
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from video_factory.workspace import plan,apply,compose,stop_all,status
from video_factory.stack import Stack
from video_factory.runtime_store import RuntimeFault


def smoke(stack,root):
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME,'localhost')]);now=datetime.now(timezone.utc)
    cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=30)).add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]),False).sign(key,hashes.SHA256())
    c,k=root/'workspace-test-cert',root/'workspace-test-key'
    c.write_bytes(cert.public_bytes(serialization.Encoding.PEM));k.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));c.chmod(0o600);k.chmod(0o600)
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
        credentials=[{'id':'vfApprovedExecution','name':'CI approved execution','type':'httpHeaderAuth','data':{'name':'Authorization','value':'Bearer '+cap['token']}}]
        for name,data,kind in [('dispatch-workflow',draft,'workflow'),('dispatch-credentials',credentials,'credentials')]:
            path=root/(name+'.json');path.write_text(json.dumps(data));path.chmod(0o644)
            stack.compose('cp',str(path),'n8n:/tmp/'+name+'.json')
            stack.compose('exec','-T','n8n','n8n','import:'+kind,'--input=/tmp/'+name+'.json')
        output=stack.compose('run','--rm','--no-deps','-e','N8N_RUNNERS_BROKER_PORT=5689','n8n','execute','--id=VfApprovedDispatch001','--rawOutput',timeout=180).decode()
        assert '"idle"' in output and '"provider_requests": 0' in output, 'N8N_APPROVED_DISPATCH_FAILED'
        post('/v1/automation/revoke',{'key_id':cap['key_id']},admin)
        # A real cold backup stops both public ingress and the DB consumers.
        from cryptography.fernet import Fernet
        backup=root/'workspace-complete.vfb';backup_key=Fernet.generate_key();stack.backup(backup,backup_key)
        with socket.socket() as s:assert s.connect_ex(('127.0.0.1',port))!=0
        restored=root/'workspace-restored';restored.mkdir(mode=0o700)
        recovered=Stack.restore(backup,restored,backup_key)
        assert (restored/'data/workspaces/fs_brand/tls/key.pem').read_bytes()==k.read_bytes()
        try:status(recovered,'fs_brand')
        except RuntimeFault as e:assert str(e)=='WORKSPACE_REAPPLY_AFTER_STACK_CHANGE'
        else:raise AssertionError('STALE_COMPANION_STARTED_AFTER_RESTORE')
        return {'status':'PASS','https_certificate_verified':True,'secure_cookie':True,'anonymous_and_bad_host_denied':True,'container_restart':'PASS','cold_backup_stops_ingress':'PASS','certificate_restore':'PASS','n8n_approved_dispatch':'PASS','human_acceptance':'not_run'}
    except Exception:
        try:print(compose(stack,'fs_brand','logs','--no-color','--tail','20').decode(),file=__import__('sys').stderr)
        except Exception:pass
        raise
    finally:stop_all(stack)
