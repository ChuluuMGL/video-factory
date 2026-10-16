"""Portable workflows: standard nodes only, no private Runner/network dependency.

Imports are disabled. Production flows are manually invoked, one row per run.
They do not claim distributed exactly-once guarantees; scheduled activation is
not delivered until overlapping execution/approval-identity gates are verified.
"""
import json
from portable_n8n import identifier


def code(name, source):
    return {'name': name, 'type': 'n8n-nodes-base.code', 'typeVersion': 2,
            'parameters': {'jsCode': source}}


def http(name, url, method='GET', body=None, credential=None, feishu=False):
    parameters = {'url': url, 'method': method, 'options': {'timeout': 60000}}
    if body is not None:
        parameters.update(sendBody=True, specifyBody='json', jsonBody=body)
    if feishu:
        parameters.update(sendHeaders=True, headerParameters={'parameters': [{
            'name': 'Authorization', 'value': "={{ 'Bearer ' + $('Feishu token').first().json.tenant_access_token }}"}]})
    node = {'name': name, 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2,
            'parameters': parameters, 'retryOnFail': False}
    if credential:
        parameters.update(authentication='genericCredentialType', genericAuthType=credential)
        node['credentials'] = {credential: {'id': 'UNBOUND', 'name': 'UNBOUND'}}
    return node


def compile_nodes(nodes):
    for index, node in enumerate(nodes):
        node['id'] = 'vf-' + str(index)
        node['position'] = [index * 240, 0]
    return {'name': 'Video Factory portable', 'nodes': nodes,
            'connections': {left['name']: {'main': [[{'node': right['name'], 'type': 'main', 'index': 0}]]}
                            for left, right in zip(nodes, nodes[1:])},
            'settings': {'executionOrder': 'v1', 'executionTimeout': 240,
                         'saveDataSuccessExecution': 'none', 'saveDataErrorExecution': 'none',
                         'saveManualExecutions': False}, 'active': False}


def start(base, table, feishu_origin='https://open.feishu.cn'):
    identifier(base); identifier(table)
    url = f'{feishu_origin}/open-apis/bitable/v1/apps/{base}/tables/{table}/records'
    return url, [
        {'name': 'Run explicitly', 'type': 'n8n-nodes-base.manualTrigger', 'typeVersion': 1, 'parameters': {}},
        http('Feishu token', feishu_origin + '/open-apis/auth/v3/tenant_access_token/internal',
             'POST', '{}', 'httpCustomAuth'),
        code('Check token', "if ($json.code !== 0 || !$json.tenant_access_token) throw new Error('FEISHU_AUTH_FAILED'); return [{json:{ok:true}}];"),
    ]


def probe_workflow(base, table, *, feishu_origin='https://open.feishu.cn'):
    url, nodes = start(base, table, feishu_origin)
    nodes += [http('Read Base', url + '?page_size=1', feishu=True),
              code('Connection receipt', "if ($json.code !== 0) throw new Error('FEISHU_BASE_ACCESS_DENIED'); return [{json:{base_read:'verified', records_seen:($json.data.items || []).length, provider_requests:0}}];")]
    return compile_nodes(nodes)


FIELDS = {'状态': 3, '商品资料': 1, '脚本': 1, '审核意见': 1, '脚本历史': 1,
          '执行编号': 1, '视频任务ID': 1, '视频链接': 15}
STATUSES = ['请求脚本', '脚本生成中', '脚本待审核', '脚本通过', '脚本退回',
            '视频生成已确认', '视频提交中', '视频生成中', '视频待审核', '视频通过', '视频退回']


def production_workflow(base, table, flow, *, feishu_origin='https://open.feishu.cn',
                        script_origin='https://api.deepseek.com', video_origin='https://api.minimaxi.com'):
    if flow not in ('script', 'video-submit', 'video-poll'):
        raise ValueError('FLOW_INVALID')
    url, nodes = start(base, table, feishu_origin)
    status = {'script': ['请求脚本'], 'video-submit': ['视频生成已确认'], 'video-poll': ['视频生成中']}[flow]
    # Server-side filter avoids silently ignoring matching rows beyond page 1.
    search = {'filter': {'conjunction': 'or', 'conditions': [
        {'field_name': '状态', 'operator': 'is', 'value': [s]} for s in status]}}
    nodes += [http('Find requested task', url + '/search?page_size=1', 'POST', json.dumps(search, ensure_ascii=False), feishu=True),
        code('Select task', """if ($json.code !== 0) throw new Error('FEISHU_SEARCH_FAILED');
const row = ($json.data.items || [])[0]; if (!row) return [];
const text = v => Array.isArray(v) ? v.map(x => x.text || '').join('') : String(v || '');
return [{json:{id:row.record_id, fields:row.fields, product:text(row.fields['商品资料']),
script:text(row.fields['脚本']), feedback:text(row.fields['审核意见']), history:text(row.fields['脚本历史']),
job:text(row.fields['视频任务ID']), execution:String($execution.id)}}];""")]
    target = "={{ " + json.dumps(url + '/') + " + $('Select task').first().json.id }}"
    def write(name, fields):
        return http(name, target, 'PUT', fields, feishu=True)
    if flow != 'video-poll':
        required = 'product' if flow == 'script' else 'script'
        nodes.append(code('Validate request', f"if (!$json.{required}.trim()) throw new Error('TASK_CONTENT_REQUIRED'); return $input.all();"))
        state = '脚本生成中' if flow == 'script' else '视频提交中'
        nodes += [write('Reserve task', '={{ {fields:{"状态":' + json.dumps(state) + ',"执行编号":$("Select task").first().json.execution}} }}'),
                  code('Check reservation', "if ($json.code !== 0) throw new Error('FEISHU_RESERVATION_FAILED'); return [{json:{ok:true}}];"),
                  http('Read reservation', target, feishu=True),
                  code('Verify reservation', """if ($json.code !== 0) throw new Error('FEISHU_RESERVATION_READ_FAILED');
const raw = $json.data.record.fields['执行编号'];
const id = Array.isArray(raw) ? raw.map(x=>x.text||'').join('') : raw;
if (id !== $('Select task').first().json.execution) throw new Error('TASK_CLAIM_CHANGED');
return [{json:{ok:true}}];""")]
    if flow == 'script':
        nodes += [http('Generate script', script_origin + '/chat/completions', 'POST', """={{ {model:'deepseek-chat',stream:false,max_tokens:2000,messages:[{role:'system',content:'根据商品资料编写可审核的短视频脚本，不编造功效。结合旧脚本和修改意见，返回完整新脚本。'},{role:'user',content:JSON.stringify({product:$('Select task').first().json.product,previous:$('Select task').first().json.script,feedback:$('Select task').first().json.feedback})}]} }}""", 'httpHeaderAuth'),
                  code('Validate script', """const script = $json.choices?.[0]?.message?.content;
if (typeof script !== 'string' || !script.trim() || script.length>18000) throw new Error('SCRIPT_RESPONSE_REQUIRES_RECONCILIATION');
const row=$('Select task').first().json;
const history=(row.history+'\n'+JSON.stringify({execution:row.execution,script:row.script,feedback:row.feedback})).trim();
if (history.length>18000) throw new Error('HISTORY_FULL_RECONCILE_BEFORE_RETRY');
return [{json:{fields:{'脚本':script,'脚本历史':history,'状态':'脚本待审核'}}}];"""),
                  write('Write script for human review', '={{ $json }}')]
    elif flow == 'video-submit':
        nodes += [http('Submit video once', video_origin + '/v1/video_generation', 'POST', """={{ {model:'MiniMax-Hailuo-2.3',prompt:$('Select task').first().json.script,duration:6,resolution:'768P'} }}""", 'httpHeaderAuth'),
                  code('Validate video receipt', "if ($json.base_resp?.status_code !== 0 || !$json.task_id) throw new Error('VIDEO_SUBMISSION_REQUIRES_RECONCILIATION'); return [{json:{fields:{'视频任务ID':String($json.task_id),'状态':'视频生成中'}}}];"),
                  write('Save video job receipt', '={{ $json }}')]
    else:
        nodes += [code('Require job receipt', "if (!/^[A-Za-z0-9_-]+$/.test($json.job)) throw new Error('VIDEO_JOB_REQUIRED_NO_RESUBMIT'); return $input.all();"),
                  http('Poll existing video', '={{ ' + json.dumps(video_origin + '/v1/query/video_generation?task_id=') + " + $('Select task').first().json.job }}", credential='httpHeaderAuth'),
                  code('Check video completion', "if ($json.base_resp?.status_code !== 0) throw new Error('VIDEO_QUERY_FAILED'); if ($json.status !== 'Success') return []; if (!$json.file_id) throw new Error('VIDEO_FILE_ID_MISSING'); return [{json:{file_id:String($json.file_id)}}];"),
                  http('Read video download', '={{ ' + json.dumps(video_origin + '/v1/files/retrieve?file_id=') + ' + $json.file_id }}', credential='httpHeaderAuth'),
                  code('Validate video URL', "if ($json.base_resp?.status_code !== 0 || !/^https:\/\//.test($json.file?.download_url || '')) throw new Error('VIDEO_DOWNLOAD_UNAVAILABLE'); return [{json:{fields:{'视频链接':{link:$json.file.download_url,text:'查看视频'},'状态':'视频待审核'}}}];"),
                  write('Write video for human review', '={{ $json }}')]
    nodes.append(code('Writeback receipt', "if ($json.code !== 0) throw new Error('FEISHU_WRITEBACK_FAILED_DO_NOT_RESUBMIT_MODEL'); return [{json:{writeback:'verified',human_review:'in_feishu',automatic_resubmit:false}}];"))
    return compile_nodes(nodes)
