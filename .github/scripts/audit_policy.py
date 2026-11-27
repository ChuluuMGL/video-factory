"""Decide the privacy check result without exposing scanned values."""
import hashlib


def reviewed_fixture_command(rule, relative_file, line):
    # Reviewed n8n synthetic-credential export command, not a file allowlist.
    # Any edit to that line removes the exception, including new credential values.
    return (rule == 'generic-api-key'
            and relative_file == '.github/scripts/container-stack-smoke.py'
            and hashlib.sha256(line.encode()).hexdigest()
            == 'f849d24d6c3517a0637702624b7cf1cb9acae7b8004015f1cabdfde9e7d1bf4c')


HISTORICAL_FIXTURES = {
    ('packages/video-factory-core/tests/test_workbench_actions_adversarial.py',
     '396c1f63c2b6bf3f819eda70b8e2bcde4fe42bce', 186):
        '3cc8c0993a023f7aa9bd010b92bd63c2' '723e51b25e15be302dc61bb154c1e359',
    ('.github/scripts/audit_policy.py', 'dd8d319bf71e1127ae7d2607921d6fab0cffbefe', 20):
        '453226cad4b2d91bbe7058fed48fd5e6' 'd5f5843a5100e527134e32b4ff953d59',
}


def reviewed_historical_fixture(rule, path, commit, number, read_line):
    # Match the exact PR 102 synthetic fixture line or its archived SHA-256
    # fingerprint. Neither classification applies to future commits.
    expected = HISTORICAL_FIXTURES.get((path, commit, number))
    return (rule == 'generic-api-key' and expected is not None
            and hashlib.sha256(read_line().encode()).hexdigest() == expected)


def verdict(report):
    reviewed_classes = {'explicit-synthetic-fixture', 'runtime-secret-file-reference',
                        'reviewed-test-command', 'reviewed-historical-fixture'}
    review_count = sum(hit.get('classification') not in reviewed_classes
                       for hit in report.get('findings', []))
    if report.get('errors'):
        return 'inspection_incomplete', review_count, 2
    if review_count:
        return 'review_required', review_count, 3
    return 'inspection_completed', 0, 0
