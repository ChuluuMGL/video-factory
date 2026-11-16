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
    def setUp(self):
        pace=patch('video_factory.feishu_client._pace_read')
        pace.start();self.addCleanup(pace.stop)
    def client(self):return FeishuClient('fixture-token',read_attempts=3)
    def test_timeout_and_502_recover_on_third_get(self):
        with patch.object(FeishuClient,'_get_once',side_effect=[RuntimeFault('FEISHU_READ_TIMEOUT'),RuntimeFault('FEISHU_READ_HTTP_502'),{}]) as get,patch('video_factory.feishu_client.time.sleep') as wait:
            self.assertEqual(self.client().get('/fixture'),{})
            self.assertEqual(get.call_count,3);self.assertEqual(wait.call_count,2)
    def test_actual_data_not_ready_response_recovers_without_leaking_message(self):
        for http_error in (False,True):
            with self.subTest(http_error=http_error),patch('video_factory.feishu_client.build_opener') as opener,patch('video_factory.feishu_client.time.sleep'):
                transient=Mock();transient.__enter__=Mock(return_value=transient);transient.__exit__=Mock(return_value=False)
                transient.status=200;transient.read.return_value=b'{"code":1254607,"msg":"private upstream detail"}'
                ok=Mock();ok.__enter__=Mock(return_value=ok);ok.__exit__=Mock(return_value=False)
                ok.status=200;ok.read.return_value=b'{"code":0,"data":{"items":[]}}'
                first=HTTPError('private-url',503,'private',{},io.BytesIO(transient.read.return_value)) if http_error else transient
                opener.return_value.open.side_effect=[first,ok]
                self.assertEqual(self.client().get('/fixture'),{'items':[]})
                self.assertEqual(opener.return_value.open.call_count,2)
    def test_data_not_ready_still_has_three_attempt_cap_and_default_one(self):
        for attempts in (1,3):
            with self.subTest(attempts=attempts),patch.object(FeishuClient,'_get_once',side_effect=RuntimeFault('FEISHU_READ_REJECTED_CODE_1254607')) as get,patch('video_factory.feishu_client.time.sleep'):
                with self.assertRaisesRegex(RuntimeFault,'^FEISHU_READ_REJECTED_CODE_1254607$'):FeishuClient('fixture-token',read_attempts=attempts).get('/fixture')
                self.assertEqual(get.call_count,attempts)
    def test_http_auth_failure_cannot_become_retryable_data_not_ready(self):
        for code in (401,403):
            with self.subTest(code=code),patch('video_factory.feishu_client.build_opener') as opener:
                opener.return_value.open.side_effect=HTTPError('private-url',code,'private',{},io.BytesIO(b'{"code":1254607}'))
                with self.assertRaisesRegex(RuntimeFault,'^FEISHU_READ_HTTP_'+str(code)+'$'):self.client().get('/fixture')
                self.assertEqual(opener.return_value.open.call_count,1)
    def test_data_not_ready_after_write_stops_without_replaying_write(self):
        for method in ('append','update'):
            client=Mock();client.append.return_value='recFixture';client.record.side_effect=RuntimeFault('FEISHU_READ_REJECTED_CODE_1254607')
            writer=CopyOnlyWeekWriter(client,source_base='bascnSource',copy_base='bascnCopy',table='tblCopy',machine_fields=['id','value'],human_fields=[],identity_field='id')
            if method=='update':client.record.side_effect=[{'record_id':'recFixture','fields':{'id':'fixture','value':'old'}},RuntimeFault('FEISHU_READ_REJECTED_CODE_1254607')]
            with self.assertRaisesRegex(RuntimeFault,'WRITE_READBACK_UNKNOWN'):
                if method=='append':writer.append({'id':'fixture'},'ticket')
                else:writer.update('recFixture','fixture',{'value':'new'})
            self.assertEqual(getattr(client,'append' if method=='append' else 'update_record').call_count,1)
    def test_three_attempt_cap(self):
        with patch.object(FeishuClient,'_get_once',side_effect=RuntimeFault('FEISHU_READ_TIMEOUT')) as get,patch('video_factory.feishu_client.time.sleep'):
            with self.assertRaisesRegex(RuntimeFault,'TIMEOUT'):self.client().get('/fixture')
            self.assertEqual(get.call_count,3)
    def test_rate_limit_goes_to_durable_hold_without_immediate_retry(self):
        for status,body in ((200,b'{"code":1254290,"msg":"private"}'),
                            (429,b'{"code":1254290}'),(429,b'private non-json')):
            with self.subTest(status=status,body=body),patch('video_factory.feishu_client.build_opener') as opener,patch('video_factory.feishu_client.time.sleep') as wait:
                response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
                response.status=status;response.read.return_value=body
                opener.return_value.open.side_effect=[response if status==200 else HTTPError('private',status,'private',{},io.BytesIO(body))]
                with self.assertRaisesRegex(RuntimeFault,'^FEISHU_READ_(REJECTED_CODE_1254290|HTTP_429)$'):self.client().get('/fixture')
                self.assertEqual(opener.return_value.open.call_count,1);wait.assert_not_called()
    def test_rate_limit_body_never_hides_http_auth_failure(self):
        for status in (401,403):
            with self.subTest(status=status),patch('video_factory.feishu_client.build_opener') as opener:
                opener.return_value.open.side_effect=HTTPError('private',status,'private',{},io.BytesIO(b'{"code":1254290}'))
                with self.assertRaisesRegex(RuntimeFault,'^FEISHU_READ_HTTP_'+str(status)+'$'):self.client().get('/fixture')
                self.assertEqual(opener.return_value.open.call_count,1)
    def test_rate_limit_after_committed_write_is_unknown_not_replayed(self):
        client=Mock();client.append.return_value='recFixture';client.record.side_effect=RuntimeFault('FEISHU_READ_REJECTED_CODE_1254290')
        writer=CopyOnlyWeekWriter(client,source_base='bascnSource',copy_base='bascnCopy',table='tblCopy',machine_fields=['id'],human_fields=[],identity_field='id')
        with self.assertRaisesRegex(RuntimeFault,'WRITE_READBACK_UNKNOWN'):writer.append({'id':'fixture'},'ticket')
        client.append.assert_called_once()
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
    def test_data_not_ready_pauses_and_recovers_without_model_or_write(self):
        self.start();read=Mock(side_effect=RuntimeFault('FEISHU_READ_REJECTED_CODE_1254607'));probe=Mock()
        with patch('time.time',return_value=self.t0):
            self.assertEqual(self.runner(read,probe).tick()['state'],'read_paused')
        with patch('time.time',return_value=self.t0+300):
            self.assertEqual(self.runner(read,probe).tick()['state'],'read_recovered')
        self.assertEqual(read.call_count,1);self.assertEqual(probe.call_count,1)
        self.assertEqual(self.state()[1]['requests'],{})
    def test_rate_limit_waits_five_minutes_then_recovers_without_generation(self):
        self.start()
        for code in ('FEISHU_READ_REJECTED_CODE_1254290','FEISHU_READ_HTTP_429'):
            with self.subTest(code=code):
                read=Mock(side_effect=RuntimeFault(code));probe=Mock()
                with patch('time.time',return_value=self.t0):self.assertEqual(self.runner(read,probe).tick()['state'],'read_paused')
                with patch('time.time',return_value=self.t0+299):self.assertEqual(self.runner(read,probe).tick()['state'],'read_paused')
                probe.assert_not_called()
                with patch('time.time',return_value=self.t0+300):self.assertEqual(self.runner(read,probe).tick()['state'],'read_recovered')
                self.assertEqual(read.call_count,1);probe.assert_called_once();self.assertEqual(self.state()[1]['requests'],{})
    def test_persistent_rate_limit_has_same_thirty_minute_stop(self):
        self.start();error=RuntimeFault('FEISHU_READ_REJECTED_CODE_1254290')
        with patch('time.time',return_value=self.t0):health.hold(self.f.store,self.f.project,error)
        with patch('time.time',return_value=self.t0+300):
            self.assertEqual(health.gate(self.f.store,self.f.project,Mock(side_effect=error))['pause_deadline'],self.t0+1800)
        with patch('time.time',return_value=self.t0+1800):self.assertEqual(health.status(self.f.store,self.f.project)['state'],'closed')
        self.assertFalse(self.state()[1]['enabled'])
    def test_data_not_ready_cannot_extend_outage_deadline(self):
        self.start();error=RuntimeFault('FEISHU_READ_REJECTED_CODE_1254607')
        with patch('time.time',return_value=self.t0):health.hold(self.f.store,self.f.project,error)
        with patch('time.time',return_value=self.t0+300):
            self.assertEqual(health.gate(self.f.store,self.f.project,Mock(side_effect=error))['pause_deadline'],self.t0+1800)
        with patch('time.time',return_value=self.t0+1800):
            self.assertEqual(health.gate(self.f.store,self.f.project,Mock())['state'],'closed')
        self.assertFalse(self.state()[1]['enabled'])
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


class ReadPacingTests(unittest.TestCase):
    def test_source_and_copy_clients_share_half_second_spacing(self):
        from video_factory import feishu_client as client
        clock=[100.0];starts=[]
        def sleep(seconds):clock[0]+=seconds
        def get(path):starts.append(clock[0]);return {}
        with patch.object(client,'_last_read_started',None),patch.object(client.time,'monotonic',side_effect=lambda:clock[0]),patch.object(client.time,'sleep',side_effect=sleep),patch.object(client.FeishuClient,'_get_once',side_effect=get):
            a=client.FeishuClient('fixture-a',read_attempts=3);b=client.FeishuClient('fixture-b',read_attempts=3)
            a.get('/fixture');b.get('/fixture');a.get('/fixture')
        self.assertEqual(starts,[100.0,100.5,101.0])
    def test_default_client_does_not_opt_into_pacing(self):
        with patch('video_factory.feishu_client._pace_read') as pace,patch.object(FeishuClient,'_get_once',return_value={}):
            FeishuClient('fixture-token').get('/fixture');pace.assert_not_called()
