"""One-shot Feishu test-table acceptance, using synthetic text and no models."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
from multiprocessing import get_context
from pathlib import Path
import sqlite3
import time

from video_factory.feishu_bridge import save
from video_factory.feishu_client import resource
from video_factory.feishu_results_client import ResultClient
from video_factory.runtime_store import RuntimeFault, canonical, private_file
from video_factory.script_week_io import CopyOnlyWeekWriter, ReadOnlyWeekSource
from video_factory.script_week_projection import WeekProjection, projection_key

NEW = 'HD01-1254002-TEST-APPEND'
EXISTING = 'HD01-1254002-TEST-UPDATE'
SENTINEL = 'HD01-1254002-HUMAN-KEEP'
PROJECT = 'hd01_copy_acceptance'


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def require(condition, code):
    if not condition:
        raise RuntimeFault(code)


class BoundedClient(ResultClient):
    def __init__(self, token, base, table):
        super().__init__(token, read_attempts=1)
        self.path = f'/bitable/v1/apps/{base}/tables/{table}/records'
        self.schema_path = f'/bitable/v1/apps/{base}/tables/{table}/fields'
        self.counts = {'GET': 0, 'POST': 0, 'PUT': 0}
        self.deadline = time.monotonic() + 540

    def reserve(self, method):
        require(time.monotonic() < self.deadline, 'VALIDATION_DEADLINE')
        require(self.counts[method] < {'GET': 15, 'POST': 1, 'PUT': 2}[method],
                'VALIDATION_REQUEST_LIMIT')
        self.counts[method] += 1

    def _get_once(self, path):
        route = path.split('?', 1)[0]
        require(route in (self.path, self.schema_path) or route.startswith(self.path + '/rec'),
                'VALIDATION_ROUTE_DENIED')
        self.reserve('GET')
        return super()._get_once(path)

    def post(self, path, body):
        require(path.split('?', 1)[0] == self.path + '/batch_create', 'VALIDATION_ROUTE_DENIED')
        self.reserve('POST')
        return super().post(path, body)

    def put(self, path, body):
        require(path.split('?', 1)[0].startswith(self.path + '/rec'), 'VALIDATION_ROUTE_DENIED')
        self.reserve('PUT')
        return super().put(path, body)


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


def validate(client, ledger, base, table, source, identity, output, human, emit):
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
    with ledger.connect() as db:
        db.execute('CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        save(db, 'script:week:' + PROJECT, {'state': 'closed'})
        save(db, 'script:trial:' + PROJECT, {'enabled': False})
        save(db, projection_key(PROJECT, base, table, EXISTING),
             {'state': 'created', 'record_id': row_id, 'target': [base, table],
              'initial_fields': {identity: EXISTING, output: 'A'}})

    def publish(projection, step, marker, value, expected):
        result = projection.publish(marker, {identity: marker, output: value})
        counts = (result['write_attempts'], result['writes'], result['unknown_writes'])
        require(counts == expected, 'VALIDATION_RECEIPT_MISMATCH')
        emit({'step': step, **dict(zip(('write_attempts', 'writes', 'unknown_writes'), counts))})

    projection = WeekProjection(ledger, PROJECT, writer, find)
    publish(projection, 'append', NEW, 'A', (1, 1, 0))
    publish(projection, 'same_input', NEW, 'A', (0, 0, 0))
    publish(projection, 'update', EXISTING, 'B', (1, 1, 0))
    require(same(client.record(base, table, row_id)['fields'].get(human), SENTINEL),
            'VALIDATION_HUMAN_CHANGED')
    publish(projection, 'clear', EXISTING, '', (1, 1, 0))
    # Separate Linux process. It opens a new SQLite connection and inherits
    # the exhausted write budget, so even a regression cannot issue a write.
    ctx = get_context('fork')
    receive, send = ctx.Pipe(duplex=False)

    def recheck():
        try:
            replay = WeekProjection(Ledger(ledger.path), PROJECT, writer, find)
            result = replay.publish(EXISTING, {identity: EXISTING, output: ''})
            send.send((result['write_attempts'], result['writes'], result['unknown_writes'],
                       client.counts))
        except Exception:
            send.send((None, None, None, client.counts))
        finally:
            send.close()

    child = ctx.Process(target=recheck)
    child.start()
    send.close()
    try:
        require(receive.poll(35), 'VALIDATION_RESTART_TIMEOUT')
        attempts, writes, unknown, child_counts = receive.recv()
        client.counts.update(child_counts)
        child.join(5)
        require(child.exitcode == 0 and (attempts, writes, unknown) == (0, 0, 0),
                'VALIDATION_RESTART_WROTE')
    finally:
        receive.close()
        if child.is_alive():
            child.terminate()
        child.join()
    emit({'step': 'restart_process', 'write_attempts': 0, 'writes': 0, 'unknown_writes': 0})
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
        client = BoundedClient(args.token_file.read_text().strip(), base, table)
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
        result = {'state': 'stopped'}
        if client:
            result['http_attempts'] = client.counts
        # Only projection-generated counters are emitted; no exception text.
        if hasattr(error, 'copy_receipt'):
            result.update(error.copy_receipt)
    print(canonical(result), flush=True)
    return int(result['state'] != 'passed')


if __name__ == '__main__':
    raise SystemExit(main())
