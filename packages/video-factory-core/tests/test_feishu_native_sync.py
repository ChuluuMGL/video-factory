"""Synthetic original-row setup and draft delivery; run in cloud CI."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from video_factory.feishu_bridge import FeishuBridge, save
from video_factory.feishu_native_sync import NativeSync, STATUS_OPTIONS
from video_factory.runtime_store import RuntimeFault, RuntimeStore, canonical


class Remote:
    def __init__(self):
        self.schema=[{'field_id':field_id,'field_name':name,'type':1} for name,field_id in (
            ('任务编号','fldTask'),('SKU','fldSkuId'),('脚本','fldScript'),('来源版本','fldSource'))]
        self.record_fields={'任务编号':'task_one','SKU':'sku_one','脚本':'Initial brief','来源版本':'source_one'}
        self.subscription=False;self.subscription_error=None;self.writes=[]
    def fields(self,base,table):return list(self.schema)
    def create_field(self,base,table,name,kind,ticket):
        field_id='fldAdded'+str(len(self.schema))
        field={'field_id':field_id,'field_name':name,'type':kind}
        if name == '状态':field['property']={'options':[{'name':name} for name in STATUS_OPTIONS]}
        self.schema.append(field)
        self.writes.append(('field',name))
        return field_id
    def base_subscription_status(self,base):
        if self.subscription_error:raise RuntimeFault(self.subscription_error)
        return self.subscription
    def subscribe_base(self,base):self.subscription=True;self.writes.append(('subscribe',base))
    def find_task(self,base,table,name,task):
        assert (name,task)==('任务编号','task_one')
        return self.record(base,table,'recFixture')
    def record(self,base,table,record):return {'record_id':record,'fields':dict(self.record_fields)}
    def update_record(self,base,table,record,fields):
        assert record=='recFixture'
        self.record_fields.update(fields);self.writes.append(('record',fields))
        return record
    def upload_prepare(self,base,name,size):
        self.writes.append(('upload_prepare',size))
        return {'upload_id':'uploadFixture','block_num':1,'block_size':4*1024*1024}
    def upload_part(self,upload_id,seq,data):
        self.writes.append(('upload_part',seq))
        return {'seq':seq,'size':len(data)}
    def upload_finish(self,upload_id,block_num):
        self.writes.append(('upload_finish',block_num))
        return 'fileFixture'


class NativeSyncTests(unittest.TestCase):
    def test_subscription_permission_failure_does_not_mutate_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=RuntimeStore.install(Path(tmp),'fixture','fixture-admin-password')
            admin=store.login('admin','fixture-admin-password')['token']
            store.put_project(admin,'brand',{'video_route':'deferred','credential_ref':'secret:fixture','billing_owner':'fixture'})
            binding={'tenant_key':'fixture_tenant','base_token':'bascnFixture','table_id':'tblFixture',
                     'fields':{'task':'fldTask','sku_id':'fldSkuId','script':'fldScript','source_revision':'fldSource'},
                     'submitters':['ou_submitter'],'reviewers':['ou_reviewer']}
            with store.connect() as db:save(db,'feishu:binding:brand',binding)
            context={'target':binding,'app_profile':{'app_id':'cli_fixture','credential_ref':'secret:fixture'},
                     'credential_revision':1,'configuration_sha256':'fixture'}
            remote=Remote();remote.subscription_error='FEISHU_READ_REJECTED_CODE_99991672'
            native=NativeSync(store,b'fixture-key')
            native.results=SimpleNamespace(context=lambda db,project:context,
                                           client=lambda value:remote,bridge=FeishuBridge(store))
            with self.assertRaisesRegex(RuntimeFault,'99991672'):
                native.prepare(admin,'brand')
            self.assertEqual(remote.writes,[])
            with store.connect() as db:
                self.assertIsNone(db.execute("SELECT value FROM meta WHERE key='native:config:brand'").fetchone())

            remote.subscription_error=None
            with store.connect() as db:
                save(db,'setup:feishu-app:another',{'app_id':'cli_fixture'})
            with self.assertRaisesRegex(RuntimeFault,'FEISHU_NATIVE_APP_SHARED_WITH_ANOTHER_PROJECT'):
                native.prepare(admin,'brand')
            self.assertEqual(remote.writes,[])

    def test_enable_adds_fields_and_syncs_original_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=RuntimeStore.install(Path(tmp),'fixture','fixture-admin-password')
            admin=store.login('admin','fixture-admin-password')['token']
            store.put_project(admin,'brand',{'video_route':'deferred','credential_ref':'secret:fixture','billing_owner':'fixture'})
            store.create_task(admin,'brand','task_one',{'sku_id':'sku_one','script':'Generated script','source_revision':'source_one'})
            target={'tenant_key':'fixture_tenant','base_token':'bascnFixture','table_id':'tblFixture',
                    'fields':{'task':'fldTask','sku_id':'fldSkuId','script':'fldScript','source_revision':'fldSource'}}
            binding={**target,'submitters':['ou_submitter'],'reviewers':['ou_reviewer']}
            with store.connect() as db:save(db,'feishu:binding:brand',binding)
            context={'target':target,'app_profile':{'app_id':'cli_fixture','credential_ref':'secret:fixture'},
                     'credential_revision':1,'configuration_sha256':'fixture'}
            remote=Remote()
            # The fake returns actual Base fields without sending any request.
            native=NativeSync(store,b'fixture-key')
            native.results=SimpleNamespace(context=lambda db,project:context,
                                           client=lambda value:remote,bridge=FeishuBridge(store))
            plan=native.prepare(admin,'brand')
            enabled=native.enable(admin,'brand',plan['plan_sha256'])
            self.assertEqual(enabled['status'],'enabled')
            self.assertEqual(len([x for x in remote.writes if x[0]=='field']),6)
            self.assertEqual(next(f for f in remote.schema if f['field_name']=='状态')['type'],3)
            self.assertEqual(native.sync('brand',Path(tmp),lambda db:store.authorize(db,admin,'admin'))['status'],'synced')
            self.assertEqual(remote.record_fields['脚本'],'Generated script')
            self.assertEqual(remote.record_fields['脚本摘要'],hashlib.sha256(b'Generated script').hexdigest())
            self.assertEqual(remote.record_fields['状态'],'脚本待审核')
            self.assertEqual(remote.record_fields['审核目标版本'],'1')
            self.assertEqual(native.sync('brand',Path(tmp),lambda db:store.authorize(db,admin,'admin'))['status'],'idle')

            # Video belongs to the same task revision. Its delivery journal
            # must be separate from the already completed script delivery.
            media=Path(tmp)/'review.mp4';media.write_bytes(b'synthetic fixture bytes')
            media.chmod(0o600)
            artifact={'sha256':hashlib.sha256(media.read_bytes()).hexdigest(),
                      'location':str(media),'verification':'full_decode_passed'}
            with store.connect() as db:
                db.execute("UPDATE tasks SET state='awaiting_video_review',artifact=? WHERE project=? AND id=? AND revision=1",
                           (canonical(artifact),'brand','task_one'))
            remote.record_fields['状态']='脚本通过'
            statuses=[native.sync('brand',Path(tmp),lambda db:store.authorize(db,admin,'admin'))['status']
                      for _ in range(4)]
            self.assertEqual(statuses,['video_upload_in_progress']*3+['synced'])
            self.assertEqual(remote.record_fields['状态'],'视频待审核')
            self.assertEqual(remote.record_fields['视频'],[{'file_token':'fileFixture'}])
            self.assertEqual(remote.record_fields['视频摘要'],artifact['sha256'])
            self.assertEqual(native.sync('brand',Path(tmp),lambda db:store.authorize(db,admin,'admin'))['status'],'idle')

    def test_existing_text_status_is_not_silently_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=RuntimeStore.install(Path(tmp),'fixture','fixture-admin-password')
            admin=store.login('admin','fixture-admin-password')['token']
            store.put_project(admin,'brand',{'video_route':'deferred','credential_ref':'secret:fixture','billing_owner':'fixture'})
            binding={'tenant_key':'fixture_tenant','base_token':'bascnFixture','table_id':'tblFixture',
                     'fields':{'task':'fldTask','sku_id':'fldSkuId','script':'fldScript','source_revision':'fldSource'},
                     'submitters':['ou_submitter'],'reviewers':['ou_reviewer']}
            with store.connect() as db:save(db,'feishu:binding:brand',binding)
            context={'target':binding,'app_profile':{'app_id':'cli_fixture','credential_ref':'secret:fixture'},
                     'credential_revision':1,'configuration_sha256':'fixture'}
            remote=Remote()
            remote.schema.append({'field_id':'fldStatus','field_name':'状态','type':1})
            native=NativeSync(store,b'fixture-key')
            native.results=SimpleNamespace(context=lambda db,project:context,
                                           client=lambda value:remote,bridge=FeishuBridge(store))
            with self.assertRaisesRegex(RuntimeFault,'FEISHU_NATIVE_SCHEMA_CONFLICT'):
                native.prepare(admin,'brand')
            self.assertEqual(remote.writes,[])



if __name__=='__main__':unittest.main()
