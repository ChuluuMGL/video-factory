"""Private administrator wizard for script key and project n8n scheduling."""
import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile

from .onboarding import SessionStore
from .runtime_store import RuntimeFault,canonical
from .stack import Stack,write_json
from .setup_admin import rpc
from .setup_run import choice
from .dispatch import template
from .workspace import status as workspace_status
from .runner import status as runner_status


def register(commands):
    p=commands.add_parser('production-setup',help='private script credential and n8n dispatch wizard')
    p.add_argument('--stack-root',type=Path,required=True)
    p.add_argument('--session',type=Path,required=True)
    p.add_argument('--browser-input',action='store_true')
    p.add_argument('--video-assets',type=Path,help='Agent-prepared non-secret SKU asset mapping')
    p.add_argument('--input-port',type=int,default=8792)


def import_json(stack,value,name,kind):
    # Secret-bearing files exist only in n8n's private temporary directory.
    # stdin avoids Docker command/environment logs; cleanup runs on every path.
    path='/tmp/vf-'+name+'.json'
    write="const fs=require('fs');let s='';process.stdin.on('data',x=>s+=x);process.stdin.on('end',()=>fs.writeFileSync(process.argv[1],s,{mode:0o600,flag:'wx'}));"
    try:
        stack.compose('exec','-T','n8n','node','-e',write,path,data=canonical(value).encode())
        stack.compose('exec','-T','n8n','n8n','import:'+kind,'--input='+path)
    finally:stack.compose('exec','-T','n8n','node','-e',"require('fs').rmSync(process.argv[1],{force:true})",path)


def schedule(stack,session,token,*,activate=False):
    project=session['configuration']['project']['id']
    base=stack.root/'data/production';base.mkdir(mode=0o700,exist_ok=True)
    path=base/(project+'.json')
    previous=json.loads(path.read_text()) if path.exists() else None
    nonce=secrets.token_hex(12)
    # An interrupted import keeps this identity so resume can replace the same
    # product-owned workflow without creating duplicate schedules.
    receipt=previous or {'project':project,'workflow_id':'Vf'+nonce,'credential_id':'VfKey'+nonce,'status':'pending'}
    write_json(path,receipt)
    cap=rpc(stack,{'action':'execution_key','token':token,'session':session})
    try:
        value=template(project,receipt['credential_id']);value['id']=receipt['workflow_id']
        credentials=[{'id':receipt['credential_id'],'name':value['nodes'][1]['credentials']['httpHeaderAuth']['name'],'type':'httpHeaderAuth',
                      'data':{'name':'Authorization','value':'Bearer '+cap['token']}}]
        import_json(stack,credentials,nonce+'-key','credentials')
        import_json(stack,value,nonce+'-workflow','workflow')
        stack.compose('exec','-T','n8n','n8n','publish:workflow' if activate else 'unpublish:workflow','--id='+receipt['workflow_id'])
        # CLI database changes are not reflected by a running n8n scheduler
        # until restart, including disabling a previously active workflow.
        stack.compose('restart','n8n')
        stack.compose('up','-d','--pull','never','--wait','--wait-timeout','120','n8n','gateway')
        check='/tmp/vf-'+nonce+'-readback.json'
        try:
            stack.compose('exec','-T','n8n','n8n','export:workflow','--id='+receipt['workflow_id'],'--output='+check)
            raw=stack.compose('exec','-T','n8n','node','-e',"process.stdout.write(require('fs').readFileSync(process.argv[1]))",check)
            actual=json.loads(raw)
            if len(actual)!=1 or any(actual[0].get(k)!=value[k] for k in ('id','name','nodes','connections')):
                raise RuntimeFault('N8N_WORKFLOW_READBACK_MISMATCH')
            if bool(actual[0].get('active'))!=activate:raise RuntimeFault('N8N_WORKFLOW_ACTIVATION_MISMATCH')
        finally:stack.compose('exec','-T','n8n','node','-e',"require('fs').rmSync(process.argv[1],{force:true})",check)
        if previous and previous.get('key_id'):
            rpc(stack,{'action':'revoke_execution','token':token,'key_id':previous['key_id']})
        receipt.update(key_id=cap['key_id'],expires_at=cap['expires_at'],status='published_restart_verified' if activate else 'imported_disabled')
        write_json(path,receipt)
        return receipt
    except Exception:
        rpc(stack,{'action':'revoke_execution','token':token,'key_id':cap['key_id']})
        raise


def welcome(args,read=input,hidden=getpass.getpass,write=print):
    stack=Stack(args.stack_root);session=SessionStore(args.session).read()
    write('Video Factory · 项目生产配置')
    write('脚本模型为 DeepSeek Flash；密钥保留在客户服务器。配置不会立即生成内容。')
    project=session['configuration']['project']['id']
    write('当前项目：'+project)
    with stack.lock():
        if not stack.status()['infrastructure_ready']:raise RuntimeFault('PRODUCTION_HEALTHY_STACK_REQUIRED')
        opened=rpc(stack,{'action':'open','session':session,'password':hidden('产品管理员密码: ')})
    token=opened['token']
    try:
        if choice('配置或更换本项目脚本 API Key',read,write):
            secret=hidden('DeepSeek API Key（隐藏输入）: ')
            billing=read('本客户费用账户标签> ').strip()
            if choice('确认将脚本密钥加密保存到本项目',read,write):
                with stack.lock():rpc(stack,{'action':'script','token':token,'session':session,'secret':secret,'billing_owner':billing})
                write('脚本凭据已保存。');secret=None
        if choice('配置或更换本项目视频 API Key 与 SKU 素材',read,write):
            secret=hidden('MiniMax H3 API Key（隐藏输入）: ')
            billing=read('视频费用账户标签> ').strip()
            write('请按这把 Key 所属的 MiniMax 平台选择区域：api.minimax.io 为 global，api.minimax.cn 为 cn；与服务器所在地无关。')
            region=read('视频账户区域 global 或 cn> ').strip()
            write('由 Agent 将已授权的产品图片上传到客户工作目录并计算哈希，再准备每 SKU 素材 JSON。')
            if getattr(args,'video_assets',None):
                from .onboarding import read_input_file
                assets=read_input_file(str(args.video_assets))
                write('Agent 已准备素材配置；请核对 SKU：'+', '.join(sorted(assets)))
            else:assets=json.loads(read('粘贴每 SKU 素材 JSON（不含密钥）> '))
            if choice('确认将视频密钥与 SKU 素材配置保存到本项目',read,write):
                with stack.lock():rpc(stack,{'action':'video','token':token,'session':session,'secret':secret,'billing_owner':billing,'region':region,'assets':assets})
                write('视频配置已保存；具体任务仍需逐条审核和授权。');secret=None
        with stack.lock():status=runner_status(stack,project)
        if status.get('status')!='running':
            # Older customers can retain the existing HTTPS workspace until
            # they deliberately migrate; it also contains an executor.
            with stack.lock():legacy=workspace_status(stack,project)
            if legacy.get('status')=='running':status=legacy
        if status.get('status')!='running':
            write('请先由 Agent 部署本项目的私有 runner，再续接调度配置；不需要公网域名。')
            return {'status':'script_configuration_saved_dispatch_pending','project':project,'business_ready':False}
        write('调度只执行明确授权的脚本请求和已批准的视频任务；每次最多推进一个任务。')
        write('授权有效期 90 天，届满停止执行；再次运行本向导可以续期。恢复备份后旧授权无效。')
        if not choice('配置本项目调度（会短暂重启本客户 n8n）',read,write):
            return {'status':'credentials_saved_schedule_not_changed','project':project}
        activate=choice('配置完成后启用调度（选 n 则保持停用）',read,write)
        with stack.lock():receipt=schedule(stack,session,token,activate=activate)
        write('调度状态：'+receipt['status'])
        return {'status':receipt['status'],'project':project,'expires_at':receipt['expires_at'],
                'executor': 'private_runner' if 'url' not in status else 'legacy_workspace',
                'provider_requests':0,'human_acceptance':'not_run'}
    finally:
        with stack.lock():rpc(stack,{'action':'close','token':token})


def cli(args):
    browser=None
    try:
        if args.browser_input:
            from .setup_browser import BrowserInput
            browser=BrowserInput(args.input_port)
            print(json.dumps({'status':'awaiting_human_input','url':browser.url,'expires_in':1800}),flush=True)
            result=welcome(args,browser.read,browser.hidden,browser.write)
        else:
            if not all(s.isatty() for s in (sys.stdin,sys.stdout,sys.stderr)):raise RuntimeFault('PRIVATE_INPUT_REQUIRED')
            result=welcome(args)
        print(json.dumps(result));return 0
    except (KeyboardInterrupt,EOFError):
        print('{"status":"interrupted","resume_same_command":true}');return 130
    except Exception as error:
        code=str(error) if isinstance(error,RuntimeFault) else 'PRODUCTION_SETUP_INCOMPLETE'
        print(json.dumps({'error':code,'business_ready':False}));return 2
    finally:
        if browser:browser.close()
