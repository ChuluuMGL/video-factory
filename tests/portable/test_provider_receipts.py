"""Bounded evidence must survive HTTP failures without enabling paid retries."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'skills/video-factory-setup/scripts'))
from n8n_workflows import production_workflow, guard_workflow
from portable_images import selection


class ProviderReceipts(unittest.TestCase):
    def test_all_guarded_providers_record_before_validation(self):
        for flow, provider, validator in [
            ('script', 'Generate script', 'Validate script'),
            ('video-submit', 'Submit video once', 'Validate video receipt'),
            ('image', 'Generate image once', 'Validate image bytes'),
        ]:
            extra = {'image_selection': selection()} if flow == 'image' else {}
            w = guard_workflow(production_workflow('baseFixture', 'tableFixture', flow, **extra),
                               {'table':'ledgerFixture','row':1})
            names = [n['name'] for n in w['nodes']]
            chain = [provider, 'Build provider HTTP receipt', 'Persist provider HTTP receipt',
                     'Check provider HTTP receipt', validator]
            self.assertEqual([names.index(n) for n in chain], sorted(names.index(n) for n in chain))
            p = next(n for n in w['nodes'] if n['name'] == provider)
            self.assertFalse(p['retryOnFail'])
            self.assertTrue(p['parameters']['options']['response']['response']['neverError'])
            self.assertEqual(w['settings']['saveDataErrorExecution'], 'none')

    def test_only_bounded_metadata_is_persisted_and_errors_never_release(self):
        w = guard_workflow(production_workflow('baseFixture','tableFixture','script'),
                           {'table':'ledgerFixture','row':1})
        sources = {n['name']:n['parameters']['jsCode'] for n in w['nodes'] if n['type']=='n8n-nodes-base.code'}
        js = r"""
const assert=require('node:assert/strict'),src=JSON.parse(process.argv[1]);
const task={execution:'42'},response={statusCode:200,body:{choices:[{message:{content:'ok'}}]}};
const lookup=n=>({first:()=>({json:n==='Select task'?task:response})});
const summarize=new Function('$json','$',src['Build provider HTTP receipt']);
const check=new Function('$json','$',src['Check provider HTTP receipt']);
const get=r=>summarize(r,lookup)[0].json;
const wrap=r=>({owner:'42',result:JSON.stringify(get(r))});
assert.deepEqual(check(wrap(response),lookup),[{json:response.body}]);
for(const status of [401,403,404,429,500,503]) {
 const r={statusCode:status,body:{error:{code:'Model.NotFound',message:'SECRET_MESSAGE'},authorization:'SECRET_KEY'},headers:{cookie:'SECRET_COOKIE'}};
 const saved=wrap(r);assert.equal(get(r).provider_http_receipt.provider_code,'Model.NotFound');
 assert.ok(!saved.result.includes('SECRET'));
 assert.throws(()=>check(saved,lookup),new RegExp('PROVIDER_HTTP_'+status+'_NO_RESUBMIT'));
}
for(const status of [null,undefined,0,600,'200',200.5]) {
 const saved=wrap({statusCode:status,body:{error:{code:'x'.repeat(1000)},id:'https://private.invalid/secret'}});
 const receipt=JSON.parse(saved.result).provider_http_receipt;
 assert.equal(receipt.http_status,null);assert.equal(receipt.provider_code,null);assert.equal(receipt.job_id,null);
 assert.throws(()=>check(saved,lookup),/UNKNOWN_NO_RESUBMIT/);
}
assert.throws(()=>check({...wrap(response),owner:'other'},lookup),/NOT_OWNED/);
assert.throws(()=>check({owner:'42',result:'{}'},lookup),/UNVERIFIED/);
assert.equal(get({statusCode:200,body:{data:{id:'job-12345'},code:0}}).provider_http_receipt.job_id,'job-12345');
const image=get({statusCode:200,body:{model:'approved-model',data:[{b64_json:'SECRET_IMAGE'}]}});
assert.equal(image.provider_http_receipt.response_model,'approved-model');
assert.equal(image.provider_http_receipt.output_count,1);assert.ok(!JSON.stringify(image).includes('SECRET_IMAGE'));
"""
        result = subprocess.run(['node','-e',js,json.dumps(sources)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)


if __name__ == '__main__':
    unittest.main()
