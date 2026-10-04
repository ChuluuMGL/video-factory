import hashlib
import io
import os
from pathlib import Path
import tarfile
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

import test_stack as fixtures
from video_factory import stack as module
from video_factory.stack import Stack, write_json
from video_factory.runtime_store import RuntimeFault
from video_factory.backup_stream import MAGIC, decrypted_archive


@unittest.skipUnless(os.getuid()==0,'Cloud root fixture')
class StackLifecycleTests(unittest.TestCase):
    setUpRoot=fixtures.StackTests.setUp
    tearDown=fixtures.StackTests.tearDown
    prepare=fixtures.StackTests.prepare

    def setUp(self):
        self.setUpRoot();self.stack=self.prepare();self.key=Fernet.generate_key()
        write_json(self.root/'initialized.json',{'fixture':True})
        self.output=self.parent/'checkpoint.vfb'

    def stopped(self):
        return {'components':{'postgres':{'state':'exited','exit_code':0}}}

    def backup(self):
        with patch.object(self.stack,'compose'),patch.object(self.stack,'status',return_value=self.stopped()),patch('video_factory.workspace.stop_all'):
            return self.stack.backup(self.output,self.key)

    def test_oversize_and_disk_shortage_refused_before_service_stop(self):
        with patch.object(module,'MAX_UNPACKED',1),patch.object(self.stack,'compose') as command,patch('video_factory.workspace.stop_all') as stop:
            with self.assertRaisesRegex(RuntimeFault,'TOO_LARGE'):self.stack.backup(self.output,self.key)
            stop.assert_not_called();command.assert_not_called()
        with patch('video_factory.backup_stream.shutil.disk_usage') as disk,patch.object(self.stack,'compose') as command,patch('video_factory.workspace.stop_all') as stop:
            disk.return_value.free=0
            with self.assertRaisesRegex(RuntimeFault,'INSUFFICIENT_DISK'):self.stack.backup(self.output,self.key)
            stop.assert_not_called();command.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_archive_larger_than_old_limits_roundtrips_without_whole_file_reads(self):
        media=self.root/'data/media';media.mkdir(exist_ok=True)
        random_file=media/'large.bin';sparse=media/'sparse.bin';expected=hashlib.sha256()
        with random_file.open('wb') as stream:
            for _ in range(257):
                part=os.urandom(1024*1024);stream.write(part);expected.update(part)
        with sparse.open('wb') as stream:stream.truncate(1024**3+1)
        original=Path.read_bytes
        def bounded(path):
            if path==self.output or path==random_file or path==sparse:
                raise AssertionError('WHOLE_ARCHIVE_OR_MEDIA_READ')
            return original(path)
        recovered=self.parent/'recovered';recovered.mkdir(mode=0o700)
        with patch.object(Path,'read_bytes',bounded):
            receipt=self.backup()
            self.assertGreater(self.output.stat().st_size,256*1024**2)
            restored=Stack.restore(self.output,recovered,self.key)
        actual=hashlib.sha256()
        with (recovered/'data/media/large.bin').open('rb') as stream:
            while part:=stream.read(1024*1024):actual.update(part)
        self.assertEqual(actual.hexdigest(),expected.hexdigest())
        self.assertEqual((recovered/'data/media/sparse.bin').stat().st_size,1024**3+1)
        self.assertNotEqual(restored.config['instance'],self.stack.config['instance'])
        self.assertEqual(receipt['format'],'v2-streaming-aes256gcm')
        self.assertEqual(self.output.stat().st_mode & 0o777,0o600)

    def test_tamper_truncation_wrong_key_and_append_extract_nothing(self):
        self.backup();good=self.output.read_bytes()
        for content,key in [(good[:-1],self.key),(good+b'x',self.key),(good,self.key[::-1]),(good[:len(MAGIC)+15]+bytes([good[len(MAGIC)+15]^1])+good[len(MAGIC)+16:],self.key)]:
            # Use a syntactically valid unrelated key for authentication failure.
            if key!=self.key:key=Fernet.generate_key()
            self.output.write_bytes(content)
            target=self.parent/'target';target.mkdir(mode=0o700,exist_ok=True)
            with self.assertRaisesRegex(RuntimeFault,'AUTHENTICATION'):Stack.restore(self.output,target,key)
            self.assertEqual(list(target.iterdir()),[])

    def test_legacy_backup_still_restores(self):
        self.backup()
        with decrypted_archive(self.output,self.key,self.parent,module.MAX_ARCHIVE) as raw:
            self.output.write_bytes(Fernet(self.key).encrypt(raw.read()))
        target=self.parent/'legacy';target.mkdir(mode=0o700)
        restored=Stack.restore(self.output,target,self.key)
        self.assertEqual(restored.config['wheels'],self.stack.config['wheels'])

    def test_failed_archive_write_never_publishes_checkpoint(self):
        with patch('video_factory.backup_stream.Writer.write',side_effect=OSError('fixture disk error')):
            with self.assertRaises(OSError):self.backup()
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.parent.glob('.vf-backup-*')))

    def test_upgrade_failure_restores_prior_entries_and_reports_resume_failure(self):
        selected=[{'project':'fixture_project'}]
        for failure in (False,True):
            target=self.parent/('candidate-'+str(failure));target.mkdir(mode=0o700)
            with patch.object(self.stack,'status',return_value={'infrastructure_ready':True}),patch.object(self.stack,'_backup_unlocked'),patch.object(Stack,'restore',side_effect=RuntimeFault('FIXTURE_RESTORE_FAILURE')),patch.object(self.stack,'_up_unlocked',return_value={'infrastructure_ready':True}),patch('video_factory.workspace.running_entries',return_value=selected),patch('video_factory.workspace.resume_entries',side_effect=RuntimeFault('FIXTURE_RESUME_FAILURE') if failure else None,return_value=['fixture_project']) as resume:
                result=self.stack.upgrade(target,self.wheels,self.output,self.key)
            resume.assert_called_once_with(self.stack,selected)
            self.assertEqual(result['status'],'needs_attention' if failure else 'rolled_back')
