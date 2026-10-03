"""Customer-owned project directory with explicit, request-local project scope."""
import re
from urllib.parse import urlsplit

from .feishu_bridge import meta
from .feishu_client import FeishuClient
from .review_service import ReviewService
from .runtime_store import RuntimeFault, identifier
from .workspace_http import WorkspaceHandler, WorkspaceServer


def project_manifest(projects):
    if not isinstance(projects, list) or not 1 <= len(projects) <= 20:
        raise RuntimeFault('PORTAL_PROJECT_LIMIT')
    for project in projects: identifier(project)
    if len(set(projects)) != len(projects): raise RuntimeFault('PORTAL_DUPLICATE_PROJECT')
    return sorted(projects)


class PortalDirectory:
    project = ''  # Public session/health endpoints disclose no configured projects.

    def __init__(self, store, projects, media_root, *, client_factory=FeishuClient):
        self.store = store
        self.services = {p: ReviewService(store, p, media_root, client_factory=client_factory)
                         for p in project_manifest(projects)}
        self.client_factory = client_factory
        self.profiles = {}
        tenants, apps = set(), set()
        with store.connect() as db:
            for project, service in self.services.items():
                profile = meta(db, 'setup:feishu-app:'+project)
                if not profile or not profile.get('app_id') or not profile.get('credential_ref'):
                    raise RuntimeFault('WORKSPACE_FEISHU_PROFILE_REQUIRED')
                self.profiles[project] = profile
                tenants.add(service.bridge._binding(db, project)['tenant_key'])
                apps.add(profile['app_id'])
        # Feishu open_id and user access tokens are scoped to this application.
        if len(apps) != 1 or len(tenants) != 1:
            raise RuntimeFault('PORTAL_SHARED_FEISHU_CONTEXT_REQUIRED')
        self.tenant = next(iter(tenants))
        self.app_id = next(iter(apps))

    def context(self, db, project):
        service = self.services.get(project)
        if (not service or meta(db, 'setup:feishu-app:'+project) != self.profiles[project]
                or service.bridge._binding(db, project)['tenant_key'] != self.tenant):
            raise RuntimeFault('PORTAL_PROJECT_UNAVAILABLE')
        return service

    def projects(self, user):
        identity = self.client_factory(user).identity()
        items = []
        with self.store.connect() as db:
            for project in self.services:
                try:
                    service = self.context(db, project)
                    service.member(db, identity)
                except RuntimeFault:
                    continue  # No inaccessible names, counts or configuration in response.
                setup = meta(db, 'setup:project:'+project)
                name = (setup or {}).get('configuration', {}).get('project', {}).get('name', project)
                items.append({'id': project, 'name': name})
        return identity, items

    def identity(self, user):
        identity, items = self.projects(user)
        if not items: raise RuntimeFault('FEISHU_TENANT_OR_ROLE_DENIED')
        return identity

    def select(self, user, project):
        with self.store.connect() as db:
            service = self.context(db, project)
        service.identity(user)  # Recheck live membership for every route, including commit/media.
        return service


def scoped_path(path):
    match = re.fullmatch(r'/p/([A-Za-z0-9_-]{1,96})(/(?:api|media)/[^#]+)', path)
    if not match: raise RuntimeFault('ROUTE_NOT_FOUND')
    return match.groups()


class PortalHandler(WorkspaceHandler):
    scope = 'employee_project_portal'

    def session_metadata(self):
        return {'portal': True}

    def service_for(self, session):
        return self.server.service.select(self.server.authorize(session), self.request_project)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ('/', '/healthz', '/api/session', '/app.js', '/style.css'):
            return super().do_GET()
        try:
            sid = self.gate()
            _, session = self.server.session(sid)
            if self.path == '/api/projects':
                with session['lock']:
                    _, items = self.server.service.projects(self.server.authorize(session))
                    self.reply(200, {'items': items})
                return
            project, route = scoped_path(self.path)
            if not (route.split('?')[0] == '/api/tasks' or route.startswith('/media/')):
                raise RuntimeFault('ROUTE_NOT_FOUND')
            self.request_project = project
            self.path = route
            super().do_GET()
        except (BrokenPipeError, ConnectionResetError): pass
        except Exception as error: self.failure(error)


class PortalServer(WorkspaceServer):
    def __init__(self, address, service, oauth, public_origin, **kwargs):
        if oauth.app_id != service.app_id: raise RuntimeFault('PORTAL_SHARED_FEISHU_CONTEXT_REQUIRED')
        super().__init__(address, service, oauth, public_origin, handler=PortalHandler, **kwargs)

    def operation(self, session, action, body):
        if action in ('/api/login', '/api/logout', '/api/poll'):
            if action != '/api/poll': session.pop('projects', None)
            return super().operation(session, action, body)
        project, route = scoped_path(action)
        if route not in ('/api/task', '/api/import/prepare', '/api/review/prepare',
                         '/api/script/prepare', '/api/video/prepare', '/api/commit'):
            raise RuntimeFault('ROUTE_NOT_FOUND')
        user = self.authorize(session)
        service = self.service.select(user, project)
        state = session.setdefault('projects', {}).setdefault(project, {'pending': None})
        # Share identity/expiry only; plans are retained under the explicit project key.
        scoped = dict(session, pending=state['pending'])
        try:
            result = super().operation(scoped, route, body, service=service)
        finally:
            state['pending'] = scoped['pending']
        if result.get('media_url'): result['media_url'] = '/p/'+project+result['media_url']
        return result
