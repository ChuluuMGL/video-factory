"""Opt-in Seedream reference images; one paid request, durable image before upload.

Images remain separate from source reference attachments. Human review and a
new video fee plan are required before using one as a video first frame.
"""
import json
from pathlib import Path
from portable_n8n import Fault, identifier, locked, private_write

MODELS = ('doubao-seedream-5-0-lite-260128',)


def selection(model=None):
    model = model or MODELS[0]
    if model not in MODELS: raise Fault('IMAGE_MODEL_UNSUPPORTED')
    return {'provider': 'seedream-cn', 'model': model}


def checked(value):
    if not isinstance(value, dict) or set(value) != {'provider', 'model'} or value['provider'] != 'seedream-cn':
        raise Fault('IMAGE_SELECTION_INVALID')
    return selection(value['model'])


def load(root, project, origin):
    path = Path(root) / ('image-models-' + identifier(project) + '.json')
    if not path.exists(): return None
    saved = json.loads(path.read_text(encoding='utf-8'))
    if saved.get('origin') != origin or saved.get('project') != project: raise Fault('IMAGE_MODEL_SCOPE_CHANGED')
    return checked(saved['image'])


def save(root, project, origin, value):
    value = checked(value)
    with locked(root) as root:
        path = root / ('image-models-' + identifier(project) + '.json')
        if path.exists():
            if load(root, project, origin) != value: raise Fault('IMAGE_MODEL_CHANGE_REQUIRES_NEW_PROJECT')
        else:
            pool = root / ('credentials-' + project + '.json')
            if pool.exists():
                state = json.loads(pool.read_text(encoding='utf-8'))
                if state.get('origin') != origin or state.get('project') != project: raise Fault('IMAGE_CREDENTIAL_SCOPE_CHANGED')
                if 'image' in state.get('roles', {}): raise Fault('IMAGE_KEY_WITHOUT_MODEL_RECONCILE')
            private_write(path, {'origin': origin, 'project': project, 'image': value})
    return value


def profile(value):
    value = checked(value)
    return {'provider': 'Seedream Beijing', 'model': value['model'],
            'endpoint': 'https://ark.cn-beijing.volces.com/api/v3/images/generations',
            'size': '2K', 'response_format': 'b64_json', 'stream': False,
            'sequential_image_generation': 'disabled', 'watermark': True,
            'max_images': 1, 'purpose': 'video_reference', 'max_output_bytes': 10*1024*1024}


IMAGE_FIELDS = {'图片要求': 1, '生成图片': 17, '图片回执': 1}
IMAGE_STATUSES = ['申请图片计划', '请求图片', '图片生成中', '图片待审核', '图片通过', '图片退回']

# These fields are captured in the owner plan and checked again before any POST.
TASK_EXTRA = """image_prompt:text(row.fields['图片要求']),image_receipt:text(row.fields['图片回执']),
image_candidates:(row.fields['生成图片']||[]).map(a=>({file_token:a.file_token,name:a.name,size:a.size})),"""
GUARD_EXTRA = r"""
if(text(fields['图片要求'])!==task.image_prompt || text(fields['图片回执'])!==task.image_receipt ||
 JSON.stringify((fields['生成图片']||[]).map(a=>({file_token:a.file_token,name:a.name,size:a.size})))!==JSON.stringify(task.image_candidates))
 throw new Error('IMAGE_INPUT_CHANGED_RESULT_PRESERVED');
"""


def apply(nodes, configured):
    """Transform the shared reserved/writeback skeleton, retaining its guards."""
    from n8n_workflows import code, http
    by = {n['name']: n for n in nodes}
    by['Select task']['parameters']['jsCode'] = by['Select task']['parameters']['jsCode'].replace('job:text(row.fields', TASK_EXTRA+'job:text(row.fields')
    validate = by['Validate request']
    validate['parameters']['jsCode'] = r"""const task=$('Select task').first().json;
if(!task.product.trim() || !task.image_prompt?.trim() || task.image_prompt.length>2000 || task.product.length>4000 || task.feedback.length>1000) throw new Error('IMAGE_PROMPT_INVALID');
if(task.image_receipt || task.image_candidates.length) throw new Error('EXISTING_IMAGE_CREATE_NEW_TASK');
if(task.references.length || task.attachments.length!==1 || task.attachments.some(a=>! /^[A-Za-z0-9_-]+$/.test(a.file_token||'') || !Number.isInteger(a.size) || a.size<1 || a.size>10*1024*1024 || !/\.(png|jpe?g)$/i.test(a.name||''))) throw new Error('IMAGE_ONE_FEISHU_REFERENCE_REQUIRED');
return $input.all();"""
    by['Verify reservation']['parameters']['jsCode'] += GUARD_EXTRA # inserted before return below
    source = by['Verify reservation']['parameters']['jsCode']
    by['Verify reservation']['parameters']['jsCode'] = source.replace('return [{json:{ok:true}}];', '')+'\nreturn [{json:{ok:true}}];'
    first = next(i for i,n in enumerate(nodes) if n['name']=='Build image request')
    last = next(i for i,n in enumerate(nodes) if n['name']=='Write image for human review')
    nodes[first:last] = [
        code('Build image request', 'const profile='+json.dumps(configured)+';'+r"""
const task=$('Select task').first().json,refs=$('Resolve video references').first().json.model_references;
if(refs.length!==1 || !/^data:image\/(png|jpeg);base64,/.test(refs[0])) throw new Error('IMAGE_REFERENCE_BYTES_INVALID');
return [{json:{model:profile.model,prompt:JSON.stringify({product:task.product,request:task.image_prompt,feedback:task.feedback}),image:refs[0],size:profile.size,response_format:profile.response_format,stream:false,sequential_image_generation:'disabled',watermark:profile.watermark}}];"""),
        http('Generate image once', configured['endpoint'], 'POST', '={{ $json }}', 'httpHeaderAuth'),
        code('Validate image bytes', 'const profile='+json.dumps(configured)+';'+r"""
if($json.error || !Array.isArray($json.data) || $json.data.length!==1 || $json.model!==profile.model) throw new Error('IMAGE_RESPONSE_RECONCILE_NO_RETRY');
const image=$json.data[0],b64=image.b64_json;
if(typeof b64!=='string' || b64.length>Math.ceil(profile.max_output_bytes/3)*4 || !/^[A-Za-z0-9+/]+={0,2}$/.test(b64)) throw new Error('IMAGE_BYTES_INVALID_NO_RETRY');
const bytes=Buffer.from(b64,'base64');
if(!bytes.length || bytes.length>profile.max_output_bytes || bytes.toString('base64')!==b64) throw new Error('IMAGE_BYTES_INVALID_NO_RETRY');
const png=bytes.subarray(0,8).toString('hex')==='89504e470d0a1a0a';
const jpeg=bytes.subarray(0,3).toString('hex')==='ffd8ff' && bytes.subarray(-2).toString('hex')==='ffd9';
if(!png && !jpeg || !/^\d{2,5}x\d{2,5}$/.test(image.size||'')) throw new Error('IMAGE_FORMAT_INVALID_NO_RETRY');
const [w,h]=image.size.split('x').map(Number);
if(w<14 || h<14 || w*h>36000000 || w/h>16 || h/w>16) throw new Error('IMAGE_DIMENSIONS_INVALID_NO_RETRY');
const invalid=()=>{throw new Error('IMAGE_STRUCTURE_INVALID_NO_RETRY');};
let actualW=0,actualH=0;
if(png) {
 let offset=8,header=false,end=false,dataSize=0;
 const crcTable=Array.from({length:256},(_,n)=>{let c=n;for(let j=0;j<8;j++)c=(c&1)?0xedb88320^(c>>>1):c>>>1;return c>>>0;});
 while(offset+12<=bytes.length) {
  const length=bytes.readUInt32BE(offset),type=bytes.subarray(offset+4,offset+8).toString('ascii');
  if(length>bytes.length-offset-12 || !/^[A-Za-z]{4}$/.test(type)) invalid();
  let crc=0xffffffff;for(let j=offset+4;j<offset+8+length;j++)crc=crcTable[(crc^bytes[j])&255]^(crc>>>8);
  if(((crc^0xffffffff)>>>0)!==bytes.readUInt32BE(offset+8+length)) invalid();
  if(!header && type!=='IHDR') invalid();
  if(type==='IHDR') {
   if(header || length!==13 || offset!==8) invalid();header=true;
   actualW=bytes.readUInt32BE(offset+8);actualH=bytes.readUInt32BE(offset+12);
  }
  if(type==='IDAT') dataSize+=length;
  offset+=length+12;
  if(type==='IEND') {if(length || offset!==bytes.length)invalid();end=true;break;}
 }
 if(!header || !end || !dataSize) invalid();
} else {
 let offset=2,scan=false;
 while(offset<bytes.length-2) {
  if(bytes[offset++]!==255) invalid();while(bytes[offset]===255)offset++;
  const marker=bytes[offset++];
  if(!marker || marker===216 || marker===217 || offset+2>bytes.length) invalid();
  const length=bytes.readUInt16BE(offset);if(length<2 || offset+length>bytes.length-2)invalid();
  if([192,193,194,195,197,198,199,201,202,203,205,206,207].includes(marker)) {
   if(length<8 || actualW)invalid();actualH=bytes.readUInt16BE(offset+3);actualW=bytes.readUInt16BE(offset+5);
  }
  if(marker===218) {if(length<6 || !actualW || offset+length>=bytes.length-2)invalid();scan=true;break;}
  offset+=length;
 }
 if(!scan)invalid();
}
if(actualW!==w || actualH!==h) throw new Error('IMAGE_ACTUAL_DIMENSIONS_MISMATCH_NO_RETRY');
const task=$('Select task').first().json;
const usage=$json.usage;
if(usage && (typeof usage!=='object' || JSON.stringify(usage).length>1000)) throw new Error('IMAGE_USAGE_INVALID_RECONCILE');
return [{json:{artifact:{b64,mime:png?'image/png':'image/jpeg',size:bytes.length,name:'vf-'+task.id+'-'+task.execution+(png?'.png':'.jpg')},receipt:{model:profile.model,size:image.size,execution:task.execution,usage:$json.usage||null}}}];""")]


def upload_nodes(base, ledger, feishu_origin):
    from n8n_workflows import code, http, ledger_update
    upload = http('Upload saved image once', feishu_origin+'/open-apis/drive/v1/medias/upload_all', 'POST', feishu=True)
    upload['parameters'].update(sendBody=True, contentType='multipart-form-data', bodyParameters={'parameters':[
        {'name':'file_name','value':'={{ $json.artifact.name }}'},
        {'name':'parent_type','value':'bitable_image'}, {'name':'parent_node','value':base},
        {'name':'size','value':'={{ String($json.artifact.size) }}'},
        {'parameterType':'formBinaryData','name':'file','inputDataFieldName':'image'}]})
    return [
        ledger_update('Claim image upload', ledger, 'image_ready', {'status':'image_uploading'}, owner=True),
        code('Prepare saved image upload',r"""
if($json.owner!==$('Select task').first().json.execution) throw new Error('IMAGE_UPLOAD_NOT_OWNED');
const saved=JSON.parse($json.result),a=saved.artifact;
if(!a || !/^[A-Za-z0-9_.-]+$/.test(a.name) || !['image/png','image/jpeg'].includes(a.mime)) throw new Error('SAVED_IMAGE_INVALID');
const bytes=Buffer.from(a.b64,'base64');
if(bytes.length!==a.size || !a.size || a.size>10*1024*1024 || bytes.toString('base64')!==a.b64) throw new Error('SAVED_IMAGE_INVALID');
return [{json:saved,binary:{image:await this.helpers.prepareBinaryData(bytes,a.name,a.mime)}}];"""),
        upload,
        code('Validate image upload receipt',r"""
if($json.code!==0 || !/^[A-Za-z0-9_-]+$/.test($json.data?.file_token||'')) throw new Error('IMAGE_UPLOAD_UNKNOWN_RECONCILE_NO_RETRY');
const saved=$('Prepare saved image upload').first().json;
return [{json:{fields:{'生成图片':[{file_token:$json.data.file_token}],'图片回执':JSON.stringify(saved.receipt),'状态':'图片待审核'}}}];""")]


def guard_image_nodes(nodes, ledger, base, feishu_origin):
    """Called before the common result ledger and original-row readback."""
    from n8n_workflows import ledger_update
    at = next(i for i,n in enumerate(nodes) if n['name']=='Write image for human review')
    nodes[at:at] = [ledger_update('Persist image before upload', ledger, 'busy',
                     {'status':'image_ready','result':'={{ JSON.stringify($json) }}'}, owner=True),
                   *upload_nodes(base,ledger,feishu_origin)]


def recover_image_workflow(base, table, ledger, *, feishu_origin='https://open.feishu.cn'):
    """Upload a durably saved image only if no upload was attempted yet.

    image_uploading is intentionally excluded: after an unknown POST the caller
    must reconcile its receipt, never guess or re-upload. result_ready uses the
    ordinary zero-model recovery route instead.
    """
    from n8n_workflows import start, http, code, compile_nodes, ledger_update, persisted_fields_check
    from portable_approval import read_approval
    url, nodes = start(base,table,feishu_origin)
    read = read_approval(ledger,'image_ready');read['name']='Read saved image'
    target='={{ '+json.dumps(url+'/')+" + $('Select task').first().json.id }}"
    nodes += [read,code('Select task',r"""
const task=JSON.parse($json.payload);
if(!/^rec[A-Za-z0-9_-]+$/.test(task.id||'') || task.execution!==$json.owner || !task.image_prompt) throw new Error('SAVED_IMAGE_SCOPE_INVALID');
return [{json:task}];"""),http('Read original image task',target,feishu=True),
        code('Check image recovery input',r"""
if($json.code!==0) throw new Error('IMAGE_TASK_UNREADABLE');
const fields=$json.data.record.fields,task=$('Select task').first().json;
const text=v=>Array.isArray(v)?v.map(x=>x.text||'').join(''):String(v||'');
if(fields['状态']!=='图片生成中' || text(fields['执行编号'])!==task.execution) throw new Error('IMAGE_STATE_CHANGED_NO_UPLOAD');
for(const [field,key] of [['商品资料','product'],['脚本','script'],['审核意见','feedback'],['脚本历史','history'],['视频任务ID','job']])
 if(text(fields[field])!==task[key]) throw new Error('IMAGE_INPUT_CHANGED_NO_UPLOAD');
if(JSON.stringify(text(fields['参考图链接']).split(/\s+/).filter(Boolean))!==JSON.stringify(task.references) ||
 JSON.stringify((fields['参考图附件']||[]).map(a=>({file_token:a.file_token,name:a.name,size:a.size})))!==JSON.stringify(task.attachments)) throw new Error('IMAGE_REFERENCE_CHANGED_NO_UPLOAD');
"""+GUARD_EXTRA+"return [{json:{ok:true}}];"),
        *upload_nodes(base,ledger,feishu_origin),
        ledger_update('Persist recovered image receipt',ledger,'image_uploading',{'status':'result_ready','result':'={{ JSON.stringify($json) }}'},owner=True),
        code('Image recovery receipt',"return [{json:{image_uploaded:true,result_saved:true,provider_requests:0,next:'Run recover to restore the saved attachment to the unchanged original row.'}}];")]
    # Upload recovery only persists the receipt; generic recover rechecks the row
    # after the upload and performs exact attachment readback before releasing it.
    return compile_nodes(nodes)
