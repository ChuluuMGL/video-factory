"""Cloud-only synthetic field migration and administrator approval tests."""
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

from video_factory.feishu_bridge import meta, save
from video_factory.feishu_production_setup import FIELDS, ProductionSetup
from video_factory.runtime_store import RuntimeFault, RuntimeStore


class Client:
    def __init__(self):
        self.schema = []
        self.writes = []
        self.lose_response = False

    def base_subscription_status(self, base):
        return base == 'bascnFixture'

    def fields(self, base, table):
        assert (base, table) == ('bascnFixture', 'tblFixture')
        return list(self.schema)

    def create_field(self, base, table, name, kind, ticket, options=None):
        self.writes.append((name, ticket))
        field = {'field_id': 'fldAdded' + str(len(self.schema) + 1),
                 'field_name': name, 'type': kind}
        if options:
            field['property'] = {'options': [{'id': 'opt' + str(i), 'name': label}
                                              for i, label in enumerate(options)]}
        self.schema.append(field)
        if self.lose_response:
            self.lose_response = False
            raise RuntimeError('lost response after field creation')
        return field['field_id']


class ProductionSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = RuntimeStore.install(Path(self.tmp.name), 'fixture', 'fixture-admin-password')
        self.admin = self.store.login('admin', 'fixture-admin-password')['token']
        self.store.put_project(self.admin, 'brand', {'video_route': 'deferred',
                                                     'credential_ref': 'secret:fixture',
                                                     'billing_owner': 'fixture'})
        master_key = Fernet.generate_key()
        self.store.put_secret(self.admin, 'script', 'synthetic-key-only', master_key)
        self.store.put_secret(self.admin, 'feishu_fixture', 'synthetic-app-secret', master_key)
        self.client = Client()
        binding = {'tenant_key': 'fixture_tenant', 'base_token': 'bascnFixture',
                   'table_id': 'tblFixture', 'fields': {'task': 'fldTask', 'sku_id': 'fldSkuId',
                   'script': 'fldScript', 'source_revision': 'fldSource'},
                   'submitters': ['ou_submitter'], 'reviewers': ['ou_reviewer']}
        with self.store.connect() as db:
            save(db, 'feishu:binding:brand', binding)
            save(db, 'setup:feishu-app:brand', {'app_id': 'cli_fixture',
                                                'credential_ref': 'secret:feishu_fixture'})
            context = self.store_context(db)
            save(db, 'native:config:brand', {'enabled': True, 'app_id': 'cli_fixture',
                                            'context': context, 'fields': {}, 'names': {}})
            save(db, 'setup:project:brand', {'configuration': {'project': {'products': [
                {'sku_id': 'sku_one', 'name': 'Synthetic SKU'}]}}})
            save(db, 'script:profile:brand', {'model': 'deepseek-chat',
                                              'credential_ref': 'secret:script',
                                              'credential_revision': 1,
                                              'billing_owner': 'fixture'})
        self.setup = ProductionSetup(self.store, b'fixture', client_factory=lambda _: self.client)

    def store_context(self, db):
        from video_factory.base_results import BaseResults
        return BaseResults(self.store, b'fixture').context(db, 'brand')

    def tearDown(self):
        self.tmp.cleanup()

    def test_plan_is_read_only_and_enable_readbacks_fields(self):
        plan = self.setup.prepare(self.admin, 'brand', ['ou_submitter'], False)
        self.assertEqual(plan['plan']['fields_to_create'], [item[0] for item in FIELDS.values()])
        self.assertEqual(self.client.writes, [])
        receipt = self.setup.enable(self.admin, 'brand', ['ou_submitter'], False,
                                    plan['plan_sha256'])
        self.assertEqual(receipt['provider_requests'], 0)
        self.assertFalse(receipt['cost_enabled'])
        self.assertEqual(len(self.client.writes), 5)
        with self.store.connect() as db:
            value = meta(db, 'production:config:brand')
            self.assertTrue(value['enabled'])
            self.assertFalse(value['cost_enabled'])
            self.assertEqual(set(value['fields']), {'operation', 'cost'})
            self.assertEqual(set(value['names']), set(FIELDS))

    def test_wrong_approver_and_changed_plan_cannot_mutate_base(self):
        with self.assertRaisesRegex(RuntimeFault, 'PRODUCTION_APPROVER_NOT_BOUND'):
            self.setup.prepare(self.admin, 'brand', ['ou_outsider'], True)
        plan = self.setup.prepare(self.admin, 'brand', ['ou_submitter'], True)
        with self.store.connect() as db:
            profile = meta(db, 'script:profile:brand')
            profile['billing_owner'] = 'changed'
            save(db, 'script:profile:brand', profile)
        with self.assertRaisesRegex(RuntimeFault, 'PRODUCTION_SETUP_PLAN_CHANGED'):
            self.setup.enable(self.admin, 'brand', ['ou_submitter'], True, plan['plan_sha256'])
        self.assertEqual(self.client.writes, [])

    def test_lost_field_response_resumes_without_duplicate_create(self):
        plan = self.setup.prepare(self.admin, 'brand', ['ou_submitter'], True)
        self.client.lose_response = True
        with self.assertRaisesRegex(RuntimeError, 'lost response'):
            self.setup.enable(self.admin, 'brand', ['ou_submitter'], True, plan['plan_sha256'])
        self.assertEqual(len(self.client.writes), 1)
        resumed = self.setup.prepare(self.admin, 'brand', ['ou_submitter'], True)
        receipt = self.setup.enable(self.admin, 'brand', ['ou_submitter'], True,
                                    resumed['plan_sha256'])
        self.assertTrue(receipt['cost_enabled'])
        self.assertEqual(len(self.client.writes), 5)
        self.assertEqual(len(set(name for name, _ in self.client.writes)), 5)

    def test_conflicting_existing_field_stops_before_writes(self):
        self.client.schema.append({'field_id': 'fldConflict', 'field_name': '费用确认', 'type': 1})
        with self.assertRaisesRegex(RuntimeFault, 'PRODUCTION_FIELD_SCHEMA_CONFLICT'):
            self.setup.prepare(self.admin, 'brand', ['ou_submitter'], False)
        self.assertEqual(self.client.writes, [])

    def test_pause_is_idempotent_and_revokes_paid_entry(self):
        plan = self.setup.prepare(self.admin, 'brand', ['ou_submitter'], True)
        self.setup.enable(self.admin, 'brand', ['ou_submitter'], True, plan['plan_sha256'])
        self.assertTrue(self.setup.status(self.admin, 'brand')['cost_enabled'])
        for _ in range(2):
            self.assertEqual(self.setup.pause(self.admin, 'brand')['provider_requests'], 0)
        self.assertFalse(self.setup.status(self.admin, 'brand')['enabled'])
        with self.store.connect() as db:
            value = meta(db, 'production:config:brand')
            self.assertFalse(value['enabled'])
            self.assertFalse(value['cost_enabled'])
        from video_factory.feishu_production_entry import policy
        with self.assertRaisesRegex(RuntimeFault, 'PRODUCTION_PLAN_DISABLED'):
            policy(self.store, 'brand')


if __name__ == '__main__':
    unittest.main()
