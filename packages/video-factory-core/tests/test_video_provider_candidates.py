"""Offline vendor-contract fixtures; these are not paid provider canaries."""
import unittest
from unittest.mock import patch

from video_factory.h3_provider import ProviderRejected

from video_factory.runtime_store import RuntimeFault
from video_factory.video_provider_candidates import CandidateVideoProvider, image_body


H3_BODY = {'model': 'MiniMax-H3', 'duration': 5, 'content': [
    {'type': 'text', 'text': 'fixture'},
    {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,YWJj'}},
]}


class CandidateVideoProviderTests(unittest.TestCase):
    def test_kling_region_and_model_bind_both_submit_and_poll(self):
        for region, host in [('beijing', 'api-beijing.klingai.com'),
                             ('singapore', 'api-singapore.klingai.com')]:
            provider = CandidateVideoProvider('kling', region=region, model='kling-3.0-turbo')
            with patch('video_factory.video_provider_candidates.opener') as network:
                response = network.return_value.open.return_value
                response.__enter__.return_value = response
                response.status = 200
                response.read.return_value = b'{"code":0,"data":{"id":"task-12345"}}'
                self.assertEqual(provider.submit({}, 'synthetic-key'), 'task-12345')
                self.assertEqual(network.return_value.open.call_args.args[0].full_url,
                                 'https://' + host + '/image-to-video/kling-3.0-turbo')
                response.read.return_value = b'{"code":0,"data":[{"id":"task-12345","status":"processing"}]}'
                self.assertEqual(provider.poll('task-12345', 'synthetic-key')['status'], 'running')
                self.assertEqual(network.return_value.open.call_args.args[0].full_url,
                                 'https://' + host + '/tasks?task_ids=task-12345')
                self.assertEqual(network.return_value.open.call_count, 2)

    def test_invalid_kling_profile_never_constructs_network_request(self):
        with patch('video_factory.video_provider_candidates.opener') as network:
            for options in ({'region': 'https://example.com'}, {'region': ''},
                            {'model': '../tasks'}, {'model': ''}):
                with self.subTest(options=options), self.assertRaises(RuntimeFault):
                    CandidateVideoProvider('kling', **options)
            with self.assertRaises(RuntimeFault):
                CandidateVideoProvider('wan', region='beijing')
            network.assert_not_called()

    def test_kling_auth_failure_does_not_switch_region(self):
        provider = CandidateVideoProvider('kling', region='beijing')
        with patch('video_factory.video_provider_candidates.opener') as network:
            response = network.return_value.open.return_value
            response.__enter__.return_value = response
            response.status = 401
            response.read.return_value = b'{}'
            with self.assertRaises(ProviderRejected):
                provider.submit({}, 'synthetic-key')
            self.assertEqual(network.return_value.open.call_count, 1)
            self.assertTrue(network.return_value.open.call_args.args[0].full_url.startswith(
                'https://api-beijing.klingai.com/'))

    def test_first_frame_formats_are_distinct(self):
        seedance = image_body('seedance', 'fixture prompt', H3_BODY)
        self.assertEqual(seedance['content'][1]['image_url']['url'], 'data:image/png;base64,YWJj')
        wan = image_body('wan', 'fixture prompt', H3_BODY)
        self.assertEqual(wan['input']['media'][0]['type'], 'first_frame')
        kling = image_body('kling', 'fixture prompt', H3_BODY)
        self.assertEqual(kling['contents'][1]['url'], 'YWJj')

    def test_multiple_references_cannot_be_silently_discarded(self):
        body = {**H3_BODY, 'content': H3_BODY['content'] + [H3_BODY['content'][1]]}
        for route in ('seedance', 'wan', 'kling'):
            with self.subTest(route=route), self.assertRaisesRegex(RuntimeFault, 'EXACTLY_ONE'):
                image_body(route, 'fixture prompt', body)

    def test_vendor_receipts_are_verified(self):
        fixtures = [
            ('seedance', {'id': 'cgt-20261016-test'}, 'cgt-20261016-test'),
            ('wan', {'output': {'task_id': 'aabbcc-112233'}}, 'aabbcc-112233'),
            ('kling', {'code': 0, 'data': {'id': '998877665544'}}, '998877665544'),
        ]
        for route, response, expected in fixtures:
            with self.subTest(route=route):
                provider = CandidateVideoProvider(route)
                provider._call = lambda *args, response=response: (200, response)
                self.assertEqual(provider.submit({}, 'synthetic-key'), expected)
                provider._call = lambda *args: (200, {})
                with self.assertRaisesRegex(RuntimeFault, 'UNCERTAIN'):
                    provider.submit({}, 'synthetic-key')

    def test_poll_rejects_mismatched_task(self):
        seedance = CandidateVideoProvider('seedance')
        seedance._call = lambda *args: (200, {'id': 'wrong-id', 'model': seedance.model,
                                              'status': 'succeeded',
                                              'content': {'video_url': 'https://example.com/video.mp4'}})
        with self.assertRaisesRegex(RuntimeFault, 'UNVERIFIED'):
            seedance.poll('cgt-20261016-test', 'synthetic-key')

    def test_error_with_task_receipt_is_not_safe_to_retry(self):
        fixtures = [('seedance', {'id': 'accepted-task'}),
                    ('wan', {'output': {'task_id': 'accepted-task'}}),
                    ('kling', {'code': 1, 'data': {'id': 'accepted-task'}})]
        for route, response in fixtures:
            for status in (400, 401, 429, 500):
                with self.subTest(route=route, status=status):
                    provider = CandidateVideoProvider(route)
                    provider._call = lambda *args: (status, response)
                    with self.assertRaises(RuntimeFault) as caught:
                        provider.submit({}, 'synthetic-key')
                    self.assertNotIsInstance(caught.exception, ProviderRejected)
                    self.assertIn('UNCERTAIN', str(caught.exception))

    def test_explicit_rejection_without_task_remains_rejected(self):
        for route in ('seedance', 'wan', 'kling'):
            provider = CandidateVideoProvider(route)
            provider._call = lambda *args: (401, {})
            with self.assertRaises(ProviderRejected):
                provider.submit({}, 'synthetic-key')

    def test_malformed_provider_envelopes_are_bounded_faults(self):
        for route, response in [('wan', {'output': []}), ('kling', {'data': []})]:
            provider = CandidateVideoProvider(route)
            provider._call = lambda *args: (200, response)
            with self.assertRaisesRegex(RuntimeFault, 'UNCERTAIN'):
                provider.submit({}, 'synthetic-key')
        fixtures = [('wan', {'output': []}), ('kling', {'code': 0, 'data': [None]}),
                    ('seedance', {'id': 'task-12345', 'model': 'doubao-seedance-2-5-260628',
                                  'status': 'succeeded', 'content': []})]
        for route, response in fixtures:
            provider = CandidateVideoProvider(route)
            provider._call = provider._call_kling_query = lambda *args: (200, response)
            with self.assertRaisesRegex(RuntimeFault, 'UNVERIFIED'):
                provider.poll('task-12345', 'synthetic-key')

    def test_kling_query_rejects_invalid_secret_before_network(self):
        provider = CandidateVideoProvider('kling')
        with patch('video_factory.video_provider_candidates.opener') as network:
            for key in ('Bearer key', 'key\nvalue', 'key\x00value', None):
                with self.subTest(key=repr(key)), self.assertRaisesRegex(RuntimeFault, 'KEY_FORMAT'):
                    provider.poll('task-12345', key)
            network.assert_not_called()

    def test_receipts_do_not_accept_local_or_credentialed_media_urls(self):
        provider = CandidateVideoProvider('seedance')
        for url in ('https://127.0.0.1/v.mp4', 'https://[::1]/v.mp4',
                    'https://localhost/v.mp4', 'https://user:pass@example.com/v.mp4',
                    'https://example.com:8080/v.mp4', 'https://example.com/\nv.mp4'):
            with self.subTest(url=url):
                provider._call = lambda *args: (200, {'id': 'task-12345', 'model': provider.model,
                    'status': 'succeeded', 'content': {'video_url': url}})
                with self.assertRaisesRegex(RuntimeFault, 'MEDIA_URL_UNVERIFIED'):
                    provider.poll('task-12345', 'synthetic-key')

    def test_invalid_first_frame_shapes_and_duration_do_not_escape(self):
        for content in (None, {}, [None, None], [{}, {'type': 'image_url', 'image_url': None}]):
            with self.subTest(content=content), self.assertRaises(RuntimeFault):
                image_body('wan', 'fixture prompt', {**H3_BODY, 'content': content})
        for duration in (True, '5', None, -1, 1000):
            with self.subTest(duration=duration), self.assertRaises(RuntimeFault):
                image_body('wan', 'fixture prompt', {**H3_BODY, 'duration': duration})

    def test_unknown_route_fails_closed(self):
        with self.assertRaisesRegex(RuntimeFault, 'NOT_SUPPORTED'):
            CandidateVideoProvider('custom-url')


if __name__ == '__main__':
    unittest.main()
