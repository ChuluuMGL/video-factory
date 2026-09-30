import concurrent.futures
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from cryptography.fernet import Fernet

from video_factory.runtime_store import RuntimeStore, RuntimeFault
from video_factory.runtime_ops import backup, restore
from video_factory.runtime_http import RuntimeServer


PASSWORD='fixture-only-strong-password'
CONFIG={'video_route':'deferred','credential_ref':'secret:fixture','billing_owner':'fixture'}
INPUT={'sku_id':'sku1','script':'Fixture script','source_revision':'r1'}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.store=RuntimeStore.install(self.root,'fixture',PASSWORD)
        self.token=self.store.login('admin',PASSWORD)['token']
        self.store.put_project(self.token,'brand',CONFIG)

    def tearDown(self):
        self.temp.cleanup()

    def ready(self, task='task1',project='brand'):
        self.store.create_task(self.token,project,task,INPUT)
        self.store.review(self.token,'script-'+project+'-'+task,project,task,1,'script','accept')

    def test_install_rejects_overwrite_and_shared_or_symlink_directory(self):
        with self.assertRaises(FileExistsError): RuntimeStore.install(self.root,'other',PASSWORD)
        self.root.chmod(0o755)
        with self.assertRaises(RuntimeFault): RuntimeStore(self.root)
        self.root.chmod(0o700)
        link=self.root/'link'; link.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(RuntimeFault): RuntimeStore(link)

    def test_database_symlink_hardlink_and_public_mode_rejected(self):
        self.store.path.chmod(0o644)
        with self.assertRaises(RuntimeFault): RuntimeStore(self.root)
        self.store.path.chmod(0o600)
        import os
        os.link(self.store.path,self.root/'copy')
        with self.assertRaises(RuntimeFault): RuntimeStore(self.root)

    def test_password_not_stored_and_failed_logins_rate_limited(self):
        self.assertNotIn(PASSWORD.encode(),self.store.path.read_bytes())
        for _ in range(5):
            with self.assertRaisesRegex(RuntimeFault,'AUTH_FAILED'): self.store.login('admin','wrong')
        with self.assertRaisesRegex(RuntimeFault,'RATE_LIMITED'): self.store.login('admin',PASSWORD)
        self.store.recover_admin(PASSWORD)
        self.store.login('admin',PASSWORD)
        with self.assertRaisesRegex(RuntimeFault,'AUTH_REQUIRED'): self.store.put_project(self.token,'new',CONFIG)

    def test_expired_token_and_reviewer_project_scope(self):
        self.store.set_user(self.token,'alice',PASSWORD,'brand')
        reviewer=self.store.login('alice',PASSWORD)['token']
        with self.assertRaisesRegex(RuntimeFault,'DENIED'): self.store.put_project(reviewer,'evil',CONFIG)
        with self.assertRaisesRegex(RuntimeFault,'DENIED'): self.store.inspect_task(reviewer,'other','task1')
        self.store.revoke_user(self.token,'alice')
        with self.assertRaisesRegex(RuntimeFault,'AUTH_REQUIRED'): self.store.inspect_task(reviewer,'brand','task1')
        with self.store.connect() as db: db.execute('UPDATE sessions SET expires=0')
        with self.assertRaisesRegex(RuntimeFault,'AUTH_REQUIRED'): self.store.inspect_task(self.token,'brand','task1')

    def test_vault_ciphertext_rotation_and_wrong_key_rollback(self):
        old,new,wrong=Fernet.generate_key(),Fernet.generate_key(),Fernet.generate_key()
        self.store.put_secret(self.token,'fixture','private-provider-fixture',old)
        self.assertNotIn(b'private-provider-fixture',self.store.path.read_bytes())
        with self.assertRaisesRegex(RuntimeFault,'DOES_NOT_MATCH'): self.store.put_secret(self.token,'fixture','bad overwrite',wrong)
        with self.assertRaisesRegex(RuntimeFault,'KEY_ROTATION_FAILED'): self.store.rotate_key(self.token,wrong,new)
        self.assertEqual(self.store.resolve_secret('fixture',old),'private-provider-fixture')
        self.store.rotate_key(self.token,old,new)
        self.assertEqual(self.store.resolve_secret('fixture',new),'private-provider-fixture')
        with self.assertRaisesRegex(RuntimeFault,'DECRYPT_FAILED'): self.store.resolve_secret('fixture',old)

    def test_reviewer_cannot_read_or_write_secrets(self):
        self.store.set_user(self.token,'alice',PASSWORD,'brand')
        token=self.store.login('alice',PASSWORD)['token']
        with self.assertRaisesRegex(RuntimeFault,'DENIED'): self.store.put_secret(token,'x','hidden',Fernet.generate_key())

    def test_multiple_tasks_and_same_id_across_projects(self):
        self.store.put_project(self.token,'other',CONFIG)
        for project,task in [('brand','one'),('brand','two'),('other','one')]:
            self.ready(task,project)
            self.assertEqual(len(self.store.inspect_task(self.token,project,task)['versions']),1)
        self.assertEqual(self.store.doctor()['task_states'],{'ready':3})

    def test_concurrent_claim_one_winner_restart_does_not_resubmit(self):
        self.ready()
        def claim(_):
            try:return RuntimeStore(self.root).claim(self.token,'brand','task1',1)['state']
            except RuntimeFault:return 'blocked'
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(claim,range(4)))
        self.assertEqual(results.count('submission_unknown'),1)
        self.assertEqual(results.count('blocked'),3)
        with self.assertRaisesRegex(RuntimeFault,'NO_RESUBMIT'): RuntimeStore(self.root).claim(self.token,'brand','task1',1)

    def test_review_replay_and_event_conflict(self):
        self.store.create_task(self.token,'brand','one',INPUT)
        first=self.store.review(self.token,'evt','brand','one',1,'script','accept')
        self.assertEqual(first,self.store.review(self.token,'evt','brand','one',1,'script','accept'))
        with self.assertRaisesRegex(RuntimeFault,'EVENT_ID_CONFLICT'): self.store.review(self.token,'evt','brand','one',1,'script','reject','change')

    def test_reject_revision_and_old_review_do_not_touch_new_version(self):
        self.store.create_task(self.token,'brand','one',INPUT)
        self.store.review(self.token,'evt1','brand','one',1,'script','reject','Correct SKU')
        self.store.create_task(self.token,'brand','one',{**INPUT,'script':'Corrected'},expected_revision=1)
        with self.assertRaisesRegex(RuntimeFault,'STALE_TASK_REVISION'): self.store.review(self.token,'evt2','brand','one',1,'script','accept')
        self.assertEqual(self.store.inspect_task(self.token,'brand','one')['versions'][-1]['state'],'awaiting_script_review')
        self.assertEqual(self.store.inspect_task(self.token,'brand','one')['reviews'][0]['feedback'],'Correct SKU')

    def test_config_drift_blocks_old_task(self):
        self.ready()
        from video_factory.runtime_store import fingerprint
        self.store.put_project(self.token,'brand',{**CONFIG,'billing_owner':'changed'},fingerprint(CONFIG))
        with self.assertRaisesRegex(RuntimeFault,'CONFIGURATION_CHANGED'): self.store.claim(self.token,'brand','task1',1)

    def test_paid_route_fails_closed(self):
        with self.assertRaisesRegex(RuntimeFault,'PAID_ADAPTER_NOT_VERIFIED'): self.store.put_project(self.token,'paid',{**CONFIG,'video_route':'minimax_h3'})

    def test_provider_identity_fenced_and_human_review_separate(self):
        self.ready();self.store.claim(self.token,'brand','task1',1)
        self.store.attach_provider(self.token,'brand','task1',1,'provider1')
        with self.assertRaisesRegex(RuntimeFault,'PROVIDER_RECEIPT_CONFLICT'): self.store.attach_provider(self.token,'brand','task1',1,'provider2')
        artifact={'sha256':'a'*64,'location':'customer-store/fixture.mp4','verification':'full_decode_passed'}
        with self.assertRaisesRegex(RuntimeFault,'PROVIDER_CONFLICT'): self.store.record_artifact(self.token,'brand','task1',1,'wrong',artifact)
        receipt=self.store.record_artifact(self.token,'brand','task1',1,'provider1',artifact)
        self.assertEqual(receipt['human_acceptance'],'pending')
        result=self.store.review(self.token,'video-evt','brand','task1',1,'video','accept')
        self.assertEqual(result['state'],'accepted')

    def test_encrypted_restore_to_separate_root_keeps_uncertain_job_and_vault(self):
        self.ready();self.store.claim(self.token,'brand','task1',1)
        master,key=Fernet.generate_key(),Fernet.generate_key()
        self.store.put_secret(self.token,'fixture','hidden-vault',master)
        dest=self.root/'backup.vfb'
        backup(self.store,dest,key)
        self.assertNotIn(b'SQLite',dest.read_bytes())
        restored=self.root/'restored';restored.mkdir(mode=0o700)
        result=restore(dest,restored,key)
        self.assertTrue(result['sessions_revoked'])
        store=RuntimeStore(restored)
        self.assertEqual(store.resolve_secret('fixture',master),'hidden-vault')
        with self.assertRaisesRegex(RuntimeFault,'AUTH_REQUIRED'): store.inspect_task(self.token,'brand','task1')
        token=store.login('admin',PASSWORD)['token']
        with self.assertRaisesRegex(RuntimeFault,'NO_RESUBMIT'): store.claim(token,'brand','task1',1)

    def test_restore_rejects_wrong_key_tamper_and_nonempty_target(self):
        key=Fernet.generate_key();dest=self.root/'backup.vfb';backup(self.store,dest,key)
        target=self.root/'restored';target.mkdir(mode=0o700)
        with self.assertRaisesRegex(RuntimeFault,'AUTHENTICATION'):restore(dest,target,Fernet.generate_key())
        self.assertEqual(list(target.iterdir()),[])
        data=bytearray(dest.read_bytes());data[20]^=1;dest.write_bytes(data)
        with self.assertRaisesRegex(RuntimeFault,'AUTHENTICATION'):restore(dest,target,key)
        (target/'keep').write_text('customer data')
        with self.assertRaisesRegex(RuntimeFault,'EMPTY_DIRECTORY'):restore(dest,target,key)

    def test_http_authentication_strict_fields_and_loopback(self):
        with self.assertRaisesRegex(RuntimeFault,'LOOPBACK'):RuntimeServer(('0.0.0.0',0),self.store)
        server=RuntimeServer(('127.0.0.1',0),self.store)
        thread=threading.Thread(target=server.serve_forever);thread.start()
        try:
            def post(path,body,token=None):
                headers={'Content-Type':'application/json'}
                if token:headers['Authorization']='Bearer '+token
                request=Request(f'http://127.0.0.1:{server.server_port}'+path,data=json.dumps(body).encode(),headers=headers)
                try:
                    with urlopen(request,timeout=5) as response:return response.status,json.load(response)
                except HTTPError as error:return error.code,json.load(error)
            self.assertEqual(post('/v1/doctor',{})[0],401)
            self.assertEqual(post('/v1/doctor',{},self.token)[0],200)
            self.assertEqual(post('/v1/doctor',{'token':'leak'},self.token)[0],409)
            self.assertEqual(post('/v1/secrets/read',{},self.token)[0],404)
            self.assertEqual(post('/v1/users',{'name':'bob'},self.token)[0],400)
        finally:
            server.shutdown();server.server_close();thread.join()


class HostPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def payload(self):
        import socket
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        return {'action':'preflight','root':str(self.root/'customer'),'port':port,'deployment':'ci'}

    def test_preflight_has_no_install_side_effect(self):
        from video_factory.host_bootstrap import main
        result=main(self.payload())
        self.assertTrue(result['port_available'])
        self.assertEqual(list(self.root.iterdir()),[])

    def test_port_conflict_and_shared_parent_fail_before_install(self):
        import socket
        from video_factory.host_bootstrap import main
        payload=self.payload()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',payload['port']))
            with self.assertRaisesRegex(ValueError,'PORT_IN_USE'):main(payload)
        self.root.chmod(0o777)
        with self.assertRaisesRegex(ValueError,'PARENT_NOT_PRIVATE'):main(payload)
        self.root.chmod(0o700)

    def test_tampered_bundle_rejected_before_directory_created(self):
        import base64
        from video_factory.host_bootstrap import main
        payload={**self.payload(),'action':'install','password':PASSWORD,
                 'wheels':[{'name':'video_factory_core-test.whl','data':base64.b64encode(b'bad').decode(),'sha256':'a'*64}]}
        with self.assertRaisesRegex(ValueError,'HASH_MISMATCH'):main(payload)
        self.assertEqual(list(self.root.iterdir()),[])

    def test_remote_root_shell_characters_rejected(self):
        from video_factory.host_bootstrap import main
        payload=self.payload();payload['root']=str(self.root/'customer;touch_injected')
        with self.assertRaisesRegex(ValueError,'ROOT_UNSAFE'):main(payload)


if __name__=='__main__':unittest.main()
