"""Offline vendor-contract fixtures; these are not paid provider canaries."""
import unittest

from video_factory.runtime_store import RuntimeFault
from video_factory.video_provider_candidates import CandidateVideoProvider, image_body


H3_BODY = {'model': 'MiniMax-H3', 'duration': 5, 'content': [
    {'type': 'text', 'text': 'fixture'},
    {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,YWJj'}},
]}


class CandidateVideoProviderTests(unittest.TestCase):
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

    def test_unknown_route_fails_closed(self):
        with self.assertRaisesRegex(RuntimeFault, 'NOT_SUPPORTED'):
            CandidateVideoProvider('custom-url')


if __name__ == '__main__':
    unittest.main()
