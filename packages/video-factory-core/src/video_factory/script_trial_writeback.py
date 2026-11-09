"""Publish a received structured draft only to its configured trial copy.

This is a projection of a Core receipt, not an execution or review authority.
It never writes baseline columns, human scores, opinions, or review decisions.
"""
from .runtime_store import RuntimeFault, fingerprint
from .feishu_bridge import meta
from .script_jobs import key
from .script_trial import PREFIX
from .feishu_native_sync import plain_text


class TrialDraftWriter:
    def __init__(self,store,client,*,base,table,names):
        self.store=store;self.client=client;self.base=base;self.table=table
        required={'task','input_digest','source_revision','candidate_version',
                  'video_script','caption_voiceover_script','generation_prompt'}
        if (not isinstance(names,dict) or set(names)!=required
                or any(not isinstance(v,str) or not v for v in names.values())
                or len(set(names.values()))!=len(names)):
            raise RuntimeFault('TRIAL_WRITE_MAPPING_INVALID')
        self.names=dict(names)

    def write(self,project,task,revision,record):
        with self.store.connect() as db:
            trial=meta(db,PREFIX+project)
            if (not trial or self.base==trial['production_base']
                    or (self.base,self.table)!=(trial['copy_base'],trial['copy_table'])):
                raise RuntimeFault('TRIAL_TARGET_MISMATCH')
            job=meta(db,key(project,task,revision))
            current=self.store._current(db,project,task,revision)
            if not job or job['state']!='received':raise RuntimeFault('TRIAL_DRAFT_NOT_RECEIVED')
            if current['state'] not in ('awaiting_script_review','ready','rejected'):
                raise RuntimeFault('TRIAL_DRAFT_STATE_CONFLICT')
            result=job['result'];fields=result.get('structured_fields')
            if (not isinstance(fields,dict) or set(fields)!={'video_script','caption_voiceover_script','generation_prompt'}
                    or any(not isinstance(v,str) or not v.strip() or len(v.encode('utf-16-le'))//2>6500 for v in fields.values())):
                raise RuntimeFault('TRIAL_STRUCTURED_DRAFT_INVALID')
            digest=result.get('prepared_input_sha256');source=result.get('prepared_source_revision')
            if not digest or not source:raise RuntimeFault('TRIAL_INPUT_PROVENANCE_REQUIRED')
            receipt_digest=fingerprint(result)
        remote=self.client.record(self.base,self.table,record)['fields'];n=self.names
        if (plain_text(remote.get(n['task']))!=task
                or plain_text(remote.get(n['input_digest']))!=digest
                or plain_text(remote.get(n['source_revision']))!=source):
            raise RuntimeFault('TRIAL_REMOTE_INPUT_CHANGED')
        version=str(revision)+':'+receipt_digest
        patch={n[k]:v for k,v in fields.items()};patch[n['candidate_version']]=version
        actual={k:('' if remote.get(k) in (None,'',[]) else plain_text(remote[k])) for k in patch}
        if actual==patch:return {'status':'unchanged','writes':0,'receipt_sha256':receipt_digest}
        if any(actual.values()):raise RuntimeFault('TRIAL_REMOTE_DRAFT_CONFLICT')
        # A single trial writer is required. A failed/unknown PUT is never retried
        # here; the next explicit reconciliation first reads the saved receipt.
        self.client.update_record(self.base,self.table,record,patch)
        saved=self.client.record(self.base,self.table,record)['fields']
        if any(plain_text(saved.get(k))!=v for k,v in patch.items()):
            raise RuntimeFault('TRIAL_WRITEBACK_READBACK_FAILED')
        return {'status':'written','writes':1,'receipt_sha256':receipt_digest}
