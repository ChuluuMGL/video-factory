"""Explicit, zero-provider read of one original Feishu task via owned n8n."""
import json
from pathlib import Path
from portable_n8n import Fault, identifier, origin, verify_setup
from n8n_workflows import start, http, code, compile_nodes, STATUSES


def task_workflow(project, base, table, record, task, revision=None, *, feishu_origin='https://open.feishu.cn'):
    for value in (project,base,table,record,task): identifier(value)
    if revision is not None and (type(revision) is not int or revision < 1): raise Fault('TASK_REVISION_INVALID')
    url,nodes=start(base,table,feishu_origin)
    expected={'source':'portable-n8n-feishu','project':project,'base':base,'table':table,
              'record_id':record,'task':task,'revision':revision}
    nodes += [http('Read exact task',url+'/'+record,feishu=True),
              code('Task inspection receipt','const expected='+json.dumps(expected)+';const states='+json.dumps(STATUSES+['资料待同步','资料待确认'])+';'+r'''
if($json.code!==0 || $json.data?.record?.record_id!==expected.record_id)throw new Error('TASK_READBACK_SCOPE_MISMATCH');
const f=$json.data.record.fields;
if(!f || typeof f!=='object')throw new Error('TASK_READBACK_UNAVAILABLE');
const text=v=>Array.isArray(v)?v.map(x=>x.text||'').join(''):String(v??'');
if(text(f['任务编号'])!==expected.task)throw new Error('TASK_ID_MISMATCH_NO_FALLBACK');
const raw=text(f['审核目标版本']),version=/^[1-9][0-9]*$/.test(raw)?Number(raw):null;
if(version!==null && !Number.isSafeInteger(version))throw new Error('TASK_REVISION_INVALID');
if(expected.revision!==null && expected.revision!==version)throw new Error('TASK_REVISION_CHANGED_OR_UNKNOWN');
const status=text(f['状态']);if(!states.includes(status))throw new Error('TASK_STATUS_UNKNOWN');
return [{json:{base_read:'verified',identity:{...expected,revision:version},status,
  result_presence:{script:Boolean(text(f['脚本']).trim()),video:Boolean(typeof f['视频链接']==='string'?f['视频链接'].trim():f['视频链接']?.link)},
  human_review:'not_verified',core_task_mapping:'not_configured',provider_requests:0,
  writes_to_task:0,automatic_resubmit:false}}];''')]
    return compile_nodes(nodes)


def inspect_task(api, root, project, record, task, revision=None):
    identifier(project)
    bindings=Path(root)/'bindings'
    config=json.loads((bindings/('project-'+project+'.json')).read_text(encoding='utf-8'))
    if origin(config['origin'])!=api.url: raise Fault('PROJECT_ORIGIN_CHANGED')
    pool=json.loads((bindings/('credentials-'+project+'.json')).read_text(encoding='utf-8'))
    if pool.get('pending'): raise Fault('CREDENTIAL_CREATE_UNKNOWN_RECONCILE_IN_N8N')
    if (pool.get('origin'),pool.get('project'),pool.get('app'))!=(api.url,project,config['app']): raise Fault('PROJECT_CREDENTIAL_SCOPE_CHANGED')
    credential=pool.get('roles',{}).get('feishu')
    if not credential or not credential.get('existing'): raise Fault('FEISHU_EXISTING_CREDENTIAL_REQUIRED')
    # No new resource if an earlier diagnostic create has an unknown outcome.
    for p in bindings.glob('binding-'+project+'-task-check-*.json'):
        if json.loads(p.read_text(encoding='utf-8')).get('pending'): raise Fault('TASK_CHECK_RESOURCE_UNKNOWN_RECONCILE_FIRST')
    template=task_workflow(project,config['base'],config['table'],record,task,revision)
    return verify_setup(api,bindings,project+'-task-check',template,{'httpCustomAuth':credential})
