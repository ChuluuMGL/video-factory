import json
import os
import unittest
from unittest.mock import patch

import test_workspace_tls as fixtures
from video_factory import workspace, workspace_tls
from video_factory.runtime_store import RuntimeFault
from video_factory.stack import write_json


@unittest.skipUnless(os.getuid()==0,'Cloud root deployment fixture')
class PortalDeploymentTests(unittest.TestCase):
    setUpRoot=fixtures.WorkspaceTLSTests.setUpRoot
    prepareStack=fixtures.WorkspaceTLSTests.prepareStack
    certificate=fixtures.WorkspaceTLSTests.certificate
    setUp=fixtures.WorkspaceTLSTests.setUp
    tearDown=fixtures.WorkspaceTLSTests.tearDown

    def portal(self):
        value=workspace.plan(self.stack,self.project,self.value['origin'],self.oldcert,self.oldkey,[self.project,'second_project'])
        write_json(self.root/'workspace.json',value)
        write_json(self.root/'compose.json',workspace.document(self.stack,value))
        return value

    def test_manifest_drives_distinct_executors_and_ready_count(self):
        value=self.portal();doc=workspace.document(self.stack,value)
        executors=[v for v in doc['services'].values() if v['entrypoint'][-1]=='video_factory.dispatch']
        self.assertEqual(len(executors),2)
        self.assertEqual({tuple(v['command']) for v in executors},{('--project',self.project),('--project','second_project')})
        self.assertEqual(len({v['networks']['ledger']['aliases'][0] for v in executors}),2)
        self.assertIn('--include-project',doc['services']['workspace']['command'])
        rows=[{'Service':name,'State':'running','Health':'healthy'} for name in doc['services']]
        with patch.object(workspace,'compose',return_value=json.dumps(rows).encode()):
            self.assertEqual(workspace.status(self.stack,self.project)['status'],'running')
        with patch.object(workspace,'compose',return_value=json.dumps(rows[:-1]).encode()):
            self.assertEqual(workspace.status(self.stack,self.project)['status'],'incomplete')

    def test_tls_rotation_retains_portal_and_rollback_retains_manifest(self):
        value=self.portal();plan=workspace_tls.plan(self.stack,self.project,self.cert,self.key)
        with patch.object(workspace,'compose'):
            workspace_tls.apply(self.stack,plan,self.cert,self.key,readback=lambda *_:True)
        updated=json.loads((self.root/'workspace.json').read_text())
        self.assertEqual(updated['projects'],value['projects'])
        self.assertEqual(workspace.document(self.stack,updated)['services']['workspace']['command'],workspace.document(self.stack,value)['services']['workspace']['command'])

    def test_running_companion_collision_refused_stopped_migration_allowed(self):
        other=workspace.directory(self.stack,'second_project');other.mkdir(mode=0o700)
        write_json(other/'workspace.json',dict(self.value,project='second_project',origin='https://other.example:9444'))
        value=self.portal()
        with patch.object(workspace,'compose',return_value=b'{"Service":"executor","State":"running"}'):
            with self.assertRaisesRegex(RuntimeFault,'STOP_CONFLICTING'):workspace.check_companions(self.stack,value)
        with patch.object(workspace,'compose',return_value=b''):
            workspace.check_companions(self.stack,value)
        # Same port is a conflict even when the project is not in the manifest.
        write_json(other/'workspace.json',dict(self.value,project='unrelated'))
        with patch.object(workspace,'compose',return_value=b'{"Service":"edge","State":"running"}'):
            with self.assertRaisesRegex(RuntimeFault,'STOP_CONFLICTING'):workspace.check_companions(self.stack,value)

    def test_invalid_project_configuration_does_not_stop_existing_workspace(self):
        value=self.portal()
        with patch.object(self.stack,'status',return_value={'infrastructure_ready':True}), patch.object(self.stack,'compose',side_effect=RuntimeFault('FIXTURE_PREFLIGHT_REFUSED')), patch.object(workspace,'compose') as companion:
            with self.assertRaisesRegex(RuntimeFault,'PREFLIGHT_REFUSED'):
                workspace.apply(self.stack,value,self.oldcert,self.oldkey)
            companion.assert_not_called()
