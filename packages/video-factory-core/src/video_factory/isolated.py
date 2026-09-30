"""Persistent mock worker. Imports no live connector and offers no provider route."""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import difflib
import hashlib
import json
import re
import sqlite3
from .canary import CanaryRuntime, CanaryTask, CanaryError


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


class Conflict(ValueError):
    pass


class Worker:
    def __init__(self, path, config):
        self.config = config
        if config.get('schema') != 'vf.mock.v1' or config.get('provider') != 'mock':
            raise Conflict('MOCK_CONFIG_REQUIRED')
        self.projects = config['projects']
        if len(self.projects) < 2:
            raise Conflict('TWO_FIXTURE_ADAPTERS_REQUIRED')
        assets = set()
        for name, adapter in self.projects.items():
            if not re.fullmatch(r'fixture_[a-z0-9_]+', name):
                raise Conflict('FIXTURE_PROJECT_REQUIRED')
            if set(adapter) != {'label', 'fields', 'rules', 'assets'} or set(adapter['fields']) != {'direction', 'sku'}:
                raise Conflict('INVALID_ADAPTER')
            if len(set(adapter['fields'].values())) != 2 or any(not isinstance(x, str) or not x for x in adapter['fields'].values()):
                raise Conflict('INVALID_FIELD_MAPPING')
            if not adapter['rules'] or not adapter['assets'] or any(not a.startswith(name + ':') for a in adapter['assets']):
                raise Conflict('PROJECT_ASSET_BOUNDARY')
            if assets.intersection(adapter['assets']):
                raise Conflict('SHARED_ASSET_FORBIDDEN')
            assets.update(adapter['assets'])
        self.db = sqlite3.connect(path, isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tasks(project TEXT, task TEXT, snapshot TEXT NOT NULL, PRIMARY KEY(project,task));
        CREATE TABLE IF NOT EXISTS commands(project TEXT, event TEXT, fingerprint TEXT, receipt TEXT, PRIMARY KEY(project,event));
        CREATE TABLE IF NOT EXISTS jobs(project TEXT, event TEXT, task TEXT, route TEXT, state TEXT, receipt TEXT, PRIMARY KEY(project,event));
        CREATE UNIQUE INDEX IF NOT EXISTS one_pending_task ON jobs(project,task) WHERE state='queued';
        CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY, time TEXT, project TEXT, task TEXT, event TEXT, kind TEXT, detail TEXT);
        CREATE TRIGGER IF NOT EXISTS no_audit_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'IMMUTABLE_AUDIT'); END;
        CREATE TRIGGER IF NOT EXISTS no_audit_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'IMMUTABLE_AUDIT'); END;
        ''')
        config_hash = hashlib.sha256(encode(config).encode()).hexdigest()
        with self.transaction():
            old = self.db.execute("SELECT value FROM metadata WHERE key='config_sha256'").fetchone()
            if old and old[0] != config_hash:
                raise Conflict('CONFIG_DRIFT_REQUIRES_NEW_SANDBOX')
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES('config_sha256',?)", (config_hash,))

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def audit(self, project, task, event, kind, detail):
        self.db.execute('INSERT INTO audit(time,project,task,event,kind,detail) VALUES(?,?,?,?,?,?)',
                        (now(), project, task, event, kind, encode(detail)))

    def identity(self, project, task):
        if project not in self.projects or not isinstance(task, str) or not re.fullmatch(r'sample_[a-z0-9_]{1,60}', task):
            raise Conflict('UNKNOWN_PROJECT_OR_INVALID_TASK')

    def task(self, project, task):
        self.identity(project, task)
        row = self.db.execute('SELECT snapshot FROM tasks WHERE project=? AND task=?', (project, task)).fetchone()
        if not row:
            raise Conflict('TASK_NOT_FOUND')
        return json.loads(row[0])

    def save(self, project, task):
        self.db.execute('INSERT INTO tasks VALUES(?,?,?) ON CONFLICT(project,task) DO UPDATE SET snapshot=excluded.snapshot',
                        (project, task.task_id, encode(asdict(task))))

    def receive(self, command, execution_id):
        if not isinstance(command, dict) or not isinstance(execution_id, str) or not execution_id:
            raise Conflict('INVALID_COMMAND_OR_EXECUTION')
        action = command.get('action')
        common = {'project', 'task', 'event', 'action', 'actor'}
        expected = common | ({'fields'} if action == 'submit' else {'target_version', 'feedback'} if action == 'reject' else set())
        if action not in {'submit', 'reject'} or set(command) != expected:
            raise Conflict('UNSUPPORTED_COMMAND_FIELDS')
        project, task, event = (command.get(k) for k in ('project', 'task', 'event'))
        self.identity(project, task)
        if not isinstance(event, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', event):
            raise Conflict('EVENT_ID_REQUIRED')
        if not isinstance(command['actor'], str) or not command['actor'].strip() or len(command['actor']) > 100:
            raise Conflict('ACTOR_REQUIRED')
        fingerprint = hashlib.sha256(encode(command).encode()).hexdigest()
        with self.transaction():
            old = self.db.execute('SELECT * FROM commands WHERE project=? AND event=?', (project, event)).fetchone()
            if old:
                if old['fingerprint'] != fingerprint:
                    raise Conflict('IDEMPOTENCY_INPUT_CHANGED')
                return json.loads(old['receipt'])
            if action == 'submit':
                if self.db.execute('SELECT 1 FROM tasks WHERE project=? AND task=?', (project, task)).fetchone():
                    raise Conflict('TASK_ALREADY_EXISTS')
                fields = command['fields']; mapping = self.projects[project]['fields']
                if not isinstance(fields, dict) or set(fields) != set(mapping.values()):
                    raise Conflict('PROJECT_FIELDS_MISMATCH')
                if any(not isinstance(v, str) or not v.strip() or len(v) > 3000 for v in fields.values()):
                    raise Conflict('INVALID_FIELD_VALUE')
                item = CanaryTask(task, fields[mapping['sku']], 'synthetic:' + project, fields[mapping['direction']])
                route = 'script_production'
            else:
                feedback = command['feedback']
                if not isinstance(feedback, str) or not feedback.strip() or len(feedback) > 3000:
                    raise Conflict('REJECTION_FEEDBACK_REQUIRED')
                item = CanaryTask(**self.task(project, task))
                runtime = CanaryRuntime([item])
                runtime.review(task, command['target_version'], 'reject', command['actor'], feedback)
                item.reviews[-1].update(time=now(), source_event=event, execution_id=execution_id)
                route = 'script_repair'
            self.save(project, item)
            receipt = {'project': project, 'task': task, 'event': event, 'state': 'queued',
                       'execution_id': execution_id, 'received_at': now(), 'simulation_only': True, 'provider_called': False}
            self.db.execute('INSERT INTO commands VALUES(?,?,?,?)', (project, event, fingerprint, encode(receipt)))
            self.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,NULL)', (project, event, task, route, 'queued'))
            self.audit(project, task, event, 'received_' + action, {'command': command, 'receipt': receipt, 'status': item.status})
            return receipt

    def process(self, project, event, execution_id, fault=None):
        # The mock computation and version commit are atomic. No network call is possible here.
        with self.transaction():
            job = self.db.execute('SELECT * FROM jobs WHERE project=? AND event=?', (project, event)).fetchone()
            if not job:
                raise Conflict('JOB_NOT_FOUND')
            if job['state'] == 'done':
                return json.loads(job['receipt'])
            item = CanaryTask(**self.task(project, job['task']))
            runtime = CanaryRuntime([item])
            runtime._claims = {e['input_sha256'] for e in item.executions}
            feedback = item.review_feedback
            previous = item.versions[-1]['content'] if item.versions else ''
            runtime.dispatch(job['route'], item.task_id)
            adapter = self.projects[project]
            scenes = [
                {'shot': 'title', 'text': '这是一份仅用于隔离测试的虚构商品介绍'},
                {'shot': 'product', 'text': '镜头展示虚构商品的外观，不作真实商品承诺'},
                {'shot': 'end', 'text': '没有购买引导，也不会发布到任何渠道'},
            ]
            if previous:
                scenes = json.loads(previous)['scenes']
            edits = [x.strip() for x in feedback.split('；') if x.strip()]
            supported = {'商品先展示', '每句少于十字', '结尾加演示结束'}
            if set(edits) - supported:
                raise Conflict('UNSUPPORTED_MOCK_FEEDBACK: use 商品先展示；每句少于十字；结尾加演示结束. Feedback remains queued.')
            if '商品先展示' in edits:
                scenes.sort(key=lambda scene: scene['shot'] != 'product')
            if '每句少于十字' in edits:
                compact = {'title': '虚构展示', 'product': '展示商品', 'end': '不作发布'}
                for scene in scenes:
                    scene['text'] = compact[scene['shot']]
            if '结尾加演示结束' in edits:
                scenes[-1]['text'] = '演示结束'
            content = json.dumps({'kind': 'mock_script', 'project': project, 'sku': item.sku_id,
                                  'direction': item.direction, 'rules': adapter['rules'],
                                  'assets': adapter['assets'], 'scenes': scenes}, ensure_ascii=False, indent=2) + '\n'
            version = item.versions[-1]
            version.update(content=content, created_at=now(), source_event=event, feedback=feedback,
                           diff=''.join(difflib.unified_diff(previous.splitlines(True), content.splitlines(True), fromfile='previous', tofile='current')))
            item.executions[-1].update(execution_id=execution_id, source_event=event, time=now(), provider_called=False)
            if fault == 'before_commit':
                raise RuntimeError('INJECTED_BEFORE_COMMIT')
            self.save(project, item)
            receipt = {'project': project, 'task': item.task_id, 'event': event, 'status': item.status,
                       'version': version['version_id'], 'execution_id': execution_id, 'completed_at': now(),
                       'simulation_only': True, 'provider_called': False, 'provider_cost': 0}
            self.db.execute("UPDATE jobs SET state='done',receipt=? WHERE project=? AND event=?", (encode(receipt), project, event))
            self.audit(project, item.task_id, event, 'generated_for_review', {'receipt': receipt, 'version': version})
            return receipt

    def snapshot(self):
        return {'simulation_only': True, 'provider_called': False, 'provider_cost': 0,
                'projects': self.projects,
                'tasks': [{'project': r['project'], **json.loads(r['snapshot'])} for r in self.db.execute('SELECT * FROM tasks ORDER BY project,task')],
                'pending': [dict(r) for r in self.db.execute("SELECT project,event,task FROM jobs WHERE state='queued'")],
                'audit': [{**dict(r), 'detail': json.loads(r['detail'])} for r in self.db.execute('SELECT * FROM audit ORDER BY seq')]}
