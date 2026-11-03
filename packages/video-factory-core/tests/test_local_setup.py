"""Synthetic cloud-only setup/resume contracts; never real external calls."""
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
from video_factory.cli import main
from video_factory.feishu_bridge import meta, save
from video_factory.local_setup import MARKER, authorize_feishu, bind, provision, validate
from video_factory.runtime_store import RuntimeFault, RuntimeStore, canonical, exclusive_write
from video_factory.script_provider import MODEL

BINDING = {'tenant_key': 'tenant_fixture', 'base_token': 'bascnFixture', 'table_id': 'tblFixture',
           'fields': {'task': 'fldTask', 'sku_id': 'fldSkuId', 'script': 'fldScript', 'source_revision': 'fldVersion'},
           'submitters': ['ou_fixture'], 'reviewers': ['ou_fixture']}


def plan():
    return {'deployment': 'local_test', 'project': 'brand_one', 'billing_owner': 'owner_one',
            'products': [{'sku_id': 'sku_one', 'name': 'Synthetic product', 'truth_source': 'Synthetic specifications'}],
            'app_id': 'cli_fixture', 'binding': deepcopy(BINDING), 'script_model': MODEL, 'video': None}


class Client:
    def __init__(self, token): self.token = token
    def identity(self): return {'tenant_key': 'tenant_fixture', 'open_id': 'ou_fixture'}
    def fields(self, base, table):
        return [{'field_id': value, 'field_name': key, 'type': 1} for key, value in BINDING['fields'].items()]


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(); self.state = self.root / 'state'; self.state.mkdir(mode=0o700)
        self.store = RuntimeStore.install(self.state, 'local_test', 'synthetic-password-long')
        self.admin = self.store.login('admin', 'synthetic-password-long')['token']
        with self.store.connect() as db: save(db, MARKER, {'deployment': 'local_test'})
        self.key = Fernet.generate_key(); self.plan = plan()
        self.secrets = {'app': 'synthetic-app-secret', 'script': 'synthetic-script-secret'}

    def put(self, values=None):
        return provision(self.store, self.admin, self.plan, self.key, self.secrets if values is None else values)

    def test_configure_and_resume_without_overwriting_keys_or_enabling_execution(self):
        self.assertFalse(self.put()['reused'])
        with self.store.connect() as db:
            before = [tuple(row) for row in db.execute('SELECT * FROM vault ORDER BY alias')]
            self.assertEqual(db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 0)
        self.assertTrue(self.put({})['reused'])
        with self.store.connect() as db:
            self.assertEqual(before, [tuple(row) for row in db.execute('SELECT * FROM vault ORDER BY alias')])
            self.assertIsNone(meta(db, 'native:config:brand_one'))
        raw = self.store.path.read_bytes()
        for value in self.secrets.values(): self.assertNotIn(value.encode(), raw)

    def test_partial_configuration_failure_rolls_back_keys_and_project_together(self):
        with patch('video_factory.local_setup.profile', side_effect=RuntimeFault('INJECTED_FAILURE')):
            with self.assertRaisesRegex(RuntimeFault, 'INJECTED_FAILURE'): self.put()
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM vault').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM projects').fetchone()[0], 0)
            self.assertIsNone(meta(db, 'setup:project:brand_one'))
        self.assertFalse(self.put()['reused'])

    def test_changed_plan_and_drift_are_rejected(self):
        self.put(); self.plan['products'][0]['name'] = 'changed'
        with self.assertRaisesRegex(RuntimeFault, 'LOCAL_SETUP_CHANGED'): self.put({})
        self.plan = plan()
        with self.store.connect() as db: save(db, 'script:profile:brand_one', {'changed': True})
        with self.assertRaisesRegex(RuntimeFault, 'LOCAL_SETUP_CHANGED'): self.put({})

    def test_missing_or_replaced_master_key_does_not_rotate_existing_credentials(self):
        self.put(); self.key = Fernet.generate_key()
        with self.assertRaisesRegex(RuntimeFault, 'SECRET_DECRYPT_FAILED'): self.put({})

    def test_new_project_does_not_change_previous_project(self):
        self.put()
        with self.store.connect() as db: before = meta(db, 'local:setup:brand_one')
        self.plan['project'] = 'brand_two'; self.put()
        with self.store.connect() as db:
            self.assertEqual(before, meta(db, 'local:setup:brand_one'))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM projects').fetchone()[0], 2)

    def test_fake_ledger_and_existing_unowned_project_rejected(self):
        with self.store.connect() as db: save(db, 'local:fake-only:v1', {'enabled': True})
        with self.assertRaisesRegex(RuntimeFault, 'LOCAL_FAKE_PROFILE'): self.put()
        with self.store.connect() as db: db.execute('DELETE FROM meta WHERE key=?', ('local:fake-only:v1',))
        from video_factory.setup_project import DISABLED_CONFIGURATION
        self.store.put_project(self.admin, 'brand_one', DISABLED_CONFIGURATION)
        with self.assertRaisesRegex(RuntimeFault, 'EXISTING_PROJECT_NOT_OWNED'): self.put()

    def test_binding_failure_retains_config_and_retry_reuses_it(self):
        self.put()
        class WrongTenant(Client):
            def identity(self): return {'tenant_key': 'other', 'open_id': 'ou_other'}
        with self.assertRaisesRegex(RuntimeFault, 'FEISHU_TENANT_OR_ROLE_DENIED'):
            bind(self.store, self.admin, self.plan, 'synthetic-user', client_factory=WrongTenant)
        self.assertTrue(self.put({})['reused'])
        for _ in range(2):
            result = bind(self.store, self.admin, self.plan, 'synthetic-user', client_factory=Client)
            self.assertEqual(result['feishu_writes'], 0)
        self.plan['binding']['table_id'] = 'tblOther'
        with self.assertRaisesRegex(RuntimeFault, 'LOCAL_SETUP_CHANGED'):
            bind(self.store, self.admin, self.plan, 'synthetic-user', client_factory=Client)

    def test_local_assets_use_private_user_folder_and_hash_validation(self):
        assets = self.root / 'assets'; assets.mkdir(mode=0o700)
        image = assets / 'product.png'; image.write_bytes(b'synthetic-image')
        self.plan['video'] = {'model': 'MiniMax-H3', 'region': 'cn', 'assets': {'sku_one': {
            'assets_root': str(assets), 'specification': {'duration': 5, 'references': [
                {'path': 'product.png', 'sha256': hashlib.sha256(image.read_bytes()).hexdigest()}]}}}}
        self.secrets['video'] = 'synthetic-video-secret'; self.put()
        with self.store.connect() as db:
            self.assertEqual(meta(db, 'video:profile:brand_one')['assets']['sku_one']['assets_root'], str(assets))
        self.plan['project'] = 'brand_two'; image.write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeFault, 'H3_REFERENCE_CHANGED'): self.put()
        with self.store.connect() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM projects WHERE id=?', ('brand_two',)).fetchone())

    def test_unsupported_model_or_secret_in_plan_is_rejected(self):
        self.plan['script_model'] = 'unknown'
        with self.assertRaisesRegex(RuntimeFault, 'MODEL_UNSUPPORTED'): validate(self.plan)
        self.plan = plan(); self.plan['api_key'] = 'must-not-be-in-plan'
        with self.assertRaisesRegex(RuntimeFault, 'PLAN_FIELDS_INVALID'): validate(self.plan)

    def test_oauth_does_not_log_tokens_or_device_code(self):
        self.put(); output = []
        class OAuth:
            def __init__(self, app, secret): pass
            def start(self): return {'device_code': 'hidden-device', 'verification_uri': 'https://accounts.feishu.cn/fixture',
                                     'user_code': 'PUBLIC-CODE', 'expires_in': 240, 'interval': 5}
            def poll(self, code): return {'status': 'authorized', 'access_token': 'hidden-user'}
        token = authorize_feishu(self.store, self.plan, self.key, oauth_factory=OAuth,
                                 write=output.append, sleep=lambda _: None, clock=lambda: 0)
        self.assertEqual(token, 'hidden-user')
        self.assertNotIn('hidden', '\n'.join(output))
        self.assertIn('PUBLIC-CODE', '\n'.join(output))

    def test_installed_command_new_setup_resume_and_session_revocation(self):
        cfg = self.root / 'plan.json'; exclusive_write(cfg, canonical(self.plan).encode())
        files = {}
        for name, value in [('password', 'synthetic-password-long'), ('app-secret', self.secrets['app']), ('script-key', self.secrets['script'])]:
            path = self.root / (name + '.input'); exclusive_write(path, value.encode()); files[name] = path
        target = self.root / 'cli-state'
        args = ['local-setup', '--root', str(target), '--plan', str(cfg)]
        for name, path in files.items(): args += ['--' + name + '-file', str(path)]
        for reused in (False, True):
            out = io.StringIO()
            with redirect_stdout(out): result = main(args)
            self.assertEqual(result, 0, out.getvalue())
            report = json.loads(out.getvalue()); self.assertEqual(report['reused'], reused)
            self.assertFalse(report['paid_execution_enabled'])
            self.assertFalse(report['feishu_binding_verified'])
            for value in self.secrets.values(): self.assertNotIn(value, out.getvalue())
        store = RuntimeStore(target)
        with store.connect() as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 0)
        self.assertEqual((target / 'master.key').stat().st_mode & 0o777, 0o600)
