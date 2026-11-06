"""Cloud-only delivered business launcher: configure, resume, preserve and reject drift."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(sys.argv[1]).resolve()
assert not (root / 'data').exists(), 'business mode must not initialize a fake ledger'
assert json.loads((root / 'install.json').read_text())['mode'] == 'business'
with tempfile.TemporaryDirectory() as tmp:
    private = Path(tmp).resolve()
    def save(name, value):
        path = private / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as out: out.write(value)
        return str(path)
    plan = {'deployment': 'smoke', 'project': 'brand_one', 'billing_owner': 'owner',
            'products': [{'sku_id': 'sku_one', 'name': 'Synthetic product', 'truth_source': 'Synthetic specifications'}],
            'app_id': 'cli_fixture', 'script_model': 'deepseek-flash', 'video': None,
            'binding': {'tenant_key': 'tenant_fixture', 'base_token': 'bascnFixture', 'table_id': 'tblFixture',
                        'fields': {'task': 'fldTask', 'sku_id': 'fldSkuId', 'script': 'fldScript', 'source_revision': 'fldVersion'},
                        'submitters': ['ou_fixture'], 'reviewers': ['ou_fixture']}}
    plan_path = save('plan.json', json.dumps(plan))
    password = save('password', 'synthetic-password-long')
    app = save('app', 'synthetic-app-secret')
    script = save('script', 'synthetic-script-secret')
    base = [str(root / 'vf-setup'), '--plan', plan_path, '--password-file', password]
    result = subprocess.run(base + ['--app-secret-file', app, '--script-key-file', script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    first = json.loads(result.stdout); assert first['configuration_saved'] and not first['reused']
    result = subprocess.run(base, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    second = json.loads(result.stdout); assert second['reused'] and not second['paid_execution_enabled']
    assert not second['feishu_binding_verified']
    # A synthetic binding, explicitly not a live Feishu acceptance claim.
    installed_python = str(root / 'environment/bin/python')
    bind_fixture = "from pathlib import Path; import json,sys; from video_factory.runtime_store import RuntimeStore; from video_factory.feishu_bridge import save; s=RuntimeStore(Path(sys.argv[1])); p=json.load(open(sys.argv[2])); db=s.connect(); c=db.__enter__(); save(c,'feishu:binding:'+p['project'],p['binding']); db.__exit__(None,None,None)"
    subprocess.run([installed_python, '-I', '-c', bind_fixture, str(root / 'business'), plan_path], check=True, timeout=30)
    run_args = ['--plan', plan_path, '--password-file', password, '--port', '0']
    inspected = subprocess.run([str(root / 'vf-run'), 'inspect', *run_args], capture_output=True, text=True, timeout=30)
    assert inspected.returncode == 0, inspected.stdout + inspected.stderr
    start_hash = json.loads(inspected.stdout)['plan_sha256']
    import signal
    for attempt in range(2):
        service = subprocess.Popen([str(root / 'vf-run'), 'start', *run_args, '--expect-plan', start_hash], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            import select
            assert select.select([service.stdout], [], [], 15)[0], 'startup readiness timeout'
            ready = json.loads(service.stdout.readline()); assert not ready['execution_enabled']
            client = [str(root / 'vf-agent'), '--connection', ready['connection']]
            status = subprocess.run([*client, 'status'], capture_output=True, text=True, timeout=30)
            assert status.returncode == 0 and json.loads(status.stdout)['state'] == 'read_only', status.stdout
            denied = subprocess.run([*client, 'status', '--json', '{"project":"another"}'], capture_output=True, text=True, timeout=30)
            assert denied.returncode == 2 and 'AGENT_FIELDS_INVALID' in denied.stdout
        finally:
            service.send_signal(signal.SIGTERM)
            out, err = service.communicate(timeout=10)
        assert service.returncode == 0, out + err
        assert json.loads(out)['session_grants_revoked']
        assert not Path(ready['connection']).exists()
    plan['products'][0]['name'] = 'changed'; Path(plan_path).write_text(json.dumps(plan))
    result = subprocess.run(base, capture_output=True, text=True, timeout=30)
    assert result.returncode == 2 and 'LOCAL_SETUP_CHANGED_USE_NEW_PROJECT' in result.stdout
    assert (root / 'business/runtime.sqlite3').is_file()
    receipt = {'installed_business_launcher': True, 'new_configuration': True, 'restart_resume': True,
               'changed_plan_rejected': True, 'fake_ledger_created': False, 'model_calls': 0,
               'installed_run_and_agent': True, 'readonly_start_stop_restart': True, 'cross_project_client_rejected': True,
               'session_credentials_removed': True, 'live_feishu_tested': False, 'human_installation_tested': False}
    target = Path('.runtime/local-kernel-evidence'); target.mkdir(parents=True, exist_ok=True)
    (target / 'business-install.json').write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt))
