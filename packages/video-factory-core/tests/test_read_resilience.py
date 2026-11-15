import io
import unittest
from unittest.mock import patch,Mock
from urllib.error import HTTPError
from video_factory.feishu_client import FeishuClient
from video_factory.feishu_results_client import ResultClient
from video_factory.runtime_store import RuntimeFault,fingerprint
from video_factory.feishu_bridge import meta,save
from video_factory import script_week as week
from video_factory import script_week_read_health as health
from video_factory.script_week_io import CopyOnlyWeekWriter
from test_script_week import WeekTests


class ReadRetryTests(unittest.TestCase):
    def client(self):return FeishuClient('fixture-token',read_attempts=3)
    def test_timeout_and_502_recover_on_third_get(self):
        with patch.object(FeishuClient,'_get_once',side_effect=[RuntimeFault('FEISHU_READ_TIMEOUT'),RuntimeFault('FEISHU_READ_HTTP_502'),{}]) as get,patch('video_factory.feishu_client.time.sleep') as wait:
            self.assertEqual(self.client().get('/fixture'),{})
            self.assertEqual(get.call_count,3);self.assertEqual(wait.call_count,2)
    def test_three_attempt_cap(self):
        with patch.object(FeishuClient,'_get_once',side_effect=RuntimeFault('FEISHU_READ_TIMEOUT')) as get,patch('video_factory.feishu_client.time.sleep'):
            with self.assertRaisesRegex(RuntimeFault,'TIMEOUT'):self.client().get('/fixture')
            self.assertEqual(get.call_count,3)
    def test_401_403_permission_tls_invalid_json_are_not_retried(self):
        for code in ('HTTP_401','HTTP_403','REJECTED_CODE_91403','REJECTED_CODE_99991663','TLS_FAILED','INVALID_JSON','RESPONSE_TOO_LARGE'):
            with self.subTest(code=code),patch.object(FeishuClient,'_get_once',side_effect=RuntimeFault('FEISHU_READ_'+code)) as get:
                with self.assertRaises(RuntimeFault):self.client().get('/fixture')
                self.assertEqual(get.call_count,1)
    def test_post_and_put_never_retry(self):
        for method in ('post','put'):
            with patch('video_factory.feishu_results_client.request',side_effect=RuntimeFault('BASE_RESULTS_REQUEST_UNKNOWN_HTTP_502')) as request:
                with self.assertRaises(RuntimeFault):getattr(ResultClient('fixture-token',read_attempts=3),method)('/fixture',{})
                self.assertEqual(request.call_count,1)
    def test_write_readback_timeout_is_not_a_transient_read(self):
        client=Mock();client.append.return_value='recFixture';client.record.side_effect=RuntimeFault('FEISHU_READ_TIMEOUT')
        writer=CopyOnlyWeekWriter(client,source_base='bascnSource',copy_base='bascnCopy',table='tblCopy',machine_fields=['id'],human_fields=[],identity_field='id')
        with self.assertRaisesRegex(RuntimeFault,'WRITE_READBACK_UNKNOWN'):writer.append({'id':'fixture'},'ticket')
        self.assertEqual(client.append.call_count,1)


class HealthTests(WeekTests):
    def runner(self,read,probe):
        return week.WeekRunner(self.jobs,self.f.project,self.token,self.key,read_new=read,read_probe=probe,publish=lambda *a:None,archive=lambda *a,**kw:None)
    def test_hold_survives_process_restart_checks_every_five_minutes(self):
        self.start();probe=Mock();read=Mock(side_effect=RuntimeFault('FEISHU_READ_TIMEOUT'))
        with patch('time.time',return_value=self.t0+1):
            first=self.runner(read,probe).tick();self.assertEqual(first['state'],'read_paused')
        with patch('time.time',return_value=self.t0+300):
            self.assertEqual(self.runner(read,probe).tick()['state'],'read_paused');probe.assert_not_called()
            with self.f.store.connect() as db:
                with self.assertRaisesRegex(RuntimeFault,'WEEK_READ_PAUSED'):week.authorize(db,self.token,self.f.project)
        with patch('time.time',return_value=self.t0+301):
            self.assertEqual(self.runner(read,probe).tick()['state'],'read_recovered')
        self.assertEqual(read.call_count,1);self.assertEqual(probe.call_count,1)
        self.assertEqual(self.state()[1]['requests'],{})
    def test_outage_limit_never_resets_on_failed_probe(self):
        self.start();probe=Mock(side_effect=RuntimeFault('FEISHU_READ_HTTP_502'))
        with patch('time.time',return_value=self.t0):health.hold(self.f.store,self.f.project,RuntimeFault('FEISHU_READ_TIMEOUT'))
        for elapsed in (300,600,900,1200,1500):
            with patch('time.time',return_value=self.t0+elapsed):
                self.assertEqual(health.gate(self.f.store,self.f.project,probe)['pause_deadline'],self.t0+1800)
        with patch('time.time',return_value=self.t0+1800):
            result=health.gate(self.f.store,self.f.project,probe);self.assertEqual(result['error'],'WEEK_READ_OUTAGE_LIMIT')
        self.assertEqual(probe.call_count,5);w,t=self.state();self.assertFalse(t['enabled'])
        with self.f.store.connect() as db:self.assertEqual(meta(db,w['token_key'])['expires_at'],0)
    def test_independent_housekeeping_enforces_outage_limit(self):
        from video_factory.script_week_service import housekeeping
        self.start()
        with patch('time.time',return_value=self.t0):health.hold(self.f.store,self.f.project,RuntimeFault('FEISHU_READ_TIMEOUT'))
        with patch('time.time',return_value=self.t0+1800):
            self.assertEqual(housekeeping(self.f.store,self.f.project,lambda *a,**kw:None)['state'],'closed')
        self.assertFalse(self.state()[1]['enabled'])
    def test_auth_failure_during_probe_closes(self):
        self.start()
        with patch('time.time',return_value=self.t0):health.hold(self.f.store,self.f.project,RuntimeFault('FEISHU_READ_TIMEOUT'))
        with patch('time.time',return_value=self.t0+300):
            result=self.runner(Mock(),Mock(side_effect=RuntimeFault('FEISHU_READ_HTTP_403'))).tick()
        self.assertEqual(result['state'],'closed');self.assertEqual(result['error'],'FEISHU_READ_HTTP_403')
    def test_slow_success_cannot_pass_168_hour_deadline(self):
        self.start();end=self.state()[0]['t1']
        with patch('time.time',return_value=end-400):health.hold(self.f.store,self.f.project,RuntimeFault('FEISHU_READ_TIMEOUT'))
        def late():clock.return_value=end
        with patch('time.time',return_value=end-100) as clock:
            self.assertEqual(health.gate(self.f.store,self.f.project,late)['state'],'closed')
        self.assertFalse(self.state()[1]['enabled'])
    def test_model_unknown_is_never_replayed_after_read_recovery(self):
        self.start();q=self.queue()
        with patch.object(self.provider,'generate',side_effect=TimeoutError) as generate:
            self.assertEqual(self.runner(Mock(),Mock()).tick()['state'],'closed')
            self.runner(Mock(),Mock()).tick();self.assertEqual(generate.call_count,1)
        self.assertEqual(len(self.state()[1]['requests']),1)
    def test_inventory_recovery_keeps_dedupe_and_requires_exact_hash(self):
        self.start();w,_=self.state()
        inventory={'contract_sha256':w['contract_sha256'],'inventory_at':self.t0,'seen':{'recFixture':'a'*64},'entries':{},'rejected':{}}
        with self.f.store.connect() as db:save(db,'script:week-inputs:'+self.f.project,inventory)
        week.close(self.f.store,self.f.project,reason='error');w,_=self.state()
        args=dict(expected_contract=w['contract_sha256'],expected_closed=fingerprint(w['closed']),start_notice_sha256='9'*64)
        with self.assertRaisesRegex(RuntimeFault,'NOT_EMPTY'):week.resume_empty(self.f.store,self.f.admin,self.f.project,**args)
        with self.assertRaisesRegex(RuntimeFault,'INVENTORY_CONFLICT'):week.resume_inventory(self.f.store,self.f.admin,self.f.project,**args,expected_inventory='0'*64)
        with patch('time.time',return_value=self.t0+300):v=week.resume_inventory(self.f.store,self.f.admin,self.f.project,**args,expected_inventory=fingerprint(inventory))
        self.assertEqual(v['t0'],self.t0+300);self.assertEqual(v['t1']-v['t0'],604800)
        with self.f.store.connect() as db:self.assertEqual(meta(db,'script:week-inputs:'+self.f.project),inventory)
        self.assertEqual(self.state()[1]['requests'],{})
    def test_inventory_recovery_denies_compiled_or_paid_state(self):
        self.start();self.queue();w,_=self.state()
        inventory={'contract_sha256':w['contract_sha256'],'inventory_at':self.t0,'seen':{},'entries':{},'rejected':{}}
        with self.f.store.connect() as db:save(db,'script:week-inputs:'+self.f.project,inventory)
        week.close(self.f.store,self.f.project,reason='error');w,_=self.state()
        with self.assertRaisesRegex(RuntimeFault,'NOT_EMPTY'):
            week.resume_inventory(self.f.store,self.f.admin,self.f.project,expected_contract=w['contract_sha256'],expected_closed=fingerprint(w['closed']),start_notice_sha256='9'*64,expected_inventory=fingerprint(inventory))
