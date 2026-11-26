"""Synthetic read-only workspace contracts."""
from types import SimpleNamespace
import unittest

import test_local_setup as fixture
from video_factory import automation
from video_factory.agent_api import AgentAPI
from video_factory.feishu_bridge import save
from video_factory.runtime_store import RuntimeFault
from video_factory.worker import job_key


class ObservationTests(unittest.TestCase):
    def setUp(self):
        fixture.SetupTests.setUp(self)
        fixture.SetupTests.put(self)

    def test_latest_only_and_failure_pagination_with_safe_codes(self):
        project = self.plan['project']
        with self.store.connect() as db:
            for task, revision, state in [('old', 1, 'failed'), ('old', 2, 'accepted'),
                                          ('other', 1, 'script_submission_unknown')]:
                db.execute('INSERT INTO tasks(project,id,revision,state,input,input_digest,configuration_digest) '
                           'VALUES(?,?,?,?,?,?,?)', (project, task, revision, state, '{}', 'a', 'b'))
            for n in range(101):
                task = f'failed-{n:03}'
                db.execute('INSERT INTO tasks(project,id,revision,state,input,input_digest,configuration_digest) '
                           'VALUES(?,?,?,?,?,?,?)', (project, task, 1, 'failed', '{}', 'a', 'b'))
            save(db, job_key(project, 'failed-000', 1), {'failure_code': 'PRIVATE_CANARY_BODY'})
            save(db, job_key(project, 'failed-001', 1), {'failure_code': 'PROVIDER_BALANCE_REQUIRED'})
        token = automation.issue(self.store, self.admin, project, scope='agent_read')['token']
        api = AgentAPI(self.store, project, SimpleNamespace())
        def call(path, **body):
            return api.call('/v1/agent/' + path, token, {'project': project, **body})
        data = call('project_stats')
        self.assertEqual(data['state_counts'], {'accepted': 1, 'failed': 101, 'script_submission_unknown': 1})
        self.assertEqual(data['success_rate'], 1/102)
        first = call('list_failures')
        self.assertEqual(len(first['items']), 100)
        self.assertEqual(first['items'][0]['error_code'], 'TASK_FAILED')
        self.assertEqual(first['items'][1]['error_code'], 'PROVIDER_BALANCE_REQUIRED')
        self.assertNotIn('PRIVATE_CANARY_BODY', str(first))
        self.assertEqual([x['id'] for x in call('list_failures', after=first['next_after'])['items']],
                         ['failed-100', 'other'])
        self.assertIn('project_stats', call('capabilities')['available'])
        with self.assertRaisesRegex(RuntimeFault, 'FIELDS_INVALID'):
            call('project_stats', after='ignored')
        with self.assertRaisesRegex(RuntimeFault, 'PROJECT_DENIED'):
            api.call('/v1/agent/list_failures', token, {'project': 'different'})
