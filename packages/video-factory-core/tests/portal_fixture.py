"""Two synthetic projects, one app and one cookie; no real tenant data."""
import copy
import hashlib
import threading

from review_fixture import Fixture, BINDING
from video_factory.feishu_bridge import save
from video_factory.portal_http import PortalDirectory, PortalServer


class PortalFixture:
    def __init__(self, root, clock=None):
        self.f = Fixture(root, project='alpha', clock=clock)
        self.store, self.admin = self.f.store, self.f.admin
        self.store.put_project(self.admin, 'beta', {'video_route':'deferred','credential_ref':'secret:fixture','billing_owner':'fixture'})
        self.f.bridge.bind(self.admin, 'beta', copy.deepcopy(BINDING), self.f.user_token)
        self.f.import_task()
        original = dict(self.f.fields)
        self.f.fields['script'] = 'Other project script'
        plan = self.f.bridge.prepare_import(self.f.user_token, 'beta', 'recFixture', 0)
        self.f.bridge.import_task(self.f.user_token, 'beta', 'recFixture', plan['plan_sha256'], 0)
        self.f.fields = original
        with self.store.connect() as db:
            for project in ('alpha','beta'):
                save(db, 'setup:feishu-app:'+project, {'app_id':'cli_fixture','credential_ref':'secret:fixture'})
        self.directory = PortalDirectory(self.store, ['alpha','beta'], self.f.media, client_factory=self.f.bridge.client_factory)
        self.server = PortalServer(('127.0.0.1',0), self.directory, self.f.oauth, 'https://portal.example', **({'clock':clock} if clock else {}))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()

    def revoke(self, project):
        binding=copy.deepcopy(BINDING);binding['submitters']=binding['reviewers']=['ou_another']
        with self.store.connect() as db: save(db,'feishu:binding:'+project,binding)

    def media(self, project, data):
        # Technical authorization fixture; browser uses real FFmpeg bytes separately.
        path=self.f.media/(project+'.mp4');path.write_bytes(data);path.chmod(0o600)
        digest=hashlib.sha256(data).hexdigest()
        with self.store.connect() as db:
            from video_factory.runtime_store import canonical
            db.execute('UPDATE tasks SET artifact=?,state=? WHERE project=? AND id=? AND revision=1',
                       (canonical({'sha256':digest,'location':str(path),'verification':'full_decode_passed'}),'awaiting_video_review',project,'task_one'))
        return digest

    def close(self):
        self.server.shutdown();self.server.server_close();self.thread.join(3);self.f.close()
