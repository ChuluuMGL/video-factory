import copy
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
from video_factory.onboarding import new_session, apply_answers
from video_factory.runtime_store import RuntimeStore, RuntimeFault, fingerprint
from video_factory.setup_project import import_project
from video_factory.setup_admin import SetupAdmin
from video_factory.feishu_bridge import meta
from video_factory.review_cli import app_secret
from types import SimpleNamespace


class SetupAdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        (self.root/'state').mkdir(mode=0o700)
        self.store = RuntimeStore.install(self.root/'state', 'example_test', 'synthetic-admin-password')
        self.admin = self.store.login('admin','synthetic-admin-password')['token']
        answers = json.loads((Path(__file__).resolve().parents[1]/'examples/setup/answers.json').read_text())
        self.session = apply_answers(new_session(),answers,0)[0]
        import_project(self.store,self.admin,self.session)
        self.key = Fernet.generate_key(); self.helper = SetupAdmin(self.store,self.key)

    def tearDown(self): self.temp.cleanup()

    def test_login_is_bounded_and_close_revokes_only_its_own_session(self):
        opened=self.helper.open(self.session,'synthetic-admin-password')
        self.assertLessEqual(opened['expires_at'],time.time()+900)
        self.assertGreater(opened['expires_at'],time.time()+895)
        self.assertIsNone(opened['profile'])
        self.helper.close(opened['token']); self.helper.close(opened['token'])
        with self.store.connect() as db:
            self.assertEqual(self.store.authorize(db,self.admin,'admin'),'admin')
            with self.assertRaisesRegex(RuntimeFault,'AUTH'):self.store.authorize(db,opened['token'],'admin')

    def test_encrypted_profile_reuse_rotation_and_no_secret_in_metadata(self):
        value=self.helper.configure(self.admin,self.session,'cli_fixture','synthetic-secret',fingerprint(None))
        profile=value['profile']; alias=profile['credential_ref'][7:]
        self.assertEqual(self.store.resolve_secret(alias,self.key),'synthetic-secret')
        with self.store.connect() as db:
            self.assertNotIn('synthetic-secret',str([tuple(row) for row in db.execute('SELECT * FROM meta').fetchall()]))
            self.assertNotIn(b'synthetic-secret',db.execute('SELECT ciphertext FROM vault WHERE alias=?',(alias,)).fetchone()[0])
        opened=self.helper.open(self.session,'synthetic-admin-password')
        self.assertEqual(opened['profile'],profile)
        with self.assertRaisesRegex(RuntimeFault,'PROFILE_CHANGED'):
            self.helper.configure(self.admin,self.session,'cli_other','another-secret',fingerprint(None))
        changed=self.helper.configure(self.admin,self.session,'cli_other','another-secret',value['profile_sha256'])
        self.assertNotEqual(changed['profile']['credential_ref'],profile['credential_ref'])
        self.assertEqual(self.store.resolve_secret(alias,self.key),'synthetic-secret')
        self.helper.close(opened['token'])

    def test_wrong_admin_role_import_or_payload_fail_closed(self):
        with self.assertRaisesRegex(RuntimeFault,'AUTH'):
            self.helper.open(self.session,'incorrect-password')
        self.store.set_user(self.admin,'employee','synthetic-employee-password','new_brand')
        user=self.store.login('employee','synthetic-employee-password')['token']
        with self.assertRaisesRegex(RuntimeFault,'DENIED'):
            self.helper.configure(user,self.session,'cli_fixture','secret',fingerprint(None))
        changed=copy.deepcopy(self.session);changed['configuration']['project']['products'][0]['name']='changed'
        with self.assertRaisesRegex(RuntimeFault,'RECONCILIATION'):
            self.helper.open(changed,'synthetic-admin-password')
        for value in ({'action':'bad'},{'action':'open','session':self.session,'password':'secret','extra':True}):
            with self.assertRaisesRegex(RuntimeFault,'FIELDS_INVALID'):self.helper.dispatch(value)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM vault').fetchone()[0],0)

    def test_revoked_admin_during_vault_write_does_not_save_profile_or_orphan(self):
        original=self.store.put_secret
        def interrupted(*args):
            value=original(*args)
            with self.store.connect() as db:db.execute('DELETE FROM sessions')
            return value
        with patch.object(self.store,'put_secret',interrupted):
            with self.assertRaisesRegex(RuntimeFault,'AUTH'):
                self.helper.configure(self.admin,self.session,'cli_fixture','synthetic-secret',fingerprint(None))
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM vault').fetchone()[0],0)
            self.assertIsNone(meta(db,'setup:feishu-app:new_brand'))

    def test_window_resolves_vault_reference_with_private_key_only(self):
        profile=self.helper.configure(self.admin,self.session,'cli_fixture','synthetic-secret',fingerprint(None))['profile']
        key=self.root/'key';key.write_bytes(self.key);key.chmod(0o600)
        args=SimpleNamespace(root=self.root/'state',app_secret_file=None,app_secret_ref=profile['credential_ref'],master_key_file=key)
        self.assertEqual(app_secret(args),'synthetic-secret')
        args.master_key_file=None
        with self.assertRaisesRegex(RuntimeFault,'MASTER_KEY_REQUIRED'):app_secret(args)
        args.app_secret_ref='env:LEAK'
        with self.assertRaisesRegex(RuntimeFault,'REFERENCE_INVALID'):app_secret(args)
