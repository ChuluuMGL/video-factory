"""Shared worker state semantics against real PG; provider is strictly synthetic."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import tempfile
from pathlib import Path
from cryptography.fernet import Fernet
from video_factory.postgres_store import PostgresStore, read_dsn
from video_factory.runtime_store import RuntimeFault, RuntimeStore, canonical
from video_factory.worker import Worker

store=PostgresStore('/state');token=store.login('admin','cloud-stack-fixture-password')['token'];key=Path('/run/secrets/runtime_master').read_bytes()
store.put_secret(token,'worker_fixture','synthetic-not-real-key',key)
assets=Path('/state/worker-assets');assets.mkdir(mode=0o700)
image=assets/'fixture.png';image.write_bytes(b'fixture-no-media-decode-in-pg-test')
spec={'duration':5,'references':[{'path':'fixture.png','sha256':hashlib.sha256(image.read_bytes()).hexdigest()}]}
class Provider:
    count=0
    def submit(self,*args):self.count+=1;return str(32345678901230+self.count)
    def poll(self,provider_id,secret):return {'id':provider_id,'status':'running','url':None}
provider=Provider();worker=Worker(store,provider_factory=lambda _:provider,media_preflight=lambda:None)
for project,task in [('new_brand','one'),('new_brand','two'),('second_brand','one')]:
    store.create_task(token,project,task,{'sku_id':'fixture','script':'Approved synthetic script','source_revision':'1'})
    store.review(token,'worker-'+project+'-'+task,project,task,1,'script','accept')
    values=dict(project=project,task=task,revision=1,assets_root=assets,specification=spec,credential_ref='secret:worker_fixture',billing_owner='fixture',region='global')
    plan=worker.prepare(token,**values);worker.approve(token,plan['request_plan_sha256'],**values)
    def step(_):
        try:return worker.step(token,project,task,1,key,Path('/state'),allow_paid=True)
        except RuntimeFault:return {'blocked':True}
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(step,range(4)))
    assert worker.status(token,project,task,1)['state']=='submitted'
assert provider.count==3
with tempfile.TemporaryDirectory(dir='/state') as temporary:
    root=Path(temporary);source=root/'source';source.mkdir(mode=0o700)
    original=RuntimeStore.install(source,'worker-migration','cloud-stack-fixture-password')
    with original.connect() as db:
        db.execute('INSERT INTO meta VALUES(?,?)',('worker:migration_fixture',canonical({'expires_at':9999999999,'provider_receipt':'kept-id'})))
    target=root/'target';target.mkdir(mode=0o700)
    dsn=root/'target.dsn';dsn.write_text(read_dsn('/run/secrets/runtime_dsn').rsplit('/',1)[0]+'/vf_worker_migration');dsn.chmod(0o600)
    PostgresStore.migrate_sqlite(source,target,dsn)
    migrated=PostgresStore(target,dsn)
    with migrated.connect() as db:
        saved=json.loads(db.execute("SELECT value FROM meta WHERE key='worker:migration_fixture'").fetchone()[0])
        assert saved['expires_at']==0 and saved['provider_receipt']=='kept-id'
    with original.connect() as db:
        assert json.loads(db.execute("SELECT value FROM meta WHERE key='worker:migration_fixture'").fetchone()[0])['expires_at']==9999999999

print(json.dumps({'status':'PASS','backend':'postgresql','projects':2,'tasks':3,'concurrent_workers':4,'fixture_submissions':3,'paid_model_requests':0}))
