#!/usr/bin/env python3
"""Offline, version-specific CLI bootstrap. No product imports before verification."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys


def require(value, code):
    if not value:
        raise ValueError(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def regular(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'RELEASE_FILE_UNSAFE')
    require(info.st_uid == os.getuid() and not info.st_mode & 0o022, 'RELEASE_FILE_NOT_OWNED')
    return path.read_bytes()


def safe_path(path):
    require(path.is_absolute() and path.resolve() == path, 'ABSOLUTE_NON_SYMLINK_PATH_REQUIRED')
    for parent in (path, *path.parents):
        if parent.exists():
            info = parent.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                    'ROOT_OWNED_NON_WRITABLE_PARENT_REQUIRED')


def verify(bundle, expected):
    safe_path(bundle)
    raw = regular(bundle / 'manifest.json')
    require(re.fullmatch(r'[a-f0-9]{64}', expected) and digest(raw) == expected, 'MANIFEST_HASH_MISMATCH')
    manifest = json.loads(raw)
    require(manifest['schema'] == 1 and manifest['target'] == 'linux-x86_64-cpython312', 'RELEASE_TARGET_UNSUPPORTED')
    require(re.fullmatch(r'0\.1\.0a[0-9]+', manifest['version']), 'RELEASE_VERSION_INVALID')
    require(re.fullmatch(r'[a-f0-9]{40}', manifest['source_commit']), 'RELEASE_SOURCE_INVALID')
    files = manifest['files']
    require(isinstance(files, dict) and 5 <= len(files) <= 150, 'RELEASE_FILES_INVALID')
    total = 0
    for name, sha in files.items():
        relative = PurePosixPath(name)
        require(not relative.is_absolute() and '..' not in relative.parts and str(relative) == name,
                'RELEASE_PATH_INVALID')
        path = bundle / name
        require(path.resolve() == path and re.fullmatch(r'[a-f0-9]{64}', sha), 'RELEASE_PATH_INVALID')
        raw = regular(path)
        total += len(raw)
        require(total <= 120 * 1024 * 1024 and digest(raw) == sha, 'RELEASE_FILE_HASH_MISMATCH')
    actual = set()
    for path in bundle.rglob('*'):
        require(not path.is_symlink(), 'RELEASE_SYMLINK_REJECTED')
        if path.is_dir():
            info = path.stat()
            require(info.st_uid == 0 and not info.st_mode & 0o022, 'RELEASE_DIRECTORY_UNSAFE')
        else:
            actual.add(path.relative_to(bundle).as_posix())
    require(actual == set(files) | {'manifest.json'}, 'RELEASE_UNEXPECTED_FILES')
    require({'install.py', 'requirements.lock', 'skill/video-factory-setup/SKILL.md'} <= set(files), 'RELEASE_INCOMPLETE')
    return manifest


def run(argv):
    # Disable pip configuration and environment injection; do not print subprocess
    # diagnostics containing host-specific configuration. Installation has no network.
    env = {k: v for k, v in os.environ.items() if not k.startswith(('PIP_', 'PYTHON'))}
    env.update(PIP_CONFIG_FILE=os.devnull, PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
    result = subprocess.run(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=240)
    require(result.returncode == 0, 'CLI_INSTALL_COMMAND_FAILED')
    return result.stdout.decode().strip()


def inventory(root):
    result = {}
    for path in sorted(root.rglob('*')):
        name = path.relative_to(root).as_posix()
        if name in ('install.lock', 'installed.json'):
            continue
        info = path.lstat()
        # Linux symlinks report mode 0777 regardless of who can change their
        # target. Check their owner and resolved destination; apply write-mode
        # guards to the containing directories and regular files instead.
        require(info.st_uid == 0 and (stat.S_ISLNK(info.st_mode) or not info.st_mode & 0o022),
                'INSTALLED_PATH_UNSAFE')
        if path.is_symlink():
            require(path.resolve().is_relative_to(root), 'INSTALLED_LINK_ESCAPES_PREFIX')
            result[name] = {'link': os.readlink(path)}
        elif stat.S_ISREG(info.st_mode):
            require(info.st_nlink == 1, 'INSTALLED_HARDLINK_REJECTED')
            result[name] = {'sha256': digest(path.read_bytes())}
        else:
            require(stat.S_ISDIR(info.st_mode), 'INSTALLED_SPECIAL_FILE_REJECTED')
    return result


def harden_created_venv(root):
    # venv --copies inherits the base interpreter mode (cloud tool caches can
    # be shared-writable). This is only called for our newly created private
    # venv, never an existing or customer-supplied directory.
    for path in (root, *root.rglob('*')):
        info = path.lstat()
        require(info.st_uid == 0, 'CREATED_VENV_OWNER_INVALID')
        if path.is_symlink():
            require(path.resolve().is_relative_to(root), 'INSTALLED_LINK_ESCAPES_PREFIX')
        else:
            require(stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                    'CREATED_VENV_PATH_INVALID')
            path.chmod(stat.S_IMODE(info.st_mode) & ~0o022)


def install(bundle, prefix, expected):
    require(platform.system() == 'Linux' and platform.machine() == 'x86_64' and os.getuid() == 0,
            'LINUX_X86_64_ROOT_REQUIRED')
    require(sys.version_info[:2] == (3, 12), 'PYTHON_312_REQUIRED')
    manifest = verify(bundle, expected)
    safe_path(prefix)
    require(prefix.parent.is_dir() and not prefix.is_relative_to(bundle) and not bundle.is_relative_to(prefix),
            'SEPARATE_EXISTING_INSTALL_PARENT_REQUIRED')
    require(shutil.disk_usage(prefix.parent).free >= 512 * 1024 * 1024, 'CLI_DISK_SPACE_UNDER_512_MIB')
    prefix.mkdir(mode=0o700, exist_ok=True)
    require(stat.S_IMODE(prefix.stat().st_mode) == 0o700, 'CLI_PREFIX_MUST_BE_PRIVATE')
    fd = os.open(prefix / 'install.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == 0 and not info.st_mode & 0o077,
                'INSTALL_LOCK_UNSAFE')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        receipt_path = prefix / 'installed.json'
        reused = receipt_path.exists()
        if reused:
            receipt = json.loads(regular(receipt_path))
            require(receipt['manifest_sha256'] == expected, 'INSTALL_RELEASE_CHANGED_USE_NEW_PREFIX')
            require(receipt['files'] == inventory(prefix), 'INSTALLED_FILES_CHANGED')
        else:
            require({p.name for p in prefix.iterdir()} == {'install.lock'}, 'INSTALL_INCOMPLETE_OR_NOT_EMPTY_USE_NEW_PREFIX')
            # A failed bootstrap is kept for diagnosis. Never delete an unknown
            # prefix, and never execute a partly installed venv on a repeat call.
            (prefix / 'pending.json').write_text(json.dumps({'manifest_sha256': expected}))
            shutil.copytree(bundle, prefix / 'release')
            verify(prefix / 'release', expected)
            run([sys.executable, '-I', '-m', 'venv', '--copies', str(prefix / 'venv')])
            harden_created_venv(prefix / 'venv')
            python = str(prefix / 'venv/bin/python')
            run([python, '-I', '-m', 'pip', '--disable-pip-version-check', '--no-cache-dir', 'install',
                 '--no-index', '--only-binary=:all:', '--require-hashes', '--find-links', str(prefix / 'release/wheels'),
                 '-r', str(prefix / 'release/requirements.lock')])
            harden_created_venv(prefix / 'venv')
        cli = str(prefix / 'venv/bin/vfctl')
        require(run([cli, '--version']) == manifest['version'], 'INSTALLED_VERSION_MISMATCH')
        run([str(prefix / 'venv/bin/python'), '-I', '-m', 'pip', 'check'])
        if not reused:
            with receipt_path.open('x') as out:
                json.dump({'manifest_sha256': expected, 'version': manifest['version'],
                           'source_commit': manifest['source_commit'], 'files': inventory(prefix)}, out, sort_keys=True)
                out.flush()
                os.fsync(out.fileno())
        command = [cli, 'setup-run', '--session', '/root/vf-private/customer.setup.json',
                   '--root', '/opt/video-factory', '--wheelhouse', str(prefix / 'release/wheels')]
        return {'status': 'cli_installed', 'version': manifest['version'], 'source_commit': manifest['source_commit'],
                'manifest_sha256': expected, 'reused': reused, 'cli': cli,
                'skill': str(prefix / 'release/skill/video-factory-setup'),
                'next_command_example': shlex.join(command), 'private_session_parent_required': '/root/vf-private',
                'docker_installed': False, 'services_started': False, 'business_ready': False}
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description='Install this verified offline CLI release into a separate private prefix')
    parser.add_argument('--prefix', type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True, help='digest from the trusted release handoff')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        print(json.dumps(install(Path(__file__).absolute().parent, args.prefix, args.manifest_sha256), ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        code = str(error) if type(error) is ValueError and re.fullmatch(r'[A-Z0-9_]+', str(error)) else 'CLI_BOOTSTRAP_FAILED'
        print(json.dumps({'error': code, 'services_started': False, 'business_ready': False}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
