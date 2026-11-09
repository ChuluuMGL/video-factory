"""Sanitized, durable provider observations. Never persist headers or body text."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib

_sink = ContextVar('script_receipt_sink', default=None)
REASONS = {'NETWORK_ERROR','HTTP_ERROR','BODY_LIMIT','INVALID_JSON','INVALID_ENVELOPE',
           'MODEL_LENGTH','CONTENT_INVALID','STRUCTURE_INVALID','VALIDATED','RESPONSE_RECEIVED'}


def usage_counts(value):
    keys=('prompt_tokens','completion_tokens','total_tokens')
    if (not isinstance(value,dict) or any(type(value.get(k)) is not int or not 0 <= value[k] <= 10000000 for k in keys)
            or value['prompt_tokens']+value['completion_tokens']!=value['total_tokens']):return None
    return {k:value[k] for k in keys}


@contextmanager
def capture(sink):
    token=_sink.set(sink)
    try:yield
    finally:_sink.reset(token)


def observe(*,reason,status=None,elapsed_ms=None,raw=None,value=None,truncated=False):
    if reason not in REASONS:raise ValueError('receipt reason')
    receipt={'schema':1,'reason':reason,'http_status':status if type(status) is int and 100<=status<=599 else None,
             'elapsed_ms':max(0,int(elapsed_ms)) if type(elapsed_ms) in (int,float) else None,
             'usage':usage_counts(value.get('usage')) if isinstance(value,dict) else None,
             'truncated':bool(truncated),'response_sha256':hashlib.sha256(raw).hexdigest() if isinstance(raw,bytes) else None,
             'response_bytes':len(raw) if isinstance(raw,bytes) else None}
    choices=value.get('choices') if isinstance(value,dict) else None
    finish=choices[0].get('finish_reason') if isinstance(choices,list) and len(choices)==1 and isinstance(choices[0],dict) else None
    receipt['finish_reason']=finish if finish in ('stop','length','content_filter','tool_calls','function_call') else None
    # Link the receipt without retaining an arbitrary upstream ID containing data.
    pid=value.get('id') if isinstance(value,dict) else None
    receipt['provider_id_sha256']=hashlib.sha256(pid.encode()).hexdigest() if isinstance(pid,str) else None
    sink=_sink.get()
    if sink is not None:sink(receipt)  # storage failure stops execution; never retry POST
    return receipt
