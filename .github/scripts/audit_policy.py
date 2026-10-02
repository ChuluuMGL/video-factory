"""Decide the privacy check result without exposing scanned values."""


def verdict(report):
    reviewed_classes = {'explicit-synthetic-fixture', 'runtime-secret-file-reference'}
    review_count = sum(hit.get('classification') not in reviewed_classes
                       for hit in report.get('findings', []))
    if report.get('errors'):
        return 'inspection_incomplete', review_count, 2
    if review_count:
        return 'review_required', review_count, 3
    return 'inspection_completed', 0, 0
