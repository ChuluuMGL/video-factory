from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import test_stack as fixtures
from video_factory import workspace, workspace_tls as tls
from video_factory.runtime_store import RuntimeFault
from video_factory.stack import write_json


@unittest.skipUnless(os.getuid() == 0, 'Cloud root fixture for container-owned private TLS files')
class WorkspaceTLSTests(unittest.TestCase):
    setUpRoot = fixtures.StackTests.setUp
    tearDown = fixtures.StackTests.tearDown
    prepareStack = fixtures.StackTests.prepare
    def setUp(self):
        self.setUpRoot(); self.stack = self.prepareStack()
        self.stack.config['runtime_image'] = 'sha256:'+'a'*64
        self.project = 'fixture_project'
        self.oldcert, self.oldkey = self.certificate('old')
        self.cert, self.key = self.certificate('new')
        self.root = workspace.directory(self.stack, self.project)
        self.root.mkdir(parents=True, mode=0o700)
        (self.root/'tls').mkdir(mode=0o700); os.chown(self.root/'tls', 10001, 10001)
        for path, source in [('certificate.pem', self.oldcert), ('key.pem', self.oldkey)]:
            p = self.root/'tls'/path; p.write_bytes(source.read_bytes()); p.chmod(0o600); os.chown(p, 10001, 10001)
        self.value = workspace.plan(self.stack, self.project, 'https://review.example:9443', self.oldcert, self.oldkey)
        write_json(self.root/'workspace.json', self.value)
        write_json(self.root/'compose.json', workspace.document(self.stack, self.value))
        (self.root/'nginx.conf').write_text(workspace.nginx(self.value))
        self.plan = tls.plan(self.stack, self.project, self.cert, self.key)

    def certificate(self, name):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        dn = x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, 'review.example')])
        now = datetime.now(timezone.utc)
        cert = x509.CertificateBuilder().subject_name(dn).issuer_name(dn).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=45)).add_extension(x509.SubjectAlternativeName([x509.DNSName('review.example')]), False).sign(key, hashes.SHA256())
        c, k = self.parent/(name+'.cert'), self.parent/(name+'.key')
        c.write_bytes(cert.public_bytes(serialization.Encoding.PEM)); k.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        c.chmod(0o600); k.chmod(0o600); return c, k

    def test_rotate_pair_once_validate_reload_and_readback_without_service_restart(self):
        with patch.object(workspace, 'compose') as compose:
            result = tls.apply(self.stack, self.plan, self.cert, self.key, readback=lambda *_: True)
        self.assertEqual(result['status'], 'rotated')
        self.assertEqual([call.args[-1] for call in compose.call_args_list], ['-t', 'reload'])
        self.assertEqual(tls.pair(self.root, json.loads((self.root/'workspace.json').read_text()))[0], self.cert.read_bytes())
        self.assertIn('/tls/current/certificate.pem', (self.root/'nginx.conf').read_text())
        self.assertEqual(len(list((self.root/'tls/generations').iterdir())), 2)
        self.assertFalse(tls.status(self.stack, self.project)['external_https_verified'])

    def test_reload_or_readback_failure_restores_old_pair(self):
        with patch.object(workspace, 'compose'), patch.object(tls, 'served_leaf'):
            reads = iter([False, True])
            with self.assertRaisesRegex(RuntimeFault, 'ROLLED_BACK'):
                tls.apply(self.stack, self.plan, self.cert, self.key, readback=lambda *_: next(reads))
        self.assertEqual(json.loads((self.root/'workspace.json').read_text()), self.value)
        self.assertEqual(tls.pair(self.root, self.value)[0], self.oldcert.read_bytes())
        self.assertEqual(tls.status(self.stack, self.project)['last_rotation']['status'], 'rolled_back')

    def test_failed_rollback_visible_and_recovery_rechecks_old_leaf(self):
        with patch.object(workspace, 'compose') as compose:
            compose.side_effect = RuntimeFault('FIXTURE_RELOAD_FAILURE')
            with self.assertRaisesRegex(RuntimeFault, 'NEEDS_ATTENTION'):
                tls.apply(self.stack, self.plan, self.cert, self.key, readback=lambda *_: False)
        self.assertTrue(tls.status(self.stack, self.project)['recovery_required'])
        with patch.object(workspace, 'compose'):
            self.assertEqual(tls.recover(self.stack, self.project, readback=lambda *_: True)['status'], 'rolled_back')

    def test_changed_key_plan_stack_and_symlink_refused(self):
        bad = dict(self.plan); bad['certificate_sha256'] = '0'*64
        with self.assertRaisesRegex(RuntimeFault, 'PLAN_CHANGED'):
            tls.apply(self.stack, bad, self.cert, self.key)
        self.stack.config['instance'] = 'different'
        with self.assertRaisesRegex(RuntimeFault, 'RECONFIRM'): tls.plan(self.stack, self.project, self.cert, self.key)

    def test_repeated_rotation_keeps_previous_generation_and_blocks_tamper(self):
        with patch.object(workspace, 'compose'):
            tls.apply(self.stack, self.plan, self.cert, self.key, readback=lambda *_: True)
            nextplan = tls.plan(self.stack, self.project, self.oldcert, self.oldkey)
            tls.apply(self.stack, nextplan, self.oldcert, self.oldkey, readback=lambda *_: True)
        self.assertEqual(len(list((self.root/'tls/generations').iterdir())), 3)
        (self.root/'tls/current').unlink(); (self.root/'tls/current').symlink_to('/etc')
        with self.assertRaisesRegex(RuntimeFault, 'LINK_UNSAFE'): tls.status(self.stack, self.project)

    def test_private_key_is_never_in_status_or_rotation_receipt(self):
        with patch.object(workspace, 'compose'):
            result = tls.apply(self.stack, self.plan, self.cert, self.key, readback=lambda *_: True)
        self.assertNotIn('PRIVATE KEY', json.dumps(result))
        self.assertNotIn('PRIVATE KEY', json.dumps(tls.status(self.stack, self.project)))

    def test_rotated_generation_survives_cold_backup_restore(self):
        from cryptography.fernet import Fernet
        from video_factory.stack import Stack
        with patch.object(workspace, 'compose'):
            tls.apply(self.stack, self.plan, self.cert, self.key, readback=lambda *_: True)
        write_json(self.stack.root/'initialized.json', {'schema':1})
        target = self.parent/'checkpoint.vfb'; key = Fernet.generate_key()
        with patch.object(workspace, 'stop_all'), patch.object(self.stack, 'compose'), patch.object(self.stack, 'status', return_value={'components':{'postgres':{'state':'exited','exit_code':0}}}):
            self.stack.backup(target,key)
        restored = self.parent/'restored'; restored.mkdir(mode=0o700)
        Stack.restore(target,restored,key)
        pointer = restored/'data/workspaces'/self.project/'tls/current'
        self.assertTrue(pointer.is_symlink())
        self.assertEqual((pointer/'key.pem').read_bytes(),self.key.read_bytes())
        self.assertEqual((pointer/'certificate.pem').read_bytes(),self.cert.read_bytes())

    def test_backup_rejects_pointer_outside_its_generations(self):
        from cryptography.fernet import Fernet
        write_json(self.stack.root/'initialized.json', {'schema':1})
        (self.root/'tls/current').symlink_to('/tmp')
        with patch.object(workspace, 'stop_all'), patch.object(self.stack, 'compose'), patch.object(self.stack, 'status', return_value={'components':{'postgres':{'state':'exited','exit_code':0}}}):
            with self.assertRaisesRegex(RuntimeFault,'SPECIAL_FILE'):
                self.stack.backup(self.parent/'bad.vfb',Fernet.generate_key())
