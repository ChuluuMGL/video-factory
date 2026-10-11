"""SKU-based, explicitly approved script jobs, durable before provider I/O.

Script generation does not authorize video spending. A generated draft enters the
same two-stage review ledger; text timeouts never auto-submit again.
"""
import json
import re
import time
from .runtime_store import RuntimeFault,canonical,fingerprint,identifier
from .feishu_bridge import FeishuBridge,meta,save,plain_text
from .script_provider import ScriptProvider,MODEL


def key(project,task,revision):return 'script:job:'+fingerprint([project,task,revision])


def profile(store,token,project,credential_ref,billing_owner):
    identifier(project);identifier(billing_owner)
    if not isinstance(credential_ref,str) or not re.fullmatch(r'secret:[A-Za-z0-9_-]{1,96}',credential_ref):
        raise RuntimeFault('SCRIPT_CREDENTIAL_REFERENCE_REQUIRED')
    with store.connect() as db:
        actor=store.authorize(db,token,'admin')
        setup=meta(db,'setup:project:'+project)
        secret=db.execute('SELECT revision FROM vault WHERE alias=?',(credential_ref[7:],)).fetchone()
        if not setup or not secret:raise RuntimeFault('SCRIPT_PROJECT_AND_SECRET_REQUIRED')
        value={'model':MODEL,'credential_ref':credential_ref,'credential_revision':secret[0],'billing_owner':billing_owner}
        save(db,'script:profile:'+project,value);store.audit(db,actor,'script_provider_configured',project)
    return {'project':project,'model':MODEL,'credential_saved':True,'provider_requests':0}


class ScriptJobs:
    def __init__(self,store,*,client_factory=None,source_client_factory=None,native_app_client_factory=None,provider=None):
        self.store=store
        self.bridge=FeishuBridge(store,**({'client_factory':client_factory} if client_factory else {}))
        self.source_client_factory=source_client_factory
        self.native_app_client_factory=native_app_client_factory
        self.provider=provider or ScriptProvider()

    def prepare(self,user,project,task,sku_id,brief,expected_revision=0):
        identifier(task)
        if not isinstance(brief,str) or not 1<=len(brief)<=6000:raise RuntimeFault('SCRIPT_BRIEF_REQUIRED')
        if type(expected_revision) is not int or expected_revision<0:raise RuntimeFault('REVISION_INVALID')
        _,identity,binding,actor,config,deployment=self.bridge._session(user,project,'submitters')
        with self.store.connect() as db:
            self.bridge._fence(db,project,binding,identity,'submitters',config)
            setup=meta(db,'setup:project:'+project);settings=meta(db,'script:profile:'+project)
            if not setup or not settings:raise RuntimeFault('SCRIPT_SETUP_REQUIRED')
            sku=next((s for s in setup['configuration']['project']['products'] if s['sku_id']==sku_id),None)
            if not sku:raise RuntimeFault('SCRIPT_SKU_NOT_IN_PROJECT')
            secret=db.execute('SELECT revision FROM vault WHERE alias=?',(settings['credential_ref'][7:],)).fetchone()
            if not secret or secret[0]!=settings['credential_revision']:raise RuntimeFault('SCRIPT_CREDENTIAL_CHANGED')
            native=meta(db,'native:config:'+project)
            native=native if native and native.get('enabled') else None
            previous=db.execute('SELECT * FROM tasks WHERE project=? AND id=? ORDER BY revision DESC LIMIT 1',(project,task)).fetchone()
            if (previous['revision'] if previous else 0)!=expected_revision:raise RuntimeFault('TASK_REVISION_CONFLICT')
            if previous and previous['state'] not in ('rejected','accepted','failed'):raise RuntimeFault('PREVIOUS_REVISION_NOT_CLOSED')
            feedback=''
            if previous:
                if not meta(db,key(project,task,expected_revision)):raise RuntimeFault('SCRIPT_TASK_SOURCE_CONFLICT')
                # IDs are validated; bind LIKE patterns and escape underscore.
                task_match='%"task":"'+task.replace('_', '\\_')+'"%'
                project_match='%"project":"'+project.replace('_', '\\_')+'"%'
                audit=db.execute("SELECT receipt FROM events WHERE receipt LIKE ? ESCAPE '\\' AND receipt LIKE ? ESCAPE '\\' LIMIT 501",(task_match,project_match)).fetchall()
                if len(audit)>500:raise RuntimeFault('SCRIPT_HISTORY_REQUIRES_REVIEW')
                rows=[json.loads(v[0]) for v in audit]
                feedback='\n'.join(r.get('feedback','') for r in rows if r.get('project')==project and r.get('task')==task and r.get('revision')==expected_revision)
        native_source=None
        if native:
            from .feishu_results_client import ResultClient
            from .feishu_native_review import source_key as native_source_key
            source_client=(self.source_client_factory or ResultClient)(user)
            names=native['names']; target=native['context']['target']
            if target['base_token']!=binding['base_token'] or target['table_id']!=binding['table_id']:
                raise RuntimeFault('SCRIPT_NATIVE_TARGET_CHANGED')
            record=source_client.find_task(target['base_token'],target['table_id'],names['task'],task)
            fields=record['fields']
            source_revision=plain_text(fields.get(names['source_revision']))
            source_script=plain_text(fields.get(names['script']))
            if (not source_revision or not source_script or
                    plain_text(fields.get(names['task']))!=task or plain_text(fields.get(names['sku_id']))!=sku_id):
                raise RuntimeFault('SCRIPT_NATIVE_SOURCE_CONFLICT')
            raw_status=fields.get(names['status']) or ''
            status=plain_text(raw_status) if raw_status else ''
            if status != ('脚本退回' if expected_revision else ''):
                raise RuntimeFault('SCRIPT_NATIVE_REVIEW_STATE_CONFLICT')
            native_source={'record_id':record['record_id'],'source_revision':source_revision,
                           'script_digest':fingerprint(source_script)}
            with self.store.connect() as db:
                if meta(db,'native:config:'+project)!=native:
                    raise RuntimeFault('SCRIPT_NATIVE_CONFIGURATION_CHANGED')
                bound=meta(db,native_source_key(project,task))
                if bound and bound['record_id']!=record['record_id']:
                    raise RuntimeFault('SCRIPT_NATIVE_SOURCE_CONFLICT')
                if previous:
                    old=meta(db,key(project,task,expected_revision))
                    old_source=old['plan'].get('native_source') if old else None
                    if not old_source or old_source['source_revision']==source_revision:
                        raise RuntimeFault('SCRIPT_NATIVE_SOURCE_REVISION_MUST_ADVANCE')
        value={'project':project,'task':task,'sku':sku,'brief':brief,'feedback':feedback[-8000:],
               'revision':expected_revision+1,'actor':actor,'deployment':deployment,'configuration_digest':config,
               'binding_sha256':fingerprint(binding),'profile':settings,'max_submissions':1,
               'native_config_sha256':fingerprint(native),'native_source':native_source}
        return {'plan':value,'plan_sha256':fingerprint(value),'provider_requests':0,'approval_required':True}

    def submit(self,user,expected_plan,**values):
        if type(values.get('expected_revision',0)) is not int or values.get('expected_revision',0)<0:raise RuntimeFault('REVISION_INVALID')
        _,identity,binding,actor,config,_=self.bridge._session(user,values['project'],'submitters')
        with self.store.connect() as db:
            self.bridge._fence(db,values['project'],binding,identity,'submitters',config)
            old=meta(db,key(values['project'],values['task'],values.get('expected_revision',0)+1))
            if old:
                if (old['plan_sha256']!=expected_plan or old['plan']['actor']!=actor or old['plan']['brief']!=values['brief']
                        or old['plan']['sku']['sku_id']!=values['sku_id']):raise RuntimeFault('SCRIPT_SUBMIT_CONFLICT')
                row=self.store._current(db,values['project'],values['task'],values.get('expected_revision',0)+1)
                return {'project':values['project'],'task':values['task'],'revision':row['revision'],'state':row['state'],'replayed':True}
        value=self.prepare(user,**values)
        if value['plan_sha256']!=expected_plan:raise RuntimeFault('SCRIPT_PLAN_CHANGED')
        plan=value['plan'];project,task,revision=plan['project'],plan['task'],plan['revision']
        with self.store.connect() as db:
            binding=self.bridge._binding(db,project)
            if fingerprint(binding)!=plan['binding_sha256'] or meta(db,'script:profile:'+project)!=plan['profile']:
                raise RuntimeFault('SCRIPT_PLAN_CHANGED')
            active_native=meta(db,'native:config:'+project)
            active_native=active_native if active_native and active_native.get('enabled') else None
            if fingerprint(active_native)!=plan['native_config_sha256']:
                raise RuntimeFault('SCRIPT_NATIVE_CONFIGURATION_CHANGED')
            if plan['native_source']:
                from .feishu_native_review import source_key as native_source_key
                source=meta(db,native_source_key(project,task))
                if source and source['record_id']!=plan['native_source']['record_id']:
                    raise RuntimeFault('SCRIPT_NATIVE_SOURCE_CONFLICT')
            source_revision=plan['native_source']['source_revision'] if plan['native_source'] else expected_plan
            result=self.store._create_task(db,plan['actor'],project,task,{'sku_id':plan['sku']['sku_id'],'script':plan['brief'],'source_revision':source_revision},revision-1)
            db.execute("UPDATE tasks SET state='script_queued' WHERE project=? AND id=? AND revision=?",(project,task,revision))
            save(db,key(project,task,revision),{'plan':plan,'plan_sha256':expected_plan,'state':'queued','expires_at':time.time()+3600,'provider_id':None})
            if plan['native_source']:
                save(db,native_source_key(project,task),{'record_id':plan['native_source']['record_id']})
        return {**result,'state':'script_queued','provider_requests':0}

    def step(self,token,project,task,revision,master_key):
        from .dispatch import authorize
        with self.store.connect() as db:
            authorize(db,token,project)
            job=meta(db,key(project,task,revision));row=self.store._current(db,project,task,revision)
            if not job:raise RuntimeFault('SCRIPT_JOB_MISSING')
            if job['state']=='received':
                return self._attach(db,project,task,revision,job)
            if job['state']!='queued':return {'state':row['state'],'provider_requests':0,'automatic_resubmit':False}
            if job['expires_at']<time.time():raise RuntimeFault('SCRIPT_APPROVAL_EXPIRED')
            plan=job['plan'];settings=meta(db,'script:profile:'+project)
            binding=self.bridge._binding(db,project)
            setup=meta(db,'setup:project:'+project)
            if (settings!=plan['profile'] or fingerprint(binding)!=plan['binding_sha256'] or not setup
                    or plan['sku'] not in setup['configuration']['project']['products']):raise RuntimeFault('SCRIPT_PLAN_CHANGED')
            native=meta(db,'native:config:'+project)
            native=native if native and native.get('enabled') else None
            if fingerprint(native)!=plan.get('native_config_sha256',fingerprint(None)):
                raise RuntimeFault('SCRIPT_NATIVE_CONFIGURATION_CHANGED')
            credential=db.execute('SELECT revision FROM vault WHERE alias=?',(settings['credential_ref'][7:],)).fetchone()
            if not credential or credential[0]!=settings['credential_revision']:raise RuntimeFault('SCRIPT_CREDENTIAL_CHANGED')
        if plan.get('native_source'):
            from .feishu_native_sync import NativeSync
            native_client=(self.native_app_client_factory(native['context']) if self.native_app_client_factory
                           else NativeSync(self.store,master_key).client(native['context']))
            target=native['context']['target'];names=native['names'];source=plan['native_source']
            remote=native_client.record(target['base_token'],target['table_id'],source['record_id'])['fields']
            raw_status=remote.get(names['status']) or ''
            status=plain_text(raw_status) if raw_status else ''
            if (plain_text(remote.get(names['task']))!=task
                    or plain_text(remote.get(names['sku_id']))!=plan['sku']['sku_id']
                    or plain_text(remote.get(names['source_revision']))!=source['source_revision']
                    or fingerprint(plain_text(remote.get(names['script'])))!=source['script_digest']
                    or status!=('脚本退回' if revision>1 else '')):
                raise RuntimeFault('SCRIPT_NATIVE_SOURCE_CHANGED')
        secret=self.store.resolve_secret(settings['credential_ref'][7:],master_key)
        if not secret or any(c.isspace() for c in secret):raise RuntimeFault('SCRIPT_KEY_INVALID')
        with self.store.connect() as db:
            authorize(db,token,project)
            latest=meta(db,key(project,task,revision));row=self.store._current(db,project,task,revision)
            if latest!=job or row['state']!='script_queued':raise RuntimeFault('SCRIPT_INTENT_ALREADY_CLAIMED')
            if meta(db,'script:profile:'+project)!=settings:raise RuntimeFault('SCRIPT_PLAN_CHANGED')
            current_native=meta(db,'native:config:'+project)
            current_native=current_native if current_native and current_native.get('enabled') else None
            if fingerprint(current_native)!=plan.get('native_config_sha256',fingerprint(None)):
                raise RuntimeFault('SCRIPT_NATIVE_CONFIGURATION_CHANGED')
            if plan.get('native_source'):
                from .feishu_native_review import source_key as native_source_key
                if meta(db,native_source_key(project,task))!={'record_id':plan['native_source']['record_id']}:
                    raise RuntimeFault('SCRIPT_NATIVE_SOURCE_CONFLICT')
            check=db.execute('SELECT revision FROM vault WHERE alias=?',(settings['credential_ref'][7:],)).fetchone()
            if not check or check[0]!=settings['credential_revision']:raise RuntimeFault('SCRIPT_CREDENTIAL_CHANGED')
            if fingerprint(self.bridge._binding(db,project))!=plan['binding_sha256'] or job['expires_at']<time.time():raise RuntimeFault('SCRIPT_PLAN_CHANGED')
            job['state']='submission_unknown';save(db,key(project,task,revision),job)
            db.execute("UPDATE tasks SET state='script_submission_unknown' WHERE project=? AND id=? AND revision=?",(project,task,revision))
            self.store.audit(db,'script-worker','script_submission_intent',project,task,revision)
        try:result=self.provider.generate(plan['sku'],plan['brief'],plan['feedback'],secret)
        except Exception:return {'state':'script_submission_unknown','automatic_resubmit':False}
        with self.store.connect() as db:
            # Persist provider receipt even if subsequent draft attachment fails.
            job['provider_id']=result['provider_id'];job['result']=result;job['state']='received'
            save(db,key(project,task,revision),job)
        with self.store.connect() as db:
            return self._attach(db,project,task,revision,job)

    def _attach(self,db,project,task,revision,job):
        row=self.store._current(db,project,task,revision)
        if row['state']!='script_submission_unknown':return {'state':row['state'],'provider_requests':0}
        source=job['plan'].get('native_source')
        payload={'sku_id':job['plan']['sku']['sku_id'],'script':job['result']['script'],
                 'source_revision':source['source_revision'] if source else job['plan_sha256']}
        db.execute("UPDATE tasks SET input=?,input_digest=?,state='awaiting_script_review' WHERE project=? AND id=? AND revision=?",(canonical(payload),fingerprint(payload),project,task,revision))
        self.store.audit(db,'script-worker','script_draft_received',project,task,revision)
        return {'state':'awaiting_script_review','provider_id':job['provider_id'],'human_acceptance':'required'}


class GeneratedReviewBridge(FeishuBridge):
    def prepare_review(self,user_token,project,task,revision,stage,decision,feedback=''):
        with self.store.connect() as db:job=meta(db,key(project,task,revision))
        if not job:return super().prepare_review(user_token,project,task,revision,stage,decision,feedback)
        if stage not in ('script','video') or decision not in ('accept','reject'):raise RuntimeFault('REVIEW_INVALID')
        if not isinstance(feedback,str) or len(feedback)>8000 or decision=='reject' and not feedback.strip():raise RuntimeFault('REVIEW_FEEDBACK_REQUIRED')
        _,identity,binding,actor,config,deployment=self._session(user_token,project,stage+'_reviewers')
        with self.store.connect() as db:
            self._fence(db,project,binding,identity,stage+'_reviewers',config)
            row=self.store._current(db,project,task,revision)
            if row['state']!='awaiting_'+stage+'_review':raise RuntimeFault('REVIEW_STATE_CONFLICT')
            payload=json.loads(row['input'])
            value={'project':project,'task':task,'revision':revision,'actor':actor,'stage':stage,'decision':decision,'feedback':feedback,
                   'binding_sha256':fingerprint(binding),'configuration_digest':config,'input_sha256':row['input_digest'],
                   'artifact':json.loads(row['artifact']) if row['artifact'] else None,
                   'snapshot':{'record_id':'SKU '+payload['sku_id'],'input':{'task':task,**payload}}}
        return {'plan':value,'plan_sha256':fingerprint(value),'feishu_writes':0}

    def review(self,user_token,project,task,revision,stage,decision,event,expected_plan,feedback=''):
        with self.store.connect() as db:job=meta(db,key(project,task,revision))
        if not job:return super().review(user_token,project,task,revision,stage,decision,event,expected_plan,feedback)
        identifier(event)
        _,identity,binding,actor,config,_=self._session(user_token,project,stage+'_reviewers')
        receipt_key='script:review:'+fingerprint([project,actor,event])
        request=fingerprint([project,actor,task,revision,stage,decision,feedback,expected_plan])
        with self.store.connect() as db:
            self._fence(db,project,binding,identity,stage+'_reviewers',config)
            old=meta(db,receipt_key)
            if old:
                if old['request']!=request:raise RuntimeFault('EVENT_ID_CONFLICT')
                return {**old['receipt'],'replayed':True}
        prepared=self.prepare_review(user_token,project,task,revision,stage,decision,feedback)
        if prepared['plan_sha256']!=expected_plan:raise RuntimeFault('REVIEW_PLAN_CHANGED')
        with self.store.connect() as db:
            self._fence(db,project,binding,identity,stage+'_reviewers',config)
            row=self.store._current(db,project,task,revision)
            if row['input_digest']!=prepared['plan']['input_sha256'] or (json.loads(row['artifact']) if row['artifact'] else None)!=prepared['plan']['artifact']:
                raise RuntimeFault('REVIEW_PLAN_CHANGED')
            receipt=self.store._review(db,actor,'gen_'+fingerprint([project,actor,event]),project,task,revision,stage,decision,feedback)
            save(db,receipt_key,{'request':request,'receipt':receipt})
        return receipt
