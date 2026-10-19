"""Feishu-owned, one-plan-at-a-time billing authorization for portable n8n.

Only an authenticated long-connection relay may call the confirmation hook.
Neither editable person columns nor last_modified_by are authorization inputs.
Plans live in n8n Data Tables; Feishu contains a reviewable copy, not authority.
"""
import json
from uuid import uuid5, NAMESPACE_URL
from pathlib import Path
from portable_n8n import identifier, Fault
from n8n_workflows import (code, http, start, compile_nodes, ledger_update,
                           production_workflow, guard_workflow, persisted_fields_check, generation_profile)

APPROVAL_FIELDS = {'生成计划': 1, '费用确认编号': 1}
APPROVAL_STATUSES = ['申请脚本计划', '申请视频计划', '申请图片计划', '费用待确认']


def policy_checked(policy):
    required = {'app', 'tenant', 'billing_owner', 'billing_account', 'configuration'}
    if set(policy) != required:
        raise Fault('APPROVAL_POLICY_REQUIRED')
    for key in required:
        identifier(policy[key])
    if not policy['app'].startswith('cli_') or not policy['billing_owner'].startswith('ou_'):
        raise Fault('APP_SCOPED_BILLING_OWNER_REQUIRED')
    return dict(policy)


def read_approval(ledger, status):
    return {'name': 'Read protected plan', 'type': 'n8n-nodes-base.dataTable', 'typeVersion': 1.1,
            'parameters': {'resource': 'row', 'operation': 'get',
                'dataTableId': {'__rl': True, 'mode': 'id', 'value': ledger['table']},
                'matchType': 'allConditions', 'filters': {'conditions': [
                    {'keyName': 'id', 'condition': 'eq', 'keyValue': ledger['row']},
                    {'keyName': 'status', 'condition': 'eq', 'keyValue': status}]},
                'returnAll': False, 'limit': 1}}


def scheduled(workflow):
    workflow['nodes'][0].update(type='n8n-nodes-base.scheduleTrigger',typeVersion=1.2,
        parameters={'rule':{'interval':[{'field':'minutes','minutesInterval':1}]}})
    return workflow


def plan_workflow(base, table, flow, approval, policy, *, feishu_origin='https://open.feishu.cn',
                  script_origin='https://api.deepseek.com', video_origin='https://api.minimax.cn',
                  verification_only=False, script_selection=None, video_selection=None, image_selection=None):
    """No model nodes. One pending plan per project; never replace it silently."""
    policy = policy_checked(policy)
    if flow not in ('script', 'video-submit', 'image'):
        raise Fault('APPROVAL_FLOW_INVALID')
    profile = generation_profile(flow, script_origin=script_origin, video_origin=video_origin, script_selection=script_selection,video_selection=video_selection,image_selection=image_selection)
    url, nodes = start(base, table, feishu_origin)
    state = {'script':'申请脚本计划','video-submit':'申请视频计划','image':'申请图片计划'}[flow]
    search = {'filter': {'conjunction': 'and', 'conditions': [
        {'field_name': '状态', 'operator': 'is', 'value': [state]}]}}
    nodes += [http('Find plan request', url+'/search?page_size=1', 'POST', json.dumps(search, ensure_ascii=False), feishu=True),
        code('Select task', "const model="+json.dumps(profile)+",verificationOnly="+json.dumps(verification_only)+",policy="+json.dumps(policy)+",flow="+json.dumps(flow)+",base="+json.dumps(base)+",table="+json.dumps(table)+";"+r'''
if($json.code!==0) throw new Error('PLAN_SEARCH_FAILED');
const row=$json.data.items?.[0]; if(!row) return [];
const text=v=>Array.isArray(v)?v.map(x=>x.text||'').join(''):String(v||'');
const fields=row.fields;
if(!/^rec[A-Za-z0-9_-]+$/.test(row.record_id)) throw new Error('PLAN_RECORD_INVALID');
if(!text(fields[flow==='script'?'商品资料':flow==='image'?'图片要求':'脚本']).trim()) throw new Error('PLAN_CONTENT_REQUIRED');
if(flow==='video-submit' && text(fields['视频任务ID'])) throw new Error('EXISTING_VIDEO_NO_NEW_SUBMISSION');
const snapshot={};
for(const k of ['商品资料','脚本','审核意见','脚本历史','参考图链接','视频任务ID']) snapshot[k]=text(fields[k]);
snapshot['参考图附件']=(fields['参考图附件']||[]).map(a=>({file_token:a.file_token,name:a.name,size:a.size}));
// Reject unusable candidate inputs before claiming or publishing a paid plan.
if(flow==='video-submit' && model.first_frame_count===1) {
 if(snapshot['脚本'].length>2500) throw new Error('VIDEO_PROMPT_INVALID');
 const frames=snapshot['参考图附件'];
 if(snapshot['参考图链接'].trim() || frames.length!==1) throw new Error('VIDEO_ONE_FEISHU_FIRST_FRAME_REQUIRED');
 if(frames.some(a=>! /^[A-Za-z0-9_-]+$/.test(a.file_token || '') || !Number.isInteger(a.size) || a.size<1 || a.size>20*1024*1024 || !/\.(jpe?g|png)$/i.test(a.name || ''))) throw new Error('VIDEO_FIRST_FRAME_INVALID');
}
if(flow==='image') {
 for(const k of ['图片要求','图片回执']) snapshot[k]=text(fields[k]);
 snapshot['生成图片']=(fields['生成图片']||[]).map(a=>({file_token:a.file_token,name:a.name,size:a.size}));
 if(snapshot['图片回执'] || snapshot['生成图片'].length) throw new Error('EXISTING_IMAGE_CREATE_NEW_TASK');
 if(!snapshot['商品资料'].trim() || snapshot['图片要求'].length>2000 || snapshot['商品资料'].length>4000 || snapshot['审核意见'].length>1000) throw new Error('IMAGE_PROMPT_INVALID');
 const refs=snapshot['参考图附件'];
 if(snapshot['参考图链接'].trim() || refs.length!==1 || refs.some(a=>! /^[A-Za-z0-9_-]+$/.test(a.file_token||'') || !Number.isInteger(a.size) || a.size<1 || a.size>10*1024*1024 || !/\.(png|jpe?g)$/i.test(a.name||''))) throw new Error('IMAGE_ONE_FEISHU_REFERENCE_REQUIRED');
}
const plan={schema:2,id:'vf-'+policy.configuration+'-'+flow+'-'+String($execution.id),base,table,record:row.record_id,
  policy,flow,model,verification_only:verificationOnly,max_submissions:1,automatic_retry:false,snapshot,expires_at:Date.now()+86400000};
const display=(verificationOnly?'【零费用身份验收：不调用模型，不生成内容】\n':'【确认后将调用模型并产生费用】\n')+JSON.stringify(plan,null,2)+
  (verificationOnly?'\n本次仅核验身份和一次性确认。':'\n费用确认：使用上列费用账户，最多调用一次，实际费用按供应商计费；不是人民币金额保证。')+
  '核对商品、脚本、参考图与模型后，由指定负责人把本计划 id 复制到「费用确认编号」。有效期 24 小时。';
if(display.length>19000) throw new Error('PLAN_TOO_LARGE');
return [{json:{id:row.record_id,execution:String($execution.id),plan,display,source_status:fields['状态']}}];'''),
        ledger_update('Claim plan slot', approval, 'idle', {'status':'planning','owner':"={{ $('Select task').first().json.execution }}",'payload':"={{ JSON.stringify($('Select task').first().json) }}",'result':''}),
        code('Check plan slot', "if($json.owner!==$('Select task').first().json.execution) throw new Error('PLAN_SLOT_NOT_OWNED'); return [{json:$('Select task').first().json}];"),
        http('Read task before plan', '={{ '+json.dumps(url+'/')+" + $('Select task').first().json.id }}", feishu=True),
        code('Validate plan input', r'''
if($json.code!==0) throw new Error('PLAN_READBACK_FAILED');
const p=$('Select task').first().json,f=$json.data.record.fields;
const text=v=>Array.isArray(v)?v.map(x=>x.text||'').join(''):String(v||'');
if(f['状态']!==p.source_status) throw new Error('PLAN_STATUS_CHANGED');
for(const [k,v] of Object.entries(p.plan.snapshot)) {
 const actual=['参考图附件','生成图片'].includes(k)?(f[k]||[]).map(a=>({file_token:a.file_token,name:a.name,size:a.size})):text(f[k]);
 if(JSON.stringify(actual)!==JSON.stringify(v)) throw new Error('PLAN_INPUT_CHANGED');
}
return [{json:{fields:{'状态':'费用待确认','生成计划':p.display,'费用确认编号':''}}}];'''),
        http('Write plan', '={{ '+json.dumps(url+'/')+" + $('Select task').first().json.id }}", 'PUT', '={{ $json }}', feishu=True),
        http('Read published plan', '={{ '+json.dumps(url+'/')+" + $('Select task').first().json.id }}", feishu=True),
        persisted_fields_check('Verify published plan','Validate plan input'),
        ledger_update('Await owner confirmation',approval,'planning',{'status':'awaiting'},owner=True),
        code('Plan receipt',"return [{json:{plan_ready:true,provider_requests:0,confirmation:'designated_feishu_owner'}}];")]
    return compile_nodes(nodes)


def confirmation_workflow(base, table, flow, execution, approval, policy, path, *,
                          feishu_origin='https://open.feishu.cn',script_origin='https://api.deepseek.com',
                          video_origin='https://api.minimax.cn', verification_only=False, script_selection=None, video_selection=None, image_selection=None):
    """A trusted event selects the exact protected plan, never the first paid row.

    verification_only consumes the plan and returns a zero-provider receipt;
    it has no provider nodes. It cannot be toggled via a webhook request.
    """
    policy=policy_checked(policy); identifier(path)
    profile = generation_profile(flow, script_origin=script_origin, video_origin=video_origin, script_selection=script_selection,video_selection=video_selection,image_selection=image_selection)
    workflow=guard_workflow(production_workflow(base,table,flow,feishu_origin=feishu_origin,
                          script_origin=script_origin,video_origin=video_origin,script_selection=script_selection,video_selection=video_selection,image_selection=image_selection),execution)
    old=workflow['nodes']; url=f'{feishu_origin}/open-apis/bitable/v1/apps/{base}/tables/{table}/records/'
    hook={'name':'Verified Feishu event','type':'n8n-nodes-base.webhook','typeVersion':2,
          'webhookId':str(uuid5(NAMESPACE_URL,'vf-owner:'+path)),
          'parameters':{'httpMethod':'POST','path':path,'responseMode':'lastNode','authentication':'headerAuth','options':{}},
          'credentials':{'httpHeaderAuth':{'id':'RELAY_UNBOUND','name':'RELAY_UNBOUND'}}}
    nodes=[hook,code('Check event scope',"const policy="+json.dumps(policy)+",base="+json.dumps(base)+",table="+json.dumps(table)+";"+r'''
const e=$json.body;
if(!e || e.schema!=='2.0' || e.header?.event_type!=='drive.file.bitable_record_changed_v1' ||
 e.header.app_id!==policy.app || e.header.tenant_key!==policy.tenant ||
 e.event?.file_type!=='bitable' || e.event.file_token!==base || e.event.table_id!==table)
 throw new Error('APPROVAL_EVENT_SCOPE_DENIED');
if(e.event.operator_id?.open_id!==policy.billing_owner) throw new Error('BILLING_OWNER_DENIED');
if(typeof e.header.event_id!=='string' || !/^[A-Za-z0-9_-]{1,128}$/.test(e.header.event_id) ||
 !Array.isArray(e.event.action_list) || e.event.action_list.length>100) throw new Error('APPROVAL_EVENT_INVALID');
return [{json:{event:e}}];'''),read_approval(approval,'awaiting'),
        code('Check protected plan',"const expectedModel="+json.dumps(profile)+",verificationOnly="+json.dumps(verification_only)+",policy="+json.dumps(policy)+",flow="+json.dumps(flow)+";"+r'''
const saved=JSON.parse($json.payload),p=saved.plan;
const canonical=x=>Array.isArray(x)?x.map(canonical):x && typeof x==='object'?Object.fromEntries(Object.keys(x).sort().map(k=>[k,canonical(x[k])])):x;
if(p.schema!==2 || JSON.stringify(canonical(p.model))!==JSON.stringify(canonical(expectedModel)))
 throw new Error('APPROVAL_MODEL_PROFILE_CHANGED_REPLAN');
if(JSON.stringify(p.policy)!==JSON.stringify(policy) || p.flow!==flow || p.max_submissions!==1 || p.automatic_retry!==false || p.verification_only!==verificationOnly)
 throw new Error('APPROVAL_POLICY_CHANGED');
if(!Number.isFinite(p.expires_at) || p.expires_at<Date.now()) throw new Error('APPROVAL_EXPIRED');
return [{json:saved}];'''),
        *old[1:3],
        http('Read confirmation field', f'{feishu_origin}/open-apis/bitable/v1/apps/{base}/tables/{table}/fields?page_size=100',feishu=True),
        code('Verify owner decision',r'''
if($json.code!==0 || $json.data.has_more) throw new Error('APPROVAL_SCHEMA_UNAVAILABLE');
const fields=$json.data.items.filter(f=>f.field_name==='费用确认编号' && f.type===1);
if(fields.length!==1) throw new Error('APPROVAL_FIELD_INVALID');
const p=$('Check protected plan').first().json;
const envelope=$('Check event scope').first().json.event;
const actions=envelope.event.action_list.filter(a=>a.action==='record_edited' && a.record_id===p.id);
const parse=v=>{
 if(typeof v!=='string' || v.length>16000) throw new Error('APPROVAL_VALUE_INVALID');
 try {const x=JSON.parse(v);return typeof x==='string'?x:Array.isArray(x)?x.map(t=>t.text||'').join(''):'';}
 catch(e){if(v.startsWith('vf-')) return v; throw new Error('APPROVAL_VALUE_INVALID');}
};
const decisions=actions.flatMap(a=>(a.after_value||[]).filter(f=>f.field_id===fields[0].field_id));
if(decisions.length!==1 || parse(decisions[0].field_value)!==p.plan.id) throw new Error('EXPLICIT_PLAN_CONFIRMATION_REQUIRED');
return [{json:p}];'''),
        http('Read confirmed task','={{ '+json.dumps(url)+" + $('Check protected plan').first().json.id }}",feishu=True),
        code('Select task',r'''
if($json.code!==0) throw new Error('APPROVAL_TASK_UNREADABLE');
const saved=$('Check protected plan').first().json,p=saved.plan,row=$json.data.record,f=row.fields;
const text=v=>Array.isArray(v)?v.map(x=>x.text||'').join(''):String(v||'');
if(row.record_id!==p.record) throw new Error('APPROVAL_RECORD_CHANGED');
if(f['状态']!=='费用待确认') throw new Error('APPROVAL_STATUS_CHANGED');
if(text(f['费用确认编号'])!==p.id) throw new Error('APPROVAL_CONFIRMATION_NOT_CURRENT');
if(text(f['生成计划'])!==saved.display) throw new Error('APPROVAL_DISPLAY_CHANGED');
if(!Number.isFinite(p.expires_at) || p.expires_at<Date.now()) throw new Error('APPROVAL_EXPIRED');
for(const [k,v] of Object.entries(p.snapshot)) {
 const actual=['参考图附件','生成图片'].includes(k)?(f[k]||[]).map(a=>({file_token:a.file_token,name:a.name,size:a.size})):text(f[k]);
 if(JSON.stringify(actual)!==JSON.stringify(v)) throw new Error('APPROVED_INPUT_CHANGED');
}
return [{json:{id:p.record,fields:f,product:text(f['商品资料']),script:text(f['脚本']),feedback:text(f['审核意见']),
 history:text(f['脚本历史']),references:text(f['参考图链接']).split(/\s+/).filter(Boolean),
 image_prompt:text(f['图片要求']),image_receipt:text(f['图片回执']),image_candidates:(f['生成图片']||[]).map(a=>({file_token:a.file_token,name:a.name,size:a.size})),attachments:p.snapshot['参考图附件'],job:text(f['视频任务ID']),execution:String($execution.id),approval:p}}];'''),
        ledger_update('Consume owner approval',approval,'awaiting',{'status':'consumed',
            'owner':"={{ $('Select task').first().json.execution }}",
            'result':"={{ JSON.stringify({plan:$('Select task').first().json.approval.id,event:$('Check event scope').first().json.event.header.event_id,actor:$('Check event scope').first().json.event.event.operator_id.open_id,consumed_at:Date.now()}) }}"}),
        code('Check consumed approval',"if($json.owner!==$('Select task').first().json.execution) throw new Error('APPROVAL_ALREADY_CONSUMED'); return [{json:$('Select task').first().json}];")]
    if verification_only:
        nodes += [code('Authorization test receipt',"return [{json:{authorization:'verified',provider_requests:0,plan_consumed:true,verification_only:true}}];")]
    else:
        # Exclude manual trigger/search/select; retain model validation, claim,
        # reservation, durable result and live writeback checks unchanged.
        nodes += old[5:]
        reservation=next(n for n in nodes if n['name']=='Verify reservation')
        reservation['parameters']['jsCode']=reservation['parameters']['jsCode'].replace(
            'return [{json:{ok:true}}];',r'''
const approved=$('Check protected plan').first().json;
if(text(fields['生成计划'])!==approved.display || text(fields['费用确认编号'])!==approved.plan.id || approved.plan.expires_at<Date.now())
 throw new Error('APPROVAL_CHANGED_BEFORE_PROVIDER');
return [{json:{ok:true}}];''')
        nodes += [ledger_update('Close completed approval',approval,'consumed',{'status':'idle'},owner=True),
                  code('Approved execution receipt',"return [{json:{authorization:'verified',writeback:'verified',automatic_resubmit:false}}];")]
    return compile_nodes(nodes)


def configure_approval(api, root, project, base, table, policy, *, verification_only=True, ask_secret=None):
    """Import disabled owner-gated workflows, preserving an interrupted setup.

    Legacy manual paid workflows are deliberately not activated or overwritten.
    Activation is a separate action after identity/no-charge acceptance.
    """
    import secrets
    from portable_n8n import (locked, private_write, ensure_ledger, provision,
                              verify_setup, secret)
    from portable_events import save_secrets, load_secrets, secure_directory
    policy=policy_checked(policy);identifier(project);identifier(base);identifier(table)
    root=Path(root); ask_secret=ask_secret or secret
    pool=json.loads((root/('credentials-'+project+'.json')).read_text(encoding='utf-8'))
    if pool['origin']!=api.url or pool['app']!=policy['app']: raise Fault('APPROVAL_CREDENTIAL_SCOPE_MISMATCH')
    from portable_models import load as load_models
    model_selection=load_models(root,project,api.url)
    if 'script' in pool['roles'] and pool.get('script_selection',{'provider':'deepseek','model':'deepseek-flash'})!=model_selection:
        raise Fault('APPROVAL_SCRIPT_CREDENTIAL_MODEL_MISMATCH')
    from portable_models import options
    from portable_video_models import load as load_video,selection as video_default
    video=load_video(root,project,api.url)
    if 'video' in pool['roles'] and pool.get('video_selection',video_default())!=video:
        raise Fault('APPROVAL_VIDEO_CREDENTIAL_MODEL_MISMATCH')
    from portable_images import load as load_image
    image=load_image(root,project,api.url)
    if 'image' in pool['roles'] and pool.get('image_selection')!=image:
        raise Fault('APPROVAL_IMAGE_CREDENTIAL_MODEL_MISMATCH')
    model_options=options(root,project,api.url)
    event_root=secure_directory(root/(project+'-events'))
    scope={'origin':api.url,'project':project,'base':base,'table':table,'policy':policy,'verification_only':verification_only}
    scope.update(model_options)
    with locked(event_root):
        config_path=event_root/'events.json'
        if config_path.exists():
            config=json.loads(config_path.read_text(encoding='utf-8'))
            if any(config.get(k)!=v for k,v in scope.items()): raise Fault('APPROVAL_SCOPE_CHANGED_USE_PROMOTE_AFTER_ACCEPTANCE')
        else:
            if not verification_only: raise Fault('OWNER_ZERO_CHARGE_ACCEPTANCE_REQUIRED_FIRST')
            config={**scope,'hooks':{},'workflows':{}};private_write(config_path,config)
        if not (event_root/'events.enc').exists():
            save_secrets(event_root,ask_secret('事件接收器首次配置：同一飞书 App Secret（隐藏输入）: '),secrets.token_urlsafe(48))
        values=load_secrets(event_root)
    # Schema must already be added via the ordinary additive schema route.
    _,nodes=start(base,table)
    nodes += [http('Read confirmation schema',f'https://open.feishu.cn/open-apis/bitable/v1/apps/{base}/tables/{table}/fields?page_size=100',feishu=True),
              code('Check confirmation schema',"if($json.code!==0 || $json.data.has_more)throw new Error('APPROVAL_SCHEMA_UNAVAILABLE');const f=$json.data.items.filter(x=>x.field_name==='费用确认编号'&&x.type===1);if(f.length!==1)throw new Error('RUN_ADDITIVE_SCHEMA_FIRST');return [{json:{base_read:'verified',confirmation_field:f[0].field_id,provider_requests:0}}];")]
    receipt=verify_setup(api,root,project+'-approval-schema',compile_nodes(nodes),{'httpCustomAuth':pool['roles']['feishu']})
    checked=receipt['result'];checked=checked[0] if isinstance(checked,list) else checked
    config['confirmation_field']=identifier(checked['confirmation_field'])
    execution=ensure_ledger(api,root,project,base,table)
    approval=ensure_ledger(api,root,project+'-approval',base,table)
    for flow,role in [('script','script'),('video-submit','video'),('image','image')]:
        if role not in pool['roles']: continue
        path='vf-owner-'+project+'-'+flow+'-'+policy['configuration']
        identifier(path)
        config['workflows']['plan-'+flow]=provision(api,root,project+'-plan-'+flow,
            scheduled(plan_workflow(base,table,flow,approval,policy,verification_only=verification_only,**model_options)),{'httpCustomAuth':pool['roles']['feishu']})
        credentials={'httpCustomAuth':pool['roles']['feishu'],
                     'relay':{'type':'httpHeaderAuth','data':{'name':'X-VF-Event-Relay','value':values['relay_token']}}}
        if not verification_only:credentials['httpHeaderAuth']=pool['roles'][role]
        config['workflows']['confirm-'+flow]=provision(api,root,project+'-confirm-'+flow,
            confirmation_workflow(base,table,flow,execution,approval,policy,path,verification_only=verification_only,**model_options),credentials)
        config['hooks'][flow]=path
        with locked(event_root):private_write(event_root/'events.json',config)
    return {'configured':True,'active':False,'verification_only':verification_only,
            'event_root':str(event_root),'workflows':config['workflows'],
            'next':'先在飞书应用配置记录变更长连接订阅；启动事件接收器并完成零费用负责人验收，再显式启用付费流程。'}


def set_approval_active(api, root, project, active):
    """Explicit activation with exact owned-template readback and rollback."""
    from portable_n8n import digest
    root=Path(root);identifier(project)
    config=json.loads((root/(project+'-events')/'events.json').read_text(encoding='utf-8'))
    if config['origin']!=api.url: raise Fault('APPROVAL_ORIGIN_CHANGED')
    if active:
        from portable_images import load as load_image
        if load_image(root,project,api.url)!=config.get('image_selection'): raise Fault('APPROVAL_IMAGE_SCOPE_CHANGED')
        from portable_models import load as load_models, selection
        from portable_video_models import load as load_video,selection as video_default
        if load_video(root,project,api.url)!=config.get('video_selection',video_default()):
            raise Fault('APPROVAL_VIDEO_SCOPE_CHANGED')
        if load_models(root,project,api.url)!=config.get('script_selection',selection()):
            raise Fault('APPROVAL_MODEL_CHANGED_REPEAT_ZERO_CHARGE_ACCEPTANCE')
    owned=[]
    for item in config['workflows'].values():
        state=json.loads((root/('binding-'+item['project']+'.json')).read_text(encoding='utf-8'))
        saved=api.request('GET','/api/v1/workflows/'+item['workflow'])
        if state['workflow']!=saved['id']: raise Fault('APPROVAL_WORKFLOW_ID_CHANGED')
        # Normalize credential bindings and API-provided node display metadata
        # using the exact originally provisioned template saved at setup time.
        evidence=state.get('owned_template')
        if active and (not evidence or saved['nodes']!=evidence['nodes'] or saved['connections']!=evidence['connections']
            or any(saved.get('settings',{}).get(k)!=v for k,v in evidence['settings'].items())):
            raise Fault('APPROVAL_WORKFLOW_CHANGED_REVIEW_REQUIRED')
        owned.append((saved['id'],saved.get('active',False)))
    changed=[]
    try:
        for workflow,was_active in owned:
            if was_active==active:continue
            changed.append(workflow)
            api.request('POST','/api/v1/workflows/'+workflow+('/activate' if active else '/deactivate'),{})
            if bool(api.request('GET','/api/v1/workflows/'+workflow).get('active'))!=active:
                raise Fault('APPROVAL_ACTIVATION_READBACK_FAILED')
    except Exception:
        if active:
            rollback_failed=False
            for workflow in changed:
                try:
                    api.request('POST','/api/v1/workflows/'+workflow+'/deactivate',{})
                    if api.request('GET','/api/v1/workflows/'+workflow).get('active'):rollback_failed=True
                except Exception:rollback_failed=True
            if rollback_failed:raise Fault('APPROVAL_ACTIVATION_UNCERTAIN_RUN_DISABLE_AND_READ_BACK') from None
        raise
    return {'active':active,'verification_only':config['verification_only'],'workflow_count':len(owned),'provider_requests':0}


def _promote_approval(api, root, project):
    """Only an SDK-received, zero-charge owner confirmation can unlock promotion.

    Imports paid workflows disabled. Explicit activation remains separate.
    """
    from portable_n8n import private_write, ensure_ledger, provision, locked
    from portable_events import Inbox,load_secrets
    root=Path(root);identifier(project);event_root=root/(project+'-events')
    config=json.loads((event_root/'events.json').read_text(encoding='utf-8'))
    checkpoint=event_root/'promotion.json'
    pending=json.loads(checkpoint.read_text(encoding='utf-8')) if checkpoint.exists() else None
    if config['origin']!=api.url: raise Fault('PROMOTION_SOURCE_INVALID')
    if not config['verification_only']:
        if pending and pending.get('complete'):
            active=any(api.request('GET','/api/v1/workflows/'+item['workflow']).get('active',False)
                       for item in config['workflows'].values())
            return {'promoted':True,'active':active,'provider_requests':0}
        raise Fault('PROMOTION_SOURCE_INVALID')
    with Inbox(event_root).connect() as db:
        receipts=db.execute("SELECT payload,receipt FROM events WHERE status='done'").fetchall()
    passed=[r for r in receipts if json.loads(r['receipt']).get('verification_only') is True
            and json.loads(r['receipt']).get('provider_requests')==0
            and json.loads(r['payload'])['event']['operator_id']['open_id']==config['policy']['billing_owner']]
    if not passed: raise Fault('REAL_OWNER_ZERO_CHARGE_ACCEPTANCE_REQUIRED')
    from portable_models import load as load_models
    selected=load_models(root,project,api.url)
    if selected!=config.get('script_selection',{'provider':'deepseek','model':'deepseek-flash'}):
        raise Fault('APPROVAL_MODEL_CHANGED_REPEAT_ZERO_CHARGE_ACCEPTANCE')
    from portable_video_models import load as load_video,selection as video_default
    if load_video(root,project,api.url)!=config.get('video_selection',video_default()):
        raise Fault('APPROVAL_VIDEO_SCOPE_CHANGED')
    from portable_models import options
    from portable_images import load as load_image
    if load_image(root,project,api.url)!=config.get('image_selection'): raise Fault('APPROVAL_IMAGE_SCOPE_CHANGED')
    model_options=options(root,project,api.url)
    base,table=config['base'],config['table'];policy=policy_checked(config['policy'])
    execution=ensure_ledger(api,root,project,base,table);approval=ensure_ledger(api,root,project+'-approval',base,table)
    rows=api.request('GET','/api/v1/data-tables/'+execution['table']+'/rows')['data']
    if len(rows)!=1 or rows[0]['status']!='idle': raise Fault('PROJECT_EXECUTION_RECONCILIATION_REQUIRED')
    a=api.request('GET','/api/v1/data-tables/'+approval['table']+'/rows')['data']
    if len(a)!=1: raise Fault('OWNER_ACCEPTANCE_PLAN_NOT_CONSUMED')
    if pending:
        if pending['policy']!=config['policy'] or a[0]['result']!=pending['result'] or a[0]['status'] not in ('consumed','idle'):
            raise Fault('PROMOTION_CHECKPOINT_CHANGED')
    elif a[0]['status']!='consumed':raise Fault('OWNER_ACCEPTANCE_PLAN_NOT_CONSUMED')
    decision=json.loads(a[0]['result'])
    if not any(json.loads(r['payload'])['header']['event_id']==decision['event'] for r in passed):
        raise Fault('OWNER_ACCEPTANCE_RECEIPT_MISMATCH')
    for name,item in config['workflows'].items():
        if name.startswith('confirm-'):
            saved=api.request('GET','/api/v1/workflows/'+item['workflow'])
            expected=json.loads((root/('binding-'+item['project']+'.json')).read_text(encoding='utf-8'))['owned_template']
            if saved['nodes']!=expected['nodes'] or saved['connections']!=expected['connections'] or any(n['name'] in ('Generate script','Submit video once','Generate image once') for n in saved['nodes']):
                raise Fault('ZERO_CHARGE_TEMPLATE_CHANGED')
    set_approval_active(api,root,project,False)
    if pending is None:
        pending={'policy':config['policy'],'result':a[0]['result'],'complete':False};private_write(checkpoint,pending)
    # No model existed in the test workflow; only now release its consumed slot.
    if a[0]['status']=='consumed':
        cleared=api.request('PATCH','/api/v1/data-tables/'+approval['table']+'/rows/update',{
            'filter':{'type':'and','filters':[{'columnName':'id','condition':'eq','value':approval['row']},
                {'columnName':'status','condition':'eq','value':'consumed'},{'columnName':'result','condition':'eq','value':a[0]['result']}]},
            'data':{'status':'idle'},'returnData':True})
        if len(cleared)!=1: raise Fault('APPROVAL_SLOT_CHANGED')
    pool=json.loads((root/('credentials-'+project+'.json')).read_text(encoding='utf-8'))['roles'];values=load_secrets(event_root)
    new=json.loads(json.dumps(config));new['workflows']={};new['hooks']={};new['verification_only']=False
    for flow,role in [('script','script'),('video-submit','video'),('image','image')]:
        if role not in pool:continue
        path='vf-owner-'+project+'-'+flow+'-'+policy['configuration']+'-paid';identifier(path)
        new['workflows']['plan-'+flow]=provision(api,root,project+'-plan-'+flow+'-paid',
            scheduled(plan_workflow(base,table,flow,approval,policy,**model_options)),{'httpCustomAuth':pool['feishu']})
        new['workflows']['confirm-'+flow]=provision(api,root,project+'-confirm-'+flow+'-paid',
            confirmation_workflow(base,table,flow,execution,approval,policy,path,**model_options),
            {'httpCustomAuth':pool['feishu'],'httpHeaderAuth':pool[role],
             'relay':{'type':'httpHeaderAuth','data':{'name':'X-VF-Event-Relay','value':values['relay_token']}}})
        new['hooks'][flow]=path
    if 'video' in pool:
        new['workflows']['video-poll']=provision(api,root,project+'-approved-poll',
            scheduled(production_workflow(base,table,'video-poll',**model_options)),
            {'httpCustomAuth':pool['feishu'],'httpHeaderAuth':pool['video']})
    private_write(event_root/'owner-acceptance.json',{'policy':policy,'decision':decision,'provider_requests':0})
    pending['complete']=True;private_write(checkpoint,pending)
    private_write(event_root/'events.json',new)
    return {'promoted':True,'active':False,'provider_requests':0,'next':'重启事件接收器读取已更新配置；核对后显式 approval-enable。'}


def promote_approval(api, root, project):
    from portable_n8n import locked
    with locked(Path(root)/(identifier(project)+'-events')):
        return _promote_approval(api,root,project)


def cancel_plan(api, root, project, expected):
    """Cancel only an unconsumed plan; never reset an uncertain paid execution."""
    from portable_n8n import ensure_ledger,locked
    root=Path(root);identifier(project)
    with locked(root/(project+'-events')):
        config=json.loads((root/(project+'-events')/'events.json').read_text(encoding='utf-8'))
        if config['origin']!=api.url: raise Fault('APPROVAL_ORIGIN_CHANGED')
        approval=ensure_ledger(api,root,project+'-approval',config['base'],config['table'])
        rows=api.request('GET','/api/v1/data-tables/'+approval['table']+'/rows')['data']
        if len(rows)!=1 or rows[0]['status'] not in ('planning','awaiting'):raise Fault('PLAN_CONSUMED_OR_UNKNOWN_NO_RESET')
        row=rows[0]
        if json.loads(row['payload'])['plan']['id']!=expected:raise Fault('CANCEL_PLAN_ID_MISMATCH')
        result=api.request('PATCH','/api/v1/data-tables/'+approval['table']+'/rows/update',{
            'filter':{'type':'and','filters':[{'columnName':'id','condition':'eq','value':approval['row']},
                {'columnName':'status','condition':'eq','value':row['status']},
                {'columnName':'payload','condition':'eq','value':row['payload']}]},
            'data':{'status':'idle','result':json.dumps({'cancelled':expected})},'returnData':True})
        if len(result)!=1:raise Fault('PLAN_CHANGED_DURING_CANCEL')
        current=api.request('GET','/api/v1/data-tables/'+approval['table']+'/rows')['data']
        if len(current)!=1 or current[0]['status']!='idle':raise Fault('PLAN_CANCEL_READBACK_FAILED')
    return {'cancelled':expected,'provider_requests':0,'next':'旧确认编号无效；在飞书重新选择申请计划后，核对新计划。'}


def reconcile_completed_approval(api, root, project, *, feishu_origin='https://open.feishu.cn'):
    """Close only a consumed plan whose exact saved result is already restored.

    This never restores a record, submits a model, retries an upload, or clears
    an unknown execution. It frees the plan slot after zero-model recovery.
    """
    from portable_n8n import locked, verify_setup
    root=Path(root);identifier(project)
    with locked(root/(project+'-events')):
        config=json.loads((root/(project+'-events')/'events.json').read_text(encoding='utf-8'))
        if config['origin']!=api.url or config['verification_only']: raise Fault('RECONCILE_PAID_PROJECT_REQUIRED')
        policy=policy_checked(config['policy'])
        rows=[];ledgers=[]
        for name in (project,project+'-approval'):
            saved=json.loads((root/('ledger-'+name+'.json')).read_text(encoding='utf-8'))
            if saved.get('pending') or any(saved.get(k)!=v for k,v in {'origin':api.url,'project':name,'base':config['base'],'task_table':config['table']}.items()):
                raise Fault('RECONCILE_LEDGER_SCOPE_CHANGED')
            identifier(saved['table'])
            data=api.request('GET','/api/v1/data-tables/'+saved['table']+'/rows')['data']
            if len(data)!=1 or data[0]['id']!=saved['row']: raise Fault('RECONCILE_LEDGER_ROW_CHANGED')
            rows.append(data[0]);ledgers.append(saved)
        execution,approval=rows
        if execution['status']!='idle' or approval['status']!='consumed': raise Fault('RECOVER_RESULT_FIRST_UNKNOWN_EXECUTION_NOT_RELEASED')
        task=json.loads(execution['payload']);result=json.loads(execution['result']);plan=json.loads(approval['payload'])['plan'];decision=json.loads(approval['result'])
        if (task.get('approval')!=plan or plan['policy']!=policy or plan['base']!=config['base'] or plan['table']!=config['table']
            or task['id']!=plan['record'] or execution['owner']!=task['execution'] or approval['owner']!=task['execution']
            or decision['plan']!=plan['id'] or decision['actor']!=policy['billing_owner'] or plan['verification_only']):
            raise Fault('RECOVERY_APPROVAL_RECEIPT_MISMATCH')
        if result.get('fields',{}).get('状态') not in ('脚本待审核','视频生成中','图片待审核'): raise Fault('RECOVERY_RESULT_INVALID')
        identifier(task['id']);identifier(config['base']);identifier(config['table'])
        pool=json.loads((root/('credentials-'+project+'.json')).read_text(encoding='utf-8'))
        if pool['origin']!=api.url or pool['project']!=project or pool['app']!=policy['app']: raise Fault('RECONCILE_CREDENTIAL_SCOPE_CHANGED')
        url,nodes=start(config['base'],config['table'],feishu_origin)
        nodes += [code('Saved restored fields','return [{json:'+json.dumps(result,ensure_ascii=False)+'}];'),
                  http('Read restored original row',url+'/'+task['id'],feishu=True),
                  persisted_fields_check('Verify restored original row','Saved restored fields'),
                  code('Reconciliation readback',"return [{json:{base_read:'verified',provider_requests:0}}];")]
        verify_setup(api,root,project+'-reconcile',compile_nodes(nodes),{'httpCustomAuth':pool['roles']['feishu']})
        # Do not release if either receipt changed while Base was being read.
        for saved,expected in zip(ledgers,rows):
            current=api.request('GET','/api/v1/data-tables/'+saved['table']+'/rows')['data']
            if current!=[expected]: raise Fault('RECONCILE_RECEIPT_CHANGED')
        updated=api.request('PATCH','/api/v1/data-tables/'+ledgers[1]['table']+'/rows/update',{
            'filter':{'type':'and','filters':[{'columnName':'id','condition':'eq','value':approval['id']},
                *[{'columnName':key,'condition':'eq','value':approval[key]} for key in ('status','owner','payload','result')]]},
            'data':{'status':'idle'},'returnData':True})
        if len(updated)!=1:raise Fault('RECONCILE_APPROVAL_CHANGED')
        current=api.request('GET','/api/v1/data-tables/'+ledgers[1]['table']+'/rows')['data']
        if len(current)!=1 or current[0]['status']!='idle' or current[0]['result']!=approval['result']:
            raise Fault('RECONCILE_READBACK_REQUIRED_NO_RETRY')
        return {'reconciled':True,'provider_requests':0,'old_plan_reusable':False,'next':'可在飞书申请新的费用计划；旧编号保持失效。'}
