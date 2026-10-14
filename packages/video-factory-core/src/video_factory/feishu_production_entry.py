"""Opt-in script plans and cost approvals from verified original-Base events.

No production configuration is installed by default. A distinct, verified
cost event may queue one script request after checking the live row and plan.
"""
import hashlib
import json
import time

from .feishu_bridge import FeishuBridge, meta, plain_text, save, select_text
from .feishu_client import resource
from .runtime_store import RuntimeFault, fingerprint, identifier

REQUEST = '申请脚本计划'
COST_CONFIRM = '确认此计划的一次生成'
PLAN_LIFETIME = 20 * 60


def config_key(project):
    return 'production:config:' + identifier(project)


def plan_key(project, record):
    return 'production:plan:' + fingerprint([identifier(project), resource(record, 'rec')])


def _single_select(client, base, table, field_id, field_name, option_name):
    matches = [field for field in client.fields(base, table)
               if field.get('field_id') == field_id]
    if len(matches) != 1 or matches[0].get('field_name') != field_name or matches[0].get('type') != 3:
        raise RuntimeFault('PRODUCTION_FIELD_SCHEMA_CHANGED')
    properties = matches[0].get('property')
    options = properties.get('options') if isinstance(properties, dict) else None
    if not isinstance(options, list):
        raise RuntimeFault('PRODUCTION_FIELD_SCHEMA_CHANGED')
    resolved = {item['id']: item['name'] for item in options if isinstance(item, dict)
                and isinstance(item.get('id'), str) and isinstance(item.get('name'), str)}
    if len(resolved) != len(options) or list(resolved.values()).count(option_name) != 1:
        raise RuntimeFault('PRODUCTION_FIELD_SCHEMA_CHANGED')
    return resolved


def policy(store, project):
    with store.connect() as db:
        binding = FeishuBridge(store)._binding(db, project)
        native = meta(db, 'native:config:' + project)
        value = meta(db, config_key(project))
        setup = meta(db, 'setup:project:' + project)
        profile = meta(db, 'script:profile:' + project)
        project_row = db.execute('SELECT digest FROM projects WHERE id=?', (project,)).fetchone()
        deployment_row = db.execute("SELECT value FROM meta WHERE key='deployment'").fetchone()
        deployment = deployment_row[0] if deployment_row else None
    if not native or not native.get('enabled') or not value or not value.get('enabled'):
        raise RuntimeFault('PRODUCTION_PLAN_DISABLED')
    required = {'enabled', 'cost_enabled', 'cost_approvers', 'app_id', 'tenant_key',
                'base_token', 'table_id', 'fields', 'names'}
    if set(value) != required or set(value['fields']) != {'operation', 'cost'} or set(value['names']) != {
            'operation', 'brief', 'plan', 'hint', 'cost'}:
        raise RuntimeFault('PRODUCTION_CONFIGURATION_INVALID')
    if (type(value['cost_enabled']) is not bool or not isinstance(value['cost_approvers'], list)
            or not value['cost_approvers'] or len(value['cost_approvers']) > 100
            or len(set(value['cost_approvers'])) != len(value['cost_approvers'])):
        raise RuntimeFault('PRODUCTION_CONFIGURATION_INVALID')
    for approver in value['cost_approvers']: resource(approver, 'ou_')
    if (value['app_id'] != native['app_id'] or value['tenant_key'] != binding['tenant_key']
            or value['base_token'] != binding['base_token'] or value['table_id'] != binding['table_id']
            or not setup or not profile or not project_row or not deployment):
        raise RuntimeFault('PRODUCTION_CONFIGURATION_CHANGED')
    for field_id in value['fields'].values(): resource(field_id, 'fld')
    for name in value['names'].values():
        if not isinstance(name, str) or not 1 <= len(name) <= 100:
            raise RuntimeFault('PRODUCTION_CONFIGURATION_INVALID')
    if len(set(value['names'].values())) != len(value['names']):
        raise RuntimeFault('PRODUCTION_CONFIGURATION_INVALID')
    return binding, native, value, setup, profile, project_row[0], deployment


class ProductionEntry:
    def __init__(self, store, client_factory, clock=time.time):
        self.store, self.client_factory, self.clock = store, client_factory, clock

    def prepare_verified(self, project, event_id, operator, record_id, action, config):
        """Prepare one live row; only the SDK event reducer may call this."""
        identifier(project)
        resource(record_id, 'rec')
        resource(operator, 'ou_')
        resource(event_id)
        binding, native, current_config, setup, profile, project_digest, deployment = policy(self.store, project)
        if current_config != config:
            raise RuntimeFault('PRODUCTION_CONFIGURATION_CHANGED')
        actor = FeishuBridge._member(binding, {'tenant_key': binding['tenant_key'],
                                               'open_id': operator}, 'submitters')
        base, table = binding['base_token'], binding['table_id']
        client = self.client_factory(current_config['app_id'])
        names = current_config['names']
        options = _single_select(client, base, table, current_config['fields']['operation'],
                                 names['operation'], REQUEST)
        _single_select(client, base, table, current_config['fields']['cost'],
                       names['cost'], '未确认')
        from .feishu_native_review import _changed
        changed = _changed(action, current_config['fields']['operation'], options)
        if not changed or changed[1] != REQUEST or changed[0] == REQUEST:
            return None
        remote = client.record(base, table, record_id)['fields']
        if select_text(remote.get(names['operation'])) != REQUEST:
            raise RuntimeFault('PRODUCTION_REQUEST_REMOTE_CHANGED')
        cost_value = remote.get(names['cost'])
        if cost_value not in (None, '', []) and select_text(cost_value) != '未确认':
            raise RuntimeFault('PRODUCTION_COST_PREMATURE')
        task = identifier(plain_text(remote.get(native['names']['task'])))
        sku = plain_text(remote.get(native['names']['sku_id']))
        source_revision = plain_text(remote.get(native['names']['source_revision']))
        brief = plain_text(remote.get(names['brief']))
        def source_still_matches(fields):
            try:
                return (select_text(fields.get(names['operation'])) == REQUEST
                        and plain_text(fields.get(native['names']['task'])) == task
                        and plain_text(fields.get(native['names']['sku_id'])) == sku
                        and plain_text(fields.get(native['names']['source_revision'])) == source_revision
                        and plain_text(fields.get(names['brief'])) == brief)
            except RuntimeFault:
                return False
        if len(brief) > 6000: raise RuntimeFault('PRODUCTION_BRIEF_TOO_LONG')
        if (remote.get(names['plan']) or remote.get(names['hint'])):
            # A previous plan or error belongs to another input snapshot and
            # must be cleared by an explicit reconciliation, never overwritten.
            existing = None
            with self.store.connect() as db: existing = meta(db, plan_key(project, record_id))
            if not existing: raise RuntimeFault('PRODUCTION_EXISTING_PLAN_REQUIRES_RECONCILIATION')
        products = setup['configuration']['project']['products']
        product = next((item for item in products if item['sku_id'] == sku), None)
        if not product: raise RuntimeFault('PRODUCTION_SKU_NOT_IN_PROJECT')
        with self.store.connect() as db:
            latest = db.execute('SELECT revision,state FROM tasks WHERE project=? AND id=? '
                                'ORDER BY revision DESC LIMIT 1', (project, task)).fetchone()
            expected_revision = latest['revision'] if latest else 0
            if latest and latest['state'] not in ('rejected', 'accepted', 'failed'):
                raise RuntimeFault('PRODUCTION_TASK_NOT_READY')
            feedback = ''
            if latest:
                from .script_jobs import key as script_key, previous_feedback
                if not meta(db, script_key(project, task, expected_revision)):
                    raise RuntimeFault('PRODUCTION_TASK_SOURCE_CONFLICT')
                feedback = previous_feedback(db, project, task, expected_revision)
                if latest['state'] == 'rejected' and not feedback.strip():
                    raise RuntimeFault('PRODUCTION_REVIEW_FEEDBACK_MISSING')
            secret = db.execute('SELECT revision FROM vault WHERE alias=?',
                                (profile['credential_ref'][7:],)).fetchone()
            if not secret or secret[0] != profile['credential_revision']:
                raise RuntimeFault('PRODUCTION_CREDENTIAL_CHANGED')
        now = self.clock()
        snapshot = {'project': project, 'record_id': record_id, 'task': task, 'sku_id': sku,
                    'source_revision': source_revision, 'brief_sha256': hashlib.sha256(brief.encode()).hexdigest(),
                    'feedback_sha256': hashlib.sha256(feedback.encode()).hexdigest(),
                    'expected_revision': expected_revision, 'model': profile['model'],
                    'billing_owner': profile['billing_owner'], 'credential_revision': profile['credential_revision'],
                    'binding_sha256': fingerprint(binding), 'native_sha256': fingerprint(native),
                    'policy_sha256': fingerprint(current_config), 'project_digest': project_digest,
                    'setup_sha256': fingerprint(setup), 'profile_sha256': fingerprint(profile),
                    'deployment': deployment, 'max_submissions': 1, 'requester': actor}
        digest = fingerprint(snapshot)
        summary = ('脚本计划 ' + digest[:16] + '｜SKU ' + sku + '｜版本 ' + str(expected_revision + 1)
                   + '｜模型 ' + profile['model'] + '｜费用账户 ' + profile['billing_owner']
                   + ('｜最多 1 次提交；等待指定负责人确认' if current_config['cost_enabled']
                      else '｜最多 1 次提交；费用确认尚未启用，不会生成'))
        key = plan_key(project, record_id)
        with self.store.connect() as db:
            if FeishuBridge(self.store)._binding(db, project) != binding or meta(db, config_key(project)) != config:
                raise RuntimeFault('PRODUCTION_CONFIGURATION_CHANGED')
            old = meta(db, key)
            if old:
                if old['snapshot'] != snapshot:
                    previous = old['snapshot']
                    if (old['state'] != 'confirmed' or expected_revision != previous['expected_revision'] + 1
                            or source_revision == previous['source_revision']
                            or remote.get(names['plan']) != old['summary']):
                        raise RuntimeFault('PRODUCTION_PLAN_REQUIRES_RECONCILIATION')
                    # A closed revision can be followed by an explicit new
                    # request on the same row. Preserve the prior approval
                    # receipt before replacing the active plan pointer.
                    save(db, 'production:plan-history:' + fingerprint([project, record_id,
                                                                         previous, old['approval_event']]), old)
                    old = None
                if old and old['state'] == 'ready':
                    if old['expires_at'] <= now:
                        raise RuntimeFault('PRODUCTION_PLAN_EXPIRED')
                    if remote.get(names['plan']) != old['summary'] or not source_still_matches(remote):
                        raise RuntimeFault('PRODUCTION_PLAN_REMOTE_CHANGED')
                    if select_text(remote.get(names['cost'])) != '未确认':
                        raise RuntimeFault('PRODUCTION_COST_REMOTE_CHANGED')
                    return {'status': 'plan_ready', 'plan_sha256': digest, 'replayed': True, 'provider_requests': 0}
                elif old and old['state'] != 'write_unknown':
                    raise RuntimeFault('PRODUCTION_PLAN_STATE_INVALID')
            if not old:
                save(db, key, {'snapshot': snapshot, 'summary': summary, 'state': 'write_unknown',
                               'event_id': event_id, 'expires_at': now + PLAN_LIFETIME})
        if old:
            remote = client.record(base, table, record_id)['fields']
            if remote.get(names['plan']) != summary:
                raise RuntimeFault('PRODUCTION_PLAN_WRITE_UNKNOWN')
        else:
            try:
                client.update_record(base, table, record_id, {names['plan']: summary,
                                                               names['cost']: '未确认',
                                                               names['hint']: ('计划已准备；等待负责人逐条确认'
                                                                               if current_config['cost_enabled']
                                                                               else '计划已准备；费用确认尚未启用')})
            except Exception:
                raise RuntimeFault('PRODUCTION_PLAN_WRITE_UNKNOWN') from None
            remote = client.record(base, table, record_id)['fields']
            if remote.get(names['plan']) != summary:
                raise RuntimeFault('PRODUCTION_PLAN_WRITE_UNKNOWN')
        if not source_still_matches(remote):
            raise RuntimeFault('PRODUCTION_PLAN_REMOTE_CHANGED')
        if select_text(remote.get(names['cost'])) != '未确认':
            raise RuntimeFault('PRODUCTION_COST_REMOTE_CHANGED')
        with self.store.connect() as db:
            current = meta(db, key)
            if not current or current['snapshot'] != snapshot or current['summary'] != summary:
                raise RuntimeFault('PRODUCTION_PLAN_CHANGED')
            current['state'] = 'ready'
            save(db, key, current)
            self.store.audit(db, actor, 'production_plan_prepared', project, task, expected_revision + 1)
        return {'status': 'plan_ready', 'plan_sha256': digest, 'expires_at': now + PLAN_LIFETIME,
                'provider_requests': 0}

    def confirm_verified(self, project, event_id, operator, record_id, action, config):
        """Queue one script job only after a distinct verified cost event."""
        identifier(project)
        resource(event_id)
        resource(operator, 'ou_')
        resource(record_id, 'rec')
        binding, native, current_config, setup, profile, project_digest, deployment = policy(self.store, project)
        if current_config != config or not current_config['cost_enabled']:
            raise RuntimeFault('PRODUCTION_COST_APPROVAL_DISABLED')
        if operator not in current_config['cost_approvers']:
            raise RuntimeFault('PRODUCTION_COST_APPROVER_DENIED')
        actor = 'feishu:' + binding['tenant_key'] + ':' + operator
        base, table = binding['base_token'], binding['table_id']
        client = self.client_factory(current_config['app_id'])
        names = current_config['names']
        options = _single_select(client, base, table, current_config['fields']['cost'],
                                 names['cost'], COST_CONFIRM)
        if '未确认' not in options.values():
            raise RuntimeFault('PRODUCTION_FIELD_SCHEMA_CHANGED')
        from .feishu_native_review import _changed, source_key
        changed = _changed(action, current_config['fields']['cost'], options)
        if not changed or changed[1] != COST_CONFIRM:
            return None
        if changed[0] != '未确认':
            raise RuntimeFault('PRODUCTION_COST_TRANSITION_INVALID')
        with self.store.connect() as db:
            saved = meta(db, plan_key(project, record_id))
        if not saved or saved['state'] not in ('ready', 'confirmed'):
            raise RuntimeFault('PRODUCTION_PLAN_NOT_READY')
        snapshot = saved['snapshot']
        if (snapshot['binding_sha256'] != fingerprint(binding)
                or snapshot['native_sha256'] != fingerprint(native)
                or snapshot['policy_sha256'] != fingerprint(current_config)
                or snapshot['project_digest'] != project_digest
                or snapshot['setup_sha256'] != fingerprint(setup)
                or snapshot['profile_sha256'] != fingerprint(profile)
                or snapshot['deployment'] != deployment):
            raise RuntimeFault('PRODUCTION_PLAN_CHANGED')
        if saved['state'] == 'confirmed':
            if saved.get('approver') != actor or saved.get('approval_event') != event_id:
                raise RuntimeFault('PRODUCTION_COST_ALREADY_CONFIRMED')
            return {'status': 'script_queued', 'replayed': True, 'provider_requests': 0}
        if saved['expires_at'] <= self.clock():
            raise RuntimeFault('PRODUCTION_PLAN_EXPIRED')
        remote = client.record(base, table, record_id)['fields']
        if select_text(remote.get(names['cost'])) != COST_CONFIRM:
            raise RuntimeFault('PRODUCTION_COST_REMOTE_CHANGED')
        brief = plain_text(remote.get(names['brief']))
        if (plain_text(remote.get(native['names']['task'])) != snapshot['task']
                or plain_text(remote.get(native['names']['sku_id'])) != snapshot['sku_id']
                or plain_text(remote.get(native['names']['source_revision'])) != snapshot['source_revision']
                or hashlib.sha256(brief.encode()).hexdigest() != snapshot['brief_sha256']
                or plain_text(remote.get(names['plan'])) != saved['summary']
                or select_text(remote.get(names['operation'])) != REQUEST):
            raise RuntimeFault('PRODUCTION_PLAN_REMOTE_CHANGED')
        status = remote.get(native['names']['status']) or ''
        if (select_text(status) if status else '') != ('脚本退回' if snapshot['expected_revision'] else ''):
            raise RuntimeFault('PRODUCTION_REVIEW_STATE_CONFLICT')
        product = next((item for item in setup['configuration']['project']['products']
                        if item['sku_id'] == snapshot['sku_id']), None)
        if not product: raise RuntimeFault('PRODUCTION_SKU_NOT_IN_PROJECT')
        with self.store.connect() as db:
            secret = db.execute('SELECT revision FROM vault WHERE alias=?',
                                (profile['credential_ref'][7:],)).fetchone()
            if not secret or secret[0] != profile['credential_revision']:
                raise RuntimeFault('PRODUCTION_CREDENTIAL_CHANGED')
            current = meta(db, plan_key(project, record_id))
            if current != saved or meta(db, config_key(project)) != config:
                raise RuntimeFault('PRODUCTION_PLAN_CHANGED')
            if current['state'] == 'confirmed':
                if current['approver'] != actor or current['approval_event'] != event_id:
                    raise RuntimeFault('PRODUCTION_COST_ALREADY_CONFIRMED')
                return {'status': 'script_queued', 'replayed': True, 'provider_requests': 0}
            latest = db.execute('SELECT revision,state FROM tasks WHERE project=? AND id=? '
                                'ORDER BY revision DESC LIMIT 1', (project, snapshot['task'])).fetchone()
            if (latest['revision'] if latest else 0) != snapshot['expected_revision']:
                raise RuntimeFault('PRODUCTION_TASK_REVISION_CHANGED')
            if latest and latest['state'] not in ('rejected', 'accepted', 'failed'):
                raise RuntimeFault('PRODUCTION_TASK_NOT_READY')
            if latest:
                previous_input = json.loads(db.execute(
                    'SELECT input FROM tasks WHERE project=? AND id=? AND revision=?',
                    (project, snapshot['task'], snapshot['expected_revision'])).fetchone()[0])
                if previous_input['source_revision'] == snapshot['source_revision']:
                    raise RuntimeFault('PRODUCTION_SOURCE_REVISION_MUST_ADVANCE')
                from .script_jobs import previous_feedback
                feedback = previous_feedback(db, project, snapshot['task'], snapshot['expected_revision'])
                if hashlib.sha256(feedback.encode()).hexdigest() != snapshot['feedback_sha256']:
                    raise RuntimeFault('PRODUCTION_REVIEW_FEEDBACK_CHANGED')
            else:
                feedback = ''
            revision = snapshot['expected_revision'] + 1
            from .script_jobs import key as script_key
            job_plan = {'project': project, 'task': snapshot['task'], 'sku': product, 'brief': brief,
                        'feedback': feedback, 'revision': revision, 'actor': snapshot['requester'],
                        'deployment': deployment, 'configuration_digest': project_digest,
                        'binding_sha256': fingerprint(binding), 'profile': profile,
                        'max_submissions': 1, 'native_config_sha256': fingerprint(native),
                        'native_source': {'record_id': record_id,
                                          'source_revision': snapshot['source_revision'],
                                          'script_digest': fingerprint(brief),
                                          'source_field': names['brief'],
                                          'cost_field': names['cost'], 'cost_value': COST_CONFIRM,
                                          'plan_field': names['plan'], 'plan_value': saved['summary']},
                        'production_gate': {'plan_key': plan_key(project, record_id),
                                            'snapshot_sha256': fingerprint(snapshot),
                                            'policy_sha256': fingerprint(current_config),
                                            'approver': actor, 'expires_at': saved['expires_at']}}
            result = self.store._create_task(db, snapshot['requester'], project, snapshot['task'],
                                             {'sku_id': snapshot['sku_id'], 'script': brief,
                                              'source_revision': snapshot['source_revision']},
                                             snapshot['expected_revision'])
            db.execute("UPDATE tasks SET state='script_queued' WHERE project=? AND id=? AND revision=?",
                       (project, snapshot['task'], revision))
            job_sha = fingerprint(job_plan)
            save(db, script_key(project, snapshot['task'], revision),
                 {'plan': job_plan, 'plan_sha256': job_sha, 'state': 'queued',
                  'expires_at': min(saved['expires_at'], self.clock() + 3600), 'provider_id': None})
            existing_source = meta(db, source_key(project, snapshot['task']))
            if existing_source and existing_source['record_id'] != record_id:
                raise RuntimeFault('PRODUCTION_TASK_SOURCE_CONFLICT')
            save(db, source_key(project, snapshot['task']), {'record_id': record_id})
            current.update(state='confirmed', approver=actor, approval_event=event_id,
                           job_sha256=job_sha)
            save(db, plan_key(project, record_id), current)
            self.store.audit(db, actor, 'production_cost_confirmed', project, snapshot['task'], revision)
        return {**result, 'state': 'script_queued', 'provider_requests': 0}
