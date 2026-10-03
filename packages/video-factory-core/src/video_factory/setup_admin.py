"""Container-local wizard credentials. Stdin only; never an HTTP admin route."""
import hashlib
import json
import re
import secrets
import sys
import time
from pathlib import Path

from .feishu_oauth import DeviceOAuth
from .feishu_bridge import meta, save
from .onboarding import read_json, SetupError
from .postgres_store import PostgresStore
from .runtime_cli import secret_input
from .runtime_store import RuntimeFault, fingerprint
from .setup_project import inspect_project, session_plan


class SetupAdmin:
    def __init__(self, store, master_key=None):
        self.store, self.master_key = store, master_key

    def profile(self, db, project, plan_hash):
        value = meta(db, 'setup:feishu-app:'+project)
        if value and value.get('setup_plan_sha256') != plan_hash:
            raise RuntimeFault('SETUP_APP_PROFILE_PLAN_CHANGED')
        return value

    def close(self, token):
        if not isinstance(token, str) or not 1 <= len(token) <= 256:
            raise RuntimeFault('AUTH_REQUIRED')
        with self.store.connect() as db:
            db.execute('DELETE FROM sessions WHERE digest=?', (hashlib.sha256(token.encode()).hexdigest(),))
        return {'status': 'wizard_session_revoked'}

    def open(self, session, password):
        token = self.store.login('admin', password)['token']
        try:
            project = inspect_project(self.store, token, session)
            with self.store.connect() as db:
                profile = self.profile(db, project['project'], project['plan_sha256'])
                expires = time.time()+900
                db.execute('UPDATE sessions SET expires=? WHERE digest=?', (expires, hashlib.sha256(token.encode()).hexdigest()))
            return {'token': token, 'expires_at': expires, 'profile': profile, 'profile_sha256': fingerprint(profile)}
        except BaseException:
            self.close(token)
            raise

    def configure(self, token, session, app_id, app_secret, expected_profile):
        project = inspect_project(self.store, token, session)
        DeviceOAuth(app_id, app_secret)  # syntax validation only; no network
        with self.store.connect() as db:
            old = self.profile(db, project['project'], project['plan_sha256'])
            if fingerprint(old) != expected_profile: raise RuntimeFault('SETUP_APP_PROFILE_CHANGED')
        # A new alias never overwrites a credential still used by a live window.
        alias = 'setup_feishu_'+secrets.token_hex(16)
        self.store.put_secret(token, alias, app_secret, self.master_key)
        try:
            with self.store.connect() as db:
                actor = self.store.authorize(db, token, 'admin')
                stored = meta(db, 'setup:project:'+project['project'])
                current = db.execute('SELECT digest FROM projects WHERE id=?', (project['project'],)).fetchone()
                if (not stored or stored['plan_sha256'] != project['plan_sha256'] or not current
                        or current[0] != stored['runtime_configuration_digest']):
                    raise RuntimeFault('SETUP_PROJECT_CHANGED_RECONCILIATION_REQUIRED')
                if fingerprint(self.profile(db, project['project'], project['plan_sha256'])) != expected_profile:
                    raise RuntimeFault('SETUP_APP_PROFILE_CHANGED')
                profile = {'app_id': app_id, 'credential_ref': 'secret:'+alias, 'setup_plan_sha256': project['plan_sha256']}
                save(db, 'setup:feishu-app:'+project['project'], profile)
                self.store.audit(db, actor, 'setup_app_profile', project['project'])
            return {'profile': profile, 'profile_sha256': fingerprint(profile), 'credential_saved_encrypted': True}
        except BaseException:
            with self.store.connect() as db: db.execute('DELETE FROM vault WHERE alias=?', (alias,))
            raise

    def script(self, token, session, secret, billing_owner):
        from .script_jobs import profile
        from .runtime_store import identifier
        project = inspect_project(self.store, token, session)['project']
        identifier(billing_owner)
        if not isinstance(secret,str) or not 16<=len(secret)<=512 or any(c.isspace() for c in secret):
            raise RuntimeFault('SCRIPT_KEY_INVALID')
        alias='script_'+secrets.token_hex(16)
        self.store.put_secret(token,alias,secret,self.master_key)
        return profile(self.store,token,project,'secret:'+alias,billing_owner)

    def video(self,token,session,secret,billing_owner,region,assets):
        from .video_jobs import configure
        project=inspect_project(self.store,token,session)['project']
        if not isinstance(secret,str) or not 16<=len(secret)<=512 or any(c.isspace() for c in secret):raise RuntimeFault('VIDEO_KEY_INVALID')
        alias='video_'+secrets.token_hex(16)
        self.store.put_secret(token,alias,secret,self.master_key)
        return configure(self.store,token,project,'secret:'+alias,billing_owner,region,assets)

    def dispatch(self, payload):
        fields = {'open': {'action','session','password'}, 'close': {'action','token'},
                  'configure': {'action','token','session','app_id','app_secret','expected_profile'},
                  'status': {'action','token','draft'}, 'video': {'action','token','session','secret','billing_owner','region','assets'}, 'script': {'action','token','session','secret','billing_owner'},
                  'base_results': {'action','token','session','operation','expected_plan','event','step'},
                  'execution_key': {'action','token','session'}, 'revoke_execution': {'action','token','key_id'}}
        if not isinstance(payload, dict) or set(payload) != fields.get(payload.get('action'), set()):
            raise RuntimeFault('SETUP_ADMIN_FIELDS_INVALID')
        action = payload['action']
        if action == 'base_results':
            from .base_results import BaseResults
            project = inspect_project(self.store, payload['token'], payload['session'])['project']
            helper = BaseResults(self.store, self.master_key)
            operation = payload['operation']
            if operation == 'repair_plan': return helper.repair_plan(payload['token'], project, payload['event'], payload['step'])
            if operation == 'repair': return helper.repair(payload['token'], project, payload['event'], payload['step'], payload['expected_plan'])
            if operation == 'recovery_plan': return helper.recovery_plan(payload['token'], project)
            if operation == 'recover': return helper.recover(payload['token'], project, payload['expected_plan'])
            if operation == 'plan': return helper.prepare(payload['token'], project)
            if operation == 'enable': return helper.enable(payload['token'], project, payload['expected_plan'])
            if operation == 'pause': return helper.pause(payload['token'], project)
            if operation == 'status': return helper.status(payload['token'], project)
            if operation == 'sync':
                return helper.sync(project, Path('/media'), lambda db: self.store.authorize(db, payload['token'], 'admin'))
            raise RuntimeFault('BASE_RESULTS_ACTION_INVALID')
        if action == 'video':return self.video(payload['token'],payload['session'],payload['secret'],payload['billing_owner'],payload['region'],payload['assets'])
        if action == 'script':return self.script(payload['token'],payload['session'],payload['secret'],payload['billing_owner'])
        if action == 'execution_key':
            from .automation import issue_execution
            project=inspect_project(self.store,payload['token'],payload['session'])['project']
            return issue_execution(self.store,payload['token'],project,2160)
        if action == 'revoke_execution':
            from .automation import revoke
            return revoke(self.store,payload['token'],payload['key_id'])
        if action == 'open': return self.open(payload['session'], payload['password'])
        if action == 'close': return self.close(payload['token'])
        if action == 'configure':
            return self.configure(payload['token'], payload['session'], payload['app_id'], payload['app_secret'], payload['expected_profile'])
        from .setup_feishu import SetupFeishu
        return SetupFeishu(self.store).status(payload['token'], payload['draft'])


def rpc(stack, payload):
    # The container's result (including a short-lived token on open) is consumed
    # in memory by the local OS administrator, never printed by the wizard.
    if payload.get('action') == 'base_results':
        from .workspace import compose
        project = payload['session']['configuration']['project']['id']
        raw = compose(stack, project, 'exec', '-T', 'executor', 'python', '-m', 'video_factory.setup_admin',
                      data=json.dumps(payload).encode())
    else:
        raw = stack.compose('exec', '-T', 'runtime', 'python', '-m', 'video_factory.setup_admin',
                            data=json.dumps(payload).encode())
    result = json.loads(raw)
    if 'error' in result:
        code = result['error']
        raise RuntimeFault(code if isinstance(code, str) and re.fullmatch('[A-Z0-9_]{1,100}', code) else 'SETUP_ADMIN_FAILED')
    return result


if __name__ == '__main__':
    try:
        helper = SetupAdmin(PostgresStore('/state'), secret_input(Path('/run/secrets/runtime_master'), ''))
        print(json.dumps(helper.dispatch(read_json(sys.stdin))))
    except (RuntimeFault, SetupError) as error: print(json.dumps({'error': str(error)}))
    except Exception: print(json.dumps({'error': 'SETUP_ADMIN_FAILED'}))
