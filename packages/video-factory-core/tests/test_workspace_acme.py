import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch, Mock

from video_factory import workspace_acme as acme, workspace, workspace_tls as tls
from video_factory.runtime_store import RuntimeFault
from video_factory.stack import write_json
import test_workspace_tls as fixtures


@unittest.skipUnless(os.getuid()==0,'Cloud root fixture')
class ACMETests(unittest.TestCase):
    setUpRoot=fixtures.WorkspaceTLSTests.setUpRoot
    prepareStack=fixtures.WorkspaceTLSTests.prepareStack
    certificate=fixtures.WorkspaceTLSTests.certificate
    tearDown=fixtures.WorkspaceTLSTests.tearDown

    def setUp(self):
        fixtures.WorkspaceTLSTests.setUp(self)
        self.client=patch.object(acme,'client_digest',return_value='a'*64);self.client.start();self.addCleanup(self.client.stop)
        self.staging=acme.plan(self.stack,self.project,'https://review.example:9443','operator@example.com',['8.8.8.8'],'staging')
        self.production={**self.staging,'environment':'production'}
        self.calls=[]

    def provider(self,argv,**kwargs):
        self.calls.append(argv)
        config=Path(argv[argv.index('--config-dir')+1]);archive=config/'archive/workspace';live=config/'live/workspace'
        archive.mkdir(parents=True);live.mkdir(parents=True)
        for name,source in [('fullchain',self.cert),('privkey',self.key)]:
            target=archive/(name+'1.pem');target.write_bytes(source.read_bytes());target.chmod(0o600)
            (live/(name+'.pem')).symlink_to('../../archive/workspace/'+target.name)
        accounts=config/'accounts/fixture/directory/account';accounts.mkdir(parents=True)
        (accounts/'meta.json').write_text('{"fixture":true}')
        return b''

    def issue(self,value):
        return acme.issue(self.stack,value,accept_terms=True,runner=self.provider,network_check=lambda _:None)

    def test_staging_then_production_use_separate_accounts_and_private_regular_output(self):
        with self.assertRaisesRegex(RuntimeFault,'STAGING_REQUIRED'):self.issue(self.production)
        self.assertEqual(self.issue(self.staging)['environment'],'staging')
        self.assertEqual(self.issue(self.production)['environment'],'production')
        self.assertIn('--standalone',self.calls[0]);self.assertNotIn('--nginx',self.calls[0])
        self.assertNotEqual(self.calls[0][self.calls[0].index('--server')+1],self.calls[1][self.calls[1].index('--server')+1])
        root=acme.folder(self.stack,self.project)
        self.assertFalse(any(p.is_symlink() for p in root.rglob('*')))
        self.assertEqual((root/'production/key.pem').stat().st_mode&0o777,0o600)
        self.assertNotIn('PRIVATE KEY',json.dumps(acme.status(self.stack,self.project)))

    def test_terms_changed_context_and_dns_are_refused_before_order(self):
        with self.assertRaisesRegex(RuntimeFault,'TERMS'):acme.issue(self.stack,self.staging)
        value={**self.staging,'stack_instance':'changed'}
        with self.assertRaisesRegex(RuntimeFault,'CONTEXT'):acme.issue(self.stack,value,accept_terms=True)
        with patch.object(acme.socket,'getaddrinfo',return_value=[(0,0,0,'',('1.1.1.1',80))]):
            with self.assertRaisesRegex(RuntimeFault,'DNS_TARGET_CHANGED'):acme.check_network(self.staging)
        self.assertFalse(acme.folder(self.stack,self.project).exists())

    def test_unknown_order_is_not_reissued_recovery_reads_same_private_job(self):
        def interrupted(argv,**kwargs):
            self.provider(argv,**kwargs)
            raise RuntimeFault('FIXTURE_INTERRUPTED')
        with self.assertRaisesRegex(RuntimeFault,'INTERRUPTED'):
            acme.issue(self.stack,self.staging,accept_terms=True,runner=interrupted,network_check=lambda _:None)
        with self.assertRaisesRegex(RuntimeFault,'UNKNOWN_ORDER'):self.issue(self.staging)
        self.assertEqual(acme.recover(self.stack,self.project,'staging')['status'],'issued')
        self.assertEqual(len(self.calls),1)

    def test_recovery_rejects_external_certificate_link(self):
        self.issue(self.staging);root=acme.folder(self.stack,self.project)/'staging'
        receipt=acme.load(root/'receipt.json');receipt['status']='in_flight';write_json(root/'receipt.json',receipt)
        link=acme.job_path(self.stack,receipt)/'config/live/workspace/privkey.pem'
        link.unlink();link.symlink_to(self.key)
        with self.assertRaisesRegex(RuntimeFault,'PATH_UNSAFE'):acme.recover(self.stack,self.project,'staging')

    def test_account_symlink_is_refused(self):
        source=self.parent/'accounts';source.mkdir();(source/'bad').symlink_to(self.key)
        with self.assertRaisesRegex(RuntimeFault,'ACCOUNT_PATH_UNSAFE'):acme.copy_accounts(source,self.parent/'copy')

    def test_deploy_uses_existing_workspace_rotation_and_never_staging(self):
        self.issue(self.staging)
        with self.assertRaisesRegex(RuntimeFault,'ISSUED_CERTIFICATE_REQUIRED'):acme.deploy(self.stack,self.project)
        self.issue(self.production)
        with patch.object(tls,'apply',return_value={'status':'rotated'}) as apply:
            self.assertEqual(acme.deploy(self.stack,self.project)['status'],'rotated')
        self.assertEqual(apply.call_args.args[1]['previous_certificate_sha256'],self.plan['previous_certificate_sha256'])

    def test_renewal_reuses_issued_but_undeployed_certificate(self):
        self.issue(self.staging);self.issue(self.production)
        issuer=Mock();deployer=Mock(return_value={'status':'rotated'})
        with patch.object(workspace,'status',return_value={'status':'running'}),patch.object(tls,'status',return_value={'renewal_due':True,'certificate':{'sha256':'old'}}):
            acme.renew(self.stack,self.project,issuer=issuer,deployer=deployer)
        issuer.assert_not_called();deployer.assert_called_once()
        with patch.object(workspace,'status',return_value={'status':'incomplete'}):
            with self.assertRaisesRegex(RuntimeFault,'RUNNING_WORKSPACE'):acme.renew(self.stack,self.project,issuer=issuer,deployer=deployer)

    def test_due_renewal_issues_once_and_not_due_avoids_provider(self):
        self.issue(self.staging);self.issue(self.production)
        receipt=acme.load(acme.folder(self.stack,self.project)/'production/receipt.json')
        issuer=Mock();deployer=Mock(return_value={'status':'rotated'})
        for due in (False,True):
            with patch.object(workspace,'status',return_value={'status':'running'}),patch.object(tls,'status',return_value={'renewal_due':due,'certificate':{'sha256':receipt['certificate_sha256']}}):
                acme.renew(self.stack,self.project,issuer=issuer,deployer=deployer)
        issuer.assert_called_once();deployer.assert_called_once()

    def test_schedule_only_own_units_context_and_unknown_unit_protected(self):
        self.issue(self.staging);self.issue(self.production)
        units=self.parent/'units';units.mkdir();runner=Mock()
        result=acme.schedule(self.stack,self.project,True,units=units,runner=runner)
        self.assertTrue(result['scheduled']);self.assertEqual(len(list(units.iterdir())),2)
        service=units/result['unit'].replace('.timer','.service')
        self.assertNotIn('operator@example.com',service.read_text())
        service.write_text('external unit')
        with self.assertRaisesRegex(RuntimeFault,'UNIT_CHANGED'):acme.schedule(self.stack,self.project,True,units=units,runner=runner)

    def test_normalized_accounts_and_certificate_are_in_cold_backup(self):
        from cryptography.fernet import Fernet
        from video_factory.stack import Stack
        self.issue(self.staging);self.issue(self.production)
        write_json(self.stack.root/'initialized.json',{'schema':1})
        key=Fernet.generate_key();target=self.parent/'acme.vfb'
        with patch.object(workspace,'stop_all'),patch.object(self.stack,'compose'),patch.object(self.stack,'status',return_value={'components':{'postgres':{'state':'exited','exit_code':0}}}):self.stack.backup(target,key)
        restored=self.parent/'restored';restored.mkdir(mode=0o700);Stack.restore(target,restored,key)
        self.assertEqual((restored/'data/acme'/self.project/'production/key.pem').read_bytes(),self.key.read_bytes())
        self.assertFalse((restored/'acme-work').exists())

    def test_changed_instance_fences_renewal_and_exposes_sanitized_failure(self):
        self.issue(self.staging);self.issue(self.production)
        self.stack.config['instance']='restored_instance'
        issuer=Mock();deployer=Mock()
        with self.assertRaisesRegex(RuntimeFault,'CONTEXT_CHANGED'):
            acme.renew(self.stack,self.project,issuer=issuer,deployer=deployer)
        issuer.assert_not_called();deployer.assert_not_called()
        result=acme.status(self.stack,self.project)
        self.assertTrue(result['environments']['production']['requires_context_review'])
        self.assertEqual(result['last_renewal']['error'],'ACME_CONTEXT_CHANGED')

    def test_provider_failure_preserves_current_certificate_and_records_no_raw_error(self):
        self.issue(self.staging);self.issue(self.production)
        receipt=acme.load(acme.folder(self.stack,self.project)/'production/receipt.json')
        issuer=Mock(side_effect=ValueError('private provider diagnostic'))
        with patch.object(workspace,'status',return_value={'status':'running'}),patch.object(tls,'status',return_value={'renewal_due':True,'certificate':{'sha256':receipt['certificate_sha256']}}):
            with self.assertRaises(ValueError):acme.renew(self.stack,self.project,issuer=issuer)
        result=acme.status(self.stack,self.project)
        self.assertEqual(result['last_renewal']['error'],'ACME_RENEWAL_FAILED')
        self.assertNotIn('private provider diagnostic',json.dumps(result))
        self.assertEqual((self.root/'tls/certificate.pem').read_bytes(),self.oldcert.read_bytes())

    def test_missing_client_and_ipv6_only_plan_fail_without_order(self):
        self.client.stop()
        with patch.object(acme,'CLIENT',self.parent/'missing-client'):
            with self.assertRaisesRegex(RuntimeFault,'TRUSTED_CERTBOT'):acme.client_digest()
        with self.assertRaisesRegex(RuntimeFault,'PUBLIC_IPV4'):
            acme.plan(self.stack,self.project,self.staging['origin'],self.staging['email'],['2606:4700:4700::1111'],'staging')
        self.assertFalse(acme.folder(self.stack,self.project).exists())

    def test_client_change_is_visible_in_status_and_origin_change_blocks_timer(self):
        self.issue(self.staging);self.issue(self.production)
        with patch.object(acme,'client_digest',return_value='b'*64):
            self.assertTrue(acme.status(self.stack,self.project)['environments']['production']['requires_context_review'])
        units=self.parent/'units';units.mkdir();runner=Mock()
        with patch.object(tls,'current',return_value=(self.root,{'origin':'https://other.example'})):
            with self.assertRaisesRegex(RuntimeFault,'ORIGIN_CHANGED'):
                acme.schedule(self.stack,self.project,True,units=units,runner=runner)
        runner.assert_not_called();self.assertEqual(list(units.iterdir()),[])

    def test_tls_status_reads_actual_acme_timer_state(self):
        self.issue(self.staging);self.issue(self.production)
        units=self.parent/'units';units.mkdir()
        acme.schedule(self.stack,self.project,True,units=units,runner=Mock())
        with patch.object(acme,'run',return_value=b''):
            self.assertTrue(tls.status(self.stack,self.project)['automatic_acme'])
        with patch.object(acme,'run',side_effect=RuntimeFault('STACK_COMMAND_FAILED')):
            state=tls.status(self.stack,self.project)
            self.assertFalse(state['automatic_acme'])
            self.assertTrue(state['acme']['schedule']['enabled'])
