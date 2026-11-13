import copy
import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

from video_factory.runtime_store import RuntimeFault
from video_factory.script_week_io import ReadOnlyWeekSource, CopyOnlyWeekWriter


class WeekIOTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.source = ReadOnlyWeekSource(self.client, base='basProductionFixture',
                                        tables={'tblTasks': ['Task', 'SKU'], 'tblSkuFixture': ['Name']})

    def test_machine_only_ledger_still_rejects_unlisted_fields(self):
        writer = CopyOnlyWeekWriter(self.client, source_base='basSource', copy_base='basCopy',
            table='tblLedger', machine_fields=['Request', 'Cost'], human_fields=[], identity_field='Request')
        self.assertEqual(writer._patch({'Request':'one', 'Cost':'0'}), {'Request':'one', 'Cost':'0'})
        with self.assertRaisesRegex(RuntimeFault, 'WEEK_COPY_FIELD_DENIED'):
            writer._patch({'Request':'one', 'Review':'approved'})
        self.client.post.assert_not_called()

    def test_complete_get_pagination_filters_private_fields(self):
        self.client.get.side_effect = [
            {'items': [{'record_id': 'recOneFixture', 'fields': {'Task': 'A', 'Email': 'hidden'}}],
             'has_more': True, 'page_token': 'next'},
            {'items': [{'record_id': 'recTwoFixture', 'fields': {'Task': 'B'}}], 'has_more': False}]
        rows = self.source.records('tblTasks')
        self.assertEqual(len(rows), 2); self.assertNotIn('Email', rows[0]['fields'])
        for call in self.client.get.call_args_list:
            query = parse_qs(urlsplit(call.args[0]).query)
            self.assertIn('field_names', query)
            self.assertEqual(query['page_size'], ['20'])
            self.assertEqual(query['automatic_fields'], ['false'])
        self.client.post.assert_not_called(); self.client.update_record.assert_not_called()

    def test_repeated_page_or_record_never_returns_partial_scan(self):
        for pages in ([{'items': [], 'has_more': True, 'page_token': 'same'}]*2,
                      [{'items': [{'record_id': 'recOneFixture', 'fields': {}}],
                        'has_more': True, 'page_token': 'a'},
                       {'items': [{'record_id': 'recOneFixture', 'fields': {}}], 'has_more': False}]):
            self.client.get.side_effect = pages
            with self.assertRaises(RuntimeFault): self.source.records('tblTasks')

    def test_page_bound_and_malformed_response_fail_closed(self):
        self.client.get.side_effect = [{'items': [], 'has_more': True, 'page_token': str(n)} for n in range(100)]
        with self.assertRaisesRegex(RuntimeFault, 'SCAN_INCOMPLETE'): self.source.records('tblTasks')
        self.client.get.side_effect = None
        self.client.get.return_value = {'items': [], 'has_more': 'false'}
        with self.assertRaisesRegex(RuntimeFault, 'PAGE_INVALID'): self.source.records('tblTasks')

    def test_oversized_page_rejected_without_partial_result(self):
        self.client.get.return_value = {'items': [{'record_id':'rec'+str(n), 'fields':{}} for n in range(21)], 'has_more':False}
        with self.assertRaisesRegex(RuntimeFault, 'PAGE_INVALID'):
            self.source.records('tblTasks')

    def test_other_tables_denied_before_network(self):
        for method, args in [(self.source.records, ('tblUnapproved',)),
                             (self.source.record, ('tblUnapproved', 'recOneFixture'))]:
            with self.assertRaisesRegex(RuntimeFault, 'TABLE_DENIED'): method(*args)
        self.client.get.assert_not_called()

    def test_record_identity_checked(self):
        self.client.get.return_value = {'record': {'record_id': 'recOther', 'fields': {}}}
        with self.assertRaisesRegex(RuntimeFault, 'RECORD_MISMATCH'):
            self.source.record('tblTasks', 'recOneFixture')

    def writer(self, **overrides):
        cfg = dict(source_base='basProductionFixture', copy_base='basCopyFixture', table='tblCopy',
                   machine_fields=['Sample', 'Candidate', 'Failure'], human_fields=['Review', 'Opinion'],
                   identity_field='Sample')
        cfg.update(overrides)
        return CopyOnlyWeekWriter(self.client, **cfg)

    def test_production_target_and_review_fields_denied(self):
        with self.assertRaisesRegex(RuntimeFault, 'COPY_TARGET'):
            self.writer(copy_base='basProductionFixture')
        with self.assertRaisesRegex(RuntimeFault, 'OWNERSHIP'):
            self.writer(machine_fields=['Sample', 'Review'])
        with self.assertRaisesRegex(RuntimeFault, 'FIELD_DENIED'):
            self.writer().append({'Sample': 'fixture', 'Review': 'approved'}, 'ticket')
        self.client.append.assert_not_called()

    def test_copy_readback_preserves_review_and_dedupes_update(self):
        remote = {'record_id': 'recCopy', 'fields': {'Sample': 'fixture', 'Review': 'approved', 'Opinion': 'human'}}
        self.client.record.side_effect = lambda *args: copy.deepcopy(remote)
        self.client.update_record.side_effect = lambda base,table,record,fields: remote['fields'].update(fields)
        writer = self.writer()
        self.assertEqual(writer.update('recCopy','fixture',{'Candidate':'draft'})['writes'],1)
        self.assertEqual(writer.update('recCopy','fixture',{'Candidate':'draft'})['writes'],0)
        self.assertEqual(remote['fields']['Review'],'approved')
        self.assertEqual(remote['fields']['Opinion'],'human')
        self.assertEqual(self.client.update_record.call_count,1)
        self.assertEqual(self.client.update_record.call_args.args[:2],('basCopyFixture','tblCopy'))

    def test_unknown_append_not_retried_and_identity_conflict_denied(self):
        self.client.append.side_effect = TimeoutError
        with self.assertRaises(TimeoutError): self.writer().append({'Sample':'fixture'},'ticket')
        self.assertEqual(self.client.append.call_count,1)
        self.client.record.return_value = {'record_id':'recCopy','fields':{'Sample':'another'}}
        with self.assertRaisesRegex(RuntimeFault,'READBACK_MISMATCH'):
            self.writer().update('recCopy','fixture',{'Candidate':'draft'})
        self.client.update_record.assert_not_called()

    def test_successful_append_requires_independent_readback(self):
        self.client.append.return_value = 'recCopy'
        self.client.record.return_value = {'record_id':'recCopy','fields':{'Sample':'fixture'}}
        self.assertEqual(self.writer().append({'Sample':'fixture'},'ticket')['writes'],1)
        self.client.record.return_value['fields']['Sample']='mismatch'
        with self.assertRaisesRegex(RuntimeFault,'READBACK_MISMATCH'):
            self.writer().append({'Sample':'fixture'},'another-ticket')


if __name__ == '__main__': unittest.main()
