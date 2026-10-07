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
from video_factory.setup_run import welcome, run, private_inputs
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
        self.events=[];self.output=[];self.connected=False
        self.stack=MagicMock();self.stack.root=self.args.root

    def tearDown(self):self.temp.cleanup()

    @contextlib.contextmanager
    def environment(self, inputs, secrets, secret_prompts=None):
        reads=iter(inputs);hidden=iter(secrets)
        def read_hidden(prompt):
            if secret_prompts is not None:secret_prompts.append(prompt)
            return next(hidden)
        with patch('video_factory.setup_run.admin_host'),patch('video_factory.setup_run.local_engine'),patch('video_factory.setup_deploy.local_machine',return_value={'hostname':'cloud-fixture','machine_id_sha256':'fixture'}),patch('video_factory.setup_run.install',return_value={'project':'new_brand'}) as install,patch('video_factory.setup_run.Stack',return_value=self.stack),patch('video_factory.setup_run.rpc',side_effect=self.rpc):
            yield lambda:welcome(self.args,read=lambda _:next(reads),hidden=read_hidden,write=self.output.append),install

    def rpc(self,stack,payload):
        self.events.append(payload)
        if payload['action']=='open':return {'token':'synthetic-admin-token','profile':None,'profile_sha256':'fixture'}
        if payload['action']=='configure':return {'profile':self.profile}
        if payload['action']=='status':return {'binding_matches_draft':self.connected,'requires_reconfirmation':False}
        return {'status':'wizard_session_revoked'}

    def test_declined_install_never_asks_secret_or_installs(self):
        with self.environment(['n'],[]) as (go,install):self.assertEqual(go()['status'],'installation_not_started');install.assert_not_called()
        self.assertEqual(self.events,[])

    def test_password_mismatch_never_installs(self):
        with self.environment(['y'],['synthetic-password','other-password']) as (go,install):
            with self.assertRaisesRegex(RuntimeFault,'PASSWORD_CONFIRMATION_MISMATCH'):go()
            install.assert_not_called()
        self.assertEqual(self.events,[])

    def test_image_fetch_resume_verifies_existing_password_once(self):
        self.args.root.mkdir()
        (self.args.root/'stack.json').write_text('{}')
        self.connected=True
        target={'declared_host':'cloud-fixture','local_machine':{'hostname':'cloud-fixture','machine_id_sha256':'fixture'},
                'root':str(self.args.root),'project':'new_brand','runtime_port':8787,'n8n_port':5678,'wheels':{}}
        prompts=[]
        with self.environment(['y','cli_fixture','y'],['synthetic-password','synthetic-app-secret'],prompts) as (go,install), \
             patch('video_factory.setup_run.execution_plan',return_value={'target':target,'execution_sha256':'fixture','sku_count':1}):
            self.assertEqual(go()['status'],'connection_ready')
            install.assert_called_once()
        self.assertEqual(prompts,['产品管理员密码: ','飞书应用 App Secret（隐藏输入）: '])

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

    def test_connected_setup_finishes_without_employee_browser(self):
        self.connected=True
        with self.environment(['y','cli_fixture','y'],
                              ['synthetic-password','synthetic-password','synthetic-app-secret']) as (go,install):
            self.assertEqual(go()['status'],'connection_ready')
            install.assert_called_once()
        self.assertEqual([v['action'] for v in self.events],['open','configure','status','close'])
        self.assertIn('项目 Base','\n'.join(self.output))

    def test_first_binding_uses_private_terminal_and_rechecks_base(self):
        @contextlib.contextmanager
        def inputs(*_): yield Path('/work/fixture')
        def connect(_):
            self.connected=True
            return 0
        with self.environment(['y','cli_fixture','y','y'],
                              ['synthetic-password','synthetic-password','synthetic-app-secret']) as (go,_), \
             patch('video_factory.setup_run.private_inputs', side_effect=inputs), \
             patch('video_factory.setup_run.run_terminal', side_effect=connect) as terminal, \
             patch('video_factory.setup_run.run_connect') as browser:
            self.assertEqual(go()['status'],'connection_ready')
            terminal.assert_called_once()
            browser.assert_not_called()

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
