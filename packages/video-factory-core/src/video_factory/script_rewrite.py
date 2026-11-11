"""One opt-in validation rewrite, with a separate durable paid request.

No network/unknown retry. The original paid plan, TTL and all job execution
fences remain in force. Never enable this policy on an existing approved plan.
"""
from .runtime_store import RuntimeFault, fingerprint
from .feishu_bridge import meta, save


def check_attempt(db,job):
    context=job.get('rewrite_context')
    if context is None:
        if job.get('request_id',job['plan_sha256'])!=job['plan_sha256']:
            raise RuntimeFault('SCRIPT_REWRITE_CONTEXT_CHANGED')
        return None
    parent=job['plan_sha256']
    if (job['plan'].get('max_submissions')!=2 or job.get('rewrite_count')!=1
            or context.get('parent_request_id')!=parent
            or job.get('request_id')!=fingerprint([parent,'rewrite',context])
            or fingerprint(meta(db,'script:receipt:'+parent))!=job.get('rewrite_receipt_sha256')):
        raise RuntimeFault('SCRIPT_REWRITE_CONTEXT_CHANGED')
    return parent


def check_reservation(db,plan,request_id,parent,trial):
    from .script_jobs import key
    from .script_trial import validation_policy
    job=meta(db,key(plan['project'],plan['task'],plan['revision']))
    row=trial['requests'].get(parent)
    if (not validation_policy(trial) or not job or job['plan']!=plan
            or check_attempt(db,job)!=parent or job['request_id']!=request_id
            or not row or row['state']!='usage_accounted' or row.get('outcome')!='rewrite_pending'
            or (row['task'],row['revision'])!=(plan['task'],plan['revision'])
            or any(r.get('rewrite_parent')==parent for r in trial['requests'].values())):
        raise RuntimeFault('SCRIPT_REWRITE_NOT_AUTHORIZED')


def queue_rewrite(store,db,project,task,revision,provider):
    from .script_jobs import key
    from .script_trial import PREFIX, validation_policy
    job=meta(db,key(project,task,revision));plan=job['plan']
    trial=meta(db,PREFIX+project)
    if (plan.get('max_submissions')!=2 or plan['profile'].get('max_validation_rewrites')!=1
            or job.get('rewrite_count',0)!=0 or job['state']!='validation_failed'
            or not validation_policy(trial) or not callable(getattr(provider,'validate_rewrite',None))
            or not callable(getattr(provider,'rewrite',None))):return False
    parent=job['plan_sha256'];history=meta(db,'script:receipt:'+parent) or []
    details=[r for r in history if r.get('validation') and r.get('model_content_redacted')
             and r.get('content_capture_truncated') is False]
    if len(details)!=1:return False
    context={k:details[0][k] for k in ('validation','model_content_redacted')}
    context['parent_request_id']=parent
    # Fail closed before queuing, preserving the original failure if input is too large.
    try:provider.validate_rewrite(plan['sku'],plan['brief'],plan['feedback'],context)
    except RuntimeFault as error:
        if str(error) not in ('SCRIPT_REWRITE_INPUT_LIMIT','SCRIPT_REWRITE_CONTEXT_INVALID'):raise
        job['rewrite_blocked']=str(error);save(db,key(project,task,revision),job);return False
    job.setdefault('attempt_failures',[]).append({'request_id':parent,'error':job.pop('submission_error'),'receipt_sha256':fingerprint(history)})
    job.update(state='queued',rewrite_count=1,rewrite_context=context,
               rewrite_receipt_sha256=fingerprint(history),request_id=fingerprint([parent,'rewrite',context]))
    # Retain failed-attempt evidence. Only a completed sample contributes to the streak.
    trial['requests'][parent]['outcome']='rewrite_pending'
    trial['requests'][parent]['validation_failed']=True
    save(db,PREFIX+project,trial);save(db,key(project,task,revision),job)
    db.execute("UPDATE tasks SET state='script_queued' WHERE project=? AND id=? AND revision=?",(project,task,revision))
    store.audit(db,'script-worker','script_validation_rewrite_queued',project,task,revision)
    return True
