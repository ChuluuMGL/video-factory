"""Run the delivered launcher in its independent venv, not the test interpreter."""
import json
from pathlib import Path
import select
import subprocess
import sys
import time

root = Path(sys.argv[1]).resolve()
launcher = str(root / 'vf-local')

def command(*args):
    result = subprocess.run([launcher, *args], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)

def wait_revision(revision):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        value = command('status')
        if len(value['tasks']) == revision:
            assert value['tasks'][-1]['state'] == 'awaiting_script_review'
            return value
        time.sleep(.2)
    raise AssertionError('Packaged scheduler did not produce the expected script')

states = []
for revision in (1, 2):
    process = subprocess.Popen([launcher, 'up', '--port', '0'], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([process.stdout], [], [], 20)[0]
        ready = json.loads(process.stdout.readline())
        assert ready['scope'] == 'local_fake_only' and ready['n8n_required'] is False
        state = wait_revision(revision)
        states.append({'revision': revision, 'state': state['tasks'][-1]['state']})
        if revision == 2:
            assert '减少产品特写' in state['tasks'][-1]['input']
        decision = 'reject' if revision == 1 else 'accept'
        receipt = command('review', '--expected-revision', str(revision), '--request-id', f'packaged-review-{revision}',
                          '--decision', decision, '--feedback', '减少产品特写')
        assert receipt['state'] == ('rejected' if revision == 1 else 'ready')
    finally:
        process.terminate()
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stdout + stderr
    if revision == 1:
        command('plan', '--expected-revision', '1', '--request-id', 'packaged-revision-2')

final = command('status')
assert [r['state'] for r in final['tasks']] == ['rejected', 'ready']
assert len(final['reviews']) == 2 and final['real_model_calls'] == final['production_writes'] == 0
assert final['video_generation'] == 'not_enabled'
evidence = Path('.runtime/local-kernel-evidence')
evidence.mkdir(parents=True, exist_ok=True)
(evidence / 'installed-launcher.json').write_text(json.dumps({
    'status': 'PASS', 'package_kind': 'installed-wheel-private-venv', 'n8n_required': False,
    'script_revisions': 2, 'review_events': 2, 'restart_preserved': True,
    'real_model_calls': 0, 'production_writes': 0, 'video_generation': 'not_enabled',
    'human_installation_accepted': False, 'states': states}, indent=2))
print('PASS: installed wheel + private launcher, fake script/reject/revise/accept, stop/restart; no n8n')
