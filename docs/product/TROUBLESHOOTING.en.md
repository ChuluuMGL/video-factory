# Troubleshooting and issue reporting

This index is for administrators, installers and their agents. First read the **running** `vfctl` version, project, failing step and exact error code. Check the customer server, Feishu Base, n8n and provider receipts separately. A passing CI job or a successful command invocation does not establish business completion.

| Symptom | Check first | Safe continuation |
|---|---|---|
| Interrupted installation | Pinned release, original Setup session, `vfctl stack status` | Resume the original session; do not delete it or recreate customer data. See [Setup](SETUP_RUN_USAGE.md). |
| `DOCKER_ADDRESS_POOL_EXHAUSTED` | Docker networks and attached containers | Only remove an identified empty, rebuildable test network, then resume. |
| No containers after a host reboot | `systemctl is-enabled docker`, `systemctl is-active docker`, then the target stack's container health | If Docker is disabled, the administrator checks host policy before enabling boot start and running a stop/start acceptance test. Healthy containers do not prove that the task ledger or event queue recovered. |
| `FEISHU_OAUTH_DENIED_OR_EXPIRED` | Grant expiry, signed-in user, waiting terminal | Start one fresh user grant after reading current state. Do not recreate the Base. |
| Uncertain Base creation | `setup-feishu status` and actual remote Base/table IDs | Stop automatic retries and reconcile the journal with the remote resources. See [Base recovery](BASE_CREATION.md). |
| Original-table review has not advanced | Status, target revision, SKU, script/video, verified event receiver and queue | Check the actual employee action and event receipt. Save specific feedback **before** selecting Reject. A later edit does not change an existing receipt. See [native review](FEISHU_NATIVE_REVIEW.md). |
| Provider submission timed out | Local state, provider receipt ID and saved artifact | Do not resubmit or pay again until the original result has been reconciled. |
| `PROVIDER_AUTH_REJECTED_CHECK_REGION_OR_KEY` | Key region, original plan digest and absence of a provider receipt | Versions with `recover-auth` can archive a definite authentication rejection after correcting credentials, preserve script review and require a newly checked generation approval. Never use it for uncertain submissions or existing receipts. See [worker recovery](WORKER_USAGE.md). |
| Video or attachment missing | Media decode and SHA-256, upload receipt, remote row and write intent | Read back and reconcile before retrying a write. See [result sync](BASE_RESULTS.md). |
| Upgrade or restore failed | Old/new stack roots, containers, database, backup and restore receipt | Identify the running version and rollback path. A backup file alone is not a restore test. See [stack operations](STACK_USAGE.md). |

These are diagnostic entry points, not promises of automatic repair. An agent should start with read-only evidence, then present the specific repair and its verification. Permission expansion, credential rotation, data recovery, paid retries and employee review have separate authority.

Search [existing issues](https://github.com/ChuluuMGL/video-factory/issues) before using the [bug template](https://github.com/ChuluuMGL/video-factory/issues/new/choose). Include the version, stage, short error code, expected and actual behavior, minimal synthetic reproduction and checks already made. Never post credentials, server IPs, real Feishu IDs, customer content, raw logs or unsanitized screenshots. Report vulnerabilities [privately](https://github.com/ChuluuMGL/video-factory/security/advisories/new). The customer or installer reviews and submits the issue; the agent does not post diagnostics automatically.

An issue records a problem to investigate; it is not a verified fix. When a fix is validated, maintainers update this index and the affected operation guide in the same PR, naming the applicable version. Recheck this index before each pinned release. Closing an issue does not upgrade or restore a customer's instance.
