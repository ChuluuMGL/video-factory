import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

from video_factory.onboarding import new_session, apply_answers
from video_factory.runtime_store import RuntimeStore, RuntimeFault
from video_factory.setup_project import import_project, inspect_project, DISABLED_CONFIGURATION
from video_factory.setup_deploy import execution_plan

PASSWORD = 'synthetic-setup-password'
EXAMPLE = Path(__file__).resolve().parents[1] / 'examples/setup/answers.json'


class SetupDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        ledger = self.root / 'ledger'; ledger.mkdir(mode=0o700)
        answers = json.loads(EXAMPLE.read_text())
        self.session = apply_answers(new_session(), answers, 0)[0]
        self.store = RuntimeStore.install(ledger, 'example_test', PASSWORD)
        self.token = self.store.login('admin', PASSWORD)['token']

    def tearDown(self):
        self.temp.cleanup()

    def test_import_persists_skus_but_not_paid_activation_or_fake_identity(self):
        result = import_project(self.store, self.token, self.session)
        self.assertEqual(result['sku_count'], 1)
        self.assertEqual(result['requested_video_route'], 'minimax_h3_canary')
        self.assertEqual(result['active_video_route'], 'deferred')
        self.assertFalse(result['credentials_resolved'])
        self.assertFalse(result['paid_execution_enabled'])
        with self.store.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key='setup:project:new_brand'").fetchone()
            self.assertEqual(json.loads(row[0])['configuration'], self.session['configuration'])
            self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM vault').fetchone()[0], 0)

    def test_repeat_import_is_idempotent_and_preserves_tasks(self):
        import_project(self.store, self.token, self.session)
        self.store.create_task(self.token, 'new_brand', 'one', {'sku_id':'EXAMPLE-001','script':'fixture','source_revision':'1'})
        self.assertTrue(import_project(self.store, self.token, self.session)['reused'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM audit WHERE action='setup_project_import'").fetchone()[0], 1)

    def test_conflicting_sku_fails_without_overwriting_prior_import(self):
        import_project(self.store, self.token, self.session)
        changed = copy.deepcopy(self.session)
        changed['configuration']['project']['products'][0]['name'] = 'Changed item'
        with self.assertRaisesRegex(RuntimeFault, 'RECONCILIATION'):
            import_project(self.store, self.token, changed)
        self.assertEqual(inspect_project(self.store, self.token, self.session)['sku_count'], 1)

    def test_other_customer_and_deployment_cannot_adopt_stack(self):
        import_project(self.store, self.token, self.session)
        for group, field, value, code in [('organization','id','other_org','BINDING_CONFLICT'),
                                          ('deployment','id','other_deploy','DEPLOYMENT_MISMATCH')]:
            changed = copy.deepcopy(self.session); changed['configuration'][group][field] = value
            changed['configuration']['project']['id'] = 'other_project'
            with self.assertRaisesRegex(RuntimeFault, code):import_project(self.store, self.token, changed)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM projects').fetchone()[0], 1)

    def test_second_project_in_same_customer_keeps_separate_skus(self):
        import_project(self.store, self.token, self.session)
        other = copy.deepcopy(self.session)
        other['configuration']['project']['id'] = 'other_project'
        other['configuration']['project']['products'][0]['sku_id'] = 'OTHER-001'
        self.assertFalse(import_project(self.store, self.token, other)['reused'])
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM projects').fetchone()[0], 2)
        self.assertEqual(inspect_project(self.store, self.token, self.session)['project'], 'new_brand')

    def test_unowned_runtime_project_and_later_runtime_drift_are_rejected(self):
        self.store.put_project(self.token, 'new_brand', DISABLED_CONFIGURATION)
        with self.assertRaisesRegex(RuntimeFault, 'NOT_OWNED'):import_project(self.store, self.token, self.session)
        other = copy.deepcopy(self.session);other['configuration']['project']['id'] = 'second'
        result = import_project(self.store, self.token, other)
        self.store.put_project(self.token, 'second', {**DISABLED_CONFIGURATION, 'credential_ref':'secret:changed'}, result['runtime_configuration_digest'])
        with self.assertRaisesRegex(RuntimeFault, 'RECONCILIATION'):inspect_project(self.store, self.token, other)

    def test_reviewer_and_incomplete_session_cannot_import(self):
        import_project(self.store, self.token, self.session)
        self.store.set_user(self.token, 'reviewer', PASSWORD, 'new_brand')
        token = self.store.login('reviewer', PASSWORD)['token']
        with self.assertRaisesRegex(RuntimeFault, 'DENIED'):import_project(self.store, token, self.session)
        with self.assertRaisesRegex(RuntimeFault, 'INCOMPLETE'):import_project(self.store, self.token, new_session())

    def test_concurrent_import_creates_one_receipt(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: import_project(self.store, self.token, self.session), range(2)))
        self.assertEqual(sorted(r['reused'] for r in results), [False, True])

    def test_execution_digest_binds_machine_plan_release_and_destination(self):
        wheels = self.root/'wheels';wheels.mkdir(mode=0o700)
        wheel = wheels/'video_factory_core-fixture.whl';wheel.write_bytes(b'fixture')
        args = SimpleNamespace(root=self.root/'destination', session=self.root/'session.json',
            host='customer.example.invalid', wheelhouse=wheels, runtime_port=None, n8n_port=None)
        with patch('video_factory.setup_deploy.local_machine', return_value={'machine_id_sha256':'one'}):
            first = execution_plan(args, self.session)
            self.assertFalse(args.root.exists())
            args.root = self.root/'another';second = execution_plan(args, self.session)
            self.assertNotEqual(first['execution_sha256'], second['execution_sha256'])
            wheel.write_bytes(b'changed');third = execution_plan(args, self.session)
            self.assertNotEqual(second['execution_sha256'], third['execution_sha256'])
        with patch('video_factory.setup_deploy.local_machine', return_value={'machine_id_sha256':'two'}):
            self.assertNotEqual(third['execution_sha256'], execution_plan(args,self.session)['execution_sha256'])
        args.host = 'wrong.example.invalid'
        with self.assertRaisesRegex(RuntimeFault,'HOST_LABEL_MISMATCH'):execution_plan(args,self.session)


if __name__ == '__main__':unittest.main()
