import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from video_factory.onboarding import SessionStore
from video_factory.setup_feishu import ConnectionSession
from video_factory.setup_run import welcome, run, private_inputs, install as install_setup
from video_factory.runtime_store import RuntimeFault


class SetupRunTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        answers=json.loads((Path(__file__).resolve().parents[1]/'examples/setup/answers.json').read_text())
        answers.update({'project.base_mode':'bind','project.base_target':'bascnFixture'})
        self.store=SessionStore(self.root/'setup.json');self.store.start();self.store.answer(answers,0)
        connection=ConnectionSession(self.root/'setup.json.feishu.json');connection.start(self.store.read())
        connection.answer({'table_id':'tblFixture','task':'fldTask','sku_id':'fldSkuId','script':'fldScript','source_revision':'fldSource','submitters':['ou_employee']},0)
        wheels=self.root/'wheels';wheels.mkdir();(wheels/'video_factory_core-fixture.whl').write_bytes(b'fixture')
        self.args=SimpleNamespace(session=self.store.path,root=self.root/'stack',wheelhouse=wheels,from_session=None,
            runtime_port=None,n8n_port=None,connection_port=8791,review_port=8790,seconds=360)
        self.profile={'app_id':'cli_fixture','credential_ref':'secret:fixture'}
        self.events=[];self.output=[]
        self.stack=MagicMock();self.stack.root=self.args.root

    def tearDown(self):self.temp.cleanup()

    @contextlib.contextmanager
    def environment(self, inputs, secrets):
        reads=iter(inputs);hidden=iter(secrets)
        with patch('video_factory.setup_run.admin_host'),patch('video_factory.setup_run.engine_ready'),patch('video_factory.setup_deploy.local_machine',return_value={'hostname':'cloud-fixture','machine_id_sha256':'fixture'}),patch('video_factory.setup_run.install',return_value={'project':'new_brand'}) as install,patch('video_factory.setup_run.Stack',return_value=self.stack),patch('video_factory.setup_run.rpc',side_effect=self.rpc):
            yield lambda:welcome(self.args,read=lambda _:next(reads),hidden=lambda _:next(hidden),write=self.output.append),install

    def rpc(self,stack,payload):
        self.events.append(payload)
        if payload['action']=='open':return {'token':'synthetic-admin-token','profile':None,'profile_sha256':'fixture'}
        if payload['action']=='configure':return {'profile':self.profile}
        if payload['action']=='status':return {'binding_matches_draft':False,'requires_reconfirmation':False}
        return {'status':'wizard_session_revoked'}

    def test_declined_install_never_asks_secret_or_installs(self):
        with self.environment(['n'],[]) as (go,install):self.assertEqual(go()['status'],'installation_not_started');install.assert_not_called()
        self.assertEqual(self.events,[])

    def test_unavailable_engine_never_prompts_for_password_or_installs(self):
        # Check both an initially stopped daemon and one stopped during review.
        for probes in ([RuntimeFault('DOCKER_ENGINE_UNAVAILABLE_CHECK_SERVICE')],
                       [None, RuntimeFault('DOCKER_ENGINE_UNAVAILABLE_CHECK_SERVICE')]):
            with self.subTest(probes=len(probes)),self.environment(['y'],[]) as (go,install):
                before=self.store.path.read_bytes()
                with patch('video_factory.setup_run.engine_ready',side_effect=probes):
                    with self.assertRaisesRegex(RuntimeFault,'DOCKER_ENGINE_UNAVAILABLE_CHECK_SERVICE'):go()
                install.assert_not_called()
                self.assertEqual(self.events,[])
                self.assertEqual(self.store.path.read_bytes(),before)

    def test_engine_lost_during_password_entry_never_authenticates_or_changes_stack(self):
        with patch('video_factory.setup_run.engine_ready',side_effect=RuntimeFault('DOCKER_ENGINE_UNAVAILABLE_CHECK_SERVICE')),patch('video_factory.setup_run.Stack') as stack,patch('video_factory.setup_run.apply_setup') as apply,patch('video_factory.setup_run.project_operation') as project:
            with self.assertRaisesRegex(RuntimeFault,'DOCKER_ENGINE_UNAVAILABLE_CHECK_SERVICE'):
                install_setup(self.args,self.store,{}, {},'synthetic-password')
            stack.assert_not_called();apply.assert_not_called();project.assert_not_called()

    def test_password_mismatch_never_installs(self):
        with self.environment(['y'],['synthetic-password','other-password']) as (go,install):
            with self.assertRaisesRegex(RuntimeFault,'PASSWORD_CONFIRMATION_MISMATCH'):go()
            install.assert_not_called()
        self.assertEqual(self.events,[])

    def test_invalid_length_reprompts_without_authentication_or_secret_echo(self):
        with self.environment(['y','cli_fixture','n'],['short-input','synthetic-password','synthetic-password','synthetic-app-secret']) as (go,install):
            self.assertEqual(go()['status'],'installed_credentials_pending')
            install.assert_called_once()
        self.assertNotIn('short-input','\n'.join(self.output))
        self.assertTrue(any('尚未提交认证' in line for line in self.output))
        self.assertEqual([v['action'] for v in self.events],['open','close'])

    def test_save_credential_and_decline_window_revokes_token_and_hides_secrets(self):
        with self.environment(['y','cli_fixture','y','n'],['synthetic-password','synthetic-password','synthetic-app-secret']) as (go,install):
            self.assertEqual(go()['status'],'installed_credentials_saved');install.assert_called_once()
        self.assertEqual([v['action'] for v in self.events],['open','configure','status','close'])
        for value in ('synthetic-password','synthetic-app-secret','synthetic-admin-token'):
            self.assertNotIn(value,'\n'.join(self.output));self.assertNotIn(value,self.store.path.read_text())

    def test_interrupt_after_login_revokes_token(self):
        with self.environment(['y',':quit'],['synthetic-password','synthetic-password']) as (go,_):
            self.assertEqual(go()['status'],'installed_credentials_pending')
        self.assertEqual(self.events[-1]['action'],'close')

    def test_declined_or_failed_secret_save_still_revokes_session(self):
        with self.environment(['y','cli_fixture','n'],['synthetic-password','synthetic-password','synthetic-secret']) as (go,_):
            self.assertEqual(go()['status'],'installed_credentials_pending')
        self.assertEqual([v['action'] for v in self.events],['open','close'])

    def test_new_base_continues_to_test_workspace_questions_without_ids(self):
        # A new immutable Setup gets its own connection session. It must not be
        # forced back into bind mode by the unified entry point.
        answers = json.loads((Path(__file__).resolve().parents[1]/'examples/setup/answers.json').read_text())
        answers.update({'project.base_mode': 'create', 'project.base_target': '安装演练'})
        self.store = SessionStore(self.root/'new-setup.json'); self.store.start(); self.store.answer(answers, 0)
        self.args.session = self.store.path
        with self.environment(['y','test','','ou_employee','cli_fixture','y','n'],
                ['synthetic-password','synthetic-password','synthetic-app-secret']) as (go, install):
            self.assertEqual(go()['status'], 'installed_credentials_saved')
            install.assert_called_once()
        draft = ConnectionSession(self.root/'new-setup.json.feishu.json').snapshot()
        self.assertEqual(draft['setup']['configuration']['project']['base_mode'], 'create')
        self.assertEqual(draft['answers'], {'workspace_kind':'test','folder_token':'','submitters':['ou_employee']})

    def test_unsafe_workspace_and_no_tty_are_rejected(self):
        with self.assertRaises((RuntimeFault,FileNotFoundError)):
            with private_inputs(self.stack,{},'token'):pass
        with patch('sys.stdin.isatty',return_value=False),contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run(self.args),2)
        self.assertIn('REQUIRES_PRIVATE_TTY',output.getvalue())

    def test_browser_failure_reports_receipt_before_closing_listener(self):
        self.args.browser_input=True;self.args.input_port=8792
        with patch('video_factory.setup_browser.BrowserInput') as bridge,patch('video_factory.setup_run.welcome',side_effect=RuntimeFault('AUTH_FAILED')),contextlib.redirect_stdout(io.StringIO()):
            ui=bridge.return_value;ui.url='http://127.0.0.1:8792/#synthetic'
            self.assertEqual(run(self.args),2)
        self.assertEqual([call[0] for call in ui.method_calls],['finish','close'])
        ui.finish.assert_called_once_with({'error':'AUTH_FAILED','resume_same_command':True,'business_ready':False})
