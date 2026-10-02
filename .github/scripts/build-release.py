"""Build a pinned candidate handoff from wheels already built in cloud CI."""
import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import re
import shutil
import tarfile
import tomllib
import zipfile
import urllib.request


def release_member(info):
    # GNU tar run by root preserves archived ownership. Never ship the CI
    # runner's uid/gid or writable modes into a customer installation.
    assert info.isdir() or info.isfile(), 'RELEASE_SPECIAL_MEMBER_REJECTED'
    info.uid = info.gid = 0
    info.uname = info.gname = 'root'
    info.mode = 0o755 if info.isdir() else 0o644
    return info


def build(out, wheel_dirs, source, public_release=False):
    assert re.fullmatch(r'[a-f0-9]{40}', source)
    version = tomllib.loads(Path('packages/video-factory-core/pyproject.toml').read_text())['project']['version']
    root = out / f'video-factory-{version}-linux-x86_64-cpython312'
    root.mkdir(parents=True)
    (root / 'wheels').mkdir()
    locks, names, downloads = [], set(), {}
    for directory in wheel_dirs:
        for wheel in sorted(directory.glob('*.whl')):
            assert wheel.is_file() and not wheel.is_symlink()
            with zipfile.ZipFile(wheel) as archive:
                metadata = [n for n in archive.namelist() if n.endswith('.dist-info/METADATA')]
                assert len(metadata) == 1
                info = BytesParser().parsebytes(archive.read(metadata[0]))
            name = re.sub(r'[-_.]+', '-', info['Name']).lower()
            assert name not in names and re.fullmatch(r'[a-z0-9-]+', name)
            assert re.fullmatch(r'[A-Za-z0-9.+]+', info['Version'])
            names.add(name)
            sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
            locks.append(f'{name}=={info["Version"]} --hash=sha256:{sha}')
            shutil.copyfile(wheel, root / 'wheels' / wheel.name)
            if public_release and name != 'video-factory-core':
                with urllib.request.urlopen(f'https://pypi.org/pypi/{name}/{info["Version"]}/json',timeout=45) as response:
                    upstream=json.load(response)
                item=next(x for x in upstream['urls'] if x['filename']==wheel.name)
                assert item['digests']['sha256']==sha and item['size']==wheel.stat().st_size
                assert item['url'].startswith('https://files.pythonhosted.org/packages/')
                downloads['wheels/'+wheel.name]={'url':item['url'],'sha256':sha,'size':item['size']}

    assert 'video-factory-core' in names and 3 <= len(names) <= 30
    (root / 'requirements.lock').write_text('\n'.join(sorted(locks)) + '\n')
    shutil.copyfile('distribution/install.py', root / 'install.py')
    shutil.copyfile('distribution/start.py', root / 'start.py')
    shutil.copyfile('distribution/bootstrap.sh', root / 'bootstrap.sh')
    shutil.copytree('skills/video-factory-setup', root / 'skill/video-factory-setup')
    # Deliver operator documentation only, never internal cloud receipts or
    # historical customer/test configuration through a blanket directory copy.
    docs = json.loads(Path('distribution/customer-docs.json').read_text())
    (root / 'docs/product').mkdir(parents=True)
    for name in docs:
        assert re.fullmatch(r'[A-Z_]+\.md', name)
        shutil.copyfile(Path('docs/product') / name, root / 'docs/product' / name)
    shutil.copyfile('distribution/INSTALL.md', root / 'INSTALL.md')
    (root / 'templates').mkdir()
    shutil.copyfile('packages/video-factory-core/examples/setup/products.json', root / 'templates/products.json')
    if public_release:
        assert Path('LICENSE').is_file(), 'OWNER_APPROVED_LICENSE_REQUIRED'
        for legal_file in ('LICENSE','NOTICE','THIRD_PARTY_NOTICES.md'):
            shutil.copyfile(legal_file,root/legal_file)
        (root/'dependency-downloads.json').write_text(json.dumps(downloads,sort_keys=True,indent=2)+'\n')
        import runpy
        runpy.run_path('.github/scripts/collect-third-party.py')['collect'](root)
    files = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(root.rglob('*')) if p.is_file()}
    manifest = {'schema': 1, 'version': version, 'source_commit': source,
                'target': 'linux-x86_64-cpython312', 'channel': 'public_alpha' if public_release else 'private_candidate', 'files': files}
    (root / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
    archive = out / (root.name + '.tar.gz')
    def shipped_member(info):
        relative=info.name.removeprefix(root.name+'/')
        # Third-party binaries are fetched by the customer from upstream, never redistributed here.
        if relative in downloads:return None
        return release_member(info)
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(root, arcname=root.name, filter=shipped_member)
    receipt = {'schema': 1, 'version': version, 'source_commit': source, 'archive': archive.name,
               'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
               'manifest_sha256': hashlib.sha256((root / 'manifest.json').read_bytes()).hexdigest(),
               'target': manifest['target'], 'public_release': public_release}
    (out / 'release.json').write_text(json.dumps(receipt, indent=2) + '\n')
    (out / 'SHA256SUMS').write_text(receipt['archive_sha256'] + '  ' + archive.name + '\n')
    print(json.dumps(receipt))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--wheels', type=Path, nargs='+', required=True)
    p.add_argument('--source', required=True)
    p.add_argument('--public-release', action='store_true')
    args = p.parse_args()
    build(args.out, args.wheels, args.source, args.public_release)
