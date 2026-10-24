import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'skills/video-factory-setup/scripts'))
from portable_catalog import catalog_workflow


class Catalog(unittest.TestCase):
    def test_snapshot_and_race_guards_without_provider_credentials(self):
        flow = catalog_workflow('baseFixture', 'tableTasks', 'tableProducts')
        self.assertFalse(flow['active'])
        self.assertNotIn('httpHeaderAuth', json.dumps(flow))
        scripts = {n['name']: n['parameters']['jsCode'] for n in flow['nodes'] if n['type'].endswith('.code')}
        js = r"""const assert=require('node:assert/strict'),s=JSON.parse(process.argv[1]);
const run=(name,json,refs={'Select catalog record':{id:'recTask'}})=>new Function('$json','$',s[name])(json,n=>({first:()=>({json:refs[n]})}));
const fields={'状态':'资料待同步','关联商品':['recSKU'],'商品资料':'old'};
const input=f=>({code:0,data:{record:{record_id:'recTask',fields:f}}});
assert.equal(run('Select catalog record',{code:0,data:{items:[{record_id:'recTask',fields:{'关联商品':{link_record_ids:['recSKU']}}}]}})[0].json.id,'recTask');
const task=run('Select catalog task',input(fields))[0].json;
assert.throws(()=>run('Select catalog task',{code:0,data:{record:{record_id:'recOther',fields}}}),/READ_FAILED/);
assert.equal(task.sku,'recSKU');
assert.equal(run('Select catalog task',input({...fields,'关联商品':[{record_ids:['recSKU'],table_id:'tableProducts',text:'SKU-1'}]}))[0].json.sku,'recSKU');
for(const link of [{record_ids:['recA','recB'],table_id:'tableProducts'},{record_ids:['recSKU'],table_id:'foreignTable'}])assert.throws(()=>run('Select catalog task',input({...fields,'关联商品':[link]})),/CATALOG_/);
for(const patch of [{'状态':'脚本通过'},{'脚本':'existing script'},{'关联商品':[]},{'关联商品':['recA','recB']},{'执行编号':'used'},{'生成计划':'frozen'},{'费用确认编号':'confirmed'}])
 assert.throws(()=>run('Select catalog task',input({...fields,...patch})),/CATALOG_/);
assert.deepEqual(run('Select catalog record',{code:0,data:{items:[]}}),[]);
const product={'SKU':'SKU-1','商品名称':'Fixture product','核心卖点':'Verified fact','资料版本':'v1','参考图':[{file_token:'fixtureToken'}]};
const source=f=>({code:0,data:{record:{record_id:'recSKU',fields:f}}});
const refs={'Select catalog task':task};
const snapshot=run('Build SKU snapshot',source(product),refs)[0].json;
assert.match(snapshot.fields['商品资料'],/Verified fact/);assert.equal(snapshot.fields['状态'],'资料待确认');
assert.deepEqual(snapshot.fields['参考图附件'],[{file_token:'fixtureToken'}]);
assert.throws(()=>run('Build SKU snapshot',source({...product,'核心卖点':''}),refs),/MISSING/);
assert.throws(()=>run('Build SKU snapshot',{code:0,data:{record:{record_id:'recOther',fields:product}}},refs),/READ_FAILED/);
const chosen={'Select catalog task':{...task,fields:{...fields,'参考图附件':[{file_token:'taskChoice'}]}}};
assert.equal(run('Build SKU snapshot',source(product),chosen)[0].json.fields['参考图附件'],undefined);
refs['Build SKU snapshot']=snapshot;
const recheck=f=>({code:0,data:{record:{record_id:'recTask',fields:f}}});
assert.deepEqual(run('Protect existing task',recheck(fields),refs)[0].json,snapshot);
for(const patch of [{'状态':'费用待确认'},{'商品资料':'edited'},{'关联商品':['recOther']},{'参考图链接':'https://example.invalid/new.jpg'},{'执行编号':'another'}])
 assert.throws(()=>run('Protect existing task',recheck({...fields,...patch}),refs),/CHANGED_NO_WRITE/);
const saved=recheck({...snapshot.fields,'脚本':'untouched'});
assert.equal(run('Verify SKU snapshot',saved,refs)[0].json.provider_requests,0);
assert.throws(()=>run('Verify SKU snapshot',recheck({...snapshot.fields,'商品资料':'wrong'}),refs),/NOT_PERSISTED/);
"""
        result = subprocess.run(['node', '-e', js, json.dumps(scripts)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
