"""Synthetic original-row setup and draft delivery; run in cloud CI."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from video_factory.feishu_bridge import FeishuBridge, save
from video_factory.feishu_native_sync import NativeSync
from video_factory.runtime_store import RuntimeStore


class Remote:
    def __init__(self):
        self.schema=[{'field_id':field_id,'field_name':name,'type':1} for name,field_id in (
            ('任务编号','fldTask'),('SKU','fldSku'),('脚本','fldScript'),('来源版本','fldSource'))]
        self.record_fields={'任务编号':'task_one','SKU':'sku_one','脚本':'Initial brief','来源版本':'source_one'}
        self.subscription=False;self.writes=[]
    def fields(self,base,table):return list(self.schema)
    def create_field(self,base,table,name,kind,ticket):
        field_id='fldAdded'+str(len(self.schema))
        self.schema.append({'field_id':field_id,'field_name':name,'type':kind})
        self.writes.append(('field',name))
        return field_id
    def base_subscription_status(self,base):return self.subscription
    def subscribe_base(self,base):self.subscription=True;self.writes.append(('subscribe',base))
    def find_task(self,base,table,name,task):
        assert (name,task)==('任务编号','task_one')
        return self.record(base,table,'recFixture')
    def record(self,base,table,record):return {'record_id':record,'fields':dict(self.record_fields)}
    def update_record(self,base,table,record,fields):
        assert record=='recFixture'
        self.record_fields.update(fields);self.writes.append(('record',fields))
        return record


class NativeSyncTests(unittest.TestCase):
    def test_enable_adds_fields_and_syncs_original_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=RuntimeStore.install(Path(tmp),'fixture','fixture-admin-password')
            admin=store.login('admin','fixture-admin-password')['token']
            store.put_project(admin,'brand',{'video_route':'deferred','credential_ref':'secret:fixture','billing_owner':'fixture'})
            store.create_task(admin,'brand','task_one',{'sku_id':'sku_one','script':'Generated script','source_revision':'source_one'})
            target={'tenant_key':'fixture_tenant','base_token':'bascnFixture','table_id':'tblFixture',
                    'fields':{'task':'fldTask','sku_id':'fldSku','script':'fldScript','source_revision':'fldSource'}}
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
            self.assertEqual(len([x for x in remote.writes if x[0]=='field']),5)
            self.assertEqual(native.sync('brand',Path(tmp),lambda db:store.authorize(db,admin,'admin'))['status'],'synced')
            self.assertEqual(remote.record_fields['脚本'],'Generated script')
            self.assertEqual(remote.record_fields['状态'],'脚本待审核')
            self.assertEqual(remote.record_fields['审核目标版本'],'1')
            self.assertEqual(native.sync('brand',Path(tmp),lambda db:store.authorize(db,admin,'admin'))['status'],'idle')


if __name__=='__main__':unittest.main()
