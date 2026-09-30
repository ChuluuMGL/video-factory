'use strict';
const $ = id => document.getElementById(id);
let csrf = '', pending = null, timer = null, deadline = 0, closed = false;
// Fragment never reaches the HTTP server or referrer. Drop it before any call.
let capability = location.hash.slice(1);
history.replaceState(null, '', '/');
const show = (id, value) => { $(id).hidden = !value; };
const message = text => { $('message').textContent = text; };
function clear() {
  clearTimeout(timer); pending = null;
  ['device', 'workspace', 'confirmation'].forEach(id => show(id, false));
  ['user-code', 'identity', 'plan', 'plan-hash'].forEach(id => { $(id).textContent = ''; });
  $('authorization-link').removeAttribute('href');
}
async function request(path, body) {
  const response = await fetch(path, {cache: 'no-store', headers: body === undefined ? {} : {'Content-Type': 'application/json', 'X-VF-CSRF': csrf},
    method: body === undefined ? 'GET' : 'POST', body: body === undefined ? undefined : JSON.stringify(body)});
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || 'SETUP_REQUEST_FAILED');
  return value;
}
function fail(error) { pending = null; show('confirmation', false); if (error.message.startsWith('AUTH_') || error.message.includes('EXPIRED')) { clear(); show('login', false); } message('未完成：'+error.message+'。请重新核对；窗口过期或退出后请在终端重新启动。'); }
async function poll() {
  try {
    const result = await request('/api/poll', {});
    if (closed) return;
    if (result.status === 'authorized') {
      clear(); show('login', false); show('workspace', true);
      $('identity').textContent = '当前用户：'+result.identity.open_id+' / 租户：'+result.identity.tenant_key;
      message('已授权。请读取并核对连接配置。');
    } else timer = setTimeout(poll, result.retry_after*1000);
  } catch (error) { fail(error); }
}
function click(id, operation) {
  $(id).addEventListener('click', async () => {
    $(id).disabled = true;
    try { await operation(); } catch (error) { fail(error); }
    finally { $(id).disabled = false; }
  });
}
click('login-button', async () => {
  clear(); const result = await request('/api/login', {});
  if (closed) return;
  $('user-code').textContent = result.user_code; $('authorization-link').href = result.verification_uri;
  show('device', true); message('请在飞书页面确认授权。'); timer = setTimeout(poll, result.retry_after*1000);
});
click('prepare', async () => {
  pending = null; show('confirmation', false);
  const result = await request('/api/prepare', {}); if (closed) return;
  pending = result.plan_sha256;
  const p = result.plan, b = p.binding;
  const field = key => p.resolved_fields[key]+'（'+b.fields[key]+'）';
  $('plan').textContent = [
    '项目：'+p.context.project, '飞书租户：'+b.tenant_key, '多维表格：'+b.base_token, '数据表：'+b.table_id,
    '', '字段对应关系', '任务 → '+field('task'), 'SKU → '+field('sku_id'), '脚本 → '+field('script'), '来源版本 → '+field('source_revision'),
    '', '提交人：'+b.submitters.join('、'), '脚本审核人：'+b.script_reviewers.join('、'), '视频审核人：'+b.video_reviewers.join('、'),
    '', '本次授权用户：'+p.verified_operator.open_id
  ].join('\n');
  $('plan-hash').textContent = pending;
  show('confirmation', true); message('请确认以上目标与人员配置。');
});
click('cancel', async () => { await request('/api/cancel', {}); pending = null; show('confirmation', false); $('plan').textContent = ''; message('已取消确认；配置尚未保存。'); });
click('commit', async () => {
  if (!pending) return;
  const result = await request('/api/commit', {plan_sha256:pending}); clear(); show('login', false); show('done', true);
  $('handoff').textContent = '在终端按安装方式运行 vfctl review-ui（本机）或 vfctl stack-review（容器），项目参数为 '+result.project+'。完整命令见 REVIEW_UI_USAGE.md。';
  message('连接配置已保存。真实业务验收尚未执行。');
});
click('logout', async () => { await request('/api/logout', {}); clear(); show('login', false); message('已退出；请在终端重新开启连接向导。'); });
(async () => {
  try {
    const session = await request('/api/session'); csrf = session.csrf;
    $('project').textContent = '项目：'+session.project+' / 飞书应用：'+session.app_id;
    deadline = Date.now()+session.remaining_seconds*1000;
    if (capability) { await request('/api/unlock', {capability}); capability = ''; }
    // Refresh preserves the HttpOnly session, but a fresh browser needs the one-use CLI link.
    show(session.authenticated ? 'workspace' : 'login', true);
    message('欢迎继续。先授权，再核对保存。');
    setInterval(() => {
      const seconds = Math.max(0, Math.ceil((deadline-Date.now())/1000));
      $('remaining').textContent = '本次窗口剩余 '+seconds+' 秒';
      if (!seconds && !closed) { closed = true; clear(); show('login', false); message('窗口已到期。请在终端重新开启。'); }
    }, 1000);
  } catch (error) { capability = ''; fail(error); }
})();
