"""Cloud-only, read-only publication audit. Never execute inspected content.

Only allowlisted finding metadata leaves the temporary scanner directory.
A successful process means the inspection completed, not publication approval.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from audit_policy import reviewed_assertion, verdict

REPO = os.environ['GITHUB_REPOSITORY']
MODE = sys.argv[1]
OUT = Path('audit-results')
OUT.mkdir(exist_ok=True)
result = {'mode': MODE, 'repository': REPO, 'source_sha': os.environ['GITHUB_SHA'],
          'scanner': 'gitleaks 8.30.1', 'archive_depth': 0 if MODE == 'source' else 5, 'decode_depth': 2,
          'inspected': [], 'uninspected': [], 'errors': [], 'findings': [],
          'publication_approved': False}


def api(endpoint):
    p = subprocess.run(['gh', 'api', endpoint], capture_output=True)
    if p.returncode:
        raise RuntimeError('API_READ_FAILED')
    return json.loads(p.stdout)


def pages(endpoint, key=None):
    n = 1
    while True:
        data = api(endpoint + ('&' if '?' in endpoint else '?') + f'per_page=100&page={n}')
        rows = data[key] if key else data
        yield from rows
        if len(rows) < 100:
            return
        n += 1


def scan(path, label, kind='dir'):
    with tempfile.TemporaryDirectory() as temp:
        report = Path(temp) / 'raw.json'
        command = ['gitleaks', kind, str(path), '--redact=0', '--no-banner',
                   '--ignore-gitleaks-allow', '--log-level=error', '--no-color',
                   '--max-archive-depth=' + ('0' if MODE == 'source' else '5'),
                   '--max-decode-depth=2', '--report-format=json',
                   '--report-path', str(report)]
        if kind == 'git':
            command += ['--log-opts=--all']
        # Raw values exist only in the temporary cloud report for classification.
        # Never publish stdout/stderr or raw reports; they can contain private metadata.
        p = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           timeout=1200)
        if p.returncode not in (0, 1) or not report.exists():
            raise RuntimeError('SCANNER_FAILED')
        findings = json.loads(report.read_text())
        for hit in findings:
            # Classify in the isolated runner; never include actual values in receipts.
            secret = hit.get('Secret', '')
            if secret == 'NOT-A-REAL-CREDENTIAL':
                classification = 'explicit-synthetic-fixture'
            elif secret.startswith('/run/secrets/') and not any(c.isspace() for c in secret):
                classification = 'runtime-secret-file-reference'
            else:
                classification = 'requires-review'
            import re
            if classification == 'requires-review':
                fixture = '.github/scripts/container-stack-smoke.py'
                file = hit.get('File', '')
                lines = []
                if kind == 'git' and file == fixture and re.fullmatch('[0-9a-f]{40}', hit.get('Commit', '')):
                    lines = subprocess.check_output(
                        ['git', 'show', hit['Commit'] + ':' + fixture], text=True).splitlines()
                elif kind == 'dir' and Path(file).resolve() == (Path(path) / fixture).resolve():
                    lines = Path(file).read_text().splitlines()
                number = hit.get('StartLine', 0)
                if 0 < number <= len(lines) and reviewed_assertion(hit.get('RuleID'), fixture, lines[number - 1]):
                    classification = 'reviewed-test-assertion'
            prefix = hit.get('Match', '').partition(secret)[0] if secret else ''
            label_match = re.search(r'([A-Za-z_][A-Za-z0-9_]{0,40})[\\"\']*\s*[:=]\s*[\\"\']*\s*$', prefix)
            prefix = label_match.group(1) if label_match else '[not recorded]'
            result['findings'].append({'object': label,
                'rule': hit.get('RuleID'), 'file': hit.get('File'),
                'line': hit.get('StartLine'), 'commit': hit.get('Commit'),
                'classification': classification, 'assignment_prefix': prefix,
                'value_length': len(secret), 'value_is_hex': bool(re.fullmatch('[0-9a-fA-F]+', secret))})
        if p.stderr.strip():
            import re
            diagnostic = p.stderr.decode(errors='replace')
            for hit in findings:
                for field in ('Secret', 'Match', 'Email', 'Author'):
                    value = hit.get(field, '')
                    if len(value) >= 4:
                        diagnostic = diagnostic.replace(value, '[redacted]')
            diagnostic = re.sub(r'[A-Za-z0-9_+/=-]{28,}', '[redacted]', diagnostic)
            result.setdefault('scanner_diagnostics', []).append({'object': label, 'text': diagnostic[:1800]})
            raise RuntimeError('SCANNER_REPORTED_ERROR')
        return len(findings)


def download_scan(endpoint, name, label, expected_digest=None):
    try:
        with tempfile.TemporaryDirectory() as temp:
            # name is metadata, never a shell command or an extraction destination.
            target = Path(temp) / Path(name).name
            with target.open('wb') as dest:
                headers = ['-H', 'Accept: application/octet-stream'] if '/releases/assets/' in endpoint else []
                p = subprocess.run(['gh', 'api', *headers, endpoint],
                                   stdout=dest, stderr=subprocess.DEVNULL, timeout=900)
            if p.returncode:
                raise RuntimeError('DOWNLOAD_FAILED')
            h = hashlib.sha256()
            with target.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    h.update(chunk)
            digest = h.hexdigest()
            if expected_digest and expected_digest != 'sha256:' + digest:
                raise RuntimeError('DIGEST_MISMATCH')
            count = scan(target, label)
            result['inspected'].append({'object': label, 'bytes': target.stat().st_size,
                                        'sha256': digest, 'findings': count})
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        result['errors'].append({'object': label, 'type': type(error).__name__,
                                 'reason': str(error) if isinstance(error, RuntimeError) else 'TIMEOUT'})
    save()


def save():
    (OUT / f'{MODE}.json').write_text(json.dumps(result, indent=2))


try:
    if MODE == 'source':
        count = scan('.', 'all-fetched-git-history', 'git')
        result['inspected'].append({'object': 'all-fetched-git-history', 'findings': count})
        # Current working tree includes newly added files; exclude .git and scanner outputs.
        with tempfile.TemporaryDirectory() as temp:
            import shutil
            for name in subprocess.check_output(['git', 'ls-files', '-z']).decode().split('\0'):
                if name:
                    target = Path(temp) / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(name, target)
            count = scan(temp, 'current-tracked-tree')
            result['inspected'].append({'object': 'current-tracked-tree', 'findings': count})
        emails = set(subprocess.check_output(['git', 'log', '--all', '--format=%ae%n%ce']).decode().splitlines())
        result['author_metadata'] = {'unique_email_count': len(emails),
            'non_github_noreply_count': sum('noreply.github.com' not in email for email in emails)}
    elif MODE == 'releases':
        for release in pages(f'repos/{REPO}/releases'):
            for asset in release['assets']:
                download_scan(f"repos/{REPO}/releases/assets/{asset['id']}", asset['name'],
                              f"{release['tag_name']}/{asset['name']}", asset.get('digest'))
    elif MODE == 'ci':
        for artifact in pages(f'repos/{REPO}/actions/artifacts', 'artifacts'):
            label = f"artifact:{artifact['id']}:{artifact['name']}"
            if artifact['expired'] or artifact['size_in_bytes'] > 32 * 1024 * 1024:
                result['uninspected'].append({'object': label,
                    'reason': 'expired' if artifact['expired'] else 'large-ci-artifact-inventory-only',
                    'bytes': artifact['size_in_bytes']})
                continue
            download_scan(f"repos/{REPO}/actions/artifacts/{artifact['id']}/zip", 'artifact.zip', label)
        for run in pages(f'repos/{REPO}/actions/runs', 'workflow_runs'):
            label = f"run:{run['id']}"
            if run['status'] != 'completed':
                result['uninspected'].append({'object': label, 'reason': 'run-not-completed'})
                continue
            download_scan(f"repos/{REPO}/actions/runs/{run['id']}/logs", 'logs.zip', label)
    else:
        raise RuntimeError('UNKNOWN_SCOPE')
except Exception as error:
    result['errors'].append({'object': MODE, 'type': type(error).__name__,
                             'reason': str(error) if isinstance(error, RuntimeError) else 'INSPECTION_ERROR'})
finally:
    result['status'], result['review_required'], exit_code = verdict(result)
    save()
    import base64, zlib
    # Small, redacted report survives even when Actions artifact storage is full.
    payload = base64.b64encode(zlib.compress(json.dumps(result).encode())).decode()
    print('AUDIT_REPORT_BASE64_ZLIB=' + payload)
    print(json.dumps({'mode': MODE, 'status': result['status'],
                      'inspected': len(result['inspected']), 'findings': len(result['findings']),
                      'uninspected': len(result['uninspected']), 'errors': len(result['errors']),
                      'review_required': result['review_required'],
                      'publication_approved': False}))
sys.exit(exit_code)
