import copy
import json
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from video_factory.onboarding import new_session, apply_answers, SetupError, SessionStore
from video_factory.runtime_store import RuntimeStore, RuntimeFault
from video_factory.setup_project import import_project
from video_factory.setup_feishu import ConnectionSession, SetupFeishu, describe, interactive
from video_factory.feishu_bridge import FeishuBridge, meta, save, configuration

EXAMPLE = Path(__file__).resolve().parents[1] / 'examples/setup/answers.json'
ANSWERS = {'table_id': 'tblFixture', 'task': 'fldTask', 'sku_id': 'fldSkuId',
           'script': 'fldScript', 'source_revision': 'fldSource', 'submitters': ['ou_submitter']}


class SetupFeishuTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        answers = json.loads(EXAMPLE.read_text())
        answers.update({'project.base_mode': 'bind', 'project.base_target': 'bascnFixture'})
        self.setup = apply_answers(new_session(), answers, 0)[0]
        self.session = ConnectionSession(self.root / 'connection.json')
        self.session.start(self.setup)
        self.session.answer(ANSWERS, 0)
        self.draft = self.session.read()
        state = self.root / 'state'; state.mkdir(mode=0o700)
        self.store = RuntimeStore.install(state, 'example_test', 'synthetic-admin-password')
        self.admin = self.store.login('admin', 'synthetic-admin-password')['token']
        import_project(self.store, self.admin, self.setup)
        self.reads = 0
        self.on_fields = None
        self.field_suffix = ''
        owner = self

        class Client:
            def __init__(self, token): self.token = token
            def identity(self):
                owner.reads += 1
                if self.token == 'expired': raise RuntimeFault('FEISHU_READ_OR_USER_AUTH_FAILED')
                return {'tenant_key': 'different' if self.token == 'outsider' else 'example_tenant', 'open_id': self.token}
            def fields(self, *args):
                if owner.on_fields: owner.on_fields()
                return [{'field_id': value, 'field_name': key + owner.field_suffix, 'type': 1}
                        for key, value in ANSWERS.items() if key not in ('table_id', 'submitters')]
            def record(self, *args):
                return {'fields': {'task': 'task_one', 'sku_id': 'EXAMPLE-001', 'script': 'Synthetic script', 'source_revision': 'one'}}
        self.service = SetupFeishu(self.store, client_factory=Client)
        self.bridge = FeishuBridge(self.store, client_factory=Client)

    def tearDown(self): self.tmp.cleanup()

    def connect(self):
        plan = self.service.prepare(self.admin, self.draft, 'ou_submitter')
        return self.service.apply(self.admin, self.draft, 'ou_submitter', plan['plan_sha256'])

    def test_questions_resume_contract_and_revision_conflict(self):
        path = self.root / 'questions.json'
        wizard = ConnectionSession(path); wizard.start(self.setup)
        for key, value in ANSWERS.items():
            state = describe(wizard.read())
            self.assertEqual(state['next_question']['field'], key)
            expected = 'array' if key == 'submitters' else 'string'
            self.assertEqual(state['next_question']['input_schema']['type'], expected)
            wizard.answer({key: value}, state['revision'])
            wizard = ConnectionSession(path); wizard.start(self.setup)
        self.assertEqual(describe(wizard.read())['status'], 'connection_draft_ready')
        with self.assertRaisesRegex(SetupError, 'REVISION_CONFLICT'): wizard.answer({'task': 'fldOther'}, 0)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_interactive_quit_resume_and_array_conversion(self):
        wizard = ConnectionSession(self.root / 'tty.json'); wizard.start(self.setup)
        first = iter(['tblFixture', ':quit']); output = []
        interactive(wizard, read=lambda _: next(first), write=output.append)
        rest = iter(['fldTask', 'fldSkuId', 'fldScript', 'fldSource', 'ou_submitter,ou_other'])
        result = interactive(wizard, read=lambda _: next(rest), write=output.append)
        self.assertEqual(result['status'], 'connection_draft_ready')
        self.assertEqual(wizard.read()['answers']['submitters'], ['ou_submitter', 'ou_other'])

    def test_real_terminal_welcome_and_saved_resume(self):
        setup_path = self.root / 'setup.json'
        source = SessionStore(setup_path)
        with source.locked(): source._save(self.setup)
        path = self.root / 'real-tty.json'
        master, slave = pty.openpty()
        process = subprocess.Popen([sys.executable, '-m', 'video_factory.cli', 'setup-feishu', 'configure',
                                    '--setup-session', str(setup_path), '--session', str(path), '--interactive'],
                                   stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
        os.close(slave)
        received = b''
        def prompt():
            nonlocal received
            end = time.monotonic() + 10
            while '输入> '.encode() not in received:
                if time.monotonic() > end: self.fail('terminal prompt timeout')
                if select.select([master], [], [], .2)[0]: received += os.read(master, 65536)
        try:
            prompt()
            self.assertIn('欢迎连接飞书'.encode(), received)
            received = b''; os.write(master, b'tblFixture\n'); prompt()
            os.write(master, b':quit\n')
            self.assertEqual(process.wait(timeout=10), 0)
            result = describe(ConnectionSession(path).read())
            self.assertEqual(result['revision'], 1)
            self.assertEqual(result['next_question']['field'], 'task')
        finally:
            if process.poll() is None: process.kill(); process.wait()
            os.close(master)

    def test_invalid_raw_values_duplicate_fields_and_arrays_not_saved(self):
        for value in ({'task': 'sk-never-save-this-raw-secret'}, {'task': 'fldScript'},
                      {'submitters': 'ou_submitter'}, {'submitters': ['ou_submitter', 'ou_submitter']},
                      {'script_reviewer': 'user_not_an_app_open_id'}):
            with self.assertRaises((SetupError, RuntimeFault)): self.session.answer(value, 1)
            self.assertEqual(self.session.read(), self.draft)

    def test_app_scoped_reviewer_ids_can_be_corrected_before_binding(self):
        result = self.session.answer({'submitters': ['ou_verified'],
            'script_reviewer': 'ou_verified', 'video_reviewer': 'ou_verified'}, 1)
        self.assertEqual(result['status'], 'connection_draft_ready')
        corrected = self.session.read()
        prepared = self.service.prepare(self.admin, corrected, 'ou_verified')
        self.assertEqual(prepared['plan']['binding']['submitters'], ['ou_verified'])
        self.assertEqual(prepared['plan']['binding']['script_reviewers'], ['ou_verified'])
        self.assertEqual(prepared['plan']['binding']['video_reviewers'], ['ou_verified'])
        self.assertEqual(self.service.apply(self.admin, corrected, 'ou_verified',
                         prepared['plan_sha256'])['status'], 'connection_binding_saved')
        self.assertTrue(self.service.status(self.admin, corrected)['binding_matches_draft'])

    def test_operation_snapshot_requires_no_writable_lock_or_directory(self):
        self.session.lock_path.unlink()
        original_open = os.open
        def readonly(path, flags, *args, **kwargs):
            if flags & (os.O_RDWR | os.O_WRONLY | os.O_CREAT):
                raise OSError('read-only mount fixture')
            return original_open(path, flags, *args, **kwargs)
        with patch('video_factory.onboarding.os.open', side_effect=readonly):
            self.assertEqual(self.session.snapshot(), self.draft)
        self.assertFalse(self.session.lock_path.exists())

    def test_changed_setup_requires_new_connection_and_new_base_not_claimed_created(self):
        changed = copy.deepcopy(self.setup); changed['configuration']['project']['name'] = 'Changed'
        with self.assertRaisesRegex(SetupError, 'SOURCE_CHANGED'): self.session.start(changed)
        changed['configuration']['project']['base_mode'] = 'create'
        fresh = ConnectionSession(self.root / 'new.json'); fresh.start(changed)
        self.assertEqual(describe(fresh.snapshot())['next_question']['field'], 'workspace_kind')

    def test_plan_does_not_bind_and_status_does_not_claim_live_verification(self):
        prepared = self.service.prepare(self.admin, self.draft, 'ou_submitter')
        status = self.service.status(self.admin, self.draft)
        self.assertFalse(status['binding_matches_draft'])
        self.assertEqual(prepared['plan']['binding']['video_reviewers'], ['ou_example_video'])
        self.connect()
        self.assertTrue(self.service.status(self.admin, self.draft)['binding_matches_draft'])
        self.assertEqual(self.service.status(self.admin, self.draft)['remote_verification'], 'not_run_in_status')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0], 0)

    def test_wrong_tenant_expiry_and_non_admin_cannot_connect(self):
        for user in ('outsider', 'expired'):
            with self.assertRaises(RuntimeFault): self.service.prepare(self.admin, self.draft, user)
        before = self.reads
        with self.assertRaises(RuntimeFault): self.service.prepare('not-an-admin', self.draft, 'ou_submitter')
        self.assertEqual(before, self.reads)

    def test_forged_setup_or_other_project_rejected_before_external_reads(self):
        for group, key, value in [('project', 'id', 'other_project'), ('deployment', 'feishu_tenant', 'other_tenant'),
                                  ('project', 'base_target', 'bascnOther'), ('project', 'video_reviewer', 'feishu:ou_other')]:
            draft = copy.deepcopy(self.draft); draft['setup']['configuration'][group][key] = value
            with self.assertRaisesRegex(RuntimeFault, 'IMPORTED_PLAN'): self.service.prepare(self.admin, draft, 'ou_submitter')
        self.assertEqual(self.reads, 0)

    def test_remote_field_rename_actor_change_and_draft_change_invalidate_plan(self):
        plan = self.service.prepare(self.admin, self.draft, 'ou_submitter')['plan_sha256']
        self.field_suffix = '_changed'
        with self.assertRaisesRegex(RuntimeFault, 'PLAN_CHANGED'): self.service.apply(self.admin, self.draft, 'ou_submitter', plan)
        self.field_suffix = ''
        with self.assertRaisesRegex(RuntimeFault, 'PLAN_CHANGED'): self.service.apply(self.admin, self.draft, 'ou_another', plan)
        changed = copy.deepcopy(self.draft); changed['answers']['submitters'] = ['ou_another']
        with self.assertRaisesRegex(RuntimeFault, 'PLAN_CHANGED'): self.service.apply(self.admin, changed, 'ou_submitter', plan)

    def test_concurrent_binding_change_fenced(self):
        def change():
            with self.store.connect() as db: save(db, 'feishu:reconfirm:new_brand', {'required': True})
        self.on_fields = change
        with self.assertRaisesRegex(RuntimeFault, 'CONTEXT_CHANGED'): self.service.prepare(self.admin, self.draft, 'ou_submitter')
        with self.store.connect() as db: self.assertIsNone(meta(db, 'feishu:binding:new_brand'))

    def test_recovery_reconfirmation_requires_fresh_plan(self):
        self.connect()
        plan = self.service.prepare(self.admin, self.draft, 'ou_submitter')['plan_sha256']
        with self.store.connect() as db: self.store.invalidate_worker_approvals(db)
        self.assertTrue(self.service.status(self.admin, self.draft)['requires_reconfirmation'])
        with self.assertRaisesRegex(RuntimeFault, 'PLAN_CHANGED'): self.service.apply(self.admin, self.draft, 'ou_submitter', plan)
        self.connect()
        self.assertFalse(self.service.status(self.admin, self.draft)['requires_reconfirmation'])

    def test_script_and_video_reviewers_cannot_exchange_roles_or_replay(self):
        self.connect()
        plan = self.bridge.prepare_import('ou_submitter', 'new_brand', 'recFixture')
        self.bridge.import_task('ou_submitter', 'new_brand', 'recFixture', plan['plan_sha256'])
        with self.assertRaisesRegex(RuntimeFault, 'ROLE_DENIED'):
            self.bridge.prepare_review('ou_example_video', 'new_brand', 'task_one', 1, 'script', 'accept')
        plan = self.bridge.prepare_review('ou_example_script', 'new_brand', 'task_one', 1, 'script', 'accept')
        self.bridge.review('ou_example_script', 'new_brand', 'task_one', 1, 'script', 'accept', 'event_one', plan['plan_sha256'])
        self.store.claim(self.admin, 'new_brand', 'task_one', 1)
        self.store.attach_provider(self.admin, 'new_brand', 'task_one', 1, '62345678901234')
        self.store.record_artifact(self.admin, 'new_brand', 'task_one', 1, '62345678901234',
                                  {'sha256': 'a'*64, 'location': '/media/fixture.mp4', 'verification': 'full_decode_passed'})
        with self.assertRaisesRegex(RuntimeFault, 'ROLE_DENIED'):
            self.bridge.prepare_review('ou_example_script', 'new_brand', 'task_one', 1, 'video', 'accept')
        self.bridge.prepare_review('ou_example_video', 'new_brand', 'task_one', 1, 'video', 'accept')
        with self.store.connect() as db:
            binding = meta(db, 'feishu:binding:new_brand')
            binding.update(script_reviewers=['ou_replacement'], reviewers=['ou_replacement', 'ou_example_video'])
            save(db, 'feishu:binding:new_brand', binding)
        with self.assertRaisesRegex(RuntimeFault, 'ROLE_DENIED'):
            self.bridge.review('ou_example_script', 'new_brand', 'task_one', 1, 'script', 'accept', 'event_one', plan['plan_sha256'])

    def test_scoped_binding_cannot_have_partial_or_inconsistent_permissions(self):
        plan = self.service.prepare(self.admin, self.draft, 'ou_submitter')
        binding = plan['plan']['binding']; del binding['video_reviewers']
        with self.assertRaises(RuntimeFault): configuration(binding)
        binding['video_reviewers'] = ['ou_unlisted']
        with self.assertRaises(RuntimeFault): configuration(binding)
