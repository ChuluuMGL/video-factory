import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'skills/video-factory-setup/scripts'))
from portable_models import SCRIPTS, selection, profile, save, load
from portable_n8n import Fault, private_write, project_credentials
from portable_approval import plan_workflow, confirmation_workflow
from test_portable import FakeAPI
from test_approval import POLICY


def node(workflow, name):
    return next(n for n in workflow['nodes'] if n['name']==name)


class Models(unittest.TestCase):
    def test_profile_endpoint_and_model_are_not_arbitrary_urls(self):
        for provider in SCRIPTS:
            p=profile(selection(provider))
            self.assertTrue(p['endpoint'].startswith('https://'))
            self.assertFalse(p['stream'])
            self.assertIn(p.get('max_tokens',p.get('max_completion_tokens')),(2048,4096))
        for model in ('https://other.invalid/model','x\nAuthorization: value','','../model'):
            with self.assertRaises(Fault):selection('deepseek',model)
        with self.assertRaises(Fault):selection('custom')
        with self.assertRaises(Fault):profile({'provider':'deepseek','model':'flash','endpoint':'https://other.invalid'})

    def test_resume_binds_origin_and_never_repurposes_legacy_key(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);api=FakeAPI()
            private_write(root/'credentials-p.json',{'origin':api.url,'project':'p','roles':{'script':{'existing':{'id':'old'}}}})
            with self.assertRaisesRegex(Fault,'EXISTING_SCRIPT_KEY'):save(root,'p',api.url,selection('qwen-cn'))
            save(root,'p',api.url,selection())
            self.assertEqual(load(root,'p',api.url),selection())
            with self.assertRaisesRegex(Fault,'SCOPE_CHANGED'):load(root,'p','https://other.invalid')
            with self.assertRaisesRegex(Fault,'MODEL_CHANGE'):save(root,'p',api.url,selection('gemini'))

    def test_selected_key_is_prompted_once_and_not_written_locally(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);api=FakeAPI();save(root,'p',api.url,selection('qwen-cn'))
            prompts=[]
            def secret(prompt):prompts.append(prompt);return 'SYNTHETIC_KEY_ONLY'
            first=project_credentials(api,root,'p','cli_fixture',['script'],secret)
            self.assertIn('Qwen',prompts[0])
            self.assertEqual(project_credentials(api,root,'p','cli_fixture',['script'],lambda _:self.fail('repeat')),first)
            self.assertEqual(len(prompts),1)
            for p in root.glob('*.json'):self.assertNotIn('SYNTHETIC_KEY_ONLY',p.read_text(encoding='utf-8'))
            metadata=root/'models-p.json';v=json.loads(metadata.read_text(encoding='utf-8'));v['script']=selection('gemini');private_write(metadata,v)
            with self.assertRaisesRegex(Fault,'CREDENTIAL_MODEL_MISMATCH'):
                project_credentials(api,root,'p','cli_fixture',['script'],lambda _:self.fail('must stop'))

    def test_each_confirmed_profile_executes_exact_options_and_rejects_cross_provider_plan(self):
        for provider in SCRIPTS:
            with self.subTest(provider=provider):
                selected=selection(provider);expected=profile(selected)
                plan=plan_workflow('baseFixture','tableFixture','script',{'table':'a','row':1},POLICY,script_selection=selected)
                confirm=confirmation_workflow('baseFixture','tableFixture','script',{'table':'e','row':1},{'table':'a','row':1},POLICY,'confirm',script_selection=selected)
                self.assertEqual(node(confirm,'Generate script')['parameters']['url'],expected['endpoint'])
                self.assertFalse(node(confirm,'Generate script')['retryOnFail'])
                data={'profile':expected,'plan':node(plan,'Select task')['parameters']['jsCode'],
                      'check':node(confirm,'Check protected plan')['parameters']['jsCode'],
                      'body':node(confirm,'Build script request')['parameters']['jsCode'],
                      'validate':node(confirm,'Validate script')['parameters']['jsCode']}
                js=r"""const assert=require('node:assert/strict'),d=JSON.parse(process.argv[1]);
const plan=new Function('$json','$execution',d.plan)({code:0,data:{items:[{record_id:'recFixture',fields:{'商品资料':'fixture','状态':'申请脚本计划'}}]}},{id:'42'})[0].json.plan;
assert.deepEqual(plan.model,d.profile);
const check=new Function('$json',d.check);
assert.equal(check({payload:JSON.stringify({plan})})[0].json.plan.flow,'script');
for(const key of Object.keys(d.profile)) {const changed=structuredClone(plan);changed.model[key]='different';assert.throws(()=>check({payload:JSON.stringify({plan:changed})}),/APPROVAL_MODEL_PROFILE_CHANGED_REPLAN/);}
const refs=()=>({first:()=>({json:{product:'fixture',script:'old',feedback:'modify',history:'',execution:'42'}})});
const body=new Function('$',d.body)(refs)[0].json;
const {provider,endpoint,...options}=d.profile;const {messages,...sent}=body;assert.deepEqual(sent,options);assert.equal(messages.length,2);
const validate=new Function('$json','$',d.validate);
assert.equal(validate({choices:[{finish_reason:'stop',message:{content:'new script'}}]},refs)[0].json.fields['脚本'],'new script');
for(const [reason,content] of [['length','partial'],['stop','<think>private reasoning</think>']])assert.throws(()=>validate({choices:[{finish_reason:reason,message:{content}}]},refs));
"""
                result=subprocess.run(['node','-e',js,json.dumps(data)],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
