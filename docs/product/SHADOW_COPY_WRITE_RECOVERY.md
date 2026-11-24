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

## Stopped-run inspection and resolution

Run this operator tool exclusively against the stopped shadow copy with the
scheduler inactive. Use the runtime's exclusive process lock and an existing
copy-scoped reader. No source access, model calls, record POST or PUT are needed.

- Before PUT, atomically freeze both `pending_fields` and `prior_fields`. The
  latter contains the observed preflight values for the same fields. Copy
  identity must match. For append, first verify by exact identity lookup that
  no row exists. Persist that absence evidence with the append ticket before
  handing the append to the client. A pre-existing or ambiguous match blocks
  append. Older intents without prior evidence cannot establish matches_prior.
- `inspect` opens local state read-only and reads the copy. Return intent state,
  ticket, classification and hashes only for field values. A unique matching
  row with the frozen pending values is `matches_pending`. A unique update row
  matching the frozen prior values, or a complete exact append lookup with no
  rows and frozen absence evidence, is `matches_prior`. Everything else,
  including missing update rows, duplicate append rows, invalid data, read
  errors or an unverifiable lookup, is `neither`. Compare with the same text
  and numeric rules as the writer and give pending precedence.
- `resolve --owner-confirm --ticket TICKET` re-reads fresh remote state and
  checks the exact current ticket before changing local state.
  `matches_pending` records written with the verified row identity.
  `matches_prior` records abandoned without resending. Keep an abandoned
  projection as a tombstone so a later call cannot silently recreate the
  operation. `neither` refuses resolution. Refusals leave the intent intact.
- Each resolve attempt produces a separate sanitized receipt with the ticket,
  classification and decision. Apply any transition and its receipt in one
  local transaction. Keep `first_failure` and `last_failure` unchanged.
  Inspect itself never writes a receipt to the runtime. These classes report
  observed state, not proof that a particular ambiguous request caused it.
