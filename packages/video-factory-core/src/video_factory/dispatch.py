"""Project-scoped n8n execution of already approved jobs, never implicit approval."""
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time
import argparse
import io

from .runtime_store import RuntimeStore,RuntimeFault,canonical,identifier
from .worker import Worker
from .postgres_store import selected_store
from .runtime_cli import secret_input
from .onboarding import read_json


def authorize(db, token, project):
    identifier(project)
    if not isinstance(token,str) or not 20<=len(token)<=256:raise RuntimeFault('AUTH_EXECUTION_REQUIRED')
    key='automation:key:'+hashlib.sha256(token.encode()).hexdigest()
    row=db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
    saved=json.loads(row[0]) if row else {}
    if saved.get('project')!=project or saved.get('scope')!='approved_execution' or saved.get('expires_at',0)<=time.time():
        raise RuntimeFault('AUTH_EXECUTION_DENIED')
    return 'automation:'+project


class ExecutionStore(RuntimeStore):
    """Internal capability adapter; the bearer is never a general runtime admin."""
    def __init__(self, store, project):self.store=store;self.root=store.root;self.project=project
    def connect(self):return self.store.connect()
    def authorize(self,db,token,role=None,project=None):
        if project is not None and project!=self.project:raise RuntimeFault('AUTH_EXECUTION_DENIED')
        return authorize(db,token,self.project)
    def resolve_secret(self,*args):return self.store.resolve_secret(*args)


def dispatch_one(store,token,project,master_key,media_root,*,worker_factory=Worker):
    identifier(project)
    with store.connect() as db:
        authorize(db,token,project)
        # Task selection comes from the customer's ledger, not caller-supplied
        # asset paths, credentials, provider IDs, plans or arbitrary job payloads.
        rows=db.execute("SELECT t.id,t.revision,t.state FROM tasks t WHERE t.project=? AND t.state IN ('ready','submitted','submission_unknown') AND t.revision=(SELECT MAX(v.revision) FROM tasks v WHERE v.project=t.project AND v.id=t.id) ORDER BY t.id LIMIT 1001",(project,)).fetchall()
        if len(rows)>1000:raise RuntimeFault('EXECUTION_QUEUE_REQUIRES_PAGINATION')
        from .worker import job_key
        selected=None;blocked=[]
        for row in rows:
            saved=db.execute('SELECT value FROM meta WHERE key=?',(job_key(project,row['id'],row['revision']),)).fetchone()
            if not saved:continue
            value=json.loads(saved[0])
            if row['state']=='ready' and value['expires_at']<time.time():continue
            if row['state']=='submission_unknown' and not value.get('provider_receipt'):
                blocked.append({'task':row['id'],'revision':row['revision'],'reason':'submission_unknown'});continue
            selected=dict(row);break
    if selected is None:return {'project':project,'status':'idle','blocked':blocked,'provider_requests':0}
    # Worker rechecks capability, approval, revision, credential version and
    # content hashes inside its durable intent transaction immediately before POST.
    worker=worker_factory(ExecutionStore(store,project))
    result=worker.step(token,project,selected['id'],selected['revision'],master_key,media_root,allow_paid=True)
    return {'project':project,'task':selected['id'],'revision':selected['revision'],'result':result,'automatic_resubmit':False}


def template(project,credential_id):
    from .automation import template as queue_template
    value=queue_template(project,credential_id)
    name='Dispatch approved task';value['name']='Video Factory approved execution '+project
    node=value['nodes'][1];node['name']=name
    node['parameters']['url']='http://vf-executor-'+hashlib.sha256(project.encode()).hexdigest()[:12]+':8793/v1/dispatch'
    node['parameters']['options']['timeout']=240000
    value['connections']['Read schedule']['main'][0][0]['node']=name
    value['settings']['executionTimeout']=300
    return value


class Server(ThreadingHTTPServer):
    daemon_threads=True
    block_on_close=False
    def __init__(self,address,store,project,master_key,media_root):
        if os.environ.get('VF_CONTAINER_MODE')!='1':raise RuntimeFault('CONTAINER_MODE_REQUIRED')
        self.store,self.project,self.master_key,self.media_root=store,project,master_key,media_root
        self.busy=threading.Lock()
        super().__init__(address,Handler)
    def handle_error(self,*_):pass


class Handler(BaseHTTPRequestHandler):
    def setup(self):super().setup();self.connection.settimeout(10)
    def log_message(self,*_):pass
    def reply(self,status,value):
        raw=canonical(value).encode();self.send_response(status)
        self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(raw)
    def do_GET(self):self.reply(200 if self.path=='/healthz' else 404,{'scope':'approved_execution','project':self.server.project})
    def do_POST(self):
        acquired=False
        try:
            if self.path!='/v1/dispatch':raise RuntimeFault('ROUTE_NOT_FOUND')
            lengths=self.headers.get_all('Content-Length',[])
            if (self.headers.get('Transfer-Encoding') or self.headers.get_content_type()!='application/json'
                    or len(lengths)!=1 or not lengths[0].isdigit() or not 0<int(lengths[0])<=2048):
                raise RuntimeFault('REQUEST_INVALID')
            raw=self.rfile.read(int(lengths[0]))
            body=read_json(io.StringIO(raw.decode()))
            if body!={'project':self.server.project}:raise RuntimeFault('PROJECT_SCOPE_DENIED')
            auth=self.headers.get_all('Authorization',[])
            if len(auth)!=1 or not auth[0].startswith('Bearer '):raise RuntimeFault('AUTH_REQUIRED')
            token=auth[0][7:]
            with self.server.store.connect() as db:authorize(db,token,self.server.project)
            acquired=self.server.busy.acquire(blocking=False)
            if not acquired:self.reply(200,{'status':'busy','provider_requests':0});return
            result=dispatch_one(self.server.store,token,self.server.project,self.server.master_key,self.server.media_root)
            self.reply(200,result)
        except RuntimeFault as error:self.reply(403,{'error':str(error),'automatic_resubmit':False})
        except Exception:self.reply(409,{'error':'DISPATCH_INCOMPLETE_READ_TASK_STATUS','automatic_resubmit':False})
        finally:
            if acquired:self.server.busy.release()


def main():
    p=argparse.ArgumentParser();p.add_argument('--project',required=True);args=p.parse_args()
    identifier(args.project)
    with Server(('0.0.0.0',8793),selected_store()('/state'),args.project,secret_input(Path('/run/secrets/runtime_master'),''),Path('/media')) as server:server.serve_forever()


if __name__=='__main__':main()
