import os
import time
import unittest
from unittest.mock import patch
import test_stack as fixtures
from video_factory import maintenance, workspace, workspace_tls, workspace_acme
from video_factory.stack import write_json


@unittest.skipUnless(os.getuid()==0,'Cloud root fixture')
class MaintenanceTests(unittest.TestCase):
    setUpRoot=fixtures.StackTests.setUp
    tearDown=fixtures.StackTests.tearDown
    prepare=fixtures.StackTests.prepare

    def setUp(self):
        self.setUpRoot();self.stack=self.prepare()
        health=patch.object(self.stack,'status',return_value={'infrastructure_ready':True})
        health.start();self.addCleanup(health.stop)

    def test_certificate_and_schedule_failures_are_visible_without_private_details(self):
        base=self.root/'data/production';base.mkdir(mode=0o700)
        write_json(base/'fixture.json',{'status':'published_restart_verified','expires_at':time.time()-1})
        tls={'certificate':{'days_remaining':-1},'acme':{'last_renewal':{'status':'failed','error':'private-provider-payload'},'schedule':{'enabled':True,'active':False}}}
        with patch.object(workspace,'entries',return_value=iter([{'project':'fixture'}])),patch.object(workspace,'status',return_value={'status':'running'}),patch.object(workspace_tls,'status',return_value=tls):
            result=maintenance.doctor(self.stack)
        codes={v['code'] for v in result['alerts']}
        self.assertTrue({'TLS_EXPIRED','TLS_RENEWAL_FAILED','TLS_TIMER_INACTIVE','SCHEDULE_EXPIRED'}<=codes)
        self.assertNotIn('private-provider-payload',str(result))
        self.assertFalse(result['external_notifications'])
        self.assertEqual(result['backup_capabilities']['restore'],'backup-v2-aes256gcm-hkdf')

    def test_recovery_and_failed_inspection_are_not_reported_healthy(self):
        for state in ({'status':'needs_attention','recovery_required':True},RuntimeError('private content')):
            with patch.object(workspace,'entries',return_value=iter([{'project':'fixture'}])),patch.object(workspace,'status',side_effect=state if isinstance(state,Exception) else None,return_value=state):
                result=maintenance.doctor(self.stack)
            self.assertEqual(result['status'],'needs_attention')
            self.assertNotIn('private content',str(result))

    def test_disabled_schedule_is_not_flagged_as_expired(self):
        base=self.root/'data/production';base.mkdir(mode=0o700)
        write_json(base/'fixture.json',{'status':'imported_disabled','expires_at':0})
        with patch.object(workspace,'entries',return_value=iter([])),patch.object(maintenance.shutil,'disk_usage') as disk:
            disk.return_value.free=10*1024**3
            self.assertEqual(maintenance.doctor(self.stack)['alerts'],[])

    def test_only_managed_certificates_require_a_renewal_timer(self):
        for production,expected in (({},False),({'status':'issued'},True)):
            tls={'certificate':{'days_remaining':60},'acme':{'environments':{'production':production},'schedule':{'enabled':False}}}
            with patch.object(workspace,'entries',return_value=iter([{'project':'fixture'}])),patch.object(workspace,'status',return_value={'status':'running'}),patch.object(workspace_tls,'status',return_value=tls):
                codes={v['code'] for v in maintenance.doctor(self.stack)['alerts']}
            self.assertEqual('TLS_TIMER_NOT_CONFIGURED' in codes,expected)

    def test_issued_certificate_without_workspace_is_visible_and_sanitized(self):
        root=self.root/'data/acme/fixture';root.mkdir(parents=True,mode=0o700)
        for production,expected in (({'status':'issued','requires_context_review':True},{'TLS_WORKSPACE_NOT_CONFIGURED','TLS_CONTEXT_REVIEW_REQUIRED'}),({'status':'in_flight'},{'TLS_ISSUANCE_INCOMPLETE'})):
            with patch.object(workspace,'entries',return_value=iter([])),patch.object(workspace_acme,'status',return_value={'environments':{'production':production},'private':'secret fixture'}):
                result=maintenance.doctor(self.stack)
            self.assertTrue(expected<={v['code'] for v in result['alerts']})
            self.assertNotIn('secret fixture',str(result))
        with patch.object(workspace,'entries',return_value=iter([])),patch.object(workspace_acme,'status',side_effect=RuntimeError('private failure')):
            result=maintenance.doctor(self.stack)
        self.assertIn('TLS_INSPECTION_FAILED',{v['code'] for v in result['alerts']})
        self.assertNotIn('private failure',str(result))
