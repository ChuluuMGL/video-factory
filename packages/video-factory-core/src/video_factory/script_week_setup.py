"""Admin-authorized isolated table binding from an existing verified project.

This grants no employee session or model execution. The target must be a fresh,
video-disabled project in the SAME customer Base with exactly inherited roles.
"""
from .runtime_store import RuntimeFault,fingerprint
from .feishu_bridge import FeishuBridge,meta,save,configuration


def bind_copy_table(store,admin,project,*,verified_binding,verified_setup,table,fields,client,proof_sha256):
    """Private deployment entry point; no API accepts client-supplied proof.

    Caller authenticates the origin administrator and reads its binding/setup
    under that lock. The target administrator must separately authorize here.
    Never use a recovered/unconfirmed origin binding as verified_binding.
    """
    from .script_week import _digest
    if not _digest(proof_sha256):raise RuntimeFault('WEEK_ORIGIN_PROOF_REQUIRED')
    origin=configuration(verified_binding)
    if origin['table_id']==table:raise RuntimeFault('WEEK_FRESH_COPY_TABLE_REQUIRED')
    binding=configuration({**origin,'table_id':table,'fields':fields})
    with store.connect() as db:
        store.authorize(db,admin,'admin')
        setup=meta(db,'setup:project:'+project)
        if (not setup or setup['configuration']['project']['base_target']!=origin['base_token']
                or setup['configuration']['project']['video_route']!='deferred'
                or setup['configuration']['organization']!=verified_setup['configuration']['organization']
                or setup['configuration']['deployment']!=verified_setup['configuration']['deployment']):
            raise RuntimeFault('WEEK_ORIGIN_SCOPE_MISMATCH')
    FeishuBridge._schema(client,binding) # application access, not a fabricated employee login
    with store.connect() as db:
        actor=store.authorize(db,admin,'admin')
        if meta(db,'setup:project:'+project)!=setup:raise RuntimeFault('WEEK_SETUP_CHANGED')
        old=meta(db,'feishu:binding:'+project)
        if old is not None:
            if old!=binding:raise RuntimeFault('WEEK_BINDING_CONFLICT')
            return {'replayed':True,'binding_sha256':fingerprint(old),'employee_login_created':False}
        if db.execute('SELECT 1 FROM tasks WHERE project=? LIMIT 1',(project,)).fetchone():
            raise RuntimeFault('WEEK_FRESH_PROJECT_REQUIRED')
        save(db,'feishu:binding:'+project,binding)
        save(db,'script:week-origin:'+project,{'proof_sha256':proof_sha256,
             'origin_binding_sha256':fingerprint(origin),'origin_setup_sha256':fingerprint(verified_setup),
             'user_identity_status':'inherited_original_verified_roles_not_new_login'})
        store.audit(db,actor,'shadow_copy_binding_inherited',project)
    return {'binding_sha256':fingerprint(binding),'employee_login_created':False,'provider_requests':0}
