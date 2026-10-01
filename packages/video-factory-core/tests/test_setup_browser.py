import http.client
import json
import socket
import threading
import time
import unittest
from video_factory.setup_browser import BrowserInput


class BrowserInputTests(unittest.TestCase):
    def setUp(self):
        with socket.socket() as sock: sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        self.ui=BrowserInput(port,30);self.cookie='';self.csrf=''
    def tearDown(self): self.ui.close()
    def req(self,path,body=None,headers=None):
        c=http.client.HTTPConnection('127.0.0.1',self.ui.server.server_port,timeout=3)
        h={'Cookie':self.cookie,'Origin':self.ui.origin,'X-VF-CSRF':self.csrf,'Content-Type':'application/json'};h.update(headers or {})
        c.request('GET' if body is None else 'POST',path,json.dumps(body) if body is not None else None,h)
        r=c.getresponse();raw=r.read();result=(r.status,json.loads(raw),dict(r.getheaders()));c.close();return result
    def unlock(self):
        key=self.ui.unlock
        code,_,h=self.req('/api/unlock',{'key':key});self.assertEqual(code,200)
        self.cookie=h['Set-Cookie'].split(';')[0]
        self.csrf=self.req('/api/prompt')[1]['csrf']
        self.assertEqual(self.req('/api/unlock',{'key':key})[0],403)
    def test_secret_never_returned_by_read_or_receipt(self):
        self.assertEqual(self.req('/api/prompt')[0],403);self.unlock()
        result=[];t=threading.Thread(target=lambda:result.append(self.ui.hidden('App Secret')));t.start()
        for _ in range(20):
            value=self.req('/api/prompt')[1]
            if value['prompt']:break
            time.sleep(.01)
        body={'id':value['prompt']['id'],'answer':'synthetic-input-secret'}
        self.assertTrue(value['prompt']['hidden'])
        response=self.req('/api/answer',body);self.assertEqual(response[0],200)
        t.join(3);self.assertEqual(result,['synthetic-input-secret'])
        self.assertNotIn('synthetic-input-secret',str(response)+str(self.req('/api/prompt')))
        self.assertIsNone(self.ui.answer);self.assertEqual(self.req('/api/answer',body)[0],403)
    def test_wrong_origin_host_csrf_and_expired_windows(self):
        self.assertEqual(self.req('/api/unlock',{'key':self.ui.unlock},{'Origin':'http://evil.example'})[0],403)
        self.unlock()
        self.assertEqual(self.req('/api/prompt',headers={'Host':'evil.example'})[0],403)
        self.assertEqual(self.req('/api/answer',{'id':'wrong','answer':'secret'},{'X-VF-CSRF':'wrong'})[0],403)
        self.ui.deadline=0
        with self.assertRaises(EOFError):self.ui.read('Expired')
        self.assertEqual(self.req('/api/prompt')[0],403)
