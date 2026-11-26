"""Explicit user-service generation and controls for the existing local runner."""
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys

from .runtime_store import RuntimeFault, canonical, exclusive_write, identifier, private_file


def command(args):
    for path in (args.root, args.plan, args.password_file):
        if not path.is_absolute() or path.resolve() != path or any(ord(c) < 32 for c in str(path)):
            raise RuntimeFault('LOCAL_SERVICE_ABSOLUTE_PATH_REQUIRED')
    private_file(args.plan)
    private_file(args.password_file)
    import re
    if not re.fullmatch('[0-9a-f]{64}', args.expect_plan):
        raise RuntimeFault('LOCAL_SERVICE_APPROVED_PLAN_REQUIRED')
    if not 1024 <= args.port <= 65535:
        raise RuntimeFault('PORT_INVALID')
    from .local_run import runtime_limit
    runtime_limit(None, args.max_runtime)
    result = [sys.executable, '-I', '-m', 'video_factory.cli', 'local-run', 'start',
              '--root', str(args.root), '--plan', str(args.plan), '--password-file', str(args.password_file),
              '--expect-plan', args.expect_plan, '--port', str(args.port)]
    if args.max_runtime is not None:
        result.extend(['--max-runtime', str(args.max_runtime)])
    return result


def template(platform, name, argv):
    identifier(name)
    label = 'com.video-factory.' + name
    if platform == 'launchd':
        return plistlib.dumps({'Label': label, 'ProgramArguments': argv, 'RunAtLoad': True,
                               'KeepAlive': False, 'Umask': 0o077})
    # systemd does not invoke a shell. Disable unit specifier and environment
    # expansion inside paths and escape the two characters special in quotes.
    def quote(arg):
        arg = arg.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$')
        return '"' + arg + '"'
    return ('[Unit]\nDescription=Video Factory local engine\n\n[Service]\n'
            'Type=simple\nUMask=0077\nExecStart=' + ' '.join(map(quote, argv)) +
            '\nRestart=no\nTimeoutStopSec=180\n\n[Install]\nWantedBy=default.target\n').encode()


def manager(platform, action, name):
    identifier(name)
    if platform == 'launchd':
        uid = 'gui/' + str(os.getuid())
        label = 'com.video-factory.' + name
        plist = Path.home() / 'Library/LaunchAgents' / (label + '.plist')
        if action == 'start':
            private_file(plist)
            return ['/bin/launchctl', 'bootstrap', uid, str(plist)]
        return ['/bin/launchctl', {'stop': 'bootout', 'status': 'print'}[action], uid + '/' + label]
    return ['systemctl', '--user', 'is-active' if action == 'status' else action,
            'video-factory-' + name + '.service']


def register(commands):
    root = commands.add_parser('local-service', help='render or control an explicit user service')
    actions = root.add_subparsers(dest='service_action', required=True)
    for action in ('render', 'start', 'stop', 'status'):
        p = actions.add_parser(action)
        p.add_argument('--name', required=True)
        p.add_argument('--platform', choices=('launchd', 'systemd'),
                       default='launchd' if sys.platform == 'darwin' else 'systemd')
        if action == 'render':
            for name in ('root', 'plan', 'password-file', 'output'):
                p.add_argument('--' + name, type=Path, required=True)
            p.add_argument('--expect-plan', required=True)
            p.add_argument('--port', type=int, default=8788)
            p.add_argument('--max-runtime', type=int)


def cli(args):
    try:
        identifier(args.name)
        if args.service_action == 'render':
            exclusive_write(args.output, template(args.platform, args.name, command(args)))
            value = {'state': 'rendered', 'enabled': False}
        else:
            proc = subprocess.run(manager(args.platform, args.service_action, args.name),
                                  capture_output=True, timeout=195, check=False)
            state = 'active' if proc.returncode == 0 else 'inactive_or_unavailable'
            if args.platform == 'launchd' and args.service_action == 'status' and proc.returncode == 0:
                state = 'active' if b'state = running' in proc.stdout else 'loaded_not_running'
            value = {'state': state if args.service_action == 'status' else
                     ('requested' if proc.returncode == 0 else 'manager_request_failed')}
            print(canonical(value))
            return 0 if proc.returncode == 0 else 2
        print(canonical(value))
        return 0
    except (OSError, RuntimeFault, subprocess.TimeoutExpired):
        print(canonical({'error': 'LOCAL_SERVICE_FAILED'}))
        return 2
