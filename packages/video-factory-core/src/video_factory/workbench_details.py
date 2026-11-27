"""Bounded workbench descriptions and trusted Feishu row links."""
import re

from .feishu_bridge import FEISHU_WEB_HOST, meta, source_key as imported_source_key, target
from .feishu_native_review import source_key as native_source_key
from .script_provider import MODEL

BASE = re.compile(r'[A-Za-z0-9_-]+')
TABLE = re.compile(r'tbl[A-Za-z0-9_-]+')
RECORD = re.compile(r'rec[A-Za-z0-9_-]+')


def record_url(db, project, row):
    binding = meta(db, 'feishu:binding:' + project)
    if not binding:
        return {}
    host, base, table = (binding.get(key, '') for key in ('web_host', 'base_token', 'table_id'))
    if not all(isinstance(value, str) and regex.fullmatch(value)
               for value, regex in ((host, FEISHU_WEB_HOST), (base, BASE), (table, TABLE))):
        return {}
    bound = target(binding)
    native = meta(db, 'native:config:' + project) or {}
    record = None
    if native.get('enabled') and native.get('context', {}).get('target') == bound:
        link = meta(db, native_source_key(project, row['id'])) or {}
        record = link.get('record_id')
    if record is None:
        imported = meta(db, imported_source_key(project, row['id'], row['revision'])) or {}
        snapshot = imported.get('snapshot') or {}
        if snapshot.get('target') == bound:
            record = snapshot.get('record_id')
    if not isinstance(record, str) or not RECORD.fullmatch(record):
        return {}
    return {'feishu_record_url': f'https://{host}/base/{base}?table={table}&record={record}'}


def model_name(kind):
    return MODEL if kind == 'script' else 'MiniMax-H3'


def no_cost():
    return '本次操作 ¥0，不调用付费模型。'


def queued_cost():
    return f'计划运行时预计调用 1 次 {model_name("video")}，费用按供应商费率结算。'


def details(db, project, row, diagnosis, change, cost):
    return {'diagnosis_summary': diagnosis, 'change_summary': change, 'cost_summary': cost,
            **record_url(db, project, row)}
