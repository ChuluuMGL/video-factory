"""Durable observations; optional private, redacted validation diagnostics."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import re
import json

_sink = ContextVar('script_receipt_sink', default=None)
_details = ContextVar('script_failure_details', default=False)
REASONS = {'NETWORK_ERROR','HTTP_ERROR','BODY_LIMIT','INVALID_JSON','INVALID_ENVELOPE',
           'MODEL_LENGTH','CONTENT_INVALID','STRUCTURE_INVALID','VALIDATED','RESPONSE_RECEIVED'}


def usage_counts(value):
    keys=('prompt_tokens','completion_tokens','total_tokens')
    if (not isinstance(value,dict) or any(type(value.get(k)) is not int or not 0 <= value[k] <= 10000000 for k in keys)
            or value['prompt_tokens']+value['completion_tokens']!=value['total_tokens']):return None
    return {k:value[k] for k in keys}


@contextmanager
def capture(sink, *, failure_details=False):
    token=_sink.set(sink)
    detail_token=_details.set(failure_details)
    try:yield
    finally:
        _sink.reset(token)
        _details.reset(detail_token)


class ValidationFailure(ValueError):
    """Trusted decoder diagnostics, not a change to validation acceptance."""
    def __init__(self, field, code, reason):
        self.field,self.code,self.reason=field,code,reason
        super().__init__('STRUCTURE_INVALID')


def redact(value, secrets=()):
    text=value if isinstance(value,str) else str(value)
    for secret in secrets:
        if secret:text=text.replace(secret,'[REDACTED]')
    text=re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----','[REDACTED_KEY]',text)
    text=re.sub(r'(?i)\b(?:Bearer\s+\S+|(?:sk|sk-proj)[-_][A-Za-z0-9_-]{8,})','[REDACTED_TOKEN]',text)
    text=re.sub(r'https?://[^\s"<>]+','[REDACTED_URL]',text)
    text=re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b','[REDACTED_EMAIL]',text)
    text=re.sub(r'(?<!\w)(?:\d{1,3}\.){3}\d{1,3}(?!\w)','[REDACTED_IP]',text)
    text=re.sub(r'(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)','[REDACTED_PHONE]',text)
    text=re.sub(r'(?i)(["\']?(?:api[_-]?key|app[_-]?secret|password|access[_-]?token|refresh[_-]?token)["\']?\s*[:=]\s*)["\']?[^\s,}\n]+',r'\1[REDACTED]',text)
    return text


def observe_validation(error, content, *, secrets=()):
    receipt=observe(reason='STRUCTURE_INVALID')
    if not _details.get():return
    if isinstance(error,ValidationFailure):
        field,code,reason=error.field,error.code,error.reason
    else:
        field,code,reason='$','DECODER_VALIDATION_ERROR','Detailed decoder cause unavailable'
    receipt={**receipt,'schema':2,'validation':{
        'field':redact(field,secrets)[:256], 'code':redact(code,secrets)[:128],
        'reason':redact(reason,secrets)[:4096]},
        'model_content_redacted':redact(content,secrets)[:131072],
        'content_capture_truncated':len(content)>131072,
        'content_sha256':hashlib.sha256(content.encode()).hexdigest()}
    sink=_sink.get()
    if sink is not None:sink(receipt)


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
