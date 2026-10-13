import json
import unittest
from urllib.error import HTTPError
from unittest.mock import patch,MagicMock
from video_factory.script_provider import ScriptProvider,MODEL
from video_factory.runtime_store import RuntimeFault

class ScriptProviderTests(unittest.TestCase):
    def test_authentication_reads_models_without_content_or_generation(self):
        response=MagicMock();response.__enter__.return_value=response;response.status=200
        response.read.return_value=json.dumps({'data':[{'id':MODEL}]}).encode()
        with patch('video_factory.script_provider.build_opener') as opener:
            opener.return_value.open.return_value=response
            result=ScriptProvider().authenticate('synthetic-key')
            request=opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url,'https://api.deepseek.com/models')
            self.assertEqual(request.get_method(),'GET');self.assertIsNone(request.data)
            self.assertEqual(opener.return_value.open.call_count,1)
            self.assertEqual(result['model_generation_requests'],0)
            self.assertNotIn('synthetic-key',str(result))

    def test_authentication_rejection_and_unavailable_model_fail_closed(self):
        with patch('video_factory.script_provider.build_opener') as opener:
            opener.return_value.open.side_effect=HTTPError('https://secret.invalid',401,'private detail',{},None)
            with self.assertRaisesRegex(RuntimeFault,'^SCRIPT_AUTH_REJECTED$'):
                ScriptProvider().authenticate('synthetic-key')
            self.assertEqual(opener.return_value.open.call_count,1)
        response=MagicMock();response.__enter__.return_value=response;response.status=200
        response.read.return_value=b'{"data":[]}'
        with patch('video_factory.script_provider.build_opener') as opener:
            opener.return_value.open.return_value=response
            with self.assertRaisesRegex(RuntimeFault,'^SCRIPT_CONFIGURED_MODEL_UNAVAILABLE$'):
                ScriptProvider().authenticate('synthetic-key')

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
    def test_http_rejection_is_classified_without_response_or_retry(self):
        for status,code in ((401,'SCRIPT_AUTH_REJECTED'),(402,'SCRIPT_BALANCE_REJECTED'),
                            (429,'SCRIPT_RATE_LIMITED'),(503,'SCRIPT_SUBMISSION_UNCERTAIN_NO_RETRY')):
            with self.subTest(status=status),patch('video_factory.script_provider.build_opener') as opener:
                opener.return_value.open.side_effect=HTTPError('https://secret.invalid',status,'private detail',{},None)
                with self.assertRaises(RuntimeFault) as raised:
                    ScriptProvider().generate({},'brief','','synthetic-key')
                self.assertEqual(str(raised.exception),code)
                self.assertEqual(opener.return_value.open.call_count,1)
