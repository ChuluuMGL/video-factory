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
  let mode = 'install';
  function select(value) {
    mode = value;
    document.querySelectorAll('[data-mode]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.mode === mode)));
    const source = new URL('skill/SKILL.md', window.location.href).href;
    prompt.value = `${intents[mode]}\n\nSkill 阅读地址：${source}\n链接不可访问时，读取附件 video-factory-setup.zip，保留全部文件。\n安装包版本：${config.version}；来源：${config.release_url}\n\n按 Skill 在私有终端逐步确认服务器与项目，隐藏输入密码与密钥，不把它们发进聊天。本人完成飞书授权后，回到终端确认 Base 计划。Setup 成功后交接服务状态和 Base 链接；真实任务另行验收。新增项目复用同客户的服务。`;
    prompt.setSelectionRange(0, 0);
    prompt.scrollTop = 0;
    document.querySelector('#copy-prompt span').textContent = '复制';
    feedback.textContent = '';
  }
  async function copy() {
    try { await navigator.clipboard.writeText(prompt.value); document.querySelector('#copy-prompt span').textContent = '已复制'; feedback.textContent = '已复制，粘贴给 AI 助手即可。'; }
    catch { prompt.focus(); prompt.select(); feedback.textContent = '已选中指引，请按 Ctrl+C 或 ⌘C 复制。'; }
  }
  document.querySelectorAll('[data-mode]').forEach(button => button.addEventListener('click', () => select(button.dataset.mode)));
  document.getElementById('copy-prompt').addEventListener('click', copy);
  document.getElementById('choose-project').addEventListener('click', () => { select('project'); document.getElementById('start').scrollIntoView(); copy(); });
  const releaseLink = document.getElementById('release-link');
  const releaseURL = new URL(config.release_url);
  if (releaseURL.protocol !== 'https:' || releaseURL.hostname !== 'github.com') throw new Error('Invalid release source');
  releaseLink.href = releaseURL.href; releaseLink.textContent = `${config.version} · ${config.public_download ? "公开测试版" : "受邀测试安装包"}`;
  document.querySelector(".edition").textContent = config.public_download ? "公开测试版" : "受邀测试中";
  document.getElementById("download-access").textContent = config.public_download
    ? "安装包公开下载，无需 GitHub 登录或仓库授权。由助手核对固定版本与校验信息。"
    : "安装包仅向受邀人员开放。没有仓库权限时，请实施同事提供或代装。";
  document.getElementById('version').textContent = config.version;
  select('install');
})();
