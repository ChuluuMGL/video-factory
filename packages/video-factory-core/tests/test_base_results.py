import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from cryptography.fernet import Fernet

from video_factory.base_results import BaseResults, FIELDS, control_key, item_prefix, snapshot
from video_factory.feishu_bridge import save, meta
from video_factory.runtime_store import RuntimeFault, canonical
import test_feishu_bridge as fixtures
from test_feishu_bridge import BINDING


class Remote:
    def __init__(self, fixture):
        self.fixture = fixture; self.table_rows = []; self.rows = {}; self.calls = []
        self.lose = None; self.callback = None; self.changed_schema = False; self.parts = []
    def done(self, kind, result):
        self.calls.append(kind)
        if self.callback: self.callback(kind)
        if self.lose == kind: raise RuntimeFault('BASE_RESULTS_REQUEST_UNKNOWN_READ_STATUS')
        return result
    def fields(self, base, table):
        assert base == BINDING['base_token']
        if table == BINDING['table_id']: return copy.deepcopy(self.fixture.schema)
        result = [{'field_id': 'fldResult'+str(n), 'field_name': name, 'type': kind} for n, (name, kind) in enumerate(FIELDS.items())]
        if self.changed_schema: result[0]['field_id'] = 'fldChanged'
        return result
    def tables(self, base): return copy.deepcopy(self.table_rows)
    def create_result_table(self, base, name):
        if self.lose == 'table_forbidden':
            self.calls.append('table_forbidden')
            raise RuntimeFault('BASE_RESULTS_REQUEST_REJECTED_91403')
        self.table_rows.append({'name': name, 'table_id': 'tblResults'})
        return self.done('table', 'tblResults')
    def find(self, base, table, event):
        assert table == 'tblResults'
        return [copy.deepcopy(row) for row in self.rows.values() if row['fields']['同步标识'] == event]
    def append(self, base, table, fields, ticket):
        import uuid
        assert uuid.UUID(ticket).version == 4
        rid = 'recResult'+str(len(self.rows)); self.rows[rid] = {'record_id': rid, 'fields': copy.deepcopy(fields)}
        return self.done('record', rid)
    def upload_prepare(self, base, name, size):
        return self.done('prepare', {'upload_id': 'fixtureUpload', 'block_size': 4*1024*1024, 'block_num': (size+4*1024*1024-1)//(4*1024*1024)})
    def upload_part(self, upload_id, seq, data):
        self.parts.append(data); return self.done('part', {'seq': seq, 'size': len(data)})
    def upload_finish(self, upload_id, block_num): return self.done('finish', 'boxcnFixture')


class ResultsTests(unittest.TestCase):
    def setUp(self):
        fixtures.FeishuTests.setUp(self)
        self.key = Fernet.generate_key()
        self.store.put_secret(self.admin, 'results_fixture', 'synthetic-application-secret', self.key)
        with self.store.connect() as db:
            save(db, 'setup:feishu-app:brand', {'app_id': 'cli_fixture', 'credential_ref': 'secret:results_fixture'})
        self.remote = Remote(self)
        self.results = BaseResults(self.store, self.key, client_factory=lambda *_: self.remote)
        self.media = self.root/'media'; self.media.mkdir(mode=0o700)
        fixtures.FeishuTests.import_task(self)
    tearDown = fixtures.FeishuTests.tearDown
    def enable(self):
        return self.results.enable(self.admin, 'brand', self.results.prepare(self.admin, 'brand')['plan_sha256'])
    def sync(self):
        return self.results.sync('brand', self.media, lambda db: self.store.authorize(db, self.admin, 'admin'))
    def video(self):
        data = b'synthetic-video-bytes'; path = self.media/'fixture.mp4'; path.write_bytes(data); path.chmod(0o600)
        with self.store.connect() as db:
            db.execute("UPDATE tasks SET state='awaiting_video_review',artifact=? WHERE project='brand'", (canonical({'sha256': hashlib.sha256(data).hexdigest(), 'location': str(path)}),))
        return data

    def test_opt_in_readback_append_history_no_input_writes(self):
        self.assertEqual(self.sync()['status'], 'disabled'); self.assertEqual(self.remote.calls, [])
        self.enable(); self.enable(); self.assertEqual(self.remote.calls, ['table'])
        self.assertEqual(self.sync()['status'], 'record_written_readback_pending')
        self.assertEqual(self.sync()['status'], 'synced')
        self.assertEqual(self.sync()['status'], 'idle')
        with self.store.connect() as db: db.execute("UPDATE tasks SET state='ready' WHERE project='brand'")
        self.sync(); self.sync()
        self.assertEqual(len(self.remote.rows), 2)
        self.assertEqual(next(iter(self.remote.rows.values()))['fields']['状态'], '脚本待审核')
        self.assertEqual(self.results.status(self.admin, 'brand')['synced'], 2)

    def test_lost_create_reconciles_exact_named_table_without_duplicate(self):
        self.remote.lose = 'table'
        with self.assertRaisesRegex(RuntimeFault, 'UNKNOWN'): self.enable()
        self.remote.lose = None
        self.enable(); self.assertEqual(self.remote.calls, ['table'])

    def test_lost_create_and_empty_read_never_creates_again(self):
        self.remote.lose = 'table'
        with self.assertRaises(RuntimeFault): self.enable()
        self.remote.table_rows = []; self.remote.lose = None
        with self.assertRaisesRegex(RuntimeFault, 'TABLE_UNKNOWN'): self.enable()
        self.assertEqual(self.remote.calls, ['table'])

    def test_explicit_forbidden_keeps_destination_name_and_can_retry_after_permission_repair(self):
        self.remote.lose = 'table_forbidden'
        with self.assertRaisesRegex(RuntimeFault, 'APP_DOCUMENT_EDIT_REQUIRED'): self.enable()
        with self.store.connect() as db:
            config = meta(db, control_key('brand'))
        self.assertFalse(config['table_attempted'])
        self.assertIsNone(config['table_id'])
        self.remote.lose = None
        self.enable()
        self.assertEqual(self.remote.calls, ['table_forbidden', 'table'])
        self.assertEqual(self.remote.table_rows[0]['name'], config['table_name'])

    def test_lost_record_response_reconciles_without_resubmitting(self):
        self.enable(); self.remote.lose = 'record'
        with self.assertRaisesRegex(RuntimeFault, 'UNKNOWN'): self.sync()
        self.remote.lose = None
        self.assertEqual(self.sync()['status'], 'synced')
        self.assertEqual(self.remote.calls.count('record'), 1)

    def test_unknown_record_empty_read_and_external_edit_fail_closed(self):
        self.enable(); self.sync()
        self.remote.rows['recResult0']['fields']['脚本'] = 'Human edit'
        with self.assertRaisesRegex(RuntimeFault, 'EXTERNAL_EDIT_CONFLICT'): self.sync()
        self.remote.rows = {}
        with self.assertRaisesRegex(RuntimeFault, 'UNKNOWN_OR_REMOVED'): self.sync()
        self.assertEqual(self.remote.calls.count('record'), 1)

    def test_video_upload_receipts_then_attachment_no_internal_paths(self):
        data = self.video(); self.enable()
        states = [self.sync()['status'] for _ in range(5)]
        self.assertEqual(states, ['upload_prepared', 'upload_part_saved', 'upload_finished', 'record_written_readback_pending', 'synced'])
        self.assertEqual(b''.join(self.remote.parts), data)
        fields = self.remote.rows['recResult0']['fields']
        self.assertEqual(fields['视频'], [{'file_token': 'boxcnFixture'}])
        self.assertNotIn(str(self.root), canonical(fields))
        self.assertNotIn('synthetic-application-secret', canonical(fields))

    def test_ambiguous_upload_stops_without_duplicate(self):
        self.video(); self.enable(); self.remote.lose = 'prepare'
        with self.assertRaisesRegex(RuntimeFault, 'UNKNOWN'): self.sync()
        self.remote.lose = None
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeFault, 'WRITE_UNKNOWN'): self.sync()
        status = self.results.status(self.admin, 'brand')
        self.assertEqual(status['blocked'][0]['step'], 'prepare')
        self.assertEqual(self.remote.calls.count('prepare'), 1)

    def test_pause_and_restore_do_not_resume_writes(self):
        self.enable(); self.results.pause(self.admin, 'brand')
        self.assertEqual(self.sync()['status'], 'disabled')
        self.enable()
        with self.store.connect() as db: self.store.invalidate_worker_approvals(db)
        status = self.results.status(self.admin, 'brand')
        self.assertFalse(status['enabled']); self.assertTrue(status['recovery_required'])
        self.assertEqual(self.sync()['status'], 'disabled')
        with self.assertRaises(RuntimeFault): self.enable()

    def test_schema_or_project_or_credential_change_blocks_write(self):
        self.enable(); self.remote.changed_schema = True
        with self.assertRaisesRegex(RuntimeFault, 'SCHEMA_CHANGED'): self.sync()
        self.remote.changed_schema = False
        self.store.put_secret(self.admin, 'results_fixture', 'synthetic-rotated-secret', self.key)
        with self.assertRaisesRegex(RuntimeFault, 'CONFIGURATION_CHANGED'): self.sync()
        self.assertEqual(self.remote.calls, ['table'])

    def test_auth_and_other_project_do_not_gain_capability(self):
        self.enable()
        with self.assertRaisesRegex(RuntimeFault, 'AUTH'):
            self.results.sync('brand', self.media, lambda db: self.store.authorize(db, 'bad', 'admin'))
        self.assertEqual(self.results.sync('other', self.media, lambda db: self.store.authorize(db, self.admin, 'admin'))['status'], 'disabled')
        self.assertEqual(self.remote.calls, ['table'])

    def test_race_claims_one_record_only(self):
        self.enable()
        def sync(_):
            try: return self.sync()
            except RuntimeFault: return None
        with ThreadPoolExecutor(max_workers=4) as pool: list(pool.map(sync, range(4)))
        self.sync(); self.assertEqual(self.remote.calls.count('record'), 1)

    def test_media_symlink_or_hash_change_blocks_before_upload(self):
        self.video(); self.enable(); (self.media/'fixture.mp4').write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeFault, 'HASH_CHANGED'): self.sync()
        self.assertEqual(self.remote.calls, ['table'])

    def test_pause_during_create_preserves_receipt_but_does_not_enable(self):
        self.remote.callback = lambda _: self.results.pause(self.admin, 'brand')
        with self.assertRaisesRegex(RuntimeFault, 'CONFIGURATION_CHANGED'): self.enable()
        status = self.results.status(self.admin, 'brand')
        self.assertFalse(status['enabled']); self.assertEqual(status['table_id'], 'tblResults')

    def test_expired_admin_after_network_preserves_known_receipt(self):
        self.enable()
        def revoke(_):
            with self.store.connect() as db: db.execute('DELETE FROM sessions')
        self.remote.callback = revoke
        self.sync()
        self.admin = self.store.login('admin', 'fixture-admin-password')['token']
        self.remote.callback = None
        self.assertEqual(self.sync()['status'], 'synced')
        self.assertEqual(self.remote.calls.count('record'), 1)

    def test_reviewed_retry_reuses_record_ticket_and_never_auto_retries(self):
        self.enable(); self.remote.lose = 'record'
        with self.assertRaises(RuntimeFault): self.sync()
        self.remote.rows = {}; self.remote.lose = None
        blocked = self.results.status(self.admin, 'brand')['blocked'][0]
        with self.store.connect() as db:
            item = meta(db, item_prefix('brand')+blocked['event']); ticket = item['steps']['record']['ticket']
        plan = self.results.repair_plan(self.admin, 'brand', blocked['event'], 'record')
        self.results.repair(self.admin, 'brand', blocked['event'], 'record', plan['plan_sha256'])
        self.sync(); self.sync()
        with self.store.connect() as db:
            item = meta(db, item_prefix('brand')+blocked['event'])
        self.assertEqual(item['steps']['record']['ticket'], ticket)
        self.assertEqual(len(item['steps']['record']['history']), 1)
        self.assertTrue(item['complete'])

    def test_retry_plan_expiry_changed_plan_and_retry_cap(self):
        self.enable(); self.remote.lose = 'record'
        with self.assertRaises(RuntimeFault): self.sync()
        self.remote.rows = {}
        blocked = self.results.status(self.admin, 'brand')['blocked'][0]; event = blocked['event']
        plan = self.results.repair_plan(self.admin, 'brand', event, 'record')
        with self.assertRaisesRegex(RuntimeFault, 'PLAN_CHANGED'):
            self.results.repair(self.admin, 'brand', event, 'record', '0'*64)
        self.results.repair(self.admin, 'brand', event, 'record', plan['plan_sha256'])
        with patch('video_factory.base_results.time.time', return_value=10**12):
            with self.assertRaisesRegex(RuntimeFault, 'AUTH|EXPIRED'): self.sync()
        for attempt in range(2):
            with self.assertRaises(RuntimeFault): self.sync()
            self.remote.rows = {}
            if attempt == 0:
                plan = self.results.repair_plan(self.admin, 'brand', event, 'record')
                self.results.repair(self.admin, 'brand', event, 'record', plan['plan_sha256'])
        with self.assertRaisesRegex(RuntimeFault, 'REPAIR_LIMIT'):
            self.results.repair_plan(self.admin, 'brand', event, 'record')

    def test_finished_checkpoint_recovers_only_after_remote_readback_and_stays_paused(self):
        self.enable(); self.sync(); self.sync()
        with self.store.connect() as db:
            self.store.invalidate_worker_approvals(db)
            db.execute('DELETE FROM meta WHERE key=?', ('feishu:reconfirm:brand',))  # fixture simulates existing binding re-verification
        plan = self.results.recovery_plan(self.admin, 'brand')
        self.assertEqual(plan['plan']['verified_rows'], 1)
        self.results.recover(self.admin, 'brand', plan['plan_sha256'])
        self.assertEqual(self.sync()['status'], 'disabled')
        self.enable(); self.assertEqual(self.sync()['status'], 'idle')
        self.assertEqual(self.remote.calls, ['table', 'record'])

    def test_checkpoint_recovery_rejects_remote_edits_and_unfinished_record(self):
        self.enable(); self.sync(); self.sync()
        with self.store.connect() as db:
            self.store.invalidate_worker_approvals(db)
            db.execute('DELETE FROM meta WHERE key=?', ('feishu:reconfirm:brand',))
        self.remote.rows['recResult0']['fields']['脚本'] = 'Modified'
        with self.assertRaisesRegex(RuntimeFault, 'REMOTE_MISMATCH'):
            self.results.recovery_plan(self.admin, 'brand')

    def test_completed_video_checkpoint_rechecks_attachment(self):
        self.video(); self.enable()
        for _ in range(5): self.sync()
        with self.store.connect() as db:
            self.store.invalidate_worker_approvals(db)
            db.execute('DELETE FROM meta WHERE key=?', ('feishu:reconfirm:brand',))
        self.results.recovery_plan(self.admin, 'brand')
        self.remote.rows['recResult0']['fields']['视频'][0]['file_token'] = 'boxcnOther'
        with self.assertRaisesRegex(RuntimeFault, 'REMOTE_MISMATCH'):
            self.results.recovery_plan(self.admin, 'brand')
