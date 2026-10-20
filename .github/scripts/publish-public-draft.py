"""Persist only verified, allowlisted release inputs as a maintainer-only draft."""
import json,os,subprocess
from pathlib import Path
assert os.environ['GITHUB_EVENT_NAME']=='workflow_dispatch'
assert os.environ['GITHUB_REF']=='refs/heads/main'
assert os.environ.get('VF_CREATE_DRAFT')=='true'
assert os.environ['GITHUB_REPOSITORY']=='ChuluuMGL/video-factory'
root=Path('public-release');r=json.loads((root/'release.json').read_text());assert r['public_release'] is True
notes=Path('public-release-notes.md');notes.write_text('''Video Factory · MIT public alpha / MIT 开源测试版

在自己的本机或服务器搭建视频工作流，或接入已有 n8n；默认飞书 Base + n8n。源码和固定安装包可公开获取。
安装指南：https://www.yueyu.tech/zh/products/video-factory/

包含固定安装包、完整 Setup Skill、校验清单、依赖许可和 Psycopg 对应源码。第三方 wheel 不在公开归档中再分发，首次安装直接从 PyPI 官方文件源获取并校验固定哈希。服务镜像由客户服务器按固定摘要从上游获取；不分发历史内部镜像或原始日志。

Cloud-tested fixed installer, complete Setup Skill, checksums and dependency notices/source. Customer servers obtain pinned upstream service images. No historical internal images or raw logs are included.

本候选包含结果同步目标校验、恢复时保留并发修改，以及便携多模型适配。DeepSeek 和 MiniMax H3 有既有真实验收；新增 GPT、Gemini、MiniMax 文本、千问、豆包、可灵、Seedance、万相和 Seedream 路线的合成测试不等于供应商真实验收。发布前以逐模型验收回执为准，不宣称所有模型可生产使用。

New provider adapters are preview routes. Synthetic n8n tests do not establish live provider acceptance, account eligibility, output quality or production readiness. Consult the per-provider acceptance status before enabling paid execution. Unknown submissions must be reconciled, never automatically retried.

This alpha includes portable Node.js/Docker setup and existing-n8n integration. Isolated acceptance verified real Feishu, DeepSeek, one H3 shot and first-attempt owner-confirmed script generation with human approval. Distinct employee authorization, nonauthor installation and actual desktop logout/login remain unverified. See docs/product/DELIVERY_ACCEPTANCE.md and handoff.json for their separate scopes.
''')
subprocess.run(['gh','release','create','v'+r['version'],'--target',r['source_commit'],'--draft','--prerelease','--title','Video Factory '+r['version'],'--notes-file',str(notes),*[str(p) for p in sorted(root.iterdir())]],check=True)
