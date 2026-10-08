"""Original task-table delivery of generated drafts and verified video.

Each mutation has a durable intent. An ambiguous response requires remote
readback or an operator repair; it never repeats a paid provider request.
"""
import hashlib
import json
import time
import uuid

from .base_results import BaseResults
from .feishu_bridge import meta, save, plain_text
from .feishu_client import resource
from .feishu_results_client import ResultClient, BLOCK_SIZE
from .feishu_native_review import source_key
from .runtime_store import RuntimeFault, fingerprint, identifier

REVIEW_FIELDS = {'status': ('状态', 1), 'review_revision': ('审核目标版本', 1),
                 'feedback': ('审核意见', 1), 'video_digest': ('视频摘要', 1),
                 'video': ('视频', 17)}
SYNC_STATES = {'awaiting_script_review': '脚本待审核',
               'awaiting_video_review': '视频待审核'}


def config_key(project): return 'native:config:'+identifier(project)
def item_key(project, task, revision):
    return 'native:sync:'+fingerprint([identifier(project), identifier(task), revision])


def schema(client, base, table, binding):
    by_name = {}
    for field in client.fields(base, table):
        name = field.get('field_name')
        if name in {v[0] for v in REVIEW_FIELDS.values()}:
            if name in by_name: raise RuntimeFault('FEISHU_NATIVE_SCHEMA_AMBIGUOUS')
            by_name[name] = field
    found = {}
    for key, (name, kind) in REVIEW_FIELDS.items():
        field = by_name.get(name)
        if field:
            if field.get('type') != kind: raise RuntimeFault('FEISHU_NATIVE_SCHEMA_CONFLICT')
            found[key] = resource(field.get('field_id'), 'fld')
    from .feishu_bridge import FeishuBridge
    base_names = FeishuBridge._schema(client, binding)
    return found, base_names | {key: REVIEW_FIELDS[key][0] for key in found}


class NativeSync:
    def __init__(self, store, master_key, *, client_factory=ResultClient.application):
        self.store, self.master_key, self.client_factory = store, master_key, client_factory
        self.results = BaseResults(store, master_key, client_factory=client_factory)

    def client(self, context): return self.results.client(context)

    def prepare(self, admin, project):
        with self.store.connect() as db:
            self.store.authorize(db, admin, 'admin')
            context = self.results.context(db, project)
            old = meta(db, config_key(project))
            if old and old['context'] != context: raise RuntimeFault('FEISHU_NATIVE_CONTEXT_CHANGED')
            binding = self.results.bridge._binding(db, project)
        client = self.client(context)
        fields, _ = schema(client, binding['base_token'], binding['table_id'], binding)
        plan = {'project': project, 'context': context, 'existing': fields,
                'missing': [key for key in REVIEW_FIELDS if key not in fields],
                'subscription': 'app_identity_document_manager_required',
                'writes_original_table': True, 'model_calls': 0,
                'journal_sha256': fingerprint(old)}
        return {'plan': plan, 'plan_sha256': fingerprint(plan)}

    def enable(self, admin, project, expected_plan):
        prepared = self.prepare(admin, project)
        if prepared['plan_sha256'] != expected_plan: raise RuntimeFault('FEISHU_NATIVE_PLAN_CHANGED')
        context = prepared['plan']['context']; target = context['target']
        with self.store.connect() as db:
            actor = self.store.authorize(db, admin, 'admin')
            if self.results.context(db, project) != context: raise RuntimeFault('FEISHU_NATIVE_CONTEXT_CHANGED')
            old = meta(db, config_key(project))
            if fingerprint(old) != prepared['plan']['journal_sha256']:
                raise RuntimeFault('FEISHU_NATIVE_PLAN_CHANGED')
            if old is None:
                old = {'context': context, 'app_id': context['app_profile']['app_id'],
                       'enabled': False, 'fields': {}, 'names': {}, 'steps': {}}
                save(db, config_key(project), old)
        client = self.client(context)
        binding = {'fields': target['fields'], **target}
        for logical, (name, kind) in REVIEW_FIELDS.items():
            found, names = schema(client, target['base_token'], target['table_id'], binding)
            if logical not in found:
                with self.store.connect() as db:
                    self.store.authorize(db, admin, 'admin')
                    current = meta(db, config_key(project)); step = current['steps'].get(logical)
                    if step and step['status'] != 'in_flight': raise RuntimeFault('FEISHU_NATIVE_SCHEMA_JOURNAL_INVALID')
                    if not step:
                        step = {'status': 'in_flight', 'ticket': str(uuid.uuid4())}
                        current['steps'][logical] = step; save(db, config_key(project), current)
                # A repeated call uses the same UUID. The documented field API
                # accepts client_token for idempotency, and readback is checked.
                client.create_field(target['base_token'], target['table_id'], name, kind, step['ticket'])
                found, names = schema(client, target['base_token'], target['table_id'], binding)
                if logical not in found: raise RuntimeFault('FEISHU_NATIVE_FIELD_READBACK_FAILED')
            with self.store.connect() as db:
                current = meta(db, config_key(project))
                current['steps'][logical] = {'status': 'done', 'field_id': found[logical]}
                save(db, config_key(project), current)
        found, names = schema(client, target['base_token'], target['table_id'], binding)
        if set(found) != set(REVIEW_FIELDS): raise RuntimeFault('FEISHU_NATIVE_SCHEMA_INCOMPLETE')
        # Subscription is a distinct, non-idempotent external operation. A
        # lost response leaves an attention state instead of auto-submitting.
        with self.store.connect() as db:
            self.store.authorize(db, admin, 'admin')
            current = meta(db, config_key(project)); subscribed = current['steps'].get('subscription')
            if not subscribed:
                current['steps']['subscription'] = {'status': 'in_flight'}
                save(db, config_key(project), current)
        if not subscribed:
            if not client.base_subscription_status(target['base_token']):
                client.subscribe_base(target['base_token'])
        if not client.base_subscription_status(target['base_token']):
            raise RuntimeFault('FEISHU_NATIVE_SUBSCRIPTION_NOT_CONFIRMED')
        if not subscribed or subscribed['status'] == 'in_flight':
            with self.store.connect() as db:
                current = meta(db, config_key(project))
                current['steps']['subscription'] = {'status': 'done'}
                save(db, config_key(project), current)
        with self.store.connect() as db:
            self.store.authorize(db, admin, 'admin')
            current = meta(db, config_key(project))
            current.update(enabled=True, fields=found, names=names)
            save(db, config_key(project), current)
            self.store.audit(db, actor, 'feishu_native_review_enabled', project)
        return {'project': project, 'status': 'enabled', 'writes_original_table': True,
                'subscription': 'registered', 'event_delivery': 'requires_live_readback'}

    def status(self, admin, project):
        with self.store.connect() as db:
            self.store.authorize(db, admin, 'admin')
            value = meta(db, config_key(project))
        return {'project': project, 'enabled': bool(value and value['enabled']),
                'fields': value['fields'] if value else {},
                'subscription': value['steps'].get('subscription') if value else None}

    def sync(self, project, media_root, authorize):
        with self.store.connect() as db:
            authorize(db)
            config = meta(db, config_key(project))
            if not config or not config['enabled']: return {'status': 'disabled', 'feishu_writes': 0}
            if self.results.context(db, project) != config['context']:
                raise RuntimeFault('FEISHU_NATIVE_CONTEXT_CHANGED')
            rows = db.execute("SELECT * FROM tasks WHERE project=? AND state IN ('awaiting_script_review','awaiting_video_review') ORDER BY id LIMIT 1001",(project,)).fetchall()
            if len(rows)>1000: raise RuntimeFault('FEISHU_NATIVE_QUEUE_CAPACITY')
            selected = None
            for row in rows:
                item = meta(db, item_key(project, row['id'], row['revision']))
                if not item or not item['complete']:
                    selected = dict(row); break
            if selected is None: return {'status': 'idle', 'feishu_writes': 0}
        return self._sync_one(project, selected, config, media_root, authorize)

    def _sync_one(self, project, row, config, media_root, authorize):
        context = config['context']; target = context['target']; base = target['base_token']; table = target['table_id']
        client = self.client(context); binding = {'fields': target['fields'], **target}
        fields, names = schema(client, base, table, binding)
        if fields != config['fields'] or names != config['names']:
            raise RuntimeFault('FEISHU_NATIVE_SCHEMA_CHANGED')
        task, revision = row['id'], row['revision']; key = item_key(project, task, revision)
        with self.store.connect() as db:
            source = meta(db, source_key(project, task))
        if source:
            record = client.record(base, table, source['record_id'])
        else:
            record = client.find_task(base, table, names['task'], task)
        record_id = record['record_id']; payload = json.loads(row['input'])
        if plain_text(record['fields'].get(names['task'])) != task or plain_text(record['fields'].get(names['sku_id'])) != payload['sku_id']:
            raise RuntimeFault('FEISHU_NATIVE_TASK_SOURCE_CONFLICT')
        if source and source['record_id'] != record_id: raise RuntimeFault('FEISHU_NATIVE_TASK_SOURCE_CONFLICT')
        with self.store.connect() as db:
            authorize(db)
            current = self.store._current(db, project, task, revision)
            if current['state'] != row['state'] or current['input_digest'] != row['input_digest']:
                raise RuntimeFault('FEISHU_NATIVE_TASK_CHANGED')
            if self.results.context(db, project) != context: raise RuntimeFault('FEISHU_NATIVE_CONTEXT_CHANGED')
            if not source:
                save(db, source_key(project, task), {'record_id': record_id})
            save(db, 'native:record:'+project+':'+record_id, {'record_id': record_id, 'task': task, 'revision': revision})
            item = meta(db, key)
            artifact = json.loads(row['artifact']) if row['artifact'] else None
            snapshot = {'task': task, 'revision': revision, 'state': row['state'],
                        'input_digest': row['input_digest'], 'artifact': artifact, 'record_id': record_id}
            if item and item['snapshot'] != snapshot: raise RuntimeFault('FEISHU_NATIVE_SNAPSHOT_CHANGED')
            if not item:
                item = {'snapshot': snapshot, 'steps': {}, 'complete': False}
                save(db, key, item)
        expected = {names['status']: SYNC_STATES[row['state']], names['review_revision']: str(revision),
                    names['feedback']: ''}
        if row['state'] == 'awaiting_script_review':
            expected[names['script']] = payload['script']
        else:
            if not artifact: raise RuntimeFault('FEISHU_NATIVE_VIDEO_MISSING')
            expected[names['video_digest']] = artifact['sha256']
            upload = item['steps'].get('finish', {})
            if upload.get('status') == 'done': expected[names['video']] = [{'file_token': upload['receipt']}]
            else:
                self._upload_step(client, base, key, item, artifact, media_root, project, context, authorize)
                return {'status': 'video_upload_in_progress', 'task': task, 'revision': revision, 'feishu_writes': 1}
        remote = client.record(base, table, record_id)['fields']
        def matches():
            for name, value in expected.items():
                actual = remote.get(name)
                if value == '' and actual in (None, '', []): continue
                if isinstance(value, list):
                    if not isinstance(actual, list) or [x.get('file_token') for x in actual] != [x['file_token'] for x in value]: return False
                else:
                    try:
                        if plain_text(actual) != value: return False
                    except RuntimeFault: return False
            return True
        if matches():
            with self.store.connect() as db:
                authorize(db); current = meta(db, key)
                if current['snapshot'] != snapshot: raise RuntimeFault('FEISHU_NATIVE_SNAPSHOT_CHANGED')
                current['complete'] = True; save(db, key, current)
            return {'status': 'synced', 'task': task, 'revision': revision, 'feishu_writes': 0}
        status = remote.get(names['status']) or ''
        if status: status = plain_text(status)
        allowed = ('', '脚本退回') if row['state']=='awaiting_script_review' and revision>1 else (
            ('',) if row['state']=='awaiting_script_review' else ('脚本通过',))
        if status not in allowed:
            raise RuntimeFault('FEISHU_NATIVE_REMOTE_REVIEW_CONFLICT')
        with self.store.connect() as db:
            authorize(db); current = meta(db, key)
            if current['steps'].get('record'):
                raise RuntimeFault('FEISHU_NATIVE_RECORD_WRITE_UNKNOWN_OR_CONFLICT')
            current['steps']['record'] = {'status': 'in_flight', 'fields_sha256': fingerprint(expected)}
            save(db, key, current)
        client.update_record(base, table, record_id, expected)
        remote = client.record(base, table, record_id)['fields']
        if matches():
            with self.store.connect() as db:
                authorize(db); current = meta(db, key)
                if current['snapshot'] != snapshot: raise RuntimeFault('FEISHU_NATIVE_SNAPSHOT_CHANGED')
                current['complete'] = True; save(db, key, current)
            return {'status': 'synced', 'task': task, 'revision': revision, 'feishu_writes': 1}
        return {'status': 'record_written_readback_pending', 'task': task, 'revision': revision, 'feishu_writes': 1}

    def _upload_step(self, client, base, key, item, artifact, media_root, project, context, authorize):
        from .review_service import open_media
        stream, size = open_media(media_root, artifact, artifact['sha256'])
        with stream:
            steps = item['steps']
            if 'prepare' not in steps:
                hashes = []; digest = hashlib.sha256()
                while chunk := stream.read(BLOCK_SIZE):
                    digest.update(chunk); hashes.append(hashlib.sha256(chunk).hexdigest())
                if digest.hexdigest() != artifact['sha256']: raise RuntimeFault('FEISHU_NATIVE_MEDIA_CHANGED')
                self._step(key, 'prepare', lambda: client.upload_prepare(base, item['snapshot']['task']+'-v'+str(item['snapshot']['revision'])+'.mp4', size)|{'chunk_sha256': hashes}, project, context, authorize)
                return
            receipt = steps['prepare']['receipt']
            if time.time()-steps['prepare']['started_at'] >= 23*3600:
                raise RuntimeFault('FEISHU_NATIVE_UPLOAD_EXPIRED')
            for seq in range(receipt['block_num']):
                name = 'part_'+str(seq)
                if name not in steps:
                    stream.seek(seq*BLOCK_SIZE); chunk = stream.read(BLOCK_SIZE)
                    if hashlib.sha256(chunk).hexdigest() != receipt['chunk_sha256'][seq]:
                        raise RuntimeFault('FEISHU_NATIVE_MEDIA_CHANGED')
                    self._step(key, name, lambda: client.upload_part(receipt['upload_id'], seq, chunk), project, context, authorize)
                    return
            self._step(key, 'finish', lambda: client.upload_finish(receipt['upload_id'], receipt['block_num']), project, context, authorize)

    def _step(self, key, name, operation, project, context, authorize):
        with self.store.connect() as db:
            authorize(db)
            if self.results.context(db, project) != context: raise RuntimeFault('FEISHU_NATIVE_CONTEXT_CHANGED')
            item = meta(db, key)
            if name in item['steps']: raise RuntimeFault('FEISHU_NATIVE_WRITE_UNKNOWN_READ_STATUS')
            item['steps'][name] = {'status': 'in_flight', 'started_at': time.time()}
            save(db, key, item)
        receipt = operation()
        with self.store.connect() as db:
            item = meta(db, key)
            item['steps'][name].update(status='done', receipt=receipt)
            save(db, key, item)
