from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from video_factory.review_forward import Forward, container_address
from video_factory.runtime_store import RuntimeFault


class ForwardTests(unittest.TestCase):
    def test_forwards_only_to_fixed_upstream_preserves_bytes_and_closes_listener(self):
        received=[];body=b'synthetic-media-'*10000
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                received.append((self.path,self.headers['Host'],self.headers['Cookie']))
                self.send_response(206);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        upstream=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=upstream.serve_forever,daemon=True);thread.start()
        forward=Forward(0,upstream.server_address,30);port=forward.server_address[1]
        try:
            client=HTTPConnection('127.0.0.1',port,timeout=5)
            client.request('GET','/media/fixed.mp4',headers={'Host':'127.0.0.1:8790','Cookie':'vf_review=synthetic'})
            response=client.getresponse();self.assertEqual(response.status,206);self.assertEqual(response.read(),body);client.close()
            self.assertEqual(received,[('/media/fixed.mp4','127.0.0.1:8790','vf_review=synthetic')])
            self.assertEqual(forward.server_address[0],'127.0.0.1')
        finally:
            forward.close();upstream.shutdown();upstream.server_close();thread.join(2)
        with socket.socket() as probe:self.assertNotEqual(probe.connect_ex(('127.0.0.1',port)),0)
        reopened=Forward(port,upstream.server_address,1)
        reopened.close()

    def test_container_address_requires_exact_deployment_networks_and_private_ip(self):
        stack=SimpleNamespace(config={'deployment':'customer','instance':'fixture'})
        networks={'vf-customer-fixture_private':{'IPAddress':'172.22.0.5'},'vf-customer-fixture_worker_link':{'IPAddress':'172.23.0.5'}}
        run=Mock(return_value=json.dumps(networks).encode())
        self.assertEqual(container_address(stack,'owned-container',run),'172.22.0.5')
        self.assertEqual(run.call_args.args[0][-1],'owned-container')
        for ip in ('8.8.8.8','127.0.0.1','169.254.169.254','0.0.0.0','::1'):
            networks['vf-customer-fixture_private']['IPAddress']=ip;run.return_value=json.dumps(networks).encode()
            with self.assertRaises(RuntimeFault):container_address(stack,'owned-container',run)
        networks['foreign_network']={};run.return_value=json.dumps(networks).encode()
        with self.assertRaisesRegex(RuntimeFault,'NETWORK_MISMATCH'):container_address(stack,'owned-container',run)
