"""Short-lived loopback media capability. The route never takes a file path."""
import base64
import hashlib
import hmac
import json
import re
import secrets
import time

from .review_service import open_media
from .runtime_store import RuntimeFault, canonical, identifier
from .workbench_views import SHA, iso, version


class WorkbenchMedia:
    def __init__(self, store, project, root, *, clock=time.time):
        self.store, self.project, self.root, self.clock = store, project, root, clock
        self.secret = secrets.token_bytes(32)

    def issue(self, row):
        if self.root is None or not row['artifact'] or row['state'] not in (
                'awaiting_storyboard_review', 'awaiting_video_review'):
            return {'media_preview_token': None, 'expires_at': None}
        artifact = json.loads(row['artifact'])
        digest = artifact.get('sha256', '')
        if not isinstance(digest, str) or not SHA.fullmatch(digest) or artifact.get('verification') != 'full_decode_passed':
            return {'media_preview_token': None, 'expires_at': None}
        expires = int(self.clock()) + 300
        claim = [self.project, row['id'], row['revision'], version(row), digest, expires]
        data = base64.urlsafe_b64encode(canonical(claim).encode()).decode().rstrip('=')
        signature = hmac.new(self.secret, data.encode(), hashlib.sha256).hexdigest()
        return {'media_preview_token': data + '.' + signature, 'expires_at': iso(expires)}

    def open(self, token):
        if self.root is None or not re.fullmatch(r'[A-Za-z0-9_-]{1,768}\.[a-f0-9]{64}', token):
            raise RuntimeFault('WORKBENCH_MEDIA_TOKEN_INVALID')
        data, signature = token.split('.')
        if not hmac.compare_digest(signature, hmac.new(self.secret, data.encode(), hashlib.sha256).hexdigest()):
            raise RuntimeFault('WORKBENCH_MEDIA_TOKEN_INVALID')
        project, task, revision, expected, digest, expires = json.loads(
            base64.urlsafe_b64decode(data + '=' * (-len(data) % 4)))
        if project != self.project or expires <= self.clock():
            raise RuntimeFault('WORKBENCH_MEDIA_TOKEN_EXPIRED')
        with self.store.connect() as db:
            row = self.store._current(db, project, identifier(task), revision)
            if version(row) != expected or not row['artifact']:
                raise RuntimeFault('WORKBENCH_MEDIA_VERSION_CHANGED')
            artifact = json.loads(row['artifact'])
        return open_media(self.root, artifact, digest)
