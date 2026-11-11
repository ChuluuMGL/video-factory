import time
import unittest
from unittest.mock import Mock, patch

from test_script_jobs import ScriptTests
from video_factory import script_week as week
from video_factory.script_jobs import profile
from video_factory.feishu_bridge import meta
from video_factory.runtime_store import RuntimeFault
from video_factory.script_week_service import housekeeping, disabled_units


class WeekServiceTests(unittest.TestCase):
    setUp = ScriptTests.setUp
    tearDown = ScriptTests.tearDown

    def prepare(self):
        profile(self.f.store,self.f.admin,self.f.project,'secret:script','customer_account',max_validation_rewrites=1)
        return week.prepare(self.f.store,self.f.admin,self.f.project,
            source_base='basProductionFixture',source_table='tblProductionFixture',
            copy_base='bascnFixture',copy_table='tblFixture',claims_sha256='a'*64,
            policy_sha256='b'*64,source_readonly_evidence_sha256='c'*64,
            history_exposure_micro=869806,notice_sha256='d'*64)

    def start(self):
        prepared=self.prepare();self.t0=time.time()
        with patch('video_factory.script_week.time.time',return_value=self.t0):
            return week.activate(self.f.store,self.f.admin,self.f.project,
                expected_contract=prepared['contract_sha256'],t0=self.t0,start_notice_sha256='e'*64)

    def test_prepared_watchdog_has_no_authority_or_exports(self):
        self.prepare();archive=Mock()
        self.assertEqual(housekeeping(self.f.store,self.f.project,archive)['state'],'not_started')
        archive.assert_not_called();self.assertEqual(self.provider.calls,0)

    def test_independent_watchdog_revokes_before_broken_export(self):
        started=self.start()
        with patch('video_factory.script_week.time.time',return_value=started['t1']):
            with self.assertRaises(OSError):
                housekeeping(self.f.store,self.f.project,Mock(side_effect=OSError('disk')))
        with self.f.store.connect() as db:
            w=meta(db,week.PREFIX+self.f.project)
            self.assertEqual(w['state'],'closed')
            self.assertFalse(meta(db,'script:trial:'+self.f.project)['enabled'])
            self.assertEqual(meta(db,w['token_key'])['expires_at'],0)
        self.assertEqual(self.provider.calls,0)

    def test_elapsed_days_and_final_export_without_paid_worker(self):
        started=self.start();archive=Mock(return_value={'saved':True})
        with patch('video_factory.script_week.time.time',return_value=self.t0+2*week.DAY):
            housekeeping(self.f.store,self.f.project,archive)
        self.assertEqual([c.args for c in archive.call_args_list],[(0,),(1,)])
        archive.reset_mock()
        with patch('video_factory.script_week.time.time',return_value=started['t1']):
            result=housekeeping(self.f.store,self.f.project,archive)
        self.assertEqual(result['state'],'closed');self.assertEqual(archive.call_count,8)
        self.assertEqual(archive.call_args.kwargs,{'final':True})
        self.assertEqual(self.provider.calls,0)

    def test_observational_future_cannot_close_early(self):
        started=self.start()
        with self.assertRaisesRegex(RuntimeFault,'NOT_EXPIRED'):
            housekeeping(self.f.store,self.f.project,Mock(),now=started['t1'])

    def test_disabled_units_have_no_install_or_restart_and_independent_lock(self):
        units=disabled_units('/srv/shadow-fixture')
        self.assertEqual(len(units),4)
        self.assertTrue(all('[Install]' not in v for v in units.values()))
        self.assertIn('Restart=no',units['vf-shadow-week-tick.service'])
        self.assertIn('/housekeeping.lock',units['vf-shadow-week-housekeeping.service'])
        units=disabled_units('/srv/shadow-fixture',t1=1795053600)
        self.assertIn('Persistent=true',units['vf-shadow-week-deadline.timer'])
        self.assertIn('UTC',units['vf-shadow-week-deadline.timer'])
        for path in ['/srv/test;evil','/srv/../tmp','/srv/test%name','/srv//test']:
            with self.assertRaises(RuntimeFault):disabled_units(path)


if __name__=='__main__':unittest.main()
