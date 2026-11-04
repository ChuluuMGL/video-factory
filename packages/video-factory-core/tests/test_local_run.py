"""Cloud-only real loopback startup/client tests; no provider or Feishu network."""
import json
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

    def test_occupied_port_cleans_up_new_capabilities(self):
        import socket
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));sock.listen();self.args.port=sock.getsockname()[1]
            with self.assertRaises(OSError): run(self.store,self.admin,self.plan,self.key,self.args,emit=lambda _:None)
        self.assertEqual(self.keys(),0);self.assertEqual(list(self.state.glob('agent-session-*')),[])


if __name__=='__main__':unittest.main()
