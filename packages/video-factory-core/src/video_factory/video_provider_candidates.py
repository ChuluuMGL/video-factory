"""Unwired video-provider transports for contract review and live canaries.

These adapters do not opt a customer into paid requests. The H3 worker remains
the only enabled video route until each vendor's media and billing contract has
been verified with a real task and the approval ledger is wired to it.
"""
import json
import re
from urllib.error import HTTPError
from urllib.request import Request

from .h3_provider import ProviderRejected, REJECTION_CODES, opener
from .runtime_store import RuntimeFault, canonical


ROUTES = {
    'seedance': ('https://ark.cn-beijing.volces.com', 'doubao-seedance-2-5-260628'),
    'wan': ('https://dashscope.aliyuncs.com', 'wan2.7-i2v-2026-04-25'),
    'kling': ('https://api-singapore.klingai.com', 'kling-3.0'),
}
TASK_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{5,95}\Z')


def image_body(route, script, h3_body):
    """Map one verified local reference to each vendor's first-frame contract.

    More H3 reference images must not be silently dropped or reinterpreted.
    """
    if route not in ROUTES:
        raise RuntimeFault('VIDEO_PROVIDER_NOT_SUPPORTED')
    if not isinstance(h3_body, dict) or len(h3_body.get('content', [])) != 2:
        raise RuntimeFault('VIDEO_PROVIDER_REQUIRES_EXACTLY_ONE_FIRST_FRAME')
    if not isinstance(script, str) or not script.strip() or len(script) > 2500:
        raise RuntimeFault('VIDEO_PROMPT_INVALID')
    reference = h3_body['content'][1]['image_url']['url']
    if not reference.startswith(('data:image/png;base64,', 'data:image/jpeg;base64,')):
        raise RuntimeFault('VIDEO_FIRST_FRAME_FORMAT_INVALID')
    duration = h3_body['duration']
    if route in ('seedance', 'kling') and duration not in (5, 10):
        raise RuntimeFault('VIDEO_PROVIDER_DURATION_UNSUPPORTED')
    if route == 'seedance':
        return {'model': ROUTES[route][1], 'content': [
            {'type': 'text', 'text': script},
            {'type': 'image_url', 'image_url': {'url': reference}},
        ], 'duration': duration, 'ratio': '9:16', 'generate_audio': True,
            'watermark': False}
    if route == 'wan':
        return {'model': ROUTES[route][1], 'input': {'prompt': script,
            'media': [{'type': 'first_frame', 'url': reference}]},
            'parameters': {'duration': duration, 'resolution': '720P',
                           'prompt_extend': False, 'watermark': False}}
    return {'contents': [{'type': 'prompt', 'text': script},
                         {'type': 'first_frame', 'url': reference.split(',', 1)[1]}],
            'settings': {'resolution': '720p', 'duration': duration, 'audio': 'native',
                         'multi_shot': False},
            'options': {'watermark_info': {'enabled': False}}}


class CandidateVideoProvider:
    """One POST; uncertain responses always require manual reconciliation."""
    def __init__(self, route):
        if route not in ROUTES:
            raise RuntimeFault('VIDEO_PROVIDER_NOT_SUPPORTED')
        self.route = route
        self.origin, self.model = ROUTES[route]

    def _call(self, method, path, secret, body=None):
        if not isinstance(secret, str) or not secret or any(c.isspace() for c in secret):
            raise RuntimeFault('PROVIDER_KEY_FORMAT_INVALID_USE_RAW_KEY')
        if not path.startswith('/') or '//' in path or '?' in path or '#' in path:
            raise RuntimeFault('VIDEO_PROVIDER_PATH_INVALID')
        headers = {'Authorization': 'Bearer ' + secret, 'Content-Type': 'application/json'}
        if self.route == 'wan' and method == 'POST':
            headers['X-DashScope-Async'] = 'enable'
        request = Request(self.origin + path, method=method,
                          data=canonical(body).encode() if body is not None else None,
                          headers=headers)
        try:
            try:
                response = opener().open(request, timeout=90)
            except HTTPError as error:
                response = error
            with response:
                raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    raise ValueError
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError
                return response.status, result
        except Exception:
            raise RuntimeFault('PROVIDER_RESPONSE_UNCERTAIN') from None

    def submit(self, body, secret):
        if self.route == 'seedance':
            path = '/api/v3/contents/generations/tasks'
        elif self.route == 'wan':
            path = '/api/v1/services/aigc/video-generation/video-synthesis'
        else:
            path = '/image-to-video/kling-3.0'
        status, result = self._call('POST', path, secret, body)
        if status in REJECTION_CODES:
            raise ProviderRejected(REJECTION_CODES[status])
        if status not in (200, 201, 202):
            raise RuntimeFault('PROVIDER_SUBMISSION_UNCERTAIN')
        if self.route == 'seedance':
            identifier = result.get('id')
        elif self.route == 'wan':
            identifier = result.get('output', {}).get('task_id')
        else:
            identifier = result.get('data', {}).get('id') if result.get('code') == 0 else None
        if not isinstance(identifier, str) or not TASK_ID.fullmatch(identifier):
            raise RuntimeFault('PROVIDER_SUBMISSION_UNCERTAIN')
        return identifier

    def poll(self, provider_id, secret):
        if not isinstance(provider_id, str) or not TASK_ID.fullmatch(provider_id):
            raise RuntimeFault('PROVIDER_ID_INVALID')
        path = {'seedance': '/api/v3/contents/generations/tasks/',
                'wan': '/api/v1/tasks/'}
        if self.route == 'kling':
            endpoint = '/tasks?task_ids=' + provider_id
            # Query strings are restricted to the validated task ID.
            status, result = self._call_kling_query(endpoint, secret)
        else:
            status, result = self._call('GET', path[self.route] + provider_id, secret)
        if status != 200:
            raise RuntimeFault('PROVIDER_QUERY_UNVERIFIED')
        if self.route == 'seedance':
            if result.get('id') != provider_id or result.get('model') != self.model:
                raise RuntimeFault('PROVIDER_QUERY_UNVERIFIED')
            state = result.get('status')
            url = result.get('content', {}).get('video_url') if state == 'succeeded' else None
        elif self.route == 'wan':
            output = result.get('output', {})
            if output.get('task_id') != provider_id:
                raise RuntimeFault('PROVIDER_QUERY_UNVERIFIED')
            state = {'PENDING':'queued','RUNNING':'running','SUCCEEDED':'succeeded',
                     'FAILED':'failed','CANCELED':'cancelled'}.get(output.get('task_status'))
            url = output.get('video_url') if state == 'succeeded' else None
        else:
            data = result.get('data')
            if result.get('code') != 0 or not isinstance(data, list) or len(data) != 1 or data[0].get('id') != provider_id:
                raise RuntimeFault('PROVIDER_QUERY_UNVERIFIED')
            state = {'submitted':'queued','processing':'running',
                     'succeeded':'succeeded','failed':'failed'}.get(data[0].get('status'))
            outputs = data[0].get('outputs', [])
            videos = [entry for entry in outputs if isinstance(entry, dict) and entry.get('type') == 'video'] if isinstance(outputs, list) else []
            url = videos[0].get('url') if state == 'succeeded' and len(videos) == 1 else None
        if state not in ('queued','running','succeeded','failed','cancelled'):
            raise RuntimeFault('PROVIDER_STATUS_UNKNOWN')
        if state == 'succeeded' and (not isinstance(url, str) or not url.startswith('https://')):
            raise RuntimeFault('PROVIDER_MEDIA_URL_UNVERIFIED')
        return {'id': provider_id, 'status': state, 'url': url}

    def _call_kling_query(self, path, secret):
        if not path.startswith('/tasks?task_ids=') or not TASK_ID.fullmatch(path.split('=', 1)[1]):
            raise RuntimeFault('VIDEO_PROVIDER_PATH_INVALID')
        # This route is intentionally separate from _call's no-query policy.
        request = Request(self.origin + path, method='GET',
                          headers={'Authorization': 'Bearer ' + secret,
                                   'Content-Type': 'application/json'})
        try:
            with opener().open(request, timeout=90) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    raise ValueError
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError
                return response.status, result
        except Exception:
            raise RuntimeFault('PROVIDER_RESPONSE_UNCERTAIN') from None
