"""Publish only the exact all-jobs-passed private candidate; no host credentials."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

root, source, run = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
receipt = json.loads((root / 'release/release.json').read_text())
assert receipt['source_commit'] == source
archive = root / 'release' / receipt['archive']
assert hashlib.sha256(archive.read_bytes()).hexdigest() == receipt['archive_sha256']
image_manifest = root / 'images/manifest.json'
export = json.loads((root / 'images/export.json').read_text())
assert hashlib.sha256(image_manifest.read_bytes()).hexdigest() == export['manifest_sha256']
images = root / 'images/images.tar'
h = hashlib.sha256()
with images.open('rb') as stream:
    for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''): h.update(chunk)
# The export receipt/manifest format is authoritative, not a copied self-report.
manifest = json.loads(image_manifest.read_text())
assert h.hexdigest() == export['archive_sha256'] == manifest['archive']['sha256']
assert images.stat().st_size == export['archive_bytes'] == manifest['archive']['size']
for store in ('classic', 'containerd'):
    result = json.loads((root / f'images/result-{store}.json').read_text())
    assert result['status'] == 'PASS' and result['manifest_sha256'] == export['manifest_sha256']
handoff = {'schema': 1, 'version': receipt['version'], 'source_commit': source,
           'archive_sha256': receipt['archive_sha256'], 'manifest_sha256': receipt['manifest_sha256'],
           'image_archive_sha256': h.hexdigest(), 'image_manifest_sha256': export['manifest_sha256'],
           'checks': f'https://github.com/{os.environ["GITHUB_REPOSITORY"]}/actions/runs/{run}',
           'customer_independent_install': 'not_run', 'business_ready': False}
(root / 'handoff.json').write_text(json.dumps(handoff, indent=2) + '\n')
notes = root / 'release-notes.md'
notes.write_text('Video Factory 受控安装候选\n\n'
    '本版本包含固定服务器安装包、完整 Setup Skill、安装指南和同版本离线镜像。\n'
    '安装者需私有仓库读取权限；本版本不代表独立客户或完整视频生产验收。\n\n'
    '安装从指南和 Skill 开始。服务、项目、飞书与真实任务分别验收。\n\n'
    'cloud checks: ' + handoff['checks'] + '\n')
assets = [*sorted((root / 'release').glob('*')), root / 'site/video-factory-setup.zip',
          root / 'handoff.json', image_manifest]
if images.stat().st_size < 2 * 1024 ** 3:
    assets.append(images)
else:
    # GitHub assets have a per-file size ceiling. Preserve exact bytes in parts.
    names = []
    with images.open('rb') as stream:
        index = 0
        while True:
            chunk = stream.read(1024 ** 3)
            if not chunk: break
            part = root / f'images.tar.part-{index:03d}'
            part.write_bytes(chunk); assets.append(part); names.append(part.name); index += 1
    instructions = root / 'IMAGE_PARTS.md'
    instructions.write_text('在客户云端按文件名顺序合并 images.tar.part-* 为 images.tar；'
        '合并后 SHA256 必须等于 handoff.json 的 image_archive_sha256。\n\n' + '\n'.join(names) + '\n')
    assets.append(instructions)
tag = 'v' + receipt['version']
subprocess.run(['gh', 'release', 'create', tag, '--target', source, '--prerelease',
                '--title', 'Video Factory ' + receipt['version'], '--notes-file', str(notes),
                *map(str, assets)], check=True, timeout=1200)
print(json.dumps({'status': 'private_prerelease_published', 'tag': tag, 'source_commit': source, 'business_ready': False}))
