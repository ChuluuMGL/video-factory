"""Agent script proposals with delegated Feishu identity and separate cost approval.

User tokens are provided by the host, never by Agent requests or persisted plans.
Feishu reads happen outside transactions; enqueue shares ScriptJobs' ledger.
"""
import json
import re
import time

from .agent_plans import authorize
from .feishu_bridge import meta, save
from .feishu_client import resource
from .runtime_store import RuntimeFault, fingerprint, identifier
from .script_jobs import ScriptJobs, key as job_key


class ScriptPlans:
    def __init__(self, store, project, *, user_token_provider=None, open_id=None, jobs=None):
        self.store, self.project = store, identifier(project)
        self.user_token_provider, self.open_id = user_token_provider, open_id
        if user_token_provider is not None:
            resource(open_id, 'ou_')
        self.jobs = jobs or ScriptJobs(store)

    def _load(self, db, plan_id):
        if not isinstance(plan_id, str) or not re.fullmatch('[0-9a-f]{64}', plan_id):
            raise RuntimeFault('AGENT_PLAN_ID_REQUIRED')
        saved = meta(db, 'agent:script:plan:' + plan_id)
        if not saved or saved['project'] != self.project:
            raise RuntimeFault('AGENT_PLAN_MISSING')
        return saved

    @staticmethod
    def _active(saved):
        if saved.get('revoked') or saved['expires_at'] <= time.time():
            raise RuntimeFault('AGENT_PLAN_EXPIRED_OR_REVOKED')

    @staticmethod
    def _request(request):
        if not isinstance(request, dict):
            raise RuntimeFault('AGENT_FIELDS_INVALID')
        if set(request) == {'record', 'expected_revision'}:
            resource(request['record'], 'rec')
        elif set(request) == {'task', 'sku_id', 'brief', 'expected_revision'}:
            identifier(request['task']); identifier(request['sku_id'])
            if not isinstance(request['brief'], str) or not 1 <= len(request['brief']) <= 6000:
                raise RuntimeFault('SCRIPT_BRIEF_REQUIRED')
        else:
            raise RuntimeFault('AGENT_FIELDS_INVALID')
        if type(request['expected_revision']) is not int or request['expected_revision'] < 0:
            raise RuntimeFault('REVISION_INVALID')

    def _fresh(self, request, saved=None):
        if self.user_token_provider is None:
            raise RuntimeFault('AGENT_SCRIPT_IDENTITY_NOT_CONFIGURED')
        user = self.user_token_provider()
        _, identity, _, actor, _, _ = self.jobs.bridge._session(user, self.project, 'submitters')
        if identity['open_id'] != self.open_id:
            raise RuntimeFault('AGENT_SCRIPT_IDENTITY_CHANGED')
        if 'record' in request:
            prepared = self.jobs.prepare_record(user, self.project, **request)
        else:
            with self.store.connect() as db:
                native = meta(db, 'native:config:' + self.project)
                if native and native.get('enabled'):
                    raise RuntimeFault('AGENT_SCRIPT_USE_NATIVE_RECORD')
            prepared = self.jobs.prepare(user, self.project, **request)
            if prepared['plan']['native_source'] is not None:
                raise RuntimeFault('AGENT_SCRIPT_USE_NATIVE_RECORD')
        if prepared['plan']['actor'] != actor:
            raise RuntimeFault('AGENT_SCRIPT_IDENTITY_CHANGED')
        if saved and prepared['plan_sha256'] != saved['source_plan_sha256']:
            raise RuntimeFault('AGENT_SCRIPT_PLAN_CHANGED')
        return prepared

    def _view(self, db, saved):
        plan = saved['source_plan']
        job = meta(db, job_key(self.project, plan['task'], plan['revision']))
        enqueued = bool(saved.get('submitted') and job and job.get('agent_plan_id') == saved['plan_id'])
        row = db.execute('SELECT state,input_digest FROM tasks WHERE project=? AND id=? AND revision=?',
                         (self.project, plan['task'], plan['revision'])).fetchone()
        latest = db.execute('SELECT MAX(revision) FROM tasks WHERE project=? AND id=?',
                            (self.project, plan['task'])).fetchone()[0]
        phase = ('revoked' if saved.get('revoked') else 'enqueued' if enqueued else
                 'execution_conflict' if saved.get('submitted') else 'expired' if saved['expires_at'] <= time.time()
                 else 'approved' if saved.get('approved_by') else 'prepared')
        result = job.get('result') if enqueued else None
        return {'schema': 'vf-agent-script-plan-v1', 'project': self.project, 'task': plan['task'],
                'revision': plan['revision'], 'sku_id': plan['sku']['sku_id'], 'plan_id': saved['plan_id'],
                'plan_sha256': saved['plan_sha256'], 'request_id': saved['request_id'], 'phase': phase,
                'expires_at': saved['expires_at'], 'model': plan['profile']['model'], 'max_submissions': 1,
                'source_kind': 'native_record' if plan['native_source'] else 'sku_brief',
                'brief_sha256': fingerprint(plan['brief']), 'feedback_sha256': fingerprint(plan['feedback']),
                'configuration_digest': plan['configuration_digest'],
                'submission_identity': 'configured_feishu_submitter', 'approval_authority': 'local_administrator',
                'approval_required': not saved.get('approved_by') or phase in ('expired', 'revoked'),
                'cost': {'quote_available': False, 'amount': None, 'currency': None,
                         'approval_basis': 'one_submission_at_configured_provider_rate'},
                'current_revision': latest, 'is_current_revision': latest == plan['revision'],
                'task_state': row['state'] if row else 'not_created',
                'provider_receipt_recorded': bool(enqueued and job.get('provider_id')),
                'draft_sha256': fingerprint(result['script']) if result else None,
                'draft_attached': bool(result and row and row['input_digest'] == fingerprint({
                    'sku_id': plan['sku']['sku_id'], 'script': result['script'],
                    'source_revision': plan['native_source']['source_revision'] if plan['native_source'] else saved['source_plan_sha256']})),
                'reconciliation_required': bool(enqueued and job['state'] == 'submission_unknown'),
                'automatic_resubmit': False, 'human_acceptance_policy': 'separate_script_review_required',
                'video_spending_approved': False}

    def prepare(self, token, request_id, request):
        identifier(request_id); self._request(request)
        plan_id = fingerprint(['script', self.project, request_id])
        key = 'agent:script:plan:' + plan_id
        with self.store.connect() as db:
            authorize(db, token, self.project, write=True)
            old = meta(db, key)
            if old:
                if old['request'] != request: raise RuntimeFault('AGENT_REQUEST_ID_CONFLICT')
                return {**self._view(db, old), 'replayed': True}
        prepared = self._fresh(request)
        with self.store.connect() as db:
            _, actor = authorize(db, token, self.project, write=True)
            old = meta(db, key)
            if old:
                if old['request'] != request: raise RuntimeFault('AGENT_REQUEST_ID_CONFLICT')
                return {**self._view(db, old), 'replayed': True}
            self.jobs._fence_prepared(db, prepared['plan'])
            saved = {'project': self.project, 'request_id': request_id, 'request': request,
                     'plan_id': plan_id, 'created_by': actor, 'source_plan': prepared['plan'],
                     'source_plan_sha256': prepared['plan_sha256'], 'expires_at': time.time() + 3600}
            saved['plan_sha256'] = fingerprint(saved)
            save(db, key, saved)
            self.store.audit(db, actor, 'agent_script_plan_prepared', self.project,
                             prepared['plan']['task'], prepared['plan']['revision'])
            return {**self._view(db, saved), 'replayed': False, 'provider_requests': 0}

    def owner(self, token, action, plan_id, expected_plan=None):
        if action not in ('inspect', 'approve', 'revoke'): raise RuntimeFault('AGENT_OWNER_ACTION_INVALID')
        with self.store.connect() as db:
            self.store.authorize(db, token, 'admin')
            saved = self._load(db, plan_id)
            if action != 'inspect' and saved['plan_sha256'] != expected_plan:
                raise RuntimeFault('AGENT_PLAN_DIGEST_REQUIRED')
            if action == 'inspect':
                plan = saved['source_plan']
                return {**self._view(db, saved), 'owner_review': {
                    'sku': plan['sku'], 'brief': plan['brief'], 'feedback': plan['feedback'],
                    'submitter': plan['actor'], 'native_source': plan['native_source'],
                    'billing_owner': plan['profile']['billing_owner'], 'feishu_cost_owner_verified': False}}
            if action == 'approve':
                self._active(saved)
                if saved.get('submitted'): raise RuntimeFault('AGENT_PLAN_ALREADY_ENQUEUED')
        if action == 'approve': self._fresh(saved['request'], saved)
        with self.store.connect() as db:
            actor = self.store.authorize(db, token, 'admin')
            saved = self._load(db, plan_id); plan = saved['source_plan']
            key = job_key(self.project, plan['task'], plan['revision'])
            job = meta(db, key)
            if action == 'approve':
                self._active(saved)
                if saved.get('submitted') or job: raise RuntimeFault('AGENT_TASK_ALREADY_HAS_EXECUTION')
                self.jobs._fence_prepared(db, plan)
                saved.setdefault('approved_by', actor)
            else:
                if saved.get('submitted'):
                    row = db.execute('SELECT state FROM tasks WHERE project=? AND id=? AND revision=?',
                                     (self.project, plan['task'], plan['revision'])).fetchone()
                    if (not row or not job or job.get('agent_plan_id') != plan_id or
                            job['state'] not in ('queued', 'cancelled') or job.get('provider_id') or
                            row['state'] not in ('script_queued', 'failed')):
                        raise RuntimeFault('AGENT_EXECUTION_ALREADY_STARTED_OR_CHANGED')
                    job.update(expires_at=0, state='cancelled', approval_revoked='agent_owner_revoked')
                    save(db, key, job)
                    db.execute("UPDATE tasks SET state='failed' WHERE project=? AND id=? AND revision=?",
                               (self.project, plan['task'], plan['revision']))
                saved['revoked'] = True
            save(db, 'agent:script:plan:' + plan_id, saved)
            self.store.audit(db, actor, 'agent_script_plan_' + action, self.project, plan['task'], plan['revision'])
            return self._view(db, saved)

    def submit(self, token, plan_id, expected_plan):
        with self.store.connect() as db:
            authorize(db, token, self.project, write=True)
            saved = self._load(db, plan_id)
            if expected_plan != saved['plan_sha256']: raise RuntimeFault('AGENT_PLAN_DIGEST_REQUIRED')
            if saved.get('submitted'): return {**self._view(db, saved), 'replayed': True, 'provider_requests': 0}
            self._active(saved)
            if not saved.get('approved_by'): raise RuntimeFault('AGENT_OWNER_APPROVAL_REQUIRED')
        try:
            prepared = self._fresh(saved['request'], saved)
        except RuntimeFault:
            # Another request may have committed this exact plan during the
            # external identity/source reads. Return only its durable receipt.
            with self.store.connect() as db:
                authorize(db, token, self.project, write=True)
                latest = self._load(db, plan_id)
                if latest.get('submitted') and latest['plan_sha256'] == expected_plan:
                    return {**self._view(db, latest), 'replayed': True, 'provider_requests': 0}
            raise
        with self.store.connect() as db:
            _, actor = authorize(db, token, self.project, write=True)
            saved = self._load(db, plan_id)
            if saved.get('submitted'): return {**self._view(db, saved), 'replayed': True, 'provider_requests': 0}
            self._active(saved)
            if not saved.get('approved_by'): raise RuntimeFault('AGENT_OWNER_APPROVAL_REQUIRED')
            self.jobs._enqueue(db, prepared, expires_at=saved['expires_at'], agent_plan_id=plan_id)
            saved['submitted'] = True
            save(db, 'agent:script:plan:' + plan_id, saved)
            plan = saved['source_plan']
            self.store.audit(db, actor, 'agent_script_plan_enqueued', self.project, plan['task'], plan['revision'])
            return {**self._view(db, saved), 'replayed': False, 'provider_requests': 0}

    def receipt(self, token, plan_id):
        with self.store.connect() as db:
            authorize(db, token, self.project)
            return self._view(db, self._load(db, plan_id))
