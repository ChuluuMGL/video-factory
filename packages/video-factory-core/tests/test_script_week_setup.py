import copy
import unittest
from unittest.mock import Mock
from test_script_jobs import ScriptTests
from video_factory.feishu_bridge import meta,save
from video_factory.runtime_store import RuntimeFault
from video_factory.script_week_setup import bind_copy_table

class SetupTests(unittest.TestCase):
    setUp=ScriptTests.setUp
    tearDown=ScriptTests.tearDown
    def config(self):
        with self.f.store.connect() as db:
            binding=meta(db,'feishu:binding:'+self.f.project)
            setup=meta(db,'setup:project:'+self.f.project)
            setup['configuration'].update(organization={'id':'fixture_org'},deployment={'id':'fixture_deploy'})
            setup['configuration']['project'].update(base_target=binding['base_token'],video_route='deferred')
            save(db,'setup:project:'+self.f.project,setup)
            db.execute('DELETE FROM meta WHERE key=?',('feishu:binding:'+self.f.project,))
        client=Mock();client.fields.return_value=[{'field_id':fid,'field_name':name,'type':1} for name,fid in binding['fields'].items()]
        return dict(verified_binding=binding,verified_setup=copy.deepcopy(setup),table='tblNewFixture',
                    fields=binding['fields'],client=client,proof_sha256='a'*64)
    def test_same_customer_copy_preserves_roles_without_employee_session(self):
        args=self.config();r=bind_copy_table(self.f.store,self.f.admin,self.f.project,**args)
        self.assertFalse(r['employee_login_created'])
        with self.f.store.connect() as db:
            actual=meta(db,'feishu:binding:'+self.f.project)
        self.assertEqual(actual['reviewers'],args['verified_binding']['reviewers'])
        self.assertEqual(actual['base_token'],args['verified_binding']['base_token'])
        self.assertTrue(bind_copy_table(self.f.store,self.f.admin,self.f.project,**args)['replayed'])
    def test_changed_customer_or_production_base_refused_before_network(self):
        args=self.config();args['verified_binding']['base_token']='basOtherFixture'
        with self.assertRaises(RuntimeFault):bind_copy_table(self.f.store,self.f.admin,self.f.project,**args)
        args['client'].fields.assert_not_called()
    def test_admin_and_proof_required(self):
        args=self.config()
        with self.assertRaises(RuntimeFault):bind_copy_table(self.f.store,'invalid',self.f.project,**args)
        with self.assertRaises(RuntimeFault):bind_copy_table(self.f.store,self.f.admin,self.f.project,**{**args,'proof_sha256':''})
    def test_schema_change_and_same_table_denied(self):
        args=self.config();args['client'].fields.return_value=[]
        with self.assertRaises(RuntimeFault):bind_copy_table(self.f.store,self.f.admin,self.f.project,**args)
        args['table']=args['verified_binding']['table_id']
        with self.assertRaises(RuntimeFault):bind_copy_table(self.f.store,self.f.admin,self.f.project,**args)

if __name__=='__main__':unittest.main()
