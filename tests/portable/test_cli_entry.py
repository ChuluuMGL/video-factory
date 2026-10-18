"""Exercise the actual __main__ entrypoint, not only imported main()."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[2]/'skills/video-factory-setup/scripts/portable_n8n.py'

class EntryPoint(unittest.TestCase):
    def test_imported_fault_is_a_safe_cli_result(self):
        with tempfile.TemporaryDirectory() as root:
            harness='''import getpass,io,json,runpy,sys,urllib.request
class PrivateInput:
 def isatty(self):return True
sys.stdin=PrivateInput()
getpass.getpass=lambda prompt:'synthetic-private-key-never-log'
class Reply(io.BytesIO):
 def __enter__(self):return self
 def __exit__(self,*args):self.close()
class FakeOpener:
 def open(self,*args,**kwargs):return Reply(b'{"data": []}')
urllib.request.build_opener=lambda *args:FakeOpener()
script,root=sys.argv[1:]
sys.path.insert(0,str(__import__('pathlib').Path(script).parent))
sys.argv=[script,'--root',root,'upgrade-plan','--url','http://127.0.0.1:5678','--project','fixture']
runpy.run_path(script,run_name='__main__')
'''
            result=subprocess.run([sys.executable,'-c',harness,str(SCRIPT),root],capture_output=True,text=True,encoding='utf-8',timeout=30)
            self.assertEqual(result.returncode,2,result.stderr)
            self.assertEqual(json.loads(result.stdout),{'error':'UPGRADE_PROJECT_SCOPE_REQUIRED'})
            self.assertNotIn('Traceback',result.stderr)
            self.assertNotIn('synthetic-private-key-never-log',result.stdout+result.stderr)
    def test_private_terminal_requirement_remains(self):
        result=subprocess.run([sys.executable,str(SCRIPT),'check','--url','http://127.0.0.1:5678'],
            input='',capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(result.returncode,2)
        self.assertEqual(json.loads(result.stdout),{'error':'PRIVATE_TERMINAL_REQUIRED'})
        self.assertNotIn('Traceback',result.stderr)

if __name__=='__main__':unittest.main()
