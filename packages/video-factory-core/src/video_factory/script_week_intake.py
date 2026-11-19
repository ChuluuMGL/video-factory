"""Pure shadow intake; source outputs never become generation input."""
import copy
import re
from .runtime_store import RuntimeFault, fingerprint

INPUT_FIELDS = ('视频编号', '关联SKU', '关联模特', '参考视频', '关联素材',
                '人工审核意见', 'Hook', 'Hook原文', 'Content Direction',
                '计划发布日', '发布账号', '发布时段', '目标时长')
OUTPUT_FIELDS = ('视频脚本', '字幕+配音文案', '生成提示词', '生成平台任务ID',
                 '当前审核版本', '成片文件', '发布链接', '审核/异常说明')
POLICY = 'shadow_rework_output_isolation_v1'
PLAN_FIELDS = ('用户场景', '内容方向', '唯一计划键', '实验组', '计划日期', '完整SKU')


def text(value):
    if value is None: return ''
    if isinstance(value, str): return value
    if isinstance(value, list): return ''.join(text(v) for v in value)
    if isinstance(value, dict):
        return text(value.get('text', value.get('name', value.get('value', ''))))
    return str(value)


def generation_task(row):
    if not isinstance(row, dict) or not isinstance(row.get('fields'), dict):
        raise RuntimeFault('WEEK_SOURCE_RECORD_INVALID')
    fields = {k: copy.deepcopy(row['fields'][k]) for k in INPUT_FIELDS if k in row['fields']}
    # Legacy source mixes plan bindings and runtime logs in one column. Preserve
    # only named planning facts; never copy the column or parse error prose.
    note = text(row['fields'].get('审核/异常说明'))
    plan = {}
    for key in PLAN_FIELDS:
        values = re.findall(r'(?:^|\n)' + re.escape(key) + r'：([^\n]+)', note)
        values = list(dict.fromkeys(v.strip() for v in values))
        if len(values) > 1 or any(len(v) > 2000 for v in values):
            raise RuntimeFault('WEEK_PLAN_METADATA_AMBIGUOUS')
        if values: plan[key] = values[0]
    plan['document_ids'] = sorted(set(re.findall(r'/docx/([A-Za-z0-9]+)', note)))
    fields['shadow_plan_binding'] = plan
    prompt = text(row['fields'].get('生成提示词'))
    if (not row['fields'].get('视频脚本') and not row['fields'].get('字幕+配音文案')
            and re.match(r'^【[^\n】]*任务简报[^】]*】', prompt)):
        fields['shadow_initial_brief'] = prompt
    # Local compiler precondition only, never a write to production.
    fields['任务状态'] = '脚本待生产'
    return {'record_id': row['record_id'], 'fields': fields}


def generation_linked(linked):
    if fingerprint(linked['inputs']) != linked['input_sha256']:
        raise RuntimeFault('WEEK_INPUT_DIGEST_MISMATCH')
    result = copy.deepcopy(linked)
    result['inputs']['task'] = generation_task(linked['inputs']['task'])
    result['input_sha256'] = fingerprint(result['inputs'])
    return result


class ReworkIntake:
    def __init__(self, *, review_backfill=()):
        if (not isinstance(review_backfill, (list, tuple)) or len(review_backfill) > 70
                or any(not isinstance(r, str) or not r.startswith('rec') for r in review_backfill)):
            raise RuntimeFault('WEEK_BACKFILL_ALLOWLIST_INVALID')
        self.review_backfill = frozenset(review_backfill)

    def __call__(self, row):
        f = row['fields']; status = text(f.get('任务状态')).strip()
        feedback = text(f.get('人工审核意见')).strip()
        has_output = any(bool(f.get(k)) for k in OUTPUT_FIELDS[:6])
        version = fingerprint({'policy': POLICY, 'record': row['record_id'], 'status': status})
        def result(eligible, reason, kind='rework'):
            return dict(eligible=eligible, reason=reason, kind=kind, version=version)
        if f.get('发布链接'): return result(False, 'SOURCE_ALREADY_PUBLISHED')
        if status == '脚本待审核':
            if row['record_id'] not in self.review_backfill:
                return result(False, 'REVIEW_BACKFILL_NOT_AUTHORIZED')
            if not feedback: return result(False, 'REWORK_FEEDBACK_MISSING')
        elif status != '脚本待生产':
            return result(False, 'SOURCE_STATUS_EMPTY' if not status else 'SOURCE_STATUS_NOT_SCRIPT_PENDING')
        if has_output and not feedback: return result(False, 'REWORK_FEEDBACK_MISSING')
        if any(not f.get(k) for k in ('关联SKU', '关联模特', '参考视频')):
            return result(False, 'REQUIRED_RELATION_MISSING')
        try:
            version = fingerprint({'policy': POLICY, 'task': generation_task(row)})
        except RuntimeFault as error:
            if str(error) != 'WEEK_PLAN_METADATA_AMBIGUOUS': raise
            return result(False, 'PLAN_METADATA_AMBIGUOUS')
        return result(True, 'ELIGIBLE_REWORK' if feedback else 'ELIGIBLE_NEW',
                      'rework' if feedback else 'new')
