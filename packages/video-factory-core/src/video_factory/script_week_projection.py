"""Durable copy projection. An uncertain append is reconciled, never resent."""
import uuid
from .feishu_bridge import meta, save
from .runtime_store import RuntimeFault, fingerprint


class WeekProjection:
    def __init__(self,store,project,writer,find):
        self.store,self.project,self.writer,self.find=store,project,writer,find

    def publish(self,identity,fields):
        key='script:week-projection:'+fingerprint([self.project,self.writer.base,self.writer.table,identity])
        if fields.get(self.writer.identity_field)!=identity:raise RuntimeFault('WEEK_PROJECTION_IDENTITY_CHANGED')
        with self.store.connect() as db:
            row=meta(db,key)
            if row is None:
                row={'state':'intent','ticket':str(uuid.uuid4()),'initial_fields':fields,
                     'target':[self.writer.base,self.writer.table]}
                save(db,key,row);fresh=True
            else:fresh=False
        if row['target']!=[self.writer.base,self.writer.table]:raise RuntimeFault('WEEK_PROJECTION_TARGET_CHANGED')
        if row['state']=='intent':
            if fresh:
                receipt=self.writer.append(row['initial_fields'],row['ticket'])
                record=receipt['record_id']
            else:
                # find() is a bounded, exact-identity read, not an append retry.
                found=self.find(identity)
                if not isinstance(found,list) or len(found)!=1:
                    raise RuntimeFault('WEEK_APPEND_UNCERTAIN_REQUIRES_INSPECTION')
                record=found[0]['record_id']
                self.writer._readback(record,row['initial_fields'])
            row.update(state='created',record_id=record)
            with self.store.connect() as db:save(db,key,row)
        if row['state']!='created':raise RuntimeFault('WEEK_PROJECTION_STATE_INVALID')
        return self.writer.update(row['record_id'],identity,fields)
