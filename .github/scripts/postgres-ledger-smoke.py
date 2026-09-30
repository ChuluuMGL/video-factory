"""Executed in the installed product container against real isolated PostgreSQL."""
import concurrent.futures
import json
import os
from pathlib import Path
import tempfile
from cryptography.fernet import Fernet

from video_factory.postgres_store import PostgresStore,read_dsn
from video_factory.runtime_store import RuntimeStore,RuntimeFault

password='cloud-stack-fixture-password'
store=PostgresStore('/state');token=store.login('admin',password)['token']
config={'video_route':'deferred','credential_ref':'secret:fixture','billing_owner':'fixture'}
store.put_project(token,'brand',config)
store.put_project(token,'second',config)
payload={'sku_id':'fixture-sku','script':'Synthetic fixture only','source_revision':'1'}
for project,task in [('brand','one'),('brand','two'),('second','one')]:
    store.create_task(token,project,task,payload)
    store.review(token,project+'-'+task,project,task,1,'script','accept')
def claim(_):
    try:return PostgresStore('/state').claim(token,'brand','one',1)['state']
    except RuntimeFault:return 'blocked'
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(claim,range(4)))
assert results.count('submission_unknown')==1 and results.count('blocked')==3
store.set_user(token,'reviewer','fixture-reviewer-password','brand')
reviewer=store.login('reviewer','fixture-reviewer-password')['token']
try:store.inspect_task(reviewer,'second','one')
except RuntimeFault:pass
else:raise AssertionError('PROJECT_ACCESS_LEAK')
store.create_task(token,'brand','repair',payload)
first=store.review(reviewer,'reject','brand','repair',1,'script','reject','Correct SKU')
assert store.review(reviewer,'reject','brand','repair',1,'script','reject','Correct SKU')==first
store.create_task(token,'brand','repair',{**payload,'script':'Corrected fixture'},expected_revision=1)
try:store.review(reviewer,'stale','brand','repair',1,'script','accept')
except RuntimeFault:pass
else:raise AssertionError('STALE_REVIEW_ACCEPTED')
key=Path('/run/secrets/runtime_master').read_bytes()
store.put_secret(token,'fixture','fixture-provider-not-a-real-key',key)
assert store.resolve_secret('fixture',key)=='fixture-provider-not-a-real-key'
# Persist only a synthetic session token for the restore-invalidates-session check.
(Path('/state')/'old-test-token').write_text(token)
with tempfile.TemporaryDirectory(dir='/state') as temporary:
    root=Path(temporary);source=root/'source';source.mkdir(mode=0o700)
    sqlite=RuntimeStore.install(source,'sqlite-migration',password)
    oldtoken=sqlite.login('admin',password)['token']
    sqlite.put_project(oldtoken,'migrated',config)
    sqlite.create_task(oldtoken,'migrated','one',payload)
    sqlite.review(oldtoken,'migrate-review','migrated','one',1,'script','accept')
    sqlite.claim(oldtoken,'migrated','one',1)
    sqlite.put_secret(oldtoken,'fixture','retained',key)
    target=root/'target';target.mkdir(mode=0o700)
    dsn=root/'target.dsn';dsn.write_text(read_dsn('/run/secrets/runtime_dsn').rsplit('/',1)[0]+'/vf_migration');dsn.chmod(0o600)
    receipt=PostgresStore.migrate_sqlite(source,target,dsn)
    assert receipt['migrated'] and sqlite.path.exists()
    migrated=PostgresStore(target,dsn)
    try:migrated.inspect_task(oldtoken,'migrated','one')
    except RuntimeFault:pass
    else:raise AssertionError('OLD_SESSION_SURVIVED_MIGRATION')
    newtoken=migrated.login('admin',password)['token']
    assert migrated.resolve_secret('fixture',key)=='retained'
    try:migrated.claim(newtoken,'migrated','one',1)
    except RuntimeFault:pass
    else:raise AssertionError('MIGRATION_RESUBMITTED_UNKNOWN')
    try:PostgresStore.migrate_sqlite(source,target,dsn)
    except RuntimeFault:pass
    else:raise AssertionError('MIGRATION_OVERWROTE_TARGET')
print(json.dumps({'status':'PASS','backend':'postgresql','concurrent_claim_winners':1,'sqlite_migration':'PASS','provider_requests':0}))
