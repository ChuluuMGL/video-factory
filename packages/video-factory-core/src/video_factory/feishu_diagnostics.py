"""Content-free failure receipts shared by projection and private host adapters."""
import re
from .runtime_store import RuntimeFault


def failure_context(error):
    code = str(error)
    result = {'error': code if isinstance(error, RuntimeFault) and
              re.fullmatch(r'[A-Z][A-Z0-9_]{0,159}', code) else 'COPY_OPERATION_FAILED'}
    role = getattr(error, 'feishu_role', None)
    if role in ('source', 'copy'): result['feishu_role'] = role
    context = getattr(error, 'request_context', None)
    if not isinstance(context, dict): return result
    if context.get('method') in ('GET', 'POST', 'PUT'): result['method'] = context['method']
    if context.get('operation') in ('application_auth', 'record_search', 'record_append', 'record_update', 'other'):
        result['operation'] = context['operation']
    for name, low, high in (('http_status', 100, 599), ('feishu_code', 0, 999999999)):
        value = context.get(name)
        if type(value) is int and low <= value <= high: result[name] = value
    for name, pattern in (('target_sha256', r'[0-9a-f]{64}'), ('request_id', r'[A-Za-z0-9_-]{1,128}')):
        value = context.get(name)
        if isinstance(value, str) and re.fullmatch(pattern, value): result[name] = value
    return result
