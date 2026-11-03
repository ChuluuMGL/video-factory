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
    plan['products'][0]['name'] = 'changed'; Path(plan_path).write_text(json.dumps(plan))
    result = subprocess.run(base, capture_output=True, text=True, timeout=30)
    assert result.returncode == 2 and 'LOCAL_SETUP_CHANGED_USE_NEW_PROJECT' in result.stdout
    assert (root / 'business/runtime.sqlite3').is_file()
    receipt = {'installed_business_launcher': True, 'new_configuration': True, 'restart_resume': True,
               'changed_plan_rejected': True, 'fake_ledger_created': False, 'model_calls': 0,
               'live_feishu_tested': False, 'human_installation_tested': False}
    target = Path('.runtime/local-kernel-evidence'); target.mkdir(parents=True, exist_ok=True)
    (target / 'business-install.json').write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt))
