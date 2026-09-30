"""Installed-package worker + real loopback HTTP + full ffmpeg decode in cloud only."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen
from cryptography.fernet import Fernet
from video_factory.h3_provider import H3Provider
from video_factory.runtime_store import RuntimeStore, RuntimeFault
from video_factory.worker import Worker
from video_factory.worker_media import verify


class FixtureProvider(H3Provider):
    def __init__(self,region,origin):
        super().__init__(region);self.origin=origin
    def download(self,url,stream):
        assert url==self.origin+'/output.mp4'
        with urlopen(url,timeout=5) as response:stream.write(response.read())


if len(sys.argv)>1 and sys.argv[1]=='step':
    root=Path(sys.argv[2]);origin=sys.argv[3]
    worker=Worker(RuntimeStore(root/'state'),provider_factory=lambda region:FixtureProvider(region,origin))
    result=worker.step((root/'token').read_text(),sys.argv[4],sys.argv[5],1,(root/'master').read_bytes(),root/'media',allow_paid=True)
    print(json.dumps(result));sys.exit(0)


def command(args):
    return json.loads(subprocess.check_output(args,stderr=subprocess.PIPE,timeout=180))


with tempfile.TemporaryDirectory(prefix='vf-worker-') as temp:
    root=Path(temp)
    for name in ('state','assets','media'):(root/name).mkdir(mode=0o700)
    output=root/'source.mp4'
    subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=c=blue:s=432x768:r=24:d=5',
                    '-f','lavfi','-i','sine=frequency=440:duration=5','-c:v','libx264','-threads','1','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(output)],check=True)
    reference=root/'assets/reference.png'
    subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(output),'-frames:v','1','-threads','1',str(reference)],check=True)
    password='cloud-worker-synthetic-password';store=RuntimeStore.install(root/'state','fixture',password)
    token=store.login('admin',password)['token'];key=Fernet.generate_key()
    for name,content in (('token',token.encode()),('master',key)):
        (root/name).write_bytes(content);(root/name).chmod(0o600)
    store.put_secret(token,'fixture','synthetic-only-provider-key',key)
    for project in ('alpha','beta'):
        store.put_project(token,project,{'video_route':'deferred','credential_ref':'secret:fixture','billing_owner':'fixture'})
    jobs=[('alpha','one','Approved fixture'),('alpha','two','unknown submit fixture'),('beta','one','Another approved fixture')]
    spec=root/'spec.json';spec.write_text(json.dumps({'duration':5,'references':[{'path':'reference.png','sha256':hashlib.sha256(reference.read_bytes()).hexdigest()}]}))
    calls={'post':0,'get':0,'downloads':0}; ids=set()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,value):
            raw=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def do_POST(self):
            assert self.path=='/v2/video_generation' and self.headers.get('Authorization')=='Bearer synthetic-only-provider-key'
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert body['model']=='MiniMax-H3' and body['duration']==5 and body['content'][1]['role']=='reference_image'
            calls['post']+=1
            if body['content'][0]['text']=='unknown submit fixture':
                self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
            provider_id=str(22345678901230+calls['post']);ids.add(provider_id);self.reply({'task_id':provider_id})
        def do_GET(self):
            if self.path=='/output.mp4':
                assert self.headers.get('Authorization') is None
                calls['downloads']+=1;raw=output.read_bytes();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
            assert self.headers.get('Authorization')=='Bearer synthetic-only-provider-key'
            provider_id=self.path.rsplit('/',1)[-1];assert provider_id in ids;calls['get']+=1
            self.reply({'task':{'id':provider_id,'model':'MiniMax-H3','status':'succeeded','content':{'url':origin+'/output.mp4'}}})
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);origin='http://127.0.0.1:'+str(server.server_port)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        vfctl=str(Path(sys.executable).with_name('vfctl'))
        for project,task,script in jobs:
            store.create_task(token,project,task,{'sku_id':'fixture','script':script,'source_revision':'1'})
            store.review(token,'review-'+project+'-'+task,project,task,1,'script','accept')
            common=['--root',str(root/'state'),'--token-file',str(root/'token'),'--project',project,'--task',task,'--revision','1',
                    '--assets-root',str(root/'assets'),'--specification',str(spec),'--credential-ref','secret:fixture','--billing-owner','fixture']
            plan=command([vfctl,'worker','prepare',*common]);assert plan['provider_requests']==0
            approved=command([vfctl,'worker','approve',*common,'--expect-plan',plan['request_plan_sha256']]);assert approved['approved']
            # New process for every step: no in-memory dedupe can make this pass.
            step=[sys.executable,str(Path(__file__).resolve()),'step',str(root),origin,project,task]
            first=command(step);second=command(step);third=command(step)
            if task=='two':
                assert first['state']==second['state']==third['state']=='submission_unknown'
            else:
                assert first['state']=='submitted' and second['state']=='awaiting_video_review' and third['replayed']
                snapshot=store.inspect_task(token,project,task);artifact=json.loads(snapshot['versions'][0]['artifact'])
                assert artifact['sha256']==hashlib.sha256(output.read_bytes()).hexdigest()
        assert calls=={'post':3,'get':2,'downloads':2},calls
        bad=root/'bad.mp4';bad.write_bytes(b'corrupt media');bad.chmod(0o600)
        try:verify(bad,5)
        except RuntimeFault:pass
        else:raise AssertionError('CORRUPT_MEDIA_ACCEPTED')
        print(json.dumps({'status':'PASS','scope':'installed_worker_loopback_provider_fixture','task_count':3,'project_count':2,
                          'fresh_step_processes':9,'fixture_http_requests':calls,'unknown_submit_not_retried':True,
                          'full_media_decode':'PASS','bad_media_rejected':True,'paid_model_requests':0,'real_provider_acceptance':'not_run','human_acceptance':'not_run'}))
    finally:server.shutdown();server.server_close();thread.join(timeout=5)
