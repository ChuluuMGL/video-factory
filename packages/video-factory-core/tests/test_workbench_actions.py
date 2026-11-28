"""Workbench receipts using only a synthetic SQLite project."""
from types import SimpleNamespace
import unittest

import test_local_setup as fixture
from video_factory.agent_api import AgentAPI
from video_factory.runtime_store import RuntimeFault
from video_factory.workbench_actions import revised
from video_factory.workbench_delegation import delegation

PROJECT = 'brand_one'
PRINCIPAL = 'auth-' + 'a' * 64


class ScriptEditRuleTests(unittest.TestCase):
    def test_removes_leading_absolute_modifier_and_its_particle_per_line(self):
        script = '绝对第一的保温杯，持续保温 24 小时。\n  全网第一 的 杯盖。\n全网绝对第一的杯身。'
        edited = revised(script, '去掉绝对化用语，按商品事实修正时长', '保温 6 小时。')
        self.assertEqual(edited, '保温杯，持续保温 6 小时。\n  杯盖。\n杯身。')

    def test_leaves_unrelated_line_start_and_product_particle_in_place(self):
        script = '的确能满足日常使用。\n磨砂的杯身，避免使用绝对第一。'
        self.assertEqual(revised(script, '去掉绝对化用语', ''), '的确能满足日常使用。\n磨砂的杯身，避免使用。')


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        fixture.SetupTests.setUp(self)
        fixture.SetupTests.put(self)
        token_file = self.root / 'workbench-key'
        delegation(self.store, self.admin, PROJECT, PRINCIPAL, ['repair', 'edit', 'review'], output=token_file)
        self.token = token_file.read_text()
        self.api = AgentAPI(self.store, PROJECT, SimpleNamespace(status=lambda: {}))

    def task(self, name, state):
        self.store.create_task(self.admin, PROJECT, name, {
            'sku_id': 'sku_one', 'script': '全网绝对第一\n普通测试正文\nBearer fixture-secret', 'source_revision': 'fixture',
        })
        with self.store.connect() as db:
            db.execute('UPDATE tasks SET state=? WHERE project=? AND id=?', (state, PROJECT, name))

    def call(self, route, principal=PRINCIPAL, **body):
        return self.api.call('/v1/agent/' + route, self.token, {'project': PROJECT, **body}, principal=principal)

    @staticmethod
    def confirmation(result):
        receipt = result['confirmation']
        return {key: receipt[key] for key in ('confirmation_id', 'expected_digest', 'expected_version')}

    def test_read_projections_are_scoped_and_redacted(self):
        self.task('script', 'awaiting_script_review')
        self.task('storyboard', 'awaiting_storyboard_review')
        self.task('video', 'awaiting_video_review')
        self.task('unknown', 'script_submission_unknown')
        result = self.call('reviews')
        self.assertEqual({row['stage'] for row in result['items']}, {'script', 'storyboard', 'video'})
        self.assertNotIn('fixture-secret', str(result))
        self.assertEqual(self.call('stats', window='all')['metrics']['by_status']['awaiting_review'], 3)
        failure = self.call('failures')['items'][0]
        self.assertTrue(failure['reconciliation_required'])
        self.assertFalse(failure['automatic_resubmit'])
        with self.assertRaisesRegex(RuntimeFault, 'PROJECT_DENIED'):
            self.api.call('/v1/agent/reviews', self.token, {'project': 'brand_two'})
        with self.assertRaisesRegex(RuntimeFault, 'UNKNOWN_RECONCILE_FIRST'):
            self.call('repair/preview', target_type='task_state', target_id=failure['task_key'],
                      expected_version=failure['version_token'], repair_action='requeue_failed_task')

    def test_script_changes_need_exact_confirmation_and_replay_once(self):
        self.task('script', 'awaiting_script_review')
        row = self.call('reviews')['items'][0]
        args = {'task_key': row['task_key'], 'expected_version': row['version_token']}
        self.assertEqual(self.call('script/prereview', **args)['model_calls'], 0)
        preview = self.call('script/edit-preview', **args, instruction='去掉绝对化用语')
        confirm = {**self.confirmation(preview), 'draft_sha256': preview['diff']['draft_sha256']}
        with self.assertRaisesRegex(RuntimeFault, 'ROLE_DENIED'):
            self.call('script/edit-commit', principal='auth-' + 'b' * 64, **confirm)
        with self.assertRaisesRegex(RuntimeFault, 'CONFIRMATION_CHANGED'):
            self.call('script/edit-commit', **{**confirm, 'expected_digest': '0' * 64})
        reply = self.call('script/edit-commit', **confirm)
        self.assertEqual(reply['new_revision'], 2)
        self.assertTrue(self.call('script/edit-commit', **confirm)['replayed'])
        self.assertEqual(len(self.call('reviews')['items']), 1)

    def test_model_edit_requires_cost_consent_and_replays_without_a_second_request(self):
        from unittest.mock import patch
        self.task('script', 'awaiting_script_review')
        self.api.scheduler.key = self.key
        row = self.call('reviews')['items'][0]
        with patch('video_factory.workbench_actions.ScriptProvider') as provider:
            provider.return_value.revise.return_value = {'script': '这里是通顺的新稿。'}
            preview = self.call('script/edit-preview', task_key=row['task_key'],
                                expected_version=row['version_token'], instruction='把中间那段删掉')
            self.assertEqual(preview['mode'], 'edit_generate_preview')
            self.assertIn('1 次 deepseek-flash', preview['cost_summary'])
            consent = self.confirmation(preview)
            with self.assertRaisesRegex(RuntimeFault, 'ROLE_DENIED'):
                self.call('script/edit-generate', **consent, accept_provider_charges=True)
            delegated = self.root / 'm5-cost-grant'
            delegation(self.store, self.admin, PROJECT, PRINCIPAL, ['edit', 'cost'], output=delegated)
            self.token = delegated.read_text()
            with self.assertRaisesRegex(RuntimeFault, 'COST_CONSENT_REQUIRED'):
                self.call('script/edit-generate', **consent, accept_provider_charges=False)
            provider.return_value.revise.assert_not_called()
            result = self.call('script/edit-generate', **consent, accept_provider_charges=True)
            self.assertEqual(result['model_calls'], 1)
            self.assertIn('+这里是通顺的新稿。', result['diff']['patch'])
            self.assertIn('¥0', result['cost_summary'])
            self.assertTrue(self.call('script/edit-generate', **consent, accept_provider_charges=True)['replayed'])
            self.assertEqual(self.call('reviews')['items'][0]['revision'], 1)
            commit = self.call('script/edit-commit', **self.confirmation(result),
                               draft_sha256=result['diff']['draft_sha256'])
            self.assertEqual(commit['new_revision'], 2)
            self.assertEqual(commit['provider_requests'], 0)
            provider.return_value.revise.assert_called_once()

    def test_uncertain_model_edit_is_never_resent(self):
        from unittest.mock import patch
        self.task('script', 'awaiting_script_review')
        self.api.scheduler.key = self.key
        delegated = self.root / 'm5-cost-grant'
        delegation(self.store, self.admin, PROJECT, PRINCIPAL, ['edit', 'cost'], output=delegated)
        self.token = delegated.read_text()
        row = self.call('reviews')['items'][0]
        preview = self.call('script/edit-preview', task_key=row['task_key'],
                            expected_version=row['version_token'], instruction='删掉中段')
        with patch('video_factory.workbench_actions.ScriptProvider') as provider:
            provider.return_value.revise.side_effect = TimeoutError()
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeFault, 'SCRIPT_SUBMISSION_UNCERTAIN_NO_RETRY'):
                    self.call('script/edit-generate', **self.confirmation(preview), accept_provider_charges=True)
            provider.return_value.revise.assert_called_once()
        self.assertEqual(self.call('reviews')['items'][0]['revision'], 1)

    def test_daily_triage_counts_actionable_tasks_and_projects_only_bound_urls(self):
        from video_factory.feishu_bridge import configuration, save, source_key, target
        self.task('script', 'awaiting_script_review')
        self.task('unknown', 'submission_unknown')
        self.task('failure', 'failed')
        self.task('unverified_failure', 'failed')
        binding = configuration({**fixture.BINDING, 'web_host': 'tenant-test.feishu.cn'})
        with self.store.connect() as db:
            save(db, 'feishu:binding:' + PROJECT, binding)
            save(db, source_key(PROJECT, 'script', 1), {
                'snapshot': {'target': target(binding), 'record_id': 'recScript'},
            })
            save(db, source_key(PROJECT, 'unknown', 1), {
                'snapshot': {'target': target(binding), 'record_id': 'recX&url=evil'},
            })
            self.store.audit(db, 'worker', 'provider_failed_no_retry', PROJECT, 'failure', 1)
        result = self.call('daily-triage')
        self.assertEqual([group['count'] for group in result['triage']], [1, 1, 1])
        self.assertEqual(result['triage'][2]['items'][0]['feishu_record_url'],
                         'https://tenant-test.feishu.cn/base/bascnFixture?table=tblFixture&record=recScript')
        self.assertNotIn('feishu_record_url', result['triage'][0]['items'][0])
        self.assertNotIn('feishu_record_url', result['triage'][1]['items'][0])
        with self.assertRaisesRegex(RuntimeFault, 'FEISHU_WEB_HOST_INVALID'):
            configuration({**binding, 'web_host': 'tenant.feishu.cn.evil.test'})

    def test_review_does_not_approve_paid_stage_without_role(self):
        self.task('story', 'awaiting_storyboard_review')
        row = self.call('reviews')['items'][0]
        preview = self.call('review/prepare', task_key=row['task_key'], expected_version=row['version_token'],
                            stage='storyboard', decision='accept', advance_next_stage=True)
        with self.assertRaisesRegex(RuntimeFault, 'CONFIRMATION_REQUIRED'):
            self.call('review/commit')
        reply = self.call('review/commit', **self.confirmation(preview), accept_provider_charges=True)
        self.assertTrue(reply['review_recorded'])
        self.assertFalse(reply['next_stage_plan']['enqueued'])
        self.assertFalse(reply['next_stage_plan']['video_spending_approved'])

    def test_media_handles_are_short_lived_version_bound_and_path_free(self):
        import hashlib
        from video_factory.runtime_store import canonical, exclusive_write
        from video_factory.workbench_media import WorkbenchMedia
        self.task('video', 'awaiting_video_review')
        clip = self.root / 'synthetic.mp4'
        data = b'synthetic bytes, not a real clip'
        exclusive_write(clip, data)
        artifact = {'sha256': hashlib.sha256(data).hexdigest(), 'location': str(clip),
                    'verification': 'full_decode_passed'}
        with self.store.connect() as db:
            db.execute('UPDATE tasks SET artifact=? WHERE project=? AND id=?', (canonical(artifact), PROJECT, 'video'))
        now = [1000]
        self.api.media = WorkbenchMedia(self.store, PROJECT, self.root, clock=lambda: now[0])
        row = self.call('reviews', stage='video')['items'][0]
        token = row['preview']['media_preview_token']
        self.assertNotIn(str(clip), str(row))
        stream, size = self.api.media.open(token)
        with stream:
            self.assertEqual(stream.read(), data)
        self.assertEqual(size, len(data))
        now[0] += 300
        with self.assertRaisesRegex(RuntimeFault, 'TOKEN_EXPIRED'):
            self.api.media.open(token)

    def test_repair_preserves_failed_revision_and_refuses_stale_confirmation(self):
        self.task('video_failure', 'failed')
        with self.store.connect() as db:
            self.store.audit(db, 'worker', 'provider_failed_no_retry', PROJECT, 'video_failure', 1)
        failure = self.call('failures')['items'][0]
        preview = self.call('repair/preview', target_type='task_state', target_id=failure['task_key'],
                            expected_version=failure['version_token'], repair_action='requeue_failed_task')
        confirmation = self.confirmation(preview)
        receipt = self.call('repair/apply', **confirmation)
        self.assertTrue(receipt['applied'])
        self.assertFalse(receipt['scheduled'])
        self.assertTrue(self.call('repair/apply', **confirmation)['replayed'])
        with self.store.connect() as db:
            values = [tuple(row) for row in db.execute('SELECT revision,state FROM tasks WHERE project=? ORDER BY revision', (PROJECT,))]
        self.assertEqual(values, [(1, 'failed'), (2, 'ready')])


class NativeWorkbenchTests(unittest.TestCase):
    def test_edit_writeback_preserves_review_columns_and_receipt_reads_current_status(self):
        import test_local_native
        from video_factory.dispatch import authorize
        from video_factory.feishu_bridge import meta
        from video_factory.workbench_writeback import flush

        test_local_native.LocalNativeTests.setUp(self)
        test_local_native.LocalNativeTests.task(self)
        signer = self.root / 'edit-grant'
        delegation(self.store, self.admin, 'brand', PRINCIPAL, ['edit'], output=signer)
        api = AgentAPI(self.store, 'brand', test_local_native.LocalNativeTests.scheduler(self))
        api.scheduler.key = None  # This test exercises rule edits and synthetic Feishu writeback.
        call = lambda route, **body: api.call('/v1/agent/' + route, signer.read_text(),
                                             {'project': 'brand', **body}, principal=PRINCIPAL)
        authorize_tick = lambda db: authorize(db, self.execution, 'brand')
        self.native.sync('brand', self.root, authorize_tick)
        row = call('reviews')['items'][0]
        original = self.remote.record_fields['脚本']
        preview = call('script/edit-preview', task_key=row['task_key'], expected_version=row['version_token'],
                       instruction=f'将“{original}”改为“新版合成脚本”')
        self.remote.record_fields['审核意见'] = '保留人工备注'
        consent = WorkbenchTests.confirmation(preview)
        result = call('script/edit-commit', **consent, draft_sha256=preview['diff']['draft_sha256'])
        self.assertEqual(result['board_writeback'], 'reconciliation_required')
        with self.store.connect() as db:
            config = meta(db, 'native:config:brand')
        self.assertEqual(flush(self.store, 'brand', self.native, config, authorize_tick)['status'], 'synced')
        refreshed = call('workbench/receipt', confirmation_id=consent['confirmation_id'])
        self.assertEqual(refreshed['board_writeback'], 'synced')
        self.assertIn('回读确认', refreshed['change_summary'])
        self.assertEqual(self.remote.record_fields['脚本'], '新版合成脚本')
        self.assertEqual(self.remote.record_fields['审核意见'], '保留人工备注')
        self.assertEqual(self.remote.record_fields['状态'], '脚本待审核')

    def test_ambiguous_native_review_write_is_never_repeated(self):
        import test_local_native
        from video_factory.dispatch import authorize
        from video_factory.feishu_bridge import meta
        from video_factory.workbench_writeback import flush
        from unittest.mock import patch

        test_local_native.LocalNativeTests.setUp(self)
        test_local_native.LocalNativeTests.task(self)
        signer = self.root / 'review-grant'
        delegation(self.store, self.admin, 'brand', PRINCIPAL, ['review'], output=signer)
        api = AgentAPI(self.store, 'brand', test_local_native.LocalNativeTests.scheduler(self))
        call = lambda route, **body: api.call('/v1/agent/' + route, signer.read_text(),
                                             {'project': 'brand', **body}, principal=PRINCIPAL)
        authorize_tick = lambda db: authorize(db, self.execution, 'brand')
        self.native.sync('brand', self.root, authorize_tick)
        row = call('reviews')['items'][0]
        preview = call('review/prepare', task_key=row['task_key'], expected_version=row['version_token'],
                       stage='script', decision='accept')
        consent = WorkbenchTests.confirmation(preview)
        self.assertEqual(call('review/commit', **consent)['board_writeback'], 'reconciliation_required')
        with self.store.connect() as db:
            config = meta(db, 'native:config:brand')
        original = self.remote.update_record

        def interrupted(*args):
            original(*args)
            raise RuntimeFault('BASE_RESULTS_REQUEST_UNKNOWN_READ_STATUS')

        with patch.object(self.remote, 'update_record', side_effect=interrupted) as update:
            result = flush(self.store, 'brand', self.native, config, authorize_tick)
            self.assertEqual(result['status'], 'attention_required')
            self.assertIsNone(flush(self.store, 'brand', self.native, config, authorize_tick))
            update.assert_called_once()
        self.assertEqual(self.remote.record_fields['状态'], '脚本通过')
        self.assertEqual(call('review/commit', **consent)['board_writeback'], 'reconciliation_required')
