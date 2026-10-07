import io
import json
import os
import unittest
import uuid
from unittest.mock import patch, MagicMock

from video_factory.feishu_results_client import ResultClient, request, BLOCK_SIZE
from video_factory.runtime_store import RuntimeFault


class ResultClientTests(unittest.TestCase):
    def test_application_auth_is_fixed_origin_and_secret_not_receipt(self):
        response = MagicMock(); response.__enter__.return_value = response
        response.status = 200; response.read.return_value = json.dumps({'code': 0, 'expire': 7200, 'tenant_access_token': 'synthetic-application-token'}).encode()
        with patch('video_factory.feishu_results_client.build_opener') as opener:
            opener.return_value.open.return_value = response
            client = ResultClient.application('cli_fixture', 'synthetic-secret')
            req = opener.return_value.open.call_args.args[0]
            self.assertEqual(req.full_url, 'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal')
            self.assertEqual(req.method, 'POST')
            self.assertNotIn('Authorization', req.headers)
            self.assertEqual(client.token, 'synthetic-application-token')

    def test_invalid_or_failed_transport_redacts_upstream(self):
        with patch('video_factory.feishu_results_client.build_opener') as opener:
            opener.return_value.open.side_effect = OSError('secret upstream diagnostic')
            with self.assertRaisesRegex(RuntimeFault, '^BASE_RESULTS_REQUEST_UNKNOWN_READ_STATUS$'):
                request('/drive/v1/medias/upload_finish', b'{}', 'synthetic-token')
        with patch.dict(os.environ, {'VF_WORKER_EGRESS': '1', 'VF_CONTAINER_MODE': '0'}):
            with self.assertRaisesRegex(RuntimeFault, 'EGRESS_REQUIRES_CONTAINER'): request('/unused', b'{}')

    def test_record_request_is_append_with_same_uuid_and_consistency(self):
        client = ResultClient('synthetic-token'); ticket = str(uuid.uuid4())
        with patch.object(client, 'post', return_value={'records': [{'record_id': 'recFixture'}]}) as post:
            for _ in range(2): self.assertEqual(client.append('bascnFixture', 'tblFixture', {'同步标识': 'event'}, ticket), 'recFixture')
            self.assertEqual(post.call_args_list[0], post.call_args_list[1])
            self.assertIn('client_token='+ticket, post.call_args.args[0])
            self.assertIn('ignore_consistency_check=false', post.call_args.args[0])

    def test_search_uses_exact_event_and_refuses_duplicate_or_partial_read(self):
        client = ResultClient('synthetic-token')
        with patch.object(client, 'post', return_value={'items': [], 'has_more': False}) as post:
            self.assertEqual(client.find('bascnFixture', 'tblFixture', 'event'), [])
            self.assertEqual(post.call_args.args[1]['filter']['conditions'][0]['value'], ['event'])
        for value in ({'items': [], 'has_more': True}, {'items': [{}, {}], 'has_more': False}):
            with patch.object(client, 'post', return_value=value):
                with self.assertRaisesRegex(RuntimeFault, 'DUPLICATE_CONFLICT'): client.find('bascnFixture', 'tblFixture', 'event')

    def test_upload_parent_chunk_size_and_multipart_checksum(self):
        client = ResultClient('synthetic-token')
        with patch.object(client, 'post', return_value={'upload_id': 'uploadFixture', 'block_size': BLOCK_SIZE, 'block_num': 2}) as post:
            client.upload_prepare('bascnFixture', 'task-v1.mp4', BLOCK_SIZE+1)
            self.assertEqual(post.call_args.args[1]['parent_type'], 'bitable_file')
            self.assertEqual(post.call_args.args[1]['parent_node'], 'bascnFixture')
        with patch('video_factory.feishu_results_client.request', return_value={'code': 0, 'data': {}}) as send:
            result = client.upload_part('uploadFixture', 1, b'fixture')
            self.assertEqual(result['size'], 7)
            self.assertIn(b'name="checksum"', send.call_args.args[1])
            self.assertNotIn(b'synthetic-token', send.call_args.args[1])
            self.assertEqual(send.call_args.args[0], '/drive/v1/medias/upload_part')
