"""Cloud-only business integration: real ledger/services, synthetic external I/O.

This does not certify live Feishu delivery, model/media quality, or installation.
"""
from contextlib import ExitStack
from functools import partial
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from video_factory.agent_plans import VideoPlans
from video_factory.dispatch import dispatch_one, dispatch_with_results
from video_factory.feishu_bridge import meta, save
from video_factory.feishu_native_review import NativeReview
from video_factory.feishu_native_sync import NativeSync
from video_factory.local_native_cli import configure_app
from video_factory.runtime_store import RuntimeFault, RuntimeStore
from video_factory.scheduler import Scheduler
from video_factory.script_jobs import ScriptJobs
from video_factory.worker import Worker
import test_agent_script_plans as scripts
from review_fixture import BINDING
from test_feishu_native_sync import Remote
from test_local_kernel import Provider as VideoProvider


class OriginalTable(Remote):
    def __init__(self):
        super().__init__()
        self.schema = [{'field_id': value, 'field_name': name, 'type': 1}
                       for name, value in BINDING['fields'].items()]
        self.record_fields = {'task': 'generated_one', 'sku_id': 'sku_one',
                              'script': 'Synthetic original brief', 'source_revision': 'source_one'}

    def create_field(self, *args):
        field_id = super().create_field(*args)
        for field in self.schema:
            for i, option in enumerate(field.get('property', {}).get('options', [])):
                option['id'] = 'optFixture' + str(i)
        return field_id

    def find_task(self, base, table, name, task):
        assert (base, table, name, task) == ('bascnFixture', 'tblFixture', 'task', 'generated_one')
        return self.record(base, table, 'recFixture')


class LocalBusinessFlowTests(unittest.TestCase):
    service = scripts.ScriptPlanTests.service

    def setUp(self):
        scripts.ScriptPlanTests.setUp(self)
        self.remote = OriginalTable()
        configure_app(self.store, self.admin, self.project, 'cli_fixture', 'synthetic-app-secret', self.key)
        self.native = NativeSync(self.store, self.key, client_factory=lambda *_: self.remote)
        prepared = self.native.prepare(self.admin, self.project)
        self.native.enable(self.admin, self.project, prepared['plan_sha256'])
        self.jobs = ScriptJobs(self.store, client_factory=self.f.bridge.client_factory,
                              provider=self.provider, source_client_factory=lambda _: self.remote,
                              native_app_client_factory=lambda _: self.remote)
        self.plans = self.service()
        self.video = VideoProvider()
        self.store.put_secret(self.admin, 'video', 'synthetic-video-secret', self.key)
        assets = self.root/'assets'; assets.mkdir(mode=0o700)
        image = assets/'reference.png'; image.write_bytes(b'synthetic-reference')
        self.media = self.root/'synthetic.mp4'; self.media.write_bytes(b'synthetic-media-not-a-real-video')
        self.media.chmod(0o600)
        with self.store.connect() as db:
            save(db, 'video:profile:' + self.project, {
                'credential_ref': 'secret:video', 'billing_owner': 'fixture', 'region': 'global',
                'assets': {'sku_one': {'assets_root': str(assets), 'specification': {'duration': 5,
                    'references': [{'path': image.name, 'sha256': hashlib.sha256(image.read_bytes()).hexdigest()}]}}}})
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('video_factory.feishu_native_sync.NativeSync', return_value=self.native))
        self.stack.enter_context(patch('video_factory.script_jobs.ScriptJobs', return_value=self.jobs))
        self.stack.enter_context(patch('video_factory.dispatch.dispatch_one',
                                     side_effect=partial(dispatch_one, worker_factory=self.worker)))
        self.stack.enter_context(patch('video_factory.h3_provider.H3Provider.call',
                                     side_effect=AssertionError('real provider forbidden')))
        self.restart_scheduler()

    def worker(self, store):
        return Worker(store, provider_factory=lambda _: self.video, media_preflight=lambda: None,
                      media_collector=lambda *_: {'sha256': hashlib.sha256(self.media.read_bytes()).hexdigest(),
                          'location': str(self.media), 'verification': 'full_decode_passed'})

    def restart_scheduler(self):
        self.scheduler = Scheduler(RuntimeStore(self.store.root), self.project,
                                   execution_token=self.execution, master_key=self.key,
                                   media_root=self.root, dispatch=dispatch_with_results)

    def tick(self):
        result = self.scheduler.tick()
        self.assertIsNone(result['last_error'], result)
        return result

    def state(self):
        return self.store.inspect_task(self.admin, self.project, 'generated_one')['versions'][-1]['state']

    def event(self, before, after, event_id, actor='ou_employee'):
        with self.store.connect() as db:
            field = meta(db, 'native:config:' + self.project)['fields']['status']
        event = {'schema': '2.0', 'header': {'event_id': event_id,
                 'event_type': 'drive.file.bitable_record_changed_v1', 'app_id': 'cli_fixture',
                 'tenant_key': 'fixture_tenant'}, 'event': {'file_type': 'bitable',
                 'file_token': 'bascnFixture', 'table_id': 'tblFixture', 'operator_id': {'open_id': actor},
                 'action_list': [{'record_id': 'recFixture', 'action': 'record_edited',
                    'before_value': [{'field_id': field, 'field_value': json.dumps([before])}],
                    'after_value': [{'field_id': field, 'field_value': json.dumps([after])}]}]}}
        return NativeReview(self.store, client_factory=lambda _: self.remote).enqueue_verified(self.project, event)

    def generate(self, revision):
        plan = self.plans.prepare(self.planner, 'script_' + str(revision),
                                  {'record': 'recFixture', 'expected_revision': revision - 1})
        with self.assertRaisesRegex(RuntimeFault, 'OWNER_APPROVAL_REQUIRED'):
            self.plans.submit(self.planner, plan['plan_id'], plan['plan_sha256'])
        self.plans.owner(self.admin, 'approve', plan['plan_id'], plan['plan_sha256'])
        self.plans.submit(self.planner, plan['plan_id'], plan['plan_sha256'])
        for _ in range(3): self.tick()
        self.assertEqual(self.state(), 'awaiting_script_review')
        self.assertEqual(self.remote.record_fields['审核目标版本'], str(revision))
        self.assertEqual(self.remote.record_fields['状态'], '脚本待审核')
        return plan

    def test_original_row_reject_revise_video_approval_writeback_and_restart(self):
        self.generate(1)
        self.remote.record_fields.update({'状态': ['脚本退回'], '审核意见': 'Shorten the ending'})
        self.event('脚本待审核', '脚本退回', 'evt_reject')
        self.tick(); self.assertEqual(self.state(), 'rejected')
        self.remote.record_fields['source_revision'] = 'source_two'
        self.restart_scheduler()
        self.generate(2)
        self.assertIn('Shorten the ending', self.provider.calls[-1][1])
        self.remote.record_fields.update({'状态': ['脚本通过'], '审核意见': ''})
        self.event('脚本待审核', '脚本通过', 'evt_outsider', actor='ou_outsider')
        self.tick(); self.assertEqual(self.state(), 'awaiting_script_review')
        self.event('脚本待审核', '脚本通过', 'evt_accept')
        self.tick(); self.assertEqual(self.state(), 'ready')
        self.assertEqual(self.video.calls, 0)
        video = VideoPlans(self.store, self.project)
        plan = video.prepare(self.planner, 'generated_one', 2, 'video_two')
        with self.assertRaisesRegex(RuntimeFault, 'OWNER_APPROVAL_REQUIRED'):
            video.submit(self.planner, plan['plan_id'], plan['plan_sha256'])
        for _ in range(2): self.tick()
        self.assertEqual(self.video.calls, 0)
        video.owner(self.admin, 'approve', plan['plan_id'], plan['plan_sha256'])
        video.submit(self.planner, plan['plan_id'], plan['plan_sha256'])
        for _ in range(8): self.tick()
        self.assertEqual(self.state(), 'awaiting_video_review')
        self.assertEqual(self.remote.record_fields['状态'], '视频待审核')
        self.assertEqual(self.remote.record_fields['视频'], [{'file_token': 'fileFixture'}])
        self.restart_scheduler()
        for _ in range(3):
            video.submit(self.planner, plan['plan_id'], plan['plan_sha256']); self.tick()
        self.assertEqual(self.video.calls, 1)
        self.remote.record_fields['状态'] = ['视频通过']
        self.event('视频待审核', '视频通过', 'evt_video_accept')
        self.tick(); self.assertEqual(self.state(), 'accepted')
        self.assertTrue(self.event('视频待审核', '视频通过', 'evt_video_accept')['replayed'])
        self.tick()
        self.assertEqual(len(self.provider.calls), 2)
        self.assertEqual(len([w for w in self.remote.writes if w[0] == 'upload_finish']), 1)
        versions = self.store.inspect_task(self.admin, self.project, 'generated_one')['versions']
        self.assertEqual([(v['revision'], v['state']) for v in versions], [(1, 'rejected'), (2, 'accepted')])
        receipt = {'scope': 'synthetic_service_integration_only', 'script_calls': 2, 'video_calls': 1,
                   'real_provider_calls': 0, 'real_feishu_calls': 0, 'n8n_used': False,
                   'original_record': 'recFixture', 'revisions': 2,
                   'restart_and_replay_no_resubmit': True,
                   'human_independent_acceptance': False, 'live_sdk_delivery_verified': False}
        out = Path('.runtime/local-kernel-evidence'); out.mkdir(parents=True, exist_ok=True)
        (out/'business-flow.json').write_text(json.dumps(receipt, indent=2) + '\n')

    def reject_video(self):
        self.generate(1)
        self.remote.record_fields['状态'] = ['脚本通过']
        self.event('脚本待审核', '脚本通过', 'evt_script_accept')
        self.tick()
        videos = VideoPlans(self.store, self.project)
        plan = videos.prepare(self.planner, 'generated_one', 1, 'video_first')
        videos.owner(self.admin, 'approve', plan['plan_id'], plan['plan_sha256'])
        videos.submit(self.planner, plan['plan_id'], plan['plan_sha256'])
        for _ in range(8): self.tick()
        self.assertEqual(self.state(), 'awaiting_video_review')
        self.remote.record_fields.update({'状态': ['视频退回'], '审核意见': 'Replace the closing shot'})
        self.event('视频待审核', '视频退回', 'evt_video_reject')
        self.tick()
        self.assertEqual(self.state(), 'rejected')
        self.remote.record_fields['source_revision'] = 'source_two'

    def test_video_rejection_replans_same_row_and_requires_new_approvals(self):
        self.reject_video()
        # The remote label alone cannot turn a video rejection into a script one.
        self.remote.record_fields['状态'] = ['脚本退回']
        with self.assertRaisesRegex(RuntimeFault, 'SCRIPT_NATIVE_REVIEW_STATE_CONFLICT'):
            self.plans.prepare(self.planner, 'wrong_stage', {'record': 'recFixture', 'expected_revision': 1})
        self.remote.record_fields['状态'] = ['视频退回']
        original_update = self.remote.update_record
        def update_clearing_empty_fields(base, table, record, fields):
            result = original_update(base, table, record, fields)
            if fields.get('视频') == []:
                self.remote.record_fields.pop('视频', None)
                self.remote.record_fields.pop('视频摘要', None)
            return result
        self.remote.update_record = update_clearing_empty_fields
        self.restart_scheduler()
        self.generate(2)
        self.assertIn('Replace the closing shot', self.provider.calls[-1][1])
        self.assertNotIn('视频', self.remote.record_fields)
        self.assertNotIn('视频摘要', self.remote.record_fields)
        versions = self.store.inspect_task(self.admin, self.project, 'generated_one')['versions']
        self.assertIsNotNone(versions[0]['artifact'])
        self.assertEqual(versions[0]['state'], 'rejected')
        self.remote.record_fields['状态'] = ['脚本通过']
        self.event('脚本待审核', '脚本通过', 'evt_script_two_accept')
        self.tick()
        next_video = VideoPlans(self.store, self.project).prepare(self.planner, 'generated_one', 2, 'video_second')
        with self.assertRaisesRegex(RuntimeFault, 'OWNER_APPROVAL_REQUIRED'):
            VideoPlans(self.store, self.project).submit(self.planner, next_video['plan_id'], next_video['plan_sha256'])
        self.restart_scheduler()
        for _ in range(3): self.tick()
        self.assertEqual(self.video.calls, 1)
        self.assertEqual(len(self.provider.calls), 2)

    def test_video_revision_status_drift_blocks_provider_and_writeback(self):
        self.reject_video()
        plan = self.plans.prepare(self.planner, 'video_rework', {'record': 'recFixture', 'expected_revision': 1})
        self.plans.owner(self.admin, 'approve', plan['plan_id'], plan['plan_sha256'])
        self.plans.submit(self.planner, plan['plan_id'], plan['plan_sha256'])
        self.remote.record_fields['状态'] = ['脚本退回']
        with self.assertRaisesRegex(RuntimeFault, 'SCRIPT_NATIVE_SOURCE_CHANGED'):
            self.jobs.step(self.execution, self.project, 'generated_one', 2, self.key)
        self.assertEqual(len(self.provider.calls), 1)
        self.remote.record_fields['状态'] = ['视频退回']
        self.jobs.step(self.execution, self.project, 'generated_one', 2, self.key)
        self.remote.record_fields['状态'] = ['视频通过']
        with self.assertRaisesRegex(RuntimeFault, 'FEISHU_NATIVE_REMOTE_REVIEW_CONFLICT'):
            self.native.sync(self.project, self.root, lambda db: self.store.authorize(db, self.admin, 'admin'))
        self.assertEqual(self.remote.record_fields['状态'], ['视频通过'])
        self.assertEqual(self.video.calls, 1)


if __name__ == '__main__':
    unittest.main()
