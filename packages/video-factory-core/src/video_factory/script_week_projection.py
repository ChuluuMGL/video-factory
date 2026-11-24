"""Durable copy projection. Uncertain writes are read-reconciled, never resent."""
import re
import time
import uuid
from .feishu_bridge import meta, save
from .runtime_store import RuntimeFault, fingerprint


def _failure(error, phase, row):
    # Upstream text and arbitrary exception messages are never persisted.
    code = str(error)
    safe = code if isinstance(error, RuntimeFault) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,159}', code) else 'COPY_OPERATION_FAILED'
    receipt = {'at': time.time(), 'phase': phase, 'error': safe,
               'target_sha256': fingerprint(row['target']),
               'fields_sha256': fingerprint(row.get('pending_fields', row['initial_fields'])),
               'operation_id': row.get('update_ticket', row['ticket'])}
    stage = getattr(error, 'copy_stage', None)
    if stage in ('update_preflight', 'update_request', 'update_readback'):
        receipt['stage'] = stage
    context = getattr(error, 'request_context', None)
    if isinstance(context, dict):
        # The transport creates this bounded, content-free context.
        for k in ('method', 'operation', 'target_sha256', 'http_status', 'feishu_code', 'request_id'):
            v = context.get(k)
            if type(v) is int and k in ('http_status', 'feishu_code'):
                receipt[k] = v
            elif isinstance(v, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', v):
                receipt[k] = v
    return receipt


class WeekProjection:
    def __init__(self, store, project, writer, find):
        self.store, self.project, self.writer, self.find = store, project, writer, find

    def publish(self, identity, fields):
        key = 'script:week-projection:' + fingerprint([self.project, self.writer.base, self.writer.table, identity])
        if fields.get(self.writer.identity_field) != identity:
            raise RuntimeFault('WEEK_PROJECTION_IDENTITY_CHANGED')
        with self.store.connect() as db:
            row = meta(db, key)
            if row is None:
                row = {'state': 'intent', 'ticket': str(uuid.uuid4()), 'initial_fields': fields,
                       'target': [self.writer.base, self.writer.table]}
                save(db, key, row); fresh = True
            else:
                fresh = False
        if row['target'] != [self.writer.base, self.writer.table]:
            raise RuntimeFault('WEEK_PROJECTION_TARGET_CHANGED')
        phase = row['state']
        try:
            if row['state'] == 'intent':
                if fresh:
                    phase = 'append'
                    record = self.writer.append(row['initial_fields'], row['ticket'])['record_id']
                else:
                    phase = 'append_reconciliation'
                    found = self.find(identity)
                    if not isinstance(found, list) or len(found) != 1:
                        raise RuntimeFault('WEEK_APPEND_UNCERTAIN_REQUIRES_INSPECTION')
                    record = found[0]['record_id']
                    self.writer._readback(record, row['initial_fields'])
                row.update(state='created', record_id=record)
                with self.store.connect() as db: save(db, key, row)
            if row['state'] == 'update_intent':
                # A previous PUT may have committed. Never replay it, even if
                # the next tick supplies a different desired value.
                phase = 'update_reconciliation'
                self.writer._readback(row['record_id'], row['pending_fields'])
                row.update(state='created', last_reconciled_at=time.time())
                row.pop('pending_fields')
                with self.store.connect() as db: save(db, key, row)
            if row['state'] != 'created':
                raise RuntimeFault('WEEK_PROJECTION_STATE_INVALID')

            def before_write():
                row.update(state='update_intent', pending_fields=fields,
                           update_ticket=str(uuid.uuid4()))
                with self.store.connect() as db: save(db, key, row)

            phase = 'update'
            result = self.writer.update(row['record_id'], identity, fields, before_write=before_write)
            if row['state'] == 'update_intent':
                row.update(state='created', last_verified_fields_sha256=fingerprint(fields))
                row.pop('pending_fields')
                with self.store.connect() as db: save(db, key, row)
            return result
        except Exception as error:
            # Retain the first failure as well as the latest read reconciliation
            # failure, so a later tick cannot erase the original operation.
            failure = _failure(error, phase, row)
            row.setdefault('first_failure', failure)
            row['last_failure'] = failure
            with self.store.connect() as db: save(db, key, row)
            raise
