"""Explicit one-task H3 execution on the shared SQLite/PostgreSQL ledger.

No schedule, automatic paid retry, fallback provider or production activation.
The durable task intent is committed BEFORE any model submission.
"""
import json
import time
from pathlib import Path
from .runtime_store import RuntimeFault, canonical, fingerprint, identifier, private_directory
from .h3_provider import H3Provider, ProviderRejected, REJECTION_CODES, request_body, ORIGINS
from .worker_media import collect, toolchain


def job_key(project,task,revision):
    return 'worker:'+fingerprint([project,task,revision])


class Worker:
    def __init__(self, store, *, provider_factory=H3Provider, media_collector=collect, media_preflight=toolchain):
        self.store=store;self.provider_factory=provider_factory
        self.media_collector=media_collector;self.media_preflight=media_preflight

    def prepare(self,token,project,task,revision,assets_root,specification,credential_ref,billing_owner,region):
        identifier(billing_owner)
        if not isinstance(credential_ref,str) or not credential_ref.startswith('secret:'):
            raise RuntimeFault('WORKER_SECRET_REFERENCE_REQUIRED')
        alias=identifier(credential_ref[7:])
        if region not in ORIGINS:raise RuntimeFault('PROVIDER_REGION_INVALID')
        with self.store.connect() as db:
            self.store.authorize(db,token,'admin')
            row=self.store._current(db,project,task,revision)
            if row['state']!='ready':raise RuntimeFault('WORKER_REQUIRES_APPROVED_SCRIPT')
            secret=db.execute('SELECT revision FROM vault WHERE alias=?',(alias,)).fetchone()
            if not secret:raise RuntimeFault('SECRET_MISSING')
            deployment=db.execute("SELECT value FROM meta WHERE key='deployment'").fetchone()[0]
        body=request_body(json.loads(row['input'])['script'],Path(assets_root),specification)
        plan={'schema':1,'deployment':deployment,'project':project,'task':task,'revision':revision,
              'input_digest':row['input_digest'],'configuration_digest':row['configuration_digest'],
              'assets_root':str(assets_root),'specification':specification,'request_sha256':fingerprint(body),
              'credential_ref':credential_ref,'credential_revision':secret[0],'billing_owner':billing_owner,
              'region':region,'provider_origin':ORIGINS[region],'model':'MiniMax-H3','max_submissions':1}
        return {'request_plan_sha256':fingerprint(plan),'plan':plan,'provider_requests':0,'approval_required':True}

    def approve(self,token,expected_plan,**values):
        prepared=self.prepare(token,**values)
        if prepared['request_plan_sha256']!=expected_plan:raise RuntimeFault('WORKER_PLAN_CHANGED_REVIEW_REQUIRED')
        plan=prepared['plan'];key=job_key(plan['project'],plan['task'],plan['revision'])
        with self.store.connect() as db:
            actor=self.store.authorize(db,token,'admin')
            row=self.store._current(db,plan['project'],plan['task'],plan['revision'])
            if row['state']!='ready' or row['input_digest']!=plan['input_digest'] or row['configuration_digest']!=plan['configuration_digest']:
                raise RuntimeFault('WORKER_TASK_CHANGED')
            value={'plan':plan,'plan_sha256':expected_plan,'expires_at':time.time()+3600,'provider_receipt':None}
            db.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,canonical(value)))
            self.store.audit(db,actor,'worker_one_submission_approved',plan['project'],plan['task'],plan['revision'])
        return {'approved':True,'request_plan_sha256':expected_plan,'expires_at':value['expires_at'],'max_submissions':1,'provider_requests':0}

    def _load(self,db,token,project,task,revision):
        self.store.authorize(db,token,'admin')
        row=self.store._current(db,project,task,revision)
        saved=db.execute('SELECT value FROM meta WHERE key=?',(job_key(project,task,revision),)).fetchone()
        if not saved:raise RuntimeFault('WORKER_APPROVAL_REQUIRED')
        value=json.loads(saved[0]);plan=value['plan']
        if row['input_digest']!=plan['input_digest'] or row['configuration_digest']!=plan['configuration_digest']:
            raise RuntimeFault('WORKER_TASK_CHANGED')
        return row,value

    def _terminal(self,token,project,task,revision,failure_code=None):
        if failure_code not in REJECTION_CODES.values():failure_code=None
        with self.store.connect() as db:
            self.store.authorize(db,token,'admin')
            row=self.store._current(db,project,task,revision)
            if row['state'] not in ('submission_unknown','submitted','failed'):
                raise RuntimeFault('PROVIDER_TERMINAL_STATE_CONFLICT')
            if row['state'] in ('submission_unknown','submitted'):
                db.execute("UPDATE tasks SET state='failed' WHERE project=? AND id=? AND revision=?",(project,task,revision))
                self.store.audit(db,'worker','provider_failed_no_retry',project,task,revision)
            if failure_code:
                key=job_key(project,task,revision)
                saved=db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
                if saved:
                    value=json.loads(saved[0]);value['failure_code']=failure_code
                    db.execute('UPDATE meta SET value=? WHERE key=?',(canonical(value),key))
        result={'state':'failed','automatic_resubmit':False}
        if failure_code:result['failure_code']=failure_code
        return result

    def recover_auth(self,token,project,task,revision,expected_plan,credential_ref,region):
        """Restore an unchanged approved script after a definite auth rejection only.

        Archive the failed attempt and revoke its paid approval. Never submit here.
        """
        if not isinstance(credential_ref,str) or not credential_ref.startswith('secret:'):
            raise RuntimeFault('WORKER_SECRET_REFERENCE_REQUIRED')
        alias=identifier(credential_ref[7:])
        if region not in ORIGINS:raise RuntimeFault('PROVIDER_REGION_INVALID')
        with self.store.connect() as db:
            actor=self.store.authorize(db,token,'admin')
            row,value=self._load(db,token,project,task,revision)
            if value['plan_sha256']!=expected_plan:raise RuntimeFault('WORKER_PLAN_CHANGED_REVIEW_REQUIRED')
            if (row['state']!='failed' or row['provider_id'] or value.get('provider_receipt')
                    or value.get('failure_code')!='PROVIDER_AUTH_REJECTED_CHECK_REGION_OR_KEY'):
                raise RuntimeFault('WORKER_AUTH_RECOVERY_NOT_SAFE')
            expected={'project':project,'task':task,'revision':revision,'state':'ready'}
            receipts=(json.loads(event[0]) for event in db.execute('SELECT receipt FROM events').fetchall())
            approved=any(all(receipt.get(k)==v for k,v in expected.items()) for receipt in receipts)
            if not approved:raise RuntimeFault('WORKER_REQUIRES_APPROVED_SCRIPT')
            secret=db.execute('SELECT revision FROM vault WHERE alias=?',(alias,)).fetchone()
            if not secret:raise RuntimeFault('SECRET_MISSING')
            plan=value['plan']
            if (credential_ref==plan['credential_ref'] and secret[0]==plan['credential_revision']
                    and region==plan['region'] and ORIGINS[region]==plan['provider_origin']):
                raise RuntimeFault('WORKER_AUTH_CONFIGURATION_UNCHANGED')
            archive='worker_attempt:'+fingerprint([project,task,revision,time.time_ns()])
            archived={**value,'expires_at':0,'recovery':{'actor':actor,'reason':'definite_auth_rejection',
                'credential_ref':credential_ref,'credential_revision':secret[0],'region':region}}
            updated=db.execute("UPDATE tasks SET state='ready' WHERE project=? AND id=? AND revision=? AND state='failed'",(project,task,revision))
            if updated.rowcount!=1:raise RuntimeFault('WORKER_AUTH_RECOVERY_NOT_SAFE')
            db.execute('INSERT INTO meta VALUES(?,?)',(archive,canonical(archived)))
            db.execute('DELETE FROM meta WHERE key=?',(job_key(project,task,revision),))
            self.store.audit(db,actor,'worker_auth_recovery_requires_new_approval',project,task,revision)
        return {'state':'ready','failed_attempt':archive,'provider_requests':0,'automatic_resubmit':False,
                'approval_required':True,'script_review_preserved':True}

    def status(self,token,project,task,revision):
        with self.store.connect() as db:
            self.store.authorize(db,token,'admin')
            row=db.execute('SELECT * FROM tasks WHERE project=? AND id=? AND revision=?',(project,task,revision)).fetchone()
            if not row:raise RuntimeFault('TASK_MISSING')
            saved=db.execute('SELECT value FROM meta WHERE key=?',(job_key(project,task,revision),)).fetchone()
            value=json.loads(saved[0]) if saved else None
        return {'project':project,'task':task,'revision':revision,'state':row['state'],
                'provider_id':row['provider_id'] or (value['provider_receipt'] if value else None),
                'failure_code':value.get('failure_code') if value else None,
                'request_plan_sha256':value['plan_sha256'] if value else None,
                'approval_expires_at':value['expires_at'] if value else None,
                'automatic_resubmit':False,'human_acceptance':'separate_review_required'}

    def step(self,token,project,task,revision,master_key,media_root,*,allow_paid=False):
        with self.store.connect() as db:row,value=self._load(db,token,project,task,revision)
        plan=value['plan'];state=row['state'];provider_id=row['provider_id'] or value['provider_receipt']
        if state in ('awaiting_video_review','accepted','rejected','failed'):
            result={'state':state,'replayed':True,'provider_requests':0}
            if state=='failed' and value.get('failure_code'):result['failure_code']=value['failure_code']
            return result
        if state=='submission_unknown' and not provider_id:
            return {'state':state,'automatic_resubmit':False,'reconciliation_required':True,'provider_requests':0}
        if plan['provider_origin']!=ORIGINS.get(plan['region']):
            raise RuntimeFault('WORKER_PROVIDER_ORIGIN_CHANGED_REVIEW_REQUIRED')
        if state=='ready' and not allow_paid:raise RuntimeFault('WORKER_EXPLICIT_PAID_SUBMISSION_REQUIRED')
        if state=='ready' and value['expires_at']<time.time():raise RuntimeFault('WORKER_APPROVAL_EXPIRED_OR_CHANGED')
        # Credential value never leaves memory; a changed key revision needs review.
        with self.store.connect() as db:
            self.store.authorize(db,token,'admin')
            secret=db.execute('SELECT revision FROM vault WHERE alias=?',(plan['credential_ref'][7:],)).fetchone()
            if not secret or secret[0]!=plan['credential_revision']:raise RuntimeFault('WORKER_CREDENTIAL_CHANGED')
        secret_value=self.store.resolve_secret(plan['credential_ref'][7:],master_key)
        if not secret_value or any(c.isspace() for c in secret_value):
            raise RuntimeFault('PROVIDER_KEY_FORMAT_INVALID_USE_RAW_KEY')
        provider=self.provider_factory(plan['region'])
        if state=='ready':
            private_directory(media_root)
            self.media_preflight()
            body=request_body(json.loads(row['input'])['script'],Path(plan['assets_root']),plan['specification'])
            if fingerprint(body)!=plan['request_sha256']:raise RuntimeFault('WORKER_INPUT_CHANGED')
            # Same DB transaction fences concurrent workers and approval revocation.
            with self.store.connect() as db:
                current,latest=self._load(db,token,project,task,revision)
                if current['state']!='ready':raise RuntimeFault('TASK_NOT_READY_NO_RESUBMIT')
                if latest['plan_sha256']!=value['plan_sha256'] or latest['expires_at']<time.time():
                    raise RuntimeFault('WORKER_APPROVAL_EXPIRED_OR_CHANGED')
                check=db.execute('SELECT revision FROM vault WHERE alias=?',(plan['credential_ref'][7:],)).fetchone()
                if not check or check[0]!=plan['credential_revision']:raise RuntimeFault('WORKER_CREDENTIAL_CHANGED')
                db.execute("UPDATE tasks SET state='submission_unknown' WHERE project=? AND id=? AND revision=?",(project,task,revision))
                self.store.audit(db,'worker','worker_submission_intent',project,task,revision)
            try:provider_id=provider.submit(body,secret_value)
            except ProviderRejected as error:return self._terminal(token,project,task,revision,str(error))
            except Exception:
                return {'state':'submission_unknown','automatic_resubmit':False,'reconciliation_required':True}
            # Retain the provider receipt independently of configuration drift.
            value['provider_receipt']=provider_id
            with self.store.connect() as db:
                db.execute('UPDATE meta SET value=? WHERE key=?',(canonical(value),job_key(project,task,revision)))
            return self.store.attach_provider(token,project,task,revision,provider_id)
        if state=='submission_unknown':
            self.store.attach_provider(token,project,task,revision,provider_id)
        result=provider.poll(provider_id,secret_value)
        if result['id']!=provider_id:raise RuntimeFault('PROVIDER_ID_MISMATCH')
        if result['status'] in ('failed','cancelled'):return self._terminal(token,project,task,revision)
        if result['status'] in ('queued','running'):return {'state':'submitted','provider_status':result['status'],'provider_id':provider_id}
        if result['status']!='succeeded':raise RuntimeFault('PROVIDER_STATUS_UNKNOWN')
        artifact=self.media_collector(provider,result['url'],Path(media_root),fingerprint([plan,provider_id]),plan['specification']['duration'])
        return self.store.record_artifact(token,project,task,revision,provider_id,artifact)
