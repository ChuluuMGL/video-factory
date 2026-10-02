"""Exercise the delivered archive and installer in a network-disabled cloud host."""
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile


def command(argv, expected=0):
    result = subprocess.run(argv, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
    if result.returncode != expected and '--prefix' in argv:
        prefix = Path(argv[argv.index('--prefix') + 1])
        unsafe = []
        for path in prefix.rglob('*'):
            info = path.lstat()
            if info.st_uid != 0 or not stat.S_ISLNK(info.st_mode) and info.st_mode & 0o022:
                unsafe.append((str(path.relative_to(prefix)), info.st_uid, oct(stat.S_IMODE(info.st_mode))))
        print(json.dumps({'synthetic_install_unsafe_paths': unsafe[:20]}), file=sys.stderr)
    assert result.returncode == expected, (argv[1:2], result.returncode, result.stdout.decode()[-1000:], result.stderr.decode()[-1000:])
    return result.stdout.decode()


release = Path(sys.argv[1])
receipt = json.loads((release / 'release.json').read_text())
archive = release / receipt['archive']
assert hashlib.sha256(archive.read_bytes()).hexdigest() == receipt['archive_sha256']
with tempfile.TemporaryDirectory(prefix='vf-release-', dir='/root') as tmp:
    root = Path(tmp)
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            assert member.isdir() or member.isfile()
            assert member.uid == member.gid == 0
            assert member.uname == member.gname == 'root'
            assert member.mode == (0o755 if member.isdir() else 0o644)
    # Follow the operator's ordinary root shell extraction. Python's data
    # filter drops archive ownership and previously hid a broken handoff.
    command(['tar', '-xzf', str(archive), '-C', str(root)])
    bundle = root / archive.name.removesuffix('.tar.gz')
    if (bundle/'dependency-downloads.json').exists():
        # Online acquisition is separately exercised by upstream-install-smoke.py.
        # Reuse the identical hash-locked cloud wheels for these network-disabled refusal checks.
        for name in json.loads((bundle/'dependency-downloads.json').read_text()):
            source=release/archive.name.removesuffix('.tar.gz')/name
            shutil.copyfile(source,bundle/name)
    prefix = root / 'cli'
    argv = [sys.executable, '-I', str(bundle / 'install.py'), '--prefix', str(prefix), '--manifest-sha256', receipt['manifest_sha256']]
    first = json.loads(command(argv))
    assert first['status'] == 'cli_installed' and not first['reused']
    assert first['version'] == receipt['version'] and first['source_commit'] == receipt['source_commit']
    assert not first['business_ready'] and not first['services_started']
    original = (prefix / 'installed.json').read_bytes()
    second = json.loads(command(argv))
    assert second['reused'] and (prefix / 'installed.json').read_bytes() == original
    assert command([first['cli'], '--version']).strip() == receipt['version']
    assert 'setup-run' in command([first['cli'], '--help'])
    # Every relative link must work inside the delivered archive, not just in Git.
    for document in (bundle / 'docs').rglob('*.md'):
        for target in re.findall(r'\]\(([^)]+)\)', document.read_text()):
            if '://' not in target and not target.startswith('#'):
                assert (document.parent / target.split('#')[0]).is_file(), (document.name, target)
    skill = Path(first['skill'])
    assert (skill / 'SKILL.md').is_file() and (skill / 'references/operations.md').is_file()
    products = json.loads((prefix / 'release/templates/products.json').read_text())
    assert products and set(products[0]) == {'sku_id', 'name', 'variant', 'truth_source'}
    # The real installed CLI still requires private human TTY, before creating data.
    result = command([first['cli'], 'setup-run', '--session', str(root / 'setup.json'), '--root', str(root / 'stack'),
                      '--wheelhouse', str(prefix / 'release/wheels')], expected=2)
    assert 'TTY' in result and not (root / 'stack').exists()
    guided = [sys.executable, str(bundle / 'start.py'), '--prefix', str(prefix),
              '--manifest-sha256', receipt['manifest_sha256'], '--session', str(root / 'private/setup.json'),
              '--root', str(root / 'stack'), '--prepare-only']
    # Test normal Python without the test helper's bytecode suppression, too.
    plain_env = dict(os.environ); plain_env.pop('PYTHONDONTWRITEBYTECODE', None)
    normal = subprocess.run(guided, env=plain_env, capture_output=True, text=True, timeout=300)
    assert normal.returncode == 0, normal.stdout
    assert not (bundle / '__pycache__').exists()
    ready = json.loads(normal.stdout)
    assert ready['status'] == 'cli_ready_setup_pending' and ready['cli_reused']
    assert ready['next_argv'][0] == first['cli'] and ready['next_argv'][1] == 'setup-run'
    assert not ready['business_ready'] and not (root / 'stack').exists()
    assert json.loads(command(guided[:-1], expected=2))['error'] == 'PRIVATE_TTY_REQUIRED'
    assert 'ECS_A26_ACCEPTANCE.md' not in {p.name for p in (bundle / 'docs/product').iterdir()}
    rejected = []

    def deny(name, args=argv, code=None):
        response = json.loads(command(args, expected=2))
        assert 'error' in response and (code is None or response['error'] == code), response
        rejected.append(name)

    bad = argv.copy();bad[-1] = '0' * 64
    deny('wrong_manifest_digest', bad, 'MANIFEST_HASH_MISMATCH')
    wheel = next((bundle / 'wheels').glob('*.whl')); raw = wheel.read_bytes()
    wheel.write_bytes(raw + b'tampered')
    deny('tampered_wheel', code='RELEASE_FILE_HASH_MISMATCH')
    wheel.write_bytes(raw)
    wheels_mode = (bundle / 'wheels').stat().st_mode & 0o777
    (bundle / 'wheels').chmod(0o777)
    deny('writable_release_subdirectory', code='RELEASE_DIRECTORY_UNSAFE')
    (bundle / 'wheels').chmod(wheels_mode)
    extra = bundle / 'extra.txt';extra.write_text('not in manifest')
    deny('unexpected_file', code='RELEASE_UNEXPECTED_FILES');extra.unlink()
    wheel.unlink();wheel.symlink_to(prefix / 'release/wheels' / wheel.name)
    deny('symlinked_release_wheel');wheel.unlink();wheel.write_bytes(raw)
    installed = prefix / 'release/INSTALL.md';raw = installed.read_bytes();installed.write_bytes(raw + b'changed')
    deny('installed_file_changed', code='INSTALLED_FILES_CHANGED');installed.write_bytes(raw)
    escaped = prefix / 'venv/escape';escaped.symlink_to(root / 'outside')
    deny('installed_link_escapes_prefix', code='INSTALLED_LINK_ESCAPES_PREFIX');escaped.unlink()
    # No overwrite of an existing configuration directory, partial installation,
    # symlink, or target guarded by another installer.
    for name, contents in [('nonempty', 'customer-config.json'), ('partial', 'pending.json')]:
        target = root / name;target.mkdir(mode=0o700);(target / contents).write_text('keep this')
        bad = argv.copy();bad[bad.index('--prefix') + 1] = str(target)
        deny(name, bad, 'INSTALL_INCOMPLETE_OR_NOT_EMPTY_USE_NEW_PREFIX')
        assert (target / contents).read_text() == 'keep this'
    link = root / 'link';link.symlink_to(prefix, target_is_directory=True)
    bad = argv.copy();bad[bad.index('--prefix') + 1] = str(link)
    deny('symlink_prefix', bad, 'ABSOLUTE_NON_SYMLINK_PATH_REQUIRED')
    with (prefix / 'install.lock').open('rb') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        deny('concurrent_install')
    # A different valid manifest must never replace the existing release.
    other = root / 'other-bundle';shutil.copytree(bundle, other)
    data = json.loads((other / 'manifest.json').read_text());data['source_commit'] = '1' * 40
    (other / 'manifest.json').write_text(json.dumps(data))
    bad = argv.copy();bad[2] = str(other / 'install.py');bad[-1] = hashlib.sha256((other / 'manifest.json').read_bytes()).hexdigest()
    deny('changed_release', bad, 'INSTALL_RELEASE_CHANGED_USE_NEW_PREFIX')
    assert json.loads(command(argv))['reused']
    # No docker invocation is needed for CLI bootstrap and no customer state exists.
    assert not (root / 'stack').exists() and not (root / 'setup.json').exists()
    print(json.dumps({'status': 'PASS', 'version': receipt['version'], 'source_commit': receipt['source_commit'],
                      'archive_sha256': receipt['archive_sha256'], 'manifest_sha256': receipt['manifest_sha256'],
                      'archive_to_offline_cli_install': 'PASS', 'same_release_reused_without_reinstall': 'PASS',
                      'root_gnu_tar_extract_and_install': 'PASS', 'archive_ownership': 'root:root',
                      'installed_skill_and_references': 'PASS', 'rejected_cases': rejected,
                      'shipped_sku_template': 'PASS',
                      'network': 'disabled_by_network_namespace', 'customer_deployment': 'not_run', 'paid_requests': 0}))
