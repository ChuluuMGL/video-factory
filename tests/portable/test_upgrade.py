import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'skills/video-factory-setup/scripts'))
from portable_n8n import Fault, digest, private_write
from n8n_workflows import probe_workflow
from portable_upgrade import preview, apply, bound


class FakeAPI:
    url = 'http://127.0.0.1:5678'
    def __init__(self):
        self.workflows = {}; self.puts = []; self.unknown = None; self.rows = []
    def request(self, method, path, value=None):
        if 'data-tables' in path:
            assert method == 'GET'
            return {'data': copy.deepcopy(self.rows)}
        wid = path.rsplit('/', 1)[1]
        if method == 'PUT':
            self.puts.append(wid)
            if self.unknown != 'before': self.workflows[wid].update(copy.deepcopy(value))
            if self.unknown: raise Fault('N8N_MUTATION_UNKNOWN_DO_NOT_RETRY')
        else: assert method == 'GET'
        return copy.deepcopy(self.workflows[wid])


class Upgrade(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.api = FakeAPI()
        private_write(self.root/'project-fixture.json', {'origin': self.api.url, 'base':'baseFixture','table':'tableFixture','app':'cli_fixture'})
        self.template = probe_workflow('baseFixture','tableFixture')
        # A prior owned template differs, but has the same preserved credential.
        old = copy.deepcopy(self.template); old['nodes'][0]['position'] = [20,20]
        self.binding = {'schema':1,'origin':self.api.url,'project':'fixture-probe','workflow':'wf1',
                        'template_sha256':digest(old),'credentials':{'httpCustomAuth':{'id':'cred1','name':'Existing credential'}},'verified_disabled':True}
        self.binding['owned_template'] = bound(old, self.binding)
        private_write(self.root/'binding-fixture-probe.json',self.binding)
        self.api.workflows['wf1']={'name':'My preserved workflow','active':False, **copy.deepcopy(self.binding['owned_template'])}
    def plan(self): return preview(self.api,self.root,'fixture')['plan']
    def test_update_preserves_ids_credentials_name_and_remains_disabled(self):
        plan=self.plan(); self.assertEqual(self.api.puts,[])
        result=apply(self.api,self.root,'fixture',plan)
        self.assertTrue(result['complete']); self.assertEqual(result['updated'],1)
        updated=json.loads((self.root/'binding-fixture-probe.json').read_text(encoding='utf-8'))
        self.assertEqual(updated['credentials'],self.binding['credentials'])
        self.assertEqual(updated['workflow'],'wf1'); self.assertEqual(updated['template_sha256'],digest(self.template))
        self.assertEqual(self.api.workflows['wf1']['name'],'My preserved workflow')
        self.assertFalse(self.api.workflows['wf1']['active'])
        self.assertEqual(apply(self.api,self.root,'fixture',plan)['updated'],0)
        self.assertEqual(preview(self.api,self.root,'fixture')['changed'],0)
    def test_unknown_success_reconciles_readback_without_second_put(self):
        plan=self.plan();self.api.unknown='after'
        with self.assertRaisesRegex(Fault,'UNKNOWN'):apply(self.api,self.root,'fixture',plan)
        with self.assertRaisesRegex(Fault,'RESUME'):self.plan()
        self.api.unknown=None
        self.assertTrue(apply(self.api,self.root,'fixture',plan)['complete'])
        self.assertEqual(self.api.puts,['wf1'])
    def test_unknown_no_write_never_retries(self):
        plan=self.plan();self.api.unknown='before'
        with self.assertRaises(Fault):apply(self.api,self.root,'fixture',plan)
        self.api.unknown=None
        with self.assertRaisesRegex(Fault,'NO_RETRY'):apply(self.api,self.root,'fixture',plan)
        self.assertEqual(self.api.puts,['wf1'])
    def test_paused_is_required_including_new_active_version_field(self):
        for key in ('active','activeVersionId','isArchived'):
            with self.subTest(key=key):
                self.api.workflows['wf1'][key]=True
                with self.assertRaisesRegex(Fault,'PAUSED'):self.plan()
                self.api.workflows['wf1'][key]=False
        self.assertEqual(self.api.puts,[])
    def test_live_edit_between_preview_and_apply_stops_before_put(self):
        plan=self.plan();self.api.workflows['wf1']['nodes'][0]['position']=[500,500]
        with self.assertRaisesRegex(Fault,'LIVE_CHANGED'):apply(self.api,self.root,'fixture',plan)
        self.assertEqual(self.api.puts,[])
    def test_foreign_live_edit_not_adopted(self):
        self.api.workflows['wf1']['nodes'][0]['position']=[500,500]
        with self.assertRaisesRegex(Fault,'MODIFIED'):self.plan()
    def test_scope_and_plan_hash_are_checked(self):
        plan=self.plan();p=self.root/('upgrade-'+plan+'.json');v=json.loads(p.read_text(encoding='utf-8'));v['origin']='https://other.invalid';private_write(p,v)
        with self.assertRaisesRegex(Fault,'PLAN_CHANGED'):apply(self.api,self.root,'fixture',plan)
        self.assertEqual(self.api.puts,[])
    def test_ledger_busy_or_pending_approval_blocks(self):
        for suffix,status in [('', 'running'),('-approval','awaiting'),('-approval','planning')]:
            p=self.root/('ledger-fixture'+suffix+'.json')
            private_write(p,{'origin':self.api.url,'project':'fixture'+suffix,'base':'baseFixture','task_table':'tableFixture','table':'ledger1','row':1})
            self.api.rows=[{'id':1,'status':status,'payload':'keep','result':'keep'}]
            with self.assertRaisesRegex(Fault,'CANCEL_PENDING'):self.plan()
            p.unlink()
        self.assertEqual(self.api.puts,[])
    def test_consumed_approval_receipt_is_retained(self):
        private_write(self.root/'ledger-fixture-approval.json',{'origin':self.api.url,'project':'fixture-approval','base':'baseFixture','task_table':'tableFixture','table':'ledger1','row':1})
        self.api.rows=[{'id':1,'status':'consumed','payload':'keep','result':'keep'}]
        before=copy.deepcopy(self.api.rows);plan=self.plan();apply(self.api,self.root,'fixture',plan)
        self.assertEqual(self.api.rows,before)
    def test_changed_ledger_after_preview_blocks(self):
        private_write(self.root/'ledger-fixture.json',{'origin':self.api.url,'project':'fixture','base':'baseFixture','task_table':'tableFixture','table':'ledger1','row':1})
        self.api.rows=[{'id':1,'status':'idle','result':'before'}];plan=self.plan()
        self.api.rows[0]['result']='after'
        with self.assertRaisesRegex(Fault,'LEDGER_CHANGED'):apply(self.api,self.root,'fixture',plan)
        self.assertEqual(self.api.puts,[])
    def test_all_targets_checked_before_any_mutation(self):
        other=copy.deepcopy(self.binding);other['project']='fixture-schema';other['workflow']='wf2'
        private_write(self.root/'binding-fixture-schema.json',other)
        self.api.workflows['wf2']=copy.deepcopy(self.api.workflows['wf1'])
        plan=self.plan();self.api.workflows['wf2']['active']=True
        with self.assertRaisesRegex(Fault,'PAUSED'):apply(self.api,self.root,'fixture',plan)
        self.assertEqual(self.api.puts,[])
    def test_owner_bundle_preserves_paid_mode_webhook_and_relay_reference(self):
        from portable_approval import confirmation_workflow
        policy={'app':'cli_fixture','tenant':'tenant_fixture','billing_owner':'ou_owner',
                'billing_account':'fixture-account','configuration':'fixture-v1'}
        event_root=self.root/'fixture-events';event_root.mkdir()
        event={'origin':self.api.url,'project':'fixture','base':'baseFixture','table':'tableFixture',
               'policy':policy,'verification_only':False,'hooks':{'script':'existing-owner-hook'},
               'workflows':{'confirm-script':{'project':'fixture-confirm-script-paid','workflow':'wf2'}}}
        private_write(event_root/'events.json',event)
        for name,table in [('fixture','execution'),('fixture-approval','approval')]:
            private_write(self.root/('ledger-'+name+'.json'),{'origin':self.api.url,'project':name,
                'base':'baseFixture','task_table':'tableFixture','table':table,'row':1})
        self.api.rows=[{'id':1,'status':'idle','payload':'','result':''}]
        template=confirmation_workflow('baseFixture','tableFixture','script',
            {'table':'execution','row':1},{'table':'approval','row':1},policy,'existing-owner-hook',verification_only=False)
        old=copy.deepcopy(template);old['nodes'][0]['position']=[20,20]
        binding={**copy.deepcopy(self.binding),'project':'fixture-confirm-script-paid','workflow':'wf2',
                 'template_sha256':digest(old)}
        binding['credentials'].update({'relay':{'id':'relay','name':'Relay'},'httpHeaderAuth':{'id':'model','name':'Model'}})
        binding['owned_template']=bound(old,binding)
        private_write(self.root/'binding-fixture-confirm-script-paid.json',binding)
        self.api.workflows['wf2']={'name':'Owner script','active':False,**copy.deepcopy(binding['owned_template'])}
        plan=self.plan();apply(self.api,self.root,'fixture',plan)
        updated=json.loads((self.root/'binding-fixture-confirm-script-paid.json').read_text(encoding='utf-8'))
        self.assertEqual(updated['owned_template'],bound(template,binding))
        self.assertEqual(updated['credentials'],binding['credentials'])
        self.assertEqual(json.loads((event_root/'events.json').read_text(encoding='utf-8')),event)
    def test_running_receiver_blocks_preview(self):
        from portable_events import receiver_lock
        event_root=self.root/'fixture-events';event_root.mkdir()
        with receiver_lock(event_root):
            with self.assertRaisesRegex(Fault,'ALREADY_RUNNING'):self.plan()
        self.assertEqual(self.api.puts,[])
    def test_symlink_binding_rejected(self):
        path=self.root/'binding-fixture-probe.json';moved=self.root/'saved.json';path.rename(moved)
        try:path.symlink_to(moved)
        except OSError:self.skipTest('symlink unavailable')
        with self.assertRaisesRegex(Fault,'SYMLINK'):self.plan()

if __name__=='__main__': unittest.main()
