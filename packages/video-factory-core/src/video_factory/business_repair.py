"""Project-bound source repair on the business SQLite ledger, default off.

Only reviewed packaged source can be applied to an isolated ledger clone. This
service has no production transport, filesystem target, arbitrary patch or model.
"""
import difflib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

from .agent_plans import authorize
from .feishu_bridge import meta, save
from .repair_profile import PROFILE, BEFORE, AFTER, source, check
from .runtime_store import RuntimeFault, fingerprint, identifier

OPERATIONS = {'repair/inspect', 'repair/propose', 'repair/validate', 'repair/apply', 'repair/rollback'}
POLICY = {'profile': PROFILE, 'target': 'isolated-business-ledger', 'arbitrary_code': False,
          'production_writes': False, 'real_models': False,
          'sources': [source(BEFORE)['sha256'], source(AFTER)['sha256']]}
POLICY_HASH = fingerprint(POLICY)


def settings(value):
    if value is None: return None
    if (not isinstance(value, dict) or set(value) != {'profile', 'allow_apply'}
            or value['profile'] != PROFILE or type(value['allow_apply']) is not bool):
        raise RuntimeFault('EMPLOYEE_REPAIR_CONFIGURATION_INVALID')
    return value


def repair_scope(config):
    value = settings(config.get('repair'))
    return 'none' if value is None else 'write' if value['allow_apply'] else 'read'


def replay(reviewed):
    try:
        check(reviewed)
        result = subprocess.run([sys.executable, '-I', '-m', 'video_factory.repair_worker'],
            input=json.dumps(reviewed), capture_output=True, text=True, timeout=5,
            env={'LANG': 'C.UTF-8'}, cwd=Path(sys.executable).parent)
        if result.returncode or len(result.stdout) > 32768: raise ValueError()
        value = json.loads(result.stdout)
        if (value.get('kind') != 'reviewed-python-source' or type(value.get('passed')) is not bool
                or value.get('executed_source_sha256') != reviewed['sha256']
                or value.get('real_model_calls') != 0 or value.get('production_writes') != 0):
            raise ValueError()
        return value
    except (ValueError, OSError, subprocess.TimeoutExpired):
        raise RuntimeFault('REPAIR_VALIDATION_FAILED') from None


class BusinessRepair:
    def __init__(self, store, project, *, clock=time.time, runner=replay):
        self.store, self.project, self.clock, self.runner = store, identifier(project), clock, runner
        self.key = 'business:repair:'+project

    def initialize(self, db):
        if meta(db, self.key) is None:
            save(db, self.key, {'schema': 1, 'project': self.project, 'policy_sha256': POLICY_HASH,
                'clone': {'version': 1, 'source': source(BEFORE)}, 'candidates': [], 'requests': [], 'events': []})
        self.load(db)

    def load(self, db):
        state = meta(db, self.key)
        try:
            if (not state or state['schema'] != 1 or state['project'] != self.project
                    or state['policy_sha256'] != POLICY_HASH or type(state['clone']['version']) is not int
                    or state['clone']['version'] < 1): raise ValueError()
            check(state['clone']['source'])
            previous = ''
            for event in state['events']:
                body = {k:v for k,v in event.items() if k != 'sha256'}
                if event['previous'] != previous or event['sha256'] != fingerprint(body): raise ValueError()
                previous = event['sha256']
            if len(state['candidates']) > 20 or len(state['requests']) > 100: raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise RuntimeFault('REPAIR_LEDGER_INVALID') from None
        return state

    def clone(self, state):
        value = state['clone']
        return {**value, 'fingerprint': fingerprint([self.project, POLICY_HASH, value])}

    def auth(self, db, token, write):
        grant, actor = authorize(db, token, self.project)
        allowed = ('write',) if write else ('read', 'write')
        if not grant.get('employee_subject') or grant.get('employee_repair_scope') not in allowed:
            raise RuntimeFault('AUTH_EMPLOYEE_REPAIR_DENIED')
        return grant, actor

    @staticmethod
    def candidate_hash(candidate):
        return fingerprint([candidate['owner'], candidate['source_fingerprint'], POLICY_HASH,
                            candidate['before'], candidate['after']])

    def fence(self, state, candidate):
        if (candidate['source_fingerprint'] != self.clone(state)['fingerprint']
                or candidate['before'] != state['clone']['source']
                or candidate['after'] != source(AFTER)
                or candidate['candidate_sha256'] != self.candidate_hash(candidate)):
            raise RuntimeFault('REPAIR_SOURCE_OR_CANDIDATE_CHANGED')

    def invoke(self, operation, token, body):
        if operation not in OPERATIONS: raise RuntimeFault('REPAIR_OPERATION_DENIED')
        op = operation.split('/')[1]
        fields = set() if op == 'inspect' else ({'request_id', 'source_fingerprint'} if op == 'propose'
                    else {'request_id', 'candidate_id', 'expected_version'})
        if not isinstance(body, dict) or set(body) != fields: raise RuntimeFault('REPAIR_FIELDS_INVALID')
        if op != 'inspect':
            if not isinstance(body['request_id'], str) or not re.fullmatch('[A-Za-z0-9_-]{16,80}', body['request_id']):
                raise RuntimeFault('REPAIR_REQUEST_ID_INVALID')
            if op == 'propose':
                if not isinstance(body['source_fingerprint'], str) or not re.fullmatch('[a-f0-9]{64}', body['source_fingerprint']):
                    raise RuntimeFault('REPAIR_SOURCE_REQUIRED')
            elif (not isinstance(body['candidate_id'], str) or not re.fullmatch('[a-f0-9]{64}', body['candidate_id'])
                    or type(body['expected_version']) is not int or body['expected_version'] < 1):
                raise RuntimeFault('REPAIR_CANDIDATE_VERSION_REQUIRED')
        # Slow verification happens outside the ledger write lock, then all
        # authority, version and source fences are repeated in the commit.
        proof = None
        if op == 'validate':
            with self.store.connect() as db:
                grant, _ = self.auth(db, token, True); state = self.load(db)
                prior = self.prior(state, grant, op, body)
                if prior: return {**prior, 'replayed': True, 'current_clone': self.clone(state)}
                candidate = self.find(state, grant, body); self.fence(state, candidate)
                if candidate['status'] not in ('proposed', 'validated', 'test_failed'):
                    raise RuntimeFault('REPAIR_STATE_INVALID')
                before, after = candidate['before'], candidate['after']
                authority = [grant['employee_session'], grant['employee_generation']]
            proof = {'before': self.runner(before), 'after': self.runner(after),
                     'authority': authority, 'at': self.clock()}
        with self.store.connect() as db:
            grant, actor = self.auth(db, token, op != 'inspect'); state = self.load(db)
            if op == 'inspect':
                return {'schema': 'vf-business-repair-v1', 'project': self.project, 'policy': POLICY,
                        'clone': self.clone(state), 'candidates': state['candidates'], 'audit': state['events'],
                        'scope': grant['employee_repair_scope'], 'production_writes': 0, 'real_model_calls': 0}
            prior = self.prior(state, grant, op, body)
            if prior: return {**prior, 'replayed': True, 'current_clone': self.clone(state)}
            if len(state['requests']) >= 100: raise RuntimeFault('REPAIR_REQUEST_LIMIT')
            if op == 'propose':
                if len(state['candidates']) >= 20: raise RuntimeFault('REPAIR_CANDIDATE_LIMIT')
                if body['source_fingerprint'] != self.clone(state)['fingerprint']: raise RuntimeFault('REPAIR_SOURCE_CHANGED')
                if state['clone']['source'] != source(BEFORE): raise RuntimeFault('REPAIR_NO_CHANGE')
                candidate = {'id': fingerprint([self.project, grant['employee_subject'], body['request_id']]),
                    'version': 1, 'status': 'proposed', 'owner': grant['employee_subject'],
                    'source_fingerprint': body['source_fingerprint'], 'before': state['clone']['source'],
                    'after': source(AFTER), 'verification': None}
                candidate['candidate_sha256'] = self.candidate_hash(candidate)
                candidate['diff'] = ''.join(difflib.unified_diff(BEFORE.splitlines(True), AFTER.splitlines(True),
                                                               fromfile='before.py', tofile='after.py'))
                state['candidates'].append(candidate)
            else:
                candidate = self.find(state, grant, body)
                if op == 'rollback':
                    if (candidate['status'] != 'applied' or candidate.get('applied_fingerprint') != self.clone(state)['fingerprint']
                            or candidate['candidate_sha256'] != self.candidate_hash(candidate)
                            or candidate['before'] != source(BEFORE) or candidate['after'] != source(AFTER)):
                        raise RuntimeFault('REPAIR_ROLLBACK_CONFLICT')
                    state['clone'] = {'version': state['clone']['version']+1, 'source': candidate['before']}
                    candidate['status'] = 'rolled_back'
                else:
                    self.fence(state, candidate)
                    if op == 'validate':
                        if proof['authority'] != [grant['employee_session'], grant['employee_generation']]:
                            raise RuntimeFault('AUTH_EMPLOYEE_REPAIR_CHANGED')
                        candidate['verification'] = {**proof, 'candidate_sha256': candidate['candidate_sha256'],
                                                    'source_fingerprint': candidate['source_fingerprint']}
                        candidate['status'] = 'validated' if proof['after']['passed'] and not proof['before']['passed'] else 'test_failed'
                    else:
                        v = candidate['verification']
                        if (candidate['status'] != 'validated' or not v or not v['after']['passed']
                                or v['candidate_sha256'] != candidate['candidate_sha256']
                                or v['source_fingerprint'] != candidate['source_fingerprint']
                                or v['authority'] != [grant['employee_session'], grant['employee_generation']]
                                or not 0 <= self.clock()-v['at'] <= 600):
                            raise RuntimeFault('REPAIR_VALIDATION_REQUIRED')
                        state['clone'] = {'version': state['clone']['version']+1, 'source': candidate['after']}
                        candidate['status'] = 'applied'; candidate['applied_fingerprint'] = self.clone(state)['fingerprint']
                candidate['version'] += 1
            event = {'actor': actor, 'operation': op, 'candidate_id': candidate['id'], 'candidate_version': candidate['version'],
                     'request_id': body['request_id'], 'at': self.clock(), 'clone_fingerprint': self.clone(state)['fingerprint'],
                     'previous': state['events'][-1]['sha256'] if state['events'] else ''}
            state['events'].append({**event, 'sha256': fingerprint(event)})
            # Freeze the response: later mutation must not rewrite a prior receipt.
            result = json.loads(json.dumps({'candidate': candidate, 'current_clone': self.clone(state), 'replayed': False,
                                            'applied_to': 'isolated-business-ledger', 'production_writes': 0, 'real_model_calls': 0}))
            state['requests'].append({'key': [grant['employee_subject'], body['request_id']],
                                      'signature': fingerprint([op, body]), 'result': result})
            save(db, self.key, state); self.store.audit(db, actor, 'business_repair_'+op, self.project, candidate['id'], candidate['version'])
            return result

    def prior(self, state, grant, op, body):
        prior = next((v for v in state['requests'] if v['key'] == [grant['employee_subject'], body['request_id']]), None)
        if prior and prior['signature'] != fingerprint([op, body]): raise RuntimeFault('REPAIR_REQUEST_ID_CONFLICT')
        return prior['result'] if prior else None

    @staticmethod
    def find(state, grant, body):
        candidate = next((c for c in state['candidates'] if c['id'] == body['candidate_id']), None)
        if candidate is None: raise RuntimeFault('REPAIR_CANDIDATE_MISSING')
        if candidate['owner'] != grant['employee_subject']: raise RuntimeFault('AUTH_EMPLOYEE_REPAIR_OWNER_DENIED')
        if candidate['version'] != body['expected_version']: raise RuntimeFault('REPAIR_VERSION_CHANGED')
        return candidate
