"""Cloud-only real loopback startup/client tests; no provider or Feishu network."""
import json
import http.client
from pathlib import Path
from types import SimpleNamespace
import threading
import time
import unittest
from unittest.mock import patch

import test_local_setup as fixture
from video_factory.feishu_bridge import save
from video_factory.local_run import agent_call, capabilities, run, start_plan
from video_factory.runtime_store import RuntimeFault, exclusive_write


class EntryTests(unittest.TestCase):
    def setUp(self):
        fixture.SetupTests.setUp(self)
        fixture.SetupTests.put(self)
        with self.store.connect() as db: save(db, 'feishu:binding:brand_one', self.plan['binding'])
        exclusive_write(self.state / 'master.key', self.key)
        self.args = SimpleNamespace(allow_plans=False,execute_approved=False,hours=2,dedicated_app=False,
                                    port=0,user_token_file=None,expect_plan=None)
        self.args.expect_plan = self.inspect()['plan_sha256']

    def inspect(self, **kwargs): return start_plan(self.store,self.admin,self.plan,self.key,**kwargs)

    def keys(self):
        with self.store.connect() as db: return db.execute("SELECT COUNT(*) FROM meta WHERE key LIKE 'automation:key:%'").fetchone()[0]

    def test_inspect_is_non_executing_and_binds_hashed_job_approval(self):
        before=self.inspect(); self.assertEqual(before['agent_scope'],'agent_read'); self.assertEqual(self.keys(),0)
        with self.store.connect() as db:
            save(db,'worker:'+'f'*64,{'plan':{'project':'brand_one'},'expires_at':123})
        changed=self.inspect(); self.assertNotEqual(before['plan_sha256'],changed['plan_sha256'])
        self.assertEqual(len(changed['jobs']),1)
        with self.assertRaisesRegex(RuntimeFault,'START_PLAN_CHANGED'):
            run(self.store,self.admin,self.plan,self.key,self.args,emit=lambda _:None)
        self.assertEqual(self.keys(),0)

    def test_paid_mode_needs_enabled_native_and_bound_plan(self):
        with self.assertRaisesRegex(RuntimeFault,'NATIVE_ENABLE_REQUIRED'): self.inspect(execute=True)
        with self.assertRaisesRegex(RuntimeFault,'MAX_TWO_HOURS'): self.inspect(hours=3)
        with self.store.connect() as db: save(db,'feishu:binding:brand_one',{})
        with self.assertRaisesRegex(RuntimeFault,'BINDING_REQUIRED'): self.inspect()

    def test_partial_grant_failure_revokes_first_key(self):
        with patch('video_factory.local_run.automation.issue_execution',side_effect=RuntimeFault('INJECTED')):
            with self.assertRaisesRegex(RuntimeFault,'INJECTED'):
                with capabilities(self.store,self.admin,'brand_one',2,True,True): pass
        self.assertEqual(self.keys(),0)

    def test_preflight_drift_revokes_keys_without_opening_server(self):
        def serve_changed(*args,**kwargs):
            with self.store.connect() as db: save(db,'feishu:binding:brand_one',{})
            kwargs['on_locked']()
            self.fail('drift must reject')
        with patch('video_factory.local_run.serve',side_effect=serve_changed):
            with self.assertRaisesRegex(RuntimeFault,'BINDING_REQUIRED'):
                run(self.store,self.admin,self.plan,self.key,self.args,emit=lambda _:None)
        self.assertEqual(self.keys(),0); self.assertEqual(list(self.state.glob('agent-session-*')),[])

    def test_real_http_client_stop_restart_expiry_and_no_owner_route(self):
        for attempt in range(2):
            stop=threading.Event(); ready=threading.Event(); output=[]; errors=[]
            def emit(value):
                output.append(value)
                if 'connection' in value: ready.set()
            def serve_thread():
                try: run(self.store,self.admin,self.plan,self.key,self.args,emit=emit,stop=stop)
                except BaseException as error: errors.append(error); ready.set()
            thread=threading.Thread(target=serve_thread);thread.start()
            try:
                self.assertTrue(ready.wait(5)); self.assertEqual(errors,[])
                connection=Path(output[0]['connection']); token=(connection.parent/'agent.token').read_text()
                result=agent_call(connection,'status',{})
                self.assertIn('project',result)
                self.assertNotIn(token,json.dumps(output)); self.assertNotIn(self.admin,json.dumps(output))
                with self.assertRaisesRegex(RuntimeFault,'ROUTE_NOT_AVAILABLE'): agent_call(connection,'approve',{})
                with self.assertRaisesRegex(RuntimeFault,'FIELDS_INVALID'): agent_call(connection,'status',{'project':'other'})
                with self.assertRaisesRegex(RuntimeFault,'SCRIPT_PLANS_DISABLED'): agent_call(connection,'catalog',{})
                data=json.loads(connection.read_text());data['expires_at']=time.time()-1;connection.write_text(json.dumps(data))
                with self.assertRaisesRegex(RuntimeFault,'EXPIRED'): agent_call(connection,'status',{})
            finally: stop.set();thread.join(5)
            self.assertFalse(thread.is_alive());self.assertEqual(errors,[]);self.assertEqual(self.keys(),0)
            self.assertFalse(connection.exists());self.assertTrue(output[-1]['session_grants_revoked'])
            with self.store.connect() as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],0)

    def test_numeric_loopback_bind_does_not_require_dns(self):
        from video_factory.agent_api import AgentServer
        with patch('socket.getfqdn', side_effect=AssertionError('DNS must not be used')):
            with AgentServer(('127.0.0.1', 0), object()) as server:
                self.assertEqual(server.server_name, 'localhost')
                self.assertGreater(server.server_port, 0)
                self.assertEqual(server.server_address[0], '127.0.0.1')

    def test_occupied_port_cleans_up_new_capabilities(self):
        import socket
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));sock.listen();self.args.port=sock.getsockname()[1]
            with self.assertRaises(OSError): run(self.store,self.admin,self.plan,self.key,self.args,emit=lambda _:None)
        self.assertEqual(self.keys(),0);self.assertEqual(list(self.state.glob('agent-session-*')),[])

    def test_employee_startup_configuration_fence_and_shutdown(self):
        from video_factory.employee_access import EmployeeAccess, manage
        config = self.root/'employee.json'
        value = {'schema':'vf-business-employee-v1','project':self.plan['project'],
                 'tenant_key':'tenant_fixture','open_id':'ou_fixture',
                 'expires_at':time.time()+1800,'allow_plans':False}
        exclusive_write(config,json.dumps(value).encode())
        self.args.employee_config=config
        self.args.expect_plan=self.inspect(employee_config=config)['plan_sha256']
        value['open_id']='ou_other';config.write_text(json.dumps(value))
        with self.assertRaisesRegex(RuntimeFault,'START_PLAN_CHANGED'):
            run(self.store,self.admin,self.plan,self.key,self.args,emit=lambda _:None)
        self.assertEqual(self.keys(),0)
        value['open_id']='ou_fixture';config.write_text(json.dumps(value))
        oauth=SimpleNamespace(start=lambda:{'verification_uri':'https://accounts.feishu.cn/fixture',
            'user_code':'fixture-code','device_code':'fixture-device','expires_in':300,'interval':0},
            poll=lambda _:{'status':'authorized','access_token':'fixture-user','expires_in':600})
        def employee(*args,**kwargs):
            return EmployeeAccess(*args,**kwargs,oauth_factory=lambda *_:oauth,client_factory=fixture.Client)
        stop=threading.Event();ready=threading.Event();output=[];errors=[]
        def emit(v):
            output.append(v)
            if 'connection' in v:ready.set()
        def work():
            try:run(self.store,self.admin,self.plan,self.key,self.args,emit=emit,stop=stop)
            except BaseException as e:errors.append(e);ready.set()
        with patch('video_factory.employee_access.EmployeeAccess',side_effect=employee):
            thread=threading.Thread(target=work);thread.start()
            try:
                self.assertTrue(ready.wait(10));self.assertEqual(errors,[])
                connection=Path(output[0]['connection'])
                port=json.loads(connection.read_text())['port']
                self.assertEqual(output[0]['employee_origin'],'http://127.0.0.1:'+str(port))
                def call(op,token=None):
                    c=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
                    c.request('POST','/v1/employee/'+op,'{}',{'Content-Type':'application/json',**({'Authorization':'Bearer '+token} if token else {})})
                    response=c.getresponse();result=json.loads(response.read());c.close();return response.status,result
                status,p=call('connect/start');self.assertEqual(status,200)
                status,grant=call('connect/poll',p['pair_token']);self.assertEqual(status,200)
                self.assertEqual(call('status',grant['token'])[0],200)
                self.assertEqual(agent_call(connection,'status',{})['project'],self.plan['project'])
                policy=manage(self.store,self.admin,self.plan['project'],'status')
                manage(self.store,self.admin,self.plan['project'],'suspend',expected_version=policy['version'],request_id='launcher-suspend-0001')
                self.assertNotEqual(call('status',grant['token'])[0],200)
                self.assertEqual(agent_call(connection,'status',{})['project'],self.plan['project'])
            finally:stop.set();thread.join(10)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[]);self.assertEqual(self.keys(),0)
        self.assertTrue(manage(self.store,self.admin,self.plan['project'],'status')['suspended'])


if __name__=='__main__':unittest.main()
