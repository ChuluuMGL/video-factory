"""Employee-confirmed H3 plans from administrator-owned per-SKU settings."""
import re
from pathlib import PurePosixPath
from .runtime_store import RuntimeStore,RuntimeFault,identifier
from .feishu_bridge import FeishuBridge,meta,save
from .worker import Worker


def configure(store,token,project,credential_ref,billing_owner,region,assets):
    identifier(project);identifier(billing_owner)
    if not re.fullmatch(r'secret:[A-Za-z0-9_-]{1,96}',credential_ref) or region not in ('global','cn'):
        raise RuntimeFault('VIDEO_PROFILE_INVALID')
    if not isinstance(assets,dict) or not 1<=len(assets)<=100:raise RuntimeFault('VIDEO_SKU_ASSETS_REQUIRED')
    for sku,item in assets.items():
        identifier(sku)
        if not isinstance(item,dict) or set(item)!={'assets_root','specification'}:raise RuntimeFault('VIDEO_ASSET_FIELDS_INVALID')
        p=PurePosixPath(item['assets_root'])
        if not p.is_absolute() or not p.is_relative_to('/work') or '..' in p.parts:raise RuntimeFault('VIDEO_ASSETS_REQUIRE_WORK_DIRECTORY')
        spec=item['specification']
        if (not isinstance(spec,dict) or set(spec)!={'duration','references'} or type(spec['duration']) is not int
                or not 4<=spec['duration']<=15 or not isinstance(spec['references'],list) or not 1<=len(spec['references'])<=9):raise RuntimeFault('H3_SPECIFICATION_INVALID')
        for ref in spec['references']:
            if not isinstance(ref,dict) or set(ref)!={'path','sha256'} or not isinstance(ref['path'],str):raise RuntimeFault('H3_REFERENCE_INVALID')
            path=PurePosixPath(ref['path'])
            if path.is_absolute() or '..' in path.parts or not re.fullmatch('[0-9a-f]{64}',ref['sha256']):raise RuntimeFault('H3_REFERENCE_INVALID')
    with store.connect() as db:
        actor=store.authorize(db,token,'admin')
        setup=meta(db,'setup:project:'+project)
        if not setup or set(assets)-{s['sku_id'] for s in setup['configuration']['project']['products']}:raise RuntimeFault('VIDEO_SKU_NOT_IN_PROJECT')
        if not db.execute('SELECT revision FROM vault WHERE alias=?',(credential_ref[7:],)).fetchone():raise RuntimeFault('SECRET_MISSING')
        value={'credential_ref':credential_ref,'billing_owner':billing_owner,'region':region,'assets':assets}
        save(db,'video:profile:'+project,value);store.audit(db,actor,'video_provider_configured',project)
    return {'project':project,'model':'MiniMax-H3','configured_skus':sorted(assets),'provider_requests':0}


class MemberStore(RuntimeStore):
    """Internal adapter only used by prepare/approve after fresh Feishu identity."""
    def __init__(self,store,project,identity,binding,config,actor):
        self.store=store;self.root=store.root;self.project=project;self.identity=identity;self.binding=binding;self.config=config;self.actor=actor
    def connect(self):return self.store.connect()
    def authorize(self,db,token,role=None,project=None):
        if project is not None and project!=self.project:raise RuntimeFault('ROLE_OR_PROJECT_DENIED')
        FeishuBridge(self.store)._fence(db,self.project,self.binding,self.identity,'submitters',self.config)
        return self.actor


def prepare(service,user,task,revision):
    _,identity,binding,actor,config,_=service.bridge._session(user,service.project,'submitters')
    with service.store.connect() as db:
        row=service.store._current(db,service.project,task,revision)
        profile=meta(db,'video:profile:'+service.project)
        import json
        sku=json.loads(row['input'])['sku_id']
        if not profile or sku not in profile['assets']:raise RuntimeFault('VIDEO_SETUP_FOR_SKU_REQUIRED')
    values={'project':service.project,'task':task,'revision':revision,**profile['assets'][sku],
            **{k:profile[k] for k in ('credential_ref','billing_owner','region')}}
    worker=Worker(MemberStore(service.store,service.project,identity,binding,config,actor))
    return worker,values,worker.prepare('member-verified',**values)
