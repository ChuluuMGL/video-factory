"""Verified original-Base video plans; a script approval is never video consent."""
import hashlib
import json
import time

from .feishu_bridge import FeishuBridge, meta, plain_text, save, select_text
from .feishu_client import resource
from .feishu_native_review import _changed, source_key
from .feishu_production_entry import _single_select, config_key, plan_key, policy, PLAN_LIFETIME
from .runtime_store import RuntimeFault, RuntimeStore, fingerprint, identifier
from .worker import Worker, job_key

VIDEO_REQUEST = '申请视频计划'
VIDEO_CONFIRM = '确认此计划的一次视频生成'


def video_plan_key(project, record):
    return 'production:video-plan:' + fingerprint([identifier(project), resource(record, 'rec')])


class VerifiedStore(RuntimeStore):
    """Allow Worker.prepare only for the actor of this verified, scoped event."""
    def __init__(self, store, project, binding, operator, config, digest, role):
        self.store, self.root, self.project = store, store.root, project
        self.binding, self.operator, self.config = binding, operator, config
        self.digest, self.role = digest, role

    def connect(self):
        return self.store.connect()

    def authorize(self, db, token, role=None, project=None):
        if project is not None and project != self.project:
            raise RuntimeFault('PRODUCTION_VIDEO_PROJECT_DENIED')
        binding = FeishuBridge(self.store)._binding(db, self.project)
        current = meta(db, config_key(self.project))
        row = db.execute('SELECT digest FROM projects WHERE id=?', (self.project,)).fetchone()
        if binding != self.binding or current != self.config or not row or row[0] != self.digest:
            raise RuntimeFault('PRODUCTION_VIDEO_CONFIGURATION_CHANGED')
        if self.role == 'request':
            return FeishuBridge._member(binding, {'tenant_key': binding['tenant_key'],
                                                   'open_id': self.operator}, 'submitters')
        if (self.role != 'cost' or not current.get('cost_enabled')
                or self.operator not in current.get('cost_approvers', [])):
            raise RuntimeFault('PRODUCTION_COST_APPROVER_DENIED')
        return 'feishu:' + binding['tenant_key'] + ':' + self.operator


class ProductionVideo:
    def __init__(self, store, client_factory, clock=time.time):
        self.store, self.client_factory, self.clock = store, client_factory, clock

    def _source(self, project, record, remote, native, profile):
        names = native['names']
        task = identifier(plain_text(remote.get(names['task'])))
        sku = plain_text(remote.get(names['sku_id']))
        revision_text = plain_text(remote.get(names['review_revision']))
        try:
            revision = int(revision_text)
        except ValueError:
            raise RuntimeFault('PRODUCTION_VIDEO_REVISION_INVALID') from None
        if revision < 1 or str(revision) != revision_text:
            raise RuntimeFault('PRODUCTION_VIDEO_REVISION_INVALID')
        source_revision = plain_text(remote.get(names['source_revision']))
        script = plain_text(remote.get(names['script']))
        if (select_text(remote.get(names['status'])) != '脚本通过'
                or plain_text(remote.get(names['script_digest'])) != hashlib.sha256(script.encode()).hexdigest()):
            raise RuntimeFault('PRODUCTION_VIDEO_SCRIPT_NOT_APPROVED')
        with self.store.connect() as db:
            source = meta(db, source_key(project, task))
            record_source = meta(db, 'native:record:' + project + ':' + record)
            row = self.store._current(db, project, task, revision)
            latest = db.execute('SELECT MAX(revision) FROM tasks WHERE project=? AND id=?',
                                (project, task)).fetchone()[0]
        data = json.loads(row['input'])
        if (source != {'record_id': record} or not record_source
                or record_source.get('revision') != revision or record_source.get('task') != task
                or latest != revision or row['state'] != 'ready'
                or data['sku_id'] != sku or data['source_revision'] != source_revision
                or data['script'] != script or sku not in profile['assets']):
            raise RuntimeFault('PRODUCTION_VIDEO_SOURCE_CHANGED')
        return task, sku, revision, source_revision, script

    def _worker_plan(self, project, binding, operator, config, digest, role,
                     task, revision, sku, profile):
        values = {'project': project, 'task': task, 'revision': revision,
                  **profile['assets'][sku],
                  **{key: profile[key] for key in ('credential_ref', 'billing_owner', 'region')}}
        scoped = VerifiedStore(self.store, project, binding, operator, config, digest, role)
        return Worker(scoped).prepare('verified-base-event', **values)

    def prepare_verified(self, project, event_id, operator, record, action, config):
        binding, native, current, setup, _, digest, deployment = policy(self.store, project)
        if current != config:
            raise RuntimeFault('PRODUCTION_VIDEO_CONFIGURATION_CHANGED')
        FeishuBridge._member(binding, {'tenant_key': binding['tenant_key'],
                                       'open_id': operator}, 'submitters')
        base, table, names = binding['base_token'], binding['table_id'], current['names']
        client = self.client_factory(current['app_id'])
        options = _single_select(client, base, table, current['fields']['operation'],
                                 names['operation'], VIDEO_REQUEST)
        change = _changed(action, current['fields']['operation'], options)
        if not change or change[1] != VIDEO_REQUEST or change[0] == VIDEO_REQUEST:
            return None
        remote = client.record(base, table, record)['fields']
        if select_text(remote.get(names['operation'])) != VIDEO_REQUEST:
            raise RuntimeFault('PRODUCTION_VIDEO_REMOTE_CHANGED')
        cost = remote.get(names['cost'])
        cost = select_text(cost) if cost not in (None, '', []) else '未确认'
        if cost not in ('未确认', '确认此计划的一次生成'):
            raise RuntimeFault('PRODUCTION_VIDEO_COST_PREMATURE')
        with self.store.connect() as db:
            profile = meta(db, 'video:profile:' + project)
            script_plan = meta(db, plan_key(project, record)) if cost != '未确认' else None
        if not profile:
            raise RuntimeFault('PRODUCTION_VIDEO_PROFILE_REQUIRED')
        task, sku, revision, source_revision, script = self._source(
            project, record, remote, native, profile)
        if cost != '未确认' and (not script_plan or script_plan.get('state') != 'confirmed'
                                  or script_plan['snapshot']['task'] != task
                                  or script_plan['snapshot']['expected_revision'] + 1 != revision):
            raise RuntimeFault('PRODUCTION_VIDEO_COST_PREMATURE')
        prepared = self._worker_plan(project, binding, operator, config, digest,
                                     'request', task, revision, sku, profile)
        now = self.clock()
        snapshot = {'project': project, 'record_id': record, 'task': task, 'sku_id': sku,
                    'revision': revision, 'source_revision': source_revision,
                    'script_sha256': hashlib.sha256(script.encode()).hexdigest(),
                    'worker_plan_sha256': prepared['request_plan_sha256'],
                    'binding_sha256': fingerprint(binding), 'native_sha256': fingerprint(native),
                    'policy_sha256': fingerprint(current), 'setup_sha256': fingerprint(setup),
                    'profile_sha256': fingerprint(profile), 'project_digest': digest,
                    'deployment': deployment, 'requester': operator, 'max_submissions': 1}
        key = video_plan_key(project, record)
        with self.store.connect() as db:
            previous = meta(db, key)
        expiry = previous['expires_at'] if previous and previous.get('snapshot') == snapshot else now + PLAN_LIFETIME
        summary = ('视频计划 ' + fingerprint(snapshot)[:16] + '｜SKU ' + sku + '｜版本 ' + str(revision)
                   + '｜脚本摘要 ' + snapshot['script_sha256'][:12] + '｜模型 MiniMax-H3'
                   + '｜区域 ' + profile['region']
                   + '｜时长 ' + str(profile['assets'][sku]['specification']['duration']) + ' 秒'
                   + '｜费用账户 ' + profile['billing_owner']
                   + '｜有效至 ' + time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(expiry))
                   + ('｜最多 1 次提交；等待指定负责人确认' if current['cost_enabled']
                      else '｜最多 1 次提交；费用确认尚未启用，不会生成'))
        with self.store.connect() as db:
            if FeishuBridge(self.store)._binding(db, project) != binding or meta(db, config_key(project)) != config:
                raise RuntimeFault('PRODUCTION_VIDEO_CONFIGURATION_CHANGED')
            old = meta(db, key)
            if old:
                if old['snapshot'] != snapshot or old['summary'] != summary:
                    prior = old['snapshot']
                    if (old['state'] != 'confirmed' or prior['task'] != task
                            or revision <= prior['revision']
                            or source_revision == prior['source_revision']):
                        raise RuntimeFault('PRODUCTION_VIDEO_PLAN_REQUIRES_RECONCILIATION')
                    save(db, 'production:video-plan-history:' + fingerprint([
                        project, record, prior, old['approval_event']]), old)
                    old = None
                if old and old['state'] == 'ready':
                    if old['expires_at'] <= now or remote.get(names['plan']) != summary:
                        raise RuntimeFault('PRODUCTION_VIDEO_PLAN_EXPIRED_OR_CHANGED')
                    return {'status': 'video_plan_ready', 'replayed': True, 'provider_requests': 0}
                if old and old['state'] != 'write_unknown':
                    raise RuntimeFault('PRODUCTION_VIDEO_PLAN_STATE_INVALID')
            if not old:
                save(db, key, {'snapshot': snapshot, 'summary': summary, 'state': 'write_unknown',
                               'event_id': event_id, 'expires_at': expiry})
        if old:
            remote = client.record(base, table, record)['fields']
            if remote.get(names['plan']) != summary:
                raise RuntimeFault('PRODUCTION_VIDEO_PLAN_WRITE_UNKNOWN')
        else:
            try:
                client.update_record(base, table, record, {names['plan']: summary,
                                                           names['cost']: '未确认',
                                                           names['hint']: ('视频计划已准备；等待负责人逐条确认'
                                                                           if current['cost_enabled']
                                                                           else '视频计划已准备；费用确认尚未启用')})
            except Exception:
                raise RuntimeFault('PRODUCTION_VIDEO_PLAN_WRITE_UNKNOWN') from None
            remote = client.record(base, table, record)['fields']
        if (remote.get(names['plan']) != summary or select_text(remote.get(names['cost'])) != '未确认'
                or select_text(remote.get(names['operation'])) != VIDEO_REQUEST):
            raise RuntimeFault('PRODUCTION_VIDEO_PLAN_WRITE_UNKNOWN')
        self._source(project, record, remote, native, profile)
        with self.store.connect() as db:
            current_plan = meta(db, key)
            if not current_plan or current_plan['snapshot'] != snapshot or current_plan['summary'] != summary:
                raise RuntimeFault('PRODUCTION_VIDEO_PLAN_CHANGED')
            current_plan['state'] = 'ready'
            save(db, key, current_plan)
            self.store.audit(db, 'feishu:' + binding['tenant_key'] + ':' + operator,
                             'production_video_plan_prepared', project, task, revision)
        return {'status': 'video_plan_ready', 'plan_sha256': fingerprint(snapshot),
                'expires_at': expiry, 'provider_requests': 0}

    def confirm_verified(self, project, event_id, operator, record, action, config):
        binding, native, current, setup, _, digest, deployment = policy(self.store, project)
        if current != config or not current['cost_enabled']:
            raise RuntimeFault('PRODUCTION_COST_APPROVAL_DISABLED')
        if operator not in current['cost_approvers']:
            raise RuntimeFault('PRODUCTION_COST_APPROVER_DENIED')
        base, table, names = binding['base_token'], binding['table_id'], current['names']
        client = self.client_factory(current['app_id'])
        options = _single_select(client, base, table, current['fields']['cost'],
                                 names['cost'], VIDEO_CONFIRM)
        change = _changed(action, current['fields']['cost'], options)
        if not change or change[1] != VIDEO_CONFIRM:
            return None
        if change[0] != '未确认':
            raise RuntimeFault('PRODUCTION_VIDEO_COST_TRANSITION_INVALID')
        key = video_plan_key(project, record)
        with self.store.connect() as db:
            saved = meta(db, key)
            profile = meta(db, 'video:profile:' + project)
        if not saved or saved['state'] not in ('ready', 'confirmed') or not profile:
            raise RuntimeFault('PRODUCTION_VIDEO_PLAN_NOT_READY')
        snapshot = saved['snapshot']
        if (saved['expires_at'] <= self.clock()
                or snapshot['binding_sha256'] != fingerprint(binding)
                or snapshot['native_sha256'] != fingerprint(native)
                or snapshot['policy_sha256'] != fingerprint(current)
                or snapshot['setup_sha256'] != fingerprint(setup)
                or snapshot['profile_sha256'] != fingerprint(profile)
                or snapshot['project_digest'] != digest or snapshot['deployment'] != deployment):
            raise RuntimeFault('PRODUCTION_VIDEO_PLAN_CHANGED')
        if saved['state'] == 'confirmed':
            if saved.get('approver') != operator or saved.get('approval_event') != event_id:
                raise RuntimeFault('PRODUCTION_VIDEO_COST_ALREADY_CONFIRMED')
            return {'status': 'video_approved', 'replayed': True, 'provider_requests': 0}
        remote = client.record(base, table, record)['fields']
        if (select_text(remote.get(names['cost'])) != VIDEO_CONFIRM
                or select_text(remote.get(names['operation'])) != VIDEO_REQUEST
                or remote.get(names['plan']) != saved['summary']):
            raise RuntimeFault('PRODUCTION_VIDEO_REMOTE_CHANGED')
        task, sku, revision, source_revision, script = self._source(
            project, record, remote, native, profile)
        if (task != snapshot['task'] or sku != snapshot['sku_id'] or revision != snapshot['revision']
                or source_revision != snapshot['source_revision']
                or hashlib.sha256(script.encode()).hexdigest() != snapshot['script_sha256']):
            raise RuntimeFault('PRODUCTION_VIDEO_SOURCE_CHANGED')
        prepared = self._worker_plan(project, binding, operator, config, digest,
                                     'cost', task, revision, sku, profile)
        if prepared['request_plan_sha256'] != snapshot['worker_plan_sha256']:
            raise RuntimeFault('PRODUCTION_VIDEO_ASSETS_CHANGED')
        actor = 'feishu:' + binding['tenant_key'] + ':' + operator
        gate = {'video_plan_key': key, 'snapshot_sha256': fingerprint(snapshot),
                'policy_sha256': fingerprint(current), 'approver': actor,
                'expires_at': saved['expires_at'], 'record_id': record,
                'summary': saved['summary']}
        with self.store.connect() as db:
            if (meta(db, key) != saved or meta(db, config_key(project)) != config
                    or meta(db, 'video:profile:' + project) != profile
                    or meta(db, 'native:config:' + project) != native
                    or FeishuBridge(self.store)._binding(db, project) != binding):
                raise RuntimeFault('PRODUCTION_VIDEO_PLAN_CHANGED')
            row = self.store._current(db, project, task, revision)
            if row['state'] != 'ready' or meta(db, job_key(project, task, revision)):
                raise RuntimeFault('PRODUCTION_VIDEO_JOB_CONFLICT')
            current_plan = meta(db, key)
            current_plan.update(state='confirmed', approver=operator, approval_event=event_id)
            save(db, key, current_plan)
            save(db, job_key(project, task, revision),
                 {'plan': prepared['plan'], 'plan_sha256': prepared['request_plan_sha256'],
                  'expires_at': min(saved['expires_at'], self.clock() + 3600),
                  'provider_receipt': None, 'production_gate': gate})
            self.store.audit(db, actor, 'production_video_cost_confirmed', project, task, revision)
        return {'status': 'video_approved', 'task': task, 'revision': revision,
                'provider_requests': 0, 'max_submissions': 1}
