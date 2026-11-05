"""Reviewed product recipe for a single-select decoder, never customer source.

This portable specimen reproduces the historical list-vs-string failure. It is
not the original customer wrapper, a whole workflow graph or an arbitrary patch.
"""
import hashlib

PROFILE = 'single-select-decoder-v1'
MODULE = 'isolated_status_decoder.py'
_TAIL = '''
def scan(pages):
    records = []
    seen = set()
    revision = None
    for page in pages:
        if revision is not None and revision != page['revision']:
            raise ValueError('REVISION_CHANGED')
        revision = page['revision']
        if page['more'] and not page['rows']:
            raise ValueError('EMPTY_PAGE')
        for row in page['rows']:
            if row['id'] in seen:
                raise ValueError('DUPLICATE_RECORD')
            seen.add(row['id'])
            decode_status(row['status'])
            records.append(dict(row))
    return records
'''
BEFORE = '''def decode_status(value):
    if value not in ('失败/需处理', '失败'):
        raise ValueError('STATUS_INVALID')
    return value
''' + _TAIL
AFTER = '''def decode_status(value):
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    if value not in ('失败/需处理', '失败'):
        raise ValueError('STATUS_INVALID')
    return value
''' + _TAIL


def sha(source): return hashlib.sha256(source.encode()).hexdigest()


def source(value):
    if value not in (BEFORE, AFTER): raise ValueError('REPAIR_SOURCE_NOT_REVIEWED')
    return {'module': MODULE, 'text': value, 'sha256': sha(value)}


def check(value):
    if not isinstance(value, dict) or set(value) != {'module', 'text', 'sha256'}:
        raise ValueError('REPAIR_SOURCE_NOT_REVIEWED')
    if value != source(value['text']): raise ValueError('REPAIR_SOURCE_NOT_REVIEWED')
    return value
