"""Durable discovery and prepared-input registry for the bounded week.

Initial inventory excludes old tasks. Only a changed/new, still-eligible source
version is compiled. Crashes between compilation and queueing reuse the frozen
artifact; no output or remote write can grant execution authority.
"""
import time
from .runtime_store import RuntimeFault, fingerprint
from .feishu_bridge import meta, save
from .script_week import PREFIX

REGISTRY='script:week-inputs:'


class WeekInputAdapter:
    def __init__(self, store, project, *, list_rows, eligible, compile_input):
        self.store,self.project=store,project
        self.list_rows,self.eligible,self.compile_input=list_rows,eligible,compile_input

    def read_new(self):
        with self.store.connect() as db:
            week=meta(db,PREFIX+self.project)
            saved=meta(db,REGISTRY+self.project)
        if not week or week['state']!='active' or week['t0'] is None:
            return []
        now=time.time()
        if not week['t0']<=now<week['t1']:return []
        rows=self.list_rows()
        if (not isinstance(rows,list) or len(rows)>2000
                or any(not isinstance(r,dict) or not isinstance(r.get('record_id'),str) for r in rows)
                or len({r['record_id'] for r in rows})!=len(rows)):
            raise RuntimeFault('WEEK_DISCOVERY_INVALID')
        observed={r['record_id']:fingerprint(r) for r in rows}
        if saved is None:
            value={'contract_sha256':week['contract_sha256'],'inventory_at':now,
                   'seen':observed,'entries':{},'rejected':{}}
            with self.store.connect() as db:
                if meta(db,REGISTRY+self.project) is not None:raise RuntimeFault('WEEK_DISCOVERY_CONCURRENT')
                save(db,REGISTRY+self.project,value)
            return []
        if saved['contract_sha256']!=week['contract_sha256']:raise RuntimeFault('WEEK_DISCOVERY_CONTRACT_CHANGED')
        # Pending snapshots are reused after process restarts, before new scans.
        admitted={v['snapshot']['brief'] for v in week['snapshots'].values()}
        for entry in saved['entries'].values():
            if entry['snapshot']['brief'] not in admitted:return [entry['snapshot']]
        for row in rows:
            rid=row['record_id'];digest=observed[rid]
            if saved['seen'].get(rid)==digest:continue
            saved['seen'][rid]=digest
            if not self.eligible(row):continue
            # Compiler owns a stable second source/plan read and explicit policy.
            # Infrastructure/auth exceptions propagate and stop the runner.
            compiled=self.compile_input(row,now)
            if isinstance(compiled,dict) and compiled.get('state')=='needs_evidence':
                reason=compiled.get('reason')
                if not isinstance(reason,str) or not reason or len(reason)>2000:
                    raise RuntimeFault('WEEK_INPUT_REJECTION_INVALID')
                saved['rejected'][fingerprint([rid,digest])]={'record_id':rid,'at':now,'reason':reason}
                continue
            if not isinstance(compiled,dict) or set(compiled)!= {'snapshot','sample','evidence'}:
                raise RuntimeFault('WEEK_COMPILED_INPUT_INVALID')
            sample=compiled['sample'];snapshot=compiled['snapshot'];ref='snapshot:'+fingerprint(sample)
            if (snapshot.get('brief')!=ref or snapshot.get('record_id')!=rid
                    or snapshot.get('eligible_at')!=now
                    or snapshot.get('source_base')!=week['contract']['source_base']
                    or snapshot.get('source_table')!=week['contract']['source_table']
                    or snapshot.get('input_sha256')!=fingerprint({k:v for k,v in snapshot.items() if k!='input_sha256'})):
                raise RuntimeFault('WEEK_COMPILED_INPUT_MISMATCH')
            if ref in saved['entries'] and saved['entries'][ref]!=compiled:
                raise RuntimeFault('WEEK_FROZEN_INPUT_CHANGED')
            saved['entries'][ref]=compiled
            self._save(saved)
            return [snapshot]
        self._save(saved)
        return []

    def _save(self,value):
        with self.store.connect() as db:
            week=meta(db,PREFIX+self.project)
            if (week['state']!='active' or week['contract_sha256']!=value['contract_sha256']
                    or not week['t0']<=time.time()<week['t1']):
                raise RuntimeFault('WEEK_CLOSED')
            save(db,REGISTRY+self.project,value)

    def samples(self):
        with self.store.connect() as db:saved=meta(db,REGISTRY+self.project) or {'entries':{}}
        return {ref:item['sample'] for ref,item in saved['entries'].items()}
