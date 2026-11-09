import json
import unittest
from unittest.mock import patch, MagicMock
import test_script_jobs as fixtures
from video_factory.script_provider import ScriptProvider
from video_factory.script_receipts import capture
from video_factory.runtime_store import RuntimeFault,RuntimeStore
from video_factory.feishu_bridge import meta
from video_factory.script_jobs import key

class ReceiptTests(unittest.TestCase):
    def response(self,raw,status=200):
        response=MagicMock();response.__enter__.return_value=response
        response.status=status;response.read.return_value=raw;return response

    def test_failure_classes_have_sanitized_receipts_without_retries(self):
        usage={'prompt_tokens':123,'completion_tokens':2048,'total_tokens':2171,'secret':'must-discard'}
        truncated=json.dumps({'id':'private-provider-id','usage':usage,'choices':[
            {'finish_reason':'length','message':{'content':'private draft synthetic-key'}}]}).encode()
        cases=[(TimeoutError('private-token'),'SCRIPT_NETWORK_FAILED','NETWORK_ERROR',None),
               (self.response(truncated),'SCRIPT_MODEL_TRUNCATED','MODEL_LENGTH',2048),
               (self.response(b'private draft not JSON'),'SCRIPT_FORMAT_INVALID','INVALID_JSON',None)]
        for response,code,reason,tokens in cases:
            receipts=[]
            with self.subTest(code=code),capture(receipts.append),patch('video_factory.script_provider.build_opener') as opener:
                if isinstance(response,Exception):opener.return_value.open.side_effect=response
                else:opener.return_value.open.return_value=response
                with self.assertRaisesRegex(RuntimeFault,code):ScriptProvider().generate({},'synthetic','','synthetic-key')
                self.assertEqual(opener.return_value.open.call_count,1)
            last=receipts[-1];self.assertEqual(last['reason'],reason)
            self.assertIsInstance(last['elapsed_ms'],int)
            if tokens:self.assertEqual(last['usage']['completion_tokens'],tokens);self.assertTrue(last['truncated'])
            for private in ('private draft','synthetic-key','private-token','private-provider-id','must-discard'):
                self.assertNotIn(private,json.dumps(receipts))

    def test_receipt_storage_failure_does_not_retry_post(self):
        with capture(lambda r:(_ for _ in ()).throw(OSError('disk failure'))),patch('video_factory.script_provider.build_opener') as opener:
            opener.return_value.open.return_value=self.response(b'{}')
            with self.assertRaises(RuntimeFault):ScriptProvider().generate({},'synthetic','','synthetic-key')
            self.assertEqual(opener.return_value.open.call_count,1)

class DurableReceiptTests(unittest.TestCase):
    setUp=fixtures.ScriptTests.setUp
    tearDown=fixtures.ScriptTests.tearDown
    queue=fixtures.ScriptTests.queue
    step=fixtures.ScriptTests.step

    def test_response_persisted_before_decoder_failure_and_after_reopen(self):
        from video_factory.script_receipts import observe
        self.queue()
        def failing(*args):
            observe(reason='RESPONSE_RECEIVED',status=200,elapsed_ms=21,raw=b'private content',
                    value={'usage':{'prompt_tokens':20,'completion_tokens':10,'total_tokens':30}})
            observe(reason='STRUCTURE_INVALID')
            raise RuntimeFault('SCRIPT_VALIDATION_FAILED')
        with patch.object(self.provider,'generate',side_effect=failing) as provider:
            self.assertEqual(self.step()['error'],'SCRIPT_VALIDATION_FAILED')
            store=RuntimeStore(self.f.store.root)
            with store.connect() as db:
                job=meta(db,key(self.f.project,'generated_one',1));history=meta(db,'script:receipt:'+job['plan_sha256'])
            self.assertEqual(history[0]['http_status'],200);self.assertEqual(history[0]['usage']['total_tokens'],30)
            self.assertEqual(history[1]['reason'],'STRUCTURE_INVALID');self.assertNotIn('private content',json.dumps(history))
            self.step();self.assertEqual(provider.call_count,1)
