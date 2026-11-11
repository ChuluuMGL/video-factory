"""Narrow Base adapters for the shadow week; no credential discovery or models.

Use different application clients for source and copy. The source client should
itself have readonly permissions. All IDs and fields come from operator config,
never task text or model output. No exception handler retries a remote write.
"""
import copy
import json
from urllib.parse import urlencode

from .feishu_client import resource
from .feishu_native_sync import plain_text
from .runtime_store import RuntimeFault, fingerprint


def _fields(value):
    if (not isinstance(value, (list, tuple)) or not value or len(value) > 100
            or any(not isinstance(v, str) or not v or len(v) > 200 for v in value)
            or len(set(value)) != len(value)):
        raise RuntimeFault('WEEK_FIELD_ALLOWLIST_INVALID')
    return tuple(value)


class ReadOnlyWeekSource:
    """GET only, fixed Base/tables/field projection, bounded complete pagination."""
    def __init__(self, client, *, base, tables):
        resource(base)
        if not isinstance(tables, dict) or not tables or len(tables) > 2:
            raise RuntimeFault('WEEK_SOURCE_TABLE_ALLOWLIST_INVALID')
        self._client, self.base = client, base
        self.tables = {resource(t, 'tbl'): _fields(f) for t, f in tables.items()}

    def _path(self, table):
        if table not in self.tables:
            raise RuntimeFault('WEEK_SOURCE_TABLE_DENIED')
        return f'/bitable/v1/apps/{self.base}/tables/{table}/records'

    def _row(self, table, row):
        if not isinstance(row, dict) or not isinstance(row.get('fields'), dict):
            raise RuntimeFault('WEEK_SOURCE_RECORD_INVALID')
        record = resource(row.get('record_id'), 'rec')
        # Drop any extra upstream fields, including people/credentials, before
        # returning to snapshot compilation or private logs.
        return {'record_id': record, 'fields': {
            k: copy.deepcopy(row['fields'][k]) for k in self.tables[table]
            if k in row['fields']}}

    def records(self, table):
        path = self._path(table)
        page, seen_pages, seen_records, rows = None, set(), set(), []
        for _ in range(20):
            query = {'page_size': 100, 'field_names': json.dumps(self.tables[table]),
                     'automatic_fields': 'false', 'text_field_as_array': 'false'}
            if page is not None: query['page_token'] = page
            data = self._client.get(path + '?' + urlencode(query))
            if (not isinstance(data, dict) or not isinstance(data.get('items'), list)
                    or len(data['items']) > 100 or type(data.get('has_more')) is not bool):
                raise RuntimeFault('WEEK_SOURCE_PAGE_INVALID')
            for raw in data['items']:
                row = self._row(table, raw)
                if row['record_id'] in seen_records:
                    raise RuntimeFault('WEEK_SOURCE_DUPLICATE_RECORD')
                seen_records.add(row['record_id']); rows.append(row)
            if not data['has_more']: return rows
            page = data.get('page_token')
            if not isinstance(page, str) or not page or len(page) > 2048 or page in seen_pages:
                raise RuntimeFault('WEEK_SOURCE_PAGINATION_INVALID')
            seen_pages.add(page)
        # Never return a partial scan as if there were no more new tasks.
        raise RuntimeFault('WEEK_SOURCE_SCAN_INCOMPLETE')

    def record(self, table, record):
        path = self._path(table); resource(record, 'rec')
        data = self._client.get(path + '/' + record + '?' + urlencode({
            'field_names': json.dumps(self.tables[table]), 'automatic_fields': 'false',
            'text_field_as_array': 'false'}))
        row = self._row(table, data.get('record') if isinstance(data, dict) else None)
        if row['record_id'] != record: raise RuntimeFault('WEEK_SOURCE_RECORD_MISMATCH')
        return row


class LinkedWeekInputs:
    """Read only the exact records linked by one approved source task.

    Relations and field projections are operator configuration, not task data.
    No material table listing, attachment download, or plan-document discovery
    is exposed here. A stable double read is evidence of the observed input,
    not proof of plan approval or of a matching production baseline.
    """
    def __init__(self, client, *, base, task_table, task_fields, relations):
        if not isinstance(relations, dict) or not 1 <= len(relations) <= 3:
            raise RuntimeFault('WEEK_RELATION_ALLOWLIST_INVALID')
        self.task_table = resource(task_table, 'tbl')
        self.source = ReadOnlyWeekSource(client, base=base,
                                        tables={task_table: task_fields})
        self.relations = {}
        for field, config in relations.items():
            if (field not in self.source.tables[task_table]
                    or not isinstance(config, dict)
                    or set(config) != {'table', 'fields'}
                    or config['table'] == task_table):
                raise RuntimeFault('WEEK_RELATION_ALLOWLIST_INVALID')
            table = resource(config['table'], 'tbl')
            self.relations[field] = (table, ReadOnlyWeekSource(
                client, base=base, tables={table: config['fields']}))

    @staticmethod
    def _linked_id(value, table):
        # Public Base API link values are record ID arrays. Some API clients
        # normalize these as {record_ids: [...]}; the CLI uses [{id: ...}].
        if isinstance(value, dict) and set(value) == {'record_ids'}:
            value = value['record_ids']
        if not isinstance(value, list) or len(value) != 1:
            raise RuntimeFault('WEEK_EXACT_ONE_LINK_REQUIRED')
        item = value[0]
        if isinstance(item, dict):
            if 'table_id' in item and item['table_id'] != table:
                raise RuntimeFault('WEEK_LINK_TABLE_MISMATCH')
            if 'record_ids' in item:
                ids = item['record_ids']
                if (not isinstance(ids, list) or len(ids) != 1
                        or 'record_id' in item or 'id' in item):
                    raise RuntimeFault('WEEK_EXACT_ONE_LINK_REQUIRED')
                item = ids[0]
            else:
                item = item.get('record_id', item.get('id'))
        return resource(item, 'rec')

    def snapshot(self, record):
        first = self.source.record(self.task_table, record)
        # Validate every link before reading any linked record.
        ids = {field: self._linked_id(first['fields'].get(field), table)
               for field, (table, _) in self.relations.items()}
        materials = {}
        for field, (table, reader) in self.relations.items():
            materials[field] = reader.record(table, ids[field])
        for field, (table, reader) in self.relations.items():
            if reader.record(table, ids[field]) != materials[field]:
                raise RuntimeFault('WEEK_LINKED_INPUT_CHANGED')
        if self.source.record(self.task_table, record) != first:
            raise RuntimeFault('WEEK_SOURCE_INPUT_CHANGED')
        inputs = {'task': first, 'materials': materials}
        return {'inputs': inputs, 'input_sha256': fingerprint(inputs),
                'provenance': {'base': self.source.base,
                               'task_table': self.task_table,
                               'task_record': record,
                               'linked_records': {
                                   field: {'table': table, 'record': ids[field],
                                           'sha256': fingerprint(materials[field])}
                                   for field, (table, _) in self.relations.items()}}}


class CopyOnlyWeekWriter:
    """Fixed copy target and machine-owned fields; preserve human review fields.

    The caller persists a unique ticket BEFORE append. Unknown append results
    must be reconciled by the caller, not retried by a systemd restart policy.
    """
    def __init__(self, client, *, source_base, copy_base, table, machine_fields,
                 human_fields, identity_field):
        resource(source_base); resource(copy_base); resource(table, 'tbl')
        if source_base == copy_base: raise RuntimeFault('WEEK_COPY_TARGET_REQUIRED')
        self.fields, self.human = _fields(machine_fields), _fields(human_fields)
        if set(self.fields) & set(self.human) or identity_field not in self.fields:
            raise RuntimeFault('WEEK_COPY_FIELD_OWNERSHIP_INVALID')
        self._client, self.base, self.table = client, copy_base, table
        self.identity_field = identity_field

    def _patch(self, fields):
        if not isinstance(fields, dict) or not fields or not set(fields) <= set(self.fields):
            raise RuntimeFault('WEEK_COPY_FIELD_DENIED')
        if len(json.dumps(fields, ensure_ascii=False).encode()) > 100000:
            raise RuntimeFault('WEEK_COPY_PATCH_LIMIT')
        return copy.deepcopy(fields)

    def append(self, fields, ticket):
        fields = self._patch(fields)
        if not isinstance(fields.get(self.identity_field), str) or not fields[self.identity_field]:
            raise RuntimeFault('WEEK_COPY_IDENTITY_REQUIRED')
        record = self._client.append(self.base, self.table, fields, ticket)
        self._readback(record, fields)
        return {'record_id': record, 'writes': 1, 'fields_sha256': fingerprint(fields)}

    def _readback(self, record, expected):
        row = self._client.record(self.base, self.table, record)
        if row.get('record_id') != record or not isinstance(row.get('fields'), dict):
            raise RuntimeFault('WEEK_COPY_READBACK_INVALID')
        for field, value in expected.items():
            actual = row['fields'].get(field)
            # These projections use text and number fields only. Human-owned
            # status, opinions and scores never enter this patch.
            if isinstance(value, str): actual = '' if actual in (None, '', []) else plain_text(actual)
            if actual != value: raise RuntimeFault('WEEK_COPY_READBACK_MISMATCH')
        return row

    def update(self, record, identity, fields):
        resource(record, 'rec'); fields = self._patch(fields)
        if self.identity_field in fields and fields[self.identity_field] != identity:
            raise RuntimeFault('WEEK_COPY_IDENTITY_CHANGED')
        row = self._readback(record, {self.identity_field: identity})
        if all(row['fields'].get(k) == v for k, v in fields.items()):
            return {'record_id': record, 'writes': 0}
        self._client.update_record(self.base, self.table, record, fields)
        self._readback(record, {**fields, self.identity_field: identity})
        return {'record_id': record, 'writes': 1, 'fields_sha256': fingerprint(fields)}
