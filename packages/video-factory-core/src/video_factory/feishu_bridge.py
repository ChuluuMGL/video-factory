"""Verified-user import/review bridge. External reads never hold ledger locks.

Approvals refer to immutable imported snapshots, not to a remotely locked row.
No Feishu mutation, paid permission, user impersonation or generated local login.
"""
import json
from .feishu_client import FeishuClient,resource
from .runtime_store import RuntimeFault,canonical,fingerprint,identifier


def meta(db,key):
    row=db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
    return json.loads(row[0]) if row else None


def save(db,key,value):
    db.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,canonical(value)))


def source_key(project,task,revision):return 'feishu:source:'+fingerprint([project,task,revision])


def configuration(value):
    required={'tenant_key','base_token','table_id','fields','submitters','reviewers'}
    scoped={'script_reviewers','video_reviewers'}
    if not isinstance(value,dict) or set(value) not in (required,required|scoped):
        raise RuntimeFault('FEISHU_BINDING_INVALID')
    resource(value['tenant_key']);resource(value['base_token']);resource(value['table_id'],'tbl')
    if not isinstance(value['fields'],dict) or set(value['fields'])!={'task','sku_id','script','source_revision'}:
        raise RuntimeFault('FEISHU_FIELD_MAPPING_INVALID')
    for v in value['fields'].values():resource(v,'fld')
    if len(set(value['fields'].values()))!=4:raise RuntimeFault('FEISHU_FIELD_MAPPING_INVALID')
    for role in ('submitters','reviewers',*(sorted(scoped) if scoped<=set(value) else ())):
        users=value[role]
        if not isinstance(users,list) or not 1<=len(users)<=100 or len(set(users))!=len(users):raise RuntimeFault('FEISHU_MEMBERS_INVALID')
        for user in users:resource(user,'ou_')
    if scoped<=set(value) and set(value['reviewers'])!=set(value['script_reviewers'])|set(value['video_reviewers']):
        raise RuntimeFault('FEISHU_MEMBERS_INVALID')
    return value


def target(binding):return {k:binding[k] for k in ('tenant_key','base_token','table_id','fields')}


def plain_text(value):
    if isinstance(value,list):
        if not all(isinstance(v,dict) and v.get('type')=='text' and isinstance(v.get('text'),str) for v in value):
            raise RuntimeFault('FEISHU_TEXT_FIELD_REQUIRED')
        value=''.join(v['text'] for v in value)
    if not isinstance(value,str) or not 1<=len(value)<=12000:raise RuntimeFault('FEISHU_TEXT_FIELD_REQUIRED')
    return value


class FeishuBridge:
    def __init__(self,store,*,client_factory=FeishuClient):self.store=store;self.client_factory=client_factory

    def _binding(self,db,project):
        identifier(project)
        if meta(db,'feishu:reconfirm:'+project):raise RuntimeFault('FEISHU_BINDING_RECONFIRM_AFTER_RECOVERY')
        value=meta(db,'feishu:binding:'+project)
        if value is None:raise RuntimeFault('FEISHU_PROJECT_NOT_BOUND')
        return configuration(value)

    @staticmethod
    def _member(binding,identity,role):
        members=binding.get(role,binding['reviewers'] if role in ('script_reviewers','video_reviewers') else [])
        if identity['tenant_key']!=binding['tenant_key'] or identity['open_id'] not in members:
            raise RuntimeFault('FEISHU_TENANT_OR_ROLE_DENIED')
        return 'feishu:'+identity['tenant_key']+':'+identity['open_id']

    @staticmethod
    def _schema(client,binding):
        fields=client.fields(binding['base_token'],binding['table_id']);by_id={}
        for field in fields:
            if not isinstance(field,dict) or not isinstance(field.get('field_id'),str) or field['field_id'] in by_id:
                raise RuntimeFault('FEISHU_SCHEMA_AMBIGUOUS')
            by_id[field['field_id']]=field
        resolved={}
        for logical,field_id in binding['fields'].items():
            field=by_id.get(field_id,{})
            if type(field.get('type')) is not int or field['type']!=1 or not isinstance(field.get('field_name'),str) or not field['field_name']:
                raise RuntimeFault('FEISHU_MAPPED_TEXT_FIELD_MISSING_OR_CHANGED')
            resolved[logical]=field['field_name']
        if len(set(resolved.values()))!=4:raise RuntimeFault('FEISHU_SCHEMA_AMBIGUOUS')
        return resolved

    def bind(self,admin_token,project,binding,user_token,expected_binding=None):
        identifier(project);configuration(binding)
        with self.store.connect() as db:self.store.authorize(db,admin_token,'admin')
        client=self.client_factory(user_token);identity=client.identity()
        if identity['tenant_key']!=binding['tenant_key']:raise RuntimeFault('FEISHU_TENANT_OR_ROLE_DENIED')
        self._schema(client,binding)
        with self.store.connect() as db:
            actor=self.store.authorize(db,admin_token,'admin')
            if not db.execute('SELECT 1 FROM projects WHERE id=?',(project,)).fetchone():raise RuntimeFault('PROJECT_MISSING')
            old=meta(db,'feishu:binding:'+project)
            if (fingerprint(old) if old else None)!=expected_binding:raise RuntimeFault('FEISHU_BINDING_CONFLICT')
            if old and target(old)!=target(binding):raise RuntimeFault('FEISHU_TARGET_IMMUTABLE_USE_NEW_PROJECT')
            save(db,'feishu:binding:'+project,binding)
            db.execute('DELETE FROM meta WHERE key=?',('feishu:reconfirm:'+project,))
            self.store.audit(db,actor,'feishu_binding',project)
        return {'project':project,'binding_sha256':fingerprint(binding),'field_schema_verified':True,'paid_execution_enabled':False,'feishu_writes':0}

    def _session(self,user_token,project,role):
        client=self.client_factory(user_token);identity=client.identity()
        with self.store.connect() as db:
            binding=self._binding(db,project);actor=self._member(binding,identity,role)
            config=db.execute('SELECT digest FROM projects WHERE id=?',(project,)).fetchone()[0]
            deployment=db.execute("SELECT value FROM meta WHERE key='deployment'").fetchone()[0]
        return client,identity,binding,actor,config,deployment

    def _fence(self,db,project,binding,identity,role,config):
        current=self._binding(db,project)
        if current!=binding:raise RuntimeFault('FEISHU_BINDING_CHANGED')
        self._member(current,identity,role)
        if db.execute('SELECT digest FROM projects WHERE id=?',(project,)).fetchone()[0]!=config:
            raise RuntimeFault('PROJECT_CONFIGURATION_CHANGED')

    def _snapshot(self,client,binding,record):
        names=self._schema(client,binding)
        value=client.record(binding['base_token'],binding['table_id'],record)
        fields={logical:plain_text(value['fields'].get(name)) for logical,name in names.items()}
        identifier(fields['task'])
        return {'target':target(binding),'record_id':record,'input':fields}

    @staticmethod
    def _sku(db,project,sku):
        setup=meta(db,'setup:project:'+project)
        if setup and sku not in {row['sku_id'] for row in setup['configuration']['project']['products']}:
            raise RuntimeFault('FEISHU_SKU_NOT_IN_PROJECT')

    def prepare_import(self,user_token,project,record,expected_revision=0):
        if type(expected_revision) is not int or expected_revision<0:raise RuntimeFault('REVISION_INVALID')
        client,identity,binding,actor,config,deployment=self._session(user_token,project,'submitters')
        snapshot=self._snapshot(client,binding,record)
        plan={'deployment':deployment,'project':project,'actor':actor,'binding_sha256':fingerprint(binding),
              'configuration_digest':config,'expected_revision':expected_revision,'snapshot':snapshot}
        with self.store.connect() as db:
            self._fence(db,project,binding,identity,'submitters',config)
            self._sku(db,project,snapshot['input']['sku_id'])
        return {'plan':plan,'plan_sha256':fingerprint(plan),'feishu_writes':0,'paid_execution_enabled':False}

    def import_task(self,user_token,project,record,expected_plan,expected_revision=0):
        prepared=self.prepare_import(user_token,project,record,expected_revision);plan=prepared['plan']
        if prepared['plan_sha256']!=expected_plan:raise RuntimeFault('FEISHU_IMPORT_PLAN_CHANGED')
        snapshot=plan['snapshot'];fields=snapshot['input'];task=fields['task'];revision=expected_revision+1
        with self.store.connect() as db:
            binding=self._binding(db,project)
            if fingerprint(binding)!=plan['binding_sha256']:raise RuntimeFault('FEISHU_BINDING_CHANGED')
            if db.execute('SELECT digest FROM projects WHERE id=?',(project,)).fetchone()[0]!=plan['configuration_digest']:raise RuntimeFault('PROJECT_CONFIGURATION_CHANGED')
            self._sku(db,project,fields['sku_id'])
            old=meta(db,source_key(project,task,revision))
            if old:
                if old['import_plan_sha256']!=expected_plan:raise RuntimeFault('FEISHU_IMPORT_CONFLICT')
                row=self.store._current(db,project,task,revision)
                return {'project':project,'task':task,'revision':revision,'state':row['state'],'replayed':True}
            # A task ID cannot silently switch source record between revisions.
            if expected_revision:
                previous=meta(db,source_key(project,task,expected_revision))
                if not previous or previous['snapshot']['record_id']!=record or previous['snapshot']['target']!=snapshot['target']:
                    raise RuntimeFault('FEISHU_TASK_SOURCE_CONFLICT')
                if previous['snapshot']['input']['source_revision']==fields['source_revision']:
                    raise RuntimeFault('FEISHU_SOURCE_REVISION_MUST_ADVANCE')
            result=self.store._create_task(db,plan['actor'],project,task,{k:fields[k] for k in ('sku_id','script','source_revision')},expected_revision)
            save(db,source_key(project,task,revision),{'snapshot':snapshot,'import_plan_sha256':expected_plan})
        return {**result,'feishu_writes':0,'paid_execution_enabled':False}

    def prepare_review(self,user_token,project,task,revision,stage,decision,feedback=''):
        if stage not in ('script','video') or decision not in ('accept','reject'):raise RuntimeFault('REVIEW_INVALID')
        if not isinstance(feedback,str) or len(feedback)>8000 or (decision=='reject' and not feedback.strip()):raise RuntimeFault('REVIEW_FEEDBACK_REQUIRED')
        role=stage+'_reviewers'
        client,identity,binding,actor,config,deployment=self._session(user_token,project,role)
        with self.store.connect() as db:
            row=self.store._current(db,project,task,revision);source=meta(db,source_key(project,task,revision))
            if not source:raise RuntimeFault('FEISHU_TASK_SOURCE_MISSING')
            if row['state']!='awaiting_'+stage+'_review':raise RuntimeFault('REVIEW_STATE_CONFLICT')
            task_input=row['input'];artifact=row['artifact']
        snapshot=self._snapshot(client,binding,source['snapshot']['record_id'])
        if snapshot!=source['snapshot']:raise RuntimeFault('FEISHU_SOURCE_CHANGED_RECONCILE_BEFORE_REVIEW')
        plan={'deployment':deployment,'project':project,'task':task,'revision':revision,'actor':actor,
              'stage':stage,'decision':decision,'feedback':feedback,'binding_sha256':fingerprint(binding),
              'configuration_digest':config,'snapshot':snapshot,'input_sha256':fingerprint(json.loads(task_input)),
              'artifact':json.loads(artifact) if artifact else None}
        with self.store.connect() as db:
            self._fence(db,project,binding,identity,role,config)
            current=self.store._current(db,project,task,revision)
            if current['input']!=task_input or current['artifact']!=artifact or current['state']!='awaiting_'+stage+'_review':raise RuntimeFault('REVIEW_STATE_CONFLICT')
        return {'plan':plan,'plan_sha256':fingerprint(plan),'feishu_writes':0}

    def review(self,user_token,project,task,revision,stage,decision,event,expected_plan,feedback=''):
        if stage not in ('script','video'):raise RuntimeFault('REVIEW_INVALID')
        role=stage+'_reviewers'
        identifier(event)
        # Verify the live user and current membership even for receipt replays.
        _,identity,binding,actor,config,_=self._session(user_token,project,role)
        key='feishu:review:'+fingerprint([project,actor,event])
        request=fingerprint([project,actor,task,revision,stage,decision,feedback,expected_plan])
        with self.store.connect() as db:
            self._fence(db,project,binding,identity,role,config)
            old=meta(db,key)
            if old:
                if old['request']!=request:raise RuntimeFault('EVENT_ID_CONFLICT')
                return {**old['receipt'],'replayed':True}
        prepared=self.prepare_review(user_token,project,task,revision,stage,decision,feedback);plan=prepared['plan']
        if prepared['plan_sha256']!=expected_plan:raise RuntimeFault('FEISHU_REVIEW_PLAN_CHANGED')
        with self.store.connect() as db:
            self._fence(db,project,binding,identity,role,config)
            if plan['actor']!=actor or plan['binding_sha256']!=fingerprint(binding):raise RuntimeFault('FEISHU_BINDING_CHANGED')
            current=self.store._current(db,project,task,revision)
            if fingerprint(json.loads(current['input']))!=plan['input_sha256'] or (json.loads(current['artifact']) if current['artifact'] else None)!=plan['artifact']:
                raise RuntimeFault('FEISHU_REVIEW_PLAN_CHANGED')
            receipt=self.store._review(db,actor,'fs_'+fingerprint([project,actor,event]),project,task,revision,stage,decision,feedback)
            save(db,key,{'request':request,'receipt':receipt})
        return {**receipt,'feishu_writes':0,'paid_execution_enabled':False}
