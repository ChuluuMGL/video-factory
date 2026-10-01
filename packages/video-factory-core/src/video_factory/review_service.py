"""Project-scoped employee reads and explicit review plans. No model executor."""
import hashlib
import json
import os
from pathlib import Path
import stat

from .script_jobs import GeneratedReviewBridge
from .feishu_bridge import FeishuBridge, meta
from .feishu_client import FeishuClient
from .runtime_store import RuntimeFault, identifier, private_directory


class ReviewService:
    def __init__(self, store, project, media_root, *, client_factory=FeishuClient):
        identifier(project)
        self.store, self.project = store, project
        self.media_root = private_directory(media_root)
        self.bridge = GeneratedReviewBridge(store, client_factory=client_factory)
        with store.connect() as db:
            self.bridge._binding(db, project)

    def identity(self, user):
        identity = self.bridge.client_factory(user).identity()
        with self.store.connect() as db:
            self.member(db, identity)
        return identity

    def member(self, db, identity):
        binding = self.bridge._binding(db, self.project)
        if (identity['tenant_key'] != binding['tenant_key']
                or identity['open_id'] not in set(binding['submitters']) | set(binding['reviewers'])):
            raise RuntimeFault('FEISHU_TENANT_OR_ROLE_DENIED')
        return binding

    def tasks(self, user, after=''):
        if after: identifier(after)
        identity = self.identity(user)
        with self.store.connect() as db:
            binding = self.member(db, identity)
            rows = db.execute('''SELECT t.id,t.revision,t.state FROM tasks t WHERE project=? AND id>?
                AND revision=(SELECT MAX(v.revision) FROM tasks v WHERE v.project=t.project AND v.id=t.id)
                ORDER BY id LIMIT 101''', (self.project, after)).fetchall()
            setup = meta(db, 'setup:project:'+self.project)
            configured = meta(db, 'script:profile:'+self.project) is not None
        return {'can_create_script': configured and identity['open_id'] in binding['submitters'],
                'skus': setup['configuration']['project']['products'] if setup else [], 'items': [dict(row) for row in rows[:100]], 'has_more': len(rows) > 100,
                'next_after': rows[99]['id'] if len(rows) > 100 else None, 'project': self.project,
                'can_import': identity['open_id'] in binding['submitters']}

    def task(self, user, task, revision):
        identifier(task)
        identity = self.identity(user)
        with self.store.connect() as db:
            binding = self.member(db, identity)
            row = self.store._current(db, self.project, task, revision)
            pattern = lambda key, value: '%"'+key+'":"'+value.replace('_', '\\_')+'"%'
            receipts = db.execute("SELECT receipt FROM events WHERE receipt LIKE ? ESCAPE '\\' AND receipt LIKE ? ESCAPE '\\' LIMIT 501",
                                  (pattern('project', self.project), pattern('task', task))).fetchall()
            history = [json.loads(item[0]) for item in receipts[:500]]
            artifact = json.loads(row['artifact']) if row['artifact'] else None
            video_profile = meta(db, 'video:profile:'+self.project)
            result = {'task': task, 'revision': revision, 'state': row['state'], 'input': json.loads(row['input']),
                      'history': sorted(history, key=lambda item: item['revision']), 'history_truncated': len(receipts)>500,
                      'artifact_sha256': artifact['sha256'] if artifact else None,
                      'can_import': identity['open_id'] in binding['submitters'],
                      'can_generate_video': row['state']=='ready' and bool(video_profile) and json.loads(row['input'])['sku_id'] in video_profile['assets'] and identity['open_id'] in binding['submitters'],
                      'review_stages': [stage for stage in ('script', 'video')
                                        if identity['open_id'] in binding.get(stage+'_reviewers', binding['reviewers'])]}
        if artifact:
            result['media_url'] = f'/media/{task}/{revision}/{artifact["sha256"]}.mp4'
        return result

    def media(self, user, task, revision, expected_hash):
        identifier(task)
        identity = self.identity(user)
        with self.store.connect() as db:
            self.member(db, identity)
            row = self.store._current(db, self.project, task, revision)
            artifact = json.loads(row['artifact']) if row['artifact'] else None
            if not artifact or artifact['sha256'] != expected_hash:
                raise RuntimeFault('REVIEW_MEDIA_NOT_BOUND')
        path = Path(artifact['location'])
        if not path.is_absolute() or not path.is_relative_to(self.media_root) or path.resolve() != path:
            raise RuntimeFault('REVIEW_MEDIA_PATH_INVALID')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        stream = os.fdopen(fd, 'rb')
        try:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                    or info.st_mode & 0o077 or not 0 < info.st_size <= 128*1024*1024):
                raise RuntimeFault('REVIEW_MEDIA_FILE_INVALID')
            digest = hashlib.sha256()
            while chunk := stream.read(1024*1024): digest.update(chunk)
            if digest.hexdigest() != expected_hash:
                raise RuntimeFault('REVIEW_MEDIA_HASH_CHANGED')
            stream.seek(0)
            return stream, info.st_size
        except BaseException:
            stream.close()
            raise
