import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'skills/video-factory-setup/scripts'))
from portable_video_models import VIDEOS, selection, profile, save, load
from portable_n8n import Fault, private_write, project_credentials
from portable_approval import plan_workflow, confirmation_workflow
from n8n_workflows import production_workflow
from test_portable import FakeAPI
from test_approval import POLICY

def node(flow,name):return next(n for n in flow['nodes'] if n['name']==name)

class VideoModels(unittest.TestCase):
    def test_keys_are_bound_to_provider_and_resume_never_prompts_again(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);api=FakeAPI();save(root,'p',api.url,selection('wan-cn'))
            prompts=[]
            def secret(prompt):prompts.append(prompt);return 'SYNTHETIC_KEY_ONLY'
            first=project_credentials(api,root,'p','cli_fixture',['video'],secret)
            self.assertEqual(project_credentials(api,root,'p','cli_fixture',['video'],lambda _:self.fail('repeat')),first)
            self.assertIn('Wan',prompts[0]);self.assertEqual(len(prompts),1)
            with self.assertRaisesRegex(Fault,'NEW_PROJECT'):save(root,'p',api.url,selection('seedance-cn'))
            for p in root.glob('*.json'):self.assertNotIn('SYNTHETIC_KEY_ONLY',p.read_text(encoding='utf-8'))
            value=json.loads((root/'video-models-p.json').read_text(encoding='utf-8'));value['video']=selection('kling-cn');private_write(root/'video-models-p.json',value)
            with self.assertRaisesRegex(Fault,'MODEL_MISMATCH'):project_credentials(api,root,'p','cli_fixture',['video'],secret)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);private_write(root/'credentials-p.json',{'origin':api.url,'project':'p','roles':{'video':{}}})
            with self.assertRaisesRegex(Fault,'CANNOT_CHANGE'):save(root,'p',api.url,selection('wan-cn'))

    def test_profiles_require_known_region_and_model(self):
        with self.assertRaises(Fault):selection('other')
        with self.assertRaises(Fault):selection('kling-cn','https://other.invalid')
        self.assertNotEqual(profile(selection('kling-cn'))['endpoint'],profile(selection('kling-global'))['endpoint'])

    def test_fee_profiles_receipts_and_polling_fail_closed(self):
        for provider, model in [(p,m) for p,(_,_,models) in VIDEOS.items() if p!='minimax-cn' for m in models]:
            with self.subTest(provider=provider, model=model):
                chosen=selection(provider,model);expected=profile(chosen)
                plan=plan_workflow('baseFixture','tableFixture','video-submit',{'table':'a','row':1},POLICY,video_selection=chosen)
                confirm=confirmation_workflow('baseFixture','tableFixture','video-submit',{'table':'e','row':1},{'table':'a','row':1},POLICY,'confirm',video_selection=chosen)
                poll=production_workflow('baseFixture','tableFixture','video-poll',video_selection=chosen)
                self.assertEqual(node(confirm,'Submit video once')['parameters']['url'],expected['endpoint'])
                self.assertFalse(node(confirm,'Submit video once')['retryOnFail'])
                data={'provider':provider,'profile':expected,'plan':node(plan,'Select task')['parameters']['jsCode'],
                      'check':node(confirm,'Check protected plan')['parameters']['jsCode'],
                      'body':node(confirm,'Build video request')['parameters']['jsCode'],
                      'receipt':node(confirm,'Validate video receipt')['parameters']['jsCode'],
                      'poll':node(poll,'Check video completion')['parameters']['jsCode']}
                js=r"""const assert=require('node:assert/strict'),d=JSON.parse(process.argv[1]);
const fields={'商品资料':'fixture','脚本':'approved scene','状态':'申请视频计划','参考图附件':[{file_token:'fixtureToken',name:'first.png',size:42}]};
const prepare=f=>new Function('$json','$execution',d.plan)({code:0,data:{items:[{record_id:'recFixture',fields:f}]}},{id:'42'});
const plan=prepare(fields)[0].json.plan;
for(const patch of [
 {'参考图附件':[]}, {'参考图附件':[...fields['参考图附件'],...fields['参考图附件']]},
 {'参考图链接':'https://media.example.invalid/image.png'}, {'脚本':'x'.repeat(2501)},
 ...[{name:'first.svg'}, {size:0}, {size:20*1024*1024+1}, {file_token:'bad/token'}].map(change=>({'参考图附件':[{...fields['参考图附件'][0],...change}]}))
])assert.throws(()=>prepare({...fields,...patch}),/VIDEO_/);
assert.equal(prepare({...fields,'脚本':'x'.repeat(2500)})[0].json.plan.max_submissions,1);
assert.deepEqual(plan.model,d.profile);const check=new Function('$json',d.check);
for(const key of Object.keys(d.profile)){const v=structuredClone(plan);v.model[key]='changed';assert.throws(()=>check({payload:JSON.stringify({plan:v})}),/PROFILE_CHANGED/);}
const refs=name=>({first:()=>({json:name==='Resolve video references'?{model_references:['data:image/png;base64,aGVsbG8=']}:{script:'approved scene',job:'candidate-task-123'}})});
const body=new Function('$',d.body)(refs)[0].json;assert.equal(body.duration||body.parameters?.duration||body.settings?.duration,5);
const receipt=new Function('$json',d.receipt),poll=new Function('$json','$',d.poll);
let submitted,result;
if(d.provider==='seedance-cn'){assert.equal(body.model,d.profile.model);assert.equal(body.resolution,'720p');assert.equal(body.generate_audio,true);submitted={id:'candidate-task-123'};result={id:'candidate-task-123',model:d.profile.model,status:'succeeded',content:{video_url:'https://media.example.invalid/video.mp4'}};}
else if(d.provider==='wan-cn'){submitted={output:{task_id:'candidate-task-123'}};result={output:{task_id:'candidate-task-123',task_status:'SUCCEEDED',video_url:'https://media.example.invalid/video.mp4'}};}
else{submitted={code:0,data:{id:'candidate-task-123'}};result={code:0,data:[{id:'candidate-task-123',status:'succeeded',outputs:[{type:'video',url:'https://media.example.invalid/video.mp4'}]}]};}
assert.equal(receipt(submitted)[0].json.fields['视频任务ID'],'candidate-task-123');assert.throws(()=>receipt({}));
assert.equal(poll(result,refs)[0].json.fields['状态'],'视频待审核');
if(d.provider==='seedance-cn'){assert.throws(()=>poll({...result,model:result.model==='doubao-seedance-2-0-260128'?'doubao-seedance-2-5-260628':'doubao-seedance-2-0-260128'},refs),/UNVERIFIED/);}
const bad=JSON.parse(JSON.stringify(result).replaceAll('candidate-task-123','other-task-999'));assert.throws(()=>poll(bad,refs),/UNVERIFIED/);
for(const url of ['http://media.example.invalid/x','https://127.0.0.1/x','https://user@media.example.invalid/x','https://host.local/x']){const v=JSON.parse(JSON.stringify(result).replaceAll('https://media.example.invalid/video.mp4',url));assert.throws(()=>poll(v,refs));}
"""
                result=subprocess.run(['node','-e',js,json.dumps(data)],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
