# Shadow-copy update recovery

This change does not authorize a run, deployment, permission grant or retry.

## No-op comparisons

`CopyOnlyWeekWriter` uses the same semantic comparison before PUT and during
readback. For text fields, omitted / null / empty string / empty rich-text array
are equivalent. Valid text segments are joined. Whitespace is significant;
malformed text is rejected; numeric zero is not an empty value or a boolean.
Only changed machine-owned fields are sent. Human review fields and the source
Base remain outside the writer's allowlist.

## Uncertain writes

The deployment's exclusive tick process lock remains required. `WeekProjection`
is not a general concurrent-writer API.

Before a real PUT, the projection persists `update_intent`, the desired fields,
and a unique operation ticket in its private runtime database. Any uncertain
result leaves that intent intact. A later invocation or process restart performs
readback against the frozen pending fields. A mismatch blocks further writes;
it does not resend the PUT. Once readback matches, the intent is resolved and a
new desired state may be compared normally. A preflight GET failure creates no
write intent. Existing append reconciliation and legacy `created` rows remain
compatible. Do not reset an uncertain intent merely to make another write pass.

The first failure is preserved alongside the latest reconciliation failure.
Receipts record bounded error code, stage, operation, method, HTTP status,
Feishu code, validated request log ID, and target/field hashes when available.
They exclude raw response text, tokens, URLs, and task bodies. Frozen desired
fields remain private runtime data, never public repository evidence.

## 1254002 interpretation

Feishu's official update-record documentation labels 1254002 `Fail` / internal
error. It is distinct from 1254290 rate limiting and 1254291 write conflict.
The numeric code alone does not establish the failing route or whether a write
committed. Historical receipts without operation context cannot be reconstructed
by speculative retries. This patch fixes local no-op detection and uncertain
update handling; it does not claim to fix a remote service's internal error.

Reference: https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/update.md

## Validation

`test_copy_update_recovery.py` uses synthetic rows, fake clients, isolated SQLite
files and mocked HTTP responses. It covers unchanged ticks, real deltas, empty
field representations, post-commit 1254002, noncommitted 1254002, lost readback,
restart, sanitized diagnostics, and source/human-field denial. Run it together
with writer, projection, results-client and week-recovery regression tests in
approved cloud CI or a network-disabled container on an authorized test host.
Do not mount production data, provider credentials or a live runtime writable.
