"""Private administrator migration for the original Base production fields.

This setup is opt-in. A plan reads the current table; enable journals every
external field creation and only activates the event path after full readback.
"""
import uuid

from .feishu_bridge import FeishuBridge, meta, save
from .feishu_client import resource
from .feishu_native_sync import NativeSync
from .feishu_production_entry import config_key
from .runtime_store import RuntimeFault, fingerprint, identifier

FIELDS = {
    'operation': ('生产操作', 3, ('申请脚本计划', '申请视频计划')),
    'brief': ('创作要求', 1, None),
    'plan': ('生成计划', 1, None),
    'cost': ('费用确认', 3, ('未确认', '确认此计划的一次生成', '确认此计划的一次视频生成')),
    'hint': ('生产提示', 1, None),
}


def setup_key(project):
    return 'production:setup:' + identifier(project)


def schema(client, base, table):
    names = {logical: spec[0] for logical, spec in FIELDS.items()}
    found = {}
    for field in client.fields(base, table):
        matching = [logical for logical, name in names.items() if name == field.get('field_name')]
        if not matching:
            continue
        logical = matching[0]
        if logical in found or field.get('type') != FIELDS[logical][1]:
            raise RuntimeFault('PRODUCTION_FIELD_SCHEMA_CONFLICT')
        options = FIELDS[logical][2]
        if options is not None:
            props = field.get('property')
            values = props.get('options') if isinstance(props, dict) else None
            labels = [value.get('name') for value in values] if isinstance(values, list) and all(
                isinstance(value, dict) for value in values) else []
            if (not all(isinstance(label, str) for label in labels)
                    or len(labels) != len(set(labels)) or not set(options).issubset(labels)):
                raise RuntimeFault('PRODUCTION_FIELD_OPTIONS_CONFLICT')
        found[logical] = resource(field.get('field_id'), 'fld')
    return found


class ProductionSetup:
    def __init__(self, store, master_key, *, client_factory=None):
        self.store = store
        self.native = NativeSync(store, master_key)
        self.client_factory = client_factory or self.native.client

    def _basis(self, admin, project, approvers):
        identifier(project)
        if not isinstance(approvers, list) or not 1 <= len(approvers) <= 100 or len(set(approvers)) != len(approvers):
            raise RuntimeFault('PRODUCTION_APPROVERS_INVALID')
        for approver in approvers:
            resource(approver, 'ou_')
        with self.store.connect() as db:
            self.store.authorize(db, admin, 'admin')
            binding = FeishuBridge(self.store)._binding(db, project)
            native = meta(db, 'native:config:' + project)
            current_context = self.native.results.context(db, project)
            setup = meta(db, 'setup:project:' + project)
            profile = meta(db, 'script:profile:' + project)
            old = meta(db, config_key(project))
            journal = meta(db, setup_key(project))
        if not native or not native.get('enabled') or not setup or not profile:
            raise RuntimeFault('PRODUCTION_PREREQUISITES_MISSING')
        context = native.get('context')
        target = context.get('target') if isinstance(context, dict) else None
        app_profile = context.get('app_profile') if isinstance(context, dict) else None
        if (context != current_context or not isinstance(target, dict) or not isinstance(app_profile, dict)
                or target.get('base_token') != binding['base_token']
                or target.get('table_id') != binding['table_id']
                or app_profile.get('app_id') != native.get('app_id')):
            raise RuntimeFault('PRODUCTION_NATIVE_CONTEXT_CHANGED')
        eligible = set(binding['submitters']) | set(binding['reviewers'])
        if not set(approvers).issubset(eligible):
            raise RuntimeFault('PRODUCTION_APPROVER_NOT_BOUND')
        basis = {'binding_sha256': fingerprint(binding), 'native_sha256': fingerprint(native),
                 'setup_sha256': fingerprint(setup), 'profile_sha256': fingerprint(profile),
                 'previous_config_sha256': fingerprint(old), 'journal_sha256': fingerprint(journal)}
        return binding, native, context, basis, journal

    def prepare(self, admin, project, approvers, cost_enabled):
        if type(cost_enabled) is not bool:
            raise RuntimeFault('PRODUCTION_COST_MODE_INVALID')
        binding, native, context, basis, journal = self._basis(admin, project, approvers)
        client = self.client_factory(context)
        if not client.base_subscription_status(binding['base_token']):
            raise RuntimeFault('PRODUCTION_EVENT_SUBSCRIPTION_REQUIRED')
        found = schema(client, binding['base_token'], binding['table_id'])
        plan = {'project': project, 'app_id': native['app_id'], 'tenant_key': binding['tenant_key'],
                'base_token': binding['base_token'], 'table_id': binding['table_id'],
                'approvers': list(approvers), 'cost_enabled': cost_enabled,
                'existing': found, 'missing': [logical for logical in FIELDS if logical not in found],
                'fields_to_create': [FIELDS[logical][0] for logical in FIELDS if logical not in found],
                'basis': basis, 'writes_original_table': bool(len(found) != len(FIELDS)),
                'provider_requests': 0}
        return {'plan': plan, 'plan_sha256': fingerprint(plan)}

    def enable(self, admin, project, approvers, cost_enabled, expected_plan):
        prepared = self.prepare(admin, project, approvers, cost_enabled)
        if prepared['plan_sha256'] != expected_plan:
            raise RuntimeFault('PRODUCTION_SETUP_PLAN_CHANGED')
        plan = prepared['plan']
        request_sha = fingerprint([project, approvers, cost_enabled,
                                   {key: value for key, value in plan['basis'].items()
                                    if key != 'journal_sha256'}])
        with self.store.connect() as db:
            actor = self.store.authorize(db, admin, 'admin')
            current = meta(db, setup_key(project))
            if fingerprint(current) != plan['basis']['journal_sha256']:
                raise RuntimeFault('PRODUCTION_SETUP_PLAN_CHANGED')
            if current and current.get('state') == 'in_progress':
                if current['request_sha256'] != request_sha:
                    raise RuntimeFault('PRODUCTION_SETUP_IN_PROGRESS')
            else:
                current = {'state': 'in_progress', 'request_sha256': request_sha, 'steps': {}}
                save(db, setup_key(project), current)
        context = self._basis(admin, project, approvers)[2]
        client = self.client_factory(context)
        base, table = plan['base_token'], plan['table_id']
        for logical, (name, kind, options) in FIELDS.items():
            found = schema(client, base, table)
            if logical not in found:
                with self.store.connect() as db:
                    self.store.authorize(db, admin, 'admin')
                    current = meta(db, setup_key(project))
                    step = current['steps'].get(logical)
                    if not step:
                        step = {'state': 'in_flight', 'ticket': str(uuid.uuid4())}
                        current['steps'][logical] = step
                        save(db, setup_key(project), current)
                client.create_field(base, table, name, kind, step['ticket'], options=options)
                found = schema(client, base, table)
                if logical not in found:
                    raise RuntimeFault('PRODUCTION_FIELD_READBACK_FAILED')
            with self.store.connect() as db:
                current = meta(db, setup_key(project))
                current['steps'][logical] = {'state': 'done', 'field_id': found[logical]}
                save(db, setup_key(project), current)
        found = schema(client, base, table)
        if set(found) != set(FIELDS):
            raise RuntimeFault('PRODUCTION_FIELD_SCHEMA_INCOMPLETE')
        with self.store.connect() as db:
            actor = self.store.authorize(db, admin, 'admin')
            binding = FeishuBridge(self.store)._binding(db, project)
            native = meta(db, 'native:config:' + project)
            setup = meta(db, 'setup:project:' + project)
            profile = meta(db, 'script:profile:' + project)
            if (fingerprint(binding) != plan['basis']['binding_sha256']
                    or fingerprint(native) != plan['basis']['native_sha256']
                    or fingerprint(setup) != plan['basis']['setup_sha256']
                    or fingerprint(profile) != plan['basis']['profile_sha256']
                    or self.native.results.context(db, project) != context):
                raise RuntimeFault('PRODUCTION_SETUP_SOURCE_CHANGED')
            old = meta(db, config_key(project))
            if fingerprint(old) != plan['basis']['previous_config_sha256']:
                raise RuntimeFault('PRODUCTION_SETUP_SOURCE_CHANGED')
            value = {'enabled': True, 'cost_enabled': cost_enabled, 'cost_approvers': list(approvers),
                     'activation_id': uuid.uuid4().hex,
                     'app_id': plan['app_id'], 'tenant_key': plan['tenant_key'],
                     'base_token': base, 'table_id': table,
                     'fields': {key: found[key] for key in ('operation', 'cost')},
                     'names': {key: FIELDS[key][0] for key in FIELDS}}
            save(db, config_key(project), value)
            current = meta(db, setup_key(project))
            current['state'] = 'done'
            save(db, setup_key(project), current)
            self.store.audit(db, actor, 'production_entry_enabled', project)
        return {'project': project, 'status': 'enabled', 'cost_enabled': cost_enabled,
                'fields': found, 'provider_requests': 0, 'event_delivery': 'requires_live_readback'}

    def status(self, admin, project):
        with self.store.connect() as db:
            self.store.authorize(db, admin, 'admin')
            value = meta(db, config_key(project))
            binding = FeishuBridge(self.store)._binding(db, project)
        return {'project': project, 'enabled': bool(value and value['enabled']),
                'cost_enabled': bool(value and value['cost_enabled']),
                'fields': value['fields'] if value else {},
                'approver_count': len(value['cost_approvers']) if value else 0,
                'eligible_approvers': sorted(set(binding['submitters']) | set(binding['reviewers']))}

    def pause(self, admin, project):
        identifier(project)
        with self.store.connect() as db:
            actor = self.store.authorize(db, admin, 'admin')
            value = meta(db, config_key(project))
            if value and (value.get('enabled') or value.get('cost_enabled')):
                value['enabled'] = False
                value['cost_enabled'] = False
                save(db, config_key(project), value)
                self.store.audit(db, actor, 'production_entry_paused', project)
        return {'project': project, 'enabled': False, 'cost_enabled': False,
                'provider_requests': 0}
