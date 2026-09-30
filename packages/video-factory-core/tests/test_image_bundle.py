import copy
import hashlib
import json
import io
import tarfile
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_factory.image_bundle import inspect_bundle, import_bundle, validate_manifest, verify_archive_index, verify_loaded
from video_factory.runtime_store import RuntimeFault
from video_factory.stack import images, compose_document, validate_config


class ImageBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.wheels={'video_factory_core-fixture.whl':'1'*64}
        raw=b'NOT_A_DOCKER_ARCHIVE'
        self.manifest={'schema':1,'platform':'linux/amd64','locks':images(),'wheels':self.wheels,
            'images':{role:'sha256:'+str(n)*64 for n,role in enumerate(('runtime','postgres','n8n','gateway'),2)},
            'archive':{'file':'images.tar','sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw)}}
        (self.root/'images.tar').write_bytes(raw);(self.root/'images.tar').chmod(0o600)
        self.save()

    def tearDown(self):self.temp.cleanup()

    def save(self):
        path=self.root/'manifest.json';path.write_text(json.dumps(self.manifest));path.chmod(0o600)
        self.sha=hashlib.sha256(path.read_bytes()).hexdigest()

    def test_wrong_digest_tamper_release_platform_and_symlink_fail_before_load(self):
        with patch('video_factory.stack.run') as run,patch('video_factory.stack.admin_host'),patch('video_factory.stack.local_engine'):
            with self.assertRaisesRegex(RuntimeFault,'MANIFEST_CHANGED'):
                import_bundle(self.root,'0'*64,self.wheels,images())
            (self.root/'images.tar').write_bytes(b'changed')
            with self.assertRaisesRegex(RuntimeFault,'ARCHIVE_CHANGED'):
                import_bundle(self.root,self.sha,self.wheels,images())
            run.assert_not_called()
        for field,value in [('platform','linux/arm64'),('wheels',{}),('locks',{})]:
            bad=copy.deepcopy(self.manifest);bad[field]=value
            with self.assertRaises(RuntimeFault):validate_manifest(bad,self.wheels,images())
        (self.root/'images.tar').unlink();(self.root/'images.tar').symlink_to(self.root/'manifest.json')
        with self.assertRaises(RuntimeFault):inspect_bundle(self.root,self.sha,self.wheels,images())

    def test_no_hash_no_implicit_trust_and_no_path_traversal(self):
        with self.assertRaisesRegex(RuntimeFault,'TRUSTED_IMAGE_MANIFEST'):
            inspect_bundle(self.root,None,self.wheels,images())
        self.manifest['archive']['file']='../images.tar';self.save()
        with self.assertRaisesRegex(RuntimeFault,'ARCHIVE_INVALID'):
            inspect_bundle(self.root,self.sha,self.wheels,images())

    def test_compose_binds_loaded_ids_and_refuses_mismatched_runtime(self):
        config={'schema':2,'deployment':'fixture','instance':'a'*12,'runtime_port':18787,'n8n_port':15678,
            'images':images(),'wheels':self.wheels,'runtime_image':self.manifest['images']['runtime'],'image_bundle':self.manifest}
        document=compose_document(config)
        for role in ('runtime','postgres','n8n','gateway'):
            self.assertEqual(document['services'][role]['image'],self.manifest['images'][role])
            self.assertEqual(document['services'][role]['pull_policy'],'never')
        config['runtime_image']='sha256:'+'f'*64
        with self.assertRaisesRegex(RuntimeFault,'RUNTIME_MISMATCH'):validate_config(config)

    def test_import_checks_loaded_identity_and_never_pulls(self):
        def engine(command, **kwargs):
            if 'load' in command:return b'loaded'
            if 'inspect' in command:return json.dumps([{'Id':command[-1],'Os':'linux','Architecture':'amd64'}]).encode()
            raise AssertionError(command)
        with patch('video_factory.stack.admin_host'),patch('video_factory.stack.local_engine'),patch('video_factory.stack.run',side_effect=engine) as run:
            self.assertEqual(import_bundle(self.root,self.sha,self.wheels,images()),self.manifest)
            self.assertFalse(any('pull' in call.args[0] for call in run.call_args_list))
        with patch('video_factory.stack.admin_host'),patch('video_factory.stack.local_engine'),patch('video_factory.stack.run',return_value=b'[{"Id":"sha256:bad","Os":"linux","Architecture":"arm64"}]'):
            with self.assertRaisesRegex(RuntimeFault,'PLATFORM_MISMATCH'):
                import_bundle(self.root,self.sha,self.wheels,images())

    def test_offline_upgrade_without_candidate_bundle_does_not_stop_source(self):
        from video_factory.stack import Stack, write_json
        from cryptography.fernet import Fernet
        wheels=self.root/'wheels';wheels.mkdir(mode=0o700)
        (wheels/'video_factory_core-fixture.whl').write_bytes(b'fixture')
        target=self.root/'stack';target.mkdir(mode=0o700)
        stack=Stack.prepare(target,'fixture',wheels,'synthetic-cloud-password',18787,15678)
        manifest=copy.deepcopy(self.manifest);manifest['wheels']=stack.config['wheels']
        stack.config['image_bundle']=manifest
        candidate=self.root/'candidate';candidate.mkdir(mode=0o700)
        with patch.object(stack,'status') as status,patch.object(stack,'_backup_unlocked') as backup:
            with self.assertRaisesRegex(RuntimeFault,'OFFLINE_UPGRADE_REQUIRES'):
                stack.upgrade(candidate,wheels,self.root/'backup',Fernet.generate_key())
            status.assert_not_called();backup.assert_not_called()

    def test_complete_docker_manifest_does_not_hide_incomplete_oci_index(self):
        # a22 ECS failure: classic sees four configs, containerd sees only one.
        ids=self.manifest['images']
        def archive(count, *, nested=False, tamper=False):
            entries={};descriptors=[]
            for image_id in list(ids.values())[:count]:
                raw=json.dumps({'schemaVersion':2,'config':{'digest':image_id}}).encode()
                checksum=hashlib.sha256(raw).hexdigest()
                entries['blobs/sha256/'+checksum]=raw+(b' ' if tamper else b'')
                descriptors.append({'digest':'sha256:'+checksum})
            index={'schemaVersion':2,'manifests':descriptors}
            if nested:
                raw=json.dumps(index).encode();checksum=hashlib.sha256(raw).hexdigest()
                entries['blobs/sha256/'+checksum]=raw
                index={'schemaVersion':2,'manifests':[{'digest':'sha256:'+checksum}]}
            entries['index.json']=json.dumps(index).encode()
            entries['manifest.json']=json.dumps([{'Config':'blobs/sha256/'+v[7:]} for v in ids.values()]).encode()
            target=self.root/'index-fixture.tar'
            with tarfile.open(target,'w') as output:
                for name,raw in entries.items():
                    member=tarfile.TarInfo(name);member.size=len(raw);output.addfile(member,io.BytesIO(raw))
            return target
        with self.assertRaisesRegex(RuntimeFault,'ARCHIVE_IMAGES_MISSING'):
            verify_archive_index(archive(1),ids)
        verify_archive_index(archive(4),ids)
        verify_archive_index(archive(4,nested=True),ids)
        with self.assertRaisesRegex(RuntimeFault,'ARCHIVE_INDEX_INVALID'):
            verify_archive_index(archive(4,tamper=True),ids)

    def test_missing_imported_image_has_actionable_secret_free_error(self):
        def engine(command,**kwargs):
            if 'load' in command:return b'Loaded one image'
            raise RuntimeFault('STACK_COMMAND_FAILED')
        with patch('video_factory.stack.admin_host'),patch('video_factory.stack.local_engine'),patch('video_factory.stack.run',side_effect=engine):
            with self.assertRaisesRegex(RuntimeFault,'IMAGE_BUNDLE_REQUIRED_IMAGE_MISSING'):
                import_bundle(self.root,self.sha,self.wheels,images())

    def test_portable_ids_select_immutable_digest_for_each_store(self):
        self.manifest['schema']=2
        self.manifest['oci_images']={role:'sha256:'+digit*64 for role,digit in zip(self.manifest['images'],'abcd')}
        self.save()
        validate_manifest(self.manifest,self.wheels,images())
        for store in ('images','oci_images'):
            def engine(command,**kwargs):
                reference=command[-1]
                if reference not in self.manifest[store].values():raise RuntimeFault('STACK_COMMAND_FAILED')
                return json.dumps([{'Id':reference,'Os':'linux','Architecture':'amd64'}]).encode()
            with patch('video_factory.stack.run',side_effect=engine):
                resolved=verify_loaded(self.manifest)
            self.assertEqual(resolved,self.manifest[store])
            config={'schema':2,'deployment':'fixture','instance':'a'*12,'runtime_port':18787,'n8n_port':15678,
                'images':images(),'wheels':self.wheels,'runtime_image':resolved['runtime'],
                'image_bundle':self.manifest,'resolved_images':resolved}
            document=compose_document(config)
            for role in resolved:self.assertEqual(document['services'][role]['image'],resolved[role])
            config['resolved_images']={**resolved,'postgres':'sha256:'+'f'*64}
            with self.assertRaisesRegex(RuntimeFault,'RESOLVED_IDS_INVALID'):validate_config(config)
        self.manifest['oci_images']['postgres']='mutable:latest'
        with self.assertRaisesRegex(RuntimeFault,'PORTABLE_IDS_INVALID'):validate_manifest(self.manifest,self.wheels,images())
