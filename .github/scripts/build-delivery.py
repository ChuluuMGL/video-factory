"""Assemble the customer page and complete Skill from this exact candidate."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import zipfile


def build(destination, receipt_file, repository, checked=False):
    assert re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository)
    receipt = json.loads(receipt_file.read_text())
    assert re.fullmatch(r'0\.1\.0a[0-9]+', receipt['version'])
    assert re.fullmatch(r'[a-f0-9]{40}', receipt['source_commit'])
    for key in ('archive_sha256', 'manifest_sha256'):
        assert re.fullmatch(r'[a-f0-9]{64}', receipt[key])
    assert not destination.exists(), 'DELIVERY_OUTPUT_MUST_BE_NEW'
    shutil.copytree('delivery/site', destination)
    shutil.copytree('skills/video-factory-setup', destination / 'skill')
    shutil.copyfile('docs/product/CUSTOMER_GUIDE.md', destination / 'customer-guide.md')
    allowed = json.loads(Path('distribution/customer-docs.json').read_text())
    for name in ('NEW_INSTALL_CAPABILITIES.md', 'INDEPENDENT_ACCEPTANCE.md'):
        assert name in allowed
        shutil.copyfile(Path('docs/product') / name, destination / name)
    shutil.copyfile(receipt_file, destination / 'release.json')
    with zipfile.ZipFile(destination / 'video-factory-setup.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((destination / 'skill').rglob('*')):
            if path.is_file():
                assert not path.is_symlink()
                info = zipfile.ZipInfo('video-factory-setup/' + path.relative_to(destination / 'skill').as_posix(), (2026, 1, 1, 0, 0, 0))
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes())
    page_source = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    config = {'version': receipt['version'], 'source_commit': receipt['source_commit'], 'page_source_commit': page_source,
              'release_url': f'https://github.com/{repository}/releases/tag/v{receipt["version"]}',
              'delivery_checks_passed': checked, 'public_download': bool(receipt.get('public_release', False)), 'business_ready': False}
    (destination / 'delivery-config.js').write_text('window.VF_DELIVERY = ' + json.dumps(config, ensure_ascii=False) + ';\n')
    hashes = {p.relative_to(destination).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(destination.rglob('*')) if p.is_file()}
    (destination / 'delivery-manifest.json').write_text(json.dumps({'schema': 1, 'source_commit': receipt['source_commit'],
          'version': receipt['version'], 'page_source_commit': page_source, 'files': hashes}, indent=2) + '\n')
    print(json.dumps({'status': 'assembled', 'version': receipt['version'], 'files': len(hashes), 'business_ready': False}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--checked', action='store_true')
    args = parser.parse_args()
    build(args.out, args.receipt, args.repository, args.checked)
