"""Read-only projections of local ledger evidence. Never probe providers."""
import json

from .feishu_bridge import meta
from .feishu_native_review import source_key, sync_key
from .h3_provider import REJECTION_CODES as VIDEO_REJECTIONS
from .runtime_store import RuntimeFault
from .script_budget import policy
from .script_jobs import CONFIRMED_FAILURES, key as script_key
from .worker import job_key
from .workbench_details import record_url
from .workbench_views import SCRIPT_ERRORS, brief, envelope, iso, stage_status, version

EVENTS = {
    'task_created': 'created', 'script_submission_intent': 'script_request_reserved',
    'script_provider_rejected_no_retry': 'script_request_rejected',
    'script_draft_received': 'script_received', 'script_budget_blocked': 'budget_blocked',
    'worker_submission_intent': 'video_request_reserved', 'submission_intent': 'video_request_reserved',
    'provider_receipt': 'video_receipt_recorded', 'provider_failed_no_retry': 'video_failed',
    'artifact_recorded': 'artifact_verified',
    **{f'review_{stage}_{decision}': f'{stage}_{decision}'
       for stage in ('script', 'storyboard', 'video') for decision in ('accept', 'reject')},
}
ERRORS = frozenset(SCRIPT_ERRORS) | CONFIRMED_FAILURES | frozenset(VIDEO_REJECTIONS.values())
BLOCKERS = ('FEISHU_RECONCILIATION_REQUIRED', 'RECONCILIATION_REQUIRED', 'SCHEDULER_STEP_FAILED')


def task_logs(db, project, task, revision, now):
    if revision is not None and (type(revision) is not int or not 1 <= revision <= 2**31 - 1):
        raise RuntimeFault('AGENT_REVISION_REQUIRED')
    row = db.execute(
        'SELECT * FROM tasks WHERE project=? AND id=? AND (? IS NULL OR revision=?) '
        'ORDER BY revision DESC LIMIT 1', (project, task, revision, revision)).fetchone()
    if row is None:
        raise RuntimeFault('TASK_MISSING')
    revision = row['revision']
    stage, state = stage_status(db, row)
    keys = tuple(EVENTS)
    events = db.execute(
        'SELECT at, action, revision FROM audit WHERE project=? AND task=? AND revision<=? AND action IN ('
        + ','.join('?' for _ in keys) + ') ORDER BY seq DESC LIMIT 21', (project, task, revision, *keys)
    ).fetchall()
    script = meta(db, script_key(project, task, revision)) or {}
    video = meta(db, job_key(project, task, revision)) or {}
    codes = [script.get('failure_code'), script.get('submission_error'), video.get('failure_code')]
    code = next((code for code in codes if isinstance(code, str) and code in ERRORS), None)
    if script.get('state') == 'budget_blocked':
        code = 'SCRIPT_BUDGET_EXHAUSTED'
    if not code and state in ('failed', 'submission_unknown'):
        code = 'SUBMISSION_UNCERTAIN' if state == 'submission_unknown' else 'TASK_FAILED'
    native = meta(db, 'native:config:' + project) or {}
    synced = meta(db, sync_key(project, task, revision, stage)) or {}
    linked = bool(meta(db, source_key(project, task)))
    outbox = 'workbench:board:' + project + ':'
    writeback = db.execute(
        "SELECT json_extract(value,'$.state') FROM meta WHERE key>=? AND key<? "
        "AND json_extract(value,'$.task')=? AND json_extract(value,'$.revision')=? "
        "ORDER BY rowid DESC LIMIT 1", (outbox, outbox + '~', task, revision)).fetchone()
    setup = meta(db, 'setup:project:' + project) or {}
    config = setup.get('configuration', {})
    sku = json.loads(row['input']).get('sku_id')
    products = config.get('project', {}).get('products', [])
    sku_bound = bool(sku and any(item.get('sku_id') == sku for item in products))
    assets = (meta(db, 'video:profile:' + project) or {}).get('assets', {})
    refs = assets.get(sku, {}).get('specification', {}).get('references', [])
    return envelope('inspect_task_logs', project, now, {
        'brief': brief(f'任务 v{revision} 当前阶段为 {stage}，状态为 {state}。'),
        'task_status': {'task_id': task, 'revision': revision, 'stage': stage,
                        'canonical_status': state, 'version_token': version(row), 'error_code': code,
                        'reconciliation_required': state == 'submission_unknown'},
        'history': [{'event': EVENTS[item['action']], 'revision': item['revision'], 'at': iso(item['at'])}
                    for item in reversed(events[:20])],
        'has_more': len(events) > 20,
        **record_url(db, project, row),
        'model_receipt': {'receipt_recorded': bool(script.get('provider_id') or video.get('provider_receipt') or row['provider_id']),
                          'result_recorded': bool(row['artifact'] or script.get('state') in ('awaiting_review', 'awaiting_script_review'))},
        'board_receipt': {'row_bound': linked,
                          'status': ('not_configured' if not native.get('enabled') else
                                     'synced' if (writeback and writeback[0] == 'synced') or synced.get('complete') else
                                     'readback_pending' if writeback or synced else 'not_observed')},
        'bindings': {'sku_bound': sku_bound, 'references_bound': bool(refs), 'reference_count': len(refs)},
    }, board='feishu_bitable' if native.get('enabled') else 'self_hosted')


def workflow_health(db, project, status, now):
    native = meta(db, 'native:config:' + project) or {}
    enabled = native.get('enabled') is True
    observed = (meta(db, 'native:last:' + project) or {}).get('sync', {}).get('status')
    events = status.get('feishu_events')
    blocker = status.get('last_error')
    budget = policy(db, project)
    limit, used = budget['max_calls'], budget['used_calls']
    recent = db.execute(
        'SELECT state, COUNT(*) FROM tasks t WHERE project=? AND state IN '
        "('script_submission_unknown','submission_unknown','failed') AND revision="
        '(SELECT MAX(v.revision) FROM tasks v WHERE v.project=t.project AND v.id=t.id) GROUP BY state',
        (project,)).fetchall()
    states = {row[0]: row[1] for row in recent}
    return envelope('inspect_workflow_health', project, now, {
        'brief': brief('已读取本地引擎、飞书绑定和脚本预算的最近记录。'),
        'health': {
            'engine_state': status.get('state') if status.get('state') in (
                'ready', 'read_only', 'running', 'waiting', 'attention_required', 'stopped') else 'unknown',
            'last_tick_at': iso(status.get('last_tick_at')),
            'execution_enabled': status.get('execution_enabled') is True,
            'binding_configured': enabled,
            'board_connectivity': ('not_configured' if not enabled else
                                   'attention_required' if blocker == 'FEISHU_RECONCILIATION_REQUIRED' else
                                   'last_sync_observed' if observed == 'synced' else
                                   'receiver_running_unverified' if events == 'receiver_running_delivery_unverified' else
                                   'configured_unverified'),
            'max_calls': limit, 'used_calls': used,
            'remaining_calls': None if limit is None else max(0, limit - used),
            'paid_video_disabled': budget['paid_video_disabled'],
            'blocker_code': blocker if blocker in BLOCKERS else None,
            'submission_unknown_count': states.get('script_submission_unknown', 0) + states.get('submission_unknown', 0),
            'failed_count': states.get('failed', 0),
        },
    }, board='feishu_bitable' if enabled else 'self_hosted')
