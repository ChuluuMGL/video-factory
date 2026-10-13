"""Interrupted public dependencies must not pass verification or leak diagnostics."""
import hashlib
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError, URLError
from http.client import IncompleteRead

spec = importlib.util.spec_from_file_location('vf_release_installer', Path(__file__).resolve().parents[3] / 'distribution/install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class DependencyDownloadTest(unittest.TestCase):
    def setUp(self):
        self.raw = b'verified synthetic wheel'
        self.item = {'size': len(self.raw), 'sha256': hashlib.sha256(self.raw).hexdigest()}
        self.request = object()
        self.temp = io.BytesIO()

    def response(self, chunks):
        response = Mock(status=200)
        response.read.side_effect = chunks
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        return response

    def test_complete_bytes_are_verified_once(self):
        opener = Mock()
        opener.open.return_value = self.response([self.raw, b''])
        installer.download_dependency(opener, self.request, self.temp, self.item)
        self.assertEqual(self.temp.getvalue(), self.raw)
        opener.open.assert_called_once_with(self.request, timeout=90)

    def test_http_rejection_hides_url_and_upstream_body(self):
        opener = Mock()
        opener.open.side_effect = HTTPError('https://example.invalid/private?secret=hidden', 403, 'private diagnostic', {}, io.BytesIO(b'private body'))
        with self.assertRaisesRegex(ValueError, '^DEPENDENCY_DOWNLOAD_HTTP_REJECTED$'):
            installer.download_dependency(opener, self.request, self.temp, self.item)
        self.assertEqual(self.temp.getvalue(), b'')
        self.assertEqual(opener.open.call_count, 1)

    def test_interrupted_response_never_succeeds_or_retries(self):
        for error in (TimeoutError('secret diagnostic'), ConnectionResetError('secret diagnostic'), IncompleteRead(b'private partial'), URLError('secret diagnostic')):
            with self.subTest(error=type(error).__name__):
                opener = Mock()
                opener.open.return_value = self.response([b'part', error])
                with self.assertRaisesRegex(ValueError, '^DEPENDENCY_DOWNLOAD_INTERRUPTED$'):
                    installer.download_dependency(opener, self.request, io.BytesIO(), self.item)
                self.assertEqual(opener.open.call_count, 1)

    def test_truncated_corrupt_or_excessive_bytes_are_rejected(self):
        for raw, code in ((self.raw[:-1], 'DEPENDENCY_HASH_MISMATCH'), (b'x' * len(self.raw), 'DEPENDENCY_HASH_MISMATCH'), (self.raw + b'x', 'DEPENDENCY_SIZE_MISMATCH')):
            with self.subTest(code=code):
                opener = Mock()
                opener.open.return_value = self.response([raw, b''])
                with self.assertRaisesRegex(ValueError, '^' + code + '$'):
                    installer.download_dependency(opener, self.request, io.BytesIO(), self.item)

    def test_redirect_policy_rejection_is_not_reclassified(self):
        opener = Mock()
        opener.open.side_effect = ValueError('DEPENDENCY_REDIRECT_REJECTED')
        with self.assertRaisesRegex(ValueError, '^DEPENDENCY_REDIRECT_REJECTED$'):
            installer.download_dependency(opener, self.request, self.temp, self.item)
