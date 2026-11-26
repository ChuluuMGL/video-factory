"""Scoped Agent metadata and opt-in plans; no owner approval or provider routes."""
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import TCPServer
import io
import shutil

from .onboarding import read_json
from .feishu_bridge import meta
from .runtime_store import RuntimeFault, canonical, identifier
from .agent_plans import VideoPlans, authorize
from .agent_script_plans import ScriptPlans
from .workbench_actions import FIELDS as WORKBENCH_FIELDS, WorkbenchActions, row_for, task_id
from .workbench_media import WorkbenchMedia


class AgentAPI:
    def __init__(self, store, project, scheduler, *, plans_enabled=False, script_plans=None, employee_access=None, media_root=None):
        self.store, self.project, self.scheduler = store, identifier(project), scheduler
        self.plans_enabled = plans_enabled
        self.script_plans = script_plans
        self.employee_access = employee_access
        self.media = WorkbenchMedia(store, project, media_root)

    def call(self, route, token, body, *, principal=None):
        if not isinstance(token, str) or not 20 <= len(token) <= 256:
            raise RuntimeFault('AUTH_AGENT_REQUIRED')
        if not isinstance(body, dict) or body.get('project') != self.project:
            raise RuntimeFault('AUTH_AGENT_PROJECT_DENIED')
        op = route.removeprefix('/v1/agent/')
        if route.startswith('/v1/agent/') and op in WORKBENCH_FIELDS:
            params = {key: value for key, value in body.items() if key != 'project'}
            if set(params) - WORKBENCH_FIELDS[op]:
                raise RuntimeFault('WORKBENCH_FIELDS_INVALID')
            actions = WorkbenchActions(self.store, self.project, self.script_plans)
            if op in ('stats', 'failures', 'reviews'):
                with self.store.connect() as db:
                    authorize(db, token, self.project, workbench=True)
                    return actions.read(db, op, params, self.media.issue)
            return actions.action(op, token, principal, params)
        fields = {
            '/v1/agent/capabilities': {'project'},
            '/v1/agent/media/preview-token': {'project', 'task_key', 'expected_version'},
            '/v1/agent/status': {'project'},
            '/v1/agent/project_stats': {'project'},
            '/v1/agent/list_failures': {'project', 'after'},
            '/v1/agent/tasks': {'project', 'after'},
            '/v1/agent/task': {'project', 'task', 'revision'},
            '/v1/agent/video/prepare': {'project', 'task', 'revision', 'request_id'},
            '/v1/agent/video/submit': {'project', 'plan_id', 'plan_sha256'},
            '/v1/agent/video/receipt': {'project', 'plan_id'},
            '/v1/agent/script/prepare': {'project', 'request_id', 'task', 'sku_id', 'brief', 'expected_revision'},
            '/v1/agent/script/prepare-record': {'project', 'request_id', 'record', 'expected_revision'},
            '/v1/agent/script/submit': {'project', 'plan_id', 'plan_sha256'},
            '/v1/agent/script/receipt': {'project', 'plan_id'},
            '/v1/agent/script/catalog': {'project'},
            '/v1/agent/script/plans': {'project', 'after'},
            '/v1/agent/script/content': {'project', 'plan_id', 'plan_sha256'},
        }
        if route not in fields:
            raise RuntimeFault('AGENT_ROUTE_NOT_AVAILABLE')
        if set(body) - fields[route]:
            raise RuntimeFault('AGENT_FIELDS_INVALID')
        if '/script/' in route:
            if route.endswith('/receipt'):
                return ScriptPlans(self.store, self.project).receipt(token, body.get('plan_id'))
            if self.script_plans is None:
                raise RuntimeFault('AGENT_SCRIPT_PLANS_DISABLED')
            if route.endswith('/catalog'):
                return self.script_plans.catalog(token)
            if route.endswith('/plans'):
                return self.script_plans.listing(token, body.get('after', ''))
            if route.endswith('/content'):
                return self.script_plans.content(token, body.get('plan_id'), body.get('plan_sha256'))
            if route.endswith('/submit'):
                return self.script_plans.submit(token, body.get('plan_id'), body.get('plan_sha256'))
            request = {k: v for k, v in body.items() if k not in ('project', 'request_id')}
            if set(body) != fields[route]: raise RuntimeFault('AGENT_FIELDS_INVALID')
            return self.script_plans.prepare(token, body.get('request_id'), request)
        if '/video/' in route:
            plans = VideoPlans(self.store, self.project)
            if route.endswith('/receipt'):
                return plans.receipt(token, body.get('plan_id'))
            if not self.plans_enabled:
                raise RuntimeFault('AGENT_PLANS_DISABLED')
            if route.endswith('/prepare'):
                return plans.prepare(token, body.get('task'), body.get('revision'), body.get('request_id'))
            return plans.submit(token, body.get('plan_id'), body.get('plan_sha256'))
        with self.store.connect() as db:
            value, _ = authorize(db, token, self.project, workbench=route.endswith(('/capabilities', '/media/preview-token')))
            if route.endswith('/media/preview-token'):
                row = row_for(self.store, db, self.project, task_id(self.project, body.get('task_key')),
                              body.get('expected_version'))
                return {'schema': 'vf-agent-media-v1', 'project': self.project, **self.media.issue(row)}
            if route.endswith('/capabilities'):
                abilities = ['media_preview'] if self.media.root is not None else []
                if principal and value.get('workbench_principal') == principal:
                    policy = meta(db, 'workbench:delegation:' + self.project) or {}
                    roles = set(value.get('workbench_roles', [])) & set(policy.get(principal, []))
                    abilities.extend(name for role, name in (
                        ('repair', 'repair_and_schedule'), ('review', 'submit_review_decision'),
                        ('edit', 'edit_commit'), ('cost', 'approve_plan')) if role in roles)
                native = meta(db, 'native:config:' + self.project) or {}
                if native.get('enabled') and self.scheduler.status().get('feishu_writeback') == 'enabled_requires_readback':
                    abilities.append('native_record_submission')
                enabled = self.plans_enabled and value['scope'] == 'agent_plan'
                scripts = self.script_plans is not None and value['scope'] == 'agent_plan'
                return {'schema': 'vf-agent-read-v1', 'project': self.project,
                        'available': ['task_metadata', 'task_list', 'runtime_status', 'video_plan_receipt', 'script_plan_receipt',
                                      'project_stats', 'list_failures', 'list_reviews', 'review_or_edit_script'] + abilities
                                     + (['prepare_video_plan', 'submit_approved_video_plan'] if enabled else [])
                                     + (['prepare_script_plan', 'prepare_script_record_plan', 'submit_approved_script_plan',
                                         'script_catalog', 'script_plan_list', 'script_content'] if scripts else []),
                        'unavailable': ['prepare_plan', 'submit_plan'] + ([] if 'approve_plan' in abilities else ['approve_plan']) + [
                                        'shot_repair'] + (['media_preview'] if self.media.root is None else []) + ([] if scripts else ['script_content']),
                        'mutations_allowed': enabled or scripts, 'approval_authority': 'local_administrator',
                        'script_plan_available': scripts}
            if route.endswith('/status'):
                return self.scheduler.status()
            if route in ('/v1/agent/project_stats', '/v1/agent/list_failures'):
                from .project_observations import stats, failures
                after = body.get('after', '')
                if after != '':
                    identifier(after)
                report = stats(db, self.project) if route.endswith('/project_stats') else failures(db, self.project, after)
                return {'schema': 'vf-agent-project-observation-v1', 'project': self.project, **report}
            columns = 't.id,t.revision,t.state,t.input_digest,t.configuration_digest'
            if route.endswith('/tasks'):
                after = body.get('after', '')
                if after != '':
                    identifier(after)
                rows = db.execute('SELECT ' + columns + ' FROM tasks t WHERE t.project=? AND t.id>? '
                                  'AND t.revision=(SELECT MAX(v.revision) FROM tasks v '
                                  'WHERE v.project=t.project AND v.id=t.id) ORDER BY t.id LIMIT 101',
                                  (self.project, after)).fetchall()
                return {'schema': 'vf-agent-read-v1', 'project': self.project,
                        'items': [dict(r) for r in rows[:100]], 'has_more': len(rows) > 100,
                        'next_after': rows[99]['id'] if len(rows) > 100 else None}
            task = identifier(body.get('task'))
            revision = body.get('revision')
            if type(revision) is not int or revision < 1:
                raise RuntimeFault('AGENT_REVISION_REQUIRED')
            row = db.execute('SELECT ' + columns + ' FROM tasks t WHERE t.project=? AND t.id=? '
                             'ORDER BY t.revision DESC LIMIT 1', (self.project, task)).fetchone()
            if row is None:
                raise RuntimeFault('TASK_MISSING')
            if row['revision'] != revision:
                raise RuntimeFault('AGENT_TASK_VERSION_CHANGED')
            return {'schema': 'vf-agent-read-v1', 'project': self.project, 'task': dict(row)}


class AgentServer(HTTPServer):
    def __init__(self, address, api):
        if address[0] != '127.0.0.1':
            raise RuntimeFault('LOOPBACK_BIND_REQUIRED')
        self.api = api
        super().__init__(address, Handler)
        self.timeout = 0.25

    def server_bind(self):
        # The listener is fixed to numeric loopback; reverse DNS adds no
        # identity assurance and can block startup on offline Macs.
        TCPServer.server_bind(self)
        self.server_name = 'localhost'
        self.server_port = self.server_address[1]

    def handle_error(self, *_):
        pass  # Socket failures cannot dump request data or tracebacks.


class Handler(BaseHTTPRequestHandler):
    server_version = 'VideoFactoryLocalCandidate'

    def setup(self):
        super().setup()
        self.connection.settimeout(3)

    def log_message(self, *_):
        pass

    def reply(self, status, value):
        raw = canonical(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def trusted_request(self):
        # Service-to-service access only. No CORS, cross-site or DNS rebinding.
        return (self.headers.get_all('Host', []) == [f'127.0.0.1:{self.server.server_port}']
                and not self.headers.get_all('Origin', []))

    def do_GET(self):
        if not self.trusted_request():
            self.reply(403, {'error': 'LOCAL_ORIGIN_DENIED'})
        elif self.path == '/healthz':
            self.reply(200, {'status': 'up', 'scope': 'local_kernel_candidate'})
        elif self.path.startswith('/v1/agent/media/stream/'):
            try:
                stream, size = self.server.api.media.open(self.path.rsplit('/', 1)[1])
            except (RuntimeFault, OSError):
                self.reply(404, {'error': 'WORKBENCH_MEDIA_UNAVAILABLE'})
                return
            with stream:
                self.send_response(200)
                self.send_header('Content-Type', 'video/mp4')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Length', str(size))
                self.end_headers()
                shutil.copyfileobj(stream, self.wfile)
        else:
            self.reply(404, {'error': 'ROUTE_NOT_FOUND'})

    def do_POST(self):
        try:
            if not self.trusted_request():
                self.reply(403, {'error': 'LOCAL_ORIGIN_DENIED'})
                return
            lengths = self.headers.get_all('Content-Length', [])
            limit = 65536 if self.path in ('/v1/agent/script/prepare', '/v1/agent/review/prepare') else 8192
            if (self.headers.get('Transfer-Encoding') or len(lengths) != 1
                    or not lengths[0].isdigit() or not 0 < int(lengths[0]) <= limit
                    or self.headers.get_content_type() != 'application/json'):
                raise RuntimeFault('AGENT_REQUEST_INVALID')
            raw = self.rfile.read(int(lengths[0]))
            if len(raw) != int(lengths[0]):
                raise RuntimeFault('AGENT_REQUEST_INVALID')
            body = read_json(io.StringIO(raw.decode()))
            headers = self.headers.get_all('Authorization', [])
            if self.path.startswith('/v1/employee/') and self.server.api.employee_access:
                if headers and (len(headers) != 1 or not headers[0].startswith('Bearer ')):
                    raise RuntimeFault('AUTH_EMPLOYEE_REQUIRED')
                result = self.server.api.employee_access.call(self.path[len('/v1/employee/'):], headers[0][7:] if headers else None, body, self.server.api)
                self.reply(200, result)
                return
            if len(headers) != 1 or not headers[0].startswith('Bearer '):
                raise RuntimeFault('AUTH_AGENT_REQUIRED')
            principal = self.headers.get_all('X-Workbench-Principal', [])
            if len(principal) > 1:
                raise RuntimeFault('AUTH_WORKBENCH_ROLE_DENIED')
            result = self.server.api.call(self.path, headers[0][7:], body,
                                          **({'principal': principal[0]} if principal else {}))
            self.reply(200, result)
        except RuntimeFault as error:
            self.reply(401 if str(error).startswith('AUTH_') else 409, {'error': str(error)})
        except (ValueError, TypeError, UnicodeError):
            self.reply(400, {'error': 'AGENT_REQUEST_INVALID'})
        except Exception:
            self.reply(500, {'error': 'AGENT_INTERNAL_FAILURE'})
