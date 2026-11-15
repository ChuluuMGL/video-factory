"""Durable, opt-in GET-only outage hold; never retries a paid or write call."""
import time
from .feishu_bridge import meta,save
from .runtime_store import RuntimeFault

PREFIX='script:week:'
TRANSIENT=frozenset(('FEISHU_READ_TIMEOUT','FEISHU_READ_HTTP_502'))
PROBE_INTERVAL=300
OUTAGE_LIMIT=1800


def hold(store,project,error):
    if str(error) not in TRANSIENT:raise error
    with store.connect() as db:
        w=meta(db,PREFIX+project)
        if not w or w['state']!='active':return {'state':'closed','provider_requests':0}
        now=time.time();p=w.get('read_pause')
        if p is None:p={'since':now,'deadline':min(now+OUTAGE_LIMIT,w['t1']),'failures':0}
        p.update(next_probe_at=min(now+PROBE_INTERVAL,p['deadline']),last_error=str(error),failures=p['failures']+1)
        w['read_pause']=p;save(db,PREFIX+project,w)
        store.audit(db,'shadow-service:'+project,'script_week_read_paused',project)
    return status(store,project)


def status(store,project):
    from .script_week import close
    with store.connect() as db:w=meta(db,PREFIX+project)
    if not w or w['state']!='active':return {'state':'closed','provider_requests':0}
    now=time.time()
    if now>=w['t1']:
        return {'state':'closed','close':close(store,project,reason='deadline'),'provider_requests':0}
    p=w.get('read_pause')
    if not p:return None
    if now>=p['deadline']:
        return {'state':'closed','error':'WEEK_READ_OUTAGE_LIMIT','close':close(store,project,reason='error'),'provider_requests':0}
    return {'state':'read_paused','error':p['last_error'],'next_probe_at':p['next_probe_at'],
            'pause_deadline':p['deadline'],'probe_due':now>=p['next_probe_at'],'provider_requests':0}


def gate(store,project,probe):
    result=status(store,project)
    if result is None or result['state']=='closed' or not result['probe_due']:return result
    with store.connect() as db:expected=meta(db,PREFIX+project)['read_pause']
    try:probe()  # Must be an independently provided, read-only health check.
    except RuntimeFault as error:return hold(store,project,error)
    # A successful response after deadline cannot resurrect the grant.
    after=status(store,project)
    if after is None or after['state']=='closed':return after
    with store.connect() as db:
        w=meta(db,PREFIX+project)
        if w['state']!='active' or w.get('read_pause')!=expected:
            raise RuntimeFault('WEEK_READ_PROBE_CONFLICT')
        w['last_read_recovery']={**w.pop('read_pause'),'recovered_at':time.time()}
        save(db,PREFIX+project,w)
        store.audit(db,'shadow-service:'+project,'script_week_read_recovered',project)
    return {'state':'read_recovered','provider_requests':0}
