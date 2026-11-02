"""Synthetic welcome/Agent handoff acceptance; run only in cloud CI."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[2] / 'skills/video-factory-setup/scripts'
sys.path.insert(0, str(SCRIPTS))
from portable_n8n import Fault, private_write, welcome, install_local
from portable_session import WelcomeSession, inspect_session


class SessionTests(unittest.TestCase):
    def test_new_install_accepts_only_its_verified_welcome_checkpoint(self):
        for mode,route in [('node','2'),('docker','3')]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as d:
                root=Path(d); session=WelcomeSession(root)
                session.remember('route',route);session.remember('port',15678)
                # Stopping at the socket gate proves no dependency install occurred.
                with patch('portable_n8n.socket.socket') as socket:
                    socket.return_value.__enter__.return_value.bind.side_effect=OSError()
                    with self.assertRaisesRegex(Fault,'PORT_IN_USE'):
                        install_local(root,mode,15678,welcome_session=session)
                self.assertTrue((root/'install-plan.json').exists())
                self.assertTrue(session.path.exists())

    def test_new_install_preserves_foreign_files_and_requires_explicit_session(self):
        for extra,provided in [(False,False),(True,True)]:
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as d:
                root=Path(d);session=WelcomeSession(root)
                session.remember('route','2');session.remember('port',15678)
                if extra:(root/'existing-business.txt').write_text('preserve',encoding='utf-8')
                with patch('portable_n8n.socket.socket') as socket:
                    with self.assertRaisesRegex(Fault,'INSTALL_DIRECTORY_NOT_EMPTY'):
                        install_local(root,'node',15678,welcome_session=session if provided else None)
                    socket.assert_not_called()
                self.assertFalse((root/'install-plan.json').exists())
                self.assertTrue(session.path.exists())
                if extra:self.assertEqual((root/'existing-business.txt').read_text(),'preserve')

    def test_new_install_rejects_checkpoint_for_other_mode_or_port(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);session=WelcomeSession(root)
            session.remember('route','3');session.remember('port',15678)
            with self.assertRaisesRegex(Fault,'WELCOME_INSTALL_SCOPE_CHANGED'):
                install_local(root,'node',15678,welcome_session=session)
            self.assertFalse((root/'install-plan.json').exists())

    def test_resume_pins_code_before_input_or_network(self):
        with tempfile.TemporaryDirectory() as d:
            s = WelcomeSession(d); s.remember('route','1'); s.finish()
            with patch('portable_session.source_digest', return_value='changed'), patch('builtins.input') as ask:
                with self.assertRaisesRegex(Fault, 'SOURCE_CHANGED'): welcome(d,resume=True)
                ask.assert_not_called()
            self.assertEqual(json.loads(s.path.read_text(encoding='utf-8'))['attempts'],1)

    def test_original_directory_and_explicit_resume_required(self):
        with tempfile.TemporaryDirectory() as d:
            s = WelcomeSession(d)
            with self.assertRaisesRegex(Fault,'USE_RESUME'): WelcomeSession(d)
            s.data['root'] = '/different'; s.save()
            with self.assertRaisesRegex(Fault,'SCOPE_CHANGED'): WelcomeSession(d,resume=True)

    def test_missing_resume_never_starts_install(self):
        with tempfile.TemporaryDirectory() as d, patch('portable_n8n.install_local') as install:
            with self.assertRaisesRegex(Fault,'CHECKPOINT_MISSING'): welcome(d,resume=True)
            install.assert_not_called()

    def test_interrupt_after_identity_resumes_without_reasking_identity(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            answers=['1','http://127.0.0.1:5678','fixture','baseFixture','tableFixture','cli_fixture']
            with patch('builtins.input',side_effect=answers), patch('portable_n8n.secret',return_value='NEVER_SAVE_THIS'), \
                 patch('portable_n8n.API'), patch('portable_n8n.project_credentials',side_effect=Fault('TEST_INTERRUPTED')):
                with self.assertRaisesRegex(Fault,'TEST_INTERRUPTED'): welcome(root)
            first=inspect_session(root)
            self.assertEqual(first['phase'],'feishu')
            self.assertEqual(first['status'],'paused_or_failed')
            with patch('builtins.input',side_effect=['n','n','n','n']) as ask, \
                 patch('portable_n8n.secret',return_value='NEVER_SAVE_THIS'), patch('portable_n8n.API'), \
                 patch('portable_n8n.project_credentials',return_value={'feishu':{}}), \
                 patch('portable_n8n.verify_setup',return_value={'provider_requests':0}), \
                 patch('portable_n8n.install_local') as install:
                result=welcome(root,resume=True)
                install.assert_not_called()
                self.assertEqual(ask.call_count,4)
            self.assertEqual(result['setup_session']['attempts'],2)
            self.assertFalse(result['production_ready'])
            for p in root.rglob('*.json'):self.assertNotIn('NEVER_SAVE_THIS',p.read_text(encoding='utf-8'))
            handoff=json.loads((root/'bindings/handoff-fixture.json').read_text(encoding='utf-8'))
            self.assertEqual(handoff['installation']['task_identity_contract']['core_task_mapping'],'not_configured')

    def test_pending_creation_is_reported_without_retry_or_secret_output(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);s=WelcomeSession(root);s.remember('project','fixture');s.remember('origin','http://127.0.0.1:5678')
            bindings=root/'bindings';bindings.mkdir()
            private_write(bindings/'credentials-fixture.json',{'pending':{'role':'feishu','raw_secret':'DO_NOT_ECHO'}})
            private_write(bindings/'binding-fixture-script.json',{'origin':s.data['origin'],'workflow':'flowFixture','verified_disabled':True,'pending':{'error':'DO_NOT_ECHO'}})
            value=inspect_session(root)
            self.assertEqual(value['next'],'reconcile_unknown_resources')
            self.assertEqual(len(value['pending_resource_receipts']),2)
            self.assertNotIn('DO_NOT_ECHO',json.dumps(value))
            self.assertEqual(value['flows'][0]['live_status'],'not_checked')
            self.assertEqual(value['network_requests'],0)

    def test_unknown_temporary_probe_blocks_resume_before_creating_another(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);s=WelcomeSession(root);s.remember('route','1');s.remember('project','fixture');s.remember('origin','http://127.0.0.1:5678')
            bindings=root/'bindings';bindings.mkdir()
            private_write(bindings/'project-fixture.json',{'origin':s.data['origin'],'base':'baseFixture','table':'tableFixture','app':'cli_fixture'})
            private_write(bindings/'binding-fixture-probe-aabb1122.json',{'origin':s.data['origin'],'pending':{'kind':'workflow'}})
            with patch('portable_n8n.secret',return_value='synthetic'),patch('portable_n8n.API'), \
                 patch('portable_n8n.project_credentials') as credentials,patch('portable_n8n.verify_setup') as verify:
                with self.assertRaisesRegex(Fault,'PENDING_RESOURCE'):welcome(root,resume=True)
                credentials.assert_not_called();verify.assert_not_called()

    def test_handoff_cli_needs_no_key_and_does_not_change_checkpoint(self):
        with tempfile.TemporaryDirectory() as d:
            s=WelcomeSession(d);s.remember('project','fixture');s.finish()
            before=s.path.read_bytes()
            process=subprocess.run([sys.executable,str(SCRIPTS/'portable_n8n.py'),'--root',d,'handoff-check'],
                                   input='',capture_output=True,text=True,encoding='utf-8',timeout=20)
            self.assertEqual(process.returncode,0,process.stdout)
            value=json.loads(process.stdout)
            self.assertEqual(value['project'],'fixture')
            self.assertEqual(value['provider_requests'],0)
            self.assertEqual(s.path.read_bytes(),before)

    def test_new_project_preserves_history_and_reuses_instance(self):
        with tempfile.TemporaryDirectory() as d:
            s=WelcomeSession(d);s.remember('route','1');s.remember('origin','https://n8n.example.invalid');s.remember('project','one');s.finish(True)
            n=WelcomeSession(d,new_project=True)
            self.assertEqual(n.data['origin'],'https://n8n.example.invalid')
            self.assertNotIn('project',n.data)
            self.assertEqual(len(list(n.directory.glob('history-*.json'))),1)
            with self.assertRaisesRegex(Fault,'INCOMPLETE'):WelcomeSession(d,new_project=True)

    def test_scope_cannot_change_and_credential_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            s=WelcomeSession(d);s.remember('project','one')
            with self.assertRaisesRegex(Fault,'SCOPE_CHANGED'):s.remember('project','two')
            with self.assertRaisesRegex(Fault,'NOT_ALLOWED'):s.remember('api_key','DO_NOT_SAVE')

    def test_unknown_status_and_source_drift_do_not_look_ready(self):
        with tempfile.TemporaryDirectory() as d:
            s=WelcomeSession(d);s.data['status']='DO_NOT_ECHO';s.save()
            with patch('portable_session.source_digest',return_value='different'):
                value=inspect_session(d)
            self.assertEqual(value['status'],'unknown')
            self.assertEqual(value['next'],'use_original_verified_skill')
            self.assertFalse(value['production_ready'])

    def test_elapsed_time_includes_interaction_not_offline_wait(self):
        with tempfile.TemporaryDirectory() as d:
            with patch('portable_session.time.monotonic',side_effect=[10,12,100,105]):
                s=WelcomeSession(d);s.finish()
                s=WelcomeSession(d,resume=True);s.finish(True)
            self.assertEqual(s.data['interactive_seconds'],7)


if __name__=='__main__':unittest.main()
