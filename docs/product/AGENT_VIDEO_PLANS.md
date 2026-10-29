# Agent 视频计划合同候选

关联 #81 阶段 B，依赖 PR #82。本候选先完成**已审核脚本到视频入队和回执**，不是脚本生成接口、完整创作 Agent 或发布版本。没有合并、部署、真实模型调用或飞书生产写入。开发说明不进入客户文档白名单，不改变 a41 安装入口。

## 权限与事实来源

- `agent_read`：保留只读，增加按计划 ID 查询脱敏回执。
- `agent_plan`：显式签发的项目权限，允许准备视频计划、将负责人已批准的计划入队及读取结果；没有批准、审核、供应商直连权限。
- 本机管理员：从私有令牌文件运行 `agent-plan inspect/approve/revoke`。这是本机管理员权限，不能表述为已验证飞书负责人身份，也不能把管理员令牌或批准命令交给模型执行。
- `approved_execution`：原有独立调度权限。入队不等于供应商已收到；只有启动执行模式且原 Worker 校验通过才发生供应商请求。

所有状态保存在 Video Factory 原 SQLite 账本，计划最终原子写入既有 Worker job；无第二个供应商提交器。HTTP 不提供批准入口，拒绝浏览器 Origin，继续限制精确 loopback Host。

## 开关与前置条件

默认不开放计划变更。隔离开发账本必须已配置项目、SKU 视频 profile、可读取的私有素材、模型凭据，并已有处于 `ready` 的已审核脚本。Agent 不能传入模型、路径、凭据引用、账号、区域或素材规格，全部使用管理员的 SKU profile。现有 profile 工具的 `/work` 路径限制仍在，macOS 素材配置向导尚未接入；CI 使用合成 profile，不代表客户安装步骤已打通。

```sh
vfctl agent-key --root /absolute/private/runtime --project test_project \
  --admin-token-file /absolute/private/admin.token \
  --scope agent_plan --output /absolute/private/agent-plan.token

vfctl up --root /absolute/private/runtime --project test_project --agent-plans
```

此启动方式可准备及入队，调度仍为只读。实际模型执行另需现有 `--execute-approved` 参数组及任务费用批准；本开发任务不在真实账本启动它。

## Agent HTTP 协议

均为 JSON POST，携带项目绑定 Bearer token。未知字段拒绝。能力发现新增 `video_plan_receipt`；仅当 `--agent-plans` 与 `agent_plan` 同时存在时返回 `prepare_video_plan`、`submit_approved_video_plan`。脚本计划与通用批准能力保持不可用。

| 路径 | JSON 字段 | 含义 |
|---|---|---|
| `/v1/agent/video/prepare` | `project, task, revision, request_id` | 准备 1 小时有效计划；正整数版本；稳定请求 ID |
| `/v1/agent/video/submit` | `project, plan_id, plan_sha256` | 仅将本机管理员已批准且未漂移的计划入队 |
| `/v1/agent/video/receipt` | `project, plan_id` | 原计划阶段、对应任务版本状态、供应商回执是否存在、产物哈希与解码证据 |

计划响应提供模型、时长、参考素材数量、输入/配置摘要、计划摘要、期限、`max_submissions:1`。价格尚无可靠报价接口，明确返回 `cost.quote_available:false` 与空金额；不能把它显示成免费、零元或预算封顶。批准依据是“按已配置供应商费率调用一次”，使用方需先自行核对供应商费率。货币预算上限及报价 UI 仍待后续。

Agent 不收到脚本内容、私有素材路径、账号引用、供应商原始 ID/响应或管理员身份。`artifact` 只有哈希和校验证据，没有播放 URL。`current_revision/is_current_revision` 区分历史计划与最新任务；旧计划回执不能用于批准新版本。

## 负责人操作

先在本机使用真实管理员凭据检查具体计划。`inspect` 除摘要外显示账本脚本、SKU、计费归属及区域，仅输出到管理员终端；不要将其原样发送到模型。

```sh
vfctl agent-plan inspect --root /absolute/private/runtime --project test_project \
  --admin-token-file /absolute/private/admin.token --plan-id PLAN_ID

vfctl agent-plan approve --root /absolute/private/runtime --project test_project \
  --admin-token-file /absolute/private/admin.token --plan-id PLAN_ID \
  --plan-sha256 REVIEWED_SHA256 --accept-provider-charges
```

`approve` 不入队、不调模型；Agent 随后 `submit` 才进入调度账本。没有明确费用参数时 CLI 拒绝批准。重复批准不续期。`revoke` 使用同一计划 ID 与摘要，可撤销未入队或尚未开始的入队任务；已经记录提交意图后不能声称撤销了外部请求。

## 故障、并发和恢复

- 同一项目的 `request_id` 固定到同一任务版本。重复 prepare 返回原计划，不续期；过期后需新的请求 ID 和新的负责人批准。
- approve 与 submit 都重新核对当前任务、输入、配置、完整 profile、凭据版本、素材实际哈希。漂移要求新计划。
- submit 在同一数据库事务内写 Worker job 与计划入队回执。重复请求/重启返回同一回执；同一任务版本的两个已批准计划只能有一个入队，不能覆盖旧 Worker 回执。
- Worker 在供应商 I/O 前持久化提交意图。未知结果仅提示对账，不重发，不扩大次数或换模型。原调度器恢复轮询已有回执。
- 账本恢复会撤销未使用计划和 automation 权限；重新签发 Agent token 不会恢复旧批准。
- `prepared`、`approved`、`enqueued`、供应商回执、产物记录、`awaiting_video_review`、人工 `accepted` 是不同事实。回执保留实际任务状态，成片入账后仍需人工审核。

## 剩余工作

此候选只完成阶段 B 的视频子集。ScriptJobs 仍需真实飞书 submitter 身份、绑定与原表源版本；#37 与脚本准备/批准拆分继续单独审查。Workflow Agent 服务端适配、脚本/预览展示、报价与预算上限、负责人友好确认 UI、真实隔离验收均未完成。#81 保持打开。

云端 Ubuntu/macOS 使用合成 SQLite、假供应商与本机回环 HTTP 验证：权限隔离、并发重复提交、事务回滚、版本/配置/凭据/素材漂移、期限/撤销/恢复、未知结果无重试、CLI 费用确认和敏感字段隔离。本机仅编辑、静态解析和 Git。
