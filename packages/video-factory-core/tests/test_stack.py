import copy
import io
import json
import os
import socket
import sys
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from cryptography.fernet import Fernet

from video_factory.stack import Stack, compose_document, images, validate_config, template, write_json, preflight
from video_factory.runtime_store import RuntimeFault
from video_factory.postgres_store import PG_SCHEMA


class StackTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.parent=Path(self.temp.name)
        self.root=self.parent/'stack';self.root.mkdir(mode=0o700)
        self.wheels=self.parent/'wheels';self.wheels.mkdir(mode=0o700)
        (self.wheels/'video_factory_core-fixture.whl').write_bytes(b'NOT_EXECUTABLE_TEST_FIXTURE')

    def tearDown(self):self.temp.cleanup()

    def prepare(self):
        return Stack.prepare(self.root,'fixture-customer',self.wheels,'fixture-admin-password',18787,15678)

    def test_compose_pins_images_isolates_roles_and_only_exposes_loopback(self):
        stack=self.prepare();document=compose_document(stack.config)
        self.assertTrue(document['networks']['private']['internal'])
        self.assertNotIn('ports',document['services']['postgres'])
        for name in ('runtime','n8n'):
            self.assertNotIn('ports',document['services'][name])
            self.assertEqual(document['services'][name]['networks'],['private'])
        self.assertTrue(all(p.startswith('127.0.0.1:') for p in document['services']['gateway']['ports']))
        self.assertNotIn('secrets',document['services']['gateway'])
        self.assertEqual(document['services']['n8n']['environment']['DB_POSTGRESDB_USER'],'vf_n8n')
        self.assertNotIn('runtime_master',document['services']['n8n']['secrets'])
        self.assertNotIn('bootstrap_password',document['services']['runtime']['secrets'])
        self.assertTrue(all('@sha256:' in images()[k] for k in ('python','postgres','n8n')))
        raw=(self.root/'compose.json').read_text()+(self.root/'stack.json').read_text()
        self.assertNotIn('fixture-admin-password',raw)
        self.assertEqual((self.root/'secrets/bootstrap_password').stat().st_mode & 0o777,0o600)

    def test_worker_is_opt_in_and_egress_has_no_credentials_or_customer_mounts(self):
        document=compose_document(self.prepare().config)
        worker=document['services']['worker'];relay=document['services']['egress']
        self.assertEqual(worker['profiles'],['worker'])
        self.assertEqual(worker['restart'],'no')
        self.assertEqual(worker['entrypoint'],['python','-m','video_factory.worker_container'])
        self.assertEqual(worker['networks'],['private','worker_link'])
        self.assertEqual(relay['networks'],['worker_link','outbound'])
        self.assertTrue(document['networks']['worker_link']['internal'])
        for field in ('secrets','volumes','ports'):self.assertNotIn(field,relay)
        self.assertEqual(relay['restart'],'no')
        self.assertIn('@sha256:',images()['ffmpeg'])

    def test_previous_deployment_assets_are_exact_and_not_silently_upgraded(self):
        stack=self.prepare();config={**stack.config,'schema':1,'images':images(1)}
        (self.root/'release/Dockerfile').write_bytes(template('Dockerfile',1).read_bytes())
        write_json(self.root/'stack.json',config);write_json(self.root/'compose.json',compose_document(config))
        prior=Stack(self.root)
        self.assertEqual(prior.config['schema'],1)
        self.assertNotIn('worker',compose_document(config)['services'])
        (self.root/'release/Dockerfile').write_bytes(template('Dockerfile',2).read_bytes())
        with self.assertRaisesRegex(RuntimeFault,'TEMPLATE_CHANGED'):Stack(self.root)
        with self.assertRaises(RuntimeFault):validate_config({**config,'images':images(2)})

    def test_resume_does_not_replace_customer_secret_or_target(self):
        stack=self.prepare();before=(self.root/'secrets/runtime_master').read_bytes()
        with self.assertRaisesRegex(RuntimeFault,'EMPTY_DIRECTORY'):self.prepare()
        self.assertEqual(before,(self.root/'secrets/runtime_master').read_bytes())
        self.assertEqual(Stack(self.root).config,stack.config)

    def test_modified_compose_or_wheel_rejected_before_docker(self):
        self.prepare();path=self.root/'compose.json';document=json.loads(path.read_text())
        document['services']['runtime']['privileged']=True;path.write_text(json.dumps(document))
        with self.assertRaisesRegex(RuntimeFault,'COMPOSE_CHANGED'):Stack(self.root)
        config=json.loads((self.root/'stack.json').read_text());path.write_text(json.dumps(compose_document(config)))
        (self.root/'release/wheels/video_factory_core-fixture.whl').write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeFault,'WHEEL_CHANGED'):Stack(self.root)

    def test_config_rejects_unpinned_images_ports_and_injected_names(self):
        config=self.prepare().config
        validate_config({**config,'deployment':'customer_team_123'})
        validate_config({**config,'deployment':'a' * 48})
        for patch in ({'deployment':'a;evil'},{'runtime_port':80},{'n8n_port':18787},{'images':{**images(),'n8n':'n8nio/n8n:latest'}}):
            with self.assertRaises(RuntimeFault):validate_config({**config,**patch})

    def test_backup_authentication_failure_leaves_destination_empty(self):
        source=self.parent/'bad.vfb';source.write_bytes(Fernet(Fernet.generate_key()).encrypt(b'fixture'));source.chmod(0o600)
        with self.assertRaisesRegex(RuntimeFault,'AUTHENTICATION'):Stack.restore(source,self.root,Fernet.generate_key())
        self.assertEqual(list(self.root.iterdir()),[])

    def test_restore_rejects_parent_traversal_symlinks_and_devices(self):
        key=Fernet.generate_key()
        for name,kind in [('../escape',tarfile.REGTYPE),('data/link',tarfile.SYMTYPE),('data/device',tarfile.CHRTYPE)]:
            buffer=io.BytesIO()
            with tarfile.open(fileobj=buffer,mode='w:gz') as archive:
                info=tarfile.TarInfo(name);info.type=kind;info.mode=0o600;archive.addfile(info)
            source=self.parent/'bad.vfb';source.write_bytes(Fernet(key).encrypt(buffer.getvalue()));source.chmod(0o600)
            with self.assertRaisesRegex(RuntimeFault,'MEMBER_UNSAFE'):Stack.restore(source,self.root,key)
            self.assertEqual(list(self.root.iterdir()),[])

    def test_failed_prepare_is_atomic_and_resume_repairs_derived_file_only(self):
        (self.wheels/'video_factory_core-fixture.whl').rename(self.wheels/'not_product.whl')
        with self.assertRaises(RuntimeFault):self.prepare()
        self.assertEqual(list(self.root.iterdir()),[])
        (self.wheels/'not_product.whl').rename(self.wheels/'video_factory_core-fixture.whl')
        stack=self.prepare()
        (self.root/'compose.json').unlink()
        with self.assertRaisesRegex(RuntimeFault,'COMPOSE_CHANGED'):Stack(self.root)
        self.assertEqual(Stack(self.root,repair_derived=True).config,stack.config)

    def test_migration_schema_has_postgres_native_types(self):
        self.assertNotIn('AUTOINCREMENT',PG_SCHEMA)
        self.assertNotIn(' BLOB ',PG_SCHEMA)
        self.assertIn('BIGSERIAL',PG_SCHEMA)
        self.assertIn('DOUBLE PRECISION',PG_SCHEMA)

    def test_restore_rejects_unbound_tls_pointers_before_writing(self):
        key=Fernet.generate_key()
        for target in ('../../../../secrets','/tmp','generations/'+'a'*32):
            buffer=io.BytesIO()
            with tarfile.open(fileobj=buffer,mode='w:gz') as archive:
                info=tarfile.TarInfo('data/workspaces/fixture/tls/current')
                info.type=tarfile.SYMTYPE;info.linkname=target;info.mode=0o777
                archive.addfile(info)
            source=self.parent/'bad-pointer.vfb'
            source.write_bytes(Fernet(key).encrypt(buffer.getvalue()));source.chmod(0o600)
            with self.assertRaisesRegex(RuntimeFault,'MEMBER_UNSAFE'):
                Stack.restore(source,self.root,key)
            self.assertEqual(list(self.root.iterdir()),[])


if __name__=='__main__':unittest.main()


@unittest.skipUnless(sys.platform == 'linux', 'Linux gateway port semantics')

class PortPreflightTests(unittest.TestCase):
    def check_ports(self, root, first, second):
        info = json.dumps({'OSType':'linux', 'Architecture':'x86_64',
                           'MemTotal':8*1024**3, 'ServerVersion':'test'}).encode()
        with patch('video_factory.stack.admin_host'), patch('video_factory.stack.local_engine'), \
             patch('video_factory.stack.run', side_effect=[info, b'2.40.3']), \
             patch('video_factory.stack.shutil.disk_usage') as disk:
            disk.return_value.free = 8*1024**3
            return preflight(root, first, second)

    def test_closed_gateway_connection_does_not_block_new_install(self):
        with tempfile.TemporaryDirectory() as root, socket.socket() as listener, socket.socket() as spare:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('127.0.0.1', 0)); address = listener.getsockname()
            spare.bind(('127.0.0.1', 0)); second = spare.getsockname()[1]; spare.close()
            listener.listen(1)
            with socket.create_connection(address, timeout=2) as client:
                connection, _ = listener.accept()
                connection.close()
                self.assertEqual(client.recv(1), b'')
            listener.close()
            # Prove this fixture reaches the old false-positive condition.
            with socket.socket() as old_probe:
                with self.assertRaises(OSError): old_probe.bind(address)
            result = self.check_ports(Path(root), address[1], second)
            self.assertTrue(result['loopback_ports_available'])

    def test_active_loopback_and_wildcard_listeners_are_still_rejected(self):
        for host in ('127.0.0.1', '0.0.0.0'):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as root, socket.socket() as listener:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind((host, 0)); listener.listen(1)
                port = listener.getsockname()[1]
                with self.assertRaisesRegex(RuntimeFault, '^STACK_PORT_ALREADY_IN_USE$'):
                    self.check_ports(Path(root), port, 5678 if port != 5678 else 8787)
