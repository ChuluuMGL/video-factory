"""Explicit SKU-to-task snapshots. No models, approvals or live-task rewrites."""
import json
from portable_n8n import identifier
from n8n_workflows import start, http, code, compile_nodes

CATALOG_FIELDS = [
    {'field_name': name, 'type': 1} for name in
    ('SKU', '商品名称', '规格', '核心卖点', '使用场景', '禁用表述', '资料版本')
] + [{'field_name': '参考图', 'type': 17}]
CATALOG_STATUSES = ['资料待同步', '资料待确认']


def catalog_workflow(base, table, catalog, *, feishu_origin='https://open.feishu.cn'):
    """Copy one explicitly requested SKU snapshot; never change an approved task.

    This is deliberately a separate zero-provider action. Later fee plans freeze
    the copied product text and task attachments, not a mutable product lookup.
    """
    identifier(catalog)
    url, nodes = start(base, table, feishu_origin)
    search = {'filter': {'conjunction': 'and', 'conditions': [
        {'field_name': '状态', 'operator': 'is', 'value': ['资料待同步']}]}}
    nodes += [
        http('Find catalog request', url+'/search?page_size=1', 'POST', json.dumps(search, ensure_ascii=False), feishu=True),
        code('Select catalog record', "if($json.code!==0)throw new Error('CATALOG_TASK_READ_FAILED');const row=$json.data.items?.[0];if(!row)return [];if(!/^rec[A-Za-z0-9]+$/.test(row.record_id||''))throw new Error('CATALOG_TASK_ID_INVALID');return [{json:{id:row.record_id}}];"),
        # Search and single-record endpoints return different field shapes.
        # Read both snapshots through the same endpoint before comparing them.
        http('Read catalog task', '={{ '+json.dumps(url+'/')+" + $json.id }}", feishu=True),
        code('Select catalog task', 'const catalog='+json.dumps(catalog)+';'+r"""if($json.code!==0)throw new Error('CATALOG_TASK_READ_FAILED');
const row=$json.data.record;if(!row || row.record_id!==$('Select catalog record').first().json.id)throw new Error('CATALOG_TASK_READ_FAILED');
const f=row.fields,text=v=>Array.isArray(v)?v.map(x=>x.text||'').join(''):String(v||'');
if(f['状态']!=='资料待同步')throw new Error('CATALOG_STATUS_CHANGED');
if(['脚本','执行编号','视频任务ID','生成计划','费用确认编号'].some(k=>text(f[k]).trim()))throw new Error('CATALOG_TASK_ALREADY_PLANNED_USE_NEW_TASK');
const links=f['关联商品'];if(!Array.isArray(links)||links.length!==1)throw new Error('CATALOG_SELECT_ONE_SKU');
const link=links[0];
if(link?.table_id && link.table_id!==catalog)throw new Error('CATALOG_LINK_SCOPE_CHANGED');
if(link?.record_ids && (!Array.isArray(link.record_ids)||link.record_ids.length!==1))throw new Error('CATALOG_SELECT_ONE_SKU');
const sku=typeof link==='string'?link:link?.record_ids?.[0]||link?.record_id||link?.id;
if(!/^rec[A-Za-z0-9]+$/.test(sku||''))throw new Error('CATALOG_SKU_ID_INVALID');
return [{json:{id:row.record_id,sku,fields:f}}];"""),
        http('Read selected SKU', '={{ '+json.dumps(feishu_origin+'/open-apis/bitable/v1/apps/'+base+'/tables/'+catalog+'/records/')+" + $('Select catalog task').first().json.sku }}", feishu=True),
        code('Build SKU snapshot', r"""const task=$('Select catalog task').first().json;
if($json.code!==0 || $json.data.record?.record_id!==task.sku)throw new Error('CATALOG_SKU_READ_FAILED');
const f=$json.data.record.fields,text=v=>Array.isArray(v)?v.map(x=>x.text||'').join(''):String(v||'');
for(const key of ['SKU','商品名称','核心卖点','资料版本'])if(!text(f[key]).trim())throw new Error('CATALOG_REQUIRED_PRODUCT_DATA_MISSING');
const lines=['SKU','商品名称','规格','核心卖点','使用场景','禁用表述','资料版本'].map(k=>k+'：'+text(f[k]));
lines.push('来源商品记录：'+task.sku);
const product=lines.join('\n');if(product.length>20000)throw new Error('CATALOG_PRODUCT_TOO_LONG');
const attachments=f['参考图']||[];
if(!Array.isArray(attachments)||attachments.length>9 || attachments.some(a=>!/^[-A-Za-z0-9_]+$/.test(a.file_token||'')))throw new Error('CATALOG_ATTACHMENTS_INVALID');
// Leave task-specific reference choices untouched; only fill an empty field.
const fields={'商品资料':product,'SKU快照':text(f['SKU']),'商品资料版本':text(f['资料版本']),'状态':'资料待确认'};
if(!(task.fields['参考图附件']||[]).length && attachments.length)fields['参考图附件']=attachments.map(a=>({file_token:a.file_token}));
return [{json:{fields}}];"""),
        http('Recheck catalog task', '={{ '+json.dumps(url+'/')+" + $('Select catalog task').first().json.id }}", feishu=True),
        code('Protect existing task', r"""const task=$('Select catalog task').first().json;
if($json.code!==0||$json.data.record?.record_id!==task.id)throw new Error('CATALOG_TASK_RECHECK_FAILED');
const f=$json.data.record.fields,old=task.fields;
for(const k of ['状态','关联商品','商品资料','脚本','审核意见','参考图附件','参考图链接','执行编号','视频任务ID','生成计划','费用确认编号','SKU快照','商品资料版本'])
 if(JSON.stringify(f[k]??null)!==JSON.stringify(old[k]??null))throw new Error('CATALOG_TASK_CHANGED_NO_WRITE');
return [{json:$('Build SKU snapshot').first().json}];"""),
        http('Save SKU snapshot', '={{ '+json.dumps(url+'/')+" + $('Select catalog task').first().json.id }}", 'PUT', '={{ $json }}', feishu=True),
        code('Check snapshot save', "if($json.code!==0)throw new Error('CATALOG_WRITE_FAILED');return $input.all();"),
        http('Read saved snapshot', '={{ '+json.dumps(url+'/')+" + $('Select catalog task').first().json.id }}", feishu=True),
        code('Verify SKU snapshot', r"""if($json.code!==0)throw new Error('CATALOG_READBACK_FAILED');
const expected=$('Build SKU snapshot').first().json.fields,actual=$json.data.record.fields;
const text=v=>Array.isArray(v)?v.map(x=>x.text||'').join(''):String(v||'');
for(const k of ['商品资料','SKU快照','商品资料版本','状态'])if(text(actual[k])!==expected[k])throw new Error('CATALOG_SNAPSHOT_NOT_PERSISTED');
if(expected['参考图附件'] && JSON.stringify((actual['参考图附件']||[]).map(a=>a.file_token))!==JSON.stringify(expected['参考图附件'].map(a=>a.file_token)))throw new Error('CATALOG_ATTACHMENTS_NOT_PERSISTED');
return [{json:{base_read:'verified',sku_snapshot:'verified',provider_requests:0,next:'请在飞书核对资料后再申请脚本计划'}}];""")]
    return compile_nodes(nodes)
