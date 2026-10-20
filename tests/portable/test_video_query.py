"""Query receipts never turn a wait or a GET failure into a second generation."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'skills/video-factory-setup/scripts'))
from n8n_workflows import production_workflow
from portable_video_models import VIDEOS, selection


class VideoQuery(unittest.TestCase):
    def test_pending_and_http_failure_receipts_preserve_jobs(self):
        for provider in VIDEOS:
            with self.subTest(provider=provider):
                flow = production_workflow('baseFixture', 'tableFixture', 'video-poll',
                                           video_selection=selection(provider))
                nodes = {n['name']: n for n in flow['nodes']}
                self.assertFalse(nodes['Poll existing video']['retryOnFail'])
                self.assertNotIn('Submit video once', nodes)
                self.assertNotIn('Video still processing', flow['connections'])
                self.assertEqual(flow['connections']['Video result ready']['main'][1][0]['node'],
                                 'Video still processing')
                data = {'provider': provider, 'model': selection(provider)['model'],
                        'http': nodes['Check video query HTTP']['parameters']['jsCode'],
                        'completion': nodes['Check video completion']['parameters']['jsCode']}
                js = r"""const assert=require('node:assert/strict'),d=JSON.parse(process.argv[1]);
const http=new Function('$json',d.http),complete=new Function('$json','$',d.completion);
const job=d.provider==='minimax-cn'?'123456789012345':'candidate-task-123';
const refs=()=>({first:()=>({json:{job}})});
for(const status of [401,404,429,500,503]) {
  assert.throws(()=>http({statusCode:status,body:'secret provider body'}),e=>
    e.message==='VIDEO_QUERY_HTTP_'+status+'_EXISTING_JOB_ONLY');
}
for(const body of ['not json','null','[]']) assert.throws(()=>http({statusCode:200,body}),/BODY_UNVERIFIED/);
for(const pending of ['queued','running']) {
 let body;
 if(d.provider==='minimax-cn') body={task:{id:job,model:d.model,status:pending}};
 else if(d.provider==='seedance-cn') body={id:job,model:d.model,status:pending};
 else if(d.provider==='wan-cn') body={output:{task_id:job,task_status:pending==='queued'?'PENDING':'RUNNING'}};
 else body={code:0,data:[{id:job,status:pending==='queued'?'submitted':'processing'}]};
 const parsed=http({statusCode:200,body:JSON.stringify(body)})[0].json;
 assert.deepEqual(complete(parsed,refs),[{json:{video_query:'pending',provider_state:pending,automatic_resubmit:false}}]);
 const changed=JSON.parse(JSON.stringify(body).replaceAll(job,'other-task-999'));
 assert.throws(()=>complete(changed,refs),/UNVERIFIED/);
}
if(d.provider==='seedance-cn') assert.throws(()=>complete({id:job,model:d.model,status:'expired'},refs),/HUMAN_REVIEW/);
"""
                result = subprocess.run(['node', '-e', js, json.dumps(data)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
