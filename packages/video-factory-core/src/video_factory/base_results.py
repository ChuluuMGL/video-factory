"""Opt-in Base result snapshots with durable intents and append-only writes.

Every network mutation has a committed intent first. Uncertain records are
queried by immutable event ID; an empty read is never permission to resubmit.
Uploads with a lost receipt stop for operator reconciliation. No PUT/DELETE.
"""
import hashlib
import json
import re
import secrets
import time
import uuid

from .feishu_bridge import FeishuBridge, meta, save, target, plain_text
from .feishu_client import resource
from .feishu_results_client import ResultClient, FIELDS, BLOCK_SIZE
from .runtime_store import RuntimeFault, canonical, fingerprint, identifier

STATES = {'script_queued': '脚本排队', 'script_submission_unknown': '脚本提交待核实',
          'awaiting_script_review': '脚本待审核', 'ready': '待生成视频',
          'submission_unknown': '视频提交待核实', 'submitted': '视频生成中',
          'awaiting_video_review': '视频待审核', 'accepted': '已通过',
          'rejected': '已退回', 'failed': '失败'}


def control_key(project): return 'results:config:'+identifier(project)
def item_prefix(project): return 'results:item:'+identifier(project)+':'


def journal_rows(db, project, limit=10001):
    # Text range ordering differs between SQLite and PostgreSQL locales. Escape
    # LIKE metacharacters so a project containing '_' cannot match a neighbour.
    pattern = item_prefix(project).replace('\\', '\\\\').replace('_', '\\_')+'%'
    return db.execute("SELECT key,value FROM meta WHERE key LIKE ? ESCAPE '\\' ORDER BY key LIMIT ?", (pattern, limit)).fetchall()


def schema(client, base, table):
    fields = client.fields(base, table); found = {}
    for field in fields:
        name = field.get('field_name')
        if name in FIELDS:
            if name in found or type(field.get('type')) is not int or field['type'] != FIELDS[name]:
                raise RuntimeFault('BASE_RESULTS_SCHEMA_CHANGED')
            found[name] = resource(field.get('field_id'), 'fld')
    if set(found) != set(FIELDS) or len(set(found.values())) != len(FIELDS):
        raise RuntimeFault('BASE_RESULTS_SCHEMA_CHANGED')
    return found


def snapshot(project, row):
    payload = json.loads(row['input']); artifact = json.loads(row['artifact']) if row['artifact'] else None
    if row['state'] not in STATES: raise RuntimeFault('BASE_RESULTS_STATE_UNSUPPORTED')
    digest = artifact['sha256'] if artifact else ''
    if artifact and not re.fullmatch('[a-f0-9]{64}', digest): raise RuntimeFault('BASE_RESULTS_MEDIA_INVALID')
    fields = {'项目': project, '任务': identifier(row['id']), '版本': str(row['revision']),
              'SKU': plain_text(payload['sku_id']), '状态': STATES[row['state']],
              '脚本': plain_text(payload['script']), '视频摘要': digest}
    fields['同步标识'] = fingerprint(fields)
    return {'fields': fields, 'task': row['id'], 'revision': row['revision'], 'artifact': artifact,
            'steps': {}, 'complete': False}


def matches(record, fields):
    actual = record.get('fields', {})
    for name, expected in fields.items():
        value = actual.get(name)
        if name == '视频':
            if not isinstance(value, list) or [item.get('file_token') for item in value] != [item['file_token'] for item in expected]:
                return False
        elif expected == '' and value in (None, '', []): continue
        else:
            try:
                if plain_text(value) != expected: return False
            except RuntimeFault: return False
    return True


class BaseResults:
    def __init__(self, store, master_key, *, client_factory=ResultClient.application):
        self.store, self.master_key, self.client_factory = store, master_key, client_factory
        self.bridge = FeishuBridge(store)

    def context(self, db, project):
        binding = self.bridge._binding(db, project)
        profile = meta(db, 'setup:feishu-app:'+project)
        if not profile or not profile.get('credential_ref', '').startswith('secret:'):
            raise RuntimeFault('BASE_RESULTS_APP_REQUIRED')
        credential = db.execute('SELECT revision FROM vault WHERE alias=?', (profile['credential_ref'][7:],)).fetchone()
        row = db.execute('SELECT digest FROM projects WHERE id=?', (project,)).fetchone()
        if not credential or not row: raise RuntimeFault('BASE_RESULTS_PROJECT_OR_SECRET_MISSING')
        return {'project': project, 'target': target(binding), 'app_profile': profile,
                'credential_revision': credential[0], 'configuration_sha256': row[0]}

    def client(self, context):
        profile = context['app_profile']
        return self.client_factory(profile['app_id'], self.store.resolve_secret(profile['credential_ref'][7:], self.master_key))

    def prepare(self, token, project):
        with self.store.connect() as db:
            self.store.authorize(db, token, 'admin')
            context = self.context(db, project); old = meta(db, control_key(project))
            if old and old.get('recovery_required'): raise RuntimeFault('BASE_RESULTS_RECOVERY_RECONCILIATION_REQUIRED')
            if old and old['context'] != context: raise RuntimeFault('BASE_RESULTS_CONFIGURATION_CHANGED')
        client = self.client(context)
        # Both the bound input table and the destination Base must be visible to
        # this app. The feature never grants itself document membership.
        self.bridge._schema(client, {'fields': context['target']['fields'], **context['target']})
        client.tables(context['target']['base_token'])
        plan = {'context': context, 'current_sha256': fingerprint(old), 'destination': 'new_product_owned_result_table',
                'writes_input_table': False, 'mode': 'append_observed_snapshots', 'uploads_video': True,
                'fields': FIELDS, 'model_calls': 0}
        return {'plan': plan, 'plan_sha256': fingerprint(plan)}

    def enable(self, token, project, expected_plan):
        prepared = self.prepare(token, project)
        if prepared['plan_sha256'] != expected_plan: raise RuntimeFault('BASE_RESULTS_PLAN_CHANGED')
        context = prepared['plan']['context']
        with self.store.connect() as db:
            actor = self.store.authorize(db, token, 'admin')
            if self.context(db, project) != context: raise RuntimeFault('BASE_RESULTS_CONFIGURATION_CHANGED')
            value = meta(db, control_key(project))
            if fingerprint(value) != prepared['plan']['current_sha256']: raise RuntimeFault('BASE_RESULTS_PLAN_CHANGED')
            if value is None:
                value = {'context': context, 'enabled': False, 'table_name': 'VF 生成结果 · '+project+' · '+secrets.token_hex(6),
                         'table_id': None, 'table_attempted': False, 'schema': None, 'generation': secrets.token_hex(16)}
                save(db, control_key(project), value)
        client = self.client(context); base = context['target']['base_token']
        if not value['table_id']:
            # Exact, unguessable product table name permits recovery of a lost
            # create response without issuing another create request.
            rows = [row for row in client.tables(base) if row.get('name') == value['table_name']]
            if len(rows) > 1: raise RuntimeFault('BASE_RESULTS_TABLE_CONFLICT')
            if rows:
                table = resource(rows[0].get('table_id'), 'tbl')
            else:
                with self.store.connect() as db:
                    self.store.authorize(db, token, 'admin')
                    current = meta(db, control_key(project))
                    if current != value or self.context(db, project) != context: raise RuntimeFault('BASE_RESULTS_PLAN_CHANGED')
                    if current['table_attempted']: raise RuntimeFault('BASE_RESULTS_TABLE_UNKNOWN_READ_STATUS')
                    current['table_attempted'] = True; save(db, control_key(project), current)
                try:
                    table = client.create_result_table(base, value['table_name'])
                except RuntimeFault as error:
                    if str(error) != 'BASE_RESULTS_REQUEST_REJECTED_91403': raise
                    # The upstream explicitly denied the POST. Preserve the
                    # same destination name and clear only its create intent;
                    # after the app gains document edit rights a later enable
                    # may try that same name once more.
                    with self.store.connect() as db:
                        current = meta(db, control_key(project))
                        if current != {**value, 'table_attempted': True}:
                            raise RuntimeFault('BASE_RESULTS_CONFIGURATION_CHANGED') from None
                        current['table_attempted'] = False
                        save(db, control_key(project), current)
                        self.store.audit(db, 'admin', 'base_results_create_forbidden', project)
                    raise RuntimeFault('BASE_RESULTS_APP_DOCUMENT_EDIT_REQUIRED') from None
            if table == context['target']['table_id']: raise RuntimeFault('BASE_RESULTS_INPUT_TABLE_DENIED')
            mapping = schema(client, base, table)
            with self.store.connect() as db:
                current = meta(db, control_key(project))
                if current['context'] != context or current.get('table_id') not in (None, table):
                    raise RuntimeFault('BASE_RESULTS_CONFIGURATION_CHANGED')
                # Preserve a known receipt even if the administrator expired.
                current.update(table_id=table, schema=mapping); save(db, control_key(project), current)
        with self.store.connect() as db:
            self.store.authorize(db, token, 'admin')
            current = meta(db, control_key(project))
            if (self.context(db, project) != context or current.get('recovery_required')
                    or current['generation'] != value['generation']):
                raise RuntimeFault('BASE_RESULTS_CONFIGURATION_CHANGED')
            current['enabled'] = True; save(db, control_key(project), current)
            self.store.audit(db, actor, 'base_results_enable', project)
        return self.status(token, project)

    def pause(self, token, project):
        with self.store.connect() as db:
            actor = self.store.authorize(db, token, 'admin'); value = meta(db, control_key(project))
            if value:
                value['enabled'] = False; value['generation'] = secrets.token_hex(16); save(db, control_key(project), value)
            self.store.audit(db, actor, 'base_results_pause', project)
        return self.status(token, project)

    def status(self, token, project):
        with self.store.connect() as db:
            self.store.authorize(db, token, 'admin')
            return self.summary(db, project)

    @staticmethod
    def summary(db, project):
        value = meta(db, control_key(project)); prefix = item_prefix(project)
        rows = journal_rows(db, project)
        items = [json.loads(row['value']) for row in rows]
        return {'project': project, 'enabled': bool(value and value['enabled']),
                'table_id': value.get('table_id') if value else None,
                'recovery_required': bool(value and value.get('recovery_required')),
                'synced': sum(item['complete'] for item in items),
                'pending': sum(not item['complete'] for item in items), 'counts_truncated': len(rows) > 10000,
                'blocked': [{'task': item['task'], 'revision': item['revision'], 'event': item['fields']['同步标识'],
                             'step': step} for item in items if not item['complete']
                            for step, receipt in item['steps'].items() if receipt['status'] == 'in_flight'],
                'writes_input_table': False, 'last_sync': meta(db, 'results:last:'+project)}

    def fence(self, db, project, context, authorize):
        authorize(db)
        value = meta(db, control_key(project))
        if not value or not value['enabled'] or value.get('recovery_required'):
            raise RuntimeFault('BASE_RESULTS_DISABLED_OR_RECOVERY_REQUIRED')
        if value['context'] != context or self.context(db, project) != context:
            raise RuntimeFault('BASE_RESULTS_CONFIGURATION_CHANGED')
        return value

    def repair_plan(self, token, project, event, step):
        if not isinstance(event, str) or not re.fullmatch('[a-f0-9]{64}', event):
            raise RuntimeFault('BASE_RESULTS_EVENT_INVALID')
        with self.store.connect() as db:
            self.store.authorize(db, token, 'admin')
            config = meta(db, control_key(project))
            if not config: raise RuntimeFault('BASE_RESULTS_NOT_CONFIGURED')
            self.fence(db, project, config['context'], lambda db: self.store.authorize(db, token, 'admin'))
            item = meta(db, item_prefix(project)+event)
            old = item['steps'].get(step) if item else None
            if (not old or old['status'] != 'in_flight' or item['complete']
                    or not (step in ('prepare', 'finish', 'record') or re.fullmatch('part_[0-9]{1,2}', step))):
                raise RuntimeFault('BASE_RESULTS_UNKNOWN_STEP_REQUIRED')
            if old.get('attempt', 1) >= 3: raise RuntimeFault('BASE_RESULTS_REPAIR_LIMIT')
        plan = {'project': project, 'event': event, 'step': step, 'task': item['task'],
                'config_sha256': fingerprint(config), 'journal_sha256': fingerprint(item),
                'same_record_ticket': step == 'record', 'same_upload_transaction': step != 'prepare',
                'may_leave_unused_upload_transaction': step == 'prepare', 'max_additional_attempts': 1}
        return {'plan': plan, 'plan_sha256': fingerprint(plan)}

    def repair(self, token, project, event, step, expected_plan):
        prepared = self.repair_plan(token, project, event, step)
        if prepared['plan_sha256'] != expected_plan: raise RuntimeFault('BASE_RESULTS_PLAN_CHANGED')
        with self.store.connect() as db:
            actor = self.store.authorize(db, token, 'admin')
            config = meta(db, control_key(project)); key = item_prefix(project)+event; item = meta(db, key)
            if (fingerprint(config) != prepared['plan']['config_sha256']
                    or fingerprint(item) != prepared['plan']['journal_sha256']):
                raise RuntimeFault('BASE_RESULTS_PLAN_CHANGED')
            old = item['steps'][step]
            history = old.get('history', [])+[{'ticket': old['ticket'], 'started_at': old['started_at'], 'status': old['status']}]
            old.update(status='retry_authorized', expires_at=time.time()+900, attempt=old.get('attempt', 1)+1, history=history)
            save(db, key, item); self.store.audit(db, actor, 'base_results_retry_once', project, item['task'], item['revision'])
        return {'status': 'one_retry_authorized', 'step': step, 'project': project, 'model_calls': 0}

    def recovery_plan(self, token, project):
        # Feishu binding must first be reverified by the existing setup flow.
        with self.store.connect() as db:
            self.store.authorize(db, token, 'admin'); context = self.context(db, project)
            config = meta(db, control_key(project)); prefix = item_prefix(project)
            if not config or not config.get('recovery_required') or not config['table_id']:
                raise RuntimeFault('BASE_RESULTS_RECOVERY_NOT_REQUIRED_OR_TABLE_UNKNOWN')
            if config['context'] != context: raise RuntimeFault('BASE_RESULTS_CONFIGURATION_CHANGED')
            rows = journal_rows(db, project)
            if len(rows) > 1000: raise RuntimeFault('BASE_RESULTS_RECOVERY_REQUIRES_BATCH_REVIEW')
            items = [(row['key'], json.loads(row['value'])) for row in rows]
            if any(not item['complete'] for _, item in items):
                raise RuntimeFault('BASE_RESULTS_RECOVERY_UNFINISHED_UPLOAD_OR_RECORD')
        client = self.client(context); base = context['target']['base_token']; table = config['table_id']
        if schema(client, base, table) != config['schema']: raise RuntimeFault('BASE_RESULTS_SCHEMA_CHANGED')
        for _, item in items:
            fields = dict(item['fields'])
            if item['artifact']: fields['视频'] = [{'file_token': item['steps']['finish']['receipt']}]
            rows = client.find(base, table, fields['同步标识'])
            if len(rows) != 1 or rows[0]['record_id'] != item['record_id'] or not matches(rows[0], fields):
                raise RuntimeFault('BASE_RESULTS_RECOVERY_REMOTE_MISMATCH')
        plan = {'project': project, 'config_sha256': fingerprint(config), 'journal_sha256': fingerprint(items),
                'verified_rows': len(items), 'enable_automatically': False, 'feishu_writes': 0}
        return {'plan': plan, 'plan_sha256': fingerprint(plan)}

    def recover(self, token, project, expected_plan):
        prepared = self.recovery_plan(token, project)
        if prepared['plan_sha256'] != expected_plan: raise RuntimeFault('BASE_RESULTS_PLAN_CHANGED')
        with self.store.connect() as db:
            actor = self.store.authorize(db, token, 'admin'); config = meta(db, control_key(project))
            prefix = item_prefix(project)
            rows = journal_rows(db, project)
            if (fingerprint(config) != prepared['plan']['config_sha256'] or self.context(db, project) != config['context']
                    or fingerprint([(row['key'], json.loads(row['value'])) for row in rows]) != prepared['plan']['journal_sha256']):
                raise RuntimeFault('BASE_RESULTS_PLAN_CHANGED')
            config.update(recovery_required=False, enabled=False); save(db, control_key(project), config)
            self.store.audit(db, actor, 'base_results_recovery_readback', project)
        return {'status': 'reconciled_still_paused', 'project': project, 'feishu_writes': 0}

    def step(self, key, name, operation, project, context, authorize):
        with self.store.connect() as db:
            self.fence(db, project, context, authorize)
            item = meta(db, key); old = item['steps'].get(name)
            if old:
                if old['status'] == 'done': return old['receipt']
                if old['status'] != 'retry_authorized': raise RuntimeFault('BASE_RESULTS_WRITE_UNKNOWN_READ_STATUS')
                if old['expires_at'] < time.time(): raise RuntimeFault('BASE_RESULTS_RETRY_AUTHORIZATION_EXPIRED')
                old.update(status='in_flight', started_at=time.time())
            else:
                item['steps'][name] = {'status': 'in_flight', 'ticket': str(uuid.uuid4()), 'started_at': time.time()}
            save(db, key, item)
        receipt = operation(item['steps'][name]['ticket'])
        with self.store.connect() as db:
            current = meta(db, key)
            if current['steps'].get(name) != item['steps'][name]: raise RuntimeFault('BASE_RESULTS_JOURNAL_CHANGED')
            current['steps'][name].update(status='done', receipt=receipt); save(db, key, current)
        return receipt

    def sync(self, project, media_root, authorize):
        identifier(project)
        with self.store.connect() as db:
            authorize(db)
            config = meta(db, control_key(project))
            if not config or not config['enabled']: return {'status': 'disabled', 'feishu_writes': 0}
            context = config['context']; self.fence(db, project, context, authorize)
            prefix = item_prefix(project)
            existing = journal_rows(db, project)
            if len(existing) > 10000: raise RuntimeFault('BASE_RESULTS_JOURNAL_CAPACITY')
            pending = [(row['key'], json.loads(row['value'])) for row in existing if not json.loads(row['value'])['complete']]
            if pending:
                key, item = pending[0]
            else:
                rows = db.execute('''SELECT t.* FROM tasks t WHERE project=? AND
                    revision=(SELECT MAX(v.revision) FROM tasks v WHERE v.project=t.project AND v.id=t.id)
                    ORDER BY id LIMIT 1001''', (project,)).fetchall()
                if len(rows) > 1000: raise RuntimeFault('BASE_RESULTS_QUEUE_CAPACITY')
                known = {row['key'] for row in existing}; item = None
                for row in rows:
                    candidate = snapshot(project, row); key = prefix+candidate['fields']['同步标识']
                    if key not in known:
                        item = candidate; save(db, key, item); break
                if item is None: return {'status': 'idle', 'feishu_writes': 0}
        client = self.client(context); base = context['target']['base_token']; table = config['table_id']
        if schema(client, base, table) != config['schema']: raise RuntimeFault('BASE_RESULTS_SCHEMA_CHANGED')
        fields = dict(item['fields'])
        upload = item['steps'].get('finish', {})
        if upload.get('status') == 'done': fields['视频'] = [{'file_token': upload['receipt']}]
        # Reconcile before any new write. Never mutate an existing remote row.
        records = client.find(base, table, fields['同步标识'])
        if records:
            if item['artifact'] and '视频' not in fields: raise RuntimeFault('BASE_RESULTS_EXTERNAL_EDIT_CONFLICT')
            if not matches(records[0], fields): raise RuntimeFault('BASE_RESULTS_EXTERNAL_EDIT_CONFLICT')
            with self.store.connect() as db:
                self.fence(db, project, context, authorize)
                current = meta(db, key); current.update(complete=True, record_id=records[0]['record_id'])
                save(db, key, current); self.store.audit(db, 'automation:'+project, 'base_results_readback', project, item['task'], item['revision'])
            return {'status': 'synced', 'task': item['task'], 'revision': item['revision'], 'feishu_writes': 0}
        if 'record' in item['steps'] and item['steps']['record']['status'] != 'retry_authorized':
            raise RuntimeFault('BASE_RESULTS_RECORD_UNKNOWN_OR_REMOVED')
        if item['artifact'] and '视频' not in fields:
            from .review_service import open_media
            stream, size = open_media(media_root, item['artifact'], item['artifact']['sha256'])
            with stream:
                prepared = item['steps'].get('prepare', {})
                if prepared.get('status') != 'done':
                    hashes = []; digest = hashlib.sha256()
                    while chunk := stream.read(BLOCK_SIZE):
                        digest.update(chunk); hashes.append(hashlib.sha256(chunk).hexdigest())
                    if digest.hexdigest() != item['artifact']['sha256']:
                        raise RuntimeFault('BASE_RESULTS_MEDIA_CHANGED')
                    def prepare_upload(_):
                        receipt = client.upload_prepare(base, item['task']+'-v'+str(item['revision'])+'.mp4', size)
                        return {**receipt, 'chunk_sha256': hashes}
                    self.step(key, 'prepare', prepare_upload, project, context, authorize)
                    return {'status': 'upload_prepared', 'feishu_writes': 1}
                receipt = prepared['receipt']
                if time.time()-prepared['started_at'] >= 23*3600:
                    raise RuntimeFault('BASE_RESULTS_UPLOAD_EXPIRED_RECONCILE')
                for seq in range(receipt['block_num']):
                    step = 'part_'+str(seq)
                    if item['steps'].get(step, {}).get('status') != 'done':
                        stream.seek(seq*BLOCK_SIZE); chunk = stream.read(BLOCK_SIZE)
                        if hashlib.sha256(chunk).hexdigest() != receipt['chunk_sha256'][seq]:
                            raise RuntimeFault('BASE_RESULTS_MEDIA_CHANGED')
                        self.step(key, step, lambda _: client.upload_part(receipt['upload_id'], seq, chunk), project, context, authorize)
                        return {'status': 'upload_part_saved', 'feishu_writes': 1}
                self.step(key, 'finish', lambda _: client.upload_finish(receipt['upload_id'], receipt['block_num']), project, context, authorize)
                return {'status': 'upload_finished', 'feishu_writes': 1}
        self.step(key, 'record', lambda ticket: client.append(base, table, fields, ticket), project, context, authorize)
        # Readback is a separate stage: a POST success alone is not completion.
        return {'status': 'record_written_readback_pending', 'feishu_writes': 1}
