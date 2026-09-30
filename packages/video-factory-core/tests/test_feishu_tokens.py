"""Regress the token length observed during real ECS device authorization."""
import unittest
from unittest.mock import patch

from video_factory.feishu_client import FeishuClient, MAX_USER_TOKEN_LENGTH
from video_factory.feishu_oauth import DeviceOAuth, SCOPES
from video_factory.feishu_provision import ProvisionClient
from video_factory.runtime_store import RuntimeFault


class FeishuTokenTests(unittest.TestCase):
    def exchange(self, token):
        client = DeviceOAuth('cli_fixture', 'synthetic-secret')
        response = {'code': 0, 'access_token': token, 'expires_in': 7199,
                    'scope': ' '.join(SCOPES), 'refresh_token': 'must-discard'}
        with patch.object(client, 'post', return_value=response):
            return client.poll('synthetic-device')

    def test_real_length_and_upper_bound_reach_read_and_create_clients(self):
        for length in (6599, MAX_USER_TOKEN_LENGTH):
            with self.subTest(length=length):
                token = 'x'*length
                result = self.exchange(token)
                self.assertEqual(result, {'status': 'authorized', 'access_token': token, 'expires_in': 7199})
                for client in (FeishuClient, ProvisionClient):
                    self.assertEqual(client(result['access_token']).token, token)

    def test_empty_whitespace_and_oversized_tokens_fail_without_echoing(self):
        for token in (None, '', 'x'*(MAX_USER_TOKEN_LENGTH+1), 'private\r\nAuthorization: other', 'private token'):
            for operation in (self.exchange, FeishuClient, ProvisionClient):
                with self.subTest(kind=operation, length=len(token) if token is not None else None):
                    with self.assertRaises(RuntimeFault) as caught:
                        operation(token)
                    self.assertNotIn('private', str(caught.exception))

    def test_larger_access_token_limit_does_not_expand_device_or_secret_limits(self):
        with self.assertRaises(RuntimeFault):
            DeviceOAuth('cli_fixture', 'x'*4097)
        client = DeviceOAuth('cli_fixture', 'synthetic-secret')
        with patch.object(client, 'post') as post:
            with self.assertRaises(RuntimeFault):
                client.poll('x'*4097)
            post.assert_not_called()
