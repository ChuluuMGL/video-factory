"""Explicitly approved finite follow-up batch; preserves cumulative trial spend."""
import re
import time
from .runtime_store import RuntimeFault, fingerprint, identifier
from .feishu_bridge import meta, save


def batch_policy(trial):
    b=(trial or {}).get('batch')
    return (isinstance(b,dict) and b.get('mode')=='finite'
            and type(b.get('max_requests')) is int and 1<=b['max_requests']<=45
            and isinstance(b.get('tasks'),list) and 1<=len(b['tasks'])<=45
            and all(isinstance(t,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,96}',t) for t in b['tasks'])
            and len(set(b['tasks']))==len(b['tasks'])
            and isinstance(b.get('notice_sha256'),str) and re.fullmatch('[0-9a-f]{64}',b['notice_sha256']) is not None)


def request_limit(trial):
    if (trial or {}).get('batch') is not None:
        if not batch_policy(trial):raise RuntimeFault('TRIAL_BATCH_POLICY_INVALID')
        return trial['batch']['max_requests']
    return 30


def activate_batch(store,token,project,*,tasks,max_requests,notice_sha256,expected_sha256):
    from .script_trial import PREFIX, CAP, RESERVE
    batch={'mode':'finite','tasks':tasks,'max_requests':max_requests,'notice_sha256':notice_sha256}
    if not batch_policy({'batch':batch}):raise RuntimeFault('TRIAL_BATCH_POLICY_INVALID')
    for task in tasks:identifier(task)
    with store.connect() as db:
        actor=store.authorize(db,token,'admin');trial=meta(db,PREFIX+project)
        if not trial or trial['enabled'] or fingerprint(trial)!=expected_sha256:
            raise RuntimeFault('TRIAL_BATCH_CONFLICT')
        if notice_sha256==trial.get('notice_sha256'):raise RuntimeFault('TRIAL_BATCH_NOTICE_REUSED')
        if any(db.execute('SELECT 1 FROM tasks WHERE project=? AND id=?',(project,t)).fetchone() for t in tasks):
            raise RuntimeFault('TRIAL_BATCH_TASK_EXISTS')
        rows=trial['requests'];excluded=trial.get('quarantined_requests',{})
        if any(q['state']!='usage_accounted' and rid not in excluded for rid,q in rows.items()):
            raise RuntimeFault('TRIAL_PENDING_OR_UNKNOWN')
        if len(rows)+len(tasks)>max_requests:raise RuntimeFault('TRIAL_REQUEST_LIMIT')
        if sum(q['cost_upper_micro'] for q in rows.values())+len(tasks)*RESERVE>CAP:
            raise RuntimeFault('TRIAL_BUDGET_LIMIT')
        binding=meta(db,'feishu:binding:'+project)
        if (not binding or binding['base_token']==trial['production_base']
                or (binding['base_token'],binding['table_id'])!=(trial['copy_base'],trial['copy_table'])):
            raise RuntimeFault('TRIAL_TARGET_MISMATCH')
        trial.setdefault('windows',[]).append({k:trial.get(k) for k in ('t0','t1','notice_sha256','batch')})
        trial.update(enabled=True,t0=time.time(),t1=None,notice_sha256=notice_sha256,batch=batch)
        save(db,PREFIX+project,trial);store.audit(db,actor,'script_trial_batch_started',project)
    return {'batch_tasks':len(tasks),'requests_already_used':len(rows),'max_requests_total':max_requests,
            'budget_cny_total':30,'stop_rule':'finite_batch_or_error','provider_requests':0}
