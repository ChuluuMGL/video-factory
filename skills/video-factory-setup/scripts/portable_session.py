"""Version-bound welcome checkpoints. No credentials, remote calls or task ledger."""
import hashlib
import json
import re
from pathlib import Path
import time
import uuid

from portable_n8n import Fault, N8N_VERSION, identifier, origin, private_write

SCHEMA = 'vf-portable-welcome-v1'
PHASES = ('route', 'instance', 'n8n-auth', 'project', 'feishu', 'schema', 'models', 'background', 'handoff')


def source_digest(directory=None):
    directory = Path(__file__).resolve().parent if directory is None else Path(directory)
    h = hashlib.sha256()
    for p in sorted(directory.glob('*.py')):
        h.update(p.name.encode() + b'\0' + p.read_bytes() + b'\0')
    return h.hexdigest()


class WelcomeSession:
    def __init__(self, root, resume=False, new_project=False):
        self.root = Path(root).resolve()
        self.directory = self.root / 'welcome-state'
        self.path = self.directory / 'session.json'
        self.started = time.monotonic()
        fingerprint = source_digest()
        seed = {}
        if new_project and self.path.exists():
            previous = json.loads(self.path.read_text(encoding='utf-8'))
            if (previous.get('schema') != SCHEMA or previous.get('root') != str(self.root)
                    or previous.get('source_sha256') != fingerprint):
                raise Fault('WELCOME_SCOPE_CHANGED')
            if previous.get('status') != 'configured_not_accepted':
                raise Fault('WELCOME_INCOMPLETE_RESUME_BEFORE_NEW_PROJECT')
            seed = {k: previous[k] for k in ('route','origin','port') if k in previous}
            private_write(self.directory / ('history-' + uuid.uuid4().hex + '.json'), previous)
        if self.path.exists() and not new_project:
            if not resume:
                raise Fault('WELCOME_EXISTS_USE_RESUME_IN_ORIGINAL_DIRECTORY')
            self.data = json.loads(self.path.read_text(encoding='utf-8'))
            if self.data.get('schema') != SCHEMA or self.data.get('root') != str(self.root):
                raise Fault('WELCOME_SCOPE_CHANGED')
            if self.data.get('source_sha256') != fingerprint or self.data.get('n8n_version') != N8N_VERSION:
                raise Fault('WELCOME_SOURCE_CHANGED_USE_ORIGINAL_VERIFIED_SKILL')
        else:
            if resume:
                raise Fault('WELCOME_CHECKPOINT_MISSING_USE_ORIGINAL_DIRECTORY')
            self.data = {'schema': SCHEMA, 'root': str(self.root), 'source_sha256': fingerprint,
                         'n8n_version': N8N_VERSION, 'phase': 'route', 'attempts': 0,
                         'interactive_seconds': 0, 'status': 'in_progress', 'completed': [],
                         'production_ready': False, **seed}
        if new_project: self.data['new_project'] = True
        self.data['attempts'] += 1
        self.data['status'] = 'in_progress'
        self.save()

    def save(self):
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        private_write(self.path, self.data)

    def remember(self, key, value):
        if key == 'route':
            if value not in ('1', '2', '3', '4'): raise Fault('WELCOME_ROUTE_INVALID')
        elif key == 'origin': value = origin(value)
        elif key == 'project': value = identifier(value)
        elif key == 'port':
            if type(value) is not int or not 1024 <= value <= 65535: raise Fault('WELCOME_PORT_INVALID')
        else: raise Fault('WELCOME_FIELD_NOT_ALLOWED')
        if key in self.data and self.data[key] != value: raise Fault('WELCOME_SCOPE_CHANGED')
        self.data[key] = value
        self.save()
        return value

    def phase(self, phase):
        if phase not in PHASES: raise Fault('WELCOME_PHASE_INVALID')
        self.data['phase'] = phase
        self.save()

    def complete(self, phase):
        if phase not in PHASES: raise Fault('WELCOME_PHASE_INVALID')
        if phase not in self.data['completed']: self.data['completed'].append(phase)
        self.save()

    def finish(self, success=False):
        self.data['interactive_seconds'] += round(time.monotonic() - self.started, 3)
        self.data['status'] = 'configured_not_accepted' if success else 'paused_or_failed'
        self.save()

    def handoff(self):
        # This describes setup, never upgrades connectivity into business acceptance.
        return {'schema': SCHEMA, 'source_sha256': self.data['source_sha256'],
                'managed_n8n_version': N8N_VERSION, 'live_n8n_version': 'not_checked', 'route': self.data.get('route'),
                'binding_directory': str(self.root / 'bindings'),
                'attempts': self.data['attempts'], 'completed': list(self.data['completed']),
                'interactive_seconds_before_this_attempt': self.data['interactive_seconds'],
                'pending_acceptance': ['designated_owner', 'real_task_writeback', 'human_review',
                                       'independent_operator_install', 'desktop_login_restart'],
                'manual_steps': ['n8n_owner_and_api_key', 'feishu_app_permissions_and_base',
                                 'hidden_credential_input', 'designated_owner_acceptance'],
                'task_identity_contract': {'source': 'portable-n8n-feishu',
                    'required': ['project', 'base', 'table', 'record_id', 'task_id', 'revision'],
                    'core_task_mapping': 'not_configured', 'agent_http_api': 'not_available_on_this_route'}}


def inspect_session(root):
    """Offline, redacted receipt; never echo arbitrary JSON fields or error bodies."""
    root = Path(root).resolve()
    path = root / 'welcome-state/session.json'
    if not path.exists(): raise Fault('WELCOME_CHECKPOINT_MISSING_USE_ORIGINAL_DIRECTORY')
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema') != SCHEMA or data.get('root') != str(root): raise Fault('WELCOME_SCOPE_CHANGED')
    if data.get('phase') not in PHASES: raise Fault('WELCOME_PHASE_INVALID')
    project = identifier(data['project']) if data.get('project') else None
    bindings = root / 'bindings'
    pending = []
    flows = []
    if project:
        config_path = bindings / ('project-' + project + '.json')
        if config_path.exists():
            config = json.loads(config_path.read_text(encoding='utf-8'))
            if config.get('origin') != data.get('origin'): raise Fault('PROJECT_ORIGIN_CHANGED')
        # Only this project's recognized roles; do not sweep other projects.
        names = ['credentials-' + project, 'ledger-' + project]
        pattern = re.compile(r'binding-' + re.escape(project) + r'-(probe|schema|script|video-submit|video-poll|image|recover-image|recover|task-check)(?:-([0-9a-f]{8}))?$')
        for p in sorted(bindings.glob('binding-' + project + '-*.json')):
            match = pattern.fullmatch(p.stem)
            if not match or match[2] and match[1] not in ('probe','schema','task-check'): continue
            names.append(p.stem)
            b = json.loads(p.read_text(encoding='utf-8'))
            if b.get('origin') != data.get('origin'): raise Fault('PROJECT_ORIGIN_CHANGED')
            flows.append({'role': match[1], 'temporary_setup_probe': bool(match[2]),
                          'workflow': identifier(b['workflow']) if b.get('workflow') else None,
                          'historically_verified_disabled': b.get('verified_disabled') is True,
                          'live_status': 'not_checked'})
        for name in names:
            p = bindings / (name + '.json')
            if p.exists() and json.loads(p.read_text(encoding='utf-8')).get('pending'):
                pending.append(name)
    return {'schema': SCHEMA, 'project': project, 'phase': data['phase'],
            'source_matches': data.get('source_sha256') == source_digest(),
            'status': data.get('status') if data.get('status') in ('in_progress','configured_not_accepted','paused_or_failed') else 'unknown',
            'pending_resource_receipts': pending, 'flows': flows, 'production_ready': False,
            'network_requests': 0, 'provider_requests': 0,
            'next': ('use_original_verified_skill' if data.get('source_sha256') != source_digest() else
                     'reconcile_unknown_resources' if pending else 'welcome --resume'),
            'live_state': 'not_checked'}
