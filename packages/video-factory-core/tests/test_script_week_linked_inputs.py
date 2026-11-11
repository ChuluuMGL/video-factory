import copy
import unittest
from unittest.mock import Mock
from urllib.parse import urlsplit

from video_factory.runtime_store import RuntimeFault
from video_factory.script_week_io import LinkedWeekInputs


class LinkedInputTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.rows = {
            'recTask': {'record_id': 'recTask', 'fields': {
                'Brief': 'approved fixture brief', 'SKU': ['recSkuFixture'],
                'Model': [{'id': 'recModel'}],
                'Reference': {'record_ids': ['recReference']},
                'Email': 'must not leave projection'}},
            'recSkuFixture': {'record_id': 'recSkuFixture', 'fields': {'Name': 'fixture SKU'}},
            'recModel': {'record_id': 'recModel', 'fields': {'Description': 'fixture model'}},
            'recReference': {'record_id': 'recReference', 'fields': {'Breakdown': 'fixture rhythm'}},
        }
        self.client.get.side_effect = self.read
        self.config = dict(base='basSourceFixture', task_table='tblTask',
                           task_fields=['Brief', 'SKU', 'Model', 'Reference'],
                           relations={
                               'SKU': {'table': 'tblSkuFixture', 'fields': ['Name']},
                               'Model': {'table': 'tblModel', 'fields': ['Description']},
                               'Reference': {'table': 'tblReference', 'fields': ['Breakdown']}})

    def read(self, path):
        record = urlsplit(path).path.split('/')[-1]
        return {'record': copy.deepcopy(self.rows[record])}

    def test_only_exact_linked_gets_stable_snapshot_with_provenance(self):
        result = LinkedWeekInputs(self.client, **self.config).snapshot('recTask')
        self.assertEqual(self.client.get.call_count, 8)
        self.assertNotIn('Email', result['inputs']['task']['fields'])
        self.assertEqual(result['provenance']['linked_records']['Model']['record'], 'recModel')
        self.assertEqual(len(result['input_sha256']), 64)
        for call in self.client.get.call_args_list:
            self.assertIn('/apps/basSourceFixture/', call.args[0])
            self.assertIn('/records/rec', call.args[0])
        self.client.post.assert_not_called()
        self.client.update_record.assert_not_called()

    def test_missing_multiple_or_url_link_rejected_before_material_read(self):
        for value in (None, [], ['recSkuFixture', 'recOther'], ['https://untrusted.invalid'],
                      {'record_ids': ['recSkuFixture'], 'base': 'basOther'}):
            self.client.reset_mock()
            self.rows['recTask']['fields']['SKU'] = value
            with self.assertRaises(RuntimeFault):
                LinkedWeekInputs(self.client, **self.config).snapshot('recTask')
            self.assertEqual(self.client.get.call_count, 1)

    def test_material_change_discards_snapshot_without_retry(self):
        counts = {}
        def read(path):
            record = urlsplit(path).path.split('/')[-1]
            counts[record] = counts.get(record, 0) + 1
            result = self.read(path)
            if record == 'recSkuFixture' and counts[record] == 2:
                result['record']['fields']['Name'] = 'changed'
            return result
        self.client.get.side_effect = read
        with self.assertRaisesRegex(RuntimeFault, 'LINKED_INPUT_CHANGED'):
            LinkedWeekInputs(self.client, **self.config).snapshot('recTask')
        self.assertEqual(counts['recSkuFixture'], 2)

    def test_native_link_envelope_accepts_exact_configured_table(self):
        self.rows['recTask']['fields']['SKU'] = [{
            'record_ids': ['recSkuFixture'], 'table_id': 'tblSkuFixture',
            'text': 'fixture', 'text_arr': ['fixture'], 'type': 'text'}]
        result = LinkedWeekInputs(self.client, **self.config).snapshot('recTask')
        self.assertEqual(result['provenance']['linked_records']['SKU']['record'],
                         'recSkuFixture')
        self.assertEqual(self.client.get.call_count, 8)

    def test_native_link_wrong_table_multiple_or_ambiguous_ids_rejected(self):
        for item in (
            {'record_ids': ['recSkuFixture'], 'table_id': 'tblOther'},
            {'record_ids': ['recSkuFixture', 'recOther'], 'table_id': 'tblSkuFixture'},
            {'record_ids': [], 'table_id': 'tblSkuFixture'},
            {'record_ids': ['recSkuFixture'], 'id': 'recOther'},
        ):
            self.client.reset_mock()
            self.rows['recTask']['fields']['SKU'] = [item]
            with self.assertRaises(RuntimeFault):
                LinkedWeekInputs(self.client, **self.config).snapshot('recTask')
            self.assertEqual(self.client.get.call_count, 1)

    def test_task_relinked_during_read_discards_snapshot(self):
        count = 0
        def read(path):
            nonlocal count
            result = self.read(path)
            if '/records/recTask?' in path:
                count += 1
                if count == 2: result['record']['fields']['SKU'] = ['recOther']
            return result
        self.client.get.side_effect = read
        with self.assertRaisesRegex(RuntimeFault, 'SOURCE_INPUT_CHANGED'):
            LinkedWeekInputs(self.client, **self.config).snapshot('recTask')

    def test_unapproved_or_task_table_relation_rejected_without_network(self):
        for relations in ({}, {'Unknown': {'table': 'tblSkuFixture', 'fields': ['Name']}},
                          {'SKU': {'table': 'tblTask', 'fields': ['Brief']}},
                          {'SKU': {'table': 'tblSkuFixture', 'fields': ['Name'], 'base': 'basOther'}}):
            config = {**self.config, 'relations': relations}
            with self.assertRaises(RuntimeFault): LinkedWeekInputs(self.client, **config)
        self.client.get.assert_not_called()

    def test_wrong_linked_record_and_network_failure_do_not_retry(self):
        self.rows['recSkuFixture']['record_id'] = 'recOther'
        with self.assertRaisesRegex(RuntimeFault, 'RECORD_MISMATCH'):
            LinkedWeekInputs(self.client, **self.config).snapshot('recTask')
        self.client.reset_mock()
        self.client.get.side_effect = TimeoutError
        with self.assertRaises(TimeoutError):
            LinkedWeekInputs(self.client, **self.config).snapshot('recTask')
        self.assertEqual(self.client.get.call_count, 1)


if __name__ == '__main__': unittest.main()
