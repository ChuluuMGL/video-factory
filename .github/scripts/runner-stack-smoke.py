"""Cloud-only real-container proof of the no-web project runner."""
import json
import subprocess
import sys
from urllib.request import Request, urlopen

from video_factory.dispatch import template
from video_factory.production_setup import import_json
from video_factory.runner import status, stop_all


def smoke(stack, root):
    project = 'fs_brand'
    def command(action, *extra):
        result = subprocess.run([sys.executable, '-m', 'video_factory.cli', 'runner', action,
                                 '--stack-root', str(stack.root), '--project', project, *extra],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
        if result.returncode:
            print(json.dumps({'stage': 'runner_' + action, 'returncode': result.returncode}), file=sys.stderr)
            raise AssertionError('RUNNER_CLI_' + action.upper() + '_FAILED')
        return json.loads(result.stdout)
    def post(path, body, token=None):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        request = Request('http://127.0.0.1:' + str(stack.config['runtime_port']) + path,
                          data=json.dumps(body).encode(), headers=headers)
        with urlopen(request, timeout=10) as response:
            return json.load(response)
    try:
        reviewed = command('plan')
        result = command('apply', '--expect-plan', reviewed['plan_sha256'])
        assert result['status'] == 'running' and result['published_ports'] == [], result
        assert command('status')['status'] == 'running' and status(stack, project)['status'] == 'running'
        admin = post('/v1/login', {'name': 'admin', 'password': 'cloud-stack-fixture-password'})['token']
        cap = post('/v1/automation/execution-keys', {'project': project, 'ttl_hours': 1}, admin)
        draft = template(project, 'vfPrivateRunnerKey')
        draft['id'] = 'VfPrivateRunner001'
        draft['nodes'][0].update(type='n8n-nodes-base.manualTrigger', typeVersion=1, parameters={})
        credential = [{'id': 'vfPrivateRunnerKey',
                       'name': draft['nodes'][1]['credentials']['httpHeaderAuth']['name'],
                       'type': 'httpHeaderAuth',
                       'data': {'name': 'Authorization', 'value': 'Bearer ' + cap['token']}}]
        for name, value, kind in [('runner-key', credential, 'credentials'),
                                  ('runner-workflow', draft, 'workflow')]:
            import_json(stack, value, name, kind)
        execution = subprocess.run(['docker', 'compose', '--project-directory', str(stack.root),
                                    '-f', str(stack.root / 'compose.json'), 'run', '--rm', '--no-deps',
                                    '-e', 'N8N_RUNNERS_BROKER_PORT=5689', 'n8n', 'execute',
                                    '--id=VfPrivateRunner001', '--rawOutput'],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
        if execution.returncode:
            print(json.dumps({'stage': 'private_runner_n8n', 'returncode': execution.returncode}), file=sys.stderr)
            raise AssertionError('PRIVATE_RUNNER_N8N_EXECUTION_FAILED')
        output = execution.stdout.decode()
        assert '"idle"' in output and '"provider_requests": 0' in output, 'PRIVATE_RUNNER_DISPATCH_FAILED'
        post('/v1/automation/revoke', {'key_id': cap['key_id']}, admin)
        return {'status': 'PASS', 'published_ports': [], 'n8n_approved_dispatch': 'PASS',
                'paid_model_requests': 0, 'human_acceptance': 'not_run'}
    finally:
        stop_all(stack)
