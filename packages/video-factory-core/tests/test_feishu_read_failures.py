import io
import json
import socket
import ssl
import unittest
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError
from unittest.mock import patch
from video_factory.feishu_client import FeishuClient
from video_factory.runtime_store import RuntimeFault

class ReadFailureTests(unittest.TestCase):
    def test_transport_classification_no_retry_or_private_message(self):
        cases=[(TimeoutError('secret upstream url'),'TIMEOUT'),
               (URLError(TimeoutError('secret')),'TIMEOUT'),
               (URLError(socket.gaierror('secret')),'DNS_FAILED'),
               (ssl.SSLError('secret'),'TLS_FAILED'),
               (ConnectionResetError('secret'),'CONNECTION_FAILED'),
               (IncompleteRead(b'secret'),'TRUNCATED_RESPONSE'),
               (RuntimeError('secret'),'TRANSPORT_FAILED')]
        for error,code in cases:
            with self.subTest(code=code),patch('video_factory.feishu_client.build_opener') as o:
                o.return_value.open.side_effect=error
                with self.assertRaisesRegex(RuntimeFault,'^FEISHU_READ_'+code+'$'):
                    FeishuClient('synthetic-token').get('/fixture')
                self.assertEqual(o.return_value.open.call_count,1)
                self.assertEqual(o.return_value.open.call_args.kwargs['timeout'],30)

    def test_response_failures_are_distinct_from_authentication(self):
        cases=[(b'x'*(2*1024*1024+1),'RESPONSE_TOO_LARGE'),
               (b'{secret malformed','INVALID_JSON'),(b'[]','INVALID_RESPONSE'),
               (b'{"code":0,"data":null}','INVALID_RESPONSE'),
               (b'{"code":99991663,"msg":"secret"}','REJECTED_CODE_99991663')]
        for raw,code in cases:
            with self.subTest(code=code),patch('video_factory.feishu_client.build_opener') as o:
                response=o.return_value.open.return_value.__enter__.return_value
                response.status=200;response.read.return_value=raw
                with self.assertRaisesRegex(RuntimeFault,'^FEISHU_READ_'+code+'$'):
                    FeishuClient('synthetic-token').get('/fixture')
                self.assertEqual(o.return_value.open.call_count,1)

    def test_numeric_http_refusal_is_retained(self):
        with patch('video_factory.feishu_client.build_opener') as o:
            o.return_value.open.side_effect=HTTPError('private-url',403,'secret',{},io.BytesIO(b'{"code":91403,"msg":"secret"}'))
            with self.assertRaisesRegex(RuntimeFault,'^FEISHU_READ_REJECTED_CODE_91403$'):
                FeishuClient('synthetic-token').get('/fixture')

    def test_valid_response_unchanged(self):
        with patch('video_factory.feishu_client.build_opener') as o:
            response=o.return_value.open.return_value.__enter__.return_value
            response.status=200;response.read.return_value=b'{"code":0,"data":{"items":[]}}'
            self.assertEqual(FeishuClient('synthetic-token').get('/fixture'),{'items':[]})
