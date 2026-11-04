"""Owner-enabled Feishu employee entry on the actual local business ledger.

The employee receives a short-lived application grant, never the owner token,
Feishu user token, app secret, execution key or filesystem access. Production
workflow observations are immutable, bounded snapshots, not live write adapters.
"""
import hashlib
import json
from pathlib import Path
import re
import secrets
import time

from .agent_api import AgentAPI
from .agent_plans import employee_authorization
from .agent_script_plans import ScriptPlans
from .feishu_bridge import meta, save
from .feishu_client import FeishuClient
from .feishu_oauth import DeviceOAuth
from .runtime_store import RuntimeFault, fingerprint, identifier, private_file

READ_ROUTES = {'capabilities', 'status', 'tasks', 'task', 'script/receipt', 'video/receipt'}
PLAN_ROUTES = {'script/catalog', 'script/plans', 'script/content', 'script/prepare-record',
               'script/submit', 'video/prepare', 'video/submit'}
STATUSES = {'new', 'running', 'success', 'error', 'canceled', 'crashed', 'waiting', 'unknown'}


def policy_key(project): return 'employee:policy:' + project

def private_bytes(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path: raise RuntimeFault('EMPLOYEE_PRIVATE_FILE_REQUIRED')
    private_file(path)
    if path.stat().st_size > 131072: raise RuntimeFault('EMPLOYEE_FILE_TOO_LARGE')
    return path.read_bytes()


def private_json(path):
    try: return json.loads(private_bytes(path))
    except (ValueError, UnicodeError): raise RuntimeFault('EMPLOYEE_FILE_INVALID') from None


def configuration(path, plan, *, allow_plans=False, clock=time.time):
    value = private_json(path)
    required = {'schema', 'project', 'tenant_key', 'open_id', 'expires_at', 'allow_plans'}
    if (not isinstance(value, dict) or not required <= set(value) or set(value)-required-{'workflow_source'}
            or value['schema'] != 'vf-business-employee-v1' or value['project'] != plan['project']
            or value['tenant_key'] != plan['binding']['tenant_key']
            or not isinstance(value['open_id'], str) or not re.fullmatch(r'ou_[A-Za-z0-9_-]{1,128}', value['open_id'])
            or type(value['allow_plans']) is not bool
            or type(value['expires_at']) not in (int, float)
            or not clock() < value['expires_at'] <= clock()+7200):
        raise RuntimeFault('EMPLOYEE_CONFIGURATION_INVALID')
    if value['allow_plans'] and (not allow_plans or value['open_id'] not in plan['binding']['submitters']):
        raise RuntimeFault('EMPLOYEE_PLANS_NOT_AUTHORIZED')
    if 'workflow_source' in value:
        source = value['workflow_source']
        if (not isinstance(source, dict) or set(source) != {'path', 'sha256', 'workflow_ids'}
                or not isinstance(source['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', source['sha256'])
                or not isinstance(source['workflow_ids'], list) or not 1 <= len(source['workflow_ids']) <= 10
                or len(set(source['workflow_ids'])) != len(source['workflow_ids'])
                or any(not isinstance(x, str) or not re.fullmatch('[A-Za-z0-9_-]{1,80}', x) for x in source['workflow_ids'])):
            raise RuntimeFault('EMPLOYEE_WORKFLOW_SOURCE_INVALID')
        observation(value, clock=clock)
    return value


def observation(config, *, clock=time.time):
    source = config.get('workflow_source')
    if not source: raise RuntimeFault('EMPLOYEE_WORKFLOW_SOURCE_UNAVAILABLE')
    raw = private_bytes(source['path'])
    try: value = json.loads(raw)
    except (ValueError, UnicodeError): raise RuntimeFault('EMPLOYEE_WORKFLOW_SOURCE_INVALID') from None
    if hashlib.sha256(raw).hexdigest() != source['sha256']:
        raise RuntimeFault('EMPLOYEE_WORKFLOW_SOURCE_CHANGED')
    if (not isinstance(value, dict) or set(value) != {'schema', 'project', 'observed_at', 'environment', 'workflows'}
            or value['schema'] != 'vf-workflow-observation-v1' or value['project'] != config['project']
            or value['environment'] != 'production-readonly' or type(value['observed_at']) not in (int, float)
            or not 0 <= clock()-value['observed_at'] <= 900 or not isinstance(value['workflows'], list)
            or len(value['workflows']) != len(source['workflow_ids'])):
        raise RuntimeFault('EMPLOYEE_WORKFLOW_SOURCE_STALE_OR_INVALID')
    ids = []
    for row in value['workflows']:
        if (not isinstance(row, dict) or set(row) != {'id', 'active', 'version_id', 'source_sha256', 'latest_execution'}
                or row['id'] not in source['workflow_ids'] or type(row['active']) is not bool
                or not isinstance(row['version_id'], str) or not re.fullmatch('[A-Za-z0-9-]{1,80}', row['version_id'])
                or not isinstance(row['source_sha256'], str) or not re.fullmatch('[a-f0-9]{64}', row['source_sha256'])):
            raise RuntimeFault('EMPLOYEE_WORKFLOW_SOURCE_INVALID')
        latest = row['latest_execution']
        if latest is not None and (not isinstance(latest, dict) or set(latest) != {'id', 'status'}
                or not isinstance(latest['id'], str) or not re.fullmatch('[0-9]{1,24}', latest['id'])
                or latest['status'] not in STATUSES):
            raise RuntimeFault('EMPLOYEE_WORKFLOW_SOURCE_INVALID')
        ids.append(row['id'])
    if set(ids) != set(source['workflow_ids']): raise RuntimeFault('EMPLOYEE_WORKFLOW_SOURCE_INVALID')
    return {**value, 'source_sha256': source['sha256'], 'read_only': True, 'live': False,
            'production_writes': 0, 'scope': 'Observed metadata only; no raw workflow, execution data, SSH or n8n credentials.'}


def revoke_grants(db, project, session=None):
    for row in db.execute("SELECT key,value FROM meta WHERE key LIKE 'automation:key:%'").fetchall():
        value = json.loads(row['value'])
        if value.get('project') == project and value.get('employee_subject') and (session is None or value.get('employee_session') == session):
            db.execute('DELETE FROM meta WHERE key=?', (row['key'],))


def manage(store, admin, project, action, *, expected_version=None, request_id=None):
    identifier(project)
    with store.connect() as db:
        actor = store.authorize(db, admin, 'admin')
        policy = meta(db, policy_key(project))
        if not policy: raise RuntimeFault('EMPLOYEE_NOT_CONFIGURED')
        if action == 'status':
            audit = [dict(r) for r in db.execute('SELECT seq,at,actor,action,task,revision FROM audit WHERE project=? ORDER BY seq DESC LIMIT 100', (project,))]
            return {'project': project, 'version': policy['version'], 'suspended': policy['suspended'],
                    'employee_subject': policy['subject'], 'audit': audit, 'owner_approval_shared': False}
        if (action not in ('suspend','resume') or type(expected_version) is not int
                or not isinstance(request_id, str) or not re.fullmatch('[A-Za-z0-9_-]{16,80}', request_id)):
            raise RuntimeFault('EMPLOYEE_MANAGEMENT_INPUT_INVALID')
        signature = fingerprint([action, expected_version])
        prior = next((x for x in policy['requests'] if x['id'] == request_id), None)
        if prior:
            if prior['signature'] != signature: raise RuntimeFault('EMPLOYEE_REQUEST_ID_CONFLICT')
            return {**prior['result'], 'replayed': True}
        if policy['version'] != expected_version: raise RuntimeFault('EMPLOYEE_VERSION_CHANGED')
        if len(policy['requests']) >= 100: raise RuntimeFault('EMPLOYEE_MANAGEMENT_LIMIT')
        policy['version'] += 1; policy['generation'] += 1; policy['suspended'] = action == 'suspend'
        revoke_grants(db, project)
        result = {'project': project, 'version': policy['version'], 'suspended': policy['suspended']}
        policy['requests'].append({'id': request_id, 'signature': signature, 'result': result})
        save(db, policy_key(project), policy); store.audit(db, actor, 'employee_'+action, project)
        return result


class EmployeeAccess:
    def __init__(self, store, admin, plan, config, master_key, *, clock=time.time,
                 oauth_factory=DeviceOAuth, client_factory=FeishuClient, script_factory=ScriptPlans):
        self.store, self.admin, self.plan, self.config = store, admin, plan, config
        self.project, self.clock = plan['project'], clock
        self.client_factory, self.script_factory = client_factory, script_factory
        self.subject = fingerprint([plan['app_id'], config['tenant_key'], config['open_id']])
        self.binding = fingerprint([self.project, self.subject, config['allow_plans']])
        self.session = secrets.token_hex(16); self.pairs = {}; self.users = {}; self.start_count = 0
        with store.connect() as db:
            store.authorize(db, admin, 'admin')
            app = meta(db, 'setup:feishu-app:'+self.project)
            if not app or app['app_id'] != plan['app_id']: raise RuntimeFault('EMPLOYEE_APP_BINDING_CHANGED')
        self.oauth = oauth_factory(app['app_id'], store.resolve_secret(app['credential_ref'][7:], master_key))

    def start(self):
        """Called only while holding the runtime lock, before opening the server."""
        with self.store.connect() as db:
            self.store.authorize(db, self.admin, 'admin')
            policy = meta(db, policy_key(self.project))
            if policy and policy['binding'] != self.binding: raise RuntimeFault('EMPLOYEE_IDENTITY_BINDING_CHANGED')
            if not policy: policy = {'binding': self.binding, 'subject': self.subject, 'version': 0, 'generation': 0, 'suspended': False, 'requests': []}
            policy['version'] += 1; policy['generation'] += 1
            revoke_grants(db, self.project); save(db, policy_key(self.project), policy)
            self.store.audit(db, 'local-owner', 'employee_entry_started', self.project)

    def close(self):
        with self.store.connect() as db: revoke_grants(db, self.project, self.session)
        self.pairs.clear(); self.users.clear()

    def active(self):
        if self.clock() >= self.config['expires_at']: raise RuntimeFault('AUTH_EMPLOYEE_WINDOW_EXPIRED')
        with self.store.connect() as db:
            policy = meta(db, policy_key(self.project))
            if not policy or policy['binding'] != self.binding or policy['suspended']: raise RuntimeFault('AUTH_EMPLOYEE_SUSPENDED')
            if meta(db, 'feishu:binding:'+self.project) != self.plan['binding']: raise RuntimeFault('AUTH_EMPLOYEE_PROJECT_CHANGED')
        return policy

    def call(self, operation, token, body, api):
        if not isinstance(body, dict): raise RuntimeFault('EMPLOYEE_FIELDS_INVALID')
        policy = self.active()
        if operation == 'connect/start':
            if body or token: raise RuntimeFault('EMPLOYEE_FIELDS_INVALID')
            self.pairs = {k:v for k,v in self.pairs.items() if v['expires_at'] > self.clock()}
            if len(self.pairs) >= 5 or self.start_count >= 20: raise RuntimeFault('EMPLOYEE_PAIRING_LIMIT')
            self.start_count += 1; upstream = self.oauth.start(); raw = secrets.token_urlsafe(32)
            expiry = min(self.config['expires_at'], self.clock()+upstream['expires_in'], self.clock()+300)
            self.pairs[hashlib.sha256(raw.encode()).hexdigest()] = {**upstream, 'expires_at': expiry, 'next_poll': self.clock()+upstream['interval'], 'generation': policy['generation']}
            return {'pair_token': raw, 'verification_uri': upstream['verification_uri'], 'user_code': upstream['user_code'],
                    'expires_at': expiry, 'interval': upstream['interval']}
        if not isinstance(token, str) or not re.fullmatch('[A-Za-z0-9_-]{43}', token): raise RuntimeFault('AUTH_EMPLOYEE_REQUIRED')
        key = hashlib.sha256(token.encode()).hexdigest()
        if operation == 'connect/poll':
            if body: raise RuntimeFault('EMPLOYEE_FIELDS_INVALID')
            pair = self.pairs.get(key)
            if not pair or pair['expires_at'] <= self.clock() or pair['generation'] != policy['generation']:
                self.pairs.pop(key, None); raise RuntimeFault('AUTH_EMPLOYEE_PAIRING_EXPIRED')
            if self.clock() < pair['next_poll']: return {'pending': True, 'interval': pair['interval']}
            pair['next_poll'] = self.clock()+pair['interval']
            try: result = self.oauth.poll(pair['device_code'])
            except BaseException:
                self.pairs.pop(key, None); raise
            if result['status'] != 'authorized':
                if result['status'] == 'slow_down': pair['interval'] = min(60, pair['interval']+5); pair['next_poll'] = self.clock()+pair['interval']
                return {'pending': True, 'interval': pair['interval']}
            self.pairs.pop(key, None)  # Single claim even if identity/audit fails.
            who = self.client_factory(result['access_token']).identity()
            if who['tenant_key'] != self.config['tenant_key'] or who['open_id'] != self.config['open_id']:
                raise RuntimeFault('AUTH_EMPLOYEE_IDENTITY_DENIED')
            policy = self.active()
            if policy['generation'] != pair['generation']: raise RuntimeFault('AUTH_EMPLOYEE_PAIRING_EXPIRED')
            raw = secrets.token_urlsafe(32); grant_key = hashlib.sha256(raw.encode()).hexdigest()
            expiry = min(self.config['expires_at'], self.clock()+3600, self.clock()+result['expires_in'])
            grant = {'project': self.project, 'scope': 'agent_plan' if self.config['allow_plans'] else 'agent_read',
                     'expires_at': expiry, 'employee_subject': self.subject, 'employee_binding': self.binding,
                     'employee_generation': policy['generation'], 'employee_session': self.session}
            with self.store.connect() as db:
                current = meta(db, policy_key(self.project))
                if current != policy: raise RuntimeFault('AUTH_EMPLOYEE_POLICY_CHANGED')
                save(db, 'automation:key:'+grant_key, grant)
                self.store.audit(db, 'employee:'+self.subject+':'+grant_key, 'employee_connected', self.project)
            # Feishu user token is server-memory-only and only needed for authorized plans.
            self.users[grant_key] = (result['access_token'] if self.config['allow_plans'] else None, expiry)
            return {'schema': 'vf-business-employee-credential-v1', 'token': raw, 'project': self.project,
                    'expires_at': expiry, 'subject': self.subject, 'scope': grant['scope']}
        if key not in self.users or self.users[key][1] <= self.clock(): raise RuntimeFault('AUTH_EMPLOYEE_SESSION_EXPIRED')
        allowed = READ_ROUTES | (PLAN_ROUTES if self.config['allow_plans'] else set()) | {'workflows', 'disconnect'}
        if operation not in allowed: raise RuntimeFault('EMPLOYEE_OPERATION_DENIED')
        with employee_authorization(token):
            from .agent_plans import authorize
            with self.store.connect() as db: _, actor = authorize(db, token, self.project)
            if operation == 'disconnect':
                if body: raise RuntimeFault('EMPLOYEE_FIELDS_INVALID')
                with self.store.connect() as db:
                    db.execute('DELETE FROM meta WHERE key=?', ('automation:key:'+key,));self.store.audit(db, actor, 'employee_disconnected', self.project)
                self.users.pop(key, None);return {'revoked': True}
            if operation == 'workflows':
                if body: raise RuntimeFault('EMPLOYEE_FIELDS_INVALID')
                result = observation(self.config, clock=self.clock)
            else:
                if 'project' in body: raise RuntimeFault('EMPLOYEE_FIELDS_INVALID')
                scripts = self.script_factory(self.store, self.project, user_token_provider=lambda:self.users[key][0], open_id=self.config['open_id']) if self.config['allow_plans'] else None
                employee_api = AgentAPI(self.store, self.project, api.scheduler, plans_enabled=self.config['allow_plans'], script_plans=scripts)
                result = employee_api.call('/v1/agent/'+operation, token, {'project':self.project, **body})
            with self.store.connect() as db:
                authorize(db, token, self.project);self.store.audit(db, actor, 'employee_'+operation.replace('/','_'), self.project)
            return result
