# Troubleshooting and issue reporting

This index is for administrators, installers and their agents. First read the **running** `vfctl` version, project, failing step and exact error code. Check the customer server, Feishu Base, n8n and provider receipts separately. A passing CI job or a successful command invocation does not establish business completion.

For portable `PROVIDER_HTTP_<status>_NO_RESUBMIT` errors, inspect the original execution ledger's `result.provider_http_receipt`. It retains only the HTTP status, bounded error code, job ID, response model, output count and execution ID, without response bodies, credentials or full execution logs. The ledger stays `busy`: do not clear it or resubmit. A 404, 429 or 500 does not prove that no charge occurred. A transport failure without a receipt remains unknown. Upgrade existing project workflows to obtain this diagnostic behavior; it cannot reconstruct previously lost responses.

A Seedream request for `doubao-seedream-5-0-lite-260128` accepts the documented same-version response alias `doubao-seedream-5-0-260128`, recording both IDs without accepting other model versions. See the [official model documentation](https://docs.volcengine.com/docs/ark/seedream-4-0-5-0?lang=en). HTTP 200 alone does not establish image validation, upload or Base writeback.

| Symptom | Check first | Safe continuation |
|---|---|---|
| Interrupted installation | Pinned release, original Setup session, `vfctl stack status` | Resume the original session; do not delete it or recreate customer data. See [Setup](SETUP_RUN_USAGE.md). |
| `DOCKER_ADDRESS_POOL_EXHAUSTED` | Attached containers, their states and Compose directories; distinguish the active stack, runners and retired tests | Release only verified retired test-stack networks with recovery material preserved, then check both the authorization worker and runner before resuming. See below. |
| No containers after a host reboot | `systemctl is-enabled docker`, `systemctl is-active docker`, then the target stack's container health | If Docker is disabled, the administrator checks host policy before enabling boot start and running a stop/start acceptance test. Healthy containers do not prove that the task ledger or event queue recovered. |
| `FEISHU_OAUTH_DENIED_OR_EXPIRED` | Grant expiry, signed-in user, waiting terminal | Start one fresh user grant after reading current state. Do not recreate the Base. |
| Uncertain Base creation | `setup-feishu status` and actual remote Base/table IDs | Stop automatic retries and reconcile the journal with the remote resources. See [Base recovery](BASE_CREATION.md). |
| User-created Base succeeds, but application reads or review setup are denied | Dedicated app ID, the target Base's document-app list, API scopes and exact Feishu error code | A user's creation grant does not grant the application access to that Base. Check the dedicated app under More → Add document app, and obtain administrator confirmation before submitting the scoped access change. Verify application reads before enabling review and subscriptions. Do not recreate the Base, replace reviewers or retry a model call. See [native review](FEISHU_NATIVE_REVIEW.md). |
| Original-table review has not advanced | Status, target revision, SKU, script/video, verified event receiver and queue | Check the actual employee action and event receipt. Save specific feedback **before** selecting Reject. A later edit does not change an existing receipt. See [native review](FEISHU_NATIVE_REVIEW.md). |
| Provider submission timed out | Local state, provider receipt ID and saved artifact | Do not resubmit or pay again until the original result has been reconciled. |
| `PROVIDER_AUTH_REJECTED_CHECK_REGION_OR_KEY` | Key region, original plan digest and absence of a provider receipt | Versions with `recover-auth` can archive a definite authentication rejection after correcting credentials, preserve script review and require a newly checked generation approval. Never use it for uncertain submissions or existing receipts. See [worker recovery](WORKER_USAGE.md). |
| Video or attachment missing | Media decode and SHA-256, upload receipt, remote row and write intent | Read back and reconcile before retrying a write. See [result sync](BASE_RESULTS.md). |
| Upgrade or restore failed | Old/new stack roots, containers, database, backup and restore receipt | Identify the running version and rollback path. A backup file alone is not a restore test. See [stack operations](STACK_USAGE.md). |

## First video query fails (query-fix candidate)

Once a provider job ID is saved, query that job only. The candidate returns `video_query: pending` for queued or running jobs and leaves the Base row unchanged. An empty output in an older temporary n8n webhook can return HTTP 500; this alone does not prove a provider generation failure.

`VIDEO_QUERY_HTTP_<status>_EXISTING_JOB_ONLY` identifies a query HTTP error separately from paid submission errors. Check the region, credentials and service, then query the same job again. Do not clear its ID or submit another generation. `VIDEO_QUERY_BODY_UNVERIFIED_NO_RESUBMIT` and `VIDEO_QUERY_UNVERIFIED` require reconciliation of the response structure, model or job identity. Provider response bodies and authentication data are excluded from these query errors.

A completed result must pass version checks, Base writeback and readback before human review. Query recovery does not approve the result. This candidate still requires release and an explicit project upgrade; existing a40 workflows do not change automatically.


## Portable candidate: terminal and startup

Use the [portable guide](PORTABLE_N8N.md) for local or existing n8n. The Ubuntu restriction belongs to the pinned server package.

- Invalid menu/address input can be retried; `:quit` preserves completed configuration. Resume with the same root and project ID.
- `PRIVATE_TERMINAL_REQUIRED`: use the installer's own interactive terminal, not an agent output pipe. Never put keys in chat or command arguments.
- `N8N_API_PERMISSION_REQUIRED`: check the instance and public API key. The wizard permits three read-only authentication attempts without recreating the project.
- `SERVICE_START_NOT_VERIFIED_DATA_PRESERVED_RUN_STATUS`: run `status` with the same root/component, then inspect that OS job, executable path and port. Preserve data.
- `SERVICE_DEFINITION_CHANGED_PRESERVED`: compare the saved and OS definitions before any repair; do not overwrite someone else's job.
- macOS/Windows jobs start at user login. Ordinary Linux user services need login or administrator-configured linger. Docker needs its own engine to start.
- Receiver process status does not prove authenticated Feishu delivery. Check the app, subscription, competing receivers and SDK receipt before reconciling an unknown delivery.

`uninstall` cancels startup and preserves data. `stop` pauses the current process; a later login or boot may start it again.

## Owner confirmation received, but generation did not start

In the portable candidate, `APPROVAL_RECORD_CHANGED`, `APPROVAL_STATUS_CHANGED`, `APPROVAL_CONFIRMATION_NOT_CURRENT` and `APPROVAL_DISPLAY_CHANGED` distinguish the live record, status, confirmation ID and displayed plan checks. `APPROVED_INPUT_CHANGED` means the approved content differs. Earlier candidates report the combined `APPROVAL_TASK_OR_PLAN_CHANGED`; that code alone does not identify the field or prove a network failure.

Read the current row, protected approval and execution ledgers, and n8n event-log node trace. Missing execution API data is not proof of zero model calls: full execution data is disabled by default. Preserve the failed event. Do not clear the queue or automatically replay it. Only consider an explicit continuation after proving the plan was not consumed, no provider node ran, and the same unexpired plan still matches the live row. Keep raw logs private. See [billing approval](PORTABLE_BILLING_APPROVAL.md).

## Image and fee-plan recovery (source candidate, not in a38)

Inspect the original project's execution ledger before retrying. `image_ready` permits `recover-image` to continue an upload that has not been attempted, followed by `recover` for Base writeback. `result_ready` permits only `recover` using the saved attachment receipt. Preserve and reconcile `busy` or `image_uploading`; never clear them and resubmit.

If writeback recovery succeeded but the fee plan remains `consumed`, run `approval-reconcile --url <instance> --project <original-project>`. It reads the original result and verifies the execution, plan and designated owner before releasing the plan slot. It makes no model request or image upload and does not reuse the old confirmation ID. `RECOVER_RESULT_FIRST_UNKNOWN_EXECUTION_NOT_RELEASED` means the execution is unfinished; `RECOVERY_APPROVAL_RECEIPT_MISMATCH` or `RECONCILE_RECEIPT_CHANGED` requires reconciliation of preserved receipts. See the [image workflow guide](PORTABLE_IMAGES.md).

## Exhausted Docker address pools

Stopped containers can retain Compose networks. Repeated upgrades and restore drills can consume the available subnets even while the active stack is healthy. A new authorization worker or project runner may then fail to start; this does not indicate an incorrect password or model key.

1. Inspect container states, attached networks and Compose working directories. Verify the new stack and preservation of review records and media hashes.
2. For an identified retired test stack whose containers are all stopped and rebuildable, save a private inspection receipt before running Compose `down` with that stack's configuration. Preserve configuration, data, backup keys, volumes and images. Do not use global prune or remove volumes.
3. Check the short-lived authorization worker and private runner separately, including health and published ports, before resuming the original Setup session. Do not recreate a Base or repeat a paid submission for this failure.

If a failed wizard closed its administrator session, private password input is required again. Check startup first, before asking for that input. After restoring n8n scheduling, read actual execution records as well as the publication receipt.

This recovery was verified on the a34 test host. Other address pools and customer environments require fresh ownership and state checks; test-stack cleanup must not be applied automatically to them.

These are diagnostic entry points, not promises of automatic repair. An agent should start with read-only evidence, then present the specific repair and its verification. Permission expansion, credential rotation, data recovery, paid retries and employee review have separate authority.

Search [existing issues](https://github.com/ChuluuMGL/video-factory/issues) before using the [bug template](https://github.com/ChuluuMGL/video-factory/issues/new/choose). Include the version, stage, short error code, expected and actual behavior, minimal synthetic reproduction and checks already made. Never post credentials, server IPs, real Feishu IDs, customer content, raw logs or unsanitized screenshots. Report vulnerabilities [privately](https://github.com/ChuluuMGL/video-factory/security/advisories/new). The customer or installer reviews and submits the issue; the agent does not post diagnostics automatically.

An issue records a problem to investigate; it is not a verified fix. When a fix is validated, maintainers update this index and the affected operation guide in the same PR, naming the applicable version. Recheck this index before each pinned release. Closing an issue does not upgrade or restore a customer's instance.
