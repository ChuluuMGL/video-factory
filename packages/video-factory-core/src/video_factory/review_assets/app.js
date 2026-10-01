'use strict';
const $ = id => document.getElementById(id);
let scriptRevision = 0;
let csrf = '', current = null, pending = null, next = null, polling = null, expiry = null;
const stateNames = {script_queued:'脚本生成已排队',script_submission_unknown:'脚本生成结果待核对，禁止自动重发',awaiting_script_review:'等待脚本审核', ready:'脚本已通过，等待生成', submitted:'生成中', submission_unknown:'生成状态待核对', awaiting_video_review:'等待视频审核', rejected:'已退回，待修订', accepted:'审核通过', failed:'执行失败，待处理'};
function message(text) { $('message').textContent = text; }
function cancelPlan() { pending = null; $('confirmation').hidden = true; }
async function request(path, body) {
  const options = {credentials:'same-origin', cache:'no-store'};
  if (body !== undefined) Object.assign(options, {method:'POST',headers:{'Content-Type':'application/json','X-VF-CSRF':csrf},body:JSON.stringify(body)});
  const response = await fetch(path, options), result = await response.json();
  if (!response.ok) throw new Error(result.error || '请求失败，请重新核对');
  return result;
}
async function act(button, work) {
  button.disabled = true;
  try { await work(); } catch (error) { message('未完成：'+error.message); }
  finally { button.disabled = false; }
}
async function boot() {
  const result = await request('/api/session'); csrf = result.csrf;
  clearTimeout(expiry);
  if (!result.persistent) expiry=setTimeout(()=>{ clearTimeout(polling); cancelPlan(); current=null; $('workspace').hidden=true; $('login').hidden=true; $('video').pause(); $('video').removeAttribute('src'); $('video').load(); $('script').textContent=''; $('history').textContent=''; $('tasks').replaceChildren(); message('本次审核入口已到期，请联系管理员重新开启。'); },result.remaining_seconds*1000);
  $('project').textContent = '项目：'+result.project+' · 应用：'+result.app_id+(result.persistent?' · 员工工作区':' · 本次剩余约 '+Math.ceil(result.remaining_seconds/60)+' 分钟');
  $('login').hidden = result.authenticated; $('workspace').hidden = !result.authenticated;
  if (result.authenticated) await tasks();
}
async function poll() {
  try {
    const result = await request('/api/poll', {});
    if (result.status === 'authorized') { message('身份已验证：'+result.identity.open_id); $('authorization').hidden=true; await boot(); }
    else polling = setTimeout(poll, result.retry_after*1000);
  } catch (error) { message('授权未完成：'+error.message); $('authorization').hidden=true; }
}
$('login-button').onclick = () => act($('login-button'), async () => {
  clearTimeout(polling); cancelPlan();
  const result = await request('/api/login', {});
  $('user-code').textContent=result.user_code; $('authorization-link').href=result.verification_uri;
  $('authorization').hidden=false; message('请在飞书完成本人的授权。');
  polling=setTimeout(poll,result.retry_after*1000);
});
async function tasks(after='') {
  const result = await request('/api/tasks'+(after?'?after='+encodeURIComponent(after):''));
  $('import-form').closest('details').hidden=!result.can_import;
  $('create-script').hidden=!result.can_create_script;
  if(!after){$('script-sku').replaceChildren();for(const sku of result.skus || []){const option=document.createElement('option');option.value=sku.sku_id;option.textContent=sku.sku_id+' · '+sku.name;$('script-sku').append(option);}}
  if (!after) $('tasks').replaceChildren();
  for (const task of result.items) {
    const button=document.createElement('button');
    button.textContent=task.id+' · 第 '+task.revision+' 版 · '+(stateNames[task.state] || task.state);
    button.onclick=()=>act(button,()=>detail(task)); $('tasks').append(button);
  }
  if (!after && !result.items.length) $('tasks').textContent='暂无任务。具有导入权限的成员可以从指定飞书记录导入。';
  next=result.next_after; $('more').hidden=!result.has_more;
}
async function detail(task) {
  cancelPlan(); current=await request('/api/task',{task:task.id || task.task,revision:task.revision});
  $('detail').hidden=false; $('task-title').textContent=current.task+' · 第 '+current.revision+' 版';
  $('task-state').textContent=stateNames[current.state] || current.state;
  $('script').textContent=current.input.script;
  $('history').textContent=current.history.length ? current.history.map(r=>'第 '+r.revision+' 版 · '+(stateNames[r.state] || r.state)+'\n'+r.actor+'\n'+(r.feedback || '无补充意见')).join('\n\n') : '暂无审核记录';
  if (current.history_truncated) $('history').textContent+='\n记录较多，当前仅展示部分。';
  $('generate-video').hidden=!current.can_generate_video;
  $('revise-script').hidden=!current.can_revise_script;
  $('download-video').hidden=current.state!=='accepted' || !current.media_url;
  if(current.state==='accepted' && current.media_url)$('download-video').href=current.media_url;
  $('video').hidden=!current.media_url;
  if (current.media_url) $('video').src=current.media_url; else { $('video').removeAttribute('src'); $('video').load(); }
  $('video-hash').textContent=current.artifact_sha256?'本版本视频 SHA256：'+current.artifact_sha256:'';
  const stage=current.state==='awaiting_video_review'?'video':'script';
  $('stage').value=stage; $('stage').disabled=true;
  $('review-form').hidden=!current.review_stages.includes(stage) || current.state!=='awaiting_'+stage+'_review';
  $('feedback').value=''; $('decision').value='accept';
}
function showPlan(result) {
  pending=result.plan_sha256;
  $('plan').textContent='任务：'+result.task+' · 第 '+result.revision+' 版\n操作：'+(result.kind==='video_job'?'生成视频（一次模型调用）':result.kind==='script_job'?'生成脚本（一次模型调用）':result.kind==='import'?'导入版本':(result.stage==='video'?'视频':'脚本')+' / '+(result.decision==='accept'?'通过':'退回'))+'\n来源：'+result.source_record+'\n\n脚本：\n'+result.script+'\n\n意见：'+(result.feedback || '无')+(result.artifact_sha256?'\n视频 SHA256：'+result.artifact_sha256:'');
  if(['script_job','video_job'].includes(result.kind))$('plan').textContent+='\n模型：'+result.model+'\n费用账户：'+result.billing_owner+'\n本次最多提交：1 次；超时不会自动重发。';
  $('confirmation').hidden=false; $('confirmation').scrollIntoView({block:'start'});
}
$('generate-video').onclick=()=>act($('generate-video'),async()=>{cancelPlan();showPlan(await request('/api/video/prepare',{task:current.task,revision:current.revision}));});
$('revise-script').onclick=()=>{cancelPlan();scriptRevision=current.revision;$('script-task').value=current.task;$('script-task').readOnly=true;$('script-sku').value=current.input.sku_id;$('script-brief').value=current.generation_brief;$('script-context').textContent='修订当前任务 · 将生成第 '+(current.revision+1)+' 版，自动带入退回意见';$('create-script').open=true;$('script-brief').focus();};
$('new-script').onclick=()=>{cancelPlan();scriptRevision=0;$('script-form').reset();$('script-task').readOnly=false;$('script-context').textContent='创建新任务';};
$('script-form').onsubmit=event=>{event.preventDefault();if(!$('script-task').value.trim())$('script-task').value='task_'+crypto.randomUUID().replaceAll('-','');act(event.submitter,async()=>{cancelPlan();showPlan(await request('/api/script/prepare',{task:$('script-task').value.trim(),sku_id:$('script-sku').value,brief:$('script-brief').value,expected_revision:scriptRevision}));});};
$('import-form').onsubmit=event=>{event.preventDefault(); const button=event.submitter; act(button,async()=>{cancelPlan();showPlan(await request('/api/import/prepare',{record:$('record').value.trim(),expected_revision:Number($('expected-revision').value)}));});};
$('review-form').onsubmit=event=>{event.preventDefault(); act(event.submitter,async()=>{
  cancelPlan(); const feedback=$('feedback').value;
  if ($('decision').value==='reject' && !feedback.trim()) throw new Error('退回时请填写具体意见');
  showPlan(await request('/api/review/prepare',{task:current.task,revision:current.revision,stage:$('stage').value,decision:$('decision').value,feedback}));
});};
$('commit').onclick=()=>act($('commit'),async()=>{ const result=await request('/api/commit',{plan_sha256:pending}); cancelPlan(); message(result.receipt.video_submission_approved?'已批准本次视频生成，等待调度。':'已保存：'+(stateNames[result.receipt.state] || result.receipt.state)); await tasks(); await detail(result.receipt); });
$('cancel').onclick=cancelPlan;
$('video').addEventListener('error',()=>{cancelPlan();message('视频未能播放，请核对文件是否可读，并使用支持该视频格式的浏览器；不要在未检查视频时通过审核。');});
$('refresh').onclick=()=>act($('refresh'),()=>tasks());
$('more').onclick=()=>act($('more'),()=>tasks(next));
$('logout').onclick=()=>act($('logout'),async()=>{await request('/api/logout',{});clearTimeout(polling);cancelPlan();current=null;$('detail').hidden=true;$('video').pause();$('video').removeAttribute('src');$('video').load();$('script').textContent='';$('history').textContent='';$('tasks').replaceChildren();message('已退出。');await boot();});
boot().catch(error=>message('无法连接：'+error.message));
