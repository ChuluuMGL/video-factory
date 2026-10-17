"""Outbound-only official Feishu SDK relay; no local/public HTTP listener.

The durable inbox is written before acknowledging an SDK-authenticated event.
A dispatch intent is committed before POST. Uncertain delivery is never retried
automatically, because the destination may already have charged a model call.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time
from urllib.request import Request, build_opener
from portable_n8n import Fault, NoRedirect, origin, identifier, private_write, secret
from portable_approval import policy_checked


def secure_directory(root):
    root=Path(root)
    if root.is_symlink(): raise Fault('EVENT_DIRECTORY_SYMLINK_REJECTED')
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    if os.name=='nt':
        # Restrict this dedicated directory before writing any credential.
        sid=subprocess.check_output(['whoami','/user','/fo','csv','/nh'],text=True).strip().rsplit(',',1)[-1].strip('"')
        if not re.fullmatch(r'S-1-[0-9-]+',sid): raise Fault('WINDOWS_OWNER_SID_UNAVAILABLE')
        subprocess.run(['icacls',str(root),'/inheritance:r','/grant:r','*'+sid+':(OI)(CI)F'],
                       check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    else:
        if root.stat().st_uid!=os.getuid(): raise Fault('EVENT_DIRECTORY_OWNER_MISMATCH')
        root.chmod(0o700)
    return root


def save_secrets(root, app_secret, relay_token):
    from cryptography.fernet import Fernet
    root=secure_directory(root)
    key=root/'events.key'; data=root/'events.enc'
    if key.exists() or data.exists(): raise Fault('EVENT_CREDENTIALS_EXIST_EXPLICIT_ROTATION_REQUIRED')
    value=Fernet.generate_key()
    private_write(key,{'key':value.decode()})
    encrypted=Fernet(value).encrypt(json.dumps({'app_secret':app_secret,'relay_token':relay_token}).encode())
    private_write(data,{'ciphertext':encrypted.decode()})


def load_secrets(root):
    from cryptography.fernet import Fernet
    root=Path(root)
    for p in [root,root/'events.key',root/'events.enc']:
        if p.is_symlink(): raise Fault('EVENT_SECRET_SYMLINK_REJECTED')
        if os.name!='nt' and (p.stat().st_uid!=os.getuid() or p.stat().st_mode&0o077):
            raise Fault('EVENT_SECRET_PERMISSIONS_TOO_BROAD')
    return json.loads(Fernet(json.loads((root/'events.key').read_text())['key'].encode()).decrypt(
        json.loads((root/'events.enc').read_text())['ciphertext'].encode()))


def confirmation_text(raw):
    if not isinstance(raw,str) or len(raw)>16000: return ''
    try: value=json.loads(raw)
    except ValueError: return raw if raw.startswith('vf-') else ''
    if isinstance(value,str): return value
    if isinstance(value,list) and all(isinstance(x,dict) for x in value):
        return ''.join(x.get('text','') for x in value if isinstance(x.get('text',''),str))
    return ''


def reduce_verified(envelope, config):
    """Called only from official authenticated SDK callback, never an HTTP body."""
    p=policy_checked(config['policy'])
    if not isinstance(envelope,dict): return None
    h=envelope.get('header') or {}; e=envelope.get('event') or {}
    if (envelope.get('schema')!='2.0' or h.get('event_type')!='drive.file.bitable_record_changed_v1'
        or h.get('app_id')!=p['app'] or h.get('tenant_key')!=p['tenant']
        or e.get('file_type')!='bitable' or e.get('file_token')!=config['base']
        or e.get('table_id')!=config['table']): return None
    # Unauthorized decisions are not forwarded; the workflow repeats this check.
    if not isinstance(e.get('operator_id'),dict) or e['operator_id'].get('open_id')!=p['billing_owner']: return None
    if not isinstance(h.get('event_id'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',h['event_id']): return None
    actions=e.get('action_list')
    if not isinstance(actions,list) or len(actions)>100: return None
    reduced=[]; routes=set()
    for a in actions:
        if not isinstance(a,dict) or a.get('action')!='record_edited': continue
        if not re.fullmatch(r'rec[A-Za-z0-9_-]+',str(a.get('record_id',''))): continue
        after=[f for f in a.get('after_value',[]) if isinstance(f,dict) and f.get('field_id')==config['confirmation_field']]
        if len(after)!=1: continue
        text=confirmation_text(after[0].get('field_value'))
        flow=next((flow for flow in config['hooks'] if text.startswith('vf-'+p['configuration']+'-'+flow+'-')),None)
        if flow is None: continue
        routes.add(flow)
        reduced.append({'action':'record_edited','record_id':a['record_id'],
                        'after_value':[{'field_id':config['confirmation_field'],'field_value':json.dumps(text)}]})
    if not reduced or len(routes)!=1: return None
    return next(iter(routes)),{'schema':'2.0','header':{k:h[k] for k in ('event_id','event_type','app_id','tenant_key')},
        'event':{'file_type':'bitable','file_token':config['base'],'table_id':config['table'],
                 'operator_id':{'open_id':p['billing_owner']},'action_list':reduced}}


class Inbox:
    def __init__(self,root):
        root=secure_directory(root); self.path=root/'events.sqlite'
        if self.path.is_symlink(): raise Fault('EVENT_INBOX_SYMLINK_REJECTED')
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, digest TEXT NOT NULL, flow TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL, receipt TEXT, created REAL NOT NULL)')
        if os.name!='nt': self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10); db.row_factory=sqlite3.Row
        try:
            with db: yield db
        finally:
            db.close()

    def enqueue(self,flow,event):
        payload=json.dumps(event,sort_keys=True,separators=(',',':')); digest=hashlib.sha256(payload.encode()).hexdigest()
        with self.connect() as db:
            old=db.execute('SELECT digest FROM events WHERE id=?',(event['header']['event_id'],)).fetchone()
            if old:
                if old['digest']!=digest: raise Fault('EVENT_ID_CONFLICT')
                return False
            if db.execute('SELECT COUNT(*) FROM events').fetchone()[0]>=10000: raise Fault('EVENT_INBOX_FULL')
            db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?)',(event['header']['event_id'],digest,flow,payload,'queued',None,time.time()))
        return True

    def dispatch_one(self,config,token,send=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT * FROM events WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            if not row: return {'status':'idle'}
            db.execute("UPDATE events SET status='dispatch_intent' WHERE id=?",(row['id'],))
        # Any process crash after intent requires reconciliation, not replay.
        try:
            target=origin(config['origin'])+'/webhook/'+identifier(config['hooks'][row['flow']])
            if send is None:
                req=Request(target,data=row['payload'].encode(),method='POST',headers={'Content-Type':'application/json','X-VF-Event-Relay':token})
                with build_opener(NoRedirect).open(req,timeout=260) as response:
                    raw=response.read(65537)
                    if len(raw)>65536: raise Fault('EVENT_RESPONSE_TOO_LARGE')
                    result=json.loads(raw)
            else: result=send(target,json.loads(row['payload']))
            if isinstance(result,list) and len(result)==1: result=result[0]
            # n8n failures/empty branches must not be called a business success.
            ok=isinstance(result,dict) and result.get('authorization')=='verified'
            receipt={k:result[k] for k in ('authorization','writeback','provider_requests','verification_only','plan_consumed') if isinstance(result,dict) and k in result}
            status='done' if ok else 'needs_reconciliation'
        except Exception:
            status='needs_reconciliation'; receipt={'delivery':'unknown_no_retry'}
        with self.connect() as db:
            db.execute('UPDATE events SET status=?,receipt=? WHERE id=?',(status,json.dumps(receipt),row['id']))
        return {'status':status,'receipt':receipt}


def serve(root):
    import lark_oapi as lark
    root=Path(root); config=json.loads((root/'events.json').read_text()); policy_checked(config['policy'])
    values=load_secrets(root); inbox=Inbox(root)
    # SDK debug/errors may include signed connection URLs or raw event values.
    logging.getLogger('Lark').disabled=True
    def receive(data):
        result=reduce_verified(json.loads(lark.JSON.marshal(data)),config)
        if result: inbox.enqueue(*result)
    def dispatch():
        while True:
            result=inbox.dispatch_one(config,values['relay_token'])
            if result['status']!='idle': print(json.dumps(result),flush=True)
            time.sleep(1)
    handler=lark.EventDispatcherHandler.builder('','').register_p2_drive_file_bitable_record_changed_v1(receive).build()
    threading.Thread(target=dispatch,daemon=True).start()
    lark.ws.Client(config['policy']['app'],values['app_secret'],event_handler=handler,log_level=lark.LogLevel.ERROR).start()


def main():
    parser=argparse.ArgumentParser(description='飞书费用确认事件接收器；只发起出站连接')
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('command',choices=['serve','status'])
    args=parser.parse_args()
    try:
        if args.command=='serve': serve(args.root)
        else:
            with Inbox(args.root).connect() as db:
                print(json.dumps(dict(db.execute('SELECT status,COUNT(*) FROM events GROUP BY status').fetchall())))
    except KeyboardInterrupt: return 0
    except Exception: print('EVENT_RELAY_STOPPED_REVIEW_PRIVATE_CONFIGURATION'); return 2
    return 0


if __name__=='__main__': raise SystemExit(main())
