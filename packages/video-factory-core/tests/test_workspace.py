import copy
from datetime import datetime,timezone,timedelta
from pathlib import Path
import tempfile
import unittest
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from video_factory.workspace_http import WorkspaceServer,origin
from video_factory.workspace import certificate,nginx
from video_factory.runtime_store import RuntimeFault
from review_fixture import Fixture


class WorkspaceTests(unittest.TestCase):
    def test_origin_and_tls_binding(self):
        for value in ('http://example.com','https://a.example/path','https://user@a.example','https://a.example/?secret=x','https://a.example/','https://a.example:0'):
            with self.assertRaises((RuntimeFault,ValueError)):origin(value)
        self.assertEqual(origin('https://review.example:9443'),'https://review.example:9443')
        self.assertIn('proxy_set_header Host $http_host',nginx({'origin':'https://review.example'}))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
            name=x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME,'review.example')])
            now=datetime.now(timezone.utc)
            cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=30)).add_extension(x509.SubjectAlternativeName([x509.DNSName('review.example')]),False).sign(key,hashes.SHA256())
            c,k=root/'cert',root/'key';c.write_bytes(cert.public_bytes(serialization.Encoding.PEM));k.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));c.chmod(0o600);k.chmod(0o600)
            self.assertTrue(certificate(c,k,'review.example'))
            with self.assertRaisesRegex(RuntimeFault,'HOST_MISMATCH'):certificate(c,k,'other.example')
            k.chmod(0o644)
            with self.assertRaises(RuntimeFault):certificate(c,k,'review.example')
    def test_service_survives_window_limit_but_sessions_expire(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=Fixture(tmp);now=[100]
            server=WorkspaceServer(('127.0.0.1',0),f.server.service,f.server.oauth,'https://review.example',clock=lambda:now[0])
            try:
                sid,session=server.session(None,True);session['token']='synthetic';session['expires']=999999
                server.session(sid);now[0]+=400
                self.assertEqual(server.authorize(session),'synthetic')
                now[0]+=2000
                with self.assertRaisesRegex(RuntimeFault,'AUTH_REQUIRED'):server.session(sid)
                for i in range(70):
                    sid,_=server.session(None,True);now[0]+=361
                self.assertLess(len(server.sessions),3)
                now[0]+=86400
                sid,_=server.session(None,True);self.assertTrue(sid)
            finally: server.server_close();f.close()
