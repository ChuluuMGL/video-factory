"""Exercise the installed Feishu CLI through a real private PTY and synthetic OAuth."""
import json
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path('packages/video-factory-core/tests').resolve()))
from setup_connect_fixture import SetupFixture


def terminal_case(answer):
    with tempfile.TemporaryDirectory(prefix='vf-setup-terminal-') as temporary:
        root = Path(temporary)
        fixture = SetupFixture(root)
        master = slave = None
        process = None
        try:
            fixture.wire.granted = True
            for name, value in (('app-secret', 'synthetic-app-secret'), ('admin-token', fixture.admin)):
                path = root / name
                path.write_text(value)
                path.chmod(0o600)
            wrapper = (
                "import sys; from video_factory.feishu_oauth import DeviceOAuth; "
                "from video_factory.feishu_client import FeishuClient; "
                "DeviceOAuth.accounts_origin=DeviceOAuth.token_origin=FeishuClient.origin=sys.argv[1]; "
                "from video_factory.cli import main; raise SystemExit(main(sys.argv[2:]))"
            )
            command = [sys.executable, '-c', wrapper, fixture.wire.oauth.accounts_origin,
                       'setup-feishu', 'terminal', '--root', str(root / 'state'),
                       '--session', str(root / 'setup-connection.json'),
                       '--token-file', str(root / 'admin-token'), '--project', fixture.project,
                       '--app-id', 'cli_fixture', '--app-secret-file', str(root / 'app-secret'),
                       '--seconds', '30']
            master, slave = pty.openpty()
            env = {**os.environ, 'VF_CONTAINER_MODE': '1'}
            process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave,
                                       env=env, close_fds=True)
            os.close(slave)
            slave = None
            output = bytearray()
            submitted = False
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                readable, _, _ = select.select([master], [], [], 0.5)
                if readable:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        chunk = b''
                    if chunk:
                        output.extend(chunk)
                        if not submitted and '确认上述飞书操作'.encode() in output:
                            os.write(master, (answer + '\n').encode())
                            submitted = True
                if process.poll() is not None:
                    break
            if process.poll() is None:
                process.kill()
                raise AssertionError('TERMINAL_BINDING_TIMEOUT')
            rendered = output.decode(errors='replace')
            assert submitted and '计划校验值' in rendered, rendered[-1000:]
            assert fixture.admin not in rendered
            assert fixture.wire.user_token not in rendered
            assert 'synthetic-app-secret' not in rendered
            bound = fixture.service.status(fixture.admin, fixture.session.snapshot())['binding_matches_draft']
            assert bound is (answer == 'yes')
            assert process.returncode == (0 if answer == 'yes' else 2), (process.returncode, rendered[-1000:])
            assert fixture.wire.starts == 1 and fixture.wire.polls >= 1
            return {'answer': answer, 'bound': bound, 'status': 'PASS'}
        finally:
            if process and process.poll() is None:
                process.kill()
                process.wait()
            if slave is not None:
                os.close(slave)
            if master is not None:
                os.close(master)
            fixture.close()


if __name__ == '__main__':
    print(json.dumps({'private_terminal': [terminal_case('no'), terminal_case('yes')],
                      'real_feishu_authorization': 'not_run', 'model_calls': 0}))
