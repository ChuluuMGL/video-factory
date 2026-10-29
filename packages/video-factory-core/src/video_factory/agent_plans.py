"""Local-admin-approved video proposals on the existing execution ledger.

An Agent can prepare and enqueue, but cannot approve. Preparation, approval and
enqueue each fence the server-owned profile, current task and credential version.
No provider I/O occurs here. The scheduler alone consumes the resulting Worker job.
"""
from contextlib import contextmanager
import hashlib
import json
import re
import time

from .feishu_bridge import meta, save
from .runtime_store import RuntimeFault, RuntimeStore, fingerprint, identifier
from .worker import Worker, job_key


def authorize(db, token, project, *, write=False):
    if not isinstance(token, str) or not 20 <= len(token) <= 256:
        raise RuntimeFault('AUTH_AGENT_REQUIRED')
    key_id = hashlib.sha256(token.encode()).hexdigest()
    value = meta(db, 'automation:key:' + key_id) or {}
    scopes = ('agent_plan',) if write else ('agent_read', 'agent_plan')
    if (value.get('scope') not in scopes or value.get('project') != project
            or value.get('expires_at', 0) <= time.time()):
        raise RuntimeFault('AUTH_AGENT_DENIED')
    return value, 'agent:' + key_id


class _TransactionStore(RuntimeStore):
    """Only used for Worker.prepare after caller authentication, never execution."""
    def __init__(self, store, db, actor):
        self.root, self.db, self.actor = store.root, db, actor

    @contextmanager
    def connect(self):
        yield self.db

    def authorize(self, db, token, role=None, project=None):
        return self.actor


class VideoPlans:
    def __init__(self, store, project):
        self.store, self.project = store, identifier(project)

    def _load(self, db, plan_id):
        if not isinstance(plan_id, str) or not re.fullmatch('[0-9a-f]{64}', plan_id):
            raise RuntimeFault('AGENT_PLAN_ID_REQUIRED')
        saved = meta(db, 'agent:video:plan:' + plan_id)
        if not saved or saved['project'] != self.project:
            raise RuntimeFault('AGENT_PLAN_MISSING')
        return saved

    def _prepare(self, db, actor, task, revision):
        row = self.store._current(db, self.project, task, revision)
        profile = meta(db, 'video:profile:' + self.project)
        sku = json.loads(row['input'])['sku_id']
        if not profile or sku not in profile['assets']:
            raise RuntimeFault('VIDEO_SETUP_FOR_SKU_REQUIRED')
        values = {'project': self.project, 'task': task, 'revision': revision,
                  **profile['assets'][sku],
                  **{k: profile[k] for k in ('credential_ref', 'billing_owner', 'region')}}
        prepared = Worker(_TransactionStore(self.store, db, actor)).prepare('internal', **values)
        return prepared, fingerprint(profile)

    def _fence(self, db, actor, saved):
        if saved.get('revoked') or saved['expires_at'] <= time.time():
            raise RuntimeFault('AGENT_PLAN_EXPIRED_OR_REVOKED')
        prepared, profile_digest = self._prepare(db, actor, saved['task'], saved['revision'])
        if (prepared['request_plan_sha256'] != saved['worker_plan_sha256']
                or profile_digest != saved['profile_digest']):
            raise RuntimeFault('AGENT_PLAN_CHANGED')

    def _view(self, db, saved):
        plan = saved['worker_plan']
        job = meta(db, job_key(self.project, saved['task'], saved['revision']))
        # Presence of another path's job is not proof this plan was submitted.
        submitted = bool(saved.get('submitted') and job and job.get('agent_plan_id') == saved['plan_id'])
        row = db.execute('SELECT state,provider_id,artifact,input_digest,configuration_digest FROM tasks '
                         'WHERE project=? AND id=? AND revision=?',
                         (self.project, saved['task'], saved['revision'])).fetchone()
        latest = db.execute('SELECT MAX(revision) FROM tasks WHERE project=? AND id=?',
                            (self.project, saved['task'])).fetchone()[0]
        phase = ('revoked' if saved.get('revoked') else 'enqueued' if submitted else
                 'execution_conflict' if saved.get('submitted') else
                 'expired' if saved['expires_at'] <= time.time() else
                 'approved' if saved.get('approved_by') else 'prepared')
        state = row['state'] if row else 'missing'
        receipt = bool(submitted and (job.get('provider_receipt') or (row and row['provider_id'])))
        artifact = json.loads(row['artifact']) if submitted and row and row['artifact'] else None
        return {'schema': 'vf-agent-video-plan-v1', 'project': self.project,
                'task': saved['task'], 'revision': saved['revision'], 'plan_id': saved['plan_id'],
                'plan_sha256': saved['plan_sha256'], 'request_id': saved['request_id'],
                'phase': phase, 'expires_at': saved['expires_at'],
                'input_digest': plan['input_digest'], 'configuration_digest': plan['configuration_digest'],
                'model': plan['model'], 'duration_seconds': plan['specification']['duration'],
                'reference_count': len(plan['specification']['references']),
                'max_submissions': 1, 'cost': {'quote_available': False, 'currency': None,
                'amount': None, 'approval_basis': 'one_submission_at_configured_provider_rate'},
                'approval_authority': 'local_administrator', 'approval_required': not bool(saved.get('approved_by')),
                'task_state': state, 'provider_receipt_recorded': receipt,
                'current_revision': latest, 'is_current_revision': latest == saved['revision'],
                'artifact': {k: artifact[k] for k in ('sha256', 'verification')} if artifact else None,
                'execution_approval_valid': bool(submitted and not job.get('approval_revoked')
                                                  and job['expires_at'] > time.time()),
                'reconciliation_required': submitted and state == 'submission_unknown' and not receipt,
                'automatic_resubmit': False, 'human_acceptance': 'separate_review_required'}

    def prepare(self, token, task, revision, request_id):
        identifier(task); identifier(request_id)
        if type(revision) is not int or revision < 1:
            raise RuntimeFault('AGENT_REVISION_REQUIRED')
        plan_id = fingerprint([self.project, request_id])
        key = 'agent:video:plan:' + plan_id
        with self.store.connect() as db:
            _, actor = authorize(db, token, self.project, write=True)
            old = meta(db, key)
            if old:
                if old['task'] != task or old['revision'] != revision:
                    raise RuntimeFault('AGENT_REQUEST_ID_CONFLICT')
                return {**self._view(db, old), 'replayed': True}
            if meta(db, job_key(self.project, task, revision)):
                raise RuntimeFault('AGENT_TASK_ALREADY_HAS_EXECUTION')
            prepared, profile_digest = self._prepare(db, actor, task, revision)
            saved = {'project': self.project, 'task': task, 'revision': revision,
                     'request_id': request_id, 'plan_id': plan_id, 'created_by': actor,
                     'expires_at': time.time() + 3600, 'profile_digest': profile_digest,
                     'worker_plan': prepared['plan'], 'worker_plan_sha256': prepared['request_plan_sha256']}
            saved['plan_sha256'] = fingerprint(saved)
            save(db, key, saved)
            self.store.audit(db, actor, 'agent_video_plan_prepared', self.project, task, revision)
            return {**self._view(db, saved), 'provider_requests': 0, 'replayed': False}

    def owner(self, token, action, plan_id, expected_plan=None):
        if action not in ('inspect', 'approve', 'revoke'):
            raise RuntimeFault('AGENT_OWNER_ACTION_INVALID')
        with self.store.connect() as db:
            actor = self.store.authorize(db, token, 'admin')
            saved = self._load(db, plan_id)
            if action != 'inspect':
                if expected_plan != saved['plan_sha256']:
                    raise RuntimeFault('AGENT_PLAN_DIGEST_REQUIRED')
                if saved.get('submitted') and action == 'approve':
                    raise RuntimeFault('AGENT_PLAN_ALREADY_ENQUEUED')
                if action == 'approve':
                    self._fence(db, actor, saved)
                    if meta(db, job_key(self.project, saved['task'], saved['revision'])):
                        raise RuntimeFault('AGENT_TASK_ALREADY_HAS_EXECUTION')
                    # Repeating approval never renews expiry or grants another call.
                    saved.setdefault('approved_by', actor)
                else:
                    if saved.get('submitted'):
                        key = job_key(self.project, saved['task'], saved['revision'])
                        job = meta(db, key)
                        # Revocation must remain possible after configuration drift.
                        row = db.execute('SELECT state,provider_id FROM tasks WHERE project=? AND id=? AND revision=?',
                                         (self.project, saved['task'], saved['revision'])).fetchone()
                        if (not row or not job or job.get('agent_plan_id') != plan_id or row['state'] != 'ready'
                                or row['provider_id'] or job.get('provider_receipt')):
                            raise RuntimeFault('AGENT_EXECUTION_ALREADY_STARTED_OR_CHANGED')
                        job['expires_at'] = 0
                        job['approval_revoked'] = 'agent_owner_revoked'
                        save(db, key, job)
                    saved['revoked'] = True
                save(db, 'agent:video:plan:' + plan_id, saved)
                self.store.audit(db, actor, 'agent_video_plan_' + action,
                                 self.project, saved['task'], saved['revision'])
            result = self._view(db, saved)
            if action == 'inspect':
                row = db.execute('SELECT input FROM tasks WHERE project=? AND id=? AND revision=?',
                                 (self.project, saved['task'], saved['revision'])).fetchone()
                result['owner_review'] = {'input': json.loads(row['input']) if row else None,
                    'billing_owner': saved['worker_plan']['billing_owner'],
                    'region': saved['worker_plan']['region'], 'feishu_identity_verified': False}
            return result

    def submit(self, token, plan_id, expected_plan):
        with self.store.connect() as db:
            _, actor = authorize(db, token, self.project, write=True)
            saved = self._load(db, plan_id)
            if expected_plan != saved['plan_sha256']:
                raise RuntimeFault('AGENT_PLAN_DIGEST_REQUIRED')
            if saved.get('submitted'):
                return {**self._view(db, saved), 'replayed': True, 'provider_requests': 0}
            if not saved.get('approved_by'):
                raise RuntimeFault('AGENT_OWNER_APPROVAL_REQUIRED')
            self._fence(db, actor, saved)
            key = job_key(self.project, saved['task'], saved['revision'])
            if meta(db, key):
                raise RuntimeFault('AGENT_TASK_ALREADY_HAS_EXECUTION')
            save(db, key, {'plan': saved['worker_plan'], 'plan_sha256': saved['worker_plan_sha256'],
                          'expires_at': saved['expires_at'], 'provider_receipt': None, 'agent_plan_id': plan_id})
            saved['submitted'] = True
            save(db, 'agent:video:plan:' + plan_id, saved)
            self.store.audit(db, actor, 'agent_video_plan_enqueued', self.project, saved['task'], saved['revision'])
            return {**self._view(db, saved), 'replayed': False, 'provider_requests': 0}

    def receipt(self, token, plan_id):
        with self.store.connect() as db:
            authorize(db, token, self.project)
            return self._view(db, self._load(db, plan_id))
