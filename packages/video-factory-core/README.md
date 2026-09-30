# Video Factory Core 0.1.0a20

Product direction and current gaps: [self-hosted PRD](../../docs/product/PRD.md), [implementation status](../../docs/product/STATUS.md), and [planned Setup contract](../../docs/product/SETUP_CONTRACT.md). Historical client-specific canaries are retained only in the private legacy repository; they are not customer installers.

## Candidate distribution

The private release archive includes this CLI, dependency wheels, SHA256 lock, installer and `video-factory-setup` Skill. See the [release guide](../../docs/product/RELEASE_USAGE.md). It targets Linux x86_64 / Python 3.12, installs into a separate version prefix without network access, and returns the next Setup command. Docker remains a prerequisite; installing the CLI does not start services.

## Unified terminal setup

On the prepared customer Linux Docker host, run `vfctl setup-run --session /root/vf-private/customer.setup.json --root /opt/video-factory --wheelhouse /root/verified-release/wheels`. The session parent must already exist and be private. This private-TTY flow collects configuration, reviews the exact installation plan, sets or verifies the administrator password, collects Feishu field mappings, saves the application secret in the customer's encrypted vault, and offers authorization and employee review windows. See the [setup-run guide](../../docs/product/SETUP_RUN_USAGE.md).

The same command resumes saved progress and can reuse the encrypted application credential. It does not install the CLI or Docker, create a Base, accept secrets in Agent chat, or enable paid generation. Real customer authorization and human acceptance remain pending.

## Runtime installation and recovery

`vfctl stack install` installs the pinned product/n8n/PostgreSQL stack on a customer-owned Linux amd64 Docker host. See the [stack usage guide](../../docs/product/STACK_USAGE.md) for prerequisites, password input, resume, migration, full cold backups, upgrade and rollback. `vfctl host` retains the native SSH installer; `vfctl runtime` manages local accounts and the persistent ledger. These infrastructure paths are separate from the offline Setup plan and do not enable Feishu or paid generation.

`vfctl setup-deploy plan/apply/status` connects a completed Setup to installation and authenticated project/SKU import on the customer host. It preserves explicit disabled model and unverified Feishu states; see the [execution guide](../../docs/product/SETUP_DEPLOY_USAGE.md).

## Resumable Setup planning

`vfctl setup` now provides a terminal welcome/questions flow and a JSON Agent interface over the same saved session. It only prepares a plan: it does not connect to SSH/Feishu/n8n, install services, create users, read credential values, or call models. `plan_ready` means the offline plan is complete, not that the deployment is ready. See the [Setup usage guide](../../docs/product/SETUP_USAGE.md).

On a supported POSIX management host (Linux/macOS), use an absolute session path inside an existing directory owned by you and not writable by other users. Session and lock files are owner-only. Keep sessions outside Git; `.vf-setup/` is ignored for local drafts.

```sh
mkdir -m 700 .vf-setup
vfctl setup --session "$(pwd)/.vf-setup/customer.setup.json" --interactive
```

Use the same command to resume. In Agent mode, `--json` returns the next question without blocking, and answers require the returned revision:

```sh
vfctl setup --session /absolute/private/customer.setup.json --json
vfctl setup --session /absolute/private/customer.setup.json --json \
  --answers /absolute/answers.json --expect-revision 0
```

`--answers -` reads a JSON object from stdin; keys are the exact `field` names in the question response. Do not place raw secrets in answer files. The synthetic [complete answer example](examples/setup/answers.json) and [SKU array](examples/setup/products.json) demonstrate the format. Credentials accept only `env:VARIABLE_NAME` or `secret:alias` references, never values. Known secret patterns and unknown fields are rejected with errors that do not echo the submitted data; arbitrary text cannot be universally classified as a password, so do not paste secrets into other fields either.

`--from-session /absolute/private/existing.setup.json` creates a new project draft that copies only organization/deployment configuration. It does not prove or reuse a live installation. All live verification remains `not_run`. A changed customer/host/tenant invalidates related bindings, and stale revisions are rejected. Unsupported session schemas fail with a fixed error rather than being silently migrated.

The H3 option is explicitly labeled as a scoped canary candidate; the bounded worker still requires real-provider acceptance. Script generation is deferred and voice is disabled. No Seedance or automatic paid fallback is advertised.

## Existing bootstrap commands

This is the **installable bootstrap**, not the production executor. `vfctl init` writes one disabled project configuration at an explicit path; `vfctl inspect` checks it offline. Both commands report `not_running` and `execute_allowed=false`. They never contact Feishu, n8n, a model provider or a self-hosted generator.

`vfctl intake` reads only `video-factory.brief.json` in a new project folder, lists other direct file names as hints, and asks for missing facts. `vfctl plan` compiles a deterministic, disabled Feishu Base / n8n resource blueprint and reconciles it against an optional offline inventory snapshot. It does **not** create resources. Without a live inventory it says `discover_live_resource`, never assumes that a resource is absent.

`vfctl discover` is the first real connector, but strictly **read-only**. It takes one explicit Base token and one n8n URL/key through environment variables, reads all table/field pages and all n8n workflow cursor pages, then reports name collisions. An existing same-name resource is **not** automatically treated as reusable until its structure has been checked. It does not create empty placeholder workflows.

The preview now includes six tables: products, assets, tasks, versions, human review events, and an execution ledger. Normal script/video production and script/video repair have separate input and output statuses. A reviewer decision is bound to one version; an execution is expected to retain its input fingerprint, n8n execution ID, and provider request ID or explicit reuse evidence. These are **contracts**, not functioning n8n nodes yet.

An internal, **unexposed** installer accepts only an explicit isolated-test target and a complete bundle of nonempty, disabled n8n templates. It serially checks for existing resources, creates only missing ones, reads each back, and returns a per-resource receipt. Re-running reuses exact matches; an uncertain create is read back once and never blindly retried. A same-name structural conflict or incomplete readback stops the batch without deleting or activating anything. The Feishu and n8n adapters have been exercised with simulated services, **not** a real test instance. There is intentionally no `vfctl install` command while real sandbox readback remains unaccepted.

`vfctl template-preview` now renders five separate, disabled n8n dispatch drafts: normal script, normal video, script repair, video repair, and health notification. Each has a schedule trigger and authenticated HTTP request to a *future* project-scoped worker endpoint. The command accepts an existing credential **ID**, not a secret. The drafts are not production-ready until that worker exists, they import successfully into the exact n8n version, and a real isolated execution is accepted. `vfctl canary` runs the matching normal/repair state transitions entirely in memory with fake outputs: human review remains a separate decision, stale review versions fail, repair needs version-bound feedback, and duplicate input claims fail closed. It makes **zero** provider requests and touches **zero** live resources.

```sh
python3 -m pip install ./packages/video-factory-core
vfctl init --project-id new_brand --output /absolute/project/path/video-factory.project.json
vfctl inspect --config /absolute/project/path/video-factory.project.json
vfctl intake --project-dir /absolute/new-project/folder
vfctl plan --project-dir /absolute/new-project/folder
vfctl discover --project-dir /absolute/new-project/folder \
  --base-token-env VF_TEST_BASE_TOKEN --n8n-url https://test-n8n.example.com \
  --n8n-api-key-env VF_TEST_N8N_API_KEY
vfctl template-preview --project-dir /absolute/new-project/folder \
  --worker-origin https://isolated-worker.example.com --credential-id EXISTING_N8N_CREDENTIAL_ID
vfctl canary --project-dir /absolute/new-project/folder
```

The project folder may contain `video-factory.brief.json` with `project_id`, `product_category`, `target_market`, `business_goal`, `feishu_target`, `n8n_target`, `script_reviewer`, `video_reviewer`, `model_route`, `spend_policy`, `category_rule_source`, and a `products` array. Every product needs `sku_id`, `name`, `variant`, and `truth_source`. Missing facts become explicit questions; no product claims or category rules are guessed. Never put passwords or API keys in the brief.

The template stores only environment-variable **names**, never credential values. A complete static configuration is still not an enabled runtime: project adapter behavior, review-event identity, conditional writeback, model route, QA, rollback and real end-to-end acceptance must be implemented and verified separately. Installation does not modify existing n8n workflows or Base records. Use an isolated environment for the first installation test.

## Explicit worker candidate

`vfctl worker prepare/approve/step/status` operates one approved task revision using the shared SQLite/PostgreSQL ledger. It does not activate project-wide paid routes. See [worker usage and limitations](../../docs/product/WORKER_USAGE.md): current cloud acceptance uses a loopback provider fixture; a13 adds a pinned FFmpeg toolchain and opt-in container worker with bounded HTTPS egress. Default startup stays offline. Real provider acceptance and Feishu/n8n dispatch remain separate pending gates.

`stack-worker` 容器执行入口和出站限制参见 [WORKER_USAGE](../../docs/product/WORKER_USAGE.md)。默认启动仍不调用模型；a13 云端结果需按提交核对。

## Feishu import and verified review candidate

`vfctl feishu` / `stack-feishu` bind explicit sources, import immutable task snapshots and verify user identity for revision-bound review/repair. `queue-template` produces a disabled, project-scoped n8n queue reader. No Base writes or paid scheduling are enabled. See [Feishu bridge](../../docs/product/FEISHU_BRIDGE.md) for credentials, recovery re-confirmation, commands and live acceptance gaps.

## 员工审核入口

a16 增加 `vfctl review-ui` / `vfctl stack-review` 限时浏览器窗口，支持飞书设备授权、脚本/视频查看和意见确认。参见 [操作说明](../../docs/product/REVIEW_UI_USAGE.md)。云端模拟授权测试不代表真实租户已验收。
