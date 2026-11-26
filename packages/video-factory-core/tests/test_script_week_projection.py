from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from review_fixture import Fixture
from video_factory.script_week_projection import WeekProjection
from video_factory.runtime_store import RuntimeFault

class ProjectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.f = Fixture(Path(directory.name).resolve())
        self.addCleanup(self.f.close)

    def adapter(self):
        self.writer=Mock(base='basCopy',table='tblCopy',identity_field='Task')
        self.writer.append.return_value={'record_id':'recNew','writes':1}
        self.writer.update.return_value={'record_id':'recNew','writes':0}
        self.find=Mock(return_value=[])
        return WeekProjection(self.f.store,self.f.project,self.writer,self.find)
    def test_restart_does_not_append_twice(self):
        p=self.adapter();p.publish('sample',{'Task':'sample','Output':'one'})
        p.publish('sample',{'Task':'sample','Output':'two'})
        self.assertEqual(self.writer.append.call_count,1)
        self.find.assert_called_once_with('sample');self.assertEqual(self.writer.update.call_count,2)
    def test_unknown_append_reconciles_existing_but_never_resends(self):
        p=self.adapter();self.writer.append.side_effect=TimeoutError
        with self.assertRaises(TimeoutError):p.publish('sample',{'Task':'sample'})
        self.find.return_value = [{'record_id':'recNew'}]
        p.publish('sample',{'Task':'sample'})
        self.assertEqual(self.writer.append.call_count,1);self.assertEqual(self.find.call_count,2)
        self.writer._readback.assert_called_once_with('recNew',{'Task':'sample'})
    def test_missing_or_ambiguous_reconciliation_stays_blocked(self):
        p=self.adapter();self.writer.append.side_effect=TimeoutError
        with self.assertRaises(TimeoutError):p.publish('sample',{'Task':'sample'})
        for found in ([],[{'record_id':'recOne'},{'record_id':'recTwo'}]):
            self.find.return_value=found
            with self.assertRaisesRegex(RuntimeFault,'UNCERTAIN'):p.publish('sample',{'Task':'sample'})
        self.assertEqual(self.writer.append.call_count,1);self.writer.update.assert_not_called()
    def test_wrong_identity_refused_before_write(self):
        p=self.adapter()
        with self.assertRaises(RuntimeFault):p.publish('sample',{'Task':'other'})
        self.writer.append.assert_not_called()

if __name__=='__main__':unittest.main()
