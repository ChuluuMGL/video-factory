"""Bounded MiniMax H3 transport. Never retries POST or forwards keys to media."""
import base64
import hashlib
import json
import os
from http.client import HTTPSConnection
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler, HTTPSHandler
from urllib.parse import urlsplit

from .runtime_store import RuntimeFault, canonical, private_directory

ORIGINS = {'global':'https://api.minimax.io', 'cn':'https://api.minimaxi.com'}
MEDIA_HOSTS = {'cdn.hailuoai.com', 'algeng-video-infer.oss-cn-shanghai.aliyuncs.com'}


class ProviderRejected(RuntimeFault):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeFault('PROVIDER_REDIRECT_REJECTED')


class TunnelConnection(HTTPSConnection):
    def __init__(self,host,**kwargs):
        # The relay address cannot be supplied by customer input or proxy env.
        # HTTPSConnection validates the original destination certificate after
        # CONNECT; the relay never receives the bearer key in plaintext.
        if host not in {urlsplit(v).hostname for v in ORIGINS.values()} | MEDIA_HOSTS:
            raise RuntimeFault('EGRESS_HOST_NOT_ALLOWED')
        super().__init__('egress',8443,**kwargs)
        self.set_tunnel(host,443)


class TunnelHandler(HTTPSHandler):
    def https_open(self,request):
        return self.do_open(TunnelConnection,request,context=self._context)


def opener():
    handlers=[ProxyHandler({}),NoRedirect()]
    if os.environ.get('VF_WORKER_EGRESS')=='1':
        if os.environ.get('VF_CONTAINER_MODE')!='1':raise RuntimeFault('EGRESS_REQUIRES_CONTAINER')
        handlers.append(TunnelHandler())
    return build_opener(*handlers)


def request_body(script, root, specification):
    root = private_directory(root)
    if (not isinstance(specification, dict) or set(specification) != {'duration','references'}
            or type(specification['duration']) is not int or not 4 <= specification['duration'] <= 15
            or not isinstance(specification['references'], list) or not 1 <= len(specification['references']) <= 9):
        raise RuntimeFault('H3_SPECIFICATION_INVALID')
    if not isinstance(script,str) or not script.strip() or len(script.encode('utf-16-le'))//2 > 7000:
        raise RuntimeFault('H3_SCRIPT_INVALID')
    content = [{'type':'text','text':script}]
    total = 0
    for item in specification['references']:
        if not isinstance(item,dict) or set(item) != {'path','sha256'} or not isinstance(item['path'],str):
            raise RuntimeFault('H3_REFERENCE_INVALID')
        path = root/item['path']
        if (Path(item['path']).is_absolute() or '..' in Path(item['path']).parts or path.resolve()!=path
                or not path.is_relative_to(root) or not path.is_file() or path.stat().st_nlink!=1):
            raise RuntimeFault('H3_REFERENCE_PATH_INVALID')
        mime = {'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp'}.get(path.suffix.lower())
        if not mime or path.stat().st_size > 30*1024*1024:
            raise RuntimeFault('H3_REFERENCE_FORMAT_OR_SIZE_INVALID')
        raw=path.read_bytes(); total+=len(raw)
        if total > 45*1024*1024 or hashlib.sha256(raw).hexdigest()!=item['sha256']:
            raise RuntimeFault('H3_REFERENCE_CHANGED_OR_TOO_LARGE')
        content.append({'type':'image_url','role':'reference_image',
                        'image_url':{'url':'data:'+mime+';base64,'+base64.b64encode(raw).decode()}})
    body={'model':'MiniMax-H3','content':content,'duration':specification['duration'],'resolution':'768P','ratio':'9:16'}
    if len(canonical(body).encode()) > 64*1024*1024:
        raise RuntimeFault('H3_REQUEST_TOO_LARGE')
    return body


class H3Provider:
    def __init__(self, region):
        if region not in ORIGINS:
            raise RuntimeFault('PROVIDER_REGION_INVALID')
        self.origin=ORIGINS[region]

    def call(self, method, path, secret, body=None):
        if not isinstance(secret,str) or not secret or any(c.isspace() for c in secret):
            raise RuntimeFault('PROVIDER_KEY_FORMAT_INVALID_USE_RAW_KEY')
        request=Request(self.origin+path, data=canonical(body).encode() if body is not None else None,
                        headers={'Authorization':'Bearer '+secret,'Content-Type':'application/json'},method=method)
        try:
            try: response=opener().open(request,timeout=90)
            except HTTPError as error: response=error
            with response:
                raw=response.read(2*1024*1024+1)
                if len(raw)>2*1024*1024:raise ValueError
                result=json.loads(raw)
                if not isinstance(result,dict):raise ValueError
                return response.status,result
        except Exception:
            raise RuntimeFault('PROVIDER_RESPONSE_UNCERTAIN') from None

    def submit(self, body, secret):
        status,result=self.call('POST','/v2/video_generation',secret,body)
        provider_id=result.get('task_id')
        if status==200 and isinstance(provider_id,str) and re.fullmatch(r'[0-9]{10,24}',provider_id):
            return provider_id
        if status in (400,401,402,403,404,422,429) and not any(result.get(k) for k in ('task_id','taskId','id')):
            raise ProviderRejected('PROVIDER_REJECTED_NO_AUTOMATIC_RETRY')
        raise RuntimeFault('PROVIDER_SUBMISSION_UNCERTAIN')

    def poll(self, provider_id, secret):
        if not re.fullmatch(r'[0-9]{10,24}',provider_id):
            raise RuntimeFault('PROVIDER_ID_INVALID')
        status,result=self.call('GET','/v2/query/video_generation/'+provider_id,secret)
        task=result.get('task')
        if status!=200 or not isinstance(task,dict) or str(task.get('id'))!=provider_id or task.get('model')!='MiniMax-H3':
            raise RuntimeFault('PROVIDER_QUERY_UNVERIFIED')
        state=task.get('status')
        if state not in ('queued','running','succeeded','failed','cancelled'):
            raise RuntimeFault('PROVIDER_STATUS_UNKNOWN')
        return {'id':provider_id,'status':state,'url':task.get('content',{}).get('url') if state=='succeeded' else None}

    def download(self, url, stream):
        parsed=urlsplit(url)
        if (parsed.scheme!='https' or parsed.hostname not in MEDIA_HOSTS or parsed.username or parsed.password
                or parsed.port is not None or parsed.fragment):
            raise RuntimeFault('MEDIA_HOST_NOT_APPROVED')
        # Deliberately no Authorization header on the separate media request.
        try:
            with opener().open(Request(url),timeout=60) as response:
                total=0
                while chunk:=response.read(1024*1024):
                    total+=len(chunk)
                    if total>300*1024*1024:raise RuntimeFault('MEDIA_TOO_LARGE')
                    stream.write(chunk)
        except RuntimeFault:raise
        except Exception:raise RuntimeFault('MEDIA_DOWNLOAD_FAILED') from None
