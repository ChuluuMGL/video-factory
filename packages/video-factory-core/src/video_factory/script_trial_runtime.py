"""Copy-only trial lifecycle. No approval creation, video dispatch or retries."""
import hashlib
import json
import time
from .runtime_store import RuntimeFault, fingerprint, identifier
from .feishu_bridge import FeishuBridge, meta, save, plain_text, select_text
from .feishu_native_review import EVENT_TYPE, _changed
from .feishu_client import resource
from .script_trial import PREFIX
from .script_jobs import key as job_key
from .script_trial_writeback import TrialDraftWriter

CONFIG = 'trial:review:'
QUEUE = 'trial:event:'
PROJECTION = 'trial:projection:'


def window(store, project):
    """Persist expiry once; only closes a window, can never open one."""
    with store.connect() as db:
        value = meta(db, PREFIX + project)
        if not value: raise RuntimeFault('TRIAL_NOT_CONFIGURED')
        if value['t1'] is not None and time.time() >= value['t1']:
            if value['enabled']:
                value['enabled'] = False
                save(db, PREFIX + project, value)
                store.audit(db, 'trial-clock', 'script_trial_expired', project)
            return 'expired'
        if not value['enabled'] or value['t0'] is None: return 'paused'
        return 'waiting' if time.time() < value['t0'] else 'open'


class TrialCopyReview:
    """SDK events are identity evidence; a cell value alone never approves."""
    def __init__(self, store, project, client):
        self.store, self.project, self.client = store, project, client
        self.bridge = FeishuBridge(store)

    def configure(self, admin, *, app_id, names, fields):
        required = {'task', 'input_digest', 'source_revision', 'candidate_version',
                    'video_script', 'caption_voiceover_script', 'generation_prompt', 'status', 'feedback'}
        if (set(names) != required or len(set(names.values())) != len(required)
                or any(not isinstance(v, str) or not v for v in names.values())
                or set(fields) != {'status'}):
            raise RuntimeFault('TRIAL_REVIEW_MAPPING_INVALID')
        with self.store.connect() as db:
            actor = self.store.authorize(db, admin, 'admin')
            binding = self.bridge._binding(db, self.project)
            trial = meta(db, PREFIX + self.project)
            app = meta(db, 'setup:feishu-app:' + self.project)
            if (not trial or not app or app['app_id'] != app_id
                    or (binding['base_token'], binding['table_id']) != (trial['copy_base'], trial['copy_table'])
                    or trial['copy_base'] == trial['production_base']):
                raise RuntimeFault('TRIAL_TARGET_MISMATCH')
            config = {'app_id': app_id, 'names': names, 'fields': fields,
                      'binding_sha256': fingerprint(binding)}
            old = meta(db, CONFIG + self.project)
            if old and old != config: raise RuntimeFault('TRIAL_REVIEW_CONFIGURATION_CHANGED')
            if not old:
                save(db, CONFIG + self.project, config)
                self.store.audit(db, actor, 'trial_review_configured', self.project)
        return {'configured': True, 'provider_requests': 0}

    def context(self, db):
        binding = self.bridge._binding(db, self.project)
        config = meta(db, CONFIG + self.project)
        trial = meta(db, PREFIX + self.project)
        if (not config or not trial or config['binding_sha256'] != fingerprint(binding)
                or (binding['base_token'], binding['table_id']) != (trial['copy_base'], trial['copy_table'])
                or trial['copy_base'] == trial['production_base']):
            raise RuntimeFault('TRIAL_REVIEW_CONFIGURATION_CHANGED')
        return binding, config

    def publish(self, task, revision, record):
        with self.store.connect() as db:
            binding, config = self.context(db)
        names = config['names']
        writer = TrialDraftWriter(self.store, self.client, base=binding['base_token'],
            table=binding['table_id'], names={k:v for k,v in names.items() if k not in ('status','feedback')})
        result = writer.write(self.project, task, revision, record)
        projection_key = PROJECTION + fingerprint([self.project, record])
        version = str(revision) + ':' + result['receipt_sha256']
        with self.store.connect() as db:
            prior = meta(db, projection_key)
            projection = {'task':task, 'revision':revision, 'version':version}
            if prior and prior != projection: raise RuntimeFault('TRIAL_PROJECTION_CONFLICT')
            save(db, projection_key, projection)
        remote = self.client.record(binding['base_token'], binding['table_id'], record)['fields']
        if plain_text(remote.get(names['candidate_version'])) != version:
            raise RuntimeFault('TRIAL_PROJECTION_CHANGED')
        status = select_text(remote[names['status']]) if remote.get(names['status']) else ''
        if status in ('', '未开始', '费用已批准', '已计划', '生成中'):
            self.client.update_record(binding['base_token'], binding['table_id'], record,
                                      {names['status']:'脚本待审核'})
            saved = self.client.record(binding['base_token'], binding['table_id'], record)['fields']
            if select_text(saved.get(names['status'])) not in ('脚本待审核','脚本通过','脚本退回'):
                raise RuntimeFault('TRIAL_STATUS_READBACK_FAILED')
        elif status not in ('脚本待审核','脚本通过','脚本退回'):
            raise RuntimeFault('TRIAL_STATUS_CONFLICT')
        return result

    def enqueue_verified(self, envelope):
        header, event = envelope.get('header', {}), envelope.get('event', {})
        if envelope.get('schema') != '2.0' or header.get('event_type') != EVENT_TYPE:
            raise RuntimeFault('FEISHU_EVENT_INVALID')
        event_id = resource(header.get('event_id'))
        operator = event.get('operator_id') or {}
        # Application writes have no human open_id; they cannot approve.
        if not operator.get('open_id'): return {'status':'ignored_nonhuman'}
        resource(operator['open_id'], 'ou_')
        actions = event.get('action_list')
        if not isinstance(actions, list) or not 1 <= len(actions) <= 100:
            raise RuntimeFault('FEISHU_EVENT_ACTIONS_INVALID')
        with self.store.connect() as db:
            binding, config = self.context(db)
            if (header.get('app_id') != config['app_id'] or header.get('tenant_key') != binding['tenant_key']
                    or event.get('file_token') != binding['base_token'] or event.get('table_id') != binding['table_id']
                    or event.get('file_type') != 'bitable'):
                raise RuntimeFault('FEISHU_EVENT_SCOPE_DENIED')
            reduced = {'event_id':event_id, 'operator':operator['open_id'], 'config_sha256':fingerprint(config), 'actions':[]}
            for action in actions:
                if action.get('action') != 'record_edited': continue
                record = resource(action.get('record_id'), 'rec')
                sides = {side:[x for x in action.get(side, []) if x.get('field_id') == config['fields']['status']]
                         for side in ('before_value','after_value')}
                if any(sides.values()): reduced['actions'].append({'record_id':record, **sides})
            if not reduced['actions']: return {'status':'ignored_no_status'}
            k = QUEUE + self.project + ':' + fingerprint(event_id)
            old = meta(db, k)
            if old:
                if old['event'] != reduced: raise RuntimeFault('FEISHU_EVENT_ID_CONFLICT')
                return {'status':old['status'], 'replayed':True}
            save(db, k, {'status':'pending','event':reduced})
        return {'status':'queued'}

    def process_one(self):
        prefix = QUEUE + self.project + ':'
        with self.store.connect() as db:
            rows = db.execute('SELECT key,value FROM meta WHERE substr(key,1,?)=? ORDER BY key LIMIT 1001',
                              (len(prefix), prefix)).fetchall()
        if len(rows) > 1000: raise RuntimeFault('TRIAL_EVENT_QUEUE_CAPACITY')
        for row in rows:
            saved = json.loads(row['value'])
            if saved['status'] != 'pending': continue
            try: result = self.consume(saved['event'])
            except RuntimeFault as error:
                # Stable refusals are recorded; transport failures remain pending.
                if str(error) not in {'FEISHU_TENANT_OR_ROLE_DENIED','TRIAL_REVIEW_STALE',
                                     'TRIAL_REVIEW_FEEDBACK_REQUIRED','TRIAL_REVIEW_TRANSITION_INVALID'}: raise
                result = {'status':'denied','reason':str(error)}
            with self.store.connect() as db:
                if meta(db, row['key']) != saved: raise RuntimeFault('TRIAL_EVENT_CHANGED')
                save(db, row['key'], {**saved,'status':'done','result':result})
            return result
        return {'status':'idle'}

    def consume(self, event):
        with self.store.connect() as db:
            binding, config = self.context(db)
            if fingerprint(config) != event['config_sha256']: raise RuntimeFault('TRIAL_REVIEW_STALE')
        names = config['names']
        fields = [x for x in self.client.fields(binding['base_token'], binding['table_id']) if x['field_id']==config['fields']['status']]
        if len(fields) != 1 or fields[0]['type'] != 3: raise RuntimeFault('TRIAL_REVIEW_STALE')
        options = {v['id']:v['name'] for v in fields[0]['property']['options']}
        reviewed = []
        for action in event['actions']:
            change = _changed(action, config['fields']['status'], options)
            if not change or change[1] not in ('脚本通过','脚本退回'): continue
            # Only review transitions require reviewer authority. App/status
            # updates cannot become approvals, and are not misreported as denied people.
            actor = self.bridge._member(binding, {'tenant_key':binding['tenant_key'],'open_id':event['operator']}, 'script_reviewers')
            if change[0] != '脚本待审核': raise RuntimeFault('TRIAL_REVIEW_TRANSITION_INVALID')
            record = action['record_id']
            receipt_key = 'trial:decision:' + fingerprint([self.project,event['event_id'],record])
            with self.store.connect() as db:
                old = meta(db, receipt_key)
                if old: reviewed.append(old); continue
                projection = meta(db, PROJECTION + fingerprint([self.project,record]))
                if not projection: raise RuntimeFault('TRIAL_REVIEW_STALE')
                task, rev = projection['task'], projection['revision']
                current = self.store._current(db, self.project, task, rev)
                job = meta(db, job_key(self.project,task,rev))
                if current['state'] != 'awaiting_script_review' or not job or job['state'] != 'received':
                    raise RuntimeFault('TRIAL_REVIEW_STALE')
                digest = current['input_digest']
            remote = self.client.record(binding['base_token'],binding['table_id'],record)['fields']
            result = job['result']
            expected = {'task':task,'candidate_version':projection['version'],
                        'input_digest':result['prepared_input_sha256'],'source_revision':result['prepared_source_revision'],
                        **result['structured_fields']}
            if (any(plain_text(remote.get(names[k])) != v for k,v in expected.items())
                    or select_text(remote.get(names['status'])) != change[1]):
                raise RuntimeFault('TRIAL_REVIEW_STALE')
            feedback = plain_text(remote[names['feedback']]) if remote.get(names['feedback']) else ''
            decision = 'accept' if change[1]=='脚本通过' else 'reject'
            if decision=='reject' and not feedback.strip(): raise RuntimeFault('TRIAL_REVIEW_FEEDBACK_REQUIRED')
            with self.store.connect() as db:
                b,c = self.context(db)
                if b != binding or c != config or meta(db,job_key(self.project,task,rev)) != job:
                    raise RuntimeFault('TRIAL_REVIEW_STALE')
                current = self.store._current(db,self.project,task,rev)
                if current['state']!='awaiting_script_review' or current['input_digest']!=digest:
                    raise RuntimeFault('TRIAL_REVIEW_STALE')
                receipt = self.store._review(db,actor,'trial_'+fingerprint([self.project,event['event_id'],record]),
                    self.project,task,rev,'script',decision,feedback)
                save(db, receipt_key, receipt)
                reviewed.append(receipt)
        return {'status':'processed','reviewed':reviewed,'provider_requests':0}


class TrialRunner:
    """Single tick, called under scheduler.runtime_lock by a service supervisor."""
    def __init__(self, store, project, jobs, review, records, *, token, master_key):
        self.store,self.project,self.jobs,self.review=store,project,jobs,review
        self.records=dict(records); self.token=token;self.master_key=master_key

    def tick(self):
        state = window(self.store,self.project)
        if state != 'open': return {'state':state,'provider_requests':0}
        # Review cannot authorize a model call. ScriptJobs checks each paid plan.
        review = self.review.process_one()
        with self.store.connect() as db:
            rows = db.execute('SELECT t.id,t.revision,t.state FROM tasks t WHERE t.project=? AND t.revision=(SELECT max(n.revision) FROM tasks n WHERE n.project=t.project AND n.id=t.id) ORDER BY t.id', (self.project,)).fetchall()
            if len(rows)>100: raise RuntimeFault('TRIAL_TASK_LIMIT')
            trial=meta(db,PREFIX+self.project)
            quarantined={(q['task'],q['revision']) for q in trial.get('quarantined_requests',{}).values()}
            checkpoint=trial.get('checkpoint_task',sorted(self.records)[0])
            if checkpoint not in self.records:raise RuntimeFault('TRIAL_CHECKPOINT_NOT_REGISTERED')
        for row in rows:
            if row['id'] not in self.records: raise RuntimeFault('TRIAL_TASK_NOT_REGISTERED')
            if (row['id'],row['revision']) in quarantined:
                if row['state']!='script_submission_unknown':raise RuntimeFault('TRIAL_QUARANTINE_CHANGED')
                continue
            if row['state']=='script_submission_unknown': return {'state':'attention_required','provider_requests':0}
            if row['state']=='script_queued':
                outcome=self.jobs.step(self.token,self.project,row['id'],row['revision'],self.master_key)
                return {'state':'stepped','task':row['id'],'review':review,'outcome':outcome}
            if row['state']=='awaiting_script_review':
                self.review.publish(row['id'],row['revision'],self.records[row['id']])
                # First draft is a human checkpoint; never run the next task until accepted.
                if row['id'] == checkpoint:
                    return {'state':'awaiting_review','task':row['id'],'provider_requests':0}
                continue
            if row['state'] in ('failed','rejected'):
                return {'state':'attention_required','task':row['id'],'provider_requests':0}
        return {'state':'idle','provider_requests':0,'review':review}


class RefreshingTrialClient:
    """Refresh application credentials before a request, never replay a failed write."""
    def __init__(self, factory, *, refresh_seconds=1200, clock=time.monotonic):
        if not 1 <= refresh_seconds <= 1200: raise ValueError('refresh interval')
        self.factory,self.interval,self.clock=factory,refresh_seconds,clock
        self.client=None;self.deadline=0

    def __getattr__(self, method):
        if method not in ('record','fields','update_record','post','append'):
            raise AttributeError(method)
        def call(*args, **kwargs):
            now=self.clock()
            if self.client is None or now>=self.deadline:
                self.client=self.factory();self.deadline=now+self.interval
            return getattr(self.client,method)(*args,**kwargs)
        return call
