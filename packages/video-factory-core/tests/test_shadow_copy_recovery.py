import sqlite3
import unittest
from unittest.mock import Mock

from video_factory.feishu_bridge import meta, save
from video_factory.shadow_copy_recovery import recover
from video_factory.script_week_projection import projection_key


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        self.project, self.base, self.table = 'fixture', 'basCopy', 'tblCopy'
        self.key = projection_key(self.project, self.base, self.table, 'fixture')
        save(self.db, 'script:week:fixture', {'state': 'closed'})
        save(self.db, 'script:trial:fixture', {'enabled': False})
        self.row = {'state': 'update_intent', 'target': [self.base, self.table],
                    'record_id': 'recFixture', 'update_ticket': 'ticket',
                    'pending_fields': {'ID': 'fixture', 'Output': 'new'},
                    'prior_fields': {'ID': 'fixture', 'Output': [{'type': 'text', 'text': 'old'}]},
                    'first_failure': {'error': 'FIRST'}, 'last_failure': {'error': 'LAST'}}
        save(self.db, self.key, self.row)
        self.record = Mock(return_value={'record_id': 'recFixture',
                                        'fields': {'ID': 'fixture', 'Output': 'old'}})
        self.find = Mock(return_value=[])

    def call(self, **kwargs):
        return recover(self.db, project=self.project, base=self.base, table=self.table,
                       identity='fixture', identity_field='ID', record=self.record,
                       find=self.find, **kwargs)

    def test_inspect_and_abandon_preserve_failures(self):
        report = self.call()
        self.assertEqual(report['classification'], 'matches_prior')
        self.assertNotIn('old', str(report))
        self.assertEqual(meta(self.db, self.key), self.row)
        receipt = self.call(resolve=True, owner_confirm=True, ticket='ticket')
        self.assertEqual(receipt['decision'], 'abandoned')
        row = meta(self.db, self.key)
        self.assertEqual(row['state'], 'abandoned')
        for key in ('first_failure', 'last_failure'):
            self.assertEqual(row[key], self.row[key])
        self.assertEqual(meta(self.db, 'script:week-projection-resolution:' +
                              receipt['receipt_id']), receipt)

    def test_owner_and_ticket_required_before_remote_read(self):
        for confirm, ticket in ((False, 'ticket'), (True, 'wrong')):
            receipt = self.call(resolve=True, owner_confirm=confirm, ticket=ticket)
            self.assertEqual(receipt['decision'], 'refused')
            self.assertEqual(meta(self.db, self.key), self.row)
        self.record.assert_not_called()

    def test_pending_written_and_neither_refused(self):
        fields = self.record.return_value['fields']
        fields['Output'] = 'unrelated'
        self.assertEqual(self.call(resolve=True, owner_confirm=True, ticket='ticket')['decision'],
                         'refused')
        fields['Output'] = 'new'
        self.assertEqual(self.call(resolve=True, owner_confirm=True, ticket='ticket')['decision'],
                         'written')
        self.assertEqual(meta(self.db, self.key)['state'], 'created')

    def test_append_absence_duplicates_and_legacy(self):
        row = dict(self.row, state='intent', ticket='append',
                   initial_fields=self.row['pending_fields'])
        save(self.db, self.key, row)
        self.assertEqual(self.call()['classification'], 'neither')
        row['prior_absent'] = True
        save(self.db, self.key, row)
        self.assertEqual(self.call()['classification'], 'matches_prior')
        self.find.return_value = [self.record.return_value] * 2
        self.assertEqual(self.call()['classification'], 'neither')
        self.find.return_value = [{'record_id': 'recFixture', 'fields': row['initial_fields']}]
        self.assertEqual(self.call(resolve=True, owner_confirm=True, ticket='append')['decision'],
                         'written')

    def test_missing_or_wrong_record_and_legacy_cannot_infer_prior(self):
        self.record.return_value['record_id'] = 'recWrong'
        self.assertEqual(self.call()['classification'], 'neither')
        self.record.return_value['record_id'] = 'recFixture'
        self.row.pop('prior_fields')
        save(self.db, self.key, self.row)
        self.assertEqual(self.call()['classification'], 'neither')
