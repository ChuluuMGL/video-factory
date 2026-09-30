"""Run inside an empty network namespace with a fresh Docker engine/image store."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
from cryptography.fernet import Fernet
from video_factory.stack import Stack, run

bundle,wheels,cli=Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3]
# Compare with the already hash-verified delivered CLI manifest, not a
# hard-coded previous release or the container's own reported version.
expected_version=json.loads((wheels.parent/'manifest.json').read_text())['version']
assert run([cli,'--version']).decode().strip()==expected_version
store=sys.argv[4]
assert store in ('classic','containerd')
checksum=hashlib.sha256((bundle/'manifest.json').read_bytes()).hexdigest()
# This checksum is CI's trusted export receipt, checked by the shell driver.
with tempfile.TemporaryDirectory(prefix='vf-airgap-',dir='/root') as temporary:
    root=Path(temporary);daemon=None;stacks=[]
    try:
        subprocess.run(['ip','link','set','lo','up'],check=True)
        assert not subprocess.check_output(['ip','route','show','default']).strip()
        config=root/'daemon.json';config.write_text(json.dumps({'features':{'containerd-snapshotter':store=='containerd'}}))
        with (root/'docker.log').open('wb') as log:
            daemon=subprocess.Popen(['dockerd','--config-file',str(config),'--data-root',str(root/'docker'),
                '--exec-root',str(root/'exec'),'--pidfile',str(root/'docker.pid')]+
                (['--storage-driver=overlay2'] if store=='classic' else []),stdout=log,stderr=log)
        for _ in range(90):
            check=subprocess.run(['docker','info'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            if check.returncode==0:break
            if daemon.poll() is not None:raise AssertionError((root/'docker.log').read_text()[-6000:])
            time.sleep(1)
        else:raise AssertionError('ISOLATED_DOCKER_NOT_READY')
        assert run(['docker','image','ls','-q']).strip()==b''
        engine=json.loads(run(['docker','info','--format','{{json .}}']))
        snapshotter=dict(engine.get('DriverStatus',[])).get('driver-type')=='io.containerd.snapshotter.v1'
        assert snapshotter==(store=='containerd'),engine.get('DriverStatus')
        password=root/'password';password.write_text('offline-cloud-fixture-password');password.chmod(0o600)
        target=root/'stack'
        command=[cli,'stack','install','--root',str(target),'--deployment','offline-fixture','--wheelhouse',str(wheels),
                 '--password-file',str(password),'--runtime-port','18787','--n8n-port','15678',
                 '--image-bundle',str(bundle),'--image-manifest-sha256',checksum]
        # Wrong trust anchor must fail before creating the stack or loading any image.
        wrong=command.copy();wrong[-1]='0'*64
        result=subprocess.run(wrong,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        assert result.returncode==2 and b'MANIFEST_CHANGED' in result.stdout and not target.exists()
        assert run(['docker','image','ls','-q']).strip()==b''
        # Keep synthetic CI failures actionable without exposing generated
        # database passwords. The product intentionally returns only safe codes.
        tracer = r'''
import pathlib, subprocess, sys
original = subprocess.run
def traced(command, *args, **kwargs):
    result = original(command, *args, **kwargs)
    if result.returncode and isinstance(command, list) and command[0] == 'docker':
        message = str(command) + '\\n' + repr(result.stdout) + '\\n' + repr(result.stderr)
        for secret in pathlib.Path(sys.argv[sys.argv.index('--root')+1]).glob('secrets/*'):
            if secret.is_file():
                value = secret.read_text().strip()
                if value: message = message.replace(value, '<redacted>')
        print('SYNTHETIC_DOCKER_FAILURE '+message[-6000:], file=sys.stderr)
    return result
subprocess.run = traced
from video_factory.cli import main
raise SystemExit(main())
'''
        first=subprocess.run([str(Path(cli).parent/'python'),'-I','-c',tracer,*command[1:]],capture_output=True,timeout=900)
        if first.returncode:
            print(first.stdout.decode(errors='replace'),file=sys.stderr)
            print(first.stderr.decode(errors='replace'),file=sys.stderr)
            raise AssertionError('OFFLINE_FIRST_INSTALL_FAILED')
        assert json.loads(first.stdout)['infrastructure_ready']
        stack=Stack(target);stacks.append(stack)
        # Exercise the same reviewed Setup adapter used by the terminal wizard.
        answers=json.loads(Path('packages/video-factory-core/examples/setup/answers.json').read_text())
        answers.update({'deployment.id':'offline-fixture','project.base_mode':'bind','project.base_target':'bascnFixture'})
        answerfile=root/'answers.json';answerfile.write_text(json.dumps(answers));answerfile.chmod(0o600)
        session=root/'setup.json'
        run([cli,'setup','--session',str(session),'--json'])
        run([cli,'setup','--session',str(session),'--answers',str(answerfile),'--expect-revision','0','--json'])
        common=['--session',str(session),'--root',str(target),'--host',answers['deployment.host'],
            '--wheelhouse',str(wheels),'--image-bundle',str(bundle),'--image-manifest-sha256',checksum]
        reviewed=json.loads(run([cli,'setup-deploy','plan',*common]))
        assert reviewed['target']['image_bundle_manifest_sha256']==checksum
        applied=json.loads(run([cli,'setup-deploy','apply',*common,'--expect-plan',reviewed['execution_sha256'],
            '--password-file',str(password)],timeout=900))
        assert applied['status']=='infrastructure_and_project_draft_ready' and not applied['business_ready']
        key_before=(target/'secrets/runtime_master').read_bytes()
        assert json.loads(run(command,timeout=900))['infrastructure_ready']
        assert (target/'secrets/runtime_master').read_bytes()==key_before
        with urlopen('http://127.0.0.1:18787/healthz',timeout=10) as response:assert response.status==200
        assert stack.compose('exec','-T','runtime','vfctl','--version').decode().strip()==expected_version
        stack.stop();assert stack.up()['infrastructure_ready']
        key=Fernet.generate_key();backup=root/'backup.vfb';stack.backup(backup,key)
        restored_root=root/'restored';restored_root.mkdir(mode=0o700)
        restored=Stack.restore(backup,restored_root,key);stacks.append(restored)
        assert restored.build()['offline_image_reused']
        assert restored.up()['infrastructure_ready']
        assert (restored_root/'secrets/runtime_master').read_bytes()==key_before
        print(json.dumps({'status':'PASS','scope':'fresh_docker_daemon_without_external_network','empty_image_store':True,'image_store':store,
            'default_route_absent':True,'wrong_digest_rejected_before_load':True,'offline_install':'PASS','same_release_resume':'PASS',
            'reviewed_setup_apply':'PASS','restart':'PASS','cold_restore':'PASS','runtime_version':expected_version,'manifest_sha256':checksum,
            'paid_requests':0,'real_ecs_acceptance':'not_run'}),flush=True)
    finally:
        for stack in reversed(stacks):
            try:stack.compose('down','--timeout','15',timeout=90)
            except Exception:pass
        if daemon is not None:
            daemon.terminate()
            try:daemon.wait(timeout=45)
            except subprocess.TimeoutExpired:daemon.kill();daemon.wait()
