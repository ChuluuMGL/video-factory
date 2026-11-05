"""Cloud-only business ledger repair with actual isolated Python replay."""
import hashlib
import json
import unittest

import test_employee_access as employees
from video_factory.business_repair import POLICY, replay
from video_factory.employee_access import configuration
from video_factory.feishu_bridge import meta, save
from video_factory.repair_profile import PROFILE, BEFORE, AFTER, source
from video_factory.runtime_store import RuntimeFault


def make_repair_fixture(allow_plans=True):
    fixture = employees.EmployeeTests()
    original = fixture.make
    def make():
        fixture.config['allow_plans'] = allow_plans
        fixture.config['repair'] = {'profile': PROFILE, 'allow_apply': True}
        return original()
    fixture.make = make
    fixture.setUp()
    return fixture


class BusinessRepairTests(unittest.TestCase):
    def setUp(self):
        self.f = make_repair_fixture(); self.addCleanup(self.f.doCleanups)
        self.token = self.f.pair(); self.counter = 0

    def call(self, op, body=None, token=None):
        return self.f.call('repair/'+op, token or self.token, body)

    def request(self, op, candidate=None, **extra):
        self.counter += 1
        body = {'request_id': 'repair-request-'+str(self.counter).zfill(8), **extra}
        if candidate: body.update(candidate_id=candidate['id'], expected_version=candidate['version'])
        return body

    def propose(self):
        return self.call('propose', self.request('propose', source_fingerprint=self.call('inspect')['clone']['fingerprint']))['candidate']

    def test_actual_source_lifecycle_shared_audit_and_paid_data_unchanged(self):
        store = self.f.f.store
        with store.connect() as db:
            tasks = [tuple(r) for r in db.execute('SELECT * FROM tasks ORDER BY id,revision')]
            jobs = [tuple(r) for r in db.execute("SELECT * FROM meta WHERE key LIKE 'script:job:%' OR key LIKE 'worker:%' ORDER BY key")]
        original = self.call('inspect')['clone']; c = self.propose()
        with self.assertRaisesRegex(RuntimeFault, 'VALIDATION_REQUIRED'):
            self.call('apply', self.request('apply', c))
        c = self.call('validate', self.request('validate', c))['candidate']
        self.assertFalse(c['verification']['before']['passed']); self.assertTrue(c['verification']['after']['passed'])
        self.assertEqual(c['verification']['after']['executed_source_sha256'], source(AFTER)['sha256'])
        apply_request = self.request('apply', c)
        result = self.call('apply', apply_request); c = result['candidate']
        self.assertEqual(result['current_clone']['source'], source(AFTER))
        self.assertTrue(self.call('apply', apply_request)['replayed'])
        self.call('rollback', self.request('rollback', c))
        restored = self.call('inspect')
        self.assertEqual(restored['clone']['source'], original['source'])
        self.assertGreater(restored['clone']['version'], original['version'])
        self.assertEqual([e['operation'] for e in restored['audit']], ['propose','validate','apply','rollback'])
        with store.connect() as db:
            self.assertEqual(tasks, [tuple(r) for r in db.execute('SELECT * FROM tasks ORDER BY id,revision')])
            self.assertEqual(jobs, [tuple(r) for r in db.execute("SELECT * FROM meta WHERE key LIKE 'script:job:%' OR key LIKE 'worker:%' ORDER BY key")])
            actors = [r['actor'] for r in db.execute("SELECT actor FROM audit WHERE action LIKE 'business_repair_%'")]
            self.assertEqual(len(actors), 4); self.assertTrue(all(a.startswith('employee:'+self.f.entry.subject+':') for a in actors))
        self.assertEqual(self.f.f.provider.calls, []); self.assertEqual(self.f.f.video.calls, 0)

    def test_expired_validation_revalidation_and_source_conflicts(self):
        c = self.propose(); other = self.propose()
        c = self.call('validate', self.request('validate', c))['candidate']
        self.f.now += 601
        with self.assertRaisesRegex(RuntimeFault, 'VALIDATION_REQUIRED'): self.call('apply', self.request('apply', c))
        c = self.call('validate', self.request('validate', c))['candidate']
        c = self.call('apply', self.request('apply', c))['candidate']
        with self.assertRaisesRegex(RuntimeFault, 'SOURCE_OR_CANDIDATE_CHANGED'): self.call('validate', self.request('validate', other))
        with self.assertRaisesRegex(RuntimeFault, 'VERSION_CHANGED'):
            self.call('rollback', {'request_id':'stale-version-request', 'candidate_id':c['id'], 'expected_version':1})
        self.call('rollback', self.request('rollback', c))
        with self.assertRaisesRegex(RuntimeFault, 'SOURCE_OR_CANDIDATE_CHANGED'): self.call('validate', self.request('validate', other))

    def test_suspension_restart_and_old_verification_require_fresh_authority(self):
        c = self.propose(); c = self.call('validate', self.request('validate', c))['candidate']
        self.f.change('suspend')
        with self.assertRaisesRegex(RuntimeFault, 'SUSPENDED'): self.call('inspect')
        self.f.change('resume'); self.token = self.f.pair()
        with self.assertRaisesRegex(RuntimeFault, 'VALIDATION_REQUIRED'): self.call('apply', self.request('apply', c))
        c = self.call('validate', self.request('validate', c))['candidate']
        self.f.entry.close(); self.f.entry = self.f.make(); self.f.entry.start(); self.f.api.employee_access = self.f.entry
        self.f.addCleanup(self.f.entry.close); self.token = self.f.pair()
        with self.assertRaisesRegex(RuntimeFault, 'VALIDATION_REQUIRED'): self.call('apply', self.request('apply', c))
        c = self.call('validate', self.request('validate', c))['candidate']
        self.call('apply', self.request('apply', c))

    def test_revocation_during_validation_does_not_commit_proof(self):
        c = self.propose(); run = self.f.entry.repairs.runner
        def racing(value):
            self.f.change('suspend'); return run(value)
        self.f.entry.repairs.runner = racing
        with self.assertRaisesRegex(RuntimeFault, 'AUTH_'): self.call('validate', self.request('validate', c))
        self.f.change('resume'); self.token = self.f.pair()
        self.assertEqual(self.call('inspect')['candidates'][0]['status'], 'proposed')

    def test_input_source_tampering_request_reuse_and_clone_rollback_fences(self):
        for extra in ({'source':'print(1)'}, {'path':'/production/workflow.py'}, {'project':'other'}, {'shell':'id'}):
            with self.assertRaisesRegex(RuntimeFault, 'FIELDS_INVALID'): self.call('propose', {**self.request('propose'), **extra})
        c = self.propose()
        with self.assertRaisesRegex(RuntimeFault, 'REPAIR_VALIDATION_FAILED'): replay({'module':'x','text':'print(1)','sha256':'a'*64})
        with self.f.f.store.connect() as db:
            state = meta(db, self.f.entry.repairs.key); state['candidates'][0]['after']['text'] += '\nprint(1)'
            save(db, self.f.entry.repairs.key, state)
        with self.assertRaisesRegex(RuntimeFault, 'SOURCE_OR_CANDIDATE_CHANGED'): self.call('validate', self.request('validate', c))

    def test_readonly_and_unconfigured_repair_default_closed(self):
        # Separate fresh instances retain distinct owner-approved policies.
        plain = employees.EmployeeTests(); plain.setUp(); self.addCleanup(plain.doCleanups)
        with self.assertRaisesRegex(RuntimeFault, 'OPERATION_DENIED'): plain.call('repair/inspect', plain.pair())
        class ReadRepair(employees.EmployeeTests):
            def make(this):
                this.config['repair'] = {'profile':PROFILE,'allow_apply':False}
                return super().make()
        ro=ReadRepair();ro.setUp();self.addCleanup(ro.doCleanups);token=ro.pair()
        self.assertEqual(ro.call('repair/inspect',token)['scope'],'read')
        with self.assertRaisesRegex(RuntimeFault,'OPERATION_DENIED'):ro.call('repair/propose',token,{})
        path=self.f.f.root/'repair-config.json'
        bad={**self.f.config,'repair':{'profile':'arbitrary-python','allow_apply':True}}
        path.write_text(json.dumps(bad));path.chmod(0o600)
        with self.assertRaisesRegex(RuntimeFault,'REPAIR_CONFIGURATION_INVALID'):
            configuration(path,self.f.plan,allow_plans=True,clock=lambda:self.f.now)

    def test_replay_request_conflict_and_failed_validation(self):
        body = self.request('propose', source_fingerprint=self.call('inspect')['clone']['fingerprint'])
        c = self.call('propose', body)['candidate']
        self.assertTrue(self.call('propose', body)['replayed'])
        with self.assertRaisesRegex(RuntimeFault, 'REQUEST_ID_CONFLICT'):
            self.call('propose', {**body, 'source_fingerprint':'a'*64})
        original = self.f.entry.repairs.runner
        self.f.entry.repairs.runner = lambda value: {**original(value), 'passed':False}
        c = self.call('validate', self.request('validate', c))['candidate']
        self.assertEqual(c['status'], 'test_failed')
        with self.assertRaisesRegex(RuntimeFault,'VALIDATION_REQUIRED'):self.call('apply',self.request('apply',c))
        self.f.entry.repairs.runner = original
        c = self.call('validate', self.request('validate', c))['candidate']
        c = self.call('apply', self.request('apply', c))['candidate']
        with self.f.f.store.connect() as db:
            state=meta(db,self.f.entry.repairs.key);state['clone']['version']+=1
            save(db,self.f.entry.repairs.key,state)
        with self.assertRaisesRegex(RuntimeFault,'ROLLBACK_CONFLICT'):self.call('rollback',self.request('rollback',c))

    def test_repair_permission_does_not_require_or_grant_paid_plan_permission(self):
        fixture=make_repair_fixture(allow_plans=False);self.addCleanup(fixture.doCleanups)
        token=fixture.pair()
        with self.assertRaisesRegex(RuntimeFault,'OPERATION_DENIED'):
            fixture.call('script/prepare-record',token,{'record':'recFixture'})
        before=fixture.call('repair/inspect',token)
        candidate=fixture.call('repair/propose',token,{'request_id':'repair-without-model-plan',
            'source_fingerprint':before['clone']['fingerprint']})['candidate']
        for op in ('validate','apply','rollback'):
            candidate=fixture.call('repair/'+op,token,{'request_id':'repair-only-'+op+'-request',
                'candidate_id':candidate['id'],'expected_version':candidate['version']})['candidate']
        self.assertEqual(fixture.call('repair/inspect',token)['clone']['source'],source(BEFORE))
        self.assertEqual(fixture.f.provider.calls,[]);self.assertEqual(fixture.f.video.calls,0)


if __name__ == '__main__': unittest.main()
