"""Inspect and explicitly resolve stopped shadow-copy intents."""
import argparse
from contextlib import closing
import fcntl
from pathlib import Path
import sqlite3
import time
import uuid

from .feishu_bridge import meta, save
from .feishu_client import FeishuClient, resource
from .runtime_store import RuntimeFault, canonical, fingerprint, private_file
from .script_week_io import CopyOnlyWeekWriter, ReadOnlyWeekSource
from .script_week_projection import projection_key


def observe_intent(row, identity, identity_field, record, find):
    state = row.get('state')
    report = {'state': state, 'classification': 'neither'}
    if state not in ('intent', 'update_intent'):
        return report, None
    update = state == 'update_intent'
    ticket = row['update_ticket' if update else 'ticket']
    pending = row['pending_fields' if update else 'initial_fields']
    prior = row.get('prior_fields') if update else None
    if not update and row.get('prior_absent') is True:
        prior = {'absent': True}
    report.update(ticket=ticket, pending_sha256=fingerprint(pending),
                  prior_sha256=fingerprint(prior) if prior is not None else None)
    if pending.get(identity_field) != identity:
        return report, None
    try:
        if update:
            actual = record(row['record_id'])
            if actual['record_id'] != row['record_id']:
                return report, None
        else:
            found = find(identity)
            if found == [] and prior is not None:
                report['classification'] = 'matches_prior'
                return report, None
            if not isinstance(found, list) or len(found) != 1:
                return report, None
            actual = found[0]
        record_id = resource(actual['record_id'], 'rec')
        fields = actual['fields']
        same = CopyOnlyWeekWriter._same_value
        if not same(fields.get(identity_field), identity):
            return report, None
        current = {name: fields.get(name) for name in pending}
        report['current_sha256'] = fingerprint(current)
        if all(same(current[name], value) for name, value in pending.items()):
            report['classification'] = 'matches_pending'
        elif update and isinstance(prior, dict) and prior.keys() == pending.keys() and all(
            same(current[name], CopyOnlyWeekWriter._text_value(value)
                 if isinstance(pending[name], str) else value)
            for name, value in prior.items()
        ):
            report['classification'] = 'matches_prior'
        return report, record_id
    except Exception:
        return report, None


def require_stopped(db, project):
    week = meta(db, 'script:week:' + project)
    trial = meta(db, 'script:trial:' + project)
    if not week or week.get('state') != 'closed' or not trial or trial.get('enabled') is not False:
        raise RuntimeFault('SHADOW_WINDOW_NOT_STOPPED')


def recover(db, *, project, base, table, identity, identity_field, record, find,
            resolve=False, owner_confirm=False, ticket=None):
    require_stopped(db, project)
    key = projection_key(project, base, table, identity)
    row = meta(db, key)
    if not row or row.get('target') != [base, table]:
        report, record_id = {'state': 'not_found', 'classification': 'neither'}, None
    elif resolve and (not owner_confirm or not ticket or ticket != row.get(
        'update_ticket' if row.get('state') == 'update_intent' else 'ticket'
    )):
        report, record_id = {'state': row['state'], 'classification': 'neither'}, None
    else:
        report, record_id = observe_intent(row, identity, identity_field, record, find)
    if not resolve:
        return report

    decision = {'matches_pending': 'written', 'matches_prior': 'abandoned'}.get(
        report['classification'], 'refused'
    )
    receipt_id = str(uuid.uuid4())
    receipt = dict(report, receipt_id=receipt_id, decision=decision, at=time.time())
    if ticket:
        receipt['submitted_ticket_sha256'] = fingerprint(ticket)
    if decision != 'refused':
        if decision == 'written':
            row.update(state='created', record_id=record_id,
                       last_verified_fields_sha256=report['pending_sha256'])
        else:
            row['state'] = 'abandoned'
        row['resolution_receipt_id'] = receipt_id
        row.pop('pending_fields', None)
        row.pop('prior_fields', None)
        save(db, key, row)
    save(db, 'script:week-projection-resolution:' + receipt_id, receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('inspect', 'resolve'))
    for option in ('deployment-root', 'project', 'copy-base', 'copy-table', 'identity',
                   'identity-field', 'token-file'):
        parser.add_argument('--' + option, required=True)
    parser.add_argument('--owner-confirm', action='store_true')
    parser.add_argument('--ticket')
    args = parser.parse_args()
    try:
        base, table = resource(args.copy_base), resource(args.copy_table, 'tbl')
        root = Path(args.deployment_root).resolve(strict=True)
        path = root / 'runtime/runtime.sqlite3'
        private_file(path)
        token_path = Path(args.token_file)
        private_file(token_path)
        client = FeishuClient(token_path.read_text().strip())
        resolve = args.action == 'resolve'
        # Do not create a replacement lock inode. Use the exact deployment tick lock.
        with (root / 'tick.lock').open('rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            uri = path.as_uri() + ('?mode=rw' if resolve else '?mode=ro')
            with closing(sqlite3.connect(uri, uri=True)) as db, db:
                db.execute('BEGIN IMMEDIATE' if resolve else 'BEGIN')
                def find(identity):
                    names = [args.identity_field]
                    row = meta(db, projection_key(args.project, base, table, identity))
                    names.extend(name for name in row['initial_fields'] if name not in names)
                    reader = ReadOnlyWeekSource(client, base=base, tables={table: names})
                    return [item for item in reader.records(table) if CopyOnlyWeekWriter._same_value(
                        item['fields'].get(args.identity_field), identity)]

                report = recover(db, project=args.project, base=base, table=table,
                                 identity=args.identity, identity_field=args.identity_field,
                                 record=lambda rid: client.record(base, table, rid), find=find,
                                 resolve=resolve, owner_confirm=args.owner_confirm, ticket=args.ticket)
        print(canonical(report))
        return int(report.get('decision') == 'refused')
    except Exception:
        print(canonical({'decision': 'refused', 'error': 'SHADOW_RECOVERY_UNAVAILABLE'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
