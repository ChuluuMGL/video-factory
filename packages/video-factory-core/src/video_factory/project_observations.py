"""Read-only projections over latest task revisions, with bounded diagnostics."""
from .feishu_bridge import meta
from .h3_provider import REJECTION_CODES
from .script_jobs import key as script_key
from .worker import job_key

LATEST = ('t.project=? AND t.revision=(SELECT MAX(v.revision) FROM tasks v '
          'WHERE v.project=t.project AND v.id=t.id)')
BLOCKED = {
    'failed': 'TASK_FAILED',
    'submission_unknown': 'VIDEO_SUBMISSION_UNCERTAIN',
    'script_submission_unknown': 'SCRIPT_SUBMISSION_UNCERTAIN',
}
SAFE_SCRIPT_ERRORS = frozenset({
    'SCRIPT_SUBMISSION_UNCERTAIN_NO_RETRY', 'SCRIPT_RATE_LIMITED', 'SCRIPT_AUTH_REJECTED',
    'SCRIPT_BALANCE_REJECTED', 'SCRIPT_ACCESS_REJECTED', 'SCRIPT_REQUEST_REJECTED',
})


def stats(db, project):
    counts = dict(db.execute('SELECT t.state,COUNT(*) FROM tasks t WHERE ' + LATEST +
                             ' GROUP BY t.state ORDER BY t.state', (project,)).fetchall())
    successful = counts.get('accepted', 0)
    terminal = successful + counts.get('failed', 0) + counts.get('rejected', 0)
    return {'total': sum(counts.values()), 'state_counts': counts, 'successful': successful,
            'terminal': terminal, 'success_rate': successful / terminal if terminal else None}


def failures(db, project, after):
    rows = db.execute("SELECT t.id,t.revision,t.state FROM tasks t WHERE " + LATEST +
                      " AND t.id>? AND t.state IN ('failed','submission_unknown','script_submission_unknown')"
                      " ORDER BY t.id LIMIT 101", (project, after)).fetchall()
    items = []
    for task, revision, state in rows[:100]:
        video = meta(db, job_key(project, task, revision)) or {}
        script = meta(db, script_key(project, task, revision)) or {}
        video_code = video.get('failure_code')
        script_code = script.get('submission_error')
        code = BLOCKED[state]
        if isinstance(video_code, str) and video_code in REJECTION_CODES.values():
            code = video_code
        elif isinstance(script_code, str) and script_code in SAFE_SCRIPT_ERRORS:
            code = script_code
        items.append({'id': task, 'revision': revision, 'state': state, 'error_code': code})
    more = len(rows) > 100
    return {'items': items, 'has_more': more, 'next_after': items[-1]['id'] if more else None}
