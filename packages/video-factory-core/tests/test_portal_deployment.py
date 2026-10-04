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

    def test_invalid_single_project_does_not_stop_existing_workspace(self):
        with patch.object(self.stack,'status',return_value={'infrastructure_ready':True}), patch.object(self.stack,'compose',side_effect=RuntimeFault('FIXTURE_PREFLIGHT_REFUSED')), patch.object(workspace,'compose') as companion:
            with self.assertRaisesRegex(RuntimeFault,'PREFLIGHT_REFUSED'):
                workspace.apply(self.stack,self.value,self.oldcert,self.oldkey)
            companion.assert_not_called()

    def healthy_rows(self, value=None):
        return json.dumps([{'Service':name,'State':'running','Health':'healthy'}
                          for name in workspace.document(self.stack,value or self.value)['services']]).encode()

    def test_failed_replacement_restores_old_config_certificate_and_running_entry(self):
        value=workspace.plan(self.stack,self.project,self.value['origin'],self.cert,self.key,[self.project,'second_project'])
        starts=[]
        def execute(stack,project,*args,**kwargs):
            if args[0]=='up':
                starts.append(json.loads((self.root/'workspace.json').read_text()))
                if len(starts)==1: raise RuntimeFault('FIXTURE_CANDIDATE_FAILED')
            return self.healthy_rows()
        with patch.object(self.stack,'status',return_value={'infrastructure_ready':True}), patch.object(self.stack,'compose'), patch.object(workspace,'compose',side_effect=execute):
            with self.assertRaisesRegex(RuntimeFault,'DEPLOYMENT_ROLLED_BACK'):
                workspace.apply(self.stack,value,self.cert,self.key)
        self.assertEqual(starts,[value,self.value])
        self.assertEqual(json.loads((self.root/'workspace.json').read_text()),self.value)
        self.assertEqual((self.root/'tls/key.pem').read_bytes(),self.oldkey.read_bytes())
        self.assertEqual(json.loads((self.root/'deployment.json').read_text())['status'],'rolled_back')

    def test_failed_rollback_is_fenced_and_can_be_recovered(self):
        from video_factory.workspace_deploy import recover
        def execute(stack,project,*args,**kwargs):
            if args[0]=='up': raise RuntimeFault('FIXTURE_START_FAILED')
            return self.healthy_rows()
        with patch.object(self.stack,'status',return_value={'infrastructure_ready':True}), patch.object(self.stack,'compose'), patch.object(workspace,'compose',side_effect=execute):
            with self.assertRaisesRegex(RuntimeFault,'NEEDS_ATTENTION'):
                workspace.apply(self.stack,self.value,self.oldcert,self.oldkey)
        self.assertTrue(workspace.status(self.stack,self.project)['recovery_required'])
        with self.assertRaisesRegex(RuntimeFault,'RECOVERY_REQUIRED'):
            workspace.apply(self.stack,self.value,self.oldcert,self.oldkey)
        with self.assertRaisesRegex(RuntimeFault,'RECOVERY_REQUIRED'):
            workspace_tls.plan(self.stack,self.project,self.cert,self.key)
        with patch.object(workspace,'compose',return_value=self.healthy_rows()):
            self.assertEqual(recover(self.stack,self.project)['previous_status'],'running')

    def test_interrupted_replacement_retains_private_snapshot_and_rejects_tampering(self):
        from video_factory.workspace_deploy import recover
        with patch.object(self.stack,'status',return_value={'infrastructure_ready':True}), patch.object(self.stack,'compose'), patch.object(workspace,'compose',side_effect=lambda *a,**k:self.healthy_rows() if a[2]=='ps' else (_ for _ in ()).throw(RuntimeFault('FIXTURE_DOWN_FAILED'))):
            with self.assertRaisesRegex(RuntimeFault,'NEEDS_ATTENTION'):
                workspace.apply(self.stack,self.value,self.oldcert,self.oldkey)
        receipt=json.loads((self.root/'deployment.json').read_text())
        (self.root/receipt['backup']/'0').write_text('{}')
        with patch.object(workspace,'compose') as command:
            with self.assertRaisesRegex(RuntimeFault,'FILES_CHANGED'):recover(self.stack,self.project)
            command.assert_not_called()
        self.assertNotIn('PRIVATE KEY',json.dumps(receipt))

    def test_stopped_previous_entry_is_not_started_by_rollback(self):
        def execute(stack,project,*args,**kwargs):
            if args[0]=='up': raise RuntimeFault('FIXTURE_START_FAILED')
            return b'[]'
        with patch.object(self.stack,'status',return_value={'infrastructure_ready':True}), patch.object(self.stack,'compose'), patch.object(workspace,'compose',side_effect=execute) as command:
            with self.assertRaisesRegex(RuntimeFault,'DEPLOYMENT_ROLLED_BACK'):
                workspace.apply(self.stack,self.value,self.oldcert,self.oldkey)
        self.assertEqual(sum(c.args[2]=='up' for c in command.call_args_list),1)

    def test_new_failed_entry_returns_to_unconfigured(self):
        value=workspace.plan(self.stack,'new_project',self.value['origin'],self.cert,self.key)
        def execute(stack,project,*args,**kwargs):
            if args[0]=='up': raise RuntimeFault('FIXTURE_START_FAILED')
            return b'[]'
        with patch.object(self.stack,'status',return_value={'infrastructure_ready':True}), patch.object(self.stack,'compose'), patch.object(workspace,'compose',side_effect=execute):
            with self.assertRaisesRegex(RuntimeFault,'DEPLOYMENT_ROLLED_BACK'):
                workspace.apply(self.stack,value,self.cert,self.key)
        self.assertEqual(workspace.status(self.stack,'new_project')['status'],'not_configured')

    def test_recovery_refuses_a_new_conflicting_entry_before_restarting_old(self):
        from video_factory.workspace_deploy import recover
        def fail_start(stack,project,*args,**kwargs):
            if args[0]=='up': raise RuntimeFault('FIXTURE_START_FAILED')
            return self.healthy_rows()
        with patch.object(self.stack,'status',return_value={'infrastructure_ready':True}), patch.object(self.stack,'compose'), patch.object(workspace,'compose',side_effect=fail_start):
            with self.assertRaisesRegex(RuntimeFault,'NEEDS_ATTENTION'):
                workspace.apply(self.stack,self.value,self.oldcert,self.oldkey)
        with patch.object(workspace,'compose',return_value=self.healthy_rows()) as command, patch.object(workspace,'check_companions',side_effect=RuntimeFault('PORTAL_STOP_CONFLICTING_WORKSPACE_FIRST')):
            with self.assertRaisesRegex(RuntimeFault,'CONFLICTING'):recover(self.stack,self.project)
            self.assertFalse(any(c.args[2]=='up' for c in command.call_args_list))
        self.assertTrue(workspace.status(self.stack,self.project)['recovery_required'])


    def test_secondary_project_resolves_live_portal_despite_stopped_old_entry(self):
        self.portal()
        other=workspace.directory(self.stack,'second_project');other.mkdir(mode=0o700)
        write_json(other/'workspace.json',dict(self.value,project='second_project',origin='https://old.example:9444'))
        def execute(stack,project,*args,**kwargs):
            return b'[]' if project=='second_project' else self.healthy_rows(self.portal())
        with patch.object(workspace,'compose',side_effect=execute):
            state=workspace.project_status(self.stack,'second_project')
        self.assertEqual(state['entry_project'],self.project)
        self.assertEqual(state['url'],self.value['origin']+'/p/second_project/')

    def test_secondary_project_setup_reaches_schedule_and_returns_scoped_url(self):
        from types import SimpleNamespace
        from video_factory import production_setup as setup
        self.portal();session={'configuration':{'project':{'id':'second_project'}}}
        answer=iter([False,False,True,False])
        with patch.object(setup,'Stack',return_value=self.stack), patch.object(setup,'SessionStore') as sessions, patch.object(setup,'rpc',return_value={'token':'fixture'}), patch.object(setup,'choice',side_effect=lambda *a:next(answer)), patch.object(setup,'schedule',return_value={'status':'imported_disabled','expires_at':123}) as schedule, patch.object(self.stack,'status',return_value={'infrastructure_ready':True}), patch.object(workspace,'compose',return_value=self.healthy_rows(self.portal())), patch('video_factory.maintenance.alerts',return_value=[]):
            sessions.return_value.read.return_value=session
            result=setup.welcome(SimpleNamespace(stack_root=self.stack.root,session=self.parent/'session'),hidden=lambda _: 'fixture',write=lambda _:None)
        self.assertEqual(result['workspace_url'],self.value['origin']+'/p/second_project/')
        self.assertEqual(schedule.call_args.args[1],session)

    def test_ambiguous_or_recovering_portal_does_not_configure_schedule(self):
        self.portal();other=workspace.directory(self.stack,'second_project');other.mkdir(mode=0o700)
        write_json(other/'workspace.json',dict(self.value,project='second_project'))
        with patch.object(workspace,'status',return_value={'status':'running','url':self.value['origin']}):
            with self.assertRaisesRegex(RuntimeFault,'AMBIGUOUS'):workspace.project_status(self.stack,'second_project')
        with patch.object(workspace,'status',return_value={'status':'needs_attention','recovery_required':True}):
            with self.assertRaisesRegex(RuntimeFault,'RECOVERY_REQUIRED'):workspace.project_status(self.stack,'second_project')

    def test_upgrade_entry_resume_keeps_stopped_entries_stopped_and_checks_https(self):
        self.portal();other=workspace.directory(self.stack,'stopped_project');other.mkdir(mode=0o700)
        write_json(other/'workspace.json',dict(self.value,project='stopped_project',origin='https://other.example:9445'))
        def state(stack,project):
            return {'status':'running' if project==self.project else 'incomplete','components':[{'State':'running'}] if project==self.project else []}
        with patch.object(workspace,'status',side_effect=state), patch.object(workspace,'compose') as command, patch.object(workspace,'check_companions'), patch.object(workspace_tls,'status',return_value={}), patch.object(workspace_tls,'served_leaf',return_value=True) as https:
            selected=workspace.running_entries(self.stack)
            self.assertEqual(workspace.resume_entries(self.stack,selected),[self.project])
            self.assertTrue(all(c.args[1]==self.project for c in command.call_args_list))
            https.assert_called_once()
        with patch.object(workspace,'compose'), patch.object(workspace,'check_companions'), patch.object(workspace,'status',side_effect=state), patch.object(workspace_tls,'status',return_value={}), patch.object(workspace_tls,'served_leaf',return_value=False):
            with self.assertRaisesRegex(RuntimeFault,'RESUME_UNHEALTHY'):workspace.resume_entries(self.stack,selected)
