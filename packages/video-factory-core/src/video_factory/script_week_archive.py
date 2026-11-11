"""Private, allowlisted shadow exports; never writes a source or review table."""
import json
import os
from pathlib import Path
import time
from .runtime_store import RuntimeFault, canonical, fingerprint
from .feishu_bridge import meta
from .script_receipts import redact
from .script_week import PREFIX, DAY

OUTPUTS = ('video_script', 'caption_voiceover_script', 'generation_prompt')


def review_export(store, project, *, day=None, baselines=None, redact_values=()):
    if day is not None and (type(day) is not int or not 0 <= day < 7):
        raise RuntimeFault('WEEK_EXPORT_DAY_INVALID')
    from .script_trial import PREFIX as TRIAL
    from .script_jobs import key
    with store.connect() as db:
        week = meta(db, PREFIX + project); trial = meta(db, TRIAL + project)
        if not week or week['t0'] is None or not trial: raise RuntimeFault('WEEK_NOT_STARTED')
        rows = []
        for source_key, item in sorted(week['snapshots'].items()):
            snapshot = item['snapshot']; task = item['task']
            attempts = [r for r in trial['requests'].values() if r['task'] == task]
            first = min((r['submitted_at'] for r in attempts), default=item['admitted_at'])
            bucket = int((first - week['t0']) // DAY)
            if day is not None and bucket != day: continue
            job = meta(db, key(project, task, 1)) or {}
            current = store._current(db, project, task, 1)
            candidate = (job.get('result') or {}).get('structured_fields') or {}
            base = (baselines or {}).get(source_key) or {}
            matched = (base.get('input_sha256') == snapshot['input_sha256']
                       and base.get('source_version') == snapshot['source_version']
                       and set(base.get('outputs', {})) == set(OUTPUTS)
                       and base.get('unmodified') is True)
            failures = []
            ids = [rid for rid,r in trial['requests'].items() if r['task'] == task]
            for rid in ids:
                for receipt in meta(db, 'script:receipt:' + rid) or []:
                    if receipt.get('validation'):
                        failures.append({'request_id_sha256': rid,
                            'validation': {k:receipt['validation'].get(k) for k in ('field','code','reason')},
                            'model_content_redacted': receipt.get('model_content_redacted'),
                            'truncated': receipt.get('content_capture_truncated')})
            rows.append({'task': task, 'source_key_sha256': source_key, 'day': bucket + 1,
                'task_requirements': snapshot['brief'], 'input_summary': snapshot['input_summary'],
                'input_sha256': snapshot['input_sha256'], 'sku_id': snapshot['sku_id'],
                'baseline': {k:base['outputs'][k] if matched else None for k in OUTPUTS},
                'pairing_status': 'matched' if matched else 'unpaired_or_pending',
                'candidate': {k:candidate.get(k) for k in OUTPUTS},
                'status': current['state'], 'failure_class': job.get('submission_error'),
                'failures': failures, 'request_count': len(attempts),
                'usage_cost_upper_micro': sum(r['cost_upper_micro'] for r in attempts),
                'fee_status': 'usage_upper_bound_invoice_pending',
                'unknown_requests': sum(r['state'] != 'usage_accounted' for r in attempts)})
        value = {'schema': 1, 'contract_sha256': week['contract_sha256'],
                 't0': week['t0'], 't1': week['t1'], 'day': None if day is None else day + 1,
                 'exported_at': time.time(), 'state': week['state'], 'rows': rows,
                 'provider_requests': 0, 'table_writes': 0}
    def clean(value):
        if isinstance(value, str): return redact(value, redact_values)
        if isinstance(value, dict): return {k:clean(v) for k,v in value.items()}
        if isinstance(value, list): return [clean(v) for v in value]
        return value
    return clean(value)


def save_archive(directory, value):
    """Exclusive writes; late receipts create a version instead of overwriting."""
    path = Path(directory); path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink(): raise RuntimeFault('WEEK_ARCHIVE_PATH_INVALID')
    digest = fingerprint({k:v for k,v in value.items() if k != 'exported_at'})
    name = 'REVIEW_EXPORT_WEEK' if value['day'] is None else 'REVIEW_DAY_%02d' % value['day']
    initial = path / (name + '.json')
    for destination in (initial, path / (name + '.' + digest + '.json')):
        try:
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if destination.is_symlink(): raise RuntimeFault('WEEK_ARCHIVE_PATH_INVALID')
            old = json.loads(destination.read_text())
            if fingerprint({k:v for k,v in old.items() if k != 'exported_at'}) == digest:
                return {'path': str(destination), 'sha256': fingerprint(old), 'replayed': True}
            continue
        with os.fdopen(fd, 'w') as handle:
            handle.write(canonical(value) + '\n'); handle.flush(); os.fsync(handle.fileno())
        return {'path': str(destination), 'sha256': fingerprint(value), 'replayed': False}
    raise RuntimeFault('WEEK_ARCHIVE_CONFLICT')
