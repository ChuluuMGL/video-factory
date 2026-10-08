"""Fail-closed reducer for Feishu Base review events.

The caller must be the verified Feishu event dispatcher. A Base status or
record-history display name alone is never a review credential.
"""
import json

from .feishu_bridge import FeishuBridge, meta, save, plain_text
from .feishu_client import resource
from .runtime_store import RuntimeFault, fingerprint, identifier

EVENT_TYPE = 'drive.file.bitable_record_changed_v1'
DECISIONS = {
    '脚本通过': ('script', 'accept'),
    '脚本退回': ('script', 'reject'),
    '视频通过': ('video', 'accept'),
    '视频退回': ('video', 'reject'),
}
PENDING = {'script': '脚本待审核', 'video': '视频待审核'}
FINAL_DENIALS = {'FEISHU_TENANT_OR_ROLE_DENIED','FEISHU_REVIEW_TRANSITION_INVALID',
                 'FEISHU_REVIEW_REMOTE_CHANGED','FEISHU_REVIEW_SCRIPT_CHANGED',
                 'FEISHU_REVIEW_VIDEO_CHANGED','FEISHU_REVIEW_FEEDBACK_REQUIRED',
                 'FEISHU_REVIEW_STATE_CONFLICT','FEISHU_REVIEW_SOURCE_NOT_BOUND',
                 'FEISHU_REVIEW_EVENT_CONFLICT'}


def source_key(project, task):
    return 'native:source:' + fingerprint([identifier(project), identifier(task)])


def review_key(project, event_id, record_id):
    return 'native:review:' + fingerprint([identifier(project), event_id, record_id])


def queue_key(project, event_id):
    return 'native:event:' + identifier(project) + ':' + fingerprint(event_id)


def _changed(action, field_id):
    """Return a changed text field, or None if it was not changed by this event."""
    def find(side):
        rows = [item for item in action.get(side, []) if item.get('field_id') == field_id]
        if len(rows) > 1: raise RuntimeFault('FEISHU_EVENT_FIELD_AMBIGUOUS')
        if not rows: return None
        raw = rows[0].get('field_value')
        if not isinstance(raw, str) or len(raw) > 16000: raise RuntimeFault('FEISHU_EVENT_FIELD_INVALID')
        try: return plain_text(json.loads(raw))
        except (ValueError, RuntimeFault): raise RuntimeFault('FEISHU_EVENT_FIELD_INVALID') from None
    before, after = find('before_value'), find('after_value')
    return (before, after) if before != after and after is not None else None


class NativeReview:
    def __init__(self, store, *, client_factory):
        self.store, self.client_factory = store, client_factory
        self.bridge = FeishuBridge(store)

    def enqueue_verified(self, project, envelope):
        """Persist an SDK-verified event before the three-second ACK deadline."""
        identifier(project)
        header = envelope.get('header', {}) if isinstance(envelope, dict) else {}
        event = envelope.get('event', {}) if isinstance(envelope, dict) else {}
        if header.get('event_type') != EVENT_TYPE or envelope.get('schema') != '2.0':
            raise RuntimeFault('FEISHU_EVENT_INVALID')
        event_id = resource(header.get('event_id'))
        operator = event.get('operator_id')
        if not isinstance(operator, dict): raise RuntimeFault('FEISHU_EVENT_OPERATOR_REQUIRED')
        resource(operator.get('open_id'), 'ou_')
        actions = event.get('action_list')
        if not isinstance(actions, list) or not 1 <= len(actions) <= 100:
            raise RuntimeFault('FEISHU_EVENT_ACTIONS_INVALID')
        with self.store.connect() as db:
            binding = self.bridge._binding(db, project)
            config = meta(db, 'native:config:'+project)
            if not config or not config.get('enabled') or header.get('app_id') != config['app_id']:
                raise RuntimeFault('FEISHU_NATIVE_REVIEW_DISABLED')
            if (header.get('tenant_key') != binding['tenant_key'] or event.get('file_token') != binding['base_token']
                    or event.get('table_id') != binding['table_id']):
                raise RuntimeFault('FEISHU_EVENT_SCOPE_DENIED')
            # Field content beyond the status transition is not needed here.
            reduced = {'schema':'2.0', 'header':{
                key:header[key] for key in ('event_id','event_type','app_id','tenant_key') if key in header}}
            reduced['event'] = {k: event[k] for k in ('file_type','file_token','table_id','operator_id') if k in event}
            reduced['event']['operator_id'] = {'open_id':event['operator_id']['open_id']}
            reduced['event']['action_list'] = []
            for action in actions:
                if not isinstance(action, dict): raise RuntimeFault('FEISHU_EVENT_ACTIONS_INVALID')
                resource(action.get('record_id'), 'rec')
                reduced['event']['action_list'].append({
                    'record_id': action['record_id'], 'action': action.get('action'),
                    **{side: [item for item in action.get(side, []) if item.get('field_id') == config['fields']['status']]
                       for side in ('before_value','after_value')}})
            key = queue_key(project, event_id)
            old = meta(db, key)
            if old:
                if old['event'] != reduced: raise RuntimeFault('FEISHU_EVENT_ID_CONFLICT')
                return {'status': old['status'], 'replayed': True}
            save(db, key, {'project': project, 'event': reduced, 'status': 'pending'})
        return {'status': 'queued', 'provider_requests': 0}

    def process_one(self, project):
        prefix = 'native:event:' + identifier(project) + ':'
        pattern = prefix.replace('\\', '\\\\').replace('_', '\\_') + '%'
        with self.store.connect() as db:
            rows = db.execute("SELECT key,value FROM meta WHERE key LIKE ? ESCAPE '\\' ORDER BY key LIMIT 1001",
                              (pattern,)).fetchall()
            if len(rows)>1000: raise RuntimeFault('FEISHU_EVENT_QUEUE_CAPACITY')
            pending = [(row['key'],json.loads(row['value'])) for row in rows
                       if json.loads(row['value']).get('project')==project and json.loads(row['value'])['status']=='pending']
        for key, value in pending:
            try: result = self.consume_verified(project, value['event'])
            except RuntimeFault as error:
                if str(error) in ('FEISHU_EVENT_SCOPE_DENIED','FEISHU_NATIVE_REVIEW_DISABLED'): continue
                if str(error) in FINAL_DENIALS:
                    with self.store.connect() as db:
                        current = meta(db,key)
                        if current != value: raise RuntimeFault('FEISHU_EVENT_QUEUE_CHANGED')
                        current.update(status='denied', reason=str(error))
                        save(db,key,current)
                        self.store.audit(db,'feishu-event','native_review_denied',project)
                    return {'status':'denied','reason':str(error),'provider_requests':0}
                raise
            with self.store.connect() as db:
                current = meta(db,key)
                if current != value: raise RuntimeFault('FEISHU_EVENT_QUEUE_CHANGED')
                current['status']='done'; current['result']=result
                save(db,key,current)
            return {'status':'processed','result':result}
        return {'status':'idle','provider_requests':0}

    def consume_verified(self, project, envelope):
        """Consume only from the SDK's authenticated event callback.

        Re-read the current row so stale/out-of-order events cannot approve an
        old script or video. The Feishu operator open_id is mandatory.
        """
        identifier(project)
        if not isinstance(envelope, dict) or envelope.get('schema') != '2.0':
            raise RuntimeFault('FEISHU_EVENT_INVALID')
        header, event = envelope.get('header'), envelope.get('event')
        if not isinstance(header, dict) or not isinstance(event, dict) or header.get('event_type') != EVENT_TYPE:
            raise RuntimeFault('FEISHU_EVENT_INVALID')
        event_id = resource(header.get('event_id'))
        operator_id = event.get('operator_id')
        if not isinstance(operator_id, dict): raise RuntimeFault('FEISHU_EVENT_OPERATOR_REQUIRED')
        operator = resource(operator_id.get('open_id'), 'ou_')
        with self.store.connect() as db:
            binding = self.bridge._binding(db, project)
            config = meta(db, 'native:config:'+project)
            if not config or not config.get('enabled'):
                raise RuntimeFault('FEISHU_NATIVE_REVIEW_DISABLED')
            if (header.get('app_id') != config['app_id'] or header.get('tenant_key') != binding['tenant_key']
                    or event.get('file_type') != 'bitable' or event.get('file_token') != binding['base_token']
                    or event.get('table_id') != binding['table_id']):
                raise RuntimeFault('FEISHU_EVENT_SCOPE_DENIED')
            mapping = config['fields']
        actions = event.get('action_list')
        if not isinstance(actions, list) or not 1 <= len(actions) <= 100:
            raise RuntimeFault('FEISHU_EVENT_ACTIONS_INVALID')
        client = self.client_factory(config['app_id'])
        results = []
        for action in actions:
            if not isinstance(action, dict) or action.get('action') != 'record_edited': continue
            record_id = resource(action.get('record_id'), 'rec')
            change = _changed(action, mapping['status'])
            if not change or change[1] not in DECISIONS: continue
            stage, decision = DECISIONS[change[1]]
            if change[0] != PENDING[stage]:
                raise RuntimeFault('FEISHU_REVIEW_TRANSITION_INVALID')
            role = stage+'_reviewers'
            actor = self.bridge._member(binding, {'tenant_key': binding['tenant_key'], 'open_id': operator}, role)
            with self.store.connect() as db:
                source = meta(db, 'native:record:'+project+':'+record_id)
                if not source or source['record_id'] != record_id:
                    raise RuntimeFault('FEISHU_REVIEW_SOURCE_NOT_BOUND')
                task, revision = source['task'], source['revision']
                row = self.store._current(db, project, task, revision)
                if row['state'] != 'awaiting_'+stage+'_review':
                    raise RuntimeFault('FEISHU_REVIEW_STATE_CONFLICT')
                sync = meta(db, 'native:sync:'+fingerprint([project, task, revision]))
                if not sync or (not sync.get('complete') and sync.get('steps',{}).get('record',{}).get('status') != 'in_flight'):
                    raise RuntimeFault('FEISHU_REVIEW_DRAFT_NOT_SYNCED')
                current_digest = row['input_digest']
                artifact_digest = fingerprint(json.loads(row['artifact']) if row['artifact'] else None)
                payload = json.loads(row['input'])
            # The event may have been delayed or reordered. The live row must
            # still show this exact version and decision before committing.
            remote = client.record(binding['base_token'], binding['table_id'], record_id)['fields']
            names = config['names']
            if (plain_text(remote.get(names['status'])) != change[1]
                    or plain_text(remote.get(names['review_revision'])) != str(revision)
                    or plain_text(remote.get(names['task'])) != task):
                raise RuntimeFault('FEISHU_REVIEW_REMOTE_CHANGED')
            if stage == 'script' and plain_text(remote.get(names['script'])) != payload['script']:
                raise RuntimeFault('FEISHU_REVIEW_SCRIPT_CHANGED')
            if stage == 'video':
                artifact = json.loads(row['artifact'])
                if plain_text(remote.get(names['video_digest'])) != artifact['sha256']:
                    raise RuntimeFault('FEISHU_REVIEW_VIDEO_CHANGED')
                token = sync['steps'].get('finish',{}).get('receipt')
                attachment = remote.get(names['video'])
                if not token or not isinstance(attachment,list) or [x.get('file_token') for x in attachment] != [token]:
                    raise RuntimeFault('FEISHU_REVIEW_VIDEO_CHANGED')
            feedback = remote.get(names['feedback']) or ''
            if feedback: feedback = plain_text(feedback)
            if decision == 'reject' and not feedback.strip():
                raise RuntimeFault('FEISHU_REVIEW_FEEDBACK_REQUIRED')
            key = review_key(project, event_id, record_id)
            request = fingerprint([actor, task, revision, stage, decision, feedback, current_digest, artifact_digest])
            with self.store.connect() as db:
                if self.bridge._binding(db, project) != binding or meta(db, 'native:config:'+project) != config:
                    raise RuntimeFault('FEISHU_REVIEW_BINDING_CHANGED')
                old = meta(db, key)
                if old:
                    if old['request'] != request: raise RuntimeFault('FEISHU_REVIEW_EVENT_CONFLICT')
                    results.append({**old['receipt'], 'replayed': True}); continue
                row = self.store._current(db, project, task, revision)
                if (row['state'] != 'awaiting_'+stage+'_review' or row['input_digest'] != current_digest
                        or fingerprint(json.loads(row['artifact']) if row['artifact'] else None) != artifact_digest):
                    raise RuntimeFault('FEISHU_REVIEW_STATE_CONFLICT')
                if not sync['complete']:
                    latest_sync = meta(db, 'native:sync:'+fingerprint([project, task, revision]))
                    if latest_sync != sync: raise RuntimeFault('FEISHU_REVIEW_SYNC_CHANGED')
                    latest_sync['complete'] = True
                    save(db, 'native:sync:'+fingerprint([project, task, revision]), latest_sync)
                receipt = self.store._review(db, actor, 'native_'+fingerprint([project, event_id, record_id]),
                                             project, task, revision, stage, decision, feedback)
                save(db, key, {'request': request, 'receipt': receipt})
                results.append(receipt)
        return {'project': project, 'reviewed': results, 'provider_requests': 0}
