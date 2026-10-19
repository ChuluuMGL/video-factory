import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'skills/video-factory-setup/scripts'))
from portable_images import selection, profile, load, save, recover_image_workflow
from portable_n8n import Fault, project_credentials
from n8n_workflows import production_workflow, guard_workflow, setup_hook, recovery_workflow
from portable_approval import plan_workflow, confirmation_workflow
from test_portable import FakeAPI
from test_approval import POLICY

LEDGER={'table':'fixtureLedger','row':1}
def node(w,name):return next(n for n in w['nodes'] if n['name']==name)

class Images(unittest.TestCase):
    def test_image_credentials_are_separate_and_resume_does_not_prompt(self):
        with tempfile.TemporaryDirectory() as d:
            api=FakeAPI();root=Path(d)
            self.assertIsNone(load(root,'p',api.url))
            with self.assertRaisesRegex(Fault,'SELECTION_REQUIRED'):project_credentials(api,root,'p','cli_fixture',['image'],lambda _:'fixture')
            save(root,'p',api.url,selection());asked=[]
            def secret(prompt):asked.append(prompt);return 'SYNTHETIC_IMAGE_KEY'
            first=project_credentials(api,root,'p','cli_fixture',['image'],secret)
            self.assertEqual(first,project_credentials(api,root,'p','cli_fixture',['image'],lambda _:self.fail('repeated')))
            self.assertEqual(len(asked),1)
            for p in root.glob('*.json'):self.assertNotIn('SYNTHETIC_IMAGE_KEY',p.read_text())
            with self.assertRaises(Fault):load(root,'p','https://other.invalid')
            with self.assertRaises(Fault):selection('unknown')

    def test_durable_order_and_zero_provider_recovery(self):
        w=guard_workflow(production_workflow('baseFixture','tableFixture','image',image_selection=selection()),LEDGER)
        names=[n['name'] for n in w['nodes']]
        chain=['Generate image once','Validate image bytes','Persist image before upload','Claim image upload','Upload saved image once','Persist model result','Read task before result writeback','Write image for human review','Read persisted result','Release completed execution']
        self.assertEqual([names.index(n) for n in chain],sorted(names.index(n) for n in chain))
        for n in w['nodes']:
            if n['type']=='n8n-nodes-base.httpRequest':self.assertFalse(n['retryOnFail'])
        with self.assertRaisesRegex(ValueError,'MUST_NOT_CALL_MODELS'):setup_hook(w,'fixture')
        for rec in [recover_image_workflow('baseFixture','tableFixture',LEDGER),recovery_workflow('baseFixture','tableFixture',LEDGER)]:
            self.assertNotIn('Generate image once',[n['name'] for n in rec['nodes']])
        read=node(recover_image_workflow('baseFixture','tableFixture',LEDGER),'Read saved image')
        self.assertIn({'keyName':'status','condition':'eq','keyValue':'image_ready'},read['parameters']['filters']['conditions'])
        zero=confirmation_workflow('baseFixture','tableFixture','image',LEDGER,LEDGER,POLICY,'fixture',image_selection=selection(),verification_only=True)
        self.assertNotIn('Generate image once',[n['name'] for n in zero['nodes']])

    def test_owner_plan_and_image_output_validation(self):
        plan=plan_workflow('baseFixture','tableFixture','image',LEDGER,POLICY,image_selection=selection())
        w=confirmation_workflow('baseFixture','tableFixture','image',LEDGER,LEDGER,POLICY,'fixture',image_selection=selection())
        data={name:node(flow,name)['parameters']['jsCode'] for flow,names in [(plan,['Select task']),(w,['Check protected plan','Build image request','Validate image bytes','Verify persisted result'])] for name in names}
        data['profile']=profile(selection())
        js=r"""const assert=require('node:assert/strict'),d=JSON.parse(process.argv[1]);
const fields={'状态':'申请图片计划','商品资料':'fixture product','图片要求':'single product first frame','参考图附件':[{file_token:'fixtureFile',name:'source.png',size:99}]};
const plan=f=>new Function('$json','$execution',d['Select task'])({code:0,data:{items:[{record_id:'recFixture',fields:f}]}},{id:'42'});
const p=plan(fields)[0].json.plan;assert.deepEqual(p.model,d.profile);assert.equal(p.max_submissions,1);
for(const patch of [{'图片要求':''},{'图片要求':'x'.repeat(2001)},{'生成图片':[{file_token:'old'}]},{'图片回执':'old'},{'参考图附件':[]},{'参考图链接':'https://image.invalid/x'},{'参考图附件':[{...fields['参考图附件'][0],size:10485761}]}])assert.throws(()=>plan({...fields,...patch}));
const check=new Function('$json',d['Check protected plan']);for(const key of Object.keys(p.model)){const c=structuredClone(p);c.model[key]='tampered';assert.throws(()=>check({payload:JSON.stringify({plan:c})}),/PROFILE_CHANGED/);}
const task={id:'recFixture',execution:'42',product:'fixture',image_prompt:'first frame',feedback:''};
const refs=name=>({first:()=>({json:name==='Resolve video references'?{model_references:['data:image/png;base64,aGVsbG8=']}:task})});
const body=new Function('$',d['Build image request'])(refs)[0].json;assert.equal(body.sequential_image_generation,'disabled');assert.equal(body.response_format,'b64_json');assert.equal(body.stream,false);
const validate=new Function('$json','$',d['Validate image bytes']);const bytes=Buffer.from('89504e470d0a1a0a0000000049454e44','hex').toString('base64');
const valid={model:d.profile.model,data:[{b64_json:bytes,size:'2048x2048'}]};assert.equal(validate(valid,refs)[0].json.artifact.mime,'image/png');
for(const changed of [{model:'other'}, {data:[]}, {data:[...valid.data,...valid.data]}, {data:[{b64_json:'<secret>',size:'2048x2048'}]}, {data:[{b64_json:bytes,size:'1x1'}]}])assert.throws(()=>validate({...valid,...changed},refs));
const expected={fields:{'生成图片':[{file_token:'saved'}],'状态':'图片待审核'}};
const verify=new Function('$json','$',d['Verify persisted result']);const get=()=>({first:()=>({json:expected})});
verify({code:0,data:{record:{fields:{'生成图片':[{file_token:'saved',name:'image.png'}],'状态':'图片待审核'}}}},get);
assert.throws(()=>verify({code:0,data:{record:{fields:{'生成图片':[{file_token:'other'}],'状态':'图片待审核'}}}},get),/NOT_PERSISTED/);
"""
        result=subprocess.run(['node','-e',js,json.dumps(data)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
