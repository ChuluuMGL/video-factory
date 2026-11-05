"""Cloud-only employee identity, business ledger and read-only workflow adapter."""
import hashlib
import http.client
import json
import threading
import time
import unittest
from unittest.mock import patch

import test_local_business_flow as business
from video_factory.agent_api import AgentAPI, AgentServer
from video_factory.agent_plans import authorize
from video_factory.agent_script_plans import ScriptPlans
from video_factory.employee_access import EmployeeAccess, configuration, manage, observation
from video_factory.feishu_bridge import save, meta
from video_factory.runtime_store import RuntimeFault


class EmployeeTests(unittest.TestCase):
    def setUp(self):
        self.f = business.LocalBusinessFlowTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        f = self.f
        self.now = time.time(); self.plan = {'project':f.project,'app_id':'cli_fixture','binding':dict(business.BINDING)}
        # Use the fixture's already-bound application. Replacing its credential
        # reference would correctly invalidate the native writeback plan.
        self.config = {'schema':'vf-business-employee-v1','project':f.project,'tenant_key':'fixture_tenant',
                       'open_id':'ou_employee','expires_at':self.now+3600,'allow_plans':True}
        self.entry = self.make(); self.entry.start(); self.addCleanup(self.entry.close)
        self.api = AgentAPI(f.store,f.project,f.scheduler,plans_enabled=True,script_plans=f.plans,employee_access=self.entry)
        f.f.granted = True

    def make(self):
        f=self.f
        return EmployeeAccess(f.store,f.admin,self.plan,dict(self.config),f.key,clock=lambda:self.now,
            oauth_factory=lambda *_:f.f.oauth,client_factory=f.f.bridge.client_factory,
            script_factory=lambda store,project,**kw:ScriptPlans(store,project,jobs=f.jobs,**kw))

    def call(self, operation, token=None, body=None): return self.entry.call(operation,token,body or {},self.api)
    def pair(self):
        p=self.call('connect/start');self.now+=6
        result=self.call('connect/poll',p['pair_token']);self.assertEqual(result['subject'],self.entry.subject)
        self.assertNotIn(self.f.f.user_token,json.dumps(result));return result['token']

    def change(self, action, request_id=None, version=None):
        current=manage(self.f.store,self.f.admin,self.f.project,'status')
        return manage(self.f.store,self.f.admin,self.f.project,action,expected_version=current['version'] if version is None else version,
                      request_id=request_id or action+'-request-'+str(current['version']).zfill(16))

    def test_employee_real_business_reject_revise_and_owner_gate(self):
        f=self.f;token=self.pair()
        caps=self.call('capabilities',token);self.assertEqual(caps['approval_authority'],'local_administrator')
        def generate(revision):
            plan=self.call('script/prepare-record',token,{'record':'recFixture','request_id':'employee_revision_'+str(revision),'expected_revision':revision-1})
            selected={k:plan[k] for k in ('plan_id','plan_sha256')}
            with self.assertRaisesRegex(RuntimeFault,'OWNER_APPROVAL_REQUIRED'):self.call('script/submit',token,selected)
            f.plans.owner(f.admin,'approve',selected['plan_id'],selected['plan_sha256'])
            self.call('script/submit',token,selected)
            for _ in range(3):f.tick()
            self.assertEqual(f.state(),'awaiting_script_review')
            self.assertEqual(self.call('script/content',token,selected)['revision'],revision)
            return plan
        generate(1)
        f.remote.record_fields.update({'状态':['脚本退回'],'审核意见':'Shorten closing scene'})
        f.event('脚本待审核','脚本退回','employee_reject');f.tick();self.assertEqual(f.state(),'rejected')
        f.remote.record_fields['source_revision']='source_two';plan=generate(2)
        self.assertIn('Shorten closing scene',f.provider.calls[-1][1]);self.assertEqual(f.video.calls,0)
        # Owner can revoke the next plan; employee cannot undo that decision.
        f.remote.record_fields.update({'状态':['脚本退回'],'审核意见':'Keep original timing'})
        f.event('脚本待审核','脚本退回','employee_reject_two');f.tick();f.remote.record_fields['source_revision']='source_three'
        proposal=self.call('script/prepare-record',token,{'record':'recFixture','request_id':'employee_revision_three','expected_revision':2})
        f.plans.owner(f.admin,'revoke',proposal['plan_id'],proposal['plan_sha256'])
        with self.assertRaises(RuntimeFault):self.call('script/submit',token,{k:proposal[k] for k in ('plan_id','plan_sha256')})
        self.assertEqual(len(f.provider.calls),2)
        with f.store.connect() as db:
            rows=db.execute("SELECT actor FROM audit WHERE action='agent_script_plan_prepared'").fetchall()
            self.assertTrue(rows and all(r['actor'].startswith('employee:'+self.entry.subject+':') for r in rows))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM tasks WHERE project=?',(f.project,)).fetchone()[0],2)

    def test_identity_tenant_scope_and_owner_routes_fail_closed(self):
        self.f.f.identity['tenant_key']='wrong'
        with self.assertRaisesRegex(RuntimeFault,'IDENTITY_DENIED'):self.pair()
        self.f.f.identity['tenant_key']='fixture_tenant';self.f.f.identity['open_id']='ou_outsider'
        with self.assertRaisesRegex(RuntimeFault,'IDENTITY_DENIED'):self.pair()
        self.f.f.identity['open_id']='ou_employee';token=self.pair()
        for operation in ('approve-script','credentials','workflow/apply','shell'):
            with self.assertRaisesRegex(RuntimeFault,'OPERATION_DENIED'):self.call(operation,token)
        with self.assertRaisesRegex(RuntimeFault,'FIELDS_INVALID'):self.call('status',token,{'project':'production'})
        with self.assertRaisesRegex(RuntimeFault,'AUTH_'):manage(self.f.store,token,self.f.project,'status')
        # Employee grant cannot enter the owner's Agent API and borrow its Feishu identity.
        with self.assertRaisesRegex(RuntimeFault,'EMPLOYEE_CONTEXT_DENIED'):
            self.api.call('/v1/agent/status',token,{'project':self.f.project})
        with self.f.store.connect() as db:
            with self.assertRaisesRegex(RuntimeFault,'EMPLOYEE_CONTEXT_DENIED'):authorize(db,token,self.f.project)

    def test_suspend_resume_restart_pending_expiry_and_audit(self):
        token=self.pair();pending=self.call('connect/start')
        self.change('suspend',request_id='suspend-durable-request')
        with self.assertRaisesRegex(RuntimeFault,'SUSPENDED'):self.call('status',token)
        self.entry.close();self.entry=self.make();self.entry.start();self.api.employee_access=self.entry
        self.addCleanup(self.entry.close)
        self.assertTrue(manage(self.f.store,self.f.admin,self.f.project,'status')['suspended'])
        self.change('resume')
        with self.assertRaisesRegex(RuntimeFault,'SESSION_EXPIRED'):self.call('status',token)
        with self.assertRaisesRegex(RuntimeFault,'PAIRING_EXPIRED'):self.call('connect/poll',pending['pair_token'])
        token=self.pair();self.assertEqual(self.call('status',token)['project'],self.f.project)
        self.call('disconnect',token)
        with self.assertRaisesRegex(RuntimeFault,'SESSION_EXPIRED'):self.call('status',token)
        self.now=self.config['expires_at']+1
        with self.assertRaisesRegex(RuntimeFault,'WINDOW_EXPIRED'):self.call('connect/start')

    def test_pairing_race_replay_and_policy_binding(self):
        pending=self.call('connect/start');self.now+=6
        identity=self.entry.client_factory
        def racing(token):
            client=identity(token);original=client.identity
            def read():
                self.change('suspend');self.change('resume');return original()
            client.identity=read;return client
        self.entry.client_factory=racing
        with self.assertRaisesRegex(RuntimeFault,'PAIRING_EXPIRED'):self.call('connect/poll',pending['pair_token'])
        self.entry.client_factory=identity
        prior=manage(self.f.store,self.f.admin,self.f.project,'status')['version']
        self.change('suspend',request_id='suspend-once-request',version=prior);self.change('resume')
        self.assertTrue(self.change('suspend',request_id='suspend-once-request',version=prior)['replayed'])
        self.assertFalse(manage(self.f.store,self.f.admin,self.f.project,'status')['suspended'])
        self.config['open_id']='ou_replacement'
        replacement=self.make()
        with self.assertRaisesRegex(RuntimeFault,'BINDING_CHANGED'):replacement.start()

    def test_readonly_employee_and_strict_snapshot(self):
        self.entry.close()
        # A separate fresh policy in a separate fixture would be used by an owner;
        # here remove only this synthetic policy to test the distinct read-only profile.
        with self.f.store.connect() as db:db.execute('DELETE FROM meta WHERE key=?',('employee:policy:'+self.f.project,))
        self.config['allow_plans']=False;self.entry=self.make();self.entry.start();self.addCleanup(self.entry.close)
        token=self.pair()
        with self.assertRaisesRegex(RuntimeFault,'OPERATION_DENIED'):self.call('script/prepare-record',token,{'record':'recFixture'})
        snapshot=self.f.root/'snapshot.json'
        value={'schema':'vf-workflow-observation-v1','project':self.f.project,'observed_at':self.now,
               'environment':'production-readonly','workflows':[{'id':'wf_fixture','active':True,
                'version_id':'revision-one','source_sha256':'a'*64,'latest_execution':{'id':'123','status':'error'}}]}
        snapshot.write_text(json.dumps(value));snapshot.chmod(0o600)
        source={'path':str(snapshot),'sha256':hashlib.sha256(snapshot.read_bytes()).hexdigest(),'workflow_ids':['wf_fixture']}
        self.entry.config['workflow_source']=source
        result=self.call('workflows',token);self.assertTrue(result['read_only']);self.assertFalse(result['live'])
        self.assertEqual(result['workflows'][0]['source_sha256'],'a'*64)
        self.now+=901
        with self.assertRaisesRegex(RuntimeFault,'STALE'):self.call('workflows',token)
        self.now-=901;snapshot.write_text(json.dumps({**value,'credentials':'must-not-leak'}))
        with self.assertRaisesRegex(RuntimeFault,'CHANGED'):self.call('workflows',token)
        source['sha256']=hashlib.sha256(snapshot.read_bytes()).hexdigest()
        with self.assertRaisesRegex(RuntimeFault,'INVALID'):self.call('workflows',token)

    def test_http_origin_and_exact_route_boundary(self):
        server=AgentServer(('127.0.0.1',0),self.api);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            conn.request('POST','/v1/employee/connect/start',body='{}',headers={'Content-Type':'application/json','Origin':'http://evil.invalid'})
            response=conn.getresponse();self.assertEqual(response.status,403);response.read();conn.close()
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            conn.request('POST','/v1/employee/connect/start',body='{}',headers={'Content-Type':'application/json'})
            response=conn.getresponse();self.assertEqual(response.status,200);self.assertIn('user_code',json.loads(response.read()));conn.close()
        finally:server.shutdown();server.server_close();thread.join(5)


if __name__=='__main__':unittest.main()
