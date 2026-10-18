from urllib.error import HTTPError, URLError
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'skills/video-factory-setup/scripts'))
from portable_n8n import Fault
from portable_approval import plan_workflow, confirmation_workflow, policy_checked, set_approval_active, promote_approval, cancel_plan
from portable_events import Inbox, reduce_verified, save_secrets, load_secrets, receiver_lock

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
    def test_receiver_singleton_releases_after_exit(self):
        with tempfile.TemporaryDirectory() as d:
            with receiver_lock(d):
                with self.assertRaisesRegex(Fault,'ALREADY_RUNNING'):
                    with receiver_lock(d): self.fail('duplicate receiver')
            with receiver_lock(d): pass

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

    def test_delivery_failure_is_classified_without_exposing_or_retrying(self):
        cases=[(HTTPError('https://secret.invalid/token',403,'secret-body',{},None),
                {'error':'http_response','http_status':403}),
               (URLError('secret-connection-details'),{'error':'transport_failure'}),
               (json.JSONDecodeError('secret-response','secret',0),{'error':'invalid_json_response'}),
               (Fault('secret-configuration'),{'error':'local_validation_failure'})]
        for error,details in cases:
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as d:
                inbox=Inbox(Path(d)/'events');inbox.enqueue(*reduce_verified(event(),CONFIG))
                calls=[]
                def fail(*args):calls.append(1);raise error
                result=inbox.dispatch_one(CONFIG,'synthetic',fail)
                self.assertEqual(result,{'status':'needs_reconciliation',
                    'receipt':{'delivery':'unknown_no_retry',**details}})
                self.assertNotIn('secret',json.dumps(result))
                self.assertEqual(inbox.dispatch_one(CONFIG,'synthetic',fail),{'status':'idle'})
                self.assertEqual(calls,[1])

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

    def test_live_readback_reports_exact_guard_without_exposing_values(self):
        w=confirmation_workflow('baseFixture','tableFixture','script',{'table':'exec','row':1},{'table':'plans','row':1},POLICY,'confirm')
        source=next(n['parameters']['jsCode'] for n in w['nodes'] if n['name']=='Select task')
        saved={'display':'private-display','plan':{'id':'vf-fixture','record':'recFixture',
            'expires_at':9999999999999,'snapshot':{'商品资料':'private-product','参考图附件':[]}}}
        row={'record_id':'recFixture','fields':{'状态':'费用待确认','费用确认编号':'vf-fixture',
            '生成计划':'private-display','商品资料':'private-product'}}
        cases=[]
        for field,value,error in [('record_id','recOther','APPROVAL_RECORD_CHANGED'),
                ('状态','脚本通过','APPROVAL_STATUS_CHANGED'),
                ('费用确认编号','','APPROVAL_CONFIRMATION_NOT_CURRENT'),
                ('生成计划','edited','APPROVAL_DISPLAY_CHANGED'),
                ('商品资料','edited','APPROVED_INPUT_CHANGED')]:
            changed=copy.deepcopy(row)
            if field=='record_id':changed[field]=value
            else:changed['fields'][field]=value
            cases.append({'saved':saved,'row':changed,'error':error})
        expired=copy.deepcopy(saved);expired['plan']['expires_at']=0
        cases += [{'saved':expired,'row':row,'error':'APPROVAL_EXPIRED'},
                  {'saved':saved,'row':row,'error':None}]
        js="""const F=(async()=>{}).constructor,assert=require('node:assert/strict');
const fn=new F('$json','$','$execution',process.argv[1]);
(async()=>{for(const c of JSON.parse(process.argv[2])) {
 let error=null,result;
 try { result=await fn({code:0,data:{record:c.row}},()=>({first:()=>({json:c.saved})}),{id:'fixture'}); }
 catch(e){error=e.message;}
 assert.equal(error,c.error);
 if(!error) assert.equal(result[0].json.id,'recFixture');
}})().catch(e=>{console.error(e);process.exit(1)});"""
        result=subprocess.run(['node','-e',js,source,json.dumps(cases)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

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
        fixture={'payload':json.dumps({'plan':{'policy':POLICY,'flow':'script','max_submissions':1,'automatic_retry':False,'expires_at':0,'verification_only':False}})}
        js="const F=(async()=>{}).constructor;new F('$json',process.argv[1])(JSON.parse(process.argv[2])).then(()=>process.exit(9)).catch(e=>{if(e.message!=='APPROVAL_EXPIRED')process.exit(8)});"
        result=subprocess.run(['node','-e',js,source,json.dumps(fixture)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.events=self.root/'fixture-events';self.events.mkdir()
        self.config={**copy.deepcopy(CONFIG),'project':'fixture','verification_only':True,
            'workflows':{'confirm-script':{'workflow':'confirm','project':'fixture-confirm'}}}
        self.save('fixture-events/events.json',self.config)
        self.template={'nodes':[{'name':'Authorization test receipt','notes':'中文审核回执'}],'connections':{},'settings':{}}
        self.save('binding-fixture-confirm.json',{'workflow':'confirm','owned_template':self.template})
        self.flows={'confirm':{'id':'confirm','active':False,**copy.deepcopy(self.template)}}
        self.calls=[];self.rows={'execution':[{'status':'idle'}], 'approval':[{
            'id':1,'status':'consumed','result':json.dumps({'event':'evtFixture'}),'payload':json.dumps({'plan':{'id':'plan-one'}})}]}
        self.api=type('FakeAPI',(),{'url':CONFIG['origin'],'request':lambda _,*a:self.request(*a)})()

    def save(self,path,value):
        (self.root/path).write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')

    def request(self,method,path,body=None):
        self.calls.append((method,path,body))
        if '/workflows/' in path:
            key=path.split('/workflows/')[1].split('/')[0]
            if method=='GET':return copy.deepcopy(self.flows[key])
            self.flows[key]['active']=path.endswith('/activate');return {}
        if '/data-tables/' in path:
            key=path.split('/data-tables/')[1].split('/')[0]
            if method=='GET':return {'data':copy.deepcopy(self.rows[key])}
            self.rows[key][0].update(body['data']);return copy.deepcopy(self.rows[key])
        raise AssertionError(path)

    def test_activation_checks_template_and_disable_can_stop_drift(self):
        self.flows['confirm']['nodes'].append({'name':'unexpected'})
        with self.assertRaisesRegex(Fault,'CHANGED_REVIEW'):set_approval_active(self.api,self.root,'fixture',True)
        self.assertFalse(any(c[0]=='POST' for c in self.calls))
        self.flows['confirm']['active']=True
        self.assertFalse(set_approval_active(self.api,self.root,'fixture',False)['active'])
        self.assertFalse(self.flows['confirm']['active'])

    def test_activation_rolls_back_partial_failure(self):
        self.config['workflows']['plan-script']={'workflow':'plan','project':'fixture-plan'}
        self.save('fixture-events/events.json',self.config)
        self.flows['plan']={'id':'plan','active':False,**copy.deepcopy(self.template)}
        self.save('binding-fixture-plan.json',{'workflow':'plan','owned_template':self.template})
        original=self.api.request
        def fail(method,path,body=None):
            if path.endswith('plan/activate'):
                original(method,path,body)
                raise Fault('INJECTED_FAILURE')
            return original(method,path,body)
        self.api.request=fail
        with self.assertRaisesRegex(Fault,'INJECTED'):set_approval_active(self.api,self.root,'fixture',True)
        self.assertFalse(self.flows['confirm']['active'])
        self.assertFalse(self.flows['plan']['active'])

    def test_promotion_requires_real_receipt_and_resumes_without_recharging(self):
        with self.assertRaisesRegex(Fault,'REAL_OWNER'):promote_approval(self.api,self.root,'fixture')
        inbox=Inbox(self.events);inbox.enqueue(*reduce_verified(event(),self.config))
        inbox.dispatch_one(self.config,'synthetic',lambda *_:{'authorization':'verified','provider_requests':0,'verification_only':True})
        save_secrets(self.events,'synthetic-app','synthetic-relay')
        self.save('credentials-fixture.json',{'roles':{'feishu':{},'script':{}}})
        created={};failed=[False]
        def provision(api,root,name,w,creds):
            if 'confirm-script-paid' in name and not failed[0]:
                failed[0]=True;raise Fault('INJECTED_IMPORT_INTERRUPTION')
            created[name]=copy.deepcopy(w)
            self.flows[name]={'id':name,'active':False,**copy.deepcopy(w)}
            return {'project':name,'workflow':name}
        def ledger(api,root,name,*_):return {'table':'approval' if name.endswith('-approval') else 'execution','row':1}
        with patch('portable_n8n.ensure_ledger',ledger),patch('portable_n8n.provision',provision):
            with self.assertRaisesRegex(Fault,'IMPORT_INTERRUPTION'):promote_approval(self.api,self.root,'fixture')
            self.assertEqual(self.rows['approval'][0]['status'],'idle')
            self.assertFalse(json.loads((self.events/'promotion.json').read_text())['complete'])
            result=promote_approval(self.api,self.root,'fixture')
        self.assertTrue(result['promoted']);self.assertFalse(result['active'])
        current=json.loads((self.events/'events.json').read_text())
        self.assertFalse(current['verification_only']);self.assertTrue(current['hooks']['script'].endswith('-paid'))
        self.assertTrue(all(not w['active'] for w in created.values()))
        self.assertEqual(created['fixture-plan-script-paid']['nodes'][0]['type'],'n8n-nodes-base.scheduleTrigger')
        self.flows['fixture-confirm-script-paid']['active']=True
        self.assertTrue(promote_approval(self.api,self.root,'fixture')['active'])

    def test_cancel_refuses_consumed_or_wrong_plan(self):
        with patch('portable_n8n.ensure_ledger',return_value={'table':'approval','row':1}):
            with self.assertRaisesRegex(Fault,'CONSUMED'):cancel_plan(self.api,self.root,'fixture','plan-one')
            self.rows['approval'][0]['status']='awaiting'
            with self.assertRaisesRegex(Fault,'ID_MISMATCH'):cancel_plan(self.api,self.root,'fixture','wrong')
            self.assertEqual(cancel_plan(self.api,self.root,'fixture','plan-one')['provider_requests'],0)
            self.assertEqual(self.rows['approval'][0]['status'],'idle')


if __name__=='__main__':unittest.main()
