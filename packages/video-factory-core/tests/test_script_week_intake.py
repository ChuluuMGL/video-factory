import copy
import unittest
from unittest.mock import Mock
from video_factory.script_week_intake import ReworkIntake, generation_task, generation_linked, OUTPUT_FIELDS
from video_factory.runtime_store import fingerprint, RuntimeFault
from video_factory.feishu_bridge import meta, save
from video_factory.script_week_inputs import REGISTRY
from video_factory.script_week import PREFIX
from test_script_week_inputs import InputTests


def row(record='recReview'):
    return {'record_id': record, 'fields': {'任务状态': '脚本待审核',
        '人工审核意见': 'Reduce repetition without changing approved facts.',
        '关联SKU': ['recSku'], '关联模特': ['recModel'], '参考视频': ['recReference'],
        'Hook原文': 'Approved hook', '视频脚本': 'OLD_OUTPUT_SENTINEL',
        '生成提示词': 'OLD_PROMPT_SENTINEL'}}


class PolicyTests(unittest.TestCase):
    def test_mixed_column_preserves_only_planning_facts_not_error_logs(self):
        value = row(); value['fields']['审核/异常说明'] = '用户场景：approved scene\n内容方向：approved goal\nhttps://example.test/docx/docPlan\nERROR: OLD_SCRIPT_SENTINEL'
        frozen = generation_task(value)
        self.assertEqual(frozen['fields']['shadow_plan_binding']['用户场景'], 'approved scene')
        self.assertNotIn('OLD_SCRIPT_SENTINEL', str(frozen))
        before = ReworkIntake()(value)['version']
        value['fields']['审核/异常说明'] += '\nNEW_RUNTIME_ERROR'
        self.assertEqual(ReworkIntake()(value)['version'], before)
        value['fields']['审核/异常说明'] = value['fields']['审核/异常说明'].replace('approved scene', 'changed approved scene')
        self.assertNotEqual(ReworkIntake()(value)['version'], before)

    def test_output_isolation_deep_freeze_and_material_preservation(self):
        original = row()
        for field in OUTPUT_FIELDS: original['fields'][field] = 'OLD_' + field
        original['fields']['unknown_upstream_field'] = 'SECRET_SENTINEL'
        inputs = {'task': original, 'materials': {'sku': {'approved': ['fact']}}}
        linked = {'inputs': inputs, 'input_sha256': fingerprint(inputs), 'provenance': {'task_record': 'recReview'}}
        result = generation_linked(linked)
        self.assertTrue(set(OUTPUT_FIELDS).isdisjoint(result['inputs']['task']['fields']))
        self.assertNotIn('unknown_upstream_field', result['inputs']['task']['fields'])
        original['fields']['人工审核意见'] = 'changed later'
        inputs['materials']['sku']['approved'].append('later')
        self.assertNotEqual(result['inputs'], inputs)
        self.assertEqual(result['input_sha256'], fingerprint(result['inputs']))
        self.assertEqual(original['fields']['任务状态'], '脚本待审核')

    def test_review_requires_explicit_allowlist_and_feedback(self):
        value = row(); policy = ReworkIntake(review_backfill=['recReview'])
        self.assertFalse(ReworkIntake()(value)['eligible'])
        self.assertTrue(policy(value)['eligible'])
        value['fields']['人工审核意见'] = ''
        self.assertEqual(policy(value)['reason'], 'REWORK_FEEDBACK_MISSING')

    def test_completed_video_and_published_not_replayed_by_allowlist(self):
        policy = ReworkIntake(review_backfill=['recReview']); value = row()
        for state in ('视频待生产', '视频待审核', '已发布', '已停止（旧计划保留）', ''):
            value['fields']['任务状态'] = state
            self.assertFalse(policy(value)['eligible'])
        value['fields'].update(任务状态='脚本待审核', 发布链接='https://example.test/published')
        self.assertEqual(policy(value)['reason'], 'SOURCE_ALREADY_PUBLISHED')

    def test_output_status_and_log_changes_do_not_change_input_version(self):
        policy = ReworkIntake(review_backfill=['recReview']); value = row(); before = policy(value)['version']
        value['fields'].update(任务状态='脚本待生产', 视频脚本='new production output', 生成提示词='new prompt', 审核异常说明='noise')
        self.assertEqual(policy(value)['version'], before)
        value['fields']['人工审核意见'] = 'A genuinely new revision requirement'
        self.assertNotEqual(policy(value)['version'], before)


class IntakeTests(unittest.TestCase):
    setUp = InputTests.setUp
    tearDown = InputTests.tearDown
    prepare = InputTests.prepare
    start = InputTests.start
    adapter = InputTests.adapter

    def compile(self, value, now):
        result = InputTests.compile(self, value, now)
        result['sample']['version'] = fingerprint(generation_task(value))
        result['snapshot']['brief'] = 'snapshot:' + fingerprint(result['sample'])
        result['snapshot']['input_sha256'] = fingerprint({k:v for k,v in result['snapshot'].items() if k!='input_sha256'})
        return result

    def admit(self, snapshots):
        with self.f.store.connect() as db:
            week = meta(db, PREFIX+self.f.project)
            for snapshot in snapshots: week['snapshots'][snapshot['brief']] = {'snapshot': snapshot}
            save(db, PREFIX+self.f.project, week)

    def test_preserved_inventory_review_backfill_restart_and_dedup(self):
        a = self.adapter(); self.rows = [row()]; a.read_new()
        a.intake = ReworkIntake(review_backfill=['recReview'])
        first = a.read_new(); self.assertEqual(len(first), 1)
        self.assertEqual(a.read_new(), first); self.assertEqual(self.compiler.call_count, 1)
        self.admit(first)
        self.rows[0]['fields']['视频脚本'] = 'later production output'
        self.assertEqual(a.read_new(), []); self.assertEqual(self.compiler.call_count, 1)
        self.rows[0]['fields']['人工审核意见'] = 'new explicit feedback'
        self.assertEqual(len(a.read_new()), 1); self.assertEqual(self.compiler.call_count, 2)

    def test_multiple_new_tasks_not_consumed_by_first_compilation(self):
        a = self.adapter(); a.read_new(); a.intake = ReworkIntake()
        for rid in ('recNew1', 'recNew2'):
            value = row(rid); value['fields']['任务状态'] = '脚本待生产'
            for field in (*OUTPUT_FIELDS, '人工审核意见'): value['fields'].pop(field, None)
            self.rows.append(value)
        first = a.read_new(); self.admit(first)
        second = a.read_new(); self.assertEqual(len(second), 1)
        self.assertNotEqual(first[0]['record_id'], second[0]['record_id'])

    def test_transport_failure_does_not_consume_input_and_skips_are_durable(self):
        a = self.adapter(); self.rows = [row(), {'record_id':'recBlank','fields':{}}]; a.read_new()
        a.intake = ReworkIntake(review_backfill=['recReview'])
        self.compiler.side_effect = TimeoutError
        with self.assertRaises(TimeoutError): a.read_new()
        self.compiler.side_effect = self.compile
        result = a.read_new(); self.assertEqual(len(result), 1); self.admit(result)
        a.read_new()
        with self.f.store.connect() as db: data = meta(db, REGISTRY+self.f.project)
        self.assertEqual(data['discovery']['reason_counts']['SOURCE_STATUS_EMPTY'], 1)
        self.assertEqual(data['discovery']['reason_counts']['INPUT_ALREADY_PROCESSED'], 1)
        self.assertNotIn('OLD_OUTPUT_SENTINEL', str(data['discovery']))

    def test_needs_evidence_is_not_retried_on_output_only_change(self):
        a = self.adapter(); self.rows = [row()]; a.read_new(); a.intake = ReworkIntake(review_backfill=['recReview'])
        self.compiler.side_effect = None; self.compiler.return_value = {'state':'needs_evidence','reason':'APPROVED_FACT_MISSING'}
        a.read_new(); self.rows[0]['fields']['视频脚本'] = 'different old output'; a.read_new()
        self.assertEqual(self.compiler.call_count, 1)


if __name__ == '__main__': unittest.main()
