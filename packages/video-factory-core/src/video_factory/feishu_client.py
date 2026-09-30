"""Read-only Feishu user API. No tenant-token fallback or configurable origin."""
import json
import os
import re
from http.client import HTTPSConnection
from urllib.parse import urlencode
from urllib.request import Request, ProxyHandler, build_opener
from .h3_provider import NoRedirect, TunnelHandler
from .runtime_store import RuntimeFault

HOST='open.feishu.cn'
# Real device-flow tokens can exceed 4 KiB. Keep the same bounded limit at
# OAuth receipt and API use; the token remains opaque and memory-only.
MAX_USER_TOKEN_LENGTH = 16384


def resource(value,prefix=''):
    if not isinstance(value,str) or not re.fullmatch(prefix+r'[A-Za-z0-9_-]{4,128}',value):
        raise RuntimeFault('FEISHU_RESOURCE_ID_INVALID')
    return value


class FeishuTunnel(HTTPSConnection):
    def __init__(self,host,**kwargs):
        if host!=HOST:raise RuntimeFault('FEISHU_HOST_INVALID')
        super().__init__('egress',8443,**kwargs);self.set_tunnel(host,443)


class FeishuHandler(TunnelHandler):
    def https_open(self,request):return self.do_open(FeishuTunnel,request,context=self._context)


class FeishuClient:
    origin='https://'+HOST
    def __init__(self,user_token):
        if not isinstance(user_token,str) or not user_token or len(user_token)>MAX_USER_TOKEN_LENGTH or any(c.isspace() for c in user_token):
            raise RuntimeFault('FEISHU_USER_TOKEN_REQUIRED')
        self.token=user_token

    def get(self,path):
        # The origin is code-owned; path is assembled from validated IDs only.
        handlers=[ProxyHandler({}),NoRedirect()]
        if os.environ.get('VF_WORKER_EGRESS')=='1':
            if os.environ.get('VF_CONTAINER_MODE')!='1':raise RuntimeFault('EGRESS_REQUIRES_CONTAINER')
            handlers.append(FeishuHandler())
        try:
            request=Request(self.origin+'/open-apis'+path,headers={'Authorization':'Bearer '+self.token},method='GET')
            with build_opener(*handlers).open(request,timeout=15) as response:
                raw=response.read(2*1024*1024+1)
                if response.status!=200 or len(raw)>2*1024*1024:raise ValueError
                result=json.loads(raw)
                if type(result.get('code')) is not int or result['code']!=0 or not isinstance(result.get('data'),dict):raise ValueError
                return result['data']
        except Exception:raise RuntimeFault('FEISHU_READ_OR_USER_AUTH_FAILED') from None

    def identity(self):
        data=self.get('/authen/v1/user_info')
        # Names and email addresses are deliberately neither trusted nor saved.
        return {'tenant_key':resource(data.get('tenant_key')),'open_id':resource(data.get('open_id'),'ou_')}

    def fields(self,base,table):
        resource(base);resource(table,'tbl');page=None;seen=set();result=[]
        for _ in range(30):
            query={'page_size':100}
            if page:query['page_token']=page
            data=self.get(f'/bitable/v1/apps/{base}/tables/{table}/fields?'+urlencode(query))
            items=data.get('items')
            if not isinstance(items,list) or type(data.get('has_more')) is not bool:raise RuntimeFault('FEISHU_FIELDS_INVALID')
            result+=items
            if not data['has_more']:return result
            page=data.get('page_token')
            if not isinstance(page,str) or not page or len(page)>2048 or page in seen:raise RuntimeFault('FEISHU_PAGINATION_INVALID')
            seen.add(page)
        raise RuntimeFault('FEISHU_PAGINATION_INCOMPLETE')

    def record(self,base,table,record):
        resource(base);resource(table,'tbl');resource(record,'rec')
        data=self.get(f'/bitable/v1/apps/{base}/tables/{table}/records/{record}?text_field_as_array=false&automatic_fields=true&user_id_type=open_id')
        result=data.get('record')
        if not isinstance(result,dict) or result.get('record_id')!=record or not isinstance(result.get('fields'),dict):
            raise RuntimeFault('FEISHU_RECORD_INVALID')
        return result
