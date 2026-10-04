import os
import time
import unittest
from unittest.mock import patch
import test_stack as fixtures
from video_factory import maintenance, workspace, workspace_tls
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
