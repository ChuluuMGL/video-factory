"""Content-safe projections for the six-operation workbench protocol."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import re

from .feishu_bridge import meta
from .h3_provider import REJECTION_CODES
from .runtime_store import RuntimeFault, fingerprint, identifier
from .script_jobs import confirmed_failure as confirmed_script_failure, key as script_key
from .worker import job_key
from .workbench_details import record_url

STATUS = {
    'script_queued': ('script', 'queued'), 'submitted': ('video', 'running'),
    'awaiting_script_review': ('script', 'awaiting_review'),
    'awaiting_storyboard_review': ('storyboard', 'awaiting_review'),
    'awaiting_video_review': ('video', 'awaiting_review'),
    'ready': ('script', 'approved_waiting_next'), 'accepted': ('video', 'completed'),
    'rejected': ('script', 'rejected'), 'failed': ('video', 'failed'),
    'script_submission_unknown': ('script', 'submission_unknown'),
    'submission_unknown': ('video', 'submission_unknown'),
}
STAGES = ('script', 'storyboard', 'video')
WINDOWS = {'24h': 86400, '7d': 604800, '30d': 2592000, 'all': None}
LATEST = ('t.project=? AND t.revision=(SELECT MAX(v.revision) FROM tasks v '
          'WHERE v.project=t.project AND v.id=t.id)')
SHA = re.compile('[a-f0-9]{64}')
# This is a conservative display projection, never a credential/body log.
SENSITIVE = re.compile(
    r'https?://\S+|(?:ssh://|file://)\S+|(?:[A-Za-z]:[\\/]|(?<!\w)/)[\w./\\~-]+'
    r'|\b(?:sk-|cli_|ou_|AKIA|ghp_|gho_)[\w-]+\b'
    r'|\b(?:Bearer|token|password|app[_ -]?secret|api[_ -]?key|authorization)\s*[:= ]\s*\S+'
    r'|-----BEGIN [A-Z ]+PRIVATE KEY-----|\b[\w+-]+@[\w.-]+\b', re.I)
SCRIPT_ERRORS = ('SCRIPT_SUBMISSION_UNCERTAIN_NO_RETRY', 'SCRIPT_RATE_LIMITED', 'SCRIPT_AUTH_REJECTED',
                 'SCRIPT_BALANCE_REJECTED', 'SCRIPT_ACCESS_REJECTED', 'SCRIPT_REQUEST_REJECTED')


def text(value, limit=240):
    if not isinstance(value, str):
        return ''
    if 'PRIVATE KEY-----' in value or SENSITIVE.search(value):
        return '[已脱敏]'
    return value[:limit]


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace('+00:00', 'Z') if seconds else None


def brief(conclusion, next_action='', blocker='', details=''):
    return {'schema': 'task-brief-v1', 'conclusion': conclusion, 'nextAction': next_action,
            'blocker': blocker, 'details': details}


def envelope(skill, project, now, content, *, board='self_hosted'):
    return {'schema': 'op-workbench-v1', 'skill': skill, 'project_id': project,
            'engine': 'video_factory', 'board': board,
            'source': {'read_at': iso(now), 'complete': True, 'stale': False}, **content}


def get_stage(body, default='all'):
    stage = body.get('stage', default)
    if stage not in (*STAGES, 'all'):
        raise RuntimeFault('WORKBENCH_STAGE_INVALID')
    return stage


def page(body):
    cursor = body.get('cursor', '')
    if cursor != '':
        identifier(cursor)
    limit = body.get('limit', 20)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise RuntimeFault('WORKBENCH_LIMIT_INVALID')
    return cursor, limit


def version(row):
    return f"rev-{row['revision']}-{fingerprint([row['input_digest'], row['configuration_digest'], row['state'], row['artifact']])}"


def first_script_row(db, project, source):
    config = db.execute('SELECT digest FROM projects WHERE id=?', (project,)).fetchone()
    return {'id': source['task'], 'revision': 1, 'input_digest': fingerprint(source),
            'configuration_digest': config[0], 'state': 'script_plan_unsubmitted', 'artifact': None}


def stage_status(db, row):
    stage, status = STATUS.get(row['state'], ('script', 'unknown'))
    if row['state'] == 'failed':
        script = meta(db, script_key(row['project'], row['id'], row['revision']))
        video = meta(db, job_key(row['project'], row['id'], row['revision']))
        if script and not video:
            stage = 'script'
    elif row['state'] == 'rejected':
        last = db.execute('SELECT action FROM audit WHERE project=? AND task=? AND revision=? '
                          "AND action LIKE 'review_%_reject' ORDER BY seq DESC LIMIT 1",
                          (row['project'], row['id'], row['revision'])).fetchone()
        if last:
            candidate = last[0].split('_')[1]
            if candidate in STAGES:
                stage = candidate
    return stage, status


def rows(db, project, cursor=''):
    # Audit timestamps reflect ledger changes. A legacy row with no audit
    # evidence cannot be attributed to a window or an overdue queue.
    return db.execute('SELECT t.*, (SELECT MAX(a.at) FROM audit a WHERE a.project=t.project '
                      'AND a.task=t.id AND a.revision=t.revision) AS touched_at FROM tasks t WHERE ' +
                      LATEST + ' AND t.id>? ORDER BY t.id', (project, cursor))


def definite_video_failure(db, row):
    job = meta(db, job_key(row['project'], row['id'], row['revision'])) or {}
    return bool(job.get('failure_code') or db.execute(
        "SELECT 1 FROM audit WHERE project=? AND task=? AND revision=? AND action='provider_failed_no_retry'",
        (row['project'], row['id'], row['revision'])).fetchone())


def repairable_failure(db, row, stage):
    if row['state'] != 'failed':
        return False
    return bool(confirmed_script_failure(db, row)) if stage == 'script' else definite_video_failure(db, row)


def daily_triage(db, project, now, board):
    groups = [
        {'kind': 'submission_unknown', 'count': 0, 'summary': '先核对供应商回执，禁止自动重排。', 'items': []},
        {'kind': 'repairable_failures', 'count': 0, 'summary': '失败证据明确，可预览修复和排产。', 'items': []},
        {'kind': 'awaiting_reviews', 'count': 0, 'summary': '脚本、分镜和成片等待人工审核。', 'items': []},
    ]
    for row in rows(db, project):
        stage, status = stage_status(db, row)
        if status == 'submission_unknown':
            group = groups[0]
        elif status == 'failed' and repairable_failure(db, row, stage):
            group = groups[1]
        elif status == 'awaiting_review':
            group = groups[2]
        else:
            continue
        group['count'] += 1
        if len(group['items']) < 5:
            group['items'].append({'task_id': row['id'], 'task_key': project + ':' + row['id'],
                                   'stage': stage, 'version_token': version(row),
                                   'canonical_status': status, **record_url(db, project, row)})
    unknown, failures, reviews = (group['count'] for group in groups)
    return envelope('daily_triage', project, now, {
        'brief': brief(f'今日优先处理：{unknown} 条待核对回执、{failures} 条可修复失败、{reviews} 条待审核。',
                       '先核对不确定的供应商回执，再处理失败和待审。'),
        'triage': groups,
    }, board=board)


def projection(db, project, endpoint, body, now, *, media_preview=None):
    stage_filter = get_stage(body)
    native = meta(db, 'native:config:' + project) or {}
    board = 'feishu_bitable' if native.get('enabled') else 'self_hosted'
    if endpoint == 'daily-triage':
        return daily_triage(db, project, now, board)
    if endpoint == 'stats':
        window = body.get('window', '7d')
        if not isinstance(window, str) or window not in WINDOWS:
            raise RuntimeFault('WORKBENCH_WINDOW_INVALID')
        statuses = dict.fromkeys((status for _, status in STATUS.values()), 0)
        phases = {stage: {'total': 0, 'awaiting_review': 0, 'failed': 0} for stage in STAGES}
        missing = 0
        for row in rows(db, project):
            stage, status = stage_status(db, row)
            if stage_filter not in ('all', stage):
                continue
            if WINDOWS[window] is not None:
                if row['touched_at'] is None:
                    missing += 1
                    continue
                if row['touched_at'] < now - WINDOWS[window]:
                    continue
            statuses[status] = statuses.get(status, 0) + 1
            phases[stage]['total'] += 1
            if status in ('awaiting_review', 'failed'):
                phases[stage][status] += 1
        total = sum(statuses.values())
        done = statuses['completed']
        result = envelope('project_stats', project, now, {
            'brief': brief(f'共 {total} 条任务，已完成 {done} 条。',
                           '查看待审及异常任务。', '存在缺少时间证据的任务。' if missing else ''),
            'metrics': {'window': window, 'total': total, 'completed': done,
                        'success_rate': done / total if total else None, 'by_status': statuses, 'by_stage': phases},
        }, board=board)
        result['source'].update(total_tasks=total, complete=not missing)
        return result
    cursor, limit = page(body)
    kinds = body.get('kinds', ['failed', 'stuck', 'submission_unknown'])
    minutes = body.get('stuck_minutes', 30)
    if (not isinstance(kinds, list) or not 1 <= len(kinds) <= 3
            or any(kind not in ('failed', 'stuck', 'submission_unknown') for kind in kinds)
            or type(minutes) is not int or not 5 <= minutes <= 1440):
        raise RuntimeFault('WORKBENCH_FAILURE_FILTER_INVALID')
    items = []
    setup = meta(db, 'setup:project:' + project) or {}
    products = {product['sku_id']: product for product in
                setup.get('configuration', {}).get('project', {}).get('products', [])}
    for row in rows(db, project, cursor):
        stage, status = stage_status(db, row)
        common = {'task_key': project + ':' + row['id'], 'task_id': row['id'],
                  'revision': row['revision'], 'version_token': version(row), 'stage': stage,
                  'canonical_status': status, 'updated_at': iso(row['touched_at']), **record_url(db, project, row)}
        if endpoint == 'reviews':
            if status != 'awaiting_review' or stage_filter not in ('all', stage):
                continue
            payload = json.loads(row['input'])
            artifact = json.loads(row['artifact']) if row['artifact'] else {}
            job = meta(db, script_key(project, row['id'], row['revision'])) or {}
            plan_id = job.get('agent_plan_id')
            plan = meta(db, 'agent:script:plan:' + plan_id) if isinstance(plan_id, str) and SHA.fullmatch(plan_id) else None
            plan_sha = plan.get('plan_sha256') if plan and plan.get('project') == project else None
            if not isinstance(plan_sha, str) or not SHA.fullmatch(plan_sha):
                plan_sha = None
            artifact_sha = artifact.get('sha256', '')
            media = media_preview(row) if media_preview else {'media_preview_token': None}
            notes = notes_for(db, project, row['id'], row['revision'])
            common.update(sku_id=payload['sku_id'], sku_name=text(products.get(payload['sku_id'], {}).get('name')),
                          plan_id=plan_id if plan_sha else None, plan_sha256=plan_sha,
                          unresolved_notes_count=sum(status != 'resolved' for status in notes.values()),
                          note_statuses=[{'id': key, 'status': status} for key, status in notes.items()],
                          preview={'kind': 'text_excerpt' if stage == 'script' else ('media' if media['media_preview_token'] else 'media_unavailable'),
                                   'excerpt': text(payload['script']) if stage == 'script' else None,
                                   **media,
                                   'artifact_sha256': artifact_sha if isinstance(artifact_sha, str) and SHA.fullmatch(artifact_sha) else None,
                                   'decode_verified': True if artifact.get('verification') == 'full_decode_passed' else None,
                                   'content_is_instruction': False})
        else:
            kind = status if status in ('failed', 'submission_unknown') else (
                'stuck' if status in ('queued', 'running') and row['touched_at'] is not None
                and now - row['touched_at'] >= minutes * 60 else None)
            if kind not in kinds:
                continue
            code = {'failed': 'TASK_FAILED', 'submission_unknown': 'SUBMISSION_UNCERTAIN',
                    'stuck': 'TASK_STUCK'}[kind]
            script = meta(db, script_key(project, row['id'], row['revision'])) or {}
            video = meta(db, job_key(project, row['id'], row['revision'])) or {}
            confirmed = confirmed_script_failure(db, row) if kind == 'failed' and stage == 'script' else None
            if confirmed:
                code = confirmed['failure_code']
            elif script.get('submission_error') in SCRIPT_ERRORS:
                code = script['submission_error']
            elif video.get('failure_code') in REJECTION_CODES.values():
                code = video['failure_code']
            repairable = bool(confirmed) if stage == 'script' else kind == 'failed' and definite_video_failure(db, row)
            common.update(raw_status=row['state'], failure_kind=kind, error_code=code,
                          diagnosis='请核对供应商回执。' if kind == 'submission_unknown' else '请核对任务状态与已保存回执。',
                          reconciliation_required=kind == 'submission_unknown', automatic_resubmit=False,
                          repairable=repairable,
                          suggested_repair_type='task_retry_from_stage' if repairable else None)
        items.append(common)
        if len(items) > limit:
            break
    more = len(items) > limit
    items = items[:limit]
    key = 'list_reviews' if endpoint == 'reviews' else 'list_failures'
    return envelope(key, project, now, {
        'brief': brief(f'本页 {len(items)} 条' + ('待审。' if endpoint == 'reviews' else '异常任务。'),
                       '选择一条核对版本。'),
        'items': items, 'has_more': more, 'next_cursor': items[-1]['task_id'] if more else None,
    }, board=board)


def notes_for(db, project, task, revision):
    """Merge local annotations with the existing workbench feedback envelopes."""
    notes = dict(meta(db, f'workbench:notes:{project}:{task}:{revision}') or {})
    prior = db.execute("SELECT receipt FROM events WHERE "
                       "json_extract(receipt,'$.project')=? AND json_extract(receipt,'$.task')=? "
                       "AND json_extract(receipt,'$.revision')<=? "
                       "AND (receipt LIKE '%[VF_SCRIPT_NOTES_V1]%' OR receipt LIKE '%[VF_NOTE_STATUS_V1]%') "
                       "ORDER BY rowid DESC LIMIT 1", (project, task, revision)).fetchone()
    if not prior:
        return notes
    feedback = json.loads(prior[0]).get('feedback', '')
    try:
        if '[VF_NOTE_STATUS_V1]' in feedback:
            marker = '\n\n[VF_NOTE_STATUS_V1]\n'
            if not feedback.startswith(marker):
                raise ValueError
            saved = json.loads(feedback[len(marker):])
            if (saved['schema'] != 'vf-note-status-v1' or type(saved['revision']) is not int
                    or not 1 <= saved['revision'] <= revision or not 1 <= len(saved['items']) <= 8):
                raise ValueError
            for item in saved['items']:
                if not SHA.fullmatch(item['id']) or item['status'] not in ('pending', 'needs_changes', 'resolved'):
                    raise ValueError
                notes.setdefault(item['id'], item['status'] if saved['revision'] == revision else 'pending')
            feedback = saved['feedback']
        if '[VF_SCRIPT_NOTES_V1]' in feedback:
            _, encoded = feedback.rsplit('\n\n[VF_SCRIPT_NOTES_V1]\n', 1)
            bundle = json.loads(encoded)
            if (bundle['schema'] != 'vf-script-notes-v1' or not 1 <= len(bundle['notes']) <= 8
                    or not SHA.fullmatch(bundle['plan_id']) or not SHA.fullmatch(bundle['script_sha256'])
                    or type(bundle['revision']) is not int or not 1 <= bundle['revision'] <= revision):
                raise ValueError
            for index, note in enumerate(bundle['notes']):
                values = [bundle['plan_id'], bundle['revision'], bundle['script_sha256'],
                          note['line'], note['quote'], note['comment'], index]
                encoded = json.dumps(values, ensure_ascii=False, separators=(',', ':'))
                notes.setdefault(sha256(encoded.encode()).hexdigest(), 'pending')
    except (KeyError, ValueError, TypeError, AttributeError):
        notes['invalid_history'] = 'pending'
    return notes
