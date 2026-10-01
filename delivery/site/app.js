'use strict';
(() => {
  const config = window.VF_DELIVERY;
  const prompt = document.getElementById('agent-prompt');
  const feedback = document.getElementById('copy-feedback');
  if (!config) {
    prompt.value = '安装包信息未能加载。请刷新页面重试；仍有问题时联系实施同事。';
    feedback.textContent = prompt.value;
    document.getElementById('copy-prompt').disabled = true;
    return;
  }
  const intents = {
    install: '请使用 Video Factory Setup Skill，带我在自己的服务器上完成第一次安装，并配置第一个测试项目。',
    project: '请使用 Video Factory Setup Skill，在同一客户的现有服务中新增项目。先检查已安装的服务与版本，复用服务，保留已有项目。',
    resume: '请使用 Video Factory Setup Skill，继续上次未完成的安装。先查阅上次安装记录，确认服务器和已完成步骤；不重复创建尚未确认结果的资源。',
    repair: '请使用 Video Factory Setup Skill，先只读检查我指定的项目。根据当前版本、任务和报错查找原因。不要删除数据或直接重装。'
  };
  const descriptions = {
    install: '第一次安装：连接你的服务器，安装服务，并配置第一个测试项目。',
    project: '新增项目：复用这个客户已有的服务，单独配置新项目，保留原有项目。',
    resume: '继续上次安装：检查已有安装记录，从尚未完成的步骤继续。',
    repair: '排查问题：先检查指定项目的状态和报错，再说明原因与处理办法。'
  };
  let mode = 'install';
  function select(value) {
    mode = value;
    document.querySelectorAll('[data-mode]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.mode === mode)));
    document.getElementById('scenario-description').textContent = descriptions[mode];
    const source = new URL('skill/SKILL.md', window.location.href).href;
    prompt.value = `${intents[mode]}\n\nSkill 阅读地址：${source}\n若该私有页面无法访问，请读取我附上的完整 video-factory-setup.zip（保留包内全部文件）。\n安装包版本：${config.version}；来源：${config.release_url}\n\n请先说明这次要做什么、完成后如何检查结果，再逐步询问必要的服务器、项目和飞书信息。请帮我整理配置并执行安装，不要求我先学习内部命令。密码与密钥只在私有输入界面填写，不在聊天中收集。完成后给出实际网址、检查结果、未完成事项，以及下次如何继续。新增项目优先使用服务器已安装的兼容版本，不能自动升级旧服务。`;
    document.getElementById('copy-prompt').firstChild.textContent = '复制这段指引 ';
    feedback.textContent = '';
  }
  async function copy() {
    try { await navigator.clipboard.writeText(prompt.value); feedback.textContent = '已复制。请粘贴给 AI 助手；第一次使用时，同时附上完整 Skill 文件。'; }
    catch { const preview = prompt.closest('details'); if (preview) preview.open = true; prompt.focus(); prompt.select(); feedback.textContent = '已选中指引，请按 Ctrl+C 或 ⌘C 复制。'; }
  }
  document.querySelectorAll('[data-mode]').forEach(button => button.addEventListener('click', () => select(button.dataset.mode)));
  document.getElementById('copy-prompt').addEventListener('click', copy);
  document.getElementById('choose-project').addEventListener('click', () => { select('project'); document.getElementById('start').scrollIntoView(); copy(); });
  const releaseLink = document.getElementById('release-link');
  const releaseURL = new URL(config.release_url);
  if (releaseURL.protocol !== 'https:' || releaseURL.hostname !== 'github.com') throw new Error('Invalid release source');
  releaseLink.href = releaseURL.href; releaseLink.textContent = `${config.version} · 受邀测试安装包`;
  document.getElementById('version').textContent = config.version;
  document.getElementById('delivery-status').textContent = config.delivery_checks_passed ? '本页自动化检查通过' : '尚无本页通过检查的记录';
  select('install');
})();
