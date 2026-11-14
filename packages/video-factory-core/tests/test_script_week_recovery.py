import time
import unittest
from unittest.mock import patch
from test_script_week import WeekTests
from video_factory import script_week as week
from video_factory.runtime_store import RuntimeFault,fingerprint
from video_factory.feishu_bridge import meta

class RecoveryTests(WeekTests):
    def recover(self,**changes):
        w,t=self.state()
        args=dict(expected_contract=w['contract_sha256'],expected_closed=fingerprint(w.get('closed',{})),start_notice_sha256='9'*64)
        args.update(changes)
        return week.resume_empty(self.f.store,self.f.admin,self.f.project,**args)
    def test_empty_error_restarts_at_real_time_preserves_old_window_and_token_revocation(self):
        old=self.start();w0,t0=self.state();week.close(self.f.store,self.f.project,reason='error')
        closed=self.state()[0]['closed'];now=self.t0+1000
        with patch('video_factory.script_week.time.time',return_value=now):new=self.recover()
        self.assertEqual((new['t0'],new['t1']),(now,now+604800));self.assertNotEqual(old['token'],new['token'])
        w,t=self.state();self.assertEqual(w['contract'],w0['contract']);self.assertEqual(t['requests'],{})
        with self.f.store.connect() as db:
            self.assertEqual(meta(db,w0['token_key'])['expires_at'],0)
            history=meta(db,'script:week-recovery:'+self.f.project+':'+fingerprint(closed))
            self.assertEqual(history['week']['closed'],closed)
            with self.assertRaises(RuntimeFault):week.authorize(db,old['token'],self.f.project)
        with self.assertRaisesRegex(RuntimeFault,'RECOVERY_CONFLICT'):self.recover(expected_closed=fingerprint(closed))
    def test_queued_or_paid_work_cannot_reset_budget(self):
        self.start();self.queue();week.close(self.f.store,self.f.project,reason='error')
        with self.assertRaisesRegex(RuntimeFault,'NOT_EMPTY'):self.recover()
    def test_manual_stop_cannot_restart(self):
        self.start();week.close(self.f.store,self.f.project,reason='manual')
        with self.assertRaisesRegex(RuntimeFault,'CONFLICT'):self.recover()
    def test_stale_close_receipt_rejected(self):
        self.start();week.close(self.f.store,self.f.project,reason='error')
        with self.assertRaisesRegex(RuntimeFault,'CONFLICT'):self.recover(expected_closed='0'*64)
