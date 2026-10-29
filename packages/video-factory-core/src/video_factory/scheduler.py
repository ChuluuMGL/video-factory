"""Single-owner local scheduling over the existing ledger, not an approval API."""
from contextlib import contextmanager
import json
import os
import stat
import threading
import time

from .dispatch import authorize, dispatch_one
from .runtime_store import RuntimeFault, canonical, identifier, private_directory


@contextmanager
def runtime_lock(root):
    """Lock the whole SQLite runtime; never unlink the lock inode on exit."""
    import fcntl  # Keep existing Windows CLI commands importable.
    root = private_directory(root)
    fd = os.open(root / 'local-runtime.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise RuntimeFault('LOCAL_LOCK_PRIVATE_FILE_REQUIRED')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeFault('LOCAL_RUNTIME_ALREADY_RUNNING') from None
        yield
    finally:
        os.close(fd)


class Scheduler:
    def __init__(self, store, project, *, execution_token=None, master_key=None,
                 media_root=None, interval=5, dispatch=dispatch_one):
        identifier(project)
        if type(interval) not in (int, float) or not 1 <= interval <= 3600:
            raise RuntimeFault('LOCAL_INTERVAL_INVALID')
        self.store, self.project, self.interval = store, project, interval
        self.token, self.key, self.media_root = execution_token, master_key, media_root
        self.dispatch = dispatch
        self.enabled = execution_token is not None
        self._busy = threading.Lock()
        self._state_lock = threading.Lock()
        self._status = {'state': 'ready' if self.enabled else 'read_only',
                        'last_tick_at': None, 'last_task': None, 'last_error': None}
        with store.connect() as db:
            if not db.execute('SELECT 1 FROM projects WHERE id=?', (project,)).fetchone():
                raise RuntimeFault('PROJECT_MISSING')
            if self.enabled:
                authorize(db, self.token, project)
        if self.enabled and (not master_key or media_root is None):
            raise RuntimeFault('LOCAL_EXECUTION_INPUTS_REQUIRED')
        if self.enabled:
            private_directory(media_root)

    @property
    def cursor_key(self):
        return 'local:scheduler:cursor:' + self.project

    def _checkpoint(self, task):
        if task:
            identifier(task)
        with self.store.connect() as db:
            db.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                       (self.cursor_key, canonical({'after': task})))

    def _update(self, **fields):
        with self._state_lock:
            self._status.update(fields)

    def status(self):
        with self._state_lock:
            return {'project': self.project, **self._status,
                    'execution_enabled': self.enabled,
                    'requires_per_task_approval': True, 'automatic_resubmit': False,
                    'feishu_events': 'not_connected', 'feishu_writeback': 'not_enabled'}

    def tick(self):
        if not self.enabled:
            return self.status()
        if not self._busy.acquire(blocking=False):
            return {'state': 'busy'}
        try:
            self._update(state='running', last_tick_at=time.time(), last_error=None)
            with self.store.connect() as db:
                row = db.execute('SELECT value FROM meta WHERE key=?', (self.cursor_key,)).fetchone()
                after = json.loads(row[0])['after'] if row else ''
            result = self.dispatch(self.store, self.token, self.project, self.key, self.media_root,
                                   after=after, on_selected=self._checkpoint)
            if 'next_after' in result:
                self._checkpoint(result['next_after'])
            self._update(state='waiting', last_task=result.get('task'),
                         last_error='RECONCILIATION_REQUIRED' if result.get('blocked') else None)
        except Exception:
            # Never return exception text, provider bodies, credentials or paths.
            self._update(state='attention_required', last_error='SCHEDULER_STEP_FAILED')
        finally:
            self._busy.release()
        return self.status()

    def run(self, stop):
        try:
            while not stop.is_set():
                self.tick()
                # One tick after wake; no catch-up burst for missed intervals.
                stop.wait(self.interval)
        finally:
            self._update(state='stopped')
