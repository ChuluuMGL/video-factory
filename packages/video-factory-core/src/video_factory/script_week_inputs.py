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
    def __init__(self, store, project, *, list_rows, eligible, compile_input, intake=None):
        self.store,self.project=store,project
        self.list_rows,self.eligible,self.compile_input=list_rows,eligible,compile_input
        self.intake = intake

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
        if self.intake is not None:
            return self._read_intake(rows, observed, saved, now)
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

    def _read_intake(self, rows, observed, saved, now):
        from collections import Counter
        import re
        processed = saved.setdefault('processed_input_versions', {})
        counts = Counter(); decisions = []; skipped = {}
        for row in rows:
            decision = self.intake(row)
            if (not isinstance(decision, dict) or type(decision.get('eligible')) is not bool
                    or decision.get('kind') not in ('new', 'rework')
                    or not re.fullmatch('[0-9a-f]{64}', str(decision.get('version', '')))
                    or not re.fullmatch('[A-Z_]{1,100}', str(decision.get('reason', '')))):
                raise RuntimeFault('WEEK_INTAKE_DECISION_INVALID')
            rid = row['record_id']; version = decision['version']; reason = decision['reason']
            if decision['eligible']:
                if version in processed:
                    reason = 'INPUT_ALREADY_PROCESSED'
                elif decision['kind'] == 'new' and saved['seen'].get(rid) == observed[rid]:
                    reason = 'HISTORICAL_UNCHANGED_NEW_TASK'
                else:
                    decisions.append((row, decision))
            counts[reason] += 1
            if reason not in ('ELIGIBLE_NEW', 'ELIGIBLE_REWORK'):
                skipped[fingerprint(rid)] = {'reason': reason, 'input_version': version}
        saved['discovery'] = {'at': now, 'rows': len(rows), 'eligible': len(decisions),
                              'reason_counts': dict(counts), 'skipped': skipped}
        # Commit together with compilation; a transport failure must not consume
        # the source version or suppress its next read-only discovery attempt.
        waiting = {row['record_id'] for row, _ in decisions}
        saved['seen'].update({rid: digest for rid, digest in observed.items() if rid not in waiting})
        for row, decision in decisions:
            compiled = self.compile_input(row, now)
            saved['seen'][row['record_id']] = observed[row['record_id']]
            if isinstance(compiled, dict) and compiled.get('state') == 'needs_evidence':
                reason = compiled.get('reason')
                if not isinstance(reason, str) or not reason or len(reason) > 2000:
                    raise RuntimeFault('WEEK_INPUT_REJECTION_INVALID')
                saved['rejected'][decision['version']] = {'record_id': row['record_id'], 'at': now, 'reason': reason}
                processed[decision['version']] = {'state': 'needs_evidence', 'at': now}
                continue
            if not isinstance(compiled, dict) or set(compiled) != {'snapshot', 'sample', 'evidence'}:
                raise RuntimeFault('WEEK_COMPILED_INPUT_INVALID')
            snapshot = compiled['snapshot']; ref = 'snapshot:' + fingerprint(compiled['sample'])
            with self.store.connect() as db: week = meta(db, PREFIX+self.project)
            if (snapshot.get('brief') != ref or snapshot.get('record_id') != row['record_id']
                    or snapshot.get('eligible_at') != now
                    or snapshot.get('source_base') != week['contract']['source_base']
                    or snapshot.get('source_table') != week['contract']['source_table']
                    or snapshot.get('input_sha256') != fingerprint({k:v for k,v in snapshot.items() if k!='input_sha256'})):
                raise RuntimeFault('WEEK_COMPILED_INPUT_MISMATCH')
            if ref in saved['entries'] and saved['entries'][ref] != compiled:
                raise RuntimeFault('WEEK_FROZEN_INPUT_CHANGED')
            saved['entries'][ref] = compiled
            processed[decision['version']] = {'state': 'compiled', 'at': now, 'ref': ref}
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
