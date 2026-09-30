"""Project-scoped, expiring n8n queue credentials. Cannot review or submit."""
import hashlib
import json
import re
import secrets
import time
from .runtime_store import RuntimeFault,canonical,identifier


def issue(store,token,project,ttl_hours=24):
    identifier(project)
    if type(ttl_hours) is not int or not 1<=ttl_hours<=168:raise RuntimeFault('AUTOMATION_TTL_INVALID')
    raw=secrets.token_urlsafe(32);key=hashlib.sha256(raw.encode()).hexdigest()
    value={'project':project,'scope':'queue_read','expires_at':time.time()+3600*ttl_hours}
    with store.connect() as db:
        actor=store.authorize(db,token,'admin')
        if not db.execute('SELECT 1 FROM projects WHERE id=?',(project,)).fetchone():raise RuntimeFault('PROJECT_MISSING')
        db.execute('INSERT INTO meta VALUES(?,?)',('automation:key:'+key,canonical(value)))
        store.audit(db,actor,'automation_queue_key_issued',project)
    return {**value,'key_id':key,'token':raw,'paid_execution_enabled':False}


def revoke(store,token,key_id):
    if not isinstance(key_id,str) or not re.fullmatch('[0-9a-f]{64}',key_id):raise RuntimeFault('AUTOMATION_KEY_INVALID')
    with store.connect() as db:
        actor=store.authorize(db,token,'admin')
        db.execute('DELETE FROM meta WHERE key=?',('automation:key:'+key_id,))
        store.audit(db,actor,'automation_queue_key_revoked')
    return {'revoked':key_id}


def queue(store,token,project,after=''):
    identifier(project)
    if not isinstance(after,str):raise RuntimeFault('AUTOMATION_CURSOR_INVALID')
    if after:identifier(after)
    if not isinstance(token,str) or not 20<=len(token)<=256:raise RuntimeFault('AUTH_AUTOMATION_REQUIRED')
    key=hashlib.sha256(token.encode()).hexdigest()
    with store.connect() as db:
        row=db.execute('SELECT value FROM meta WHERE key=?',('automation:key:'+key,)).fetchone()
        value=json.loads(row[0]) if row else {}
        if value.get('project')!=project or value.get('scope')!='queue_read' or value.get('expires_at',0)<=time.time():raise RuntimeFault('AUTH_AUTOMATION_DENIED')
        rows=db.execute("SELECT t.id,t.revision,t.state FROM tasks t WHERE t.project=? AND t.id>? AND t.state!='accepted' AND t.revision=(SELECT MAX(v.revision) FROM tasks v WHERE v.project=t.project AND v.id=t.id) ORDER BY t.id LIMIT 101",(project,after)).fetchall()
    return {'project':project,'items':[dict(r) for r in rows[:100]],'has_more':len(rows)>100,
            'next_after':rows[99]['id'] if len(rows)>100 else None,'scope':'queue_read','claims_created':0,'model_calls':0}


def template(project,credential_id):
    identifier(project);identifier(credential_id)
    return {'name':'Video Factory queue '+project,'active':False,
        'nodes':[{'id':'schedule','name':'Read schedule','type':'n8n-nodes-base.scheduleTrigger','typeVersion':1.2,'position':[0,0],
                  'parameters':{'rule':{'interval':[{'field':'minutes','minutesInterval':3}]}}},
                 {'id':'queue','name':'Read project queue','type':'n8n-nodes-base.httpRequest','typeVersion':4.2,'position':[250,0],
                  'parameters':{'method':'POST','url':'http://runtime:8787/v1/automation/queue','authentication':'genericCredentialType','genericAuthType':'httpHeaderAuth',
                                'sendBody':True,'specifyBody':'json','jsonBody':canonical({'project':project}),'options':{'timeout':30000}},
                  'credentials':{'httpHeaderAuth':{'id':credential_id,'name':'Video Factory project queue'}}}],
        'connections':{'Read schedule':{'main':[[{'node':'Read project queue','type':'main','index':0}]]}},'settings':{'executionOrder':'v1'}}
