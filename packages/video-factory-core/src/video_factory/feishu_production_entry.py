"""Opt-in, zero-provider-request plans from verified original-Base events.

This is the preparation half of the Base production flow. It never queues a
ScriptJobs or VideoJobs request; a separate cost-approval implementation is
required before a plan can spend money. The opt-in configuration is deliberately
absent from existing installations.
"""
import hashlib
import time

from .feishu_bridge import FeishuBridge, meta, plain_text, save, select_text
from .feishu_client import resource
from .runtime_store import RuntimeFault, fingerprint, identifier

REQUEST = '申请脚本计划'
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
    required = {'enabled', 'app_id', 'tenant_key', 'base_token', 'table_id', 'fields', 'names'}
    if set(value) != required or set(value['fields']) != {'operation'} or set(value['names']) != {
            'operation', 'brief', 'plan', 'hint'}:
        raise RuntimeFault('PRODUCTION_CONFIGURATION_INVALID')
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
        from .feishu_native_review import _changed
        changed = _changed(action, current_config['fields']['operation'], options)
        if not changed or changed[1] != REQUEST or changed[0] == REQUEST:
            return None
        remote = client.record(base, table, record_id)['fields']
        if select_text(remote.get(names['operation'])) != REQUEST:
            raise RuntimeFault('PRODUCTION_REQUEST_REMOTE_CHANGED')
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
            secret = db.execute('SELECT revision FROM vault WHERE alias=?',
                                (profile['credential_ref'][7:],)).fetchone()
            if not secret or secret[0] != profile['credential_revision']:
                raise RuntimeFault('PRODUCTION_CREDENTIAL_CHANGED')
        now = self.clock()
        snapshot = {'project': project, 'record_id': record_id, 'task': task, 'sku_id': sku,
                    'source_revision': source_revision, 'brief_sha256': hashlib.sha256(brief.encode()).hexdigest(),
                    'expected_revision': expected_revision, 'model': profile['model'],
                    'billing_owner': profile['billing_owner'], 'credential_revision': profile['credential_revision'],
                    'binding_sha256': fingerprint(binding), 'native_sha256': fingerprint(native),
                    'policy_sha256': fingerprint(current_config), 'project_digest': project_digest,
                    'setup_sha256': fingerprint(setup), 'profile_sha256': fingerprint(profile),
                    'deployment': deployment, 'max_submissions': 1, 'requester': actor}
        digest = fingerprint(snapshot)
        summary = ('脚本计划 ' + digest[:16] + '｜SKU ' + sku + '｜版本 ' + str(expected_revision + 1)
                   + '｜模型 ' + profile['model'] + '｜费用账户 ' + profile['billing_owner']
                   + '｜最多 1 次提交；费用确认尚未启用，不会生成')
        key = plan_key(project, record_id)
        with self.store.connect() as db:
            if FeishuBridge(self.store)._binding(db, project) != binding or meta(db, config_key(project)) != config:
                raise RuntimeFault('PRODUCTION_CONFIGURATION_CHANGED')
            old = meta(db, key)
            if old:
                if old['snapshot'] != snapshot:
                    raise RuntimeFault('PRODUCTION_PLAN_REQUIRES_RECONCILIATION')
                if old['state'] == 'ready':
                    if old['expires_at'] <= now:
                        raise RuntimeFault('PRODUCTION_PLAN_EXPIRED')
                    if remote.get(names['plan']) != old['summary'] or not source_still_matches(remote):
                        raise RuntimeFault('PRODUCTION_PLAN_REMOTE_CHANGED')
                    return {'status': 'plan_ready', 'plan_sha256': digest, 'replayed': True, 'provider_requests': 0}
                if old['state'] != 'write_unknown': raise RuntimeFault('PRODUCTION_PLAN_STATE_INVALID')
            else:
                save(db, key, {'snapshot': snapshot, 'summary': summary, 'state': 'write_unknown',
                               'event_id': event_id, 'expires_at': now + PLAN_LIFETIME})
        if old:
            remote = client.record(base, table, record_id)['fields']
            if remote.get(names['plan']) != summary:
                raise RuntimeFault('PRODUCTION_PLAN_WRITE_UNKNOWN')
        else:
            try:
                client.update_record(base, table, record_id, {names['plan']: summary,
                                                               names['hint']: '计划已准备；费用确认尚未启用'})
            except Exception:
                raise RuntimeFault('PRODUCTION_PLAN_WRITE_UNKNOWN') from None
            remote = client.record(base, table, record_id)['fields']
            if remote.get(names['plan']) != summary:
                raise RuntimeFault('PRODUCTION_PLAN_WRITE_UNKNOWN')
        if not source_still_matches(remote):
            raise RuntimeFault('PRODUCTION_PLAN_REMOTE_CHANGED')
        with self.store.connect() as db:
            current = meta(db, key)
            if not current or current['snapshot'] != snapshot or current['summary'] != summary:
                raise RuntimeFault('PRODUCTION_PLAN_CHANGED')
            current['state'] = 'ready'
            save(db, key, current)
            self.store.audit(db, actor, 'production_plan_prepared', project, task, expected_revision + 1)
        return {'status': 'plan_ready', 'plan_sha256': digest, 'expires_at': now + PLAN_LIFETIME,
                'provider_requests': 0}
