"""Bounded device authorization, following the official Feishu CLI contract.

Only browser verification data is public. No refresh token is retained. This
client never logs upstream response bodies, secrets or authorization headers.
"""
import base64
from http.client import HTTPSConnection
import json
import os
import re
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, ProxyHandler, build_opener

from .h3_provider import NoRedirect, TunnelHandler
from .feishu_client import MAX_USER_TOKEN_LENGTH
from .runtime_store import RuntimeFault

HOSTS = {'accounts.feishu.cn', 'open.feishu.cn'}
SCOPES = ('bitable:app:readonly', 'contact:user.base:readonly')
CREATE_SCOPES = SCOPES + ('base:app:create', 'base:table:create', 'base:record:create')


class OAuthTunnel(HTTPSConnection):
    def __init__(self, host, **kwargs):
        if host not in HOSTS:
            raise RuntimeFault('FEISHU_OAUTH_HOST_INVALID')
        super().__init__('egress', 8443, **kwargs)
        self.set_tunnel(host, 443)


class OAuthHandler(TunnelHandler):
    def https_open(self, request):
        return self.do_open(OAuthTunnel, request, context=self._context)


def text_value(value, limit=4096):
    if not isinstance(value, str) or not 1 <= len(value) <= limit or any(c.isspace() for c in value):
        raise RuntimeFault('FEISHU_OAUTH_RESPONSE_INVALID')
    return value


def verification_url(value):
    text_value(value)
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or parsed.hostname not in HOSTS or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.fragment or '\\' in value):
        raise RuntimeFault('FEISHU_OAUTH_VERIFICATION_URL_INVALID')
    return value


class DeviceOAuth:
    accounts_origin = 'https://accounts.feishu.cn'
    token_origin = 'https://open.feishu.cn'

    def __init__(self, app_id, app_secret, *, scopes=SCOPES):
        if not isinstance(app_id, str) or not re.fullmatch(r'cli_[A-Za-z0-9_-]{4,128}', app_id):
            raise RuntimeFault('FEISHU_APP_ID_INVALID')
        if (not isinstance(app_secret, str) or not 1 <= len(app_secret) <= 4096
                or any(c.isspace() for c in app_secret) or ':' in app_secret):
            raise RuntimeFault('FEISHU_APP_SECRET_INVALID')
        if tuple(scopes) not in (SCOPES, CREATE_SCOPES):
            raise RuntimeFault('FEISHU_OAUTH_SCOPE_INVALID')
        self.app_id, self.app_secret, self.scopes = app_id, app_secret, tuple(scopes)

    def post(self, origin, path, body, *, basic=False):
        handlers = [ProxyHandler({}), NoRedirect()]
        if os.environ.get('VF_WORKER_EGRESS') == '1':
            if os.environ.get('VF_CONTAINER_MODE') != '1':
                raise RuntimeFault('EGRESS_REQUIRES_CONTAINER')
            handlers.append(OAuthHandler())
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        if basic:
            headers['Authorization'] = 'Basic ' + base64.b64encode((self.app_id + ':' + self.app_secret).encode()).decode()
        try:
            request = Request(origin + path, data=urlencode(body).encode(), headers=headers, method='POST')
            try:
                response = build_opener(*handlers).open(request, timeout=15)
            except HTTPError as error:
                if error.code != 400:
                    raise
                response = error
            with response:
                raw = response.read(65537)
                if response.status not in (200, 400) or len(raw) > 65536:
                    raise ValueError
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError
                return value
        except Exception:
            raise RuntimeFault('FEISHU_OAUTH_REQUEST_FAILED') from None

    def start(self):
        value = self.post(self.accounts_origin, '/oauth/v1/device_authorization',
                          {'client_id': self.app_id, 'scope': ' '.join(self.scopes)}, basic=True)
        if value.get('error') or value.get('code', 0) != 0:
            raise RuntimeFault('FEISHU_OAUTH_START_DENIED')
        value = value.get('data', value)
        if not isinstance(value, dict):
            raise RuntimeFault('FEISHU_OAUTH_RESPONSE_INVALID')
        expires, interval = value.get('expires_in'), value.get('interval', 5)
        if type(expires) is not int or not 1 <= expires <= 3600 or type(interval) is not int or not 1 <= interval <= 60:
            raise RuntimeFault('FEISHU_OAUTH_RESPONSE_INVALID')
        return {'device_code': text_value(value.get('device_code')),
                'user_code': text_value(value.get('user_code'), 128),
                'verification_uri': verification_url(value.get('verification_uri_complete') or value.get('verification_uri')),
                'expires_in': expires, 'interval': max(5, interval)}

    def poll(self, device_code):
        value = self.post(self.token_origin, '/open-apis/authen/v2/oauth/token',
                          {'client_id': self.app_id, 'client_secret': self.app_secret,
                           'grant_type': 'urn:ietf:params:oauth:grant-type:device_code', 'device_code': text_value(device_code)})
        error = value.get('error')
        if error in ('authorization_pending', 'slow_down'):
            return {'status': error}
        if error or value.get('code', 0) != 0:
            raise RuntimeFault('FEISHU_OAUTH_DENIED_OR_EXPIRED')
        value = value.get('data', value)
        if not isinstance(value, dict):
            raise RuntimeFault('FEISHU_OAUTH_RESPONSE_INVALID')
        expires = value.get('expires_in')
        if type(expires) is not int or not 30 <= expires <= 86400:
            raise RuntimeFault('FEISHU_OAUTH_RESPONSE_INVALID')
        if 'scope' in value and (not isinstance(value['scope'], str) or not set(self.scopes) <= set(value['scope'].split())):
            raise RuntimeFault('FEISHU_OAUTH_SCOPE_MISSING')
        return {'status': 'authorized', 'access_token': text_value(value.get('access_token'), MAX_USER_TOKEN_LENGTH), 'expires_in': expires}
