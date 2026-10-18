"""Portable workflows: standard nodes only, no private Runner/network dependency.

Imports are disabled. Production flows are manually invoked, one row per run.
They do not claim distributed exactly-once guarantees; scheduled activation is
not delivered until overlapping execution/approval-identity gates are verified.
"""
import json
from uuid import NAMESPACE_URL, uuid5
from portable_n8n import identifier


def code(name, source):
    return {'name': name, 'type': 'n8n-nodes-base.code', 'typeVersion': 2,
            'parameters': {'jsCode': source}}


def http(name, url, method='GET', body=None, credential=None, feishu=False):
    parameters = {'url': url, 'method': method, 'options': {'timeout': 60000, 'redirect': {'redirect': {'followRedirects': False}}}}
    if body is not None:
        parameters.update(sendBody=True, specifyBody='json', jsonBody=body)
    if feishu:
        parameters.update(sendHeaders=True, headerParameters={'parameters': [{
            'name': 'Authorization', 'value': "={{ 'Bearer ' + $('Feishu token').first().json.tenant_access_token }}"}]})
    node = {'name': name, 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2,
            'parameters': parameters, 'retryOnFail': False}
    if credential:
        parameters.update(authentication='genericCredentialType', genericAuthType=credential)
        node['credentials'] = {credential: {'id': 'UNBOUND', 'name': 'UNBOUND'}}
    return node


def compile_nodes(nodes):
    for index, node in enumerate(nodes):
        node['id'] = 'vf-' + str(index)
        node['position'] = [index * 240, 0]
    workflow = {'name': 'Video Factory portable', 'nodes': nodes,
            'connections': {left['name']: {'main': [[{'node': right['name'], 'type': 'main', 'index': 0}]]}
                            for left, right in zip(nodes, nodes[1:])},
            'settings': {'executionOrder': 'v1', 'executionTimeout': 240,
                         'saveDataSuccessExecution': 'none', 'saveDataErrorExecution': 'none',
                         'saveManualExecutions': False}, 'active': False}
    if any(n['name']=='Have Feishu images' for n in nodes):
        workflow['connections']['Have Feishu images']={'main':[
            [{'node':'Read reference field','type':'main','index':0}],
            [{'node':'Resolve video references','type':'main','index':0}]]}
    return workflow


def attachment_nodes(base, table, feishu_origin):
    """Download scoped Base attachments without publishing a public image URL."""
    field_url=f'{feishu_origin}/open-apis/bitable/v1/apps/{base}/tables/{table}/fields?page_size=100'
    download=http('Download Feishu image','={{ $json.download }}',feishu=True)
    download['parameters']['options']['response']={'response':{'responseFormat':'file','outputPropertyName':'image'}}
    return [
        {'name':'Have Feishu images','type':'n8n-nodes-base.if','typeVersion':2.2,
         'parameters':{'conditions':{'options':{'caseSensitive':True,'leftValue':'','typeValidation':'strict'},
                       'conditions':[{'id':'has-images','leftValue':"={{ $('Select task').first().json.attachments.length > 0 }}",
                                      'rightValue':True,'operator':{'type':'boolean','operation':'true','singleValue':True}}],
                       'combinator':'and'},'options':{}}},
        http('Read reference field',field_url,feishu=True),
        code('Prepare attachment downloads',"const origin="+json.dumps(feishu_origin)+",table="+json.dumps(table)+";"+r"""
if($json.code!==0 || $json.data?.has_more) throw new Error('REFERENCE_FIELD_UNAVAILABLE');
const fields=$json.data.items.filter(f=>f.field_name==='参考图附件' && f.type===17);
if(fields.length!==1) throw new Error('REFERENCE_ATTACHMENT_FIELD_REQUIRED');
const task=$('Select task').first().json;
return task.attachments.map(a=>({json:{download:origin+'/open-apis/drive/v1/medias/'+a.file_token+'/download?extra='+encodeURIComponent(JSON.stringify({bitablePerm:{tableId:table,attachments:{[fields[0].field_id]:{[task.id]:[a.file_token]}}}}))}}));"""),
        download,
        code('Resolve video references',r"""const task=$('Select task').first().json;
const references=[...task.references]; let total=0;
if(task.attachments.length) {
  if($input.all().length!==task.attachments.length) throw new Error('REFERENCE_DOWNLOAD_INCOMPLETE');
  for(let i=0;i<task.attachments.length;i++) {
    const data=await this.helpers.getBinaryDataBuffer(i,'image');
    total+=data.length;
    if(!data.length || data.length!==task.attachments[i].size || total>20*1024*1024) throw new Error('REFERENCE_SIZE_MISMATCH');
    const hex=data.subarray(0,12).toString('hex');
    const mime=hex.startsWith('ffd8ff')?'image/jpeg':hex.startsWith('89504e470d0a1a0a')?'image/png':
      data.subarray(0,4).toString()==='RIFF' && data.subarray(8,12).toString()==='WEBP'?'image/webp':null;
    if(!mime) throw new Error('REFERENCE_NOT_JPEG_PNG_WEBP');
    references.push('data:'+mime+';base64,'+data.toString('base64'));
  }
}
return [{json:{...task,model_references:references}}];""")]


def persisted_fields_check(name, result_node):
    """A successful write receipt is not proof that Base saved the result."""
    return code(name, "const expected=$(" + json.dumps(result_node) + ").first().json.fields;" + r"""
if($json.code!==0 || !$json.data?.record?.fields) throw new Error('RESULT_READBACK_UNAVAILABLE_RESULT_SAVED');
const actual=$json.data.record.fields;
const text=v=>Array.isArray(v)?v.map(x=>x.text||'').join(''):String(v||'');
for(const [key,value] of Object.entries(expected)) {
  const equal=value && typeof value==='object' && !Array.isArray(value)
    ? actual[key]?.link===value.link && actual[key]?.text===value.text
    : text(actual[key])===String(value);
  if(!equal) throw new Error('RESULT_NOT_PERSISTED_RECONCILE_WITHOUT_MODEL_RETRY');
}
return [{json:{writeback:'verified',human_review:'in_feishu',automatic_resubmit:false}}];""")


def start(base, table, feishu_origin='https://open.feishu.cn'):
    identifier(base); identifier(table)
    url = f'{feishu_origin}/open-apis/bitable/v1/apps/{base}/tables/{table}/records'
    return url, [
        {'name': 'Run explicitly', 'type': 'n8n-nodes-base.manualTrigger', 'typeVersion': 1, 'parameters': {}},
        http('Feishu token', feishu_origin + '/open-apis/auth/v3/tenant_access_token/internal',
             'POST', '{}', 'httpCustomAuth'),
        code('Check token', "if ($json.code !== 0 || !$json.tenant_access_token) throw new Error('FEISHU_AUTH_FAILED'); return [{json:{ok:true}}];"),
    ]


def probe_workflow(base, table, *, feishu_origin='https://open.feishu.cn'):
    url, nodes = start(base, table, feishu_origin)
    nodes += [http('Read Base', url + '?page_size=1', feishu=True),
              code('Connection receipt', "if ($json.code !== 0) throw new Error('FEISHU_BASE_ACCESS_DENIED'); return [{json:{base_read:'verified', records_seen:($json.data.items || []).length, provider_requests:0}}];")]
    return compile_nodes(nodes)


FIELDS = {'状态': 3, '商品资料': 1, '脚本': 1, '审核意见': 1, '脚本历史': 1, '参考图附件': 17,
          '执行编号': 1, '视频任务ID': 1, '视频链接': 15, '参考图链接': 1,
          '生成计划': 1, '费用确认编号': 1}
STATUSES = ['请求脚本', '脚本生成中', '脚本待审核', '脚本通过', '脚本退回',
            '视频生成已确认', '视频提交中', '视频生成中', '视频待审核', '视频通过', '视频退回',
            '申请脚本计划', '申请视频计划', '费用待确认']


def generation_profile(flow, *, script_origin='https://api.deepseek.com', video_origin='https://api.minimax.cn', script_selection=None, video_selection=None):
    """The same non-secret contract is displayed, confirmed and executed."""
    if flow == 'script' and script_selection is not None:
        from portable_models import profile as script_profile
        result = script_profile(script_selection)
        if script_origin != 'https://api.deepseek.com':
            result['endpoint'] = script_origin + '/chat/completions'
        return result
    if flow == 'script':
        return {'provider': 'DeepSeek', 'model': 'deepseek-flash',
                'endpoint': script_origin + '/chat/completions', 'max_tokens': 2048,
                'stream': False, 'thinking': {'type': 'disabled'}}
    if flow in ('video-submit', 'video-poll') and video_selection is not None:
        from portable_video_models import profile as video_profile
        result=video_profile(video_selection)
        if video_origin!='https://api.minimax.cn':
            from urllib.parse import urlsplit
            result['endpoint']=video_origin+urlsplit(result['endpoint']).path
        return result
    if flow in ('video-submit', 'video-poll'):
        return {'provider': 'MiniMax China', 'model': 'MiniMax-H3',
                'endpoint': video_origin + '/v2/video_generation',
                'duration': 6, 'resolution': '768P', 'ratio': '9:16'}
    raise ValueError('FLOW_INVALID')


def production_workflow(base, table, flow, *, feishu_origin='https://open.feishu.cn',
                        script_origin='https://api.deepseek.com', video_origin='https://api.minimax.cn', script_selection=None, video_selection=None):
    if flow not in ('script', 'video-submit', 'video-poll'):
        raise ValueError('FLOW_INVALID')
    profile = generation_profile(flow, script_origin=script_origin, video_origin=video_origin, script_selection=script_selection, video_selection=video_selection)
    url, nodes = start(base, table, feishu_origin)
    status = {'script': ['请求脚本'], 'video-submit': ['视频生成已确认'], 'video-poll': ['视频生成中']}[flow]
    # Server-side filter avoids silently ignoring matching rows beyond page 1.
    search = {'filter': {'conjunction': 'or', 'conditions': [
        {'field_name': '状态', 'operator': 'is', 'value': [s]} for s in status]}}
    nodes += [http('Find requested task', url + '/search?page_size=1', 'POST', json.dumps(search, ensure_ascii=False), feishu=True),
        code('Select task', r"""if ($json.code !== 0) throw new Error('FEISHU_SEARCH_FAILED');
const row = ($json.data.items || [])[0]; if (!row) return [];
const text = v => Array.isArray(v) ? v.map(x => x.text || '').join('') : String(v || '');
return [{json:{id:row.record_id, fields:row.fields, product:text(row.fields['商品资料']),
script:text(row.fields['脚本']), feedback:text(row.fields['审核意见']), history:text(row.fields['脚本历史']),
references:text(row.fields['参考图链接']).split(/\s+/).filter(Boolean),
attachments:(row.fields['参考图附件'] || []).map(a=>({file_token:a.file_token,name:a.name,size:a.size})),
job:text(row.fields['视频任务ID']), execution:String($execution.id)}}];""")]
    target = "={{ " + json.dumps(url + '/') + " + $('Select task').first().json.id }}"
    def write(name, fields):
        return http(name, target, 'PUT', fields, feishu=True)
    if flow != 'video-poll':
        required = 'product' if flow == 'script' else 'script'
        if flow == 'video-submit':
            nodes.append(code('Validate references', "if ($json.script.length>7000) throw new Error('H3_SCRIPT_TOO_LONG'); const count=$json.references.length+$json.attachments.length; if (count<1 || count>9 || $json.references.some(url=>!url.startsWith('https://'))) throw new Error('H3_REFERENCE_IMAGES_REQUIRED'); if($json.attachments.some(a=>! /^[A-Za-z0-9_-]+$/.test(a.file_token || '') || !Number.isInteger(a.size) || a.size<1 || !/\\.(jpe?g|png|webp)$/i.test(a.name || '')) || $json.attachments.reduce((n,a)=>n+a.size,0)>20*1024*1024) throw new Error('REFERENCE_ATTACHMENT_INVALID_OR_TOO_LARGE'); return $input.all();"))
            nodes += attachment_nodes(base,table,feishu_origin)
        nodes.append(code('Validate request', f"if (!$json.{required}.trim()) throw new Error('TASK_CONTENT_REQUIRED');" +
                          "if (($json.history+'\\n'+JSON.stringify({execution:$json.execution,script:$json.script,feedback:$json.feedback})).trim().length>18000) throw new Error('HISTORY_FULL_BEFORE_PROVIDER'); return $input.all();"))
        state = '脚本生成中' if flow == 'script' else '视频提交中'
        nodes += [code('Build reservation', 'return [{json:{fields:{"状态":' + json.dumps(state) + ',"执行编号":$("Select task").first().json.execution}}}];'),
                  write('Reserve task', '={{ $json }}'),
                  code('Check reservation', "if ($json.code !== 0) throw new Error('FEISHU_RESERVATION_FAILED'); return [{json:{ok:true}}];"),
                  http('Read reservation', target, feishu=True),
                  code('Verify reservation', r"""if ($json.code !== 0) throw new Error('FEISHU_RESERVATION_READ_FAILED');
const fields=$json.data.record.fields, task=$('Select task').first().json;
const text=v=>Array.isArray(v)?v.map(x=>x.text||'').join(''):String(v||'');
if (text(fields['执行编号'])!==task.execution || !['脚本生成中','视频提交中'].includes(fields['状态'])) throw new Error('TASK_CLAIM_CHANGED');
if (text(fields['商品资料'])!==task.product || text(fields['脚本'])!==task.script || text(fields['审核意见'])!==task.feedback || JSON.stringify(text(fields['参考图链接']).split(/\s+/).filter(Boolean))!==JSON.stringify(task.references)) throw new Error('TASK_INPUT_CHANGED_BEFORE_PROVIDER');
if(JSON.stringify((fields['参考图附件'] || []).map(a=>({file_token:a.file_token,name:a.name,size:a.size})))!==JSON.stringify(task.attachments)) throw new Error('TASK_ATTACHMENTS_CHANGED_BEFORE_PROVIDER');
return [{json:{ok:true}}];""")]
    if flow == 'script':
        nodes += [code('Build script request', 'const profile='+json.dumps(profile)+';'+"""return [{json:{model:profile.model,stream:profile.stream,max_tokens:profile.max_tokens,thinking:profile.thinking,messages:[{role:'system',content:'根据商品资料编写可审核的短视频脚本，不编造功效。结合旧脚本和修改意见，返回完整新脚本。'},{role:'user',content:JSON.stringify({product:$('Select task').first().json.product,previous:$('Select task').first().json.script,feedback:$('Select task').first().json.feedback})}]}}];"""),
                  http('Generate script', profile['endpoint'], 'POST', '={{ $json }}', 'httpHeaderAuth'),
                  code('Validate script', r"""const script = $json.choices?.[0]?.message?.content;
if ($json.choices?.[0]?.finish_reason !== 'stop' || typeof script !== 'string' || !script.trim() || script.length>6500) throw new Error('SCRIPT_RESPONSE_REQUIRES_RECONCILIATION');
const row=$('Select task').first().json;
const history=(row.history+'\n'+JSON.stringify({execution:row.execution,script:row.script,feedback:row.feedback})).trim();
if (history.length>18000) throw new Error('HISTORY_FULL_RECONCILE_BEFORE_RETRY');
return [{json:{fields:{'脚本':script,'脚本历史':history,'状态':'脚本待审核'}}}];"""),
                  write('Write script for human review', '={{ $json }}')]
    elif flow == 'video-submit':
        nodes += [code('Build video request', 'const profile='+json.dumps(profile)+';'+"""return [{json:{model:profile.model,content:[{type:'text',text:$('Select task').first().json.script},...$('Resolve video references').first().json.model_references.map(url=>({type:'image_url',role:'reference_image',image_url:{url}}))],duration:profile.duration,resolution:profile.resolution,ratio:profile.ratio}}];"""),
                  http('Submit video once', profile['endpoint'], 'POST', '={{ $json }}', 'httpHeaderAuth'),
                  code('Validate video receipt', "if (!/^[0-9]{10,24}$/.test($json.task_id || '')) throw new Error('VIDEO_SUBMISSION_REQUIRES_RECONCILIATION'); return [{json:{fields:{'视频任务ID':String($json.task_id),'状态':'视频生成中'}}}];"),
                  write('Save video job receipt', '={{ $json }}')]
    else:
        nodes += [code('Require job receipt', "if (!/^[0-9]{10,24}$/.test($json.job)) throw new Error('VIDEO_JOB_REQUIRED_NO_RESUBMIT'); return $input.all();"),
                  http('Poll existing video', '={{ ' + json.dumps(video_origin + '/v2/query/video_generation/') + " + $('Select task').first().json.job }}", credential='httpHeaderAuth'),
                  code('Check video completion', """const task=$json.task;
if (!task || String(task.id)!==$('Select task').first().json.job || task.model!=='MiniMax-H3') throw new Error('VIDEO_QUERY_UNVERIFIED');
if (['failed','cancelled'].includes(task.status)) throw new Error('VIDEO_FAILED_REQUIRES_HUMAN_REVIEW');
if (['queued','running'].includes(task.status)) return [];
if (task.status!=='succeeded' || !String(task.content?.url || '').startsWith('https://')) throw new Error('VIDEO_DOWNLOAD_UNAVAILABLE');
return [{json:{fields:{'视频链接':{link:task.content.url,text:'查看视频'},'状态':'视频待审核'}}}];"""),
                  http('Read task before video writeback',target,feishu=True),
                  code('Verify video task unchanged',r"""if($json.code!==0) throw new Error('VIDEO_TASK_READBACK_FAILED');
const task=$('Select task').first().json, fields=$json.data.record.fields;
const text=v=>Array.isArray(v)?v.map(x=>x.text||'').join(''):String(v||'');
if(fields['状态']!=='视频生成中' || text(fields['视频任务ID'])!==task.job || text(fields['脚本'])!==task.script)
    throw new Error('VIDEO_TASK_CHANGED_NO_OVERWRITE');
return [{json:$('Check video completion').first().json}];"""),
                  write('Write video for human review', '={{ $json }}')]
    if flow in ('video-submit','video-poll') and video_selection is not None:
        from portable_video_models import apply as apply_video
        apply_video(nodes,video_selection,profile,flow,video_origin if video_origin!='https://api.minimax.cn' else None)
    if flow == 'script' and script_selection is not None:
        for node in nodes:
            if node['name'] == 'Build script request':
                source = node['parameters']['jsCode']
                source = source.replace('model:profile.model,stream:profile.stream,max_tokens:profile.max_tokens,thinking:profile.thinking,', '...options,')
                source = source.replace('return [{json:', 'const {provider,endpoint,...options}=profile;return [{json:', 1)
                node['parameters']['jsCode'] = source
            if node['name'] == 'Validate script':
                node['parameters']['jsCode'] = node['parameters']['jsCode'].replace(
                    "if ($json.choices?.[0]?.finish_reason",
                    "if (typeof script==='string' && /<[/]?think>/i.test(script)) throw new Error('SCRIPT_REASONING_NOT_SEPARATED');\nif ($json.choices?.[0]?.finish_reason")
    nodes.append(code('Writeback receipt', "if ($json.code !== 0) throw new Error('FEISHU_WRITEBACK_FAILED_DO_NOT_RESUBMIT_MODEL'); return [{json:{writeback:'verified',human_review:'in_feishu',automatic_resubmit:false}}];"))
    result_node={'script':'Validate script','video-submit':'Validate video receipt','video-poll':'Check video completion'}[flow]
    nodes += [http('Read persisted result',target,feishu=True),
              persisted_fields_check('Verify persisted result',result_node)]
    return compile_nodes(nodes)


def schema_workflow(base, table, *, feishu_origin='https://open.feishu.cn'):
    """Add missing fields; preserve existing fields/options and reject type drift."""
    _, nodes = start(base, table, feishu_origin)
    fields_url=f'{feishu_origin}/open-apis/bitable/v1/apps/{base}/tables/{table}/fields'
    script = r"""if ($json.code !== 0 || $json.data.has_more) throw new Error('SCHEMA_INVENTORY_INCOMPLETE');
const fields=$json.data.items || [], specs=REQUIRED_FIELDS, statuses=REQUIRED_STATUSES;
const changes=[];
for (const [name,type] of Object.entries(specs)) {
  const matches=fields.filter(f=>f.field_name===name);
  if(matches.length>1 || (matches[0] && matches[0].type!==type)) throw new Error('SCHEMA_FIELD_TYPE_OR_NAME_CONFLICT');
  const found=matches[0];
  if (!found) {
    const body={field_name:name,type};
    if(type===3) body.property={options:statuses.map(name=>({name}))};
    changes.push({json:{method:'POST',suffix:'',body}});
  } else if(type===3) {
    const options=found.property?.options || [];
    const missing=statuses.filter(name=>!options.some(o=>o.name===name));
    if(missing.length) {
      if(!/^[A-Za-z0-9_-]+$/.test(found.field_id)) throw new Error('FIELD_ID_INVALID');
      changes.push({json:{method:'PUT',suffix:'/'+found.field_id,body:{field_name:name,type,property:{...found.property,options:[...options,...missing.map(name=>({name}))]}}}});
    }
  }
}
return changes.length ? changes : [{json:{noop:true}}];""".replace('REQUIRED_FIELDS',json.dumps(FIELDS,ensure_ascii=False)).replace('REQUIRED_STATUSES',json.dumps(STATUSES,ensure_ascii=False))
    nodes += [http('Read existing fields',fields_url+'?page_size=100',feishu=True),
              code('Plan additive schema changes',script),
              {'name':'Fields already ready','type':'n8n-nodes-base.if','typeVersion':2.2,
               'parameters':{'conditions':{'options':{'caseSensitive':True,'leftValue':'','typeValidation':'strict','version':2},
                  'conditions':[{'id':'noop','leftValue':'={{ $json.noop === true }}','rightValue':'','operator':{'type':'boolean','operation':'true','singleValue':True}}],
                  'combinator':'and'},'options':{}}},
              http('Apply missing fields','={{ '+json.dumps(fields_url)+' + $json.suffix }}',
                   '={{ $json.method }}','={{ $json.body }}',feishu=True),
              code('Check schema responses',"if ($input.all().some(item=>!item.json.noop && item.json.code !== 0)) throw new Error('SCHEMA_CHANGE_INCOMPLETE_REREAD_FIELDS'); return [{json:{ok:true}}];"),
              http('Read prepared fields',fields_url+'?page_size=100',feishu=True),
              code('Schema receipt',r"""if ($json.code!==0 || $json.data.has_more) throw new Error('SCHEMA_READBACK_INCOMPLETE');
const fields=$json.data.items || [], specs=REQUIRED_FIELDS, statuses=REQUIRED_STATUSES;
for (const [name,type] of Object.entries(specs)) {
  const matches=fields.filter(f=>f.field_name===name);
  if(matches.length!==1 || matches[0].type!==type) throw new Error('SCHEMA_READBACK_MISMATCH');
  if(type===3 && statuses.some(name=>!(matches[0].property?.options || []).some(o=>o.name===name)))
    throw new Error('SCHEMA_STATUS_OPTIONS_INCOMPLETE');
}
for (const before of $('Read existing fields').first().json.data.items || []) {
  const after=fields.find(f=>f.field_id===before.field_id);
  if(!after || after.field_name!==before.field_name || after.type!==before.type)
    throw new Error('SCHEMA_EXISTING_FIELD_CHANGED');
  for (const option of before.property?.options || []) {
    if(!(after.property?.options || []).some(o=>o.name===option.name &&
        (option.id===undefined || o.id===option.id) && (option.color===undefined || o.color===option.color)))
      throw new Error('SCHEMA_EXISTING_OPTION_CHANGED');
  }
}
return [{json:{schema_change:'accepted',schema_readback:'verified',provider_requests:0}}];""".replace('REQUIRED_FIELDS',json.dumps(FIELDS,ensure_ascii=False)).replace('REQUIRED_STATUSES',json.dumps(STATUSES,ensure_ascii=False)))]
    workflow=compile_nodes(nodes)
    workflow['connections']['Fields already ready']={'main':[
        [{'node':'Check schema responses','type':'main','index':0}],
        [{'node':'Apply missing fields','type':'main','index':0}]]}
    return workflow


def setup_hook(workflow, path):
    """Temporary authenticated entry for zero-model setup checks only."""
    identifier(path)
    if any(n['name'] in ('Generate script','Submit video once','Poll existing video') for n in workflow['nodes']):
        raise ValueError('SETUP_HOOK_MUST_NOT_CALL_MODELS')
    workflow=json.loads(json.dumps(workflow))
    start=workflow['nodes'][0]['name']
    hook={'id':'vf-setup-hook','name':'Temporary setup check','type':'n8n-nodes-base.webhook',
          'typeVersion':2,'webhookId':str(uuid5(NAMESPACE_URL,'video-factory:'+path)),
          'position':[0,200],'parameters':{'httpMethod':'POST','path':path,
          'responseMode':'lastNode','authentication':'headerAuth','options':{}},
          'credentials':{'httpHeaderAuth':{'id':'UNBOUND','name':'UNBOUND'}}}
    workflow['nodes'].append(hook)
    workflow['connections'][hook['name']]=workflow['connections'][start]
    return workflow


def ledger_update(name, ledger, status, values, owner=False):
    conditions=[{'keyName':'id','condition':'eq','keyValue':ledger['row']},
                {'keyName':'status','condition':'eq','keyValue':status}]
    if owner:
        conditions.append({'keyName':'owner','condition':'eq','keyValue':"={{ $('Select task').first().json.execution }}"})
    return {'name':name,'type':'n8n-nodes-base.dataTable','typeVersion':1.1,
            'parameters':{'resource':'row','operation':'update',
                          'dataTableId':{'__rl':True,'mode':'id','value':ledger['table']},
                          'matchType':'allConditions','filters':{'conditions':conditions},
                          'columns':{'mappingMode':'defineBelow','value':values},'options':{}}}


def guard_workflow(workflow, ledger):
    """Serialize a project's model operations in n8n's own transactional store.

    A failure leaves the row busy/result_ready; no lease expiry guesses whether
    a paid POST reached its provider. Result fields are saved before Base write.
    """
    identifier(ledger['table'])
    if type(ledger['row']) is not int or ledger['row']<1: raise ValueError('LEDGER_ROW_REQUIRED')
    nodes=json.loads(json.dumps(workflow['nodes']))
    claim=ledger_update('Claim project execution',ledger,'idle',{
        'status':'busy','owner':"={{ $('Select task').first().json.execution }}",
        'payload':"={{ JSON.stringify($('Select task').first().json) }}",'result':''})
    verify=code('Verify exclusive owner',"if ($json.owner !== $('Select task').first().json.execution) throw new Error('EXECUTION_NOT_OWNED'); return [{json:$('Select task').first().json}];")
    anchor=next(i for i,n in enumerate(nodes) if n['name'] in ('Validate request','Require job receipt'))
    nodes[anchor+1:anchor+1]=[claim,verify]
    # Polls that are still running must release their claim too; they do not
    # make paid requests and return no items before writeback. Leave polling
    # unguarded for now; only mutation/submission flows need exclusivity.
    names=[n['name'] for n in nodes]
    write_name='Write script for human review' if 'Write script for human review' in names else 'Save video job receipt'
    before=next(i for i,n in enumerate(nodes) if n['name']==write_name)
    save=ledger_update('Persist model result',ledger,'busy',{'status':'result_ready','result':'={{ JSON.stringify($json) }}'},owner=True)
    restore=code('Restore saved result',"if ($json.owner !== $('Select task').first().json.execution) throw new Error('RESULT_NOT_OWNED'); return [{json:JSON.parse($json.result)}];")
    readback=next(n for n in nodes if n['name']=='Read reservation')
    fresh=http('Read task before result writeback',readback['parameters']['url'],feishu=True)
    unchanged=code('Verify task before result writeback',r"""if ($json.code!==0) throw new Error('TASK_READBACK_FAILED_RESULT_SAVED');
const fields=$json.data.record.fields, task=$('Select task').first().json;
const text=v=>Array.isArray(v)?v.map(x=>x.text||'').join(''):String(v||'');
const result=$('Restore saved result').first().json;
const expected=result.fields['状态']==='脚本待审核'?'脚本生成中':'视频提交中';
if(text(fields['执行编号'])!==task.execution || fields['状态']!==expected ||
   text(fields['商品资料'])!==task.product || text(fields['脚本'])!==task.script || text(fields['审核意见'])!==task.feedback ||
   JSON.stringify(text(fields['参考图链接']).split(/\s+/).filter(Boolean))!==JSON.stringify(task.references))
    throw new Error('TASK_CHANGED_RESULT_SAVED_NO_OVERWRITE');
if(JSON.stringify((fields['参考图附件'] || []).map(a=>({file_token:a.file_token,name:a.name,size:a.size})))!==JSON.stringify(task.attachments)) throw new Error('TASK_ATTACHMENTS_CHANGED_RESULT_SAVED');
return [{json:result}];""")
    nodes[before:before]=[save,restore,fresh,unchanged]
    nodes += [ledger_update('Release completed execution',ledger,'result_ready',{'status':'idle'},owner=True),
              code('Execution receipt',"return [{json:{writeback:'verified',human_review:'in_feishu',automatic_resubmit:false,result_saved:true}}];")]
    return compile_nodes(nodes)


def recovery_workflow(base, table, ledger, *, feishu_origin='https://open.feishu.cn'):
    """Replay a saved result to its original Base row; contains no model node."""
    url,nodes=start(base,table,feishu_origin)
    nodes += [
        {'name':'Read saved result','type':'n8n-nodes-base.dataTable','typeVersion':1.1,
         'parameters':{'resource':'row','operation':'get','dataTableId':{'__rl':True,'mode':'id','value':ledger['table']},
         'matchType':'allConditions','filters':{'conditions':[{'keyName':'id','condition':'eq','keyValue':ledger['row']},
                                                            {'keyName':'status','condition':'eq','keyValue':'result_ready'}]},
         'returnAll':False,'limit':1}},
        code('Select task',"""const task=JSON.parse($json.payload), result=JSON.parse($json.result);
if (!task.id || !/^[A-Za-z0-9_-]+$/.test(task.id) || task.execution!==$json.owner || !result.fields ||
    !['脚本待审核','视频生成中'].includes(result.fields['状态'])) throw new Error('SAVED_RESULT_INVALID');
return [{json:{...task,result}}];"""),
        http('Read original task', '={{ '+json.dumps(url+'/')+" + $('Select task').first().json.id }}",feishu=True),
        code('Verify unchanged task',r"""if ($json.code!==0) throw new Error('ORIGINAL_TASK_UNREADABLE');
const fields=$json.data.record.fields, saved=$('Select task').first().json;
const text=v=>Array.isArray(v)?v.map(x=>x.text||'').join(''):String(v||'');
if(text(fields['执行编号'])!==saved.execution) throw new Error('TASK_CHANGED_NO_WRITEBACK');
const expected=saved.result.fields['状态']==='脚本待审核'?'脚本生成中':'视频提交中';
if(fields['状态']!==expected && fields['状态']!==saved.result.fields['状态']) throw new Error('REVIEW_STATE_CHANGED_NO_WRITEBACK');
if(text(fields['商品资料'])!==saved.product || text(fields['审核意见'])!==saved.feedback || JSON.stringify(text(fields['参考图链接']).split(/\s+/).filter(Boolean))!==JSON.stringify(saved.references)) throw new Error('INPUT_CHANGED_NO_WRITEBACK');
if(JSON.stringify((fields['参考图附件'] || []).map(a=>({file_token:a.file_token,name:a.name,size:a.size})))!==JSON.stringify(saved.attachments || [])) throw new Error('ATTACHMENTS_CHANGED_NO_WRITEBACK');
if(text(fields['脚本'])!==saved.script && text(fields['脚本'])!==saved.result.fields['脚本']) throw new Error('SCRIPT_CHANGED_NO_WRITEBACK');
return [{json:saved.result}];"""),
        http('Restore result to Base','={{ '+json.dumps(url+'/')+" + $('Select task').first().json.id }}",'PUT','={{ $json }}',feishu=True),
        code('Verify recovery writeback',"if ($json.code!==0) throw new Error('WRITEBACK_STILL_UNAVAILABLE_NO_MODEL_RETRY'); return $input.all();"),
        http('Read recovered result','={{ '+json.dumps(url+'/')+" + $('Select task').first().json.id }}",feishu=True),
        persisted_fields_check('Verify recovered result','Verify unchanged task'),
        ledger_update('Release recovered execution',ledger,'result_ready',{'status':'idle'},owner=True),
        code('Recovery receipt',"return [{json:{writeback:'verified',provider_requests:0,recovered:true}}];")]
    return compile_nodes(nodes)
