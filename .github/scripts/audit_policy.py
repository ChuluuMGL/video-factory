"""Decide the privacy check result without exposing scanned values."""
import hashlib


def reviewed_fixture_command(rule, relative_file, line):
    # Reviewed n8n synthetic-credential export command, not a file allowlist.
    # Any edit to that line removes the exception, including new credential values.
    return (rule == 'generic-api-key'
            and relative_file == '.github/scripts/container-stack-smoke.py'
            and hashlib.sha256(line.encode()).hexdigest()
            == 'f849d24d6c3517a0637702624b7cf1cb9acae7b8004015f1cabdfde9e7d1bf4c')


def reviewed_adversarial_fixture(rule, path, commit, line, secret_digest):
    # PR 102 uses one fixed fake credential solely as a redaction sentinel.
    # Review this exact historical finding; keep scanning both history and tree.
    return (rule == 'generic-api-key'
            and path == 'packages/video-factory-core/tests/test_workbench_actions_adversarial.py'
            and commit == '396c1f63c2b6bf3f819eda70b8e2bcde4fe42bce' and line == 186
            and secret_digest == 'eac161ce751c2defa3934af3c113722803cf61b4c09a1c27ab645e5425ac9d1f')


def verdict(report):
    reviewed_classes = {'explicit-synthetic-fixture', 'runtime-secret-file-reference',
                        'reviewed-test-command', 'reviewed-adversarial-fixture'}
    review_count = sum(hit.get('classification') not in reviewed_classes
                       for hit in report.get('findings', []))
    if report.get('errors'):
        return 'inspection_incomplete', review_count, 2
    if review_count:
        return 'review_required', review_count, 3
    return 'inspection_completed', 0, 0
