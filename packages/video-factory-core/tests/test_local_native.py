"""Cloud-only real SQLite/native-sync integration with synthetic remote I/O."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from video_factory import automation
from video_factory.dispatch import dispatch_with_results
from video_factory.feishu_bridge import meta, save
from video_factory.feishu_native_sync import NativeSync
from video_factory.local_native_cli import configure_app
from video_factory.local_native_events import callback, context
from video_factory.local_runtime import native_receiver
from video_factory.runtime_store import RuntimeStore, RuntimeFault
from video_factory.scheduler import Scheduler
from test_feishu_native_sync import Remote


class LocalNativeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.store = RuntimeStore.install(self.root, 'fixture', 'fixture-admin-password')
        self.admin = self.store.login('admin', 'fixture-admin-password')['token']
        self.store.put_project(self.admin, 'brand', {'video_route': 'deferred',
                               'credential_ref': 'secret:fixture', 'billing_owner': 'fixture'})
        self.key = Fernet.generate_key()
        configure_app(self.store, self.admin, 'brand', 'cli_fixture', 'fixture-secret', self.key)
        self.binding = {'tenant_key': 'fixture_tenant', 'base_token': 'bascnFixture',
                        'table_id': 'tblFixture', 'fields': {'task': 'fldTask', 'sku_id': 'fldSkuId',
                        'script': 'fldScript', 'source_revision': 'fldSource'},
                        'submitters': ['ou_submitter'], 'reviewers': ['ou_reviewer']}
        with self.store.connect() as db:
            save(db, 'feishu:binding:brand', self.binding)
        self.remote = Remote()
        self.native = NativeSync(self.store, self.key, client_factory=lambda *_: self.remote)
        plan = self.native.prepare(self.admin, 'brand')
        self.native.enable(self.admin, 'brand', plan['plan_sha256'])
        self.execution = automation.issue_execution(self.store, self.admin, 'brand')['token']

    def scheduler(self):
        return Scheduler(self.store, 'brand', execution_token=self.execution,
                         master_key=self.key, media_root=self.root, dispatch=dispatch_with_results)

    def task(self):
        self.store.create_task(self.admin, 'brand', 'task_one',
                               {'sku_id': 'sku_one', 'script': 'Generated fixture draft', 'source_revision': 'source_one'})

    def test_scheduler_writes_original_row_and_recovers_lost_response_without_repeat(self):
        self.task()
        original = self.remote.update_record
        def lose(*args):
            original(*args)
            raise RuntimeFault('BASE_RESULTS_REQUEST_UNKNOWN_READ_STATUS')
        with patch('video_factory.feishu_native_sync.NativeSync', return_value=self.native), \
                patch('video_factory.base_results.BaseResults.sync', return_value={'status': 'disabled'}):
            with patch.object(self.remote, 'update_record', side_effect=lose):
                result = self.scheduler().tick()
            self.assertEqual(result['last_error'], 'FEISHU_RECONCILIATION_REQUIRED')
            self.assertEqual(self.remote.record_fields['状态'], '脚本待审核')
            self.assertEqual(self.remote.record_fields['审核目标版本'], '1')
            self.assertEqual(self.scheduler().tick()['state'], 'waiting')
            self.assertEqual(self.scheduler().tick()['state'], 'waiting')
            self.assertEqual(len([w for w in self.remote.writes if w[0] == 'record']), 1)
        with self.store.connect() as db:
            self.assertEqual(meta(db, 'local:scheduler:cursor:brand'), {'after': ''})

    def test_revocation_prevents_remote_io(self):
        self.task()
        automation.revoke(self.store, self.admin, hashlib.sha256(self.execution.encode()).hexdigest())
        before = list(self.remote.writes)
        with self.assertRaisesRegex(RuntimeFault, 'AUTH_EXECUTION_DENIED'):
            context(self.store, 'brand', self.execution, self.key)
        with self.assertRaisesRegex(RuntimeFault, 'AUTH_EXECUTION_DENIED'):
            dispatch_with_results(self.store, self.execution, 'brand', self.key, self.root, after='')
        self.assertEqual(before, self.remote.writes)

    def test_event_callback_checks_current_capability_and_context(self):
        expected = context(self.store, 'brand', self.execution, self.key)
        with patch('video_factory.local_native_events.NativeReview') as reducer, \
                patch('lark_oapi.JSON.marshal', return_value='{"schema":"2.0"}'):
            receive = callback(self.store, 'brand', self.execution, self.key, expected)
            receive(object())
            reducer.return_value.enqueue_verified.assert_called_once_with('brand', {'schema': '2.0'})
            self.store.put_project(self.admin, 'brand', {'video_route': 'deferred',
                                   'credential_ref': 'secret:fixture', 'billing_owner': 'changed'},
                                   expected_digest=expected['configuration_sha256'])
            with self.assertRaisesRegex(RuntimeFault, 'CONTEXT_CHANGED'):
                receive(object())
            self.assertEqual(reducer.return_value.enqueue_verified.call_count, 1)

    def test_fake_runtime_cannot_be_converted_and_app_cannot_be_replaced(self):
        with self.assertRaisesRegex(RuntimeFault, 'APP_ALREADY_CONFIGURED'):
            configure_app(self.store, self.admin, 'brand', 'cli_other', 'other-secret', self.key)
        with self.store.connect() as db:
            save(db, 'local:fake-only:v1', {'mode': 'fake-only'})
        with self.assertRaisesRegex(RuntimeFault, 'NO_EXTERNAL_IO'):
            context(self.store, 'brand', self.execution, self.key)

    def test_receiver_death_blocks_dispatch_and_shutdown_reaps_child(self):
        scheduler = self.scheduler()
        scheduler.dispatch = Mock(return_value={'status': 'idle'})
        child = Mock(); child.poll.return_value = None
        with patch('video_factory.local_runtime.subprocess.Popen', return_value=child) as launch:
            with native_receiver(self.store, 'brand', (self.root/'execution', self.root/'master'), scheduler):
                self.assertEqual(scheduler.status()['feishu_events'], 'receiver_running_delivery_unverified')
                self.assertEqual(scheduler.dispatch()['status'], 'idle')
                child.poll.return_value = 2
                with self.assertRaisesRegex(RuntimeFault, 'RECEIVER_STOPPED'):
                    scheduler.dispatch()
            child.wait.assert_called_once_with(timeout=5)
            self.assertNotIn(self.execution, str(launch.call_args))
            self.assertEqual(scheduler.status()['feishu_events'], 'stopped')

    def test_live_receiver_cannot_dispatch_after_binding_changes(self):
        scheduler = self.scheduler()
        dispatch = Mock(return_value={'status': 'idle'})
        scheduler.dispatch = dispatch
        child = Mock(); child.poll.return_value = None
        with patch('video_factory.local_runtime.subprocess.Popen', return_value=child):
            with native_receiver(self.store, 'brand', (self.root/'execution', self.root/'master'), scheduler):
                with self.store.connect() as db:
                    binding = meta(db, 'feishu:binding:brand')
                    binding['table_id'] = 'tblDifferent'
                    save(db, 'feishu:binding:brand', binding)
                with self.assertRaisesRegex(RuntimeFault, 'CONTEXT_CHANGED'):
                    scheduler.dispatch()
                dispatch.assert_not_called()
            child.terminate.assert_called_once()


if __name__ == '__main__':
    unittest.main()
