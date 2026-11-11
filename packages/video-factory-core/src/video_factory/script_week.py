"""Bounded, opt-in script-only shadow grant. Preparing never activates it.

A deployment adapter must establish a read-only source and freeze each version.
No HTTP endpoint accepts caller-built plans; the local adapter owns source reads.
Human review and video execution receive no authority from this grant.
"""
import hashlib
import re
import secrets
import time
from .runtime_store import RuntimeFault, fingerprint, identifier
from .feishu_bridge import FeishuBridge, meta, save

PREFIX = 'script:week:'
DAY = 86400
DURATION = 7 * DAY
LIMITS = {'daily_samples': 10, 'weekly_samples': 70,
          'daily_requests': 20, 'weekly_requests': 140,
          'daily_micro': 3000000, 'weekly_micro': 21000000,
          'combined_micro': 30000000, 'duration_seconds': DURATION}


def _digest(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _facts(db, project):
    binding = FeishuBridge(None)._binding(db, project)
    setup = meta(db, 'setup:project:' + project)
    settings = meta(db, 'script:profile:' + project)
    config = db.execute('SELECT digest FROM projects WHERE id=?', (project,)).fetchone()
    deployment = db.execute("SELECT value FROM meta WHERE key='deployment'").fetchone()
    native = meta(db, 'native:config:' + project)
    if (not setup or not settings or not config or not deployment
            or (native and native.get('enabled'))):
        raise RuntimeFault('WEEK_ISOLATED_SETUP_REQUIRED')
    if settings.get('max_validation_rewrites') != 1:
        raise RuntimeFault('WEEK_REWRITE_PROFILE_REQUIRED')
    secret = db.execute('SELECT revision FROM vault WHERE alias=?',
                        (settings['credential_ref'][7:],)).fetchone()
    if not secret or secret[0] != settings['credential_revision']:
        raise RuntimeFault('SCRIPT_CREDENTIAL_CHANGED')
    return {'binding': binding, 'setup_sha256': fingerprint(setup),
            'profile': settings, 'configuration_digest': config[0], 'deployment': deployment[0]}


def prepare(store, admin, project, *, source_base, source_table, copy_base, copy_table,
            claims_sha256, policy_sha256, source_readonly_evidence_sha256,
            history_exposure_micro, notice_sha256):
    """Bind audited immutable inputs; caller must first obtain claims approval."""
    identifier(project)
    if (not all(isinstance(v, str) and v for v in (source_base, source_table, copy_base, copy_table))
            or source_base == copy_base):
        raise RuntimeFault('WEEK_COPY_TARGET_REQUIRED')
    if not all(_digest(v) for v in (claims_sha256, policy_sha256, source_readonly_evidence_sha256, notice_sha256)):
        raise RuntimeFault('WEEK_EVIDENCE_REQUIRED')
    if type(history_exposure_micro) is not int or not 0 <= history_exposure_micro <= 9000000:
        raise RuntimeFault('WEEK_HISTORY_EXPOSURE_INVALID')
    with store.connect() as db:
        actor = store.authorize(db, admin, 'admin')
        facts = _facts(db, project)
        if (facts['binding']['base_token'], facts['binding']['table_id']) != (copy_base, copy_table):
            raise RuntimeFault('WEEK_TARGET_MISMATCH')
        from .script_trial import PREFIX as TRIAL
        trial = meta(db, TRIAL + project)
        if (meta(db, PREFIX + project) or trial or
                db.execute('SELECT 1 FROM tasks WHERE project=? LIMIT 1', (project,)).fetchone()):
            raise RuntimeFault('WEEK_FRESH_PROJECT_REQUIRED')
        contract = {'project': project, 'source_base': source_base, 'source_table': source_table,
                    'copy_base': copy_base, 'copy_table': copy_table,
                    'claims_sha256': claims_sha256, 'policy_sha256': policy_sha256,
                    'source_readonly_evidence_sha256': source_readonly_evidence_sha256,
                    'history_exposure_micro': history_exposure_micro, 'notice_sha256': notice_sha256,
                    'limits': dict(LIMITS), 'facts': facts}
        value = {'contract': contract, 'contract_sha256': fingerprint(contract),
                 'state': 'prepared', 't0': None, 't1': None, 'snapshots': {}}
        save(db, PREFIX + project, value)
        store.audit(db, actor, 'script_week_prepared_not_started', project)
    return {'state': 'prepared', 'contract_sha256': value['contract_sha256'], 'provider_requests': 0}


def activate(store, admin, project, *, expected_contract, t0, start_notice_sha256):
    """Separate explicit T0 approval. A past or changed start is never backdated."""
    now = time.time()
    if type(t0) not in (int, float) or not now <= t0 <= now + 300 or not _digest(start_notice_sha256):
        raise RuntimeFault('WEEK_START_NOTICE_REQUIRED')
    token = secrets.token_urlsafe(32)
    token_key = 'automation:key:' + hashlib.sha256(token.encode()).hexdigest()
    with store.connect() as db:
        actor = store.authorize(db, admin, 'admin')
        week = meta(db, PREFIX + project)
        if (not week or week['state'] != 'prepared' or week['contract_sha256'] != expected_contract
                or fingerprint(week['contract']) != expected_contract):
            raise RuntimeFault('WEEK_START_CONFLICT')
        contract = week['contract']
        if _facts(db, project) != contract['facts']:
            raise RuntimeFault('WEEK_CONFIGURATION_CHANGED')
        from .script_trial import PREFIX as TRIAL
        if meta(db, TRIAL + project): raise RuntimeFault('WEEK_FRESH_PROJECT_REQUIRED')
        week.update(state='active', t0=t0, t1=t0 + DURATION,
                    start_notice_sha256=start_notice_sha256, token_key=token_key)
        save(db, PREFIX + project, week)
        save(db, token_key, {'project': project, 'scope': 'bounded_script_week',
                            'contract_sha256': expected_contract, 'expires_at': week['t1']})
        save(db, TRIAL + project, {'production_base': contract['source_base'],
            'copy_base': contract['copy_base'], 'copy_table': contract['copy_table'],
            'enabled': True, 't0': t0, 't1': week['t1'], 'notice_sha256': start_notice_sha256,
            'week_contract_sha256': expected_contract, 'requests': {},
            'validation_failure_policy': {'continue_samples': True, 'consecutive_limit': 3,
                                          'notice_sha256': start_notice_sha256}})
        store.audit(db, actor, 'script_week_started', project)
    return {'token': token, 't0': t0, 't1': t0 + DURATION, 'scope': 'bounded_script_week'}


def active(db, project):
    from .script_trial import PREFIX as TRIAL
    week = meta(db, PREFIX + project)
    trial = meta(db, TRIAL + project)
    if (not week or week['state'] != 'active' or not trial or not trial['enabled']
            or not week['t0'] <= time.time() < week['t1']):
        raise RuntimeFault('WEEK_CLOSED')
    if (fingerprint(week['contract']) != week['contract_sha256']
            or trial.get('week_contract_sha256') != week['contract_sha256']
            or trial['t0'] != week['t0'] or trial['t1'] != week['t1']
            or week['t1'] - week['t0'] != DURATION
            or week['contract']['limits'] != LIMITS
            or _facts(db, project) != week['contract']['facts']):
        raise RuntimeFault('WEEK_CONFIGURATION_CHANGED')
    return week


def authorize(db, token, project):
    """Not used by general dispatch; this token can never dispatch video jobs."""
    if not isinstance(token, str) or not 20 <= len(token) <= 256:
        raise RuntimeFault('AUTH_WEEK_REQUIRED')
    token_key = 'automation:key:' + hashlib.sha256(token.encode()).hexdigest()
    saved = meta(db, token_key) or {}
    week = active(db, project)
    if (token_key != week.get('token_key') or saved.get('project') != project
            or saved.get('scope') != 'bounded_script_week' or saved.get('expires_at', 0) <= time.time()
            or saved.get('contract_sha256') != week['contract_sha256']):
        raise RuntimeFault('AUTH_WEEK_DENIED')
    return week


def authorize_step(db, token, project, task, revision):
    from .script_jobs import key
    job = meta(db, key(project, task, revision))
    week = meta(db, PREFIX + project)
    if not week:
        from .dispatch import authorize as normal_authorize
        return normal_authorize(db, token, project)
    value = authorize(db, token, project)
    if (not job or job['plan'].get('week_contract_sha256') != value['contract_sha256']
            or job['plan']['actor'] != 'shadow-service:' + project):
        raise RuntimeFault('AUTH_WEEK_PLAN_DENIED')
    snapshot = value['snapshots'].get(job['plan'].get('week_source_key'))
    if not snapshot or snapshot['plan_sha256'] != job['plan_sha256']:
        raise RuntimeFault('AUTH_WEEK_PLAN_DENIED')


def queue_snapshot(jobs, token, project, snapshot):
    """Internal trusted read adapter only, no user/OAuth impersonation.

    Source input excludes n8n outputs. Same source version with changed content is
    an error, not a new billable task. Snapshots and jobs commit atomically.
    """
    required = {'source_base', 'source_table', 'record_id', 'source_version',
                'eligible_at', 'sku_id', 'brief', 'input_summary', 'input_sha256'}
    if not isinstance(snapshot, dict) or set(snapshot) != required:
        raise RuntimeFault('WEEK_SNAPSHOT_INVALID')
    if (not all(isinstance(snapshot[k], str) and snapshot[k] for k in required - {'eligible_at'})
            or not 1 <= len(snapshot['brief']) <= 6000 or len(snapshot['input_summary']) > 12000
            or type(snapshot['eligible_at']) not in (int, float)
            or snapshot['input_sha256'] != fingerprint({k: v for k, v in snapshot.items() if k != 'input_sha256'})):
        raise RuntimeFault('WEEK_SNAPSHOT_INVALID')
    with jobs.store.connect() as db:
        week = authorize(db, token, project); contract = week['contract']
        if (snapshot['source_base'], snapshot['source_table']) != (contract['source_base'], contract['source_table']):
            raise RuntimeFault('WEEK_SOURCE_MISMATCH')
        if not week['t0'] <= snapshot['eligible_at'] <= min(time.time(), week['t1']):
            raise RuntimeFault('WEEK_NOT_A_NEW_TASK')
        source_key = fingerprint([snapshot[k] for k in ('source_base','source_table','record_id','source_version')])
        existing = week['snapshots'].get(source_key)
        if existing:
            if existing['snapshot'] != snapshot: raise RuntimeFault('WEEK_SOURCE_VERSION_CHANGED')
            return {'task': existing['task'], 'revision': 1, 'replayed': True, 'provider_requests': 0}
        from .script_trial import PREFIX as TRIAL
        trial = meta(db, TRIAL + project)
        # Admit only one unsubmitted sample. Do not silently carry a paid approval
        # into another day or fill a queue that bypasses that day's sample limit.
        submitted = {r['task'] for r in trial['requests'].values()}
        if any(v['task'] not in submitted for v in week['snapshots'].values()):
            raise RuntimeFault('WEEK_UNSUBMITTED_SAMPLE_PENDING')
        check_limits(week, trial, time.time(), rewrite_parent=None)
        setup = meta(db, 'setup:project:' + project)
        sku = next((v for v in setup['configuration']['project']['products'] if v['sku_id'] == snapshot['sku_id']), None)
        if sku is None: raise RuntimeFault('SCRIPT_SKU_NOT_IN_PROJECT')
        facts = contract['facts']; task = 'HD01_WEEK_' + source_key[:24]
        plan = {'project': project, 'task': task, 'sku': sku, 'brief': snapshot['brief'],
                'feedback': '', 'revision': 1, 'actor': 'shadow-service:' + project,
                'deployment': facts['deployment'], 'configuration_digest': facts['configuration_digest'],
                'binding_sha256': fingerprint(facts['binding']), 'profile': facts['profile'],
                'max_submissions': 2, 'native_config_sha256': fingerprint(None), 'native_source': None,
                'week_contract_sha256': week['contract_sha256'], 'week_source_key': source_key}
        prepared = {'plan': plan, 'plan_sha256': fingerprint(plan)}
        result = jobs._enqueue(db, prepared, expires_at=min(time.time() + 3600, week['t1']))
        week['snapshots'][source_key] = {'snapshot': snapshot, 'task': task, 'admitted_at': time.time(),
                                        'plan_sha256': prepared['plan_sha256']}
        save(db, PREFIX + project, week)
        return result


def check_limits(week, trial, now, *, rewrite_parent):
    from .script_trial import RESERVE, consecutive_validation_failures
    if not week['t0'] <= now < week['t1']: raise RuntimeFault('WEEK_CLOSED')
    if trial.get('batch') or trial.get('quarantined_requests'):
        raise RuntimeFault('WEEK_LEGACY_POLICY_FORBIDDEN')
    rows = trial['requests']
    if any(r['state'] != 'usage_accounted' for r in rows.values()):
        raise RuntimeFault('TRIAL_PENDING_OR_UNKNOWN')
    if consecutive_validation_failures(trial) >= 3:
        raise RuntimeFault('TRIAL_CONSECUTIVE_FAILURE_LIMIT')
    day = int((now - week['t0']) // DAY)
    daily = [r for r in rows.values() if int((r['submitted_at'] - week['t0']) // DAY) == day]
    if len(rows) >= LIMITS['weekly_requests']: raise RuntimeFault('WEEK_REQUEST_LIMIT')
    if len(daily) >= LIMITS['daily_requests']: raise RuntimeFault('WEEK_DAILY_REQUEST_LIMIT')
    if rewrite_parent is None:
        if sum('rewrite_parent' not in r for r in rows.values()) >= LIMITS['weekly_samples']:
            raise RuntimeFault('WEEK_SAMPLE_LIMIT')
        if sum('rewrite_parent' not in r for r in daily) >= LIMITS['daily_samples']:
            raise RuntimeFault('WEEK_DAILY_SAMPLE_LIMIT')
    exposure = sum(r['cost_upper_micro'] for r in rows.values())
    if exposure + RESERVE > LIMITS['weekly_micro']: raise RuntimeFault('WEEK_BUDGET_LIMIT')
    if exposure + RESERVE + week['contract']['history_exposure_micro'] > LIMITS['combined_micro']:
        raise RuntimeFault('WEEK_COMBINED_BUDGET_LIMIT')
    if sum(r['cost_upper_micro'] for r in daily) + RESERVE > LIMITS['daily_micro']:
        raise RuntimeFault('WEEK_DAILY_BUDGET_LIMIT')


def fence_reservation(db, plan, trial, now, rewrite_parent):
    week = active(db, plan['project'])
    if (plan.get('week_contract_sha256') != week['contract_sha256']
            or plan.get('week_source_key') not in week['snapshots']):
        raise RuntimeFault('AUTH_WEEK_PLAN_DENIED')
    check_limits(week, trial, now, rewrite_parent=rewrite_parent)


def close(store, project, *, reason='deadline'):
    """Idempotent local stop; no authority to start or extend a window."""
    if reason not in ('deadline', 'manual', 'error'): raise RuntimeFault('WEEK_CLOSE_REASON_INVALID')
    with store.connect() as db:
        week = meta(db, PREFIX + project)
        if not week: raise RuntimeFault('WEEK_NOT_PREPARED')
        if reason == 'deadline' and (week['t1'] is None or time.time() < week['t1']):
            raise RuntimeFault('WEEK_NOT_EXPIRED')
        if week['state'] == 'closed': return week['closed']
        from .script_trial import PREFIX as TRIAL
        trial = meta(db, TRIAL + project)
        if trial:
            trial['enabled'] = False; save(db, TRIAL + project, trial)
        grant = meta(db, week.get('token_key', ''))
        if grant:
            grant['expires_at'] = 0; save(db, week['token_key'], grant)
        receipt = {'reason': reason, 'closed_at': time.time(), 't0': week['t0'], 't1': week['t1'],
                   'requests': len((trial or {}).get('requests', {})), 'execution_enabled': False}
        week.update(state='closed', closed=receipt); save(db, PREFIX + project, week)
        store.audit(db, 'shadow-clock', 'script_week_closed', project)
        return receipt
