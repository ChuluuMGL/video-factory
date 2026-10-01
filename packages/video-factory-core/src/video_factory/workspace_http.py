"""Persistent employee service behind a generated HTTPS gateway, no admin routes."""
import argparse
import json
import re
import time
from urllib.parse import urlsplit

from .review_http import ReviewServer, ReviewHandler
from .review_service import ReviewService
from .runtime_store import RuntimeFault
from .runtime_cli import selected_store, secret_input
from .feishu_bridge import meta
from .feishu_oauth import DeviceOAuth
from pathlib import Path


def origin(value):
    p = urlsplit(value)
    if (p.scheme != 'https' or p.username or p.password or p.path or p.query or p.fragment
            or not p.hostname or not re.fullmatch(r'[a-z0-9.-]+', p.hostname)
            or not 1 <= (443 if p.port is None else p.port) <= 65535 or value != 'https://'+p.netloc):
        raise RuntimeFault('WORKSPACE_HTTPS_ORIGIN_REQUIRED')
    return value


class WorkspaceHandler(ReviewHandler):
    scope = 'employee_workspace'
    cookie_name = '__Host-vf_workspace'

    def headers_out(self, status, mime, length, extra=()):
        super().headers_out(status, mime, length, (*extra, ('Strict-Transport-Security', 'max-age=31536000')))


class WorkspaceServer(ReviewServer):
    def __init__(self, address, service, oauth, public_origin, *, clock=time.monotonic):
        origin(public_origin)
        super().__init__(address, service, oauth, container_network=True, clock=clock, handler=WorkspaceHandler)
        self.origin = public_origin
        self.persistent = True
        self.cookie_seconds = 28800
        self.deadline = float('inf')
        self.login_reset = clock()+60

    def session(self, sid, create=False):
        with self.session_lock:
            now = self.clock()
            for key, value in list(self.sessions.items()):
                if now >= value.get('absolute_expiry', now+1) or now >= value.get('idle_expiry', now+1):
                    self.sessions.pop(key)
            if now >= self.login_reset:
                self.login_count = 0; self.login_reset = now+60
        key, value = super().session(sid, create)
        with self.session_lock:
            now = self.clock()
            value.setdefault('absolute_expiry', now+self.cookie_seconds)
            # Unauthenticated sessions cannot fill capacity for an entire day.
            value['idle_expiry'] = min(value['absolute_expiry'], now+(1800 if value['token'] else 360))
        return key, value

    def authorize(self, session):
        if self.clock() >= session['absolute_expiry']:
            session.update(token=None, pending=None, device=None)
            raise RuntimeFault('AUTH_REQUIRED')
        return super().authorize(session)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--project', required=True)
    p.add_argument('--origin', required=True)
    args = p.parse_args()
    store = selected_store()('/state')
    service = ReviewService(store, args.project, '/media')
    with store.connect() as db:
        profile = meta(db, 'setup:feishu-app:'+args.project)
    if not profile: raise RuntimeFault('WORKSPACE_FEISHU_PROFILE_REQUIRED')
    secret = store.resolve_secret(profile['credential_ref'][7:], secret_input(Path('/run/secrets/runtime_master'), ''))
    oauth = DeviceOAuth(profile['app_id'], secret)
    with WorkspaceServer(('0.0.0.0', 8790), service, oauth, args.origin) as server:
        print(json.dumps({'status':'ready','scope':'employee_workspace','project':args.project}), flush=True)
        server.serve_forever()


if __name__ == '__main__':
    try: main()
    except Exception:
        print('{"error":"WORKSPACE_START_FAILED"}', flush=True)
        raise SystemExit(2)
