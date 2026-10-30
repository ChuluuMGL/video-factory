"""Scoped Agent metadata and opt-in plans; no owner approval or provider routes."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import io

from .onboarding import read_json
from .runtime_store import RuntimeFault, canonical, identifier
from .agent_plans import VideoPlans, authorize
from .agent_script_plans import ScriptPlans


class AgentAPI:
    def __init__(self, store, project, scheduler, *, plans_enabled=False, script_plans=None):
        self.store, self.project, self.scheduler = store, identifier(project), scheduler
        self.plans_enabled = plans_enabled
        self.script_plans = script_plans

    def call(self, route, token, body):
        if not isinstance(token, str) or not 20 <= len(token) <= 256:
            raise RuntimeFault('AUTH_AGENT_REQUIRED')
        if not isinstance(body, dict) or body.get('project') != self.project:
            raise RuntimeFault('AUTH_AGENT_PROJECT_DENIED')
        fields = {
            '/v1/agent/capabilities': {'project'},
            '/v1/agent/status': {'project'},
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
            value, _ = authorize(db, token, self.project)
            if route.endswith('/capabilities'):
                enabled = self.plans_enabled and value['scope'] == 'agent_plan'
                scripts = self.script_plans is not None and value['scope'] == 'agent_plan'
                return {'schema': 'vf-agent-read-v1', 'project': self.project,
                        'available': ['task_metadata', 'task_list', 'runtime_status', 'video_plan_receipt', 'script_plan_receipt']
                                     + (['prepare_video_plan', 'submit_approved_video_plan'] if enabled else [])
                                     + (['prepare_script_plan', 'prepare_script_record_plan', 'submit_approved_script_plan',
                                         'script_catalog', 'script_plan_list', 'script_content'] if scripts else []),
                        'unavailable': ['prepare_plan', 'approve_plan', 'submit_plan',
                                        'media_preview', 'shot_repair'] + ([] if scripts else ['script_content']),
                        'mutations_allowed': enabled or scripts, 'approval_authority': 'local_administrator',
                        'script_plan_available': scripts}
            if route.endswith('/status'):
                return self.scheduler.status()
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
        else:
            self.reply(404, {'error': 'ROUTE_NOT_FOUND'})

    def do_POST(self):
        try:
            if not self.trusted_request():
                self.reply(403, {'error': 'LOCAL_ORIGIN_DENIED'})
                return
            lengths = self.headers.get_all('Content-Length', [])
            limit = 65536 if self.path == '/v1/agent/script/prepare' else 8192
            if (self.headers.get('Transfer-Encoding') or len(lengths) != 1
                    or not lengths[0].isdigit() or not 0 < int(lengths[0]) <= limit
                    or self.headers.get_content_type() != 'application/json'):
                raise RuntimeFault('AGENT_REQUEST_INVALID')
            raw = self.rfile.read(int(lengths[0]))
            if len(raw) != int(lengths[0]):
                raise RuntimeFault('AGENT_REQUEST_INVALID')
            body = read_json(io.StringIO(raw.decode()))
            headers = self.headers.get_all('Authorization', [])
            if len(headers) != 1 or not headers[0].startswith('Bearer '):
                raise RuntimeFault('AUTH_AGENT_REQUIRED')
            result = self.server.api.call(self.path, headers[0][7:], body)
            self.reply(200, result)
        except RuntimeFault as error:
            self.reply(401 if str(error).startswith('AUTH_') else 409, {'error': str(error)})
        except (ValueError, TypeError, UnicodeError):
            self.reply(400, {'error': 'AGENT_REQUEST_INVALID'})
        except Exception:
            self.reply(500, {'error': 'AGENT_INTERNAL_FAILURE'})
