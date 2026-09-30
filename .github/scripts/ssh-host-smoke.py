"""Actual SSH, pinned host key, installation and bad-host-key rejection on CI."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


def execute(argv):
    return subprocess.run(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=240)


with tempfile.TemporaryDirectory(prefix="vf-ssh-", dir="/root") as temp:
    root=Path(temp);sshport=port();runtimeport=port()
    for name in ('host','identity','wrong'):
        result=execute(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(root/name)])
        assert result.returncode==0,'SSH_KEYGEN_FAILED'
    authorized=root/'authorized_keys';authorized.write_bytes((root/'identity.pub').read_bytes());authorized.chmod(0o600)
    known=root/'known_hosts'
    known.write_text(f'[127.0.0.1]:{sshport} '+(root/'host.pub').read_text());known.chmod(0o600)
    incorrect=root/'wrong_known_hosts'
    incorrect.write_text(f'[127.0.0.1]:{sshport} '+(root/'wrong.pub').read_text());incorrect.chmod(0o600)
    config=root/'sshd_config'
    config.write_text(f'''Port {sshport}
ListenAddress 127.0.0.1
HostKey {root}/host
AuthorizedKeysFile {authorized}
PidFile {root}/sshd.pid
PermitRootLogin prohibit-password
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
UsePAM yes
AllowUsers root
LogLevel VERBOSE
''')
    Path('/run/sshd').mkdir(exist_ok=True)
    server_log=(root/'sshd.log').open('wb')
    server=subprocess.Popen(['/usr/sbin/sshd','-D','-e','-f',str(config)],stdout=subprocess.DEVNULL,stderr=server_log)
    try:
        for _ in range(100):
            if server.poll() is not None:raise RuntimeError('SSHD_EXITED')
            try:
                with socket.create_connection(('127.0.0.1',sshport),timeout=.1):break
            except OSError:time.sleep(.05)
        wheelhouse=root/'release';wheelhouse.mkdir(mode=0o700)
        for source in (Path(sys.argv[1]),Path(sys.argv[2])):
            for wheel in source.glob('*.whl'):shutil.copyfile(wheel,wheelhouse/wheel.name)
        password=root/'password';password.write_text('ci-ssh-fixture-password');password.chmod(0o600)
        common=['--host','127.0.0.1','--user','root','--identity',str(root/'identity'),'--ssh-port',str(sshport),
                '--root',str(root/'customer'),'--deployment','ssh-customer','--port',str(runtimeport)]
        vf=str(Path(sys.executable).with_name('vfctl'))
        assert Path(vf).is_file()
        rejected=execute([vf,'host','preflight',*common,'--known-hosts',str(incorrect)])
        assert rejected.returncode!=0,'BAD_HOST_KEY_ACCEPTED'
        preflight=execute([vf,'host','preflight',*common,'--known-hosts',str(known)])
        if preflight.returncode:
            diagnostic=execute(['ssh','-vv','-T','-p',str(sshport),'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
                                '-o','IdentitiesOnly=yes','-o','UserKnownHostsFile='+str(known),'-i',str(root/'identity'),
                                'root@127.0.0.1','python3 -c '+__import__('shlex').quote('import sys; print(sys.version)')])
            raise RuntimeError('FIXTURE_SSH_DIAGNOSTIC: '+diagnostic.stderr.decode()[-5000:]+' STDOUT '+diagnostic.stdout.decode()[-1000:]+' SERVER '+(root/'sshd.log').read_text()[-3000:])
        install=[vf,'host','install',*common,'--known-hosts',str(known),'--wheelhouse',str(wheelhouse),'--password-file',str(password)]
        for _ in range(2):
            result=execute(install)
            assert result.returncode==0,result.stdout.decode()
            assert json.loads(result.stdout)['installed']
        print(json.dumps({'status':'PASS','scope':'isolated_cloud_ssh_host_bootstrap','pinned_host_key':True,
                          'wrong_host_key_rejected':True,'remote_install_and_resume':True,'provider_requests':0,
                          'real_customer_host':'not_run','systemd_boot':'not_run'}))
    finally:
        server.terminate();server.wait(timeout=10);server_log.close()
