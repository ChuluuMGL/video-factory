"""Installable n8n-free local rehearsal using the real ledger and scheduler.

OS owner commands only. No Feishu, provider transport, arbitrary code or shell.
This profile deliberately has no conversion switch to paid/production mode.
"""
import json
import os
from pathlib import Path
import secrets
import signal
import sys
import threading
import uuid

from . import automation
from .dispatch import authorize
from .local_runtime import serve
from .runtime_cli import secret_input
from .runtime_store import (RuntimeFault, RuntimeStore, canonical, exclusive_write,
                            fingerprint, identifier)

PROFILE = 'local:fake-only:v1'
QUEUE = 'local:fake-script:'


def register(commands):
    p = commands.add_parser('local', help='n8n-free local fake-only installation and script rehearsal')
    p.add_argument('action', choices=('install', 'up', 'status', 'plan', 'review'))
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--project', default='local_trial')
    p.add_argument('--task', default='trial_001')
    p.add_argument('--brief', default='本地假模型测试：展示一个日常使用场景。')
    p.add_argument('--expected-revision', type=int, default=0)
    p.add_argument('--request-id')
    p.add_argument('--decision', choices=('accept', 'reject'))
    p.add_argument('--feedback', default='')
    p.add_argument('--port', type=int, default=8788)
    p.add_argument('--interval', type=float, default=1)


def profile(store):
    with store.connect() as db:
        row = db.execute('SELECT value FROM meta WHERE key=?', (PROFILE,)).fetchone()
        if not row:
            raise RuntimeFault('LOCAL_FAKE_PROFILE_REQUIRED')
        value = json.loads(row[0])
        if set(value) != {'project', 'mode'} or value['mode'] != 'fake-only':
            raise RuntimeFault('LOCAL_FAKE_PROFILE_INVALID')
        identifier(value['project'])
        projects = db.execute('SELECT id FROM projects').fetchall()
        if [p[0] for p in projects] != [value['project']]:
            raise RuntimeFault('LOCAL_FAKE_PROJECT_BINDING_CHANGED')
        return value


def install(root, project):
    identifier(project)
    if not root.is_absolute() or root.resolve() != root or not root.parent.is_dir():
        raise RuntimeFault('NEW_ABSOLUTE_LOCAL_DIRECTORY_REQUIRED')
    # Never adopt, clear, chmod or overwrite an existing production/test directory.
    root.mkdir(mode=0o700)
    password = secrets.token_urlsafe(40)
    exclusive_write(root / 'owner.password', password.encode())
    store = RuntimeStore.install(root, 'local_fake_only', password)
    admin = store.login('admin', password)['token']
    store.put_project(admin, project, {'video_route': 'deferred',
                      'credential_ref': 'secret:fake_only', 'billing_owner': 'local_owner'})
    with store.connect() as db:
        db.execute('INSERT INTO meta VALUES(?,?)', (PROFILE, canonical({'project': project, 'mode': 'fake-only'})))
    reader = automation.issue(store, admin, project, ttl_hours=168, scope='agent_read')
    exclusive_write(root / 'agent-read.token', reader['token'].encode())
    return {'installed': True, 'project': project, 'mode': 'fake-only', 'n8n_required': False,
            'real_model_calls': 0, 'feishu': 'not_connected', 'production_ready': False,
            'next': ['local up', 'local plan', 'local status', 'local review']}


def owner(store):
    return store.login('admin', secret_input(store.root / 'owner.password', ''))['token']


def plan(store, admin, project, task, revision, brief, request):
    identifier(task); identifier(request)
    if type(revision) is not int or revision < 0 or not isinstance(brief, str) or not 1 <= len(brief) <= 1000:
        raise RuntimeFault('LOCAL_PLAN_INPUT_INVALID')
    key = QUEUE + project + ':' + request
    digest = fingerprint([project, task, revision, brief])
    with store.connect() as db:
        actor = store.authorize(db, admin, 'admin')
        prior = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        if prior:
            result = json.loads(prior[0])
            if result['digest'] != digest: raise RuntimeFault('LOCAL_REQUEST_CONFLICT')
            return result
        current = db.execute('SELECT revision,state FROM tasks WHERE project=? AND id=? ORDER BY revision DESC LIMIT 1',
                             (project, task)).fetchone()
        if (current['revision'] if current else 0) != revision:
            raise RuntimeFault('STALE_TASK_REVISION')
        if current and current['state'] != 'rejected':
            raise RuntimeFault('LOCAL_REJECT_BEFORE_REVISING')
        jobs = db.execute('SELECT value FROM meta WHERE key LIKE ?', (QUEUE + '%',)).fetchall()
        if len(jobs) >= 100: raise RuntimeFault('LOCAL_TRIAL_LIMIT_REACHED')
        if any((j := json.loads(r[0]))['task'] == task and j['status'] == 'queued' for r in jobs):
            raise RuntimeFault('LOCAL_TASK_ALREADY_QUEUED')
        result = {'request': request, 'task': task, 'expected_revision': revision, 'brief': brief,
                  'digest': digest, 'status': 'queued', 'provider': 'fake-only', 'real_model_calls': 0}
        db.execute('INSERT INTO meta VALUES(?,?)', (key, canonical(result)))
        store.audit(db, actor, 'local_fake_plan', project, task, revision)
        return result


def fake_dispatch(store, token, project, master_key, media_root, *, after=None, on_selected=None):
    # A deterministic fake model runs inside the same transaction as its result.
    # No real transport is constructed, so restart cannot duplicate a submission.
    if profile(store)['project'] != project: raise RuntimeFault('LOCAL_PROJECT_DENIED')
    with store.connect() as db:
        actor = authorize(db, token, project)
        for row in db.execute('SELECT key,value FROM meta WHERE key LIKE ? ORDER BY key', (QUEUE + project + ':%',)).fetchall():
            job = json.loads(row['value'])
            if job['status'] != 'queued': continue
            task, revision = job['task'], job['expected_revision']
            feedback = ''
            if revision:
                reviews = [json.loads(r[0]) for r in db.execute('SELECT receipt FROM events')]
                feedback = next((r['feedback'] for r in reviews if r['project'] == project and r['task'] == task
                                 and r['revision'] == revision and r['state'] == 'rejected'), '')
                if not feedback: raise RuntimeFault('LOCAL_REJECTION_MISSING')
            script = '[假模型脚本 v%d]\n%s\n%s' % (revision + 1, job['brief'],
                      '按审核意见修订：' + feedback if feedback else '开场：日常场景。中段：产品演示。结尾：自然收束。')
            receipt = store._create_task(db, actor, project, task,
                         {'sku_id': 'synthetic_sku', 'script': script, 'source_revision': str(revision + 1)}, revision)
            job.update(status='completed', revision=receipt['revision'], script_sha256=fingerprint(script))
            db.execute('UPDATE meta SET value=? WHERE key=?', (canonical(job), row['key']))
            store.audit(db, actor, 'local_fake_script_generated', project, task, receipt['revision'])
            return {'task': task, 'revision': receipt['revision'], 'real_model_calls': 0, 'next_after': ''}
    return {'status': 'idle', 'next_after': '', 'real_model_calls': 0}


def status(store, project):
    with store.connect() as db:
        tasks = [dict(r) for r in db.execute('SELECT id,revision,state,input FROM tasks WHERE project=? ORDER BY id,revision', (project,))]
        reviews = [json.loads(r[0]) for r in db.execute('SELECT receipt FROM events')]
        jobs = [json.loads(r[0]) for r in db.execute('SELECT value FROM meta WHERE key LIKE ?', (QUEUE + project + ':%',))]
    return {'project': project, 'mode': 'fake-only', 'n8n_required': False, 'tasks': tasks,
            'reviews': reviews, 'plans': jobs, 'real_model_calls': 0, 'production_writes': 0,
            'feishu': 'not_connected', 'video_generation': 'not_enabled', 'production_ready': False}


def cli(args):
    emit = lambda value: print(canonical(value), flush=True)
    try:
        if sys.platform not in ('linux', 'darwin') or os.environ.get('VF_DATABASE_URL_FILE') or os.environ.get('VF_CONTAINER_MODE') == '1':
            raise RuntimeFault('LOCAL_SQLITE_PLATFORM_REQUIRED')
        if args.action == 'install':
            emit(install(args.root, args.project)); return 0
        store = RuntimeStore(args.root)
        project = profile(store)['project']
        if project != args.project: raise RuntimeFault('LOCAL_PROJECT_DENIED')
        if args.action == 'status': emit(status(store, project)); return 0
        admin = owner(store)
        if args.action == 'plan':
            emit(plan(store, admin, project, args.task, args.expected_revision, args.brief,
                      args.request_id or str(uuid.uuid4()))); return 0
        if args.action == 'review':
            if not args.request_id or not args.decision: raise RuntimeFault('LOCAL_REVIEW_INPUT_REQUIRED')
            emit(store.review(admin, args.request_id, project, args.task, args.expected_revision,
                              'script', args.decision, args.feedback)); return 0
        stop = threading.Event()
        def deny_external(event, values):
            if event.startswith(('socket.connect', 'socket.getaddrinfo', 'subprocess.', 'os.system', 'os.exec', 'os.spawn')):
                raise RuntimeFault('LOCAL_FAKE_EXTERNAL_IO_DISABLED')
        sys.addaudithook(deny_external)
        token = automation.issue_execution(store, admin, project)['token']
        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            for sig in previous: signal.signal(sig, lambda *_: stop.set())
            serve(store, project, args.port, stop, execution_token=token, master_key=b'fake-only-no-provider-key',
                  media_root=store.root, interval=args.interval, dispatch=fake_dispatch,
                  on_ready=lambda value: emit({**value, 'scope': 'local_fake_only', 'n8n_required': False,
                                               'real_model_calls': 0, 'video_generation': 'not_enabled'}))
        finally:
            automation.revoke(store, admin, __import__('hashlib').sha256(token.encode()).hexdigest())
            for sig, handler in previous.items(): signal.signal(sig, handler)
        emit({'status': 'stopped', 'mode': 'fake-only'}); return 0
    except (RuntimeFault, FileExistsError) as error:
        emit({'error': 'LOCAL_DIRECTORY_ALREADY_EXISTS' if isinstance(error, FileExistsError) else str(error)}); return 2
    except Exception:
        emit({'error': 'LOCAL_SANDBOX_FAILED', 'existing_data_preserved': True}); return 2
