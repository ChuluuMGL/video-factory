import json
from pathlib import Path
import subprocess
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'skills/video-factory-setup/scripts'))
from portable_task_inspection import task_workflow

class InspectionTests(unittest.TestCase):
    def test_scope_revision_status_and_secret_exclusion(self):
        template=task_workflow('projectFixture','baseFixture','tableFixture','recFixture','TASK_1',2)
        http=[n for n in template['nodes'] if n['type']=='n8n-nodes-base.httpRequest']
        self.assertEqual([n['parameters']['method'] for n in http],['POST','GET'])
        self.assertTrue(http[0]['parameters']['url'].endswith('tenant_access_token/internal'))
        script=next(n['parameters']['jsCode'] for n in template['nodes'] if n['name']=='Task inspection receipt')
        js=r"""
const assert=require('assert');const run=new Function('$json',process.argv[1]);
const row={record_id:'recFixture',fields:{'任务编号':'TASK_1','审核目标版本':2,'状态':'脚本待审核','脚本':'PRIVATE_SCRIPT','视频链接':{link:'https://example.invalid/PRIVATE_VIDEO'},'secret':'PRIVATE_SECRET'}};
const call=r=>run({code:0,data:{record:r}})[0].json;
const result=call(row);assert.equal(result.identity.task,'TASK_1');assert.equal(result.identity.revision,2);
assert.equal(result.status,'脚本待审核');assert.equal(result.result_presence.script,true);assert.equal(result.result_presence.video,true);
assert.equal(result.provider_requests,0);assert.equal(result.writes_to_task,0);
assert(!JSON.stringify(result).includes('PRIVATE_'));assert.equal(result.core_task_mapping,'not_configured');
assert.throws(()=>call({...row,record_id:'recOther'}),/SCOPE_MISMATCH/);
for(const fields of [{'任务编号':'OTHER'},{'审核目标版本':3},{'审核目标版本':''},{'状态':'PRIVATE_ERROR'}])assert.throws(()=>call({...row,fields:{...row.fields,...fields}}));
"""
        process=subprocess.run(['node','-e',js,script],capture_output=True,text=True)
        self.assertEqual(process.returncode,0,process.stderr)

    def test_unknown_revision_is_not_invented(self):
        template=task_workflow('p','b','t','recOne','TASK_1')
        script=next(n['parameters']['jsCode'] for n in template['nodes'] if n['name']=='Task inspection receipt')
        js="const r=new Function('$json',process.argv[1])({code:0,data:{record:{record_id:'recOne',fields:{'任务编号':'TASK_1','状态':'脚本待审核'}}}})[0].json;require('assert').equal(r.identity.revision,null);"
        process=subprocess.run(['node','-e',js,script],capture_output=True,text=True)
        self.assertEqual(process.returncode,0,process.stderr)

if __name__=='__main__':unittest.main()
