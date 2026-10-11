"""Fixed-origin application client for opt-in, append-only Base results.

No employee token fallback, environment proxy, redirects or raw upstream diagnostics.
The application token stays in memory and is never returned in a receipt.
"""
import json
import os
import secrets
import zlib
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, ProxyHandler, build_opener

from .feishu_client import FeishuClient, FeishuHandler, resource
from .feishu_oauth import DeviceOAuth, text_value
from .h3_provider import NoRedirect
from .runtime_store import RuntimeFault, canonical

FIELDS = {'同步标识': 1, '项目': 1, '任务': 1, '版本': 1, 'SKU': 1,
          '状态': 1, '脚本': 1, '视频摘要': 1, '视频': 17}
BLOCK_SIZE = 4*1024*1024


def request(path, body, token=None, content_type='application/json; charset=utf-8', method='POST'):
    if method not in ('POST', 'PUT'): raise RuntimeFault('FEISHU_METHOD_DENIED')
    handlers = [ProxyHandler({}), NoRedirect()]
    if os.environ.get('VF_WORKER_EGRESS') == '1':
        if os.environ.get('VF_CONTAINER_MODE') != '1': raise RuntimeFault('EGRESS_REQUIRES_CONTAINER')
        handlers.append(FeishuHandler())
    headers = {'Content-Type': content_type}
    if token: headers['Authorization'] = 'Bearer '+token
    try:
        req = Request('https://open.feishu.cn/open-apis'+path, data=body, headers=headers, method=method)
        with build_opener(*handlers).open(req, timeout=30) as response:
            raw = response.read(2*1024*1024+1)
            if response.status != 200 or len(raw) > 2*1024*1024: raise ValueError
            value = json.loads(raw)
            if type(value.get('code')) is not int: raise ValueError
            if value['code'] != 0:
                # Only a bounded numeric code is safe to surface. The message
                # and response body can contain customer data or credentials.
                if 0 < value['code'] < 1000000000:
                    raise RuntimeFault('BASE_RESULTS_REQUEST_UNKNOWN_FEISHU_CODE_'+str(value['code']))
                raise ValueError
            return value
    except HTTPError as error:
        try:
            raw = error.read(65537)
            value = json.loads(raw) if len(raw) <= 65536 else {}
            code = value.get('code') if isinstance(value, dict) else None
            # Feishu has explicitly refused this request. In particular a
            # create-table 403/91403 cannot have created a table, whereas a
            # lost response or a successful HTTP response with an error body
            # remains an uncertain write until reconciled.
            if error.code == 403 and code == 91403:
                raise RuntimeFault('BASE_RESULTS_REQUEST_REJECTED_91403') from None
            if type(code) is int and 0 < code < 1000000000:
                raise RuntimeFault('BASE_RESULTS_REQUEST_UNKNOWN_FEISHU_CODE_'+str(code)) from None
        except RuntimeFault:
            raise
        except Exception:
            pass
        raise RuntimeFault('BASE_RESULTS_REQUEST_UNKNOWN_HTTP_'+str(error.code)) from None
    except RuntimeFault:
        raise
    except Exception:
        # A failed POST is not evidence that the upstream did nothing.
        raise RuntimeFault('BASE_RESULTS_REQUEST_UNKNOWN_READ_STATUS') from None


class ResultClient(FeishuClient):
    @classmethod
    def application(cls, app_id, secret):
        DeviceOAuth(app_id, secret)  # validate only; no employee authorization
        value = request('/auth/v3/tenant_access_token/internal',
                        canonical({'app_id': app_id, 'app_secret': secret}).encode())
        if type(value.get('expire')) is not int or value['expire'] < 60:
            raise RuntimeFault('BASE_RESULTS_APP_AUTH_FAILED')
        return cls(value.get('tenant_access_token'))

    def post(self, path, body):
        value = request(path, canonical(body).encode(), self.token)
        if not isinstance(value.get('data'), dict): raise RuntimeFault('BASE_RESULTS_RESPONSE_INVALID')
        return value['data']

    def put(self, path, body):
        value = request(path, canonical(body).encode(), self.token, method='PUT')
        if not isinstance(value.get('data'), dict): raise RuntimeFault('FEISHU_WRITE_RESPONSE_INVALID')
        return value['data']

    def create_field(self, base, table, name, kind, ticket):
        import uuid
        from .feishu_native_sync import STATUS_OPTIONS
        resource(base); resource(table, 'tbl')
        if str(uuid.UUID(ticket)) != ticket or uuid.UUID(ticket).version != 4 or kind not in (1, 3, 17):
            raise RuntimeFault('FEISHU_FIELD_REQUEST_INVALID')
        if kind == 3 and name != '状态': raise RuntimeFault('FEISHU_FIELD_REQUEST_INVALID')
        body = {'field_name': name, 'type': kind}
        if kind == 3: body['property'] = {'options': [{'name': option} for option in STATUS_OPTIONS]}
        data = self.post(f'/bitable/v1/apps/{base}/tables/{table}/fields?'+urlencode({'client_token': ticket}),
                         body)
        field = data.get('field', {})
        if field.get('field_name') != name or field.get('type') != kind:
            raise RuntimeFault('FEISHU_FIELD_RECEIPT_INVALID')
        return resource(field.get('field_id'), 'fld')

    def update_record(self, base, table, record, fields):
        resource(base); resource(table, 'tbl'); resource(record, 'rec')
        if not isinstance(fields, dict) or not fields: raise RuntimeFault('FEISHU_UPDATE_FIELDS_INVALID')
        data = self.put(f'/bitable/v1/apps/{base}/tables/{table}/records/{record}?ignore_consistency_check=false',
                        {'fields': fields})
        result = data.get('record', {})
        if result.get('record_id') != record: raise RuntimeFault('FEISHU_UPDATE_RECEIPT_INVALID')
        return record

    def find_task(self, base, table, task_field, task):
        resource(base); resource(table, 'tbl')
        data = self.post(f'/bitable/v1/apps/{base}/tables/{table}/records/search?page_size=2', {
            'filter': {'conjunction': 'and', 'conditions': [
                {'field_name': task_field, 'operator': 'is', 'value': [task]}]}})
        items = data.get('items')
        if not isinstance(items, list) or type(data.get('has_more')) is not bool:
            raise RuntimeFault('FEISHU_TASK_SEARCH_INVALID')
        if data['has_more'] or len(items) != 1:
            raise RuntimeFault('FEISHU_TASK_RECORD_NOT_UNIQUE')
        record = items[0]
        resource(record.get('record_id'), 'rec')
        if not isinstance(record.get('fields'), dict): raise RuntimeFault('FEISHU_TASK_SEARCH_INVALID')
        return record

    def subscribe_base(self, base):
        resource(base)
        self.post(f'/drive/v1/files/{base}/subscribe?file_type=bitable', {})

    def base_subscription_status(self, base):
        resource(base)
        data = self.get(f'/drive/v1/files/{base}/get_subscribe?file_type=bitable')
        if type(data.get('is_subscribe')) is not bool: raise RuntimeFault('FEISHU_SUBSCRIPTION_READBACK_INVALID')
        return data['is_subscribe']

    def tables(self, base):
        resource(base); page = None; seen = set(); result = []
        for _ in range(10):
            query = {'page_size': 100}
            if page: query['page_token'] = page
            data = self.get(f'/bitable/v1/apps/{base}/tables?'+urlencode(query))
            if not isinstance(data.get('items'), list) or type(data.get('has_more')) is not bool:
                raise RuntimeFault('BASE_RESULTS_TABLES_INVALID')
            result.extend(data['items'])
            if not data['has_more']: return result
            page = data.get('page_token')
            if not isinstance(page, str) or not 1 <= len(page) <= 2048 or page in seen:
                raise RuntimeFault('FEISHU_PAGINATION_INVALID')
            seen.add(page)
        raise RuntimeFault('FEISHU_PAGINATION_INCOMPLETE')

    def create_result_table(self, base, name):
        resource(base)
        value = self.post(f'/bitable/v1/apps/{base}/tables', {'table': {
            'name': name, 'default_view_name': '生成结果',
            'fields': [{'field_name': name, 'type': kind} for name, kind in FIELDS.items()]}})
        return resource(value.get('table_id'), 'tbl')

    def find(self, base, table, event):
        resource(base); resource(table, 'tbl')
        data = self.post(f'/bitable/v1/apps/{base}/tables/{table}/records/search?page_size=2', {
            'field_names': list(FIELDS), 'automatic_fields': False,
            'filter': {'conjunction': 'and', 'conditions': [
                {'field_name': '同步标识', 'operator': 'is', 'value': [event]}]}})
        items = data.get('items')
        if not isinstance(items, list) or type(data.get('has_more')) is not bool:
            raise RuntimeFault('BASE_RESULTS_SEARCH_INVALID')
        if data['has_more'] or len(items) > 1: raise RuntimeFault('BASE_RESULTS_DUPLICATE_CONFLICT')
        for row in items:
            resource(row.get('record_id'), 'rec')
            if not isinstance(row.get('fields'), dict): raise RuntimeFault('BASE_RESULTS_SEARCH_INVALID')
        return items

    def append(self, base, table, fields, ticket):
        import uuid
        resource(base); resource(table, 'tbl')
        if str(uuid.UUID(ticket)) != ticket or uuid.UUID(ticket).version != 4:
            raise RuntimeFault('BASE_RESULTS_TICKET_INVALID')
        value = self.post(f'/bitable/v1/apps/{base}/tables/{table}/records/batch_create?'+
                          urlencode({'client_token': ticket, 'ignore_consistency_check': 'false'}),
                          {'records': [{'fields': fields}]})
        rows = value.get('records')
        if not isinstance(rows, list) or len(rows) != 1: raise RuntimeFault('BASE_RESULTS_RESPONSE_INVALID')
        return resource(rows[0].get('record_id'), 'rec')

    def upload_prepare(self, base, name, size):
        resource(base)
        value = self.post('/drive/v1/medias/upload_prepare', {
            'file_name': name, 'parent_type': 'bitable_file', 'parent_node': base, 'size': size})
        upload_id = text_value(value.get('upload_id'), 256)
        if value.get('block_size') != BLOCK_SIZE or value.get('block_num') != (size+BLOCK_SIZE-1)//BLOCK_SIZE:
            raise RuntimeFault('BASE_RESULTS_UPLOAD_STRATEGY_INVALID')
        return {'upload_id': upload_id, 'block_size': BLOCK_SIZE, 'block_num': value['block_num']}

    def upload_part(self, upload_id, seq, data):
        text_value(upload_id, 256)
        boundary = 'vf'+secrets.token_hex(24)
        pieces = []
        for name, value in {'upload_id': upload_id, 'seq': str(seq), 'size': str(len(data)),
                            'checksum': str(zlib.adler32(data) & 0xffffffff)}.items():
            pieces.append(('--'+boundary+'\r\nContent-Disposition: form-data; name="'+name+'"\r\n\r\n'+value+'\r\n').encode())
        pieces.extend([('--'+boundary+'\r\nContent-Disposition: form-data; name="file"; filename="part.bin"\r\nContent-Type: application/octet-stream\r\n\r\n').encode(),
                       data, ('\r\n--'+boundary+'--\r\n').encode()])
        request('/drive/v1/medias/upload_part', b''.join(pieces), self.token, 'multipart/form-data; boundary='+boundary)
        return {'seq': seq, 'size': len(data)}

    def upload_finish(self, upload_id, block_num):
        text_value(upload_id, 256)
        value = self.post('/drive/v1/medias/upload_finish', {'upload_id': upload_id, 'block_num': block_num})
        return resource(value.get('file_token'))
