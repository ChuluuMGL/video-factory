import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'skills/video-factory-setup/scripts'))
from portable_n8n import Fault
from portable_approval import plan_workflow, confirmation_workflow, policy_checked
from portable_events import Inbox, reduce_verified, save_secrets, load_secrets

POLICY={'app':'cli_fixture','tenant':'tenant_fixture','billing_owner':'ou_owner',
        'billing_account':'test-account','configuration':'fixture-v1'}
CONFIG={'policy':POLICY,'base':'baseFixture','table':'tableFixture','confirmation_field':'fldConfirm',
        'origin':'http://127.0.0.1:5678','hooks':{'script':'fixture-confirm'}}


def event():
    return {'schema':'2.0','header':{'event_id':'evtFixture','event_type':'drive.file.bitable_record_changed_v1',
        'app_id':'cli_fixture','tenant_key':'tenant_fixture'},'event':{'file_type':'bitable',
        'file_token':'baseFixture','table_id':'tableFixture','operator_id':{'open_id':'ou_owner'},
        'action_list':[{'record_id':'recFixture','action':'record_edited','after_value':[
            {'field_id':'fldConfirm','field_value':json.dumps([{'type':'text','text':'vf-fixture-v1-script-42'}])}]}]}}


class Approval(unittest.TestCase):
    def test_relay_scope_and_actor_fail_closed(self):
        self.assertIsNotNone(reduce_verified(event(),CONFIG))
        for obj,key,value in [('header','tenant_key','other'),('header','app_id','cli_other'),
                              ('event','file_token','other'),('event','table_id','other'),
                              ('event','operator_id',{'open_id':'ou_other'})]:
            e=event();e[obj][key]=value
            self.assertIsNone(reduce_verified(e,CONFIG))
        e=event();e['event']['action_list'][0]['after_value'][0]['field_id']='unrelated'
        self.assertIsNone(reduce_verified(e,CONFIG))
        e=event();e['event']['action_list'][0]['after_value'][0]['field_value']='vf-older-config-script-42'
        self.assertIsNone(reduce_verified(e,CONFIG))

    def test_sdk_inbox_deduplicates_and_preserves_unknown_delivery(self):
        with tempfile.TemporaryDirectory() as d:
            inbox=Inbox(Path(d)/'events');flow,e=reduce_verified(event(),CONFIG)
            self.assertTrue(inbox.enqueue(flow,e));self.assertFalse(inbox.enqueue(flow,e))
            changed=copy.deepcopy(e);changed['event']['action_list'][0]['record_id']='recOther'
            with self.assertRaisesRegex(Fault,'EVENT_ID_CONFLICT'):inbox.enqueue(flow,changed)
            calls=[]
            def unknown(url,payload):calls.append(url);raise TimeoutError()
            self.assertEqual(inbox.dispatch_one(CONFIG,'fake',unknown)['status'],'needs_reconciliation')
            self.assertEqual(Inbox(Path(d)/'events').dispatch_one(CONFIG,'fake',unknown)['status'],'idle')
            self.assertEqual(len(calls),1)

    def test_dispatch_intent_after_crash_never_replayed(self):
        with tempfile.TemporaryDirectory() as d:
            inbox=Inbox(Path(d)/'events');inbox.enqueue(*reduce_verified(event(),CONFIG))
            with inbox.connect() as db:db.execute("UPDATE events SET status='dispatch_intent'")
            self.assertEqual(inbox.dispatch_one(CONFIG,'fake',lambda *_:self.fail('replayed'))['status'],'idle')

    def test_receipts_require_verified_authorization(self):
        for response,expected in [({'ok':True},'needs_reconciliation'),
                                   ({'authorization':'verified','provider_requests':0},'done')]:
            with tempfile.TemporaryDirectory() as d:
                inbox=Inbox(Path(d)/'events');inbox.enqueue(*reduce_verified(event(),CONFIG))
                self.assertEqual(inbox.dispatch_one(CONFIG,'fake',lambda *_:response)['status'],expected)

    def test_encrypted_secrets_and_no_silent_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'events';save_secrets(root,'synthetic-app-secret','synthetic-relay-secret')
            self.assertEqual(load_secrets(root)['app_secret'],'synthetic-app-secret')
            for p in root.iterdir():self.assertNotIn('synthetic-app-secret',p.read_text())
            with self.assertRaisesRegex(Fault,'EXIST'):save_secrets(root,'new','new')

    def test_production_gate_has_no_manual_bypass_and_separate_relay_credential(self):
        w=confirmation_workflow('baseFixture','tableFixture','script',{'table':'exec','row':1},{'table':'plans','row':1},POLICY,'confirm')
        self.assertNotIn('n8n-nodes-base.manualTrigger',[n['type'] for n in w['nodes']])
        hook=w['nodes'][0];self.assertEqual(hook['credentials']['httpHeaderAuth']['id'],'RELAY_UNBOUND')
        paid=next(n for n in w['nodes'] if n['name']=='Generate script')
        self.assertEqual(paid['credentials']['httpHeaderAuth']['id'],'UNBOUND')
        self.assertFalse(paid['retryOnFail']);self.assertFalse(w['active'])

    def test_zero_charge_acceptance_has_no_model_or_reservation_nodes(self):
        w=confirmation_workflow('baseFixture','tableFixture','video-submit',{'table':'exec','row':1},{'table':'plans','row':1},POLICY,'check',verification_only=True)
        self.assertFalse(any(n['name'] in ('Submit video once','Generate script','Reserve task') for n in w['nodes']))

    def test_parse_every_approval_code_node(self):
        workflows=[]
        for flow in ('script','video-submit'):
            workflows.append(plan_workflow('baseFixture','tableFixture',flow,{'table':'plans','row':1},POLICY))
            workflows.append(confirmation_workflow('baseFixture','tableFixture',flow,{'table':'exec','row':1},{'table':'plans','row':1},POLICY,'confirm-'+flow))
        codes=[n['parameters']['jsCode'] for w in workflows for n in w['nodes'] if n['type']=='n8n-nodes-base.code']
        result=subprocess.run(['node','-e',"let s='';process.stdin.on('data',c=>s+=c);process.stdin.on('end',()=>{const F=(async()=>{}).constructor;for(const c of JSON.parse(s))new F('$json','$input','$','$execution',c);});"],input=json.dumps(codes),text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_expired_policy_and_unrelated_edits_do_not_approve(self):
        w=confirmation_workflow('baseFixture','tableFixture','script',{'table':'exec','row':1},{'table':'plans','row':1},POLICY,'confirm')
        source=next(n['parameters']['jsCode'] for n in w['nodes'] if n['name']=='Check protected plan')
        fixture={'payload':json.dumps({'plan':{'policy':POLICY,'flow':'script','max_submissions':1,'automatic_retry':False,'expires_at':0}})}
        js="const F=(async()=>{}).constructor;new F('$json',process.argv[1])(JSON.parse(process.argv[2])).then(()=>process.exit(9)).catch(e=>{if(e.message!=='APPROVAL_EXPIRED')process.exit(8)});"
        result=subprocess.run(['node','-e',js,source,json.dumps(fixture)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)


if __name__=='__main__':unittest.main()
