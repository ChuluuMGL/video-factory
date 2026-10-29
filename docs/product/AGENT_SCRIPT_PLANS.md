# Agent 脚本计划合同候选

关联 #81 阶段 B，接续 #82/#83。本文是开发候选说明，不进入客户安装文档白名单，不修改 a41 已发布安装包、生产表或现有 Workflow Agent 界面。

## 身份与费用分别验证

Agent 使用项目绑定的 `agent_plan` token 提出脚本计划。宿主另行显式配置一个飞书提交人的私有用户 token 文件和固定 open ID；服务端在准备、费用批准和首次入队前重新读取文件、向飞书验证身份，并检查租户及项目 submitters 成员资格。更换为另一位成员，即使仍在同一项目，也不能继续使用原计划。

这是**宿主显式配置的飞书提交人委托**，不能声称聊天中的每个用户都已登录飞书，也不能声称被配置的成员亲自点击了费用确认。飞书提交资格不等于费用批准。费用继续由本机管理员查看具体脚本计划后，通过私有 CLI 批准；本版本没有指定飞书费用负责人审批 UI。

Agent 请求不能传用户 token、身份、模型、账号、凭据引用、权限名单或原表目标。用户 token 留在受控私有文件中，不写入计划、模型上下文、响应或日志。token 过期/撤销或身份读取失败时拒绝新计划和首次入队；已入队计划的查询及重复提交仅回读已有账本，不自动重发模型请求。

## 显式启动与接口

已有隔离 SQLite 项目需配置有效飞书绑定、SKU、脚本 provider profile 和对应 vault 凭据。以下只是候选合同示例，不是让客户复制正式账本做测试。

```sh
vfctl up --root /absolute/private/runtime --project test_project \
  --agent-script-plans \
  --script-user-token-file /absolute/private/feishu-user.token \
  --script-open-id ou_EXPECTED_MEMBER
```

两个身份参数缺一不可，不带显式开关不能加载委托身份。启动不会验证真实业务已可用；请求发生时才进行身份/原表核验。启动时检查私有文件，每次准备/批准/首次入队重新读取，以支持同一成员手动更新过期 token。未提供 `--execute-approved` 参数组时调度仍不执行模型任务。

脚本与视频的启用开关独立。`agent_read` 只能读取脱敏回执；只有 `agent_plan` 与脚本开关同时启用，能力发现才开放脚本计划。以下均为 JSON POST，要求精确 loopback Host、Bearer token，拒绝 Origin 和未知字段：

| 路径 | JSON 字段 | 说明 |
|---|---|---|
| `/v1/agent/script/prepare` | `project, request_id, task, sku_id, brief, expected_revision` | 非原表生产项目的 SKU 简报，最多 6000 字符；请求上限 64 KiB |
| `/v1/agent/script/prepare-record` | `project, request_id, record, expected_revision` | 明确选中的一条原表记录；服务端读回任务、SKU、简报和来源版本 |
| `/v1/agent/script/submit` | `project, plan_id, plan_sha256` | 已批准、未过期且未漂移的计划原子入队 |
| `/v1/agent/script/receipt` | `project, plan_id` | 原计划与任务版本状态、供应商回执是否已记账、草稿摘要和是否已附回任务 |

`expected_revision` 是非负整数：首版为 0；修订必须针对当前已关闭版本。当前仍待审核、排队或提交未知的任务不能直接创建下一版。修改意见使用已有审核账本里的反馈，不接受 Agent 自称的审核结果。

启用原表生产绑定后，普通 `prepare` 明确拒绝，必须使用 `prepare-record`。任务编号、SKU、原表行、来源版本、脚本文本摘要和原表审核状态均需一致；修订必须保留来源约束。对话里改变简报不等于已更新原表，本候选不会自动写入原表。

## 负责人查看、批准、撤销

```sh
vfctl agent-script-plan inspect --root /absolute/private/runtime --project test_project \
  --admin-token-file /absolute/private/admin.token --plan-id PLAN_ID

vfctl agent-script-plan approve --root /absolute/private/runtime --project test_project \
  --admin-token-file /absolute/private/admin.token --plan-id PLAN_ID \
  --plan-sha256 REVIEWED_SHA256 --accept-provider-charges \
  --script-user-token-file /absolute/private/feishu-user.token \
  --script-open-id ou_EXPECTED_MEMBER
```

`inspect` 只在管理员终端展示具体 SKU、简报、修改意见、绑定提交人、原表来源和费用归属。Agent 回执不提供这些原文或凭据。`approve` 必须重新校验飞书身份、来源和精确摘要，不创建任务或调用模型。没有可靠金额报价时返回空金额与 `quote_available:false`；批准是按已配置供应商费率的一次调用，不能显示成免费或已有金额预算封顶。

`revoke` 使用管理员凭据、计划 ID 与精确摘要，不需要委托用户 token。尚未入队时只撤销计划；已入队但尚未记录提交意图时将 job 标记 cancelled、任务置 failed，留下可审计历史并允许按已有修订规则准备下一版。已经开始/未知/已返回结果的请求拒绝撤销，不能声称外部请求被取消。

## 执行与恢复合同

- 计划一小时到期。请求 ID 固定绑定完整输入；重复 prepare/approve 不续期，不创建第二个计划或扩大费用授权。
- 身份和原表网络读取在事务之外进行；保存/批准/入队时再次核验本地权限、绑定、配置、SKU、凭据版本、当前任务版本和原表配置。
- 从现有 `ScriptJobs.submit` 提取内部提交事务；Agent 使用同一创建任务、脚本 job 和原表关联逻辑。任务/job/Agent 回执在一个事务内提交，异常整体回滚，没有第二个执行器。
- 并发提交只允许一个任务版本入队；另一条计划不能接管或覆盖已存在的 job。响应丢失/重启后的同计划重试返回原回执。
- 调度仍调用现有 ScriptJobs，供应商 I/O 前保存不确定提交意图。未知结果不自动重试；供应商已返回但附回草稿失败时，保存供应商回执后恢复附回，不重新生成。
- 草稿进入 `awaiting_script_review`；退回意见沿用现有审核账本，新版本需要新计划与费用批准。脚本通过进入 ready，不会创建或批准视频 job。
- 恢复账本会撤销旧脚本计划及 automation 权限。新签发 Agent token 不能复活旧批准。

## 验证与剩余工作

云端合成测试覆盖真实本机回环飞书身份 fixture、假脚本供应商、原表变更、并发、事务回滚、身份/权限/配置/凭据漂移、审核修订、未知提交、恢复和 HTTP/CLI 边界。本机只做编辑、静态解析和 Git，不进行运行测试或真实 API 请求。

尚需现有 Workflow Agent 的服务端适配与对话界面、受控脚本内容/视频预览接口、友好的身份授权与费用确认页面、金额预算，以及隔离真实飞书/模型和第二位使用者验收。#37 仍是单独未合并的原表生产入口，未在本候选中整体引入。该接口候选不代表这些阶段已完成。
