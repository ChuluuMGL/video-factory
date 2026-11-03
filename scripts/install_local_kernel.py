#!/usr/bin/env python3
"""Source-candidate local installer. Private venv + real SQLite kernel; no n8n.

Existing directories are never adopted or overwritten. Failed installations stay
available for inspection; no recursive deletion or changes to system services.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import venv


def main():
    p = argparse.ArgumentParser(description='Video Factory 本地假模型版安装（无需 n8n / Docker）')
    p.add_argument('--root', type=Path, required=True, help='全新目录；父目录须已存在')
    p.add_argument('--project', default='local_trial')
    p.add_argument('--mode', choices=('fake-only', 'business'), default='fake-only',
                   help='business installs the terminal setup launcher without a fake ledger')
    p.add_argument('--package', type=Path, help='已核对的本地 wheel；默认使用当前源码包')
    p.add_argument('--wheelhouse', type=Path, help='可选完整离线依赖目录')
    args = p.parse_args()
    import re
    if sys.version_info < (3, 11) or sys.platform not in ('linux', 'darwin'):
        p.error('需要 macOS/Linux 和 Python 3.11 或更新版本')
    if not re.fullmatch('[A-Za-z0-9_-]{1,96}', args.project): p.error('项目标识只能包含字母、数字、短横线和下划线')
    root = args.root.expanduser()
    if not root.is_absolute() or root.resolve() != root or not root.parent.is_dir():
        p.error('需要无符号链接的绝对新目录，且父目录已存在')
    if root.exists() or root.is_symlink(): p.error('目录已存在，保留原内容；重新启动请使用原 vf-local')
    package = args.package or Path(__file__).resolve().parents[1] / 'packages/video-factory-core'
    package = package.resolve(strict=True)
    if args.package and (not package.is_file() or package.suffix != '.whl'): p.error('package 必须是本地 wheel')
    wheels = args.wheelhouse.resolve(strict=True) if args.wheelhouse else None
    if wheels and not wheels.is_dir(): p.error('wheelhouse 必须是目录')
    root.mkdir(mode=0o700)
    receipt = {'schema': 1, 'mode': args.mode, 'project': args.project, 'phase': 'created',
               'n8n_installed': False, 'production_ready': False, 'real_model_calls': 0,
               'package_sha256': hashlib.sha256(package.read_bytes()).hexdigest() if package.is_file() else None,
               'package_kind': 'wheel' if package.is_file() else 'source-candidate'}
    def checkpoint(phase):
        receipt['phase'] = phase
        path = root / 'install.json'
        tmp = root / 'install.json.tmp'
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as out:
            out.write(json.dumps(receipt, ensure_ascii=False, indent=2));out.flush();os.fsync(out.fileno())
        os.replace(tmp, path)
    checkpoint('preparing_environment')
    # Business credentials and other runtime selection variables do not enter pip or vfctl.
    env = {k: os.environ[k] for k in ('PATH', 'HOME', 'LANG', 'TMPDIR', 'SYSTEMROOT') if k in os.environ}
    env.update(PYTHONNOUSERSITE='1', PIP_CONFIG_FILE=os.devnull)
    try:
        venv.EnvBuilder(with_pip=True).create(root / 'environment')
        python = root / 'environment/bin/python'
        pip = [str(python), '-m', 'pip', '--disable-pip-version-check', 'install', '--no-input', '--no-compile']
        if wheels: pip += ['--no-index', '--find-links', str(wheels)]
        subprocess.run(pip + [str(package)], env=env, check=True)
        cli = root / 'environment/bin/vfctl'
        if args.mode == 'business':
            checkpoint('preparing_business_setup')
            command = [str(python), '-I', '-c',
                       'from video_factory.cli import main; raise SystemExit(main())', 'local-setup']
            launcher = '#!/bin/sh\nset -eu\nexec ' + ' '.join(shlex.quote(v) for v in command)
            launcher += ' "$@" --root ' + shlex.quote(str(root / 'business')) + '\n'
            launcher_name = 'vf-setup'
        else:
            checkpoint('initializing_local_ledger')
            subprocess.run([str(cli), 'local', 'install', '--root', str(root / 'data'), '--project', args.project], env=env, check=True)
            launcher = '#!/bin/sh\nset -eu\nif [ "$#" -eq 0 ]; then set -- up; fi\nexec ' + shlex.quote(str(cli)) + ' local "$@" --root ' + shlex.quote(str(root / 'data')) + ' --project ' + shlex.quote(args.project) + '\n'
            launcher_name = 'vf-local'
        # Fixed root/project come last; callers cannot target another ledger through this wrapper.
        fd = os.open(root / launcher_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
        with os.fdopen(fd, 'w') as out: out.write(launcher)
        checkpoint('installed')
        if args.mode == 'business':
            print(json.dumps({'installed': True, 'mode': 'business', 'configuration_complete': False,
                              'setup': str(root / 'vf-setup'), 'required_arguments': ['--plan', '<private-plan.json>', '--interactive', '--bind'],
                              'production_ready': False, 'real_model_calls': 0}, ensure_ascii=False))
            return 0
        print(json.dumps({'installed': True, 'mode': 'fake-only', 'n8n_required': False,
                          'start': str(root / 'vf-local'), 'status': [str(root / 'vf-local'), 'status'],
                          'stop': 'Ctrl-C; restart with the same vf-local', 'production_ready': False}, ensure_ascii=False))
        return 0
    except Exception:
        checkpoint('incomplete_preserved')
        print('安装未完成，原目录与检查点保留；没有启动服务或启用真实模型。', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
