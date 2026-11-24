"""Independent adversarial tests for HD01 1254002 copy update recovery and stopped-run inspect/resolve.

Authored strictly from docs/product/SHADOW_COPY_WRITE_RECOVERY.md and public test contracts,
using only synthetic fixtures and mock clients without network access.
"""
import copy
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, Mock, patch
from urllib.error import HTTPError

from video_factory.feishu_bridge import meta, save
from video_factory.feishu_diagnostics import failure_context
from video_factory.feishu_results_client import request
from video_factory.runtime_store import RuntimeFault
from video_factory.script_week_io import CopyOnlyWeekWriter
from video_factory.script_week_projection import WeekProjection, projection_key
from video_factory.shadow_copy_recovery import recover


class SyntheticLedger:
    def __init__(self, root: str):
        self.path = root + '/adversarial.sqlite3'
        with self.connect() as db:
            db.execute('CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            save(db, 'script:week:fixture', {'state': 'closed'})
            save(db, 'script:trial:fixture', {'enabled': False})

    @contextmanager
    def connect(self):
        with sqlite3.connect(self.path) as db:
            yield db

    def get(self, key: str):
        with self.connect() as db:
            return meta(db, key)

    def put(self, key: str, value: dict):
        with self.connect() as db:
            save(db, key, value)

    def all_receipts(self):
        with self.connect() as db:
            rows = db.execute(
                "SELECT key, value FROM meta WHERE key LIKE 'script:week-projection-resolution:%'"
            ).fetchall()
            return {k: json.loads(v) for k, v in rows}


class SyntheticCopyClient:
    def __init__(self):
        self.remote = {
            'record_id': 'recSynth01',
            'fields': {
                'Sample': 'sample-01',
                'Output': 'prior-draft',
                'Review': 'human-approved',
            },
        }
        self.appends = 0
        self.updates = []
        self.fail_write = None
        self.commit_on_fail = False
        self.fail_read = None
        self.fail_read_after_put = None

    def record(self, *args):
        if self.fail_read:
            raise RuntimeFault(self.fail_read)
        return copy.deepcopy(self.remote)

    def append(self, base, table, fields, ticket):
        self.appends += 1
        if self.commit_on_fail or not self.fail_write:
            self.remote['fields'].update({k: v for k, v in fields.items() if v != ''})
        if self.fail_write:
            raise RuntimeFault(self.fail_write)
        return self.remote['record_id']

    def update_record(self, base, table, record_id, fields):
        self.updates.append(copy.deepcopy(fields))
        if self.commit_on_fail or not self.fail_write:
            for k, v in fields.items():
                if v == '' or v is None:
                    self.remote['fields'].pop(k, None)
                else:
                    self.remote['fields'][k] = v
        if self.fail_write:
            raise RuntimeFault(self.fail_write)
        if self.fail_read_after_put:
            self.fail_read = self.fail_read_after_put


class ResolveAdversarialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = SyntheticLedger(self.tmp.name)
        self.client = SyntheticCopyClient()
        self.project = 'fixture'
        self.source_base = 'basSourceNeverTouch'
        self.copy_base = 'basCopyTarget'
        self.table = 'tblCopyTarget'
        self.identity = 'sample-01'
        self.key = projection_key(self.project, self.copy_base, self.table, self.identity)
        self.writer = CopyOnlyWeekWriter(
            self.client,
            source_base=self.source_base,
            copy_base=self.copy_base,
            table=self.table,
            machine_fields=['Sample', 'Output', 'Failure', 'Cost'],
            human_fields=['Review'],
            identity_field='Sample',
        )

    def new_projection(self, find_fn=None):
        if find_fn is None:
            find_fn = lambda _: [copy.deepcopy(self.client.remote)] if self.client.appends else []
        return WeekProjection(self.ledger, self.project, self.writer, find_fn)

    def call_recover(self, record_fn=None, find_fn=None, **kwargs):
        rec = record_fn or (lambda *a: self.client.record(*a))
        fnd = find_fn or (lambda *a: [copy.deepcopy(self.client.remote)] if self.client.appends else [])
        with self.ledger.connect() as db:
            return recover(
                db,
                project=self.project,
                base=self.copy_base,
                table=self.table,
                identity=self.identity,
                identity_field='Sample',
                record=rec,
                find=fnd,
                **kwargs,
            )

    def seed_update_intent(self, pending=None, prior=None, ticket='tkt-upd-01', state='update_intent'):
        row = {
            'state': state,
            'target': [self.copy_base, self.table],
            'record_id': 'recSynth01',
            'update_ticket': ticket,
            'pending_fields': pending or {'Sample': self.identity, 'Output': 'pending-v2', 'Failure': ''},
            'prior_fields': prior or {'Sample': self.identity, 'Output': 'prior-draft'},
            'first_failure': {'error': 'FIRST_1254002', 'feishu_code': 1254002},
            'last_failure': {'error': 'LAST_1254002', 'feishu_code': 1254002},
        }
        self.ledger.put(self.key, row)
        return row

    # 1. inspect 的三种结论：matches_pending / matches_prior / neither
    def test_inspect_three_classifications_for_update_and_append(self):
        # Update: matches_prior
        self.seed_update_intent()
        self.client.remote['fields'] = {'Sample': self.identity, 'Output': 'prior-draft', 'Review': 'human'}
        rep_prior = self.call_recover()
        self.assertEqual(rep_prior['classification'], 'matches_prior')
        self.assertEqual(self.ledger.all_receipts(), {})

        # Update: matches_pending
        self.client.remote['fields'] = {'Sample': self.identity, 'Output': 'pending-v2', 'Review': 'human'}
        rep_pending = self.call_recover()
        self.assertEqual(rep_pending['classification'], 'matches_pending')

        # Update: neither (divergent machine value)
        self.client.remote['fields'] = {'Sample': self.identity, 'Output': 'unexpected-v3', 'Review': 'human'}
        rep_neither = self.call_recover()
        self.assertEqual(rep_neither['classification'], 'neither')

        # Append: matches_prior requires prior_absent=True and zero rows returned
        append_row = {
            'state': 'intent',
            'target': [self.copy_base, self.table],
            'ticket': 'tkt-app-01',
            'initial_fields': {'Sample': self.identity, 'Output': 'pending-v2'},
            'prior_absent': True,
            'first_failure': {'error': 'FIRST'},
            'last_failure': {'error': 'LAST'},
        }
        self.ledger.put(self.key, append_row)
        self.assertEqual(self.call_recover(find_fn=lambda *_: [])['classification'], 'matches_prior')

        # Append: matches_pending when unique matching row exists
        matched_row = {'record_id': 'recSynth01', 'fields': {'Sample': self.identity, 'Output': 'pending-v2'}}
        self.assertEqual(self.call_recover(find_fn=lambda *_: [matched_row])['classification'], 'matches_pending')

        # Append: neither when duplicate rows or missing prior_absent evidence
        self.assertEqual(
            self.call_recover(find_fn=lambda *_: [matched_row, matched_row])['classification'],
            'neither',
        )
        append_no_evidence = dict(append_row)
        append_no_evidence.pop('prior_absent')
        self.ledger.put(self.key, append_no_evidence)
        self.assertEqual(self.call_recover(find_fn=lambda *_: [])['classification'], 'neither')

    # 2. 没有负责人确认、ticket 不对、状态不是 intent 时，resolve 都要拒绝；neither 时 resolve 也要拒绝
    def test_resolve_refuses_without_owner_confirm_wrong_ticket_non_intent_or_neither(self):
        seeded = self.seed_update_intent(ticket='tkt-exact-99')
        mock_rec = Mock(return_value=self.client.remote)

        # Missing owner_confirm
        r1 = self.call_recover(record_fn=mock_rec, resolve=True, owner_confirm=False, ticket='tkt-exact-99')
        self.assertEqual(r1['decision'], 'refused')
        self.assertEqual(self.ledger.get(self.key), seeded)
        mock_rec.assert_not_called()

        # Wrong or empty ticket
        for bad_ticket in ('wrong-ticket', '', None):
            r2 = self.call_recover(record_fn=mock_rec, resolve=True, owner_confirm=True, ticket=bad_ticket)
            self.assertEqual(r2['decision'], 'refused')
            self.assertEqual(self.ledger.get(self.key), seeded)
        mock_rec.assert_not_called()

        # State is not intent / update_intent (e.g., created, abandoned)
        for non_intent_state in ('created', 'abandoned', 'completed'):
            non_intent_row = dict(seeded, state=non_intent_state)
            self.ledger.put(self.key, non_intent_row)
            r3 = self.call_recover(record_fn=mock_rec, resolve=True, owner_confirm=True, ticket='tkt-exact-99')
            self.assertEqual(r3['decision'], 'refused')
            self.assertEqual(self.ledger.get(self.key), non_intent_row)

        # State is update_intent, owner_confirm=True, ticket matches, but classification is 'neither'
        self.ledger.put(self.key, seeded)
        self.client.remote['fields']['Output'] = 'third-party-drift'
        r4 = self.call_recover(resolve=True, owner_confirm=True, ticket='tkt-exact-99')
        self.assertEqual(r4['classification'], 'neither')
        self.assertEqual(r4['decision'], 'refused')
        # Intent and failure records must remain completely intact
        self.assertEqual(self.ledger.get(self.key), seeded)

    # 3. 中途有人工改动了机器字段或人工字段
    def test_midflight_human_edit_on_machine_field_vs_human_field(self):
        self.seed_update_intent(
            pending={'Sample': self.identity, 'Output': 'machine-pending', 'Failure': ''},
            prior={'Sample': self.identity, 'Output': 'machine-prior', 'Failure': ''},
            ticket='tkt-midflight',
        )

        # Case A: Human edited human-owned field ('Review') mid-flight while machine fields stay at prior
        self.client.remote['fields'] = {
            'Sample': self.identity,
            'Output': 'machine-prior',
            'Review': 'human-edited-during-outage',
        }
        rep_human_prior = self.call_recover()
        self.assertEqual(rep_human_prior['classification'], 'matches_prior')

        # If machine fields match pending and human edited 'Review', classification is still matches_pending
        self.client.remote['fields']['Output'] = 'machine-pending'
        rep_human_pending = self.call_recover()
        self.assertEqual(rep_human_pending['classification'], 'matches_pending')

        # Case B: Human manually edited a machine-owned field ('Output' or 'Failure') to a custom value
        self.client.remote['fields']['Output'] = 'human-overrode-machine-output'
        rep_machine_tampered = self.call_recover()
        self.assertEqual(rep_machine_tampered['classification'], 'neither')
        refusal = self.call_recover(resolve=True, owner_confirm=True, ticket='tkt-midflight')
        self.assertEqual(refusal['decision'], 'refused')
        self.assertEqual(self.ledger.get(self.key)['state'], 'update_intent')

        # Also verify writer directly rejects any attempt to pass human field 'Review'
        with self.assertRaises(RuntimeFault):
            self.writer.update('recSynth01', self.identity, {'Review': 'illegal-machine-write'})
        self.assertEqual(self.client.updates, [])

    # 4. 重启后状态一致，并且不发生任何重发
    def test_restart_preserves_state_and_never_resends_put_or_post(self):
        proj1 = self.new_projection()
        proj1.publish(self.identity, {'Sample': self.identity, 'Output': 'v1', 'Failure': ''})
        self.assertEqual(self.client.appends, 1)

        # Trigger uncertain 1254002 without remote commit
        self.client.fail_write = 'BASE_RESULTS_REQUEST_UNKNOWN_FEISHU_CODE_1254002'
        self.client.commit_on_fail = False
        with self.assertRaises(RuntimeFault):
            proj1.publish(self.identity, {'Sample': self.identity, 'Output': 'v2', 'Failure': ''})
        self.assertEqual(len(self.client.updates), 1)
        frozen_before_restart = copy.deepcopy(self.ledger.get(self.key))
        self.assertEqual(frozen_before_restart['state'], 'update_intent')

        # Simulate multiple process restarts with new WeekProjection instances
        self.client.fail_write = None
        for desired in ('v2', 'v3-new-attempt'):
            restarted_proj = self.new_projection()
            with self.assertRaisesRegex(RuntimeFault, 'READBACK_MISMATCH'):
                restarted_proj.publish(self.identity, {'Sample': self.identity, 'Output': desired, 'Failure': ''})
            # Must NOT resend PUT or POST on restart
            self.assertEqual(len(self.client.updates), 1)
            self.assertEqual(self.client.appends, 1)
            after_row = self.ledger.get(self.key)
            self.assertEqual(after_row['state'], 'update_intent')
            self.assertEqual(after_row['update_ticket'], frozen_before_restart['update_ticket'])
            self.assertEqual(after_row['pending_fields'], frozen_before_restart['pending_fields'])
            self.assertEqual(after_row['first_failure'], frozen_before_restart['first_failure'])

        # Resolve matches_prior -> abandoned tombstone; verify first_failure and last_failure preserved
        current_ticket = self.ledger.get(self.key)['update_ticket']
        expected_first = self.ledger.get(self.key)['first_failure']
        expected_last = self.ledger.get(self.key)['last_failure']
        res = self.call_recover(resolve=True, owner_confirm=True, ticket=current_ticket)
        self.assertEqual(res['decision'], 'abandoned')
        tombstone = self.ledger.get(self.key)
        self.assertEqual(tombstone['state'], 'abandoned')
        self.assertEqual(tombstone['first_failure'], expected_first)
        self.assertEqual(tombstone['last_failure'], expected_last)
        self.assertEqual(len(self.client.updates), 1)

    # 5. 清空字段的各种表示：null、空字符串、空数组、缺字段
    def test_empty_field_representations_equivalent_in_writer_and_inspect_resolve(self):
        empty_variants = [
            {'Sample': self.identity, 'Output': 'v2'},  # omitted Failure
            {'Sample': self.identity, 'Output': 'v2', 'Failure': None},  # null
            {'Sample': self.identity, 'Output': 'v2', 'Failure': ''},  # empty string
            {'Sample': self.identity, 'Output': 'v2', 'Failure': []},  # empty list
            {'Sample': self.identity, 'Output': 'v2', 'Failure': [{'type': 'text', 'text': ''}]},  # empty rich text
        ]
        # When pending wants Failure cleared ('') and Output='v2', all remote empty representations match pending
        self.seed_update_intent(
            pending={'Sample': self.identity, 'Output': 'v2', 'Failure': ''},
            prior={'Sample': self.identity, 'Output': 'v1', 'Failure': 'old-err'},
            ticket='tkt-clear-01',
        )
        for remote_fields in empty_variants:
            self.client.remote['fields'] = copy.deepcopy(remote_fields)
            report = self.call_recover()
            self.assertEqual(report['classification'], 'matches_pending', f'Failed for {remote_fields}')
            # Writer also sees zero delta
            self.assertEqual(
                self.writer.update('recSynth01', self.identity, {'Output': 'v2', 'Failure': ''})['writes'],
                0,
            )

        # Conversely, when prior had an empty Failure and pending sets Failure='new-err',
        # all remote empty representations of Failure with Output='v1' match prior
        self.seed_update_intent(
            pending={'Sample': self.identity, 'Output': 'v1', 'Failure': 'new-err'},
            prior={'Sample': self.identity, 'Output': 'v1', 'Failure': ''},
            ticket='tkt-clear-02',
        )
        for remote_fields in empty_variants:
            rf = copy.deepcopy(remote_fields)
            rf['Output'] = 'v1'
            self.client.remote['fields'] = rf
            report = self.call_recover()
            self.assertEqual(report['classification'], 'matches_prior', f'Failed prior for {rf}')

    # 6. 1254002 已提交 / 未提交，以及回读丢失
    def test_1254002_committed_uncommitted_and_lost_readback_recovery(self):
        # Scenario 1: 1254002 Committed on remote, then inspected & resolved to 'written'
        proj = self.new_projection()
        proj.publish(self.identity, {'Sample': self.identity, 'Output': 'v1', 'Failure': ''})
        self.client.fail_write = 'BASE_RESULTS_REQUEST_UNKNOWN_FEISHU_CODE_1254002'
        self.client.commit_on_fail = True
        with self.assertRaises(RuntimeFault):
            proj.publish(self.identity, {'Sample': self.identity, 'Output': 'v2-committed', 'Failure': ''})
        self.assertEqual(self.ledger.get(self.key)['state'], 'update_intent')
        tkt1 = self.ledger.get(self.key)['update_ticket']
        self.assertEqual(self.call_recover()['classification'], 'matches_pending')
        res1 = self.call_recover(resolve=True, owner_confirm=True, ticket=tkt1)
        self.assertEqual(res1['decision'], 'written')
        self.assertEqual(self.ledger.get(self.key)['state'], 'created')

        # Scenario 2: 1254002 Uncommitted on remote -> inspect is matches_prior
        self.client.commit_on_fail = False
        with self.assertRaises(RuntimeFault):
            proj.publish(self.identity, {'Sample': self.identity, 'Output': 'v3-uncommitted', 'Failure': ''})
        self.assertEqual(self.ledger.get(self.key)['state'], 'update_intent')
        self.assertEqual(self.call_recover()['classification'], 'matches_prior')

        # Scenario 3: PUT succeeded (committed), but immediate readback GET failed (lost readback),
        # and during stopped inspect remote read also fails or record is missing -> 'neither'
        self.client.fail_write = None
        self.ledger.put(self.key, {'state': 'created', 'target': [self.copy_base, self.table], 'record_id': 'recSynth01', 'ticket': 't0'})
        self.client.remote['fields']['Output'] = 'v2-committed'
        self.client.fail_read_after_put = 'FEISHU_READ_TIMEOUT'
        with self.assertRaisesRegex(RuntimeFault, 'WRITE_READBACK_UNKNOWN'):
            proj.publish(self.identity, {'Sample': self.identity, 'Output': 'v4-readback-lost', 'Failure': ''})
        self.assertEqual(self.ledger.get(self.key)['state'], 'update_intent')
        tkt3 = self.ledger.get(self.key)['update_ticket']

        # While read is still failing, inspect classifies as 'neither' and resolve refuses
        self.assertEqual(self.call_recover()['classification'], 'neither')
        self.assertEqual(
            self.call_recover(resolve=True, owner_confirm=True, ticket=tkt3)['decision'],
            'refused',
        )
        # Once read recovers, inspect sees matches_pending and resolve marks it written without extra PUT
        self.client.fail_read = None
        self.client.fail_read_after_put = None
        self.assertEqual(self.call_recover()['classification'], 'matches_pending')
        self.assertEqual(
            self.call_recover(resolve=True, owner_confirm=True, ticket=tkt3)['decision'],
            'written',
        )
        self.assertEqual(len(self.client.updates), 3)

    # 7. 回执里不能出现 token、URL、原始响应
    def test_receipts_and_reports_never_leak_tokens_urls_or_raw_responses(self):
        secret_token = 't-secret_bearer_token_999'
        secret_url = 'https://open.feishu.cn/open-apis/bitable/v1/apps/basSecretUrl/tables/tblSecretUrl/records/recSynth01'
        secret_raw_body = 'RAW_UPSTREAM_RESPONSE_BODY_WITH_PRIVATE_CUSTOMER_COPY'

        proj = self.new_projection()
        proj.publish(self.identity, {'Sample': self.identity, 'Output': 'clean-prior', 'Failure': ''})

        def failing_put(*_):
            return request(
                '/bitable/v1/apps/basSecretUrl/tables/tblSecretUrl/records/recSynth01?token=' + secret_token,
                b'{"fields":{"Output":"secret-task-body"}}',
                secret_token,
                method='PUT',
            )

        self.client.update_record = failing_put
        with patch('video_factory.feishu_results_client.build_opener') as opener:
            raw_json = json.dumps({
                'code': 1254002,
                'msg': f'fail at {secret_url} with {secret_token} and {secret_raw_body}',
            }).encode('utf-8')
            opener.return_value.open.side_effect = HTTPError(
                secret_url,
                500,
                secret_raw_body,
                {'X-Tt-Logid': '20261125004212ABCDEF123456'},
                io.BytesIO(raw_json),
            )
            with self.assertRaises(RuntimeFault) as ctx:
                proj.publish(self.identity, {'Sample': self.identity, 'Output': 'secret-pending-text', 'Failure': ''})

        diag = failure_context(ctx.exception)
        row = self.ledger.get(self.key)
        tkt = row['update_ticket']
        inspect_report = self.call_recover()
        resolve_receipt = self.call_recover(resolve=True, owner_confirm=True, ticket=tkt)
        stored_receipts = self.ledger.all_receipts()

        combined_serialized = json.dumps({
            'diag': diag,
            'first_failure': row.get('first_failure'),
            'last_failure': row.get('last_failure'),
            'inspect_report': inspect_report,
            'resolve_receipt': resolve_receipt,
            'stored_receipts': stored_receipts,
        })

        for forbidden in (
            secret_token,
            secret_url,
            'open.feishu.cn',
            'basSecretUrl',
            'tblSecretUrl',
            secret_raw_body,
            'secret-task-body',
            'secret-pending-text',
            'clean-prior',
        ):
            self.assertNotIn(forbidden, combined_serialized)

        self.assertEqual(row['first_failure']['feishu_code'], 1254002)
        self.assertEqual(row['first_failure']['request_id'], '20261125004212ABCDEF123456')


if __name__ == '__main__':
    unittest.main()
