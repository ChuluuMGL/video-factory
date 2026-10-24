"""Explicit portable video candidates; fixed regions, no automatic POST retry."""
import json
from pathlib import Path
from portable_n8n import Fault, identifier, locked, private_write

VIDEOS = {
    'minimax-cn': ('MiniMax China', 'https://api.minimax.cn', ('MiniMax-H3',)),
    'seedance-cn': ('Seedance Beijing', 'https://ark.cn-beijing.volces.com', ('doubao-seedance-2-0-260128', 'doubao-seedance-2-5-260628')),
    'seedance-novai': ('Seedance NovAI Enterprise', 'https://xinghezhiyun.com', ('doubao-seedance-2-0-260128', 'doubao-seedance-2-5-260628')),
    'wan-cn': ('Wan Beijing', 'https://dashscope.aliyuncs.com', ('wan2.7-i2v-2026-04-25',)),
    'kling-cn': ('Kling Beijing', 'https://api-beijing.klingai.com', ('kling-3.0', 'kling-3.0-turbo')),
    'kling-global': ('Kling Singapore', 'https://api-singapore.klingai.com', ('kling-3.0', 'kling-3.0-turbo')),
}
ARK_ROUTES = ('seedance-cn', 'seedance-novai')


def selection(provider='minimax-cn', model=None):
    if provider not in VIDEOS: raise Fault('VIDEO_PROVIDER_UNSUPPORTED')
    model = model or VIDEOS[provider][2][0]
    if model not in VIDEOS[provider][2]: raise Fault('VIDEO_MODEL_UNSUPPORTED')
    return {'provider': provider, 'model': model}


def checked(value):
    if not isinstance(value,dict) or set(value)!={'provider','model'}: raise Fault('VIDEO_SELECTION_INVALID')
    return selection(value['provider'],value['model'])


def load(root, project, origin):
    path=Path(root)/('video-models-'+identifier(project)+'.json')
    if not path.exists(): return selection()
    value=json.loads(path.read_text(encoding='utf-8'))
    if value.get('origin')!=origin or value.get('project')!=project: raise Fault('VIDEO_MODEL_SCOPE_CHANGED')
    return checked(value['video'])


def save(root, project, origin, value):
    value=checked(value)
    with locked(root) as root:
        path=root/('video-models-'+identifier(project)+'.json')
        if path.exists():
            if load(root,project,origin)!=value: raise Fault('VIDEO_MODEL_CHANGE_REQUIRES_NEW_PROJECT')
        else:
            credentials=root/('credentials-'+project+'.json')
            if credentials.exists():
                saved=json.loads(credentials.read_text(encoding='utf-8'))
                if saved.get('origin')!=origin or saved.get('project')!=project: raise Fault('VIDEO_CREDENTIAL_SCOPE_CHANGED')
                if 'video' in saved.get('roles',{}) and value!=selection(): raise Fault('EXISTING_VIDEO_KEY_CANNOT_CHANGE_PROVIDER')
            private_write(path,{'origin':origin,'project':project,'video':value})
    return value


def profile(value):
    value=checked(value);provider=value['provider'];label,origin,_=VIDEOS[provider]
    if provider=='minimax-cn':
        return {'provider':label,'model':value['model'],'endpoint':origin+'/v2/video_generation',
                'duration':6,'resolution':'768P','ratio':'9:16'}
    path=('/api/v3/contents/generations/tasks' if provider in ARK_ROUTES else
          '/api/v1/services/aigc/video-generation/video-synthesis' if provider=='wan-cn' else
          '/image-to-video/'+value['model'])
    result={'provider':label,'model':value['model'],'endpoint':origin+path,'duration':5,
            'resolution':'720p' if provider.startswith('kling-') else '720P',
            'ratio':'9:16' if provider in ARK_ROUTES else 'from_first_frame',
            'first_frame_count':1,'audio':'native','watermark':False}
    if provider=='wan-cn':result['prompt_extend']=False
    if provider=='seedance-novai':result['auto_asset']=False
    if provider.startswith('kling-'):result['multi_shot']=False
    return result


def apply(nodes, value, configured_profile, flow, fixture_origin=None):
    """Replace only provider nodes; preserve reservation, ledger and writeback."""
    value=checked(value);route=value['provider']
    if route=='minimax-cn': return
    by_name={node['name']:node for node in nodes}
    prefix='const profile='+json.dumps(configured_profile)+';'
    if flow=='video-submit':
        by_name['Validate references']['parameters']['jsCode']=r"""const count=$json.references.length+$json.attachments.length;
if(!$json.script.trim() || $json.script.length>2500)throw new Error('VIDEO_PROMPT_INVALID');
if(count!==1 || $json.references.length)throw new Error('VIDEO_ONE_FEISHU_FIRST_FRAME_REQUIRED');
if($json.attachments.some(a=>! /^[A-Za-z0-9_-]+$/.test(a.file_token || '') || !Number.isInteger(a.size) || a.size<1 || a.size>20*1024*1024 || !/\.(jpe?g|png)$/i.test(a.name || '')))throw new Error('VIDEO_FIRST_FRAME_INVALID');
return $input.all();"""
        common=prefix+r"""const task=$('Select task').first().json,refs=$('Resolve video references').first().json.model_references;
if(refs.length!==1 || !/^data:image\/(png|jpeg);base64,[A-Za-z0-9+/]+=*$/.test(refs[0]))throw new Error('VIDEO_ONE_VERIFIED_FIRST_FRAME_REQUIRED');
const image=refs[0];"""
        if route in ARK_ROUTES:
            body="{model:profile.model,content:[{type:'text',text:task.script},{type:'image_url',image_url:{url:image}}],duration:profile.duration,resolution:profile.resolution.toLowerCase(),ratio:profile.ratio,generate_audio:true,watermark:profile.watermark}"
            if route=='seedance-novai':body=body[:-1]+",auto_asset:profile.auto_asset}"
            receipt="if($json.error)throw new Error('VIDEO_SUBMISSION_REQUIRES_RECONCILIATION');const id=$json.id;"
        elif route=='wan-cn':
            body="{model:profile.model,input:{prompt:task.script,media:[{type:'first_frame',url:image}]},parameters:{duration:profile.duration,resolution:profile.resolution,prompt_extend:profile.prompt_extend,watermark:profile.watermark}}"
            receipt="if($json.code)throw new Error('VIDEO_SUBMISSION_REQUIRES_RECONCILIATION');const id=$json.output?.task_id;"
            by_name['Submit video once']['parameters'].update(sendHeaders=True,headerParameters={'parameters':[{'name':'X-DashScope-Async','value':'enable'}]})
        else:
            body="{contents:[{type:'prompt',text:task.script},{type:'first_frame',url:image.split(',')[1]}],settings:{resolution:profile.resolution,duration:profile.duration,audio:profile.audio,multi_shot:profile.multi_shot},options:{watermark_info:{enabled:profile.watermark}}}"
            receipt="if($json.code!==0)throw new Error('VIDEO_SUBMISSION_REQUIRES_RECONCILIATION');const id=$json.data?.id;"
        by_name['Build video request']['parameters']['jsCode']=common+'return [{json:'+body+'}];'
        by_name['Validate video receipt']['parameters']['jsCode']=receipt+r"""if(typeof id!=='string' || !/^[A-Za-z0-9][A-Za-z0-9_-]{5,95}$/.test(id))throw new Error('VIDEO_SUBMISSION_REQUIRES_RECONCILIATION');return [{json:{fields:{'视频任务ID':id,'状态':'视频生成中'}}}];"""
    elif flow=='video-poll':
        origin=fixture_origin or VIDEOS[route][1]
        path=('/api/v3/contents/generations/tasks/' if route in ARK_ROUTES else '/api/v1/tasks/' if route=='wan-cn' else '/tasks?task_ids=')
        by_name['Require job receipt']['parameters']['jsCode']=r"""if(!/^[A-Za-z0-9][A-Za-z0-9_-]{5,95}$/.test($json.job))throw new Error('VIDEO_JOB_REQUIRED_NO_RESUBMIT');return $input.all();"""
        by_name['Poll existing video']['parameters']['url']='={{ '+json.dumps(origin+path)+" + $('Select task').first().json.job }}"
        common=prefix+"const expected=$('Select task').first().json.job;let id,state,url;"
        if route in ARK_ROUTES:
            result="if($json.error || $json.model!==profile.model)throw new Error('VIDEO_QUERY_UNVERIFIED');id=$json.id;state=$json.status;url=$json.content?.video_url;"
        elif route=='wan-cn':
            result="if($json.code)throw new Error('VIDEO_QUERY_UNVERIFIED');id=$json.output?.task_id;state=({PENDING:'queued',RUNNING:'running',SUCCEEDED:'succeeded',FAILED:'failed',CANCELED:'cancelled'})[$json.output?.task_status];url=$json.output?.video_url;"
        else:
            result="if($json.code!==0 || !Array.isArray($json.data) || $json.data.length!==1)throw new Error('VIDEO_QUERY_UNVERIFIED');const task=$json.data[0];id=task.id;state=({submitted:'queued',processing:'running',succeeded:'succeeded',failed:'failed'})[task.status];const videos=Array.isArray(task.outputs)?task.outputs.filter(x=>x.type==='video'):[];url=videos.length===1?videos[0].url:null;"
        finish=r"""if(id!==expected)throw new Error('VIDEO_QUERY_UNVERIFIED');
if(['queued','running'].includes(state))return [{json:{video_query:'pending',provider_state:state,automatic_resubmit:false}}];
if(['failed','cancelled','expired'].includes(state))throw new Error('VIDEO_FAILED_REQUIRES_HUMAN_REVIEW');
// Preserve the provider URL only; do not download it with credentials.
if(state!=='succeeded' || typeof url!=='string' || !/^https:\/\/[a-zA-Z0-9.-]+(?::443)?\//.test(url) || /[\s\\#@]/.test(url))throw new Error('VIDEO_DOWNLOAD_UNAVAILABLE');
const host=url.split('/')[2].split(':')[0];
if(!host.includes('.') || /^[\d.]+$/.test(host) || /(^|\.)(localhost|local|internal)$/.test(host))throw new Error('VIDEO_DOWNLOAD_UNAVAILABLE');
return [{json:{fields:{'视频链接':{link:url,text:'查看视频'},'状态':'视频待审核'}}}];"""
        by_name['Check video completion']['parameters']['jsCode']=common+result+finish
