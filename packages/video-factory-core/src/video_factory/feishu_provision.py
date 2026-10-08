"""Reviewed creation of a customer-owned Base with a durable write journal.

Only the provisioner can write, only into resources it just created. Ambiguous
non-idempotent writes are fenced instead of blindly retried. Tokens stay in RAM.
"""
import copy
import json
import os
import uuid
from urllib.request import Request, ProxyHandler, build_opener

from .feishu_client import FeishuClient, FeishuHandler, resource
from .feishu_bridge import FeishuBridge, meta, save, plain_text
from .h3_provider import NoRedirect
from .runtime_store import RuntimeFault, fingerprint

TASK_FIELDS = {'task': '任务编号', 'sku_id': 'SKU', 'script': '脚本', 'source_revision': '来源版本'}
from .feishu_native_sync import REVIEW_FIELDS
PRODUCT_FIELDS = ('SKU', '商品名称', '规格', '事实来源')


class ProvisionClient(FeishuClient):
    def post(self, path, body):
        handlers = [ProxyHandler({}), NoRedirect()]
        if os.environ.get('VF_WORKER_EGRESS') == '1':
            if os.environ.get('VF_CONTAINER_MODE') != '1':
                raise RuntimeFault('EGRESS_REQUIRES_CONTAINER')
            handlers.append(FeishuHandler())
        try:
            request = Request(self.origin+'/open-apis'+path, data=json.dumps(body).encode(),
                              headers={'Authorization': 'Bearer '+self.token,
                                       'Content-Type': 'application/json; charset=utf-8'}, method='POST')
            with build_opener(*handlers).open(request, timeout=15) as response:
                raw = response.read(2*1024*1024+1)
                if response.status != 200 or len(raw) > 2*1024*1024: raise ValueError
                value = json.loads(raw)
                if type(value.get('code')) is not int or value['code'] != 0 or not isinstance(value.get('data'), dict):
                    raise ValueError
                return value['data']
        except Exception:
            # Even an error body is not used to infer absence of remote effects.
            raise RuntimeFault('FEISHU_PROVISION_OUTCOME_UNKNOWN_READ_STATUS') from None

    def create_base(self, body):
        app = self.post('/bitable/v1/apps', body).get('app', {})
        if body.get('folder_token') and app.get('folder_token') != body['folder_token']:
            raise RuntimeFault('FEISHU_PROVISION_FOLDER_RECEIPT_MISMATCH')
        return {'app_token': resource(app.get('app_token')), 'folder_token': body.get('folder_token', '')}

    def create_table(self, base, name, fields):
        resource(base)
        value = self.post(f'/bitable/v1/apps/{base}/tables', {'table': {'name': name,
            'default_view_name': '全部记录', 'fields': [
                {'field_name': f if isinstance(f,str) else f[0], 'type': 1 if isinstance(f,str) else f[1]}
                for f in fields]}})
        return {'table_id': resource(value.get('table_id'), 'tbl')}

    def create_records(self, base, table, rows, client_token):
        resource(base); resource(table, 'tbl')
        value = self.post(f'/bitable/v1/apps/{base}/tables/{table}/records/batch_create?client_token={uuid.UUID(client_token)}',
                          {'records': [{'fields': row} for row in rows]})
        records = value.get('records')
        if not isinstance(records, list) or len(records) != len(rows):
            raise RuntimeFault('FEISHU_PROVISION_RECEIPT_INVALID')
        ids = [resource(row.get('record_id'), 'rec') for row in records]
        if len(set(ids)) != len(ids): raise RuntimeFault('FEISHU_PROVISION_RECEIPT_INVALID')
        return {'record_ids': ids}

    def base(self, base):
        resource(base)
        app = self.get(f'/bitable/v1/apps/{base}').get('app')
        if not isinstance(app, dict) or app.get('app_token') != base:
            raise RuntimeFault('FEISHU_PROVISION_BASE_READBACK_FAILED')
        return app


def is_create(draft):
    return draft['setup']['configuration']['project']['base_mode'] == 'create'


def specification(draft):
    project = draft['setup']['configuration']['project']
    test = draft['answers']['workspace_kind'] == 'test'
    prefix = 'VF 测试 · ' if test else 'VF · '
    target = project['base_target']
    name = target if target.startswith(prefix) else prefix + target
    if len(name) > 240 or any(ord(c) < 32 for c in name):
        raise RuntimeFault('FEISHU_NEW_BASE_NAME_INVALID')
    products = [dict(zip(PRODUCT_FIELDS, [p['sku_id'], p['name'], p['variant'], p['truth_source']]))
                for p in project['products']]
    tasks = [{'任务编号': 'VF_TEST_'+str(i), 'SKU': project['products'][0]['sku_id'],
              '脚本': '仅用于安装验收的测试脚本 '+str(i)+'；不用于生产或发布。', '来源版本': 'test-v1'}
             for i in (1, 2)] if test else []
    return {'base': {'name': name, **({'folder_token': draft['answers']['folder_token']}
                                    if draft['answers']['folder_token'] else {})},
            'workspace_kind': 'test' if test else 'production', 'product_fields': list(PRODUCT_FIELDS),
            'task_fields': TASK_FIELDS, 'product_rows': products, 'test_rows': tasks,
            'task_table_name': '测试任务' if test else '任务', 'product_table_name': '商品资料'}


def journal_key(draft):
    return 'setup:feishu-provision:'+draft['setup']['configuration']['project']['id']


def binding_for(draft, base, table, fields):
    config = draft['setup']['configuration']
    script = config['project']['script_reviewer'].removeprefix('feishu:')
    video = config['project']['video_reviewer'].removeprefix('feishu:')
    return {'tenant_key': config['deployment']['feishu_tenant'], 'base_token': base, 'table_id': table,
            'fields': fields, 'submitters': draft['answers']['submitters'],
            'reviewers': sorted({script, video}), 'script_reviewers': [script], 'video_reviewers': [video]}


class Provisioner:
    def __init__(self, service): self.service = service

    def prepare(self, admin, draft, user):
        from .setup_feishu import describe
        if describe(draft)['status'] != 'connection_draft_ready':
            raise RuntimeFault('SETUP_FEISHU_QUESTIONS_INCOMPLETE')
        spec = specification(draft)
        with self.service.store.connect() as db:
            context = self.service.context(db, admin, draft)
            journal = meta(db, journal_key(draft))
            recovery = meta(db, 'setup:provision-recovery:'+context['project'])
        identity = self.service.provision_client_factory(user).identity()
        if identity['tenant_key'] != draft['setup']['configuration']['deployment']['feishu_tenant']:
            raise RuntimeFault('FEISHU_TENANT_OR_ROLE_DENIED')
        if recovery and not (journal and journal.get('binding')):
            raise RuntimeFault('FEISHU_PROVISION_RESTORED_CHECKPOINT_REQUIRES_RECONCILIATION')
        if journal:
            if journal['draft_sha256'] != fingerprint(draft) or journal['identity'] != identity:
                raise RuntimeFault('FEISHU_PROVISION_OWNER_OR_DRAFT_CHANGED')
            if any(step['status'] == 'in_flight' for step in journal['steps'].values()):
                raise RuntimeFault('FEISHU_PROVISION_OUTCOME_UNKNOWN_READ_STATUS')
        if context['previous_binding'] and (not journal or journal.get('binding') != context['previous_binding']):
            raise RuntimeFault('FEISHU_TARGET_IMMUTABLE_USE_NEW_PROJECT')
        project = draft['setup']['configuration']['project']
        plan = {'submitters': draft['answers']['submitters'], 'script_reviewer': project['script_reviewer'],
                'video_reviewer': project['video_reviewer'], 'operation': 'create_workspace', 'context': context, 'verified_operator': identity,
                'specification': spec, 'draft_sha256': fingerprint(draft), 'journal_sha256': fingerprint(journal),
                'completed_steps': list(journal['steps']) if journal else [],
                'model_calls': 0, 'changes_existing_base': False}
        return {'plan': plan, 'plan_sha256': fingerprint(plan), 'business_ready': False,
                'feishu_writes': 0, 'model_calls': 0}

    def apply(self, admin, draft, user, expected):
        prepared = self.prepare(admin, draft, user)
        if prepared['plan_sha256'] != expected: raise RuntimeFault('SETUP_FEISHU_PLAN_CHANGED')
        plan = prepared['plan']; context = plan['context']; spec = plan['specification']
        store = self.service.store; key = journal_key(draft)
        client = self.service.provision_client_factory(user)
        # Once a binding has completed, seeded rows belong to the customer's
        # working Base. Their script, revision and product details may evolve.
        # A partial first-time provision must still match every seeded value.
        completed_binding = bool(context['previous_binding'])
        with store.connect() as db:
            if self.service.context(db, admin, draft) != context or fingerprint(meta(db, key)) != plan['journal_sha256']:
                raise RuntimeFault('SETUP_FEISHU_CONTEXT_CHANGED')
            journal = meta(db, key) or {'schema': 1, 'draft_sha256': fingerprint(draft),
                'identity': plan['verified_operator'], 'steps': {}, 'binding': None}
            save(db, key, journal)

        def step(name, operation):
            nonlocal journal
            # Claim before I/O and release the DB lock during the request. A
            # competing process or crash sees in_flight and cannot resubmit.
            with store.connect() as db:
                if self.service.context(db, admin, draft) != context:
                    raise RuntimeFault('SETUP_FEISHU_CONTEXT_CHANGED')
                current = meta(db, key)
                if current != journal: raise RuntimeFault('FEISHU_PROVISION_CONCURRENT_CHANGE')
                if name in journal['steps']:
                    if journal['steps'][name]['status'] != 'done':
                        raise RuntimeFault('FEISHU_PROVISION_OUTCOME_UNKNOWN_READ_STATUS')
                    return journal['steps'][name]['receipt']
                ticket = str(uuid.uuid4())
                journal = copy.deepcopy(journal)
                journal['steps'][name] = {'status': 'in_flight', 'request_id': ticket}
                save(db, key, journal)
            receipt = operation(ticket)
            with store.connect() as db:
                # Preserve the remote receipt even if the admin expires while
                # waiting. Authorization is rechecked before any subsequent I/O.
                if meta(db, key) != journal: raise RuntimeFault('FEISHU_PROVISION_CONCURRENT_CHANGE')
                journal['steps'][name].update(status='done', receipt=receipt)
                save(db, key, journal)
                store.audit(db, context['actor'], 'setup_feishu_create_'+name, context['project'])
            return receipt

        base = step('base', lambda _: client.create_base(spec['base']))['app_token']
        app = client.base(base)
        # GET app does not expose folder_token; validate it in the create receipt.
        if app.get('name') != spec['base']['name']:
            raise RuntimeFault('FEISHU_PROVISION_BASE_CHANGED')
        products = step('product_table', lambda _: client.create_table(base, spec['product_table_name'], PRODUCT_FIELDS))['table_id']
        self.verify_fields(client, base, products, PRODUCT_FIELDS)
        product_records = step('products', lambda ticket: client.create_records(base, products, spec['product_rows'], ticket))
        product_content_unchanged = self.verify_records(
            client, base, products, product_records, spec['product_rows'],
            anchors=('SKU',) if completed_binding else ())
        tasks = step('task_table', lambda _: client.create_table(base, spec['task_table_name'],
                     list(TASK_FIELDS.values())+list(REVIEW_FIELDS.values())))['table_id']
        ids = self.verify_fields(client, base, tasks, TASK_FIELDS.values())
        if spec['test_rows']:
            records = step('test_tasks', lambda ticket: client.create_records(base, tasks, spec['test_rows'], ticket))
            task_content_unchanged = self.verify_records(
                client, base, tasks, records, spec['test_rows'],
                anchors=('任务编号', 'SKU') if completed_binding else ())
        else:
            task_content_unchanged = True
        binding = binding_for(draft, base, tasks, {key: ids[name] for key, name in TASK_FIELDS.items()})
        FeishuBridge._schema(client, binding)
        with store.connect() as db:
            if self.service.context(db, admin, draft) != context or meta(db, key) != journal:
                raise RuntimeFault('SETUP_FEISHU_CONTEXT_CHANGED')
            journal['binding'] = binding
            save(db, key, journal); save(db, 'feishu:binding:'+context['project'], binding)
            db.execute('DELETE FROM meta WHERE key=?', ('feishu:reconfirm:'+context['project'],))
            db.execute('DELETE FROM meta WHERE key=?', ('setup:provision-recovery:'+context['project'],))
            store.audit(db, context['actor'], 'setup_feishu_created_binding', context['project'])
        return {'status': 'connection_binding_saved', 'project': context['project'], 'created_base': base,
                'product_table_id': products, 'task_table_id': tasks, 'binding_sha256': fingerprint(binding),
                'plan_sha256': expected, 'test_task_count': len(spec['test_rows']), 'sku_count': len(spec['product_rows']),
                'operator_identity_verified': True, 'field_schema_verified': True, 'records_verified': True,
                'seed_content_unchanged': product_content_unchanged and task_content_unchanged,
                'reviewer_identity_acceptance': 'not_run', 'business_ready': False, 'model_calls': 0,
                'feishu_writes': len(journal['steps'])-len(plan['completed_steps'])}

    @staticmethod
    def verify_fields(client, base, table, names):
        fields = client.fields(base, table); ids = {}
        for f in fields:
            name = f.get('field_name')
            if name in names:
                if name in ids or f.get('type') != 1: raise RuntimeFault('FEISHU_PROVISION_SCHEMA_CHANGED')
                ids[name] = resource(f.get('field_id'), 'fld')
        if set(ids) != set(names) or len(set(ids.values())) != len(ids):
            raise RuntimeFault('FEISHU_PROVISION_SCHEMA_CHANGED')
        return ids

    @staticmethod
    def verify_records(client, base, table, receipt, rows, anchors=()):
        # Response order isn't guaranteed: compare the actual record set.
        record_ids = receipt['record_ids']
        if len(record_ids) != len(rows) or len(set(record_ids)) != len(record_ids):
            raise RuntimeFault('FEISHU_PROVISION_RECORD_READBACK_FAILED')
        actual = []
        for rid in record_ids:
            fields = client.record(base, table, rid)['fields']
            actual.append({name: plain_text(fields.get(name)) for name in rows[0]})
        if sorted(map(fingerprint, actual)) == sorted(map(fingerprint, rows)):
            return True
        if not anchors or sorted(fingerprint({name: row[name] for name in anchors}) for row in actual) != sorted(
                fingerprint({name: row[name] for name in anchors}) for row in rows):
            raise RuntimeFault('FEISHU_PROVISION_RECORD_READBACK_FAILED')
        return False
