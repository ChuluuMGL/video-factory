"""Run generated guards against concurrent editor changes, without models."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'skills/video-factory-setup/scripts'))
from n8n_workflows import production_workflow, guard_workflow, recovery_workflow


def source(workflow, name):
    return next(n['parameters']['jsCode'] for n in workflow['nodes'] if n['name'] == name)


class RecoveryEdits(unittest.TestCase):
    def test_version_edits_block_provider_writeback_recovery_and_poll(self):
        ledger = {'table': 'ledgerFixture', 'row': 1}
        guarded = guard_workflow(production_workflow('baseFixture', 'tableFixture', 'script'), ledger)
        recovery = recovery_workflow('baseFixture', 'tableFixture', ledger)
        poll = production_workflow('baseFixture', 'tableFixture', 'video-poll')
        scripts = {
            'reservation': source(guarded, 'Verify reservation'),
            'writeback': source(guarded, 'Verify task before result writeback'),
            'recovery': source(recovery, 'Verify unchanged task'),
            'poll': source(poll, 'Verify video task unchanged'),
        }
        js = r"""
const assert=require('node:assert/strict'),scripts=JSON.parse(process.argv[1]);
const task={id:'recFixture',execution:'42',product:'product',script:'old script',feedback:'',history:'old history',
 references:[],attachments:[],job:''};
let result={fields:{'脚本':'new script','脚本历史':'saved history','状态':'脚本待审核'}};
const original={'执行编号':'42','商品资料':'product','脚本':'old script','审核意见':'','脚本历史':'old history',
 '参考图链接':'','参考图附件':[],'视频任务ID':'','状态':'脚本生成中'};
const run=(name,fields)=>new Function('$json','$',scripts[name])(
 {code:0,data:{record:{record_id:'recFixture',fields}}},
 node=>({first:()=>({json:node==='Select task'?{...task,result}:result})}));
for(const name of ['reservation','writeback','recovery']) {
 assert.ok(run(name,original));
 for(const field of ['脚本历史','视频任务ID']) {
  assert.throws(()=>run(name,{...original,[field]:'human edit'}),/VERSION_CHANGED/);
 }
}
// Either an unapplied PUT or its exact persisted result may be reconciled.
assert.ok(run('recovery',{...original,...result.fields}));
assert.throws(()=>run('recovery',{...original,...result.fields,'脚本历史':'human edit'}),/VERSION_CHANGED/);
assert.throws(()=>run('recovery',{...original,...result.fields,'状态':'脚本通过'}),/REVIEW_STATE_CHANGED/);
result={fields:{'视频任务ID':'provider-job-123','状态':'视频生成中'}};
assert.ok(run('recovery',{...original,'状态':'视频提交中'}));
assert.ok(run('recovery',{...original,...result.fields}));
for(const field of ['脚本历史','视频任务ID']) {
 assert.throws(()=>run('recovery',{...original,...result.fields,[field]:'human edit'}),/VERSION_CHANGED/);
}
task.job='provider-job-123';
const polling={...original,'状态':'视频生成中','视频任务ID':task.job};
result={fields:{'视频链接':{link:'https://media.example.invalid/video.mp4',text:'查看视频'},'状态':'视频待审核'}};
assert.ok(run('poll',polling));
for(const patch of [{'商品资料':'changed'},{'审核意见':'changed'},{'脚本历史':'changed'},
 {'参考图链接':'https://media.example.invalid/changed.png'},
 {'参考图附件':[{file_token:'changed',name:'frame.png',size:4}]}]) {
 assert.throws(()=>run('poll',{...polling,...patch}),/INPUT_CHANGED/);
}
assert.throws(()=>run('poll',{...polling,'视频任务ID':'another-job'}),/TASK_CHANGED/);
assert.ok(run('poll',{...polling,'脚本历史':[{text:'old history'}]}));
"""
        completed = subprocess.run(['node', '-e', js, json.dumps(scripts)], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == '__main__':
    unittest.main()
