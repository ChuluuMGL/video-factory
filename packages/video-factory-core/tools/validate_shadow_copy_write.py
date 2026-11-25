"""One-shot Feishu test-table acceptance, using synthetic text and no models."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import subprocess
import sys
from tempfile import TemporaryDirectory
from pathlib import Path
import sqlite3
import time

from video_factory.feishu_bridge import meta, save
from video_factory.feishu_diagnostics import failure_context
from video_factory.feishu_client import resource
from video_factory.feishu_results_client import ResultClient
from video_factory.runtime_store import RuntimeFault, canonical, private_file
from video_factory.script_week_io import CopyOnlyWeekWriter, ReadOnlyWeekSource
from video_factory.script_week_projection import WeekProjection, projection_key

NEW = 'HD01-1254002-TEST-APPEND'
EXISTING = 'HD01-1254002-TEST-UPDATE'
SENTINEL = 'HD01-1254002-HUMAN-KEEP'
PROJECT = 'hd01_copy_acceptance'
LIMITS = {'GET': 15, 'POST': 1, 'PUT': 2}
RESTART_LIMIT_SECONDS = 35


def stopped(error, stage):
    context = failure_context(error)
    copy_stage = getattr(error, 'copy_stage', None)
    if copy_stage in ('update_preflight', 'update_request', 'update_readback'):
        stage = copy_stage
    report = {'state': 'stopped', 'error': context['error'], 'stage': stage}
    for name in ('method', 'http_status', 'feishu_code', 'request_id'):
        if name in context:
            report[name] = context[name]
    receipt = getattr(error, 'copy_receipt', None)
    if receipt and receipt['write_attempts'] > 0:
        report.update({name: receipt[name] for name in ('write_attempts', 'writes', 'unknown_writes')})
    return report


def fail_from_child(report):
    error = RuntimeFault(report['error'])
    error.request_context = {key: report[key] for key in
                             ('method', 'http_status', 'feishu_code', 'request_id')
                             if key in report}
    raise error


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def require(condition, code):
    if not condition:
        raise RuntimeFault(code)


class BoundedClient(ResultClient):
    def __init__(self, token, base, table, identity, output, *, limits=None, journal=None):
        super().__init__(token, read_attempts=1)
        self.path = f'/bitable/v1/apps/{base}/tables/{table}/records'
        self.schema_path = f'/bitable/v1/apps/{base}/tables/{table}/fields'
        self.base, self.table = base, table
        self.identity, self.output = identity, output
        self.allowed = {identity, output}
        require(len(self.allowed) == 2, 'VALIDATION_FIELD_CONFIGURATION')
        self.limits = dict(LIMITS if limits is None else limits)
        self.counts = dict.fromkeys(LIMITS, 0)
        self.journal = journal
        self.stage = 'preflight'
        self.human_check = None
        self.deadline = time.monotonic() + 540

    def reserve(self, method):
        require(time.monotonic() < self.deadline, 'VALIDATION_DEADLINE')
        require(self.counts[method] < self.limits[method],
                'VALIDATION_REQUEST_LIMIT')
        if self.journal is not None:
            self.journal.write(method + '\n')
            self.journal.flush()
            os.fsync(self.journal.fileno())
        self.counts[method] += 1

    def record(self, base, table, record):
        row = super().record(base, table, record)
        if self.human_check is not None:
            guarded_record, field = self.human_check
            if record == guarded_record:
                require(CopyOnlyWeekWriter._same_value(row['fields'].get(field), SENTINEL),
                        'VALIDATION_HUMAN_CHANGED')
        return row

    def checked_fields(self, body):
        require(isinstance(body, dict) and set(body) == {'fields'}
                and isinstance(body['fields'], dict) and bool(body['fields']),
                'VALIDATION_WRITE_BODY_INVALID')
        fields = body['fields']
        require(set(fields) <= self.allowed, 'VALIDATION_HUMAN_FIELD_DENIED')
        return fields

    def _get_once(self, path):
        route = path.split('?', 1)[0]
        require(route in (self.path, self.schema_path) or route.startswith(self.path + '/rec'),
                'VALIDATION_ROUTE_DENIED')
        self.reserve('GET')
        return super()._get_once(path)

    def post(self, path, body):
        require(path.split('?', 1)[0] == self.path + '/batch_create', 'VALIDATION_ROUTE_DENIED')
        require(isinstance(body, dict) and set(body) == {'records'}
                and isinstance(body['records'], list) and len(body['records']) == 1,
                'VALIDATION_WRITE_BODY_INVALID')
        self.checked_fields(body['records'][0])
        self.reserve('POST')
        return super().post(path, body)

    def put(self, path, body):
        require(path.split('?', 1)[0].startswith(self.path + '/rec'), 'VALIDATION_ROUTE_DENIED')
        fields = dict(self.checked_fields(body))
        if fields.get(self.output) == '':
            fields[self.output] = None
        self.reserve('PUT')
        return super().put(path, {'fields': fields})


class Ledger:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()


def verify_restart_child():
    """Isolated entry point with a read-only ledger and no write allowance."""
    try:
        token, base, table, identity, output, source, human, path, journal_path, get_limit = json.load(sys.stdin)
        with open(journal_path, 'a', encoding='ascii') as journal:
            client = BoundedClient(token, base, table, identity, output,
                                   limits={'GET': get_limit, 'POST': 0, 'PUT': 0}, journal=journal)
            uri = Path(path).resolve().as_uri() + '?mode=ro'
            db = sqlite3.connect(uri, uri=True)
            try:
                row = meta(db, projection_key(PROJECT, base, table, EXISTING))
            finally:
                db.close()
            require(row and row['state'] == 'created' and row['target'] == [base, table],
                    'VALIDATION_RESTART_LEDGER_INVALID')
            # Identity and output are the only writable columns in the harness.
            writer = CopyOnlyWeekWriter(client, source_base=source, copy_base=base,
                                        table=table, machine_fields=[identity, output],
                                        human_fields=[human], identity_field=identity)
            result = writer.update(row['record_id'], EXISTING, {identity: EXISTING, output: ''})
            require(result['writes'] == 0, 'VALIDATION_RESTART_WROTE')
        result = {'state': 'passed'}
    except Exception as error:
        result = stopped(error, 'restart_process')
    print(canonical(result), flush=True)
    return int(result['state'] != 'passed')


def verify_restart(client, ledger, source, human):
    remaining = client.deadline - time.monotonic()
    require(remaining > 0, 'VALIDATION_DEADLINE')
    budget = min(client.limits['GET'] - client.counts['GET'], 1)
    require(budget > 0, 'VALIDATION_REQUEST_LIMIT')
    with TemporaryDirectory(dir=Path(ledger.path).parent) as directory:
        path = Path(directory) / 'requests'
        args = (client.token, client.base, client.table, client.identity, client.output, source, human,
                str(ledger.path), str(path), budget)
        try:
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--restart-child'],
                                    input=canonical(args), text=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, timeout=min(remaining, RESTART_LIMIT_SECONDS))
        except subprocess.TimeoutExpired:
            raise RuntimeFault('VALIDATION_RESTART_TIMEOUT') from None
        finally:
            if path.exists():
                # The child flushes each reservation before I/O. A crash in the
                # gap can overcount an attempt, but cannot hide a sent request.
                client.counts['GET'] += path.read_text(encoding='ascii').splitlines().count('GET')
        if result.returncode < 0:
            raise RuntimeFault('VALIDATION_RESTART_CRASHED')
        try:
            report = json.loads(result.stdout)
            require(isinstance(report, dict), 'VALIDATION_RESTART_CRASHED')
        except ValueError:
            raise RuntimeFault('VALIDATION_RESTART_CRASHED') from None
        if result.returncode == 1 and report.get('state') == 'stopped':
            fail_from_child(report)
        require(result.returncode == 0 and report.get('state') == 'passed',
                'VALIDATION_RESTART_CRASHED')


def validate(client, ledger, base, table, source, identity, output, human, emit):
    with ledger.connect() as db:
        db.execute('CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        save(db, 'script:week:' + PROJECT, {'state': 'closed'})
        save(db, 'script:trial:' + PROJECT, {'enabled': False})
    writer = CopyOnlyWeekWriter(client, source_base=source, copy_base=base, table=table,
                                machine_fields=[identity, output], human_fields=[human],
                                identity_field=identity)
    reader = ReadOnlyWeekSource(client, base=base, tables={table: [identity, output, human]})
    same = writer._same_value

    def find(marker):
        return [row for row in reader.records(table) if same(row['fields'].get(identity), marker)]

    schema = client.get(client.schema_path + '?page_size=100')
    require(schema.get('has_more') is False, 'VALIDATION_SCHEMA_INCOMPLETE')
    columns = {field['field_name']: field['type'] for field in schema['items']}
    require(all(columns.get(name) == 1 for name in (identity, output, human)),
            'VALIDATION_TEXT_FIELDS_REQUIRED')
    initial = reader.records(table)
    require(len(initial) == 1, 'VALIDATION_TEST_TABLE_NOT_DEDICATED')
    seeded = initial[0]
    require(all(same(seeded['fields'].get(k), v) for k, v in
                {identity: EXISTING, output: 'A', human: SENTINEL}.items()),
            'VALIDATION_FIXTURE_NOT_READY')
    row_id = seeded['record_id']
    client.human_check = (row_id, human)
    with ledger.connect() as db:
        save(db, projection_key(PROJECT, base, table, EXISTING),
             {'state': 'created', 'record_id': row_id, 'target': [base, table],
              'initial_fields': {identity: EXISTING, output: 'A'}})

    def publish(projection, step, marker, value, expected):
        client.stage = step
        result = projection.publish(marker, {identity: marker, output: value})
        counts = (result['write_attempts'], result['writes'], result['unknown_writes'])
        require(counts == expected, 'VALIDATION_RECEIPT_MISMATCH')
        emit({'step': step, **dict(zip(('write_attempts', 'writes', 'unknown_writes'), counts))})

    projection = WeekProjection(ledger, PROJECT, writer, find)
    publish(projection, 'append', NEW, 'A', (1, 1, 0))
    publish(projection, 'same_input', NEW, 'A', (0, 0, 0))
    publish(projection, 'update', EXISTING, 'B', (1, 1, 0))
    publish(projection, 'clear', EXISTING, '', (1, 1, 0))
    client.stage = 'restart_process'
    verify_restart(client, ledger, source, human)
    emit({'step': 'restart_process', 'write_attempts': 0, 'writes': 0, 'unknown_writes': 0})
    client.stage = 'final_readback'
    final = reader.records(table)
    require(len(final) == 2, 'VALIDATION_ROW_COUNT_MISMATCH')
    expected = {NEW: 'A', EXISTING: ''}
    for row in final:
        fields = row['fields']
        marker = writer._text_value(fields.get(identity))
        require(marker in expected and same(fields.get(output), expected.pop(marker)),
                'VALIDATION_FINAL_READBACK_MISMATCH')
        if marker == EXISTING:
            require(row['record_id'] == row_id and same(fields.get(human), SENTINEL),
                    'VALIDATION_HUMAN_CHANGED')
    require(client.counts['POST'] == 1 and client.counts['PUT'] == 2,
            'VALIDATION_HTTP_COUNT_MISMATCH')
    return {'state': 'passed', 'http_attempts': client.counts, 'human_unchanged': True}


def main():
    if sys.argv[1:] == ['--restart-child']:
        return verify_restart_child()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner-confirm', action='store_true', required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--config-sha256', required=True)
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--new-state-dir', type=Path, required=True)
    args = parser.parse_args()
    client = None
    try:
        private_file(args.config)
        raw = args.config.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == args.config_sha256, 'VALIDATION_CONFIG_CHANGED')
        config = json.loads(raw)
        base = resource(config['test_base'])
        table = resource(config['test_table'], 'tbl')
        source = resource(config['production_base'])
        require(base != source and sha(base) == config['test_base_sha256']
                and sha(table) == config['test_table_sha256'], 'VALIDATION_TARGET_DENIED')
        private_file(args.token_file)
        # Existing scoped token only. No credential refresh or permission changes.
        client = BoundedClient(args.token_file.read_text().strip(), base, table,
                               config['identity_field'], config['output_field'])
        args.new_state_dir.mkdir(mode=0o700, exist_ok=False)
        (args.new_state_dir / 'tick.lock').touch(mode=0o600, exist_ok=False)
        runtime = args.new_state_dir / 'runtime'
        runtime.mkdir(mode=0o700)
        path = runtime / 'runtime.sqlite3'
        path.touch(mode=0o600, exist_ok=False)
        with (args.new_state_dir / 'tick.lock').open('rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = validate(client, Ledger(path), base, table, source,
                              config['identity_field'], config['output_field'], config['human_field'],
                              lambda step: print(canonical(step), flush=True))
    except Exception as error:
        result = stopped(error, client.stage if client else 'setup')
        if client:
            result['http_attempts'] = client.counts
    print(canonical(result), flush=True)
    return int(result['state'] != 'passed')


if __name__ == '__main__':
    raise SystemExit(main())
