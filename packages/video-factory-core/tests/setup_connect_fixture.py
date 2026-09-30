"""Cloud-only bootstrap fixture with no binding for setup_brand."""
import json
from pathlib import Path
import threading

from review_fixture import Fixture
from video_factory.onboarding import new_session, apply_answers
from video_factory.setup_project import import_project
from video_factory.setup_feishu import ConnectionSession, SetupFeishu
from video_factory.feishu_provision import ProvisionClient
from video_factory.setup_connect import ConnectionService, ConnectionServer


class SetupFixture:
    def __init__(self, root, store=None, admin=None, clock=None, create=False, user_token='synthetic-user-token'):
        self.root = Path(root)
        self.project = 'setup_new_brand' if create else 'setup_brand'
        self.wire = Fixture(root, store, admin, clock, project='bootstrap_new_wire' if create else 'bootstrap_wire', create=create, user_token=user_token)
        self.store, self.admin = self.wire.store, self.wire.admin
        answers = json.loads((Path(__file__).resolve().parents[1]/'examples/setup/answers.json').read_text())
        answers.update({'deployment.id': 'fixture', 'deployment.feishu_tenant': 'fixture_tenant',
                        'project.id': self.project, 'project.base_mode': 'create' if create else 'bind',
                        'project.base_target': '安装演练' if create else 'bascnFixture',
                        'project.script_reviewer': 'feishu:ou_employee', 'project.video_reviewer': 'feishu:ou_video'})
        # The PostgreSQL job already contains another project for this same
        # synthetic customer. Preserve its customer binding, just as Setup's
        # second-project flow does; never relax the product's conflict fence.
        with self.store.connect() as db:
            existing = db.execute("SELECT value FROM meta WHERE key='setup:binding'").fetchone()
        if existing:
            for group, values in json.loads(existing[0]).items():
                for name, value in values.items(): answers[group+'.'+name] = value
        assert answers['deployment.feishu_tenant'] == 'fixture_tenant'
        self.setup = apply_answers(new_session(), answers, 0)[0]
        import_project(self.store, self.admin, self.setup)
        self.session = ConnectionSession(self.root/'setup-connection.json'); self.session.start(self.setup)
        connection_answers = {'table_id': 'tblFixture', 'task': 'fldTask', 'sku_id': 'fldSkuId',
                             'script': 'fldScript', 'source_revision': 'fldSource', 'submitters': ['ou_employee']}
        if create: connection_answers = {'workspace_kind': 'test', 'folder_token': 'fldcnDestination', 'submitters': ['ou_employee']}
        self.session.answer(connection_answers, 0)
        class Creator(ProvisionClient): pass
        Creator.origin = self.wire.oauth.accounts_origin
        self.service = SetupFeishu(self.store, client_factory=self.wire.service.bridge.client_factory, provision_client_factory=Creator)
        self.connection = ConnectionService(self.service, self.admin, self.session, self.project)
        self.server = ConnectionServer(('127.0.0.1', 0), self.connection, self.wire.oauth, **({'clock': clock} if clock else {}))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(3); self.wire.close()
