import json
import unittest
from unittest.mock import patch,MagicMock
from video_factory.script_provider import ScriptProvider,MODEL
from video_factory.runtime_store import RuntimeFault

class ScriptProviderTests(unittest.TestCase):
    def call(self,value):
        response=MagicMock();response.__enter__.return_value=response;response.status=200
        response.read.return_value=json.dumps(value).encode()
        opening=MagicMock();opening.open.return_value=response
        with patch('video_factory.script_provider.build_opener',return_value=opening):
            result=ScriptProvider().generate({'name':'data'},'brief','feedback','synthetic-key')
        req=opening.open.call_args.args[0]
        self.assertEqual(req.full_url,'https://api.deepseek.com/chat/completions')
        self.assertEqual(opening.open.call_count,1)
        body=json.loads(req.data);self.assertEqual(body['model'],MODEL);self.assertNotIn('tools',body)
        self.assertNotIn('synthetic-key',str(result))
        return result
    def test_complete_text_and_receipt_only(self):
        result=self.call({'id':'receipt','choices':[{'finish_reason':'stop','message':{'content':'A script'}}]})
        self.assertEqual(result['script'],'A script')
    def test_truncation_and_unbounded_text_are_not_review_ready(self):
        for reason,text in [('length','partial'),('stop','x'*6501),('stop','')]:
            with self.assertRaisesRegex(RuntimeFault,'UNCERTAIN_NO_RETRY'):
                self.call({'id':'receipt','choices':[{'finish_reason':reason,'message':{'content':text}}]})
    def test_timeout_makes_one_post_only(self):
        with patch('video_factory.script_provider.build_opener') as opener:
            opener.return_value.open.side_effect=TimeoutError('secret must not leak')
            with self.assertRaisesRegex(RuntimeFault,'SCRIPT_SUBMISSION_UNCERTAIN_NO_RETRY'):
                ScriptProvider().generate({},'brief','','synthetic-key')
            self.assertEqual(opener.return_value.open.call_count,1)
