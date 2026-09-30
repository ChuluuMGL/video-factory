import datetime
import os
from pathlib import Path
import socket
import ssl
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.request import Request,build_opener,ProxyHandler
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from video_factory import worker_egress as egress
from video_factory.h3_provider import TunnelHandler, TunnelConnection
from video_factory.runtime_store import RuntimeFault
from video_factory.feishu_client import FeishuHandler
from video_factory.feishu_oauth import OAuthHandler


class EgressTests(unittest.TestCase):
    def test_dns_private_mixed_and_metadata_addresses_rejected(self):
        for address in ('127.0.0.1','10.0.0.1','169.254.169.254','::1','::ffff:127.0.0.1','192.168.1.1','0.0.0.0','224.0.0.1','ff02::1'):
            with patch.object(socket,'getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',(address,443))]):
                with self.assertRaises(ValueError):egress.public_addresses('api.minimax.io')
        with patch.object(socket,'getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443)),(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]):
            with self.assertRaises(ValueError):egress.public_addresses('api.minimax.io')

    def test_numeric_connect_uses_the_validated_resolution_once(self):
        address=(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443))
        with patch.object(socket,'getaddrinfo',return_value=[address]) as resolve,patch.object(socket,'socket') as factory:
            egress.connect_public('api.minimax.io')
            self.assertEqual(resolve.call_count,1)
            factory.return_value.connect.assert_called_once_with(('8.8.8.8',443))
        with self.assertRaises(ValueError):egress.connect_public('example.com')
        with self.assertRaises(RuntimeFault):TunnelConnection('api.minimax.io.evil.example')

    def test_https_certificate_validation_and_wire_relay(self):
        # Actual TLS over actual CONNECT; only this fixture's dialer is replaced.
        # A certificate for the original API hostname is required, not "egress".
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
            name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'api.minimax.io')]);now=datetime.datetime.now(datetime.timezone.utc)
            cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now-datetime.timedelta(minutes=1)).not_valid_after(now+datetime.timedelta(days=1))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName('api.minimax.io'),x509.DNSName('open.feishu.cn'),x509.DNSName('accounts.feishu.cn')]),critical=False).sign(key,hashes.SHA256()))
            (root/'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            (root/'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
            received=[]
            class Handler(BaseHTTPRequestHandler):
                def log_message(self,*args):pass
                def do_GET(self):
                    received.append(self.headers.get('Authorization'))
                    self.send_response(200);self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'OK')
            upstream=ThreadingHTTPServer(('127.0.0.1',0),Handler)
            context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(root/'cert.pem',root/'key.pem')
            upstream.socket=context.wrap_socket(upstream.socket,server_side=True)
            relay=egress.RelayServer(('127.0.0.1',8443),egress.Relay)
            for server in (upstream,relay):threading.Thread(target=server.serve_forever,daemon=True).start()
            original=socket.getaddrinfo
            def resolve(host,port,*args,**kwargs):
                return original('127.0.0.1' if host=='egress' else host,port,*args,**kwargs)
            try:
                trusted=ssl.create_default_context(cafile=str(root/'cert.pem'))
                with patch.object(egress,'connect_public',side_effect=lambda host:socket.create_connection(('127.0.0.1',upstream.server_port),3)),patch.object(socket,'getaddrinfo',side_effect=resolve),patch.dict(os.environ,{'no_proxy':'*','HTTPS_PROXY':'http://invalid:1'}):
                    client=build_opener(ProxyHandler({}),TunnelHandler(context=trusted))
                    with client.open(Request('https://api.minimax.io/fixture',headers={'Authorization':'Bearer SYNTHETIC'}),timeout=5) as response:self.assertEqual(response.read(),b'OK')
                    with build_opener(ProxyHandler({}),FeishuHandler(context=trusted)).open(Request('https://open.feishu.cn/fixture',headers={'Authorization':'Bearer USER_SYNTHETIC'}),timeout=5) as response:self.assertEqual(response.read(),b'OK')
                    with build_opener(ProxyHandler({}),OAuthHandler(context=trusted)).open(Request('https://accounts.feishu.cn/fixture',headers={'Authorization':'Basic SYNTHETIC'}),timeout=5) as response:self.assertEqual(response.read(),b'OK')
                    with self.assertRaises(Exception):
                        build_opener(ProxyHandler({}),TunnelHandler()).open('https://api.minimax.io/fixture',timeout=5)
                    # Same trusted cert must fail for a different allowed host.
                    with self.assertRaises(Exception):client.open('https://api.minimaxi.com/fixture',timeout=5)
                self.assertEqual(received,['Bearer SYNTHETIC','Bearer USER_SYNTHETIC','Basic SYNTHETIC'])
                for raw in (b'GET http://example.com/ HTTP/1.1\r\n\r\n',b'CONNECT 127.0.0.1:443 HTTP/1.1\r\n\r\n',b'CONNECT api.minimax.io:80 HTTP/1.1\r\n\r\n'):
                    with socket.create_connection(('127.0.0.1',8443),3) as stream:
                        stream.sendall(raw);self.assertTrue(stream.recv(1024).startswith(b'HTTP/1.1 403'))
            finally:
                for server in (relay,upstream):server.shutdown();server.server_close()
