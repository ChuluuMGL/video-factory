"""Optional, durable copy-only trial limits inside the existing script authority.

This module grants no execution authority. ScriptJobs calls reserve only after its
normal identity, paid approval, revision and binding fences, in the same atomic
transaction as its irrevocable submission intent. Uncertain calls stay reserved.
Amounts are integer micro-CNY; usage accounting is a conservative upper bound,
not a claim that the provider invoice has been reconciled.
"""
import time
from .runtime_store import RuntimeFault, fingerprint
from .feishu_bridge import meta, save

PREFIX = 'script:trial:'
RESERVE = 200000
CAP = 30000000
LIMIT = 30
DURATION = 86400


def validation_policy(trial):
    policy=(trial or {}).get('validation_failure_policy')
    return (isinstance(policy,dict) and policy.get('continue_samples') is True
            and policy.get('consecutive_limit') == 3
            and isinstance(policy.get('notice_sha256'),str) and len(policy['notice_sha256'])==64)


def consecutive_validation_failures(trial):
    count=0
    for digest,row in sorted(trial['requests'].items(),key=lambda item:item[1]['submitted_at']):
        if digest in trial.get('quarantined_requests',{}):continue
        if row.get('outcome')=='validation_failed':count+=1
        elif row['state']=='usage_accounted':count=0
        # A queued/inflight request does not erase preceding failures.
    return count


def settle_validation_failure(store,db,project,task,revision):
    """Settle a known response once, retain diagnostics, never resend or approve."""
    from .script_jobs import key
    trial=meta(db,PREFIX+project)
    if not validation_policy(trial):return False
    job=meta(db,key(project,task,revision));row=store._current(db,project,task,revision)
    if job.get('state')=='validation_failed' and row['state']=='failed':return True
    if (job.get('submission_error')!='SCRIPT_VALIDATION_FAILED' or job['state']!='submission_unknown'
            or row['state']!='script_submission_unknown'):return False
    history=meta(db,'script:receipt:'+job['plan_sha256']) or []
    responses=[r for r in history if r.get('reason')=='RESPONSE_RECEIVED']
    if (len(responses)!=1 or not any(r.get('reason')=='STRUCTURE_INVALID' for r in history)):
        raise RuntimeFault('TRIAL_VALIDATION_RECEIPT_MISSING')
    response=responses[0]
    if (response.get('http_status')!=200 or response.get('truncated') is not False
            or response.get('finish_reason')!='stop' or not response.get('provider_id_sha256')):
        raise RuntimeFault('TRIAL_VALIDATION_RECEIPT_INVALID')
    request=trial['requests'].get(job['plan_sha256'])
    if not request or (request['task'],request['revision'])!=(task,revision):
        raise RuntimeFault('TRIAL_RECEIPT_CONFLICT')
    settle(db,project,job['plan_sha256'],{'provider_id':'sha256:'+response['provider_id_sha256'],'usage':response.get('usage')})
    trial=meta(db,PREFIX+project);request=trial['requests'][job['plan_sha256']]
    if request['state']!='usage_accounted':return False
    request['outcome']='validation_failed';save(db,PREFIX+project,trial)
    job['state']='validation_failed';job['failure_receipt_sha256']=fingerprint(history)
    save(db,key(project,task,revision),job)
    db.execute("UPDATE tasks SET state='failed' WHERE project=? AND id=? AND revision=?",(project,task,revision))
    store.audit(db,'trial-worker','script_validation_failed_settled',project,task,revision)
    return True


def configure(store, token, project, *, production_base, copy_base, copy_table):
    if (not all(isinstance(x,str) and x for x in (production_base,copy_base,copy_table))
            or production_base == copy_base):
        raise RuntimeFault('TRIAL_COPY_TARGET_REQUIRED')
    value = {'production_base': production_base, 'copy_base': copy_base,
             'copy_table': copy_table, 'enabled': False, 't0': None, 't1': None,
             'notice_sha256': None, 'requests': {}}
    with store.connect() as db:
        actor = store.authorize(db,token,'admin')
        if meta(db,PREFIX+project) is not None:
            raise RuntimeFault('TRIAL_ALREADY_CONFIGURED')
        # Binding must already have been installed through the regular flow.
        from .feishu_bridge import FeishuBridge
        binding = FeishuBridge(store)._binding(db,project)
        if (binding['base_token'],binding['table_id']) != (copy_base,copy_table):
            raise RuntimeFault('TRIAL_TARGET_MISMATCH')
        save(db,PREFIX+project,value)
        store.audit(db,actor,'script_trial_configured',project)
    return {'enabled': False, 'provider_requests': 0}


def activate(store,token,project,*,t0,notice_sha256):
    import re
    if (type(t0) not in (int,float) or not time.time() <= t0 <= time.time()+300
            or not isinstance(notice_sha256,str) or not re.fullmatch('[0-9a-f]{64}',notice_sha256)):
        raise RuntimeFault('TRIAL_START_NOTICE_REQUIRED')
    with store.connect() as db:
        actor=store.authorize(db,token,'admin')
        trial=meta(db,PREFIX+project)
        if not trial or trial['t0'] is not None:raise RuntimeFault('TRIAL_WINDOW_IMMUTABLE')
        trial.update(enabled=True,t0=t0,t1=t0+DURATION,notice_sha256=notice_sha256)
        save(db,PREFIX+project,trial);store.audit(db,actor,'script_trial_started',project)
    return {'t0':t0,'t1':t0+DURATION,'max_requests':LIMIT,'budget_cny':30}


def pause(store,token,project):
    with store.connect() as db:
        actor=store.authorize(db,token,'admin');trial=meta(db,PREFIX+project)
        if not trial:raise RuntimeFault('TRIAL_NOT_CONFIGURED')
        trial['enabled']=False;save(db,PREFIX+project,trial)
        store.audit(db,actor,'script_trial_paused',project)
    return {'enabled':False}


def resume(store,token,project,*,t0,notice_sha256,expected_sha256,quarantine,checkpoint):
    """Explicit administrator recovery: preserve uncertain intents and all exposure.

    A new window requires a new recorded user instruction. Quarantine excludes
    only named, already-uncertain tasks from dispatch; it is not a refund/retry.
    """
    import re
    if (type(t0) not in (int,float) or not time.time() <= t0 <= time.time()+300
            or not isinstance(notice_sha256,str) or not re.fullmatch('[0-9a-f]{64}',notice_sha256)
            or not isinstance(quarantine,list) or not quarantine or len(set(quarantine))!=len(quarantine)
            or not isinstance(checkpoint,str) or not checkpoint):
        raise RuntimeFault('TRIAL_RECOVERY_NOTICE_REQUIRED')
    with store.connect() as db:
        actor=store.authorize(db,token,'admin');trial=meta(db,PREFIX+project)
        if (not trial or trial['enabled'] or trial['t0'] is None
                or fingerprint(trial)!=expected_sha256 or notice_sha256==trial['notice_sha256']):
            raise RuntimeFault('TRIAL_RECOVERY_CONFLICT')
        from .script_jobs import key
        excluded=dict(trial.get('quarantined_requests',{}))
        for request in quarantine:
            row=trial['requests'].get(request)
            if not row or row['state']!='reserved':raise RuntimeFault('TRIAL_QUARANTINE_DENIED')
            current=store._current(db,project,row['task'],row['revision'])
            job=meta(db,key(project,row['task'],row['revision']))
            if (current['state']!='script_submission_unknown' or not job or job['state']!='submission_unknown'
                    or job['plan_sha256']!=request or row['task']==checkpoint):
                raise RuntimeFault('TRIAL_QUARANTINE_DENIED')
            excluded[request]={'task':row['task'],'revision':row['revision'],'notice_sha256':notice_sha256}
        history=trial.setdefault('windows',[])
        history.append({k:trial[k] for k in ('t0','t1','notice_sha256')})
        trial.update(enabled=True,t0=t0,t1=t0+DURATION,notice_sha256=notice_sha256,
                     quarantined_requests=excluded,checkpoint_task=checkpoint)
        save(db,PREFIX+project,trial);store.audit(db,actor,'script_trial_recovered',project)
    return {'t0':t0,'t1':t0+DURATION,'requests_used':len(trial['requests']),
            'remaining_requests':LIMIT-len(trial['requests']),'reserved_micro':sum(r['cost_upper_micro'] for r in trial['requests'].values())}


def reserve(db,plan,binding,request_id):
    trial=meta(db,PREFIX+plan['project'])
    if trial is None:return
    now=time.time()
    from .script_trial_batch import batch_policy, request_limit
    batch=batch_policy(trial)
    if (not trial['enabled'] or trial['t0'] is None or now<trial['t0']
            or (not batch and (trial['t1'] is None or now>=trial['t1']))):
        raise RuntimeFault('TRIAL_WINDOW_CLOSED')
    if batch and plan['task'] not in trial['batch']['tasks']:
        raise RuntimeFault('TRIAL_BATCH_TASK_NOT_APPROVED')
    if (binding['base_token']==trial['production_base'] or
            (binding['base_token'],binding['table_id'])!=(trial['copy_base'],trial['copy_table'])):
        raise RuntimeFault('TRIAL_TARGET_MISMATCH')
    # Do not allow a new task name or a rotated credential to reset consumption.
    source=plan.get('native_source') or {}
    digest=fingerprint([plan['sku'],plan['brief'],plan['feedback'],source.get('source_revision')])
    rows=trial['requests']
    if validation_policy(trial) and consecutive_validation_failures(trial)>=3:
        raise RuntimeFault('TRIAL_CONSECUTIVE_FAILURE_LIMIT')
    if request_id in rows or any(r['input_digest']==digest for r in rows.values()):
        raise RuntimeFault('TRIAL_DUPLICATE_INPUT')
    if any(r['state']!='usage_accounted' and rid not in trial.get('quarantined_requests',{}) for rid,r in rows.items()):
        raise RuntimeFault('TRIAL_PENDING_OR_UNKNOWN')
    if len(rows)>=request_limit(trial):raise RuntimeFault('TRIAL_REQUEST_LIMIT')
    exposure=sum(r.get('cost_upper_micro',RESERVE) for r in rows.values())
    if exposure+RESERVE>CAP:raise RuntimeFault('TRIAL_BUDGET_LIMIT')
    rows[request_id]={'state':'reserved','input_digest':digest,'submitted_at':now,
                      'task':plan['task'],'revision':plan['revision'],'cost_upper_micro':RESERVE}
    save(db,PREFIX+plan['project'],trial)


def settle(db,project,request_id,result):
    trial=meta(db,PREFIX+project)
    if trial is None:return
    row=trial['requests'].get(request_id)
    if not row or row['state']!='reserved':raise RuntimeFault('TRIAL_RECEIPT_CONFLICT')
    row['provider_id']=result['provider_id']
    usage=result.get('usage')
    valid=(isinstance(usage,dict) and all(type(usage.get(k)) is int and usage[k]>=0
           for k in ('prompt_tokens','completion_tokens','total_tokens'))
           and usage['prompt_tokens']+usage['completion_tokens']==usage['total_tokens']
           and 0 < usage['prompt_tokens']<=32768 and 0 < usage['completion_tokens']<=2048)
    if valid:
        row.update(state='usage_accounted',usage=usage,
                   cost_upper_micro=usage['prompt_tokens']*2+usage['completion_tokens']*8)
    else:
        row['state']='usage_unknown'
        trial['enabled']=False
    save(db,PREFIX+project,trial)
