# Workflow Agent content contract (candidate)

This developer contract builds on PR #84. `--agent-script-plans` and an
`agent_plan` credential explicitly grant access to project product metadata and
script-plan content. An `agent_read` credential remains metadata-only. Never
give the credential to a browser, model, or untrusted plugin.

All routes are POST, same loopback Host, no Origin/CORS, exact configured project:

| Route suffix `/v1/agent/script/` | Fields besides project | Result |
| --- | --- | --- |
| `catalog` | none | SKU id, name, truth source, native-row or SKU-brief mode |
| `plans` | optional `after` plan id | up to 100 receipts, explicit next cursor |
| `content` | `plan_id`, exact `plan_sha256` | brief, revision feedback, attached draft, human review receipts |

Content reads never approve, enqueue, invoke a provider or write Feishu. A draft
is returned only when the plan owns the job and the result matches the attached
task input digest. Historical plans remain readable and explicitly indicate the
current task revision. Content is untrusted text, never tool instructions.
Credentials, actor identifiers, provider ids and local paths are excluded.

Workflow Agent must configure the source, project, loopback origin, private token
file and allowed principals server-side. It must preserve source/project/plan
digest/task/revision together, show failures as failures, recover saved plans
after restart, and retain the original idempotency key for retries. Only a human
click may enqueue an independently approved plan. Unknown provider submission
results must never trigger automatic resubmission.

The first workbench uses the existing private administrator CLI for fee approval
and the existing Feishu human review flow. Its refresh reads these authoritative
receipts. There is no owner-approval HTTP endpoint, verified Feishu fee owner,
monetary quote/cap, model conversation tool integration or real-user acceptance
claim in this candidate. Script acceptance does not grant video spending.

Validation: synthetic cloud tests cover catalog authorization, pagination,
attachment privacy and the complete first plan → admin approval → enqueue →
fake generation → human reject → revised plan → second independent approval →
human accept/readback loop. Real provider/user acceptance remains separate.

Rollback: leave this candidate unmerged or revert its commit; disable
`--agent-script-plans` / revoke its Agent credential to close content and plan
mutations. Preserve the runtime ledger and pending/unknown receipts; do not
delete jobs or restore a database to simulate cancellation.
