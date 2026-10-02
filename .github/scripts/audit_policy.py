"""Decide the privacy check result without exposing scanned values."""
import hashlib


def reviewed_assertion(rule, relative_file, line):
    # Reviewed n8n synthetic-credential round-trip assertion, not a file allowlist.
    # Any edit to that line removes the exception, including new credential values.
    return (rule == 'generic-api-key'
            and relative_file == '.github/scripts/container-stack-smoke.py'
            and hashlib.sha256(line.encode()).hexdigest()
            == 'f849d24d6c3517a0637702624b7cf1cb9acae7b8004015f1cabdfde9e7d1bf4c')


def verdict(report):
    reviewed_classes = {'explicit-synthetic-fixture', 'runtime-secret-file-reference',
                        'reviewed-test-assertion'}
    review_count = sum(hit.get('classification') not in reviewed_classes
                       for hit in report.get('findings', []))
    if report.get('errors'):
        return 'inspection_incomplete', review_count, 2
    if review_count:
        return 'review_required', review_count, 3
    return 'inspection_completed', 0, 0
