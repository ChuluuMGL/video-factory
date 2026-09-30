"""PostgreSQL ledger with the same transaction/state-machine contract as SQLite.

One advisory transaction lock serializes cooperating runtime writers. Provider
I/O must stay outside connect(). Database credentials are never returned.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import secrets

import psycopg
from psycopg.rows import dict_row
from psycopg import sql

from .runtime_store import RuntimeStore, RuntimeFault, SCHEMA, private_directory, private_file, identifier

PG_SCHEMA = SCHEMA.replace('INTEGER PRIMARY KEY AUTOINCREMENT', 'BIGSERIAL PRIMARY KEY').replace(' BLOB ', ' BYTEA ').replace(' REAL ', ' DOUBLE PRECISION ')
TABLES = ('meta', 'users', 'sessions', 'login_limits', 'vault', 'projects', 'tasks', 'events', 'audit')
LOCK_ID = 864213905


class Row(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class Cursor:
    def __init__(self, cursor):
        self.cursor = cursor

    def fetchone(self):
        row = self.cursor.fetchone()
        return Row(row) if row is not None else None

    def fetchall(self):
        return [Row(row) for row in self.cursor.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


class Connection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, statement, parameters=()):
        # Statements are application constants, never supplied by API callers.
        return Cursor(self.connection.execute(statement.replace('?', '%s'), parameters or None))


def read_dsn(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise RuntimeFault('DATABASE_CREDENTIAL_FILE_UNSAFE')
    private_file(path)
    if path.stat().st_size > 4096:
        raise RuntimeFault('DATABASE_CREDENTIAL_FILE_TOO_LARGE')
    return path.read_text().strip()


class PostgresStore(RuntimeStore):
    def __init__(self, root, dsn_file=None, *, validate=True):
        self.root = private_directory(root)
        self.dsn_file = dsn_file or os.environ.get('VF_DATABASE_URL_FILE')
        if not self.dsn_file:
            raise RuntimeFault('DATABASE_CREDENTIAL_FILE_REQUIRED')
        if validate:
            with self.connect() as db:
                exists = db.execute("SELECT to_regclass('public.meta') AS present").fetchone()[0]
                if not exists:
                    raise RuntimeFault('RUNTIME_NOT_INSTALLED')
                row = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
                if row is None or row[0] != '1':
                    raise RuntimeFault('SCHEMA_UNSUPPORTED')

    @contextmanager
    def connect(self):
        try:
            with psycopg.connect(read_dsn(self.dsn_file), row_factory=dict_row, connect_timeout=10,
                                 options='-c statement_timeout=30000 -c lock_timeout=10000') as connection:
                connection.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK_ID,))
                yield Connection(connection)
        except psycopg.Error:
            # psycopg error strings can contain connection parameters.
            raise RuntimeFault('DATABASE_OPERATION_FAILED') from None

    @staticmethod
    def create_schema(db):
        if db.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'").fetchone()[0]:
            raise RuntimeFault('DATABASE_NOT_EMPTY')
        for statement in PG_SCHEMA.split(';'):
            if statement.strip():
                db.execute(statement)

    @classmethod
    def install(cls, root, deployment, password, dsn_file=None):
        identifier(deployment)
        cls.validate_password(password)
        store = cls(root, dsn_file, validate=False)
        with store.connect() as db:
            cls.create_schema(db)
            db.execute("INSERT INTO meta VALUES('schema','1')")
            db.execute("INSERT INTO meta VALUES('deployment',?)", (deployment,))
            salt = secrets.token_bytes(16)
            db.execute("INSERT INTO users(name,salt,password,role) VALUES(?,?,?,'admin')",
                       ('admin', salt, cls.password_hash(password, salt)))
        return cls(root, dsn_file)

    @classmethod
    def migrate_sqlite(cls, source_root, root, dsn_file=None):
        """Copy an existing ledger into an EMPTY PG database, in one transaction.

        Source is retained untouched for offline rollback. Target sessions are
        revoked. Switching services is an explicit separate step.
        """
        source = RuntimeStore(source_root)
        target = cls(root, dsn_file, validate=False)
        counts = {}
        with source.connect() as source_db, target.connect() as target_db:
            if source_db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise RuntimeFault('SOURCE_DATABASE_INTEGRITY_FAILED')
            cls.create_schema(target_db)
            for table in TABLES:
                rows = source_db.execute('SELECT * FROM '+table).fetchall()
                counts[table] = len(rows)
                if table == 'sessions':
                    continue
                for row in rows:
                    columns = list(row.keys())
                    statement = sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(
                        sql.Identifier(table), sql.SQL(',').join(map(sql.Identifier, columns)),
                        sql.SQL(',').join(sql.Placeholder() for _ in columns))
                    target_db.connection.execute(statement, [row[column] for column in columns])
            target_db.execute("SELECT setval('audit_seq_seq', COALESCE((SELECT max(seq) FROM audit),0)+1, false)")
            cls.invalidate_worker_approvals(target_db)
            cls.audit(target_db, 'os_owner', 'sqlite_migration_revoke_sessions')
        return {'migrated': True, 'source_preserved': True, 'sessions_revoked': True,
                'source_rows': counts, 'doctor': cls(root, dsn_file).doctor()}

    def doctor(self):
        with self.connect() as db:
            db.execute('SELECT 1').fetchone()
            states = {row[0]: row[1] for row in db.execute('SELECT state,count(*) FROM tasks GROUP BY state')}
            deployment = db.execute("SELECT value FROM meta WHERE key='deployment'").fetchone()[0]
        return {'database_backend': 'postgresql', 'database_connection': 'ok', 'deployment': deployment,
                'schema': 1, 'task_states': states, 'paid_adapter': 'disabled', 'feishu_identity': 'not_connected'}


def selected_store():
    return PostgresStore if os.environ.get('VF_DATABASE_URL_FILE') else RuntimeStore
