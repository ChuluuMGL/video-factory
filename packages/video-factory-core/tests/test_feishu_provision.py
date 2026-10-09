import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_factory.onboarding import new_session, apply_answers
from video_factory.runtime_store import RuntimeStore, RuntimeFault
from video_factory.setup_project import import_project
from video_factory.setup_feishu import ConnectionSession, SetupFeishu, describe
from video_factory.feishu_bridge import meta
from video_factory.feishu_provision import ProvisionClient, PRODUCT_FIELDS, TASK_FIELDS, specification
from video_factory.feishu_native_sync import REVIEW_FIELDS, STATUS_OPTIONS
from video_factory.feishu_oauth import DeviceOAuth, SCOPES, CREATE_SCOPES


class Remote:
    def __init__(self):
        self.writes = []; self.app = None; self.tables = {}; self.rows = {}
        self.fail_after = None; self.wrong_tenant = False; self.break_read = False
        self.callback = None

    def identity(self):
        return {'tenant_key': 'another' if self.wrong_tenant else 'fixture_tenant', 'open_id': 'ou_owner'}

    def done(self, kind, receipt):
        self.writes.append(kind)
        if self.callback: self.callback(kind)
        if self.fail_after == kind: raise RuntimeFault('FEISHU_PROVISION_OUTCOME_UNKNOWN_READ_STATUS')
        return receipt

    def create_base(self, body):
        self.app = {'app_token': 'bascnCreated', **body}
        return self.done('base', {'app_token': 'bascnCreated'})

    def base(self, base):
        assert base == 'bascnCreated'
        return {k: v for k, v in self.app.items() if k != 'folder_token'}

    def create_table(self, base, name, fields):
        tid = 'tblCreated'+str(len(self.tables))
        self.tables[tid] = [{'field_id': 'fld'+str(len(self.tables))+str(i)+'Created',
                             'field_name': field if isinstance(field,str) else field[0],
                             'type': 1 if isinstance(field,str) else field[1],
                             **({'property':{'options':[{'name':option} for option in STATUS_OPTIONS]}}
                                if not isinstance(field,str) and field[0]=='状态' else {})}
                            for i, field in enumerate(fields)]
        return self.done('table', {'table_id': tid})

    def fields(self, base, table):
        return copy.deepcopy(self.tables[table])

    def create_records(self, base, table, rows, ticket):
        import uuid
        assert uuid.UUID(ticket).version == 4
        ids = []
        for row in rows:
            rid = 'recCreated'+str(len(self.rows)); ids.append(rid)
            self.rows[rid] = {'record_id': rid, 'fields': row.copy()}
        return self.done('records', {'record_ids': list(reversed(ids))})

    def record(self, base, table, rid):
        if self.break_read: raise RuntimeFault('FEISHU_READ_OR_USER_AUTH_FAILED')
        return copy.deepcopy(self.rows[rid])


class ProvisionTests(unittest.TestCase):
    def test_create_table_sends_single_select_options(self):
        calls=[]
        client=ProvisionClient('synthetic-token')
        with patch.object(client,'post',side_effect=lambda path,body: calls.append((path,body)) or {'table_id':'tblCreated'}):
            client.create_table('bascnCreated','测试任务',list(TASK_FIELDS.values())+list(REVIEW_FIELDS.values()))
        status=next(field for field in calls[0][1]['table']['fields'] if field['field_name']=='状态')
        self.assertEqual(status['type'],3)
        self.assertEqual([option['name'] for option in status['property']['options']],list(STATUS_OPTIONS))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        (self.root/'runtime').mkdir(mode=0o700)
        self.store = RuntimeStore.install(self.root/'runtime', 'fixture', 'synthetic-fixture-password')
        self.admin = self.store.login('admin', 'synthetic-fixture-password')['token']
        answers = json.loads((Path(__file__).resolve().parents[1]/'examples/setup/answers.json').read_text())
        answers.update({'deployment.id': 'fixture', 'deployment.feishu_tenant': 'fixture_tenant',
                        'project.id': 'new_brand', 'project.base_mode': 'create', 'project.base_target': '安装演练',
                        'project.script_reviewer': 'feishu:ou_owner', 'project.video_reviewer': 'feishu:ou_video'})
        self.setup = apply_answers(new_session(), answers, 0)[0]
        import_project(self.store, self.admin, self.setup)
        self.session = ConnectionSession(self.root/'connection.json'); self.session.start(self.setup)
        self.session.answer({'workspace_kind': 'test', 'folder_token': '', 'submitters': ['ou_owner']}, 0)
        self.draft = self.session.snapshot(); self.remote = Remote()
        self.service = SetupFeishu(self.store, provision_client_factory=lambda _: self.remote)

    def tearDown(self): self.temp.cleanup()

    def prepare(self): return self.service.prepare(self.admin, self.draft, 'synthetic-user-token')
    def apply(self):
        return self.service.apply(self.admin, self.draft, 'synthetic-user-token', self.prepare()['plan_sha256'])

    def test_test_base_name_does_not_repeat_prefix(self):
        self.draft['setup']['configuration']['project']['base_target'] = 'VF 测试 · 安装演练'
        self.assertEqual(specification(self.draft)['base']['name'], 'VF 测试 · 安装演练')

    def test_review_then_create_readback_bind_and_repeat_without_writes(self):
        plan = self.prepare(); self.assertEqual(self.remote.writes, [])
        self.assertEqual(len(plan['plan']['specification']['test_rows']), 2)
        self.assertNotIn('synthetic-user-token', json.dumps(plan))
        result = self.apply()
        self.assertEqual(result['status'], 'connection_binding_saved')
        self.assertEqual(result['feishu_writes'], 5)
        self.assertTrue(self.service.status(self.admin, self.draft)['binding_matches_draft'])
        writes = self.remote.writes.copy()
        self.assertEqual(self.apply()['feishu_writes'], 0)
        self.assertEqual(self.remote.writes, writes)
        self.assertNotIn('synthetic-user-token', json.dumps(self.service.status(self.admin, self.draft)))
        with self.store.connect() as db:
            binding = meta(db, 'feishu:binding:new_brand')
        self.assertEqual(binding['base_token'], 'bascnCreated')
        self.assertEqual(set(binding['fields']), set(TASK_FIELDS))
        status=next(f for f in self.remote.tables[binding['table_id']] if f['field_name']=='状态')
        self.assertEqual(status['type'],3)
        self.assertEqual([option['name'] for option in status['property']['options']],list(STATUS_OPTIONS))

    def test_folder_is_verified_in_create_receipt_not_unavailable_get_field(self):
        self.draft['answers']['folder_token'] = 'fldcnDestination'
        self.apply()
        self.assertEqual(self.remote.app['folder_token'], 'fldcnDestination')

    def test_production_does_not_seed_test_tasks(self):
        self.draft['answers']['workspace_kind'] = 'production'
        result = self.apply()
        self.assertEqual(result['test_task_count'], 0)
        self.assertEqual(result['feishu_writes'], 4)
        self.assertEqual(len(self.remote.rows), len(self.setup['configuration']['project']['products']))

    def test_wrong_tenant_changed_plan_or_admin_cannot_write(self):
        plan = self.prepare(); self.remote.wrong_tenant = True
        with self.assertRaisesRegex(RuntimeFault, 'TENANT'): self.apply()
        self.remote.wrong_tenant = False
        with self.assertRaisesRegex(RuntimeFault, 'PLAN_CHANGED'):
            self.service.apply(self.admin, self.draft, 'token', '0'*64)
        with self.assertRaisesRegex(RuntimeFault, 'AUTH'):
            self.service.apply('wrong', self.draft, 'token', plan['plan_sha256'])
        self.assertEqual(self.remote.writes, [])

    def test_lost_base_response_never_resubmits_on_resume(self):
        self.remote.fail_after = 'base'
        with self.assertRaisesRegex(RuntimeFault, 'OUTCOME_UNKNOWN'): self.apply()
        self.remote.fail_after = None
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeFault, 'OUTCOME_UNKNOWN'): self.apply()
        self.assertEqual(self.remote.writes, ['base'])
        state = self.service.status(self.admin, self.draft)
        self.assertEqual(state['provisioning']['steps']['base']['status'], 'in_flight')
        self.assertFalse(state['binding_matches_draft'])

    def test_readback_failure_resumes_known_receipts_without_duplicate_records(self):
        self.remote.break_read = True
        with self.assertRaisesRegex(RuntimeFault, 'READ_OR_USER_AUTH'): self.apply()
        self.remote.break_read = False
        result = self.apply()
        self.assertEqual(result['feishu_writes'], 2)
        self.assertEqual(self.remote.writes, ['base', 'table', 'records', 'table', 'records'])

    def test_concurrent_window_cannot_repeat_in_flight_write(self):
        def concurrent(kind):
            if kind == 'base':
                with self.assertRaisesRegex(RuntimeFault, 'OUTCOME_UNKNOWN'): self.apply()
        self.remote.callback = concurrent
        self.apply(); self.assertEqual(self.remote.writes.count('base'), 1)

    def test_changed_remote_schema_refused_and_original_setup_not_rewritten(self):
        self.apply()
        tid = next(reversed(self.remote.tables)); self.remote.tables[tid][0]['type'] = 2
        with self.assertRaisesRegex(RuntimeFault, 'SCHEMA_CHANGED'): self.apply()
        self.assertEqual(self.setup['configuration']['project']['base_mode'], 'create')

    def test_recovery_before_first_external_write_is_fenced(self):
        with self.store.connect() as db: self.store.invalidate_worker_approvals(db)
        with self.assertRaisesRegex(RuntimeFault, 'RESTORED_CHECKPOINT'): self.apply()
        self.assertEqual(self.remote.writes, [])

    def test_completed_recovery_rechecks_and_reuses_without_creating(self):
        self.apply(); writes = self.remote.writes.copy()
        with self.store.connect() as db: self.store.invalidate_worker_approvals(db)
        self.assertTrue(self.service.status(self.admin, self.draft)['requires_reconfirmation'])
        self.apply()
        self.assertEqual(self.remote.writes, writes)
        self.assertFalse(self.service.status(self.admin, self.draft)['requires_reconfirmation'])

    def test_completed_recovery_preserves_edited_seed_rows(self):
        self.apply(); writes = self.remote.writes.copy()
        task = next(row for row in self.remote.rows.values() if row['fields'].get('任务编号') == 'VF_TEST_1')
        task['fields']['来源版本'] = 'test-v3-global-key'
        task['fields']['脚本'] = '人工审核后修改的脚本'
        product = next(row for row in self.remote.rows.values() if row['fields'].get('SKU'))
        product['fields']['规格'] = '验收过程中更新的规格'
        with self.store.connect() as db: self.store.invalidate_worker_approvals(db)
        result = self.apply()
        self.assertEqual(result['status'], 'connection_binding_saved')
        self.assertFalse(result['seed_content_unchanged'])
        self.assertEqual(self.remote.writes, writes)
        self.assertEqual(task['fields']['来源版本'], 'test-v3-global-key')
        self.assertFalse(self.service.status(self.admin, self.draft)['requires_reconfirmation'])

    def test_completed_recovery_rejects_changed_seed_identity(self):
        self.apply(); writes = self.remote.writes.copy()
        task = next(row for row in self.remote.rows.values() if row['fields'].get('任务编号') == 'VF_TEST_1')
        task['fields']['任务编号'] = 'another-task'
        with self.store.connect() as db: self.store.invalidate_worker_approvals(db)
        with self.assertRaisesRegex(RuntimeFault, 'RECORD_READBACK_FAILED'): self.apply()
        self.assertEqual(self.remote.writes, writes)
        self.assertTrue(self.service.status(self.admin, self.draft)['requires_reconfirmation'])

    def test_initial_create_requires_exact_record_readback(self):
        def change_before_read(kind):
            if kind == 'records':
                self.remote.callback = None
                next(iter(self.remote.rows.values()))['fields']['规格'] = 'changed before first binding'
        self.remote.callback = change_before_read
        with self.assertRaisesRegex(RuntimeFault, 'RECORD_READBACK_FAILED'): self.apply()
        self.assertFalse(self.service.status(self.admin, self.draft)['binding_matches_draft'])

    def test_new_questions_need_no_remote_ids_and_reject_field_injection(self):
        new = ConnectionSession(self.root/'other.json'); new.start(self.setup)
        self.assertEqual(describe(new.snapshot())['next_question']['field'], 'workspace_kind')
        with self.assertRaises(Exception): new.answer({'table_id': 'tblForeign'}, 0)
        new.answer({'workspace_kind': 'test'}, 0)
        self.assertEqual(describe(new.snapshot())['next_question']['field'], 'folder_token')

    def test_oauth_permissions_separate_from_employee_readonly(self):
        readonly = DeviceOAuth('cli_fixture', 'synthetic-secret')
        creator = DeviceOAuth('cli_fixture', 'synthetic-secret', scopes=CREATE_SCOPES)
        self.assertEqual(readonly.scopes, SCOPES)
        response = {'device_code': 'device', 'user_code': 'ABCD', 'verification_uri': 'https://accounts.feishu.cn/verify', 'expires_in': 240}
        with patch.object(creator, 'post', return_value=response) as post:
            creator.start(); self.assertEqual(post.call_args.args[2]['scope'], ' '.join(CREATE_SCOPES))
        with patch.object(creator, 'post', return_value={'access_token': 'token', 'expires_in': 7200, 'scope': ' '.join(SCOPES)}):
            with self.assertRaisesRegex(RuntimeFault, 'SCOPE_MISSING'): creator.poll('device')

    def test_wire_uses_json_fixed_official_host_and_never_echoes_response(self):
        from unittest.mock import MagicMock
        opener = MagicMock(); response = opener.open.return_value.__enter__.return_value
        response.status = 200; response.read.return_value = b'{"code":0,"data":{"app":{"app_token":"bascnCreated"}}}'
        client = ProvisionClient('synthetic-user-token')
        with patch('video_factory.feishu_provision.build_opener', return_value=opener):
            client.create_base({'name': 'Test'})
            request = opener.open.call_args.args[0]
            self.assertEqual(request.full_url, 'https://open.feishu.cn/open-apis/bitable/v1/apps')
            self.assertEqual(json.loads(request.data), {'name': 'Test'})
            with self.assertRaisesRegex(RuntimeFault, 'FOLDER_RECEIPT_MISMATCH'):
                client.create_base({'name': 'Test', 'folder_token': 'fldcnDestination'})
            response.read.return_value = b'{"code":0,"data":{"app":{"app_token":"bascnCreated","folder_token":"fldcnDestination"}}}'
            receipt = client.create_base({'name': 'Test', 'folder_token': 'fldcnDestination'})
            self.assertEqual(receipt['folder_token'], 'fldcnDestination')
            response.read.return_value = b'{"code":123,"msg":"never-echo-secret"}'
            with self.assertRaisesRegex(RuntimeFault, '^FEISHU_PROVISION_OUTCOME_UNKNOWN_READ_STATUS$'): client.create_base({'name': 'Test'})
