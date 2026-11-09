"""Read-only continuation decisions; never authorize, retry, or settle a request.

A durable submission intent is deliberately uncertain before provider I/O. Wait
for its completion/error evidence instead of treating the intent as a failure.
An abandoned intent remains uncertain and stops continuation after a bounded wait.
Callers must read trial, tasks and jobs in one database transaction, then use the
normal freshly authenticated prepare/submit path. Reservation fences still own
count, budget, deduplication and approval enforcement.
"""
from .runtime_store import RuntimeFault
from .script_trial import CAP, LIMIT, RESERVE

COMPLETION_WAIT_SECONDS = 600


def continuation(trial, rows, jobs, planned_tasks, *, now):
    if not trial['enabled'] or trial['t1'] is None or now >= trial['t1']:
        return {'state': 'closed'}
    if trial['t0'] is None or now < trial['t0']:
        return {'state': 'waiting_window'}
    if len(planned_tasks) != len(set(planned_tasks)) or len(planned_tasks) > LIMIT:
        raise RuntimeFault('TRIAL_DISPATCH_PLAN_INVALID')
    tasks = {r['id']: r for r in rows}
    if len(tasks) != len(rows) or not set(tasks) <= set(planned_tasks):
        raise RuntimeFault('TRIAL_DISPATCH_TASK_CONFLICT')
    requests = trial['requests']
    excluded = trial.get('quarantined_requests', {})
    for digest, q in excluded.items():
        request = requests.get(digest)
        if not request or any(request[k] != q[k] for k in ('task', 'revision')):
            raise RuntimeFault('TRIAL_DISPATCH_QUARANTINE_CONFLICT')
    checkpoint = tasks.get(trial.get('checkpoint_task'))
    waiting = None
    for task, row in tasks.items():
        job = jobs.get(task)
        if not job or job['plan']['revision'] != row['revision']:
            raise RuntimeFault('TRIAL_DISPATCH_JOB_CONFLICT')
        digest = job['plan_sha256']
        if digest in excluded:
            q = excluded[digest]
            if (q['task'], q['revision']) != (task, row['revision']):
                raise RuntimeFault('TRIAL_DISPATCH_QUARANTINE_CONFLICT')
            continue
        if job.get('submission_error') or row['state'] in ('failed', 'rejected'):
            raise RuntimeFault('TRIAL_DISPATCH_TERMINAL_FAILURE')
        if row['state'] == 'script_submission_unknown':
            request = requests.get(digest)
            if (not request or (request['task'], request['revision']) != (task, row['revision'])
                    or job['state'] not in ('submission_unknown', 'received')):
                raise RuntimeFault('TRIAL_DISPATCH_INTENT_CONFLICT')
            age = now - request['submitted_at']
            if age < 0 or age >= COMPLETION_WAIT_SECONDS:
                raise RuntimeFault('TRIAL_DISPATCH_UNCERTAIN_REQUIRES_INSPECTION')
            waiting = {'state': 'waiting_completion', 'task': task}
        elif row['state'] == 'script_queued':
            if job['state'] != 'queued' or job['expires_at'] <= now:
                raise RuntimeFault('TRIAL_DISPATCH_QUEUE_EXPIRED_OR_CHANGED')
            waiting = {'state': 'waiting_worker', 'task': task}
        elif row['state'] not in ('ready', 'awaiting_script_review') or job['state'] != 'received':
            raise RuntimeFault('TRIAL_DISPATCH_STATE_CONFLICT')
    # Check all failures before returning a wait. A different in-flight task must
    # not hide a terminal failure, missing usage, or an orphan request.
    pending = False
    for digest, request in requests.items():
        task = request['task']
        if task not in tasks or jobs[task]['plan_sha256'] != digest:
            raise RuntimeFault('TRIAL_DISPATCH_ORPHAN_REQUEST')
        if digest in excluded:
            continue
        if request['state'] != 'usage_accounted':
            if request['state'] != 'reserved' or tasks[task]['state'] != 'script_submission_unknown':
                raise RuntimeFault('TRIAL_DISPATCH_USAGE_REQUIRES_INSPECTION')
            pending = True
    if not checkpoint or checkpoint['state'] != 'ready':
        return {'state': 'waiting_first_real_review'}
    if waiting:
        return waiting
    if pending:
        raise RuntimeFault('TRIAL_DISPATCH_USAGE_REQUIRES_INSPECTION')
    remaining = [task for task in planned_tasks if task not in tasks]
    if not remaining:
        return {'state': 'complete', 'requests': len(requests)}
    if len(requests) >= LIMIT:
        raise RuntimeFault('TRIAL_REQUEST_LIMIT')
    if sum(r.get('cost_upper_micro', RESERVE) for r in requests.values()) + RESERVE > CAP:
        raise RuntimeFault('TRIAL_BUDGET_LIMIT')
    return {'state': 'prepare_next', 'task': remaining[0]}
