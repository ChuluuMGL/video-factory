"""Keep conditional mutation guards consistent across ledger backends."""
import unittest
from unittest.mock import Mock

from video_factory.postgres_store import Connection


class PostgresCursorTests(unittest.TestCase):
    def test_conditional_update_exposes_actual_affected_rows(self):
        raw = Mock()
        raw.execute.return_value.rowcount = 1
        db = Connection(raw)
        updated = db.execute('UPDATE tasks SET state=? WHERE state=?', ('ready', 'failed'))
        self.assertEqual(updated.rowcount, 1)
        raw.execute.assert_called_once_with(
            'UPDATE tasks SET state=%s WHERE state=%s', ('ready', 'failed'))
        raw.execute.return_value.rowcount = 0
        self.assertEqual(updated.rowcount, 0)
