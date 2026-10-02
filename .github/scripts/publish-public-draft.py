"""Persist only verified, allowlisted release inputs as a maintainer-only draft."""
import json,os,subprocess
from pathlib import Path
assert os.environ['GITHUB_EVENT_NAME']=='workflow_dispatch'
assert os.environ['GITHUB_REF']=='refs/heads/main'
assert os.environ.get('VF_CREATE_DRAFT')=='true'
assert os.environ['GITHUB_REPOSITORY']=='ChuluuMGL/video-factory'
root=Path('public-release');r=json.loads((root/'release.json').read_text());assert r['public_release'] is True
notes=Path('public-release-notes.md');notes.write_text('''Video Factory · MIT public alpha / MIT 开源测试版

在客户自己的服务器部署视频工作流；默认飞书 Base + n8n。源码和固定安装包可公开获取。
安装指南：https://www.yueyu.tech/zh/products/video-factory/

包含固定安装包、完整 Setup Skill、校验清单、依赖许可和 Psycopg 对应源码。第三方 wheel 不在公开归档中再分发，首次安装直接从 PyPI 官方文件源获取并校验固定哈希。服务镜像由客户服务器按固定摘要从上游获取；不分发历史内部镜像或原始日志。

Cloud-tested fixed installer, complete Setup Skill, checksums and dependency notices/source. Customer servers obtain pinned upstream service images. No historical internal images or raw logs are included.

This is an alpha. Independent customer installation, new live paid generation and distinct employee acceptance remain pending. See handoff.json for scope.
''')
subprocess.run(['gh','release','create','v'+r['version'],'--target',r['source_commit'],'--draft','--prerelease','--title','Video Factory '+r['version'],'--notes-file',str(notes),*[str(p) for p in sorted(root.iterdir())]],check=True)
