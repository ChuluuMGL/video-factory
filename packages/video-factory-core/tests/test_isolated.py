from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from video_factory.isolated import Worker, Conflict
from video_factory.canary import CanaryError

CONFIG = json.loads((Path(__file__).resolve().parent / 'fixtures/projects.json').read_text())


class IsolatedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='vf-unit-')
        self.path = Path(self.temp.name) / 'worker.sqlite'
        self.w = Worker(self.path, CONFIG)

    def tearDown(self):
        self.w.close()
        self.temp.cleanup()

    def submit(self, project='fixture_alpha', event='submit-0001', task='sample_test'):
        m=CONFIG['projects'][project]['fields']
        return {'project':project,'task':task,'event':event,'action':'submit','actor':'AUTOMATED_TEST_NOT_HUMAN',
                'fields':{m['sku']:'SYNTHETIC-001',m['direction']:'展示虚构商品'}}

    def generate(self, command):
        self.w.receive(command,'test-n8n-001')
        return self.w.process(command['project'],command['event'],'test-n8n-001')

    def reject(self, feedback='商品先展示；每句少于十字', target='sample_test-script-001'):
        return {'project':'fixture_alpha','task':'sample_test','event':'reject-0001','action':'reject',
                'actor':'AUTOMATED_TEST_NOT_HUMAN','target_version':target,'feedback':feedback}

    def test_full_loop_opinion_diff_and_reopen(self):
        self.generate(self.submit())
        old=self.w.task('fixture_alpha','sample_test')['versions'][0]
        command=self.reject();self.w.receive(command,'test-n8n-002')
        self.w.close();self.w=Worker(self.path,CONFIG)
        self.assertEqual(self.w.task('fixture_alpha','sample_test')['review_feedback'],command['feedback'])
        self.w.process('fixture_alpha','reject-0001','test-n8n-003')
        t=self.w.task('fixture_alpha','sample_test');v=t['versions'][-1]
        self.assertEqual(t['status'],'脚本待审核');self.assertEqual(len(t['versions']),2)
        self.assertEqual(t['versions'][0],old)
        self.assertEqual(v['previous_version'],old['version_id']);self.assertEqual(v['feedback'],command['feedback'])
        self.assertIn('product',v['diff']);self.assertEqual(t['reviews'][0]['feedback'],command['feedback'])
        script=json.loads(v['content']);self.assertEqual(script['scenes'][0]['shot'],'product')
        self.assertTrue(all(len(s['text'])<10 for s in script['scenes']))

    def test_two_adapters_same_ids_never_mix(self):
        for p in CONFIG['projects']:self.generate(self.submit(p))
        for p in CONFIG['projects']:
            t=self.w.task(p,'sample_test');data=json.loads(t['versions'][0]['content'])
            self.assertEqual(data['project'],p);self.assertEqual(data['assets'],CONFIG['projects'][p]['assets'])
            self.assertEqual(data['rules'],CONFIG['projects'][p]['rules'])
        bad=self.submit('fixture_beta',event='submit-0002',task='sample_other')
        bad['fields']=self.submit()['fields']
        with self.assertRaisesRegex(Conflict,'PROJECT_FIELDS_MISMATCH'):self.w.receive(bad,'test')

    def test_duplicate_and_changed_event(self):
        command=self.submit();first=self.generate(command)
        self.w.receive(command,'test-again')
        self.assertEqual(self.w.process('fixture_alpha',command['event'],'test-again'),first)
        self.assertEqual(len(self.w.task('fixture_alpha','sample_test')['versions']),1)
        command['fields']['创作要求']='changed'
        with self.assertRaisesRegex(Conflict,'IDEMPOTENCY_INPUT_CHANGED'):self.w.receive(command,'test')

    def test_crash_before_commit_resume(self):
        c=self.submit();self.w.receive(c,'test')
        with self.assertRaisesRegex(RuntimeError,'INJECTED'):self.w.process(c['project'],c['event'],'test',fault='before_commit')
        self.w.close();self.w=Worker(self.path,CONFIG)
        self.assertEqual(self.w.task(c['project'],c['task'])['versions'],[])
        self.assertEqual(len(self.w.snapshot()['pending']),1)
        self.w.process(c['project'],c['event'],'test-resume')
        self.assertEqual(len(self.w.task(c['project'],c['task'])['versions']),1)

    def test_concurrent_duplicate_generates_once(self):
        c=self.submit();self.w.receive(c,'test')
        def run(_):
            w=Worker(self.path,CONFIG)
            try:return w.process(c['project'],c['event'],'test-concurrent')
            finally:w.close()
        with ThreadPoolExecutor(max_workers=4) as pool:receipts=list(pool.map(run,range(8)))
        self.assertTrue(all(r==receipts[0] for r in receipts));self.assertEqual(len(self.w.task(c['project'],c['task'])['versions']),1)

    def test_stale_empty_and_unrecognized_review(self):
        self.generate(self.submit())
        with self.assertRaisesRegex(CanaryError,'STALE'):self.w.receive(self.reject(target='old-version'),'test')
        with self.assertRaisesRegex(Conflict,'FEEDBACK_REQUIRED'):self.w.receive(self.reject(feedback=' '),'test')
        c=self.reject(feedback='暂未支持的要求');self.w.receive(c,'test')
        with self.assertRaisesRegex(Conflict,'UNSUPPORTED_MOCK_FEEDBACK'):self.w.process(c['project'],c['event'],'test')
        self.assertEqual(self.w.task(c['project'],c['task'])['reviews'][0]['feedback'],c['feedback'])
        self.assertEqual(len(self.w.task(c['project'],c['task'])['versions']),1)

    def test_duplicate_review_and_two_reviewers(self):
        self.generate(self.submit());c=self.reject();self.generate(c)
        self.w.receive(c,'repeat');self.w.process(c['project'],c['event'],'repeat')
        self.assertEqual(len(self.w.task(c['project'],c['task'])['reviews']),1)
        c['event']='reject-0002'
        with self.assertRaisesRegex(CanaryError,'STALE'):self.w.receive(c,'test')

    def test_config_drift_and_asset_collision(self):
        cfg=copy.deepcopy(CONFIG);cfg['projects']['fixture_beta']['rules']=['different']
        with self.assertRaisesRegex(Conflict,'CONFIG_DRIFT'):Worker(self.path,cfg)
        cfg=copy.deepcopy(CONFIG);cfg['projects']['fixture_beta']['assets']=['fixture_alpha:pencil_mock']
        with self.assertRaisesRegex(Conflict,'ASSET_BOUNDARY'):Worker(self.path,cfg)

    def test_no_live_identity_or_unknown_fields(self):
        c=self.submit();c['project']='production'
        with self.assertRaises(Conflict):self.w.receive(c,'test')
        c=self.submit();c['provider_url']='https://example.com'
        with self.assertRaises(Conflict):self.w.receive(c,'test')
        self.assertEqual(self.w.snapshot()['tasks'],[])

    def test_immutable_audit(self):
        self.generate(self.submit())
        with self.assertRaisesRegex(sqlite3.IntegrityError,'IMMUTABLE_AUDIT'):self.w.db.execute('DELETE FROM audit')

if __name__=='__main__':unittest.main()
