"""Durable copy projection. Uncertain writes are read-reconciled, never resent."""
import copy
import time
import uuid
from .feishu_bridge import meta, save
from .runtime_store import RuntimeFault, fingerprint
from .feishu_diagnostics import failure_context


def _failure(error, phase, row, attempt_id, desired):
    receipt = failure_context(error)
    receipt.update(at=time.time(), phase=phase, attempt_id=attempt_id,
                   projection_target_sha256=fingerprint(row['target']))
    # An unresolved write keeps its frozen identity. A preflight failure belongs
    # to this new read attempt, never to an earlier successful PUT.
    if phase in ('append', 'append_reconciliation'):
        operation_id, fields = row['ticket'], row['initial_fields']
    elif row['state'] == 'update_intent':
        operation_id, fields = row['update_ticket'], row['pending_fields']
    else:
        operation_id, fields = attempt_id, desired
    receipt.update(operation_id=operation_id, fields_sha256=fingerprint(fields))
    stage = getattr(error, 'copy_stage', None)
    if stage in ('update_preflight', 'update_request', 'update_readback'):
        receipt['stage'] = stage
    return receipt


def projection_key(project, base, table, identity):
    return 'script:week-projection:' + fingerprint([project, base, table, identity])


class WeekProjection:
    def __init__(self, store, project, writer, find):
        self.store, self.project, self.writer, self.find = store, project, writer, find

    def publish(self, identity, fields):
        fields = copy.deepcopy(fields)
        attempt_id = str(uuid.uuid4())
        counts = {'writes': 0, 'write_attempts': 0, 'unknown_writes': 0}
        key = projection_key(self.project, self.writer.base, self.writer.table, identity)
        if fields.get(self.writer.identity_field) != identity:
            raise RuntimeFault('WEEK_PROJECTION_IDENTITY_CHANGED')
        with self.store.connect() as db:
            row = meta(db, key)
            if row is None:
                row = {'state': 'intent', 'ticket': str(uuid.uuid4()), 'initial_fields': fields,
                       'target': [self.writer.base, self.writer.table], 'created_at': time.time()}
                save(db, key, row); fresh = True
            else:
                fresh = False
        if row['target'] != [self.writer.base, self.writer.table]:
            raise RuntimeFault('WEEK_PROJECTION_TARGET_CHANGED')
        if row['state'] == 'abandoned':
            raise RuntimeFault('WEEK_PROJECTION_INTENT_ABANDONED')
        phase = row['state']
        try:
            if row['state'] == 'intent':
                if fresh:
                    phase = 'append'
                    found = self.find(identity)
                    if not isinstance(found, list) or found:
                        raise RuntimeFault('WEEK_APPEND_PREFLIGHT_NOT_EMPTY')

                    def append_attempt():
                        row['prior_absent'] = True
                        with self.store.connect() as db:
                            save(db, key, row)
                        counts['write_attempts'] += 1
                    appended = self.writer.append(row['initial_fields'], row['ticket'], before_write=append_attempt)
                    record = appended['record_id']
                    counts['writes'] += appended['writes']
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
                row.pop('prior_fields', None)
                with self.store.connect() as db: save(db, key, row)
            if row['state'] != 'created':
                raise RuntimeFault('WEEK_PROJECTION_STATE_INVALID')

            def before_write(prior):
                row.update(state='update_intent', pending_fields=copy.deepcopy(fields),
                           prior_fields=prior,
                           update_ticket=str(uuid.uuid4()))
                with self.store.connect() as db: save(db, key, row)
                counts['write_attempts'] += 1

            phase = 'update'
            result = self.writer.update(row['record_id'], identity, fields, capture_prior=before_write)
            counts['writes'] += result['writes']
            if row['state'] == 'update_intent':
                row.update(state='created', last_verified_fields_sha256=fingerprint(fields))
                row.pop('pending_fields')
                row.pop('prior_fields', None)
                with self.store.connect() as db: save(db, key, row)
            return {**result, **counts}
        except Exception as error:
            # Retain the first failure as well as the latest read reconciliation
            # failure, so a later tick cannot erase the original operation.
            counts['unknown_writes'] = counts['write_attempts'] - counts['writes']
            failure = _failure(error, phase, row, attempt_id, fields)
            failure['write_counts'] = dict(counts)
            failure['unresolved_prior_write'] = phase in ('append_reconciliation', 'update_reconciliation')
            error.copy_receipt = dict(counts)
            row.setdefault('first_failure', failure)
            row['last_failure'] = failure
            with self.store.connect() as db: save(db, key, row)
            raise
