'use strict';
(() => {
  const config = window.VF_DELIVERY;
  const prompt = document.getElementById('agent-prompt');
  const feedback = document.getElementById('copy-feedback');
  if (!config) {
    prompt.value = '交付信息加载失败。请重新打开页面，或下载完整 Skill 后交给 Agent。';
    document.getElementById('copy-prompt').disabled = true;
    return;
  }
  const intents = {
    install: '请使用 Video Factory Setup Skill，带我在自己的服务器上完成首次部署和第一个测试项目。',
    project: '请使用 Video Factory Setup Skill，在同一客户的现有服务中新增项目。先回读原部署和版本，复用服务与 n8n，保留已有项目。',
    resume: '请使用 Video Factory Setup Skill，继续上次未完成的安装。先读取原 session、主机和实际状态，不重新创建未知状态的资源。',
    repair: '请使用 Video Factory Setup Skill，先只读检查我指定的项目。根据当前版本、任务和错误回执诊断，不清库或重装来消除错误。'
  };
  let mode = 'install';
  function select(value) {
    mode = value;
    document.querySelectorAll('[data-mode]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.mode === mode)));
    const source = new URL('skill/SKILL.md', window.location.href).href;
    prompt.value = `${intents[mode]}\n\nSkill 阅读地址：${source}\n若该私有页面无法访问，请读取我附上的完整 video-factory-setup.zip（保留 references/）。\n安装包版本：${config.version}；来源：${config.release_url}\n\n请先说明这次操作与可验收范围，再逐步询问必要的服务器、项目和飞书信息。不要让我先手写 JSON 或拼接内部命令。密码与密钥只在私有输入界面填写，不在聊天中收集。完成后交付实际入口、检查结果、未完成事项和续接方式。新增项目优先使用服务器已安装的兼容版本，不能自动升级旧服务。`;
    document.getElementById('copy-prompt').firstChild.textContent = mode === 'project' ? '复制新增项目指引 ' : '复制给 Agent ';
    feedback.textContent = '';
  }
  async function copy() {
    try { await navigator.clipboard.writeText(prompt.value); feedback.textContent = '已复制。粘贴给 Agent，并在需要时附上完整 Skill 包。'; }
    catch { prompt.focus(); prompt.select(); feedback.textContent = '已选中指引，请按 Ctrl+C 或 ⌘C 复制。'; }
  }
  document.querySelectorAll('[data-mode]').forEach(button => button.addEventListener('click', () => select(button.dataset.mode)));
  document.getElementById('copy-prompt').addEventListener('click', copy);
  document.getElementById('choose-project').addEventListener('click', () => { select('project'); document.getElementById('start').scrollIntoView(); copy(); });
  const releaseLink = document.getElementById('release-link');
  const releaseURL = new URL(config.release_url);
  if (releaseURL.protocol !== 'https:' || releaseURL.hostname !== 'github.com') throw new Error('Invalid release source');
  releaseLink.href = releaseURL.href; releaseLink.textContent = `${config.version} · 私有候选发行版`;
  document.getElementById('version').textContent = config.version;
  document.getElementById('delivery-status').textContent = config.delivery_checks_passed ? '本版云端检查通过' : '本版检查进行中';
  select('install');
})();
