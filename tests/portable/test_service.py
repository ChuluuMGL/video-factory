import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'skills/video-factory-setup/scripts'))
from portable_n8n import Fault, ask_choice, welcome
from portable_service import Backend, system_quote, install, control, location


class Service(unittest.TestCase):
    def test_commands_preserve_spaces_unicode_and_do_not_include_credentials(self):
        with tempfile.TemporaryDirectory(prefix='vf 空格 ') as d:
            command=[str(Path(d)/'python.exe'),str(Path(d)/'runner.py'),'--root',d]
            with patch('os.getuid',return_value=501,create=True):
                b=Backend(d,command,'Darwin')
                value=plistlib.loads(b.definition())
                self.assertEqual(value['ProgramArguments'],command)
                self.assertEqual(value['KeepAlive'],{'SuccessfulExit':False})
            b=Backend(d,command,'Windows')
            value=json.loads(b.definition())
            self.assertEqual(value['arguments'],subprocess.list2cmdline(command[1:]))
            self.assertNotIn('Secret',b.definition().decode())
            self.assertEqual(system_quote('/path with $ and % "'),'"/path with $$ and %% \\""')
            with self.assertRaises(Fault):system_quote('/path\nInjected=1')

    def test_foreign_definition_and_symlink_are_not_removed(self):
        with tempfile.TemporaryDirectory() as d:
            b=Backend(d,[sys.executable,'fixture.py'],'Windows')
            b.path.write_text('unrelated')
            with self.assertRaisesRegex(Fault,'CHANGED'):b.control('uninstall')
            self.assertEqual(b.path.read_text(),'unrelated')
            if os.name!='nt':
                b.path.unlink();target=Path(d)/'target';target.write_text('keep');b.path.symlink_to(target)
                with self.assertRaisesRegex(Fault,'SYMLINK'):b.install()
                self.assertEqual(target.read_text(),'keep')

    def test_unknown_installation_not_modified(self):
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'unknown'
            with self.assertRaisesRegex(Fault,'EXISTING_INSTALLATION'):install(target,'n8n')
            self.assertFalse(target.exists())

    def test_uninstall_preserves_data_and_marks_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);directory=location(root,'n8n');directory.mkdir()
            (root/'data').mkdir();(root/'data/keep').write_text('keep')
            (directory/'service.json').write_text(json.dumps({'root':str(root.resolve()),'component':'n8n','command':['fixture'],'installed':True}))
            with patch('portable_service.Backend.control',return_value={'data_preserved':True}) as native:
                self.assertTrue(control(root,'n8n','uninstall')['data_preserved'])
                native.assert_called_once_with('uninstall')
            self.assertEqual((root/'data/keep').read_text(),'keep')
            self.assertFalse(json.loads((directory/'service.json').read_text())['installed'])

    def test_invalid_choice_can_retry_or_pause(self):
        with patch('builtins.input',side_effect=['invalid','2']):self.assertEqual(ask_choice('?',('1','2')),'2')
        with patch('builtins.input',return_value=':quit'):
            with self.assertRaisesRegex(Fault,'PAUSED'):ask_choice('?',('1','2'))

    def test_existing_n8n_welcome_never_installs_service_and_writes_handoff(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'bindings').mkdir()
            answers=['1','http://127.0.0.1:5678','fixture','baseFixture','tableFixture','cli_fixture','n','n','n']
            with patch('builtins.input',side_effect=answers),patch('portable_n8n.secret',return_value='synthetic'),\
                patch('portable_n8n.API') as api,patch('portable_n8n.project_credentials',return_value={'feishu':{}}),\
                patch('portable_n8n.verify_setup',return_value={'ok':True}),patch('portable_service.install') as background:
                result=welcome(root)
                self.assertFalse(result['production_ready']);background.assert_not_called()
                api.assert_called_once_with('http://127.0.0.1:5678','synthetic')
            handoff=json.loads((root/'bindings/handoff-fixture.json').read_text())
            self.assertFalse(handoff['background']['managed'])
            self.assertNotIn('synthetic',json.dumps(handoff))


if __name__=='__main__':unittest.main()
