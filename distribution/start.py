#!/usr/bin/env python3
"""Verified bundle entry: install/reuse the CLI and continue into private Setup."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
# Do not change the verified bundle by creating an import cache before verification.
sys.dont_write_bytecode = True
from install import install, require, safe_path


def main():
    parser = argparse.ArgumentParser(description='Video Factory guided server entry')
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--prefix', type=Path, required=True)
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--mode', choices=['install', 'resume', 'new-project'], default='install')
    parser.add_argument('--from-session', type=Path)
    parser.add_argument('--image-bundle', type=Path)
    parser.add_argument('--image-manifest-sha256')
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        require(args.session.is_absolute() and args.root.is_absolute(), 'ABSOLUTE_PATH_REQUIRED')
        safe_path(args.session.parent)
        require(args.session.parent.parent.is_dir(), 'SESSION_PARENT_PARENT_REQUIRED')
        require((args.mode == 'new-project') == bool(args.from_session), 'NEW_PROJECT_SOURCE_REQUIRED')
        require(bool(args.image_bundle) == bool(args.image_manifest_sha256), 'IMAGE_MANIFEST_REQUIRED')
        if args.mode == 'new-project':
            require(args.from_session.is_file() and args.from_session != args.session, 'NEW_PROJECT_DISTINCT_SESSION_REQUIRED')
            require(not args.session.exists(), 'NEW_PROJECT_SESSION_EXISTS_USE_RESUME')
            require((args.root / 'stack.json').is_file(), 'NEW_PROJECT_EXISTING_STACK_REQUIRED')
        if args.mode == 'resume': require(args.session.is_file(), 'RESUME_SESSION_REQUIRED')
        if not args.prepare_only:
            require(sys.stdin.isatty() and sys.stdout.isatty() and sys.stderr.isatty(), 'PRIVATE_TTY_REQUIRED')
        result = install(Path(__file__).absolute().parent, args.prefix, args.manifest_sha256)
        args.session.parent.mkdir(mode=0o700, exist_ok=True)
        require(args.session.parent.stat().st_mode & 0o077 == 0, 'SESSION_DIRECTORY_MUST_BE_PRIVATE')
        command = [result['cli'], 'setup-run', '--session', str(args.session), '--root', str(args.root),
                   '--wheelhouse', str(args.prefix / 'release/wheels')]
        if args.from_session: command += ['--from-session', str(args.from_session)]
        if args.image_bundle:
            command += ['--image-bundle', str(args.image_bundle), '--image-manifest-sha256', args.image_manifest_sha256]
        if args.prepare_only:
            print(json.dumps({'status': 'cli_ready_setup_pending', 'mode': args.mode,
                  'version': result['version'], 'cli_reused': result['reused'], 'next_argv': command,
                  'next_command': shlex.join(command), 'private_tty_required': True,
                  'services_started': False, 'business_ready': False}))
            return 0
        return subprocess.run(command, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}).returncode
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        code = str(error) if type(error) is ValueError and str(error).replace('_', '').isalnum() else 'GUIDED_START_FAILED'
        print(json.dumps({'error': code, 'business_ready': False}))
        return 2


if __name__ == '__main__': sys.exit(main())
