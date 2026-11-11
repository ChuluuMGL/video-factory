"""Independent zero-generation deadline/archive checks and disabled unit plans."""
import re
import time
from pathlib import PurePosixPath

from .feishu_bridge import meta
from .runtime_store import RuntimeFault
from .script_week import PREFIX, DAY, DURATION, close


def housekeeping(store, project, archive, *, now=None):
    """No provider/token dependency. Revocation precedes fallible final export.

    Run independently of the paid worker, so an idle, crashed, or paused worker
    cannot prevent expiry. Export exceptions stay visible to the operator.
    """
    now = time.time() if now is None else now
    with store.connect() as db:
        week = meta(db, PREFIX + project)
    if not week or week['t0'] is None:
        return {'state': 'not_started', 'provider_requests': 0, 'archives': []}
    if week['t1'] - week['t0'] != DURATION:
        raise RuntimeFault('WEEK_CONFIGURATION_CHANGED')
    if now < week['t0']:
        return {'state': 'waiting_for_t0', 'provider_requests': 0, 'archives': []}
    expired = now >= week['t1']
    receipt = close(store, project, reason='deadline') if expired else None
    # The time argument is observational; close() independently checks the real
    # clock and cannot be tricked into early expiry by callers passing a future.
    count = min(7, int((now - week['t0']) // DAY))
    exports = [archive(day) for day in range(count)]
    if expired: exports.append(archive(None, final=True))
    return {'state': 'closed' if expired else week['state'], 'close': receipt,
            'provider_requests': 0, 'archives': exports}


def disabled_units(root, *, prefix='vf-shadow-week', t1=None):
    """Render only; never install/start units or grant authority.

    Staging needs no T0. At activation, t1 is supplied for a one-shot absolute
    deadline in addition to the minute watchdog. Host adapters must enforce the
    persisted approved contract, not command-line dates.
    """
    if (not isinstance(root, str) or not re.fullmatch(r'/[A-Za-z0-9_/-]+', root)
            or str(PurePosixPath(root)) != root or '..' in PurePosixPath(root).parts
            or not re.fullmatch(r'[a-z][a-z0-9-]{2,60}', prefix)):
        raise RuntimeFault('WEEK_UNIT_PATH_INVALID')
    if t1 is not None and (type(t1) is not int or t1 <= 0):
        raise RuntimeFault('WEEK_UNIT_DEADLINE_INVALID')
    units = {}
    for mode in ('tick', 'housekeeping'):
        name = prefix + '-' + mode
        # The watchdog has its own lock, never waits on a hung provider worker.
        # Runtime storage transactions are short and do not cover provider I/O.
        units[name+'.service'] = (
            '[Unit]\nDescription=Bounded shadow '+mode+'\n[Service]\nType=oneshot\n'
            f'WorkingDirectory={root}\nExecStart=/usr/bin/flock -n {root}/{mode}.lock '
            f'/usr/bin/python3 {root}/{mode}.py\nUMask=0077\nNoNewPrivileges=true\n'
            'Restart=no\nTimeoutStartSec=180\n')
        units[name+'.timer'] = (
            '[Unit]\nDescription=Bounded shadow '+mode+' timer\n[Timer]\n'
            'OnBootSec=30s\nOnUnitInactiveSec=60s\nAccuracySec=1s\n'
            f'Unit={name}.service\n')
    if t1 is not None:
        from datetime import datetime, timezone
        end = datetime.fromtimestamp(t1, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        units[prefix+'-deadline.timer'] = (
            '[Unit]\nDescription=Single 168-hour shadow deadline\n[Timer]\n'
            f'OnCalendar={end}\nPersistent=true\nAccuracySec=1s\n'
            f'Unit={prefix}-housekeeping.service\n')
    return units
