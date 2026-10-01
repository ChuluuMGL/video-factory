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
        return {'status':'PASS','https_certificate_verified':True,'secure_cookie':True,'anonymous_and_bad_host_denied':True,'container_restart':'PASS','cold_backup_stops_ingress':'PASS','certificate_restore':'PASS','human_acceptance':'not_run'}
    finally:stop_all(stack)
