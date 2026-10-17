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
                           production_workflow, guard_workflow, persisted_fields_check)

APPROVAL_FIELDS = {'生成计划': 1, '费用确认编号': 1}
APPROVAL_STATUSES = ['申请脚本计划', '申请视频计划', '费用待确认']


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


def plan_workflow(base, table, flow, approval, policy, *, feishu_origin='https://open.feishu.cn'):
    """No model nodes. One pending plan per project; never replace it silently."""
    policy = policy_checked(policy)
    if flow not in ('script', 'video-submit'):
        raise Fault('APPROVAL_FLOW_INVALID')
    url, nodes = start(base, table, feishu_origin)
    state = '申请脚本计划' if flow == 'script' else '申请视频计划'
    search = {'filter': {'conjunction': 'and', 'conditions': [
        {'field_name': '状态', 'operator': 'is', 'value': [state]}]}}
    nodes += [http('Find plan request', url+'/search?page_size=1', 'POST', json.dumps(search, ensure_ascii=False), feishu=True),
        code('Select task', "const policy="+json.dumps(policy)+",flow="+json.dumps(flow)+",base="+json.dumps(base)+",table="+json.dumps(table)+";"+r'''
if($json.code!==0) throw new Error('PLAN_SEARCH_FAILED');
const row=$json.data.items?.[0]; if(!row) return [];
const text=v=>Array.isArray(v)?v.map(x=>x.text||'').join(''):String(v||'');
const fields=row.fields;
if(!/^rec[A-Za-z0-9_-]+$/.test(row.record_id)) throw new Error('PLAN_RECORD_INVALID');
if(!text(fields[flow==='script'?'商品资料':'脚本']).trim()) throw new Error('PLAN_CONTENT_REQUIRED');
if(flow==='video-submit' && text(fields['视频任务ID'])) throw new Error('EXISTING_VIDEO_NO_NEW_SUBMISSION');
const snapshot={};
for(const k of ['商品资料','脚本','审核意见','脚本历史','参考图链接','视频任务ID']) snapshot[k]=text(fields[k]);
snapshot['参考图附件']=(fields['参考图附件']||[]).map(a=>({file_token:a.file_token,name:a.name,size:a.size}));
const model=flow==='script'?{provider:'DeepSeek',model:'deepseek-flash',max_tokens:2048}:
  {provider:'MiniMax China',model:'MiniMax-H3',duration:6,resolution:'768P',ratio:'9:16'};
const plan={schema:1,id:'vf-'+policy.configuration+'-'+flow+'-'+String($execution.id),base,table,record:row.record_id,
  policy,flow,model,max_submissions:1,automatic_retry:false,snapshot,expires_at:Date.now()+86400000};
const display=JSON.stringify(plan,null,2)+'\n费用确认：使用上列费用账户，最多调用一次，实际费用按供应商计费；不是人民币金额保证。核对商品、脚本、参考图与模型后，由指定负责人把本计划 id 复制到「费用确认编号」。有效期 24 小时。';
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
 const actual=k==='参考图附件'?(f[k]||[]).map(a=>({file_token:a.file_token,name:a.name,size:a.size})):text(f[k]);
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
                          video_origin='https://api.minimax.cn', verification_only=False):
    """A trusted event selects the exact protected plan, never the first paid row.

    verification_only consumes the plan and returns a zero-provider receipt;
    it has no provider nodes. It cannot be toggled via a webhook request.
    """
    policy=policy_checked(policy); identifier(path)
    workflow=guard_workflow(production_workflow(base,table,flow,feishu_origin=feishu_origin,
                          script_origin=script_origin,video_origin=video_origin),execution)
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
        code('Check protected plan',"const policy="+json.dumps(policy)+",flow="+json.dumps(flow)+";"+r'''
const saved=JSON.parse($json.payload),p=saved.plan;
if(JSON.stringify(p.policy)!==JSON.stringify(policy) || p.flow!==flow || p.max_submissions!==1 || p.automatic_retry!==false)
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
if(row.record_id!==p.record || f['状态']!=='费用待确认' || text(f['费用确认编号'])!==p.id || text(f['生成计划'])!==saved.display)
 throw new Error('APPROVAL_TASK_OR_PLAN_CHANGED');
for(const [k,v] of Object.entries(p.snapshot)) {
 const actual=k==='参考图附件'?(f[k]||[]).map(a=>({file_token:a.file_token,name:a.name,size:a.size})):text(f[k]);
 if(JSON.stringify(actual)!==JSON.stringify(v)) throw new Error('APPROVED_INPUT_CHANGED');
}
return [{json:{id:p.record,fields:f,product:text(f['商品资料']),script:text(f['脚本']),feedback:text(f['审核意见']),
 history:text(f['脚本历史']),references:text(f['参考图链接']).split(/\s+/).filter(Boolean),
 attachments:p.snapshot['参考图附件'],job:text(f['视频任务ID']),execution:String($execution.id),approval:p}}];'''),
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
    pool=json.loads((root/('credentials-'+project+'.json')).read_text())
    if pool['origin']!=api.url or pool['app']!=policy['app']: raise Fault('APPROVAL_CREDENTIAL_SCOPE_MISMATCH')
    event_root=secure_directory(root/(project+'-events'))
    scope={'origin':api.url,'project':project,'base':base,'table':table,'policy':policy,'verification_only':verification_only}
    with locked(event_root):
        config_path=event_root/'events.json'
        if config_path.exists():
            config=json.loads(config_path.read_text())
            if any(config.get(k)!=v for k,v in scope.items()): raise Fault('APPROVAL_SCOPE_CHANGED_EXPLICIT_MIGRATION_REQUIRED')
        else:
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
    for flow,role in [('script','script'),('video-submit','video')]:
        if role not in pool['roles']: continue
        path='vf-owner-'+project+'-'+flow+'-'+policy['configuration']
        identifier(path)
        config['workflows']['plan-'+flow]=provision(api,root,project+'-plan-'+flow,
            plan_workflow(base,table,flow,approval,policy),{'httpCustomAuth':pool['roles']['feishu']})
        credentials={'httpCustomAuth':pool['roles']['feishu'],
                     'relay':{'type':'httpHeaderAuth','data':{'name':'X-VF-Event-Relay','value':values['relay_token']}}}
        if not verification_only:credentials['httpHeaderAuth']=pool['roles'][role]
        config['workflows']['confirm-'+flow]=provision(api,root,project+'-confirm-'+flow,
            confirmation_workflow(base,table,flow,execution,approval,policy,path,verification_only=verification_only),credentials)
        config['hooks'][flow]=path
        with locked(event_root):private_write(event_root/'events.json',config)
    return {'configured':True,'active':False,'verification_only':verification_only,
            'event_root':str(event_root),'workflows':config['workflows'],
            'next':'先在飞书应用配置记录变更长连接订阅；启动事件接收器并完成零费用负责人验收，再显式启用付费流程。'}
