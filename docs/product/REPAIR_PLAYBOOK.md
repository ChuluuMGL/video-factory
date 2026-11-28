# 工作流排障与精准修复规则手册

本手册供一线运营同事、实施人员、管理员以及接入本机 Codex / Claude / Cursor 或「月瑀助手」的 Agent 使用，统一规范**自助 MCP 排障接入、标准化排查决策树、五类故障精准修复分流**以及**员工与管理员的权限边界**。

全流程严格遵守五层分离原则：**环境安装、项目配置、飞书身份、付费模型执行、人工验收**在文案、权限与回执中各自独立；只读调查可由 Agent 自主连续执行，任何写操作、扣费或生产变更必须经过预览与人工确认。

---

## 一、同事通过本机 Codex / Claude / Cursor 自主排查（无需找管理员）

同事无需登录生产主机，也无需索取管理员密码、飞书 App Secret 或模型 API Key。只需在本机 AI 客户端（Codex、Claude 或 Cursor）挂接标准 stdio MCP 工具，即可在受控权限边界内完成只读排查、脚本预审改稿与受控修复：

| MCP 工具 | 适用场景 | 身份与凭据隔离 | 权限范围 |
| --- | --- | --- | --- |
| **`employee-mcp`** | 真实业务项目日常排障、脚本改稿、受控失败修复与隔离副本源码修复预演 | 管理员按项目签发短期员工配置（`vf-business-employee-v1`，最长 2 小时）；同事通过本人飞书官方设备授权领取短期凭证（最长 1 小时），仅访问 `/v1/employee/*` | 绝不暴露安装目录、管理员主密钥、用户长效 token 或模型密钥；管理员可随时 `employee-suspend` 吊销。详见 [业务员工接入说明](BUSINESS_EMPLOYEE_ACCESS.md) |
| **`project-mcp`** | 项目级受限委托接入，或本地 `fake-only` 隔离演练 | 仅读取当前受控会话的 `connection.json` 或按角色签发的短期项目委托凭证（`repair` / `edit` / `review` / `cost`） | 严格区分隔离演练与真实项目；不得共享管理员目录或 `owner.password` 来替代权限隔离 |

### 本机挂接要点

1. **前置条件**：本机安装 Node.js 22+（用于运行标准 stdio MCP 客户端）；所需连接参数仅保存在本机 `0600` 私有配置中，严禁贴入聊天记录、公共 Issue 或 Git。
2. **客户端登记**：在 Codex / Claude / Cursor 的 MCP 配置中登记团队交付的 `employee-mcp`（真实业务）或 `project-mcp`（项目/演练）启动入口，并加载 `video-factory-setup` Skill（保留完整的 `SKILL.md` 与 `references/` 目录）。
3. **本人授权与只读自检**：通过 `employee-mcp` 完成本人飞书设备授权后，先调用 `capabilities`、`daily_triage` 或 `project_stats` 验证项目范围与只读连通性，再开始定位具体任务。
4. **版本核对**：`inspect_task_logs`、`inspect_workflow_health`、`inspect_n8n_workflow`、`propose_n8n_patch` 属于 v0.3/M6 工作台与 Agent 诊断能力；旧固定包（如 a41）未内置这些接口时，按对应版本的只读命令核查，不虚构未上线能力。

---

## 二、标准化排查决策树

在「月瑀助手」悬浮窗与本机 Codex / Claude / Cursor 中均遵循同一四步决策链路：

1. **Step 1｜查今日巡检**：调用 `daily_triage` / `project_stats` / `list_failures` / `list_reviews`，识别待核对回执（`submission_unknown`）、可修复失败（`failed`）、卡住（`stuck`）与待审核（`awaiting_review`）任务。
2. **Step 2｜查任务日志（`inspect_task_logs`）**：按 `project_id` 与 `task_key` 读取脱敏后的错误码、阶段流转、版本号、SKU/参考图绑定与供应商回执摘要（不含原始密钥或响应正文）。
3. **Step 3｜查引擎健康或 n8n 节点**：
   - **无 n8n 本机常驻路线**：调用 `inspect_workflow_health`，核对本地常驻引擎状态、SQLite 账本、飞书长连接/回写、持久化硬预算余量与用户级服务状态（`launchd` / `systemd`）。
   - **现有 / 便携 n8n 路线**：调用 `inspect_n8n_workflow`，只读检查工作流节点、连线、允许修改字段与最近失败执行（自动脱敏凭据与敏感代码）；受限员工也可通过 `vf-workflow-observation-v1` 只读快照核对，不直连 SSH 或改动生产 n8n。
4. **Step 4｜精准分流与受控修复**：

| 分流类型 | 适用条件 | 标准处理动作 | 禁止事项 |
| --- | --- | --- | --- |
| **任务重试（受控重排）** | 明确 `failed`，且日志证实无远端供应商回执、无未知扣费 | 通过 `repair_and_schedule` 生成 4 行透明预览卡（诊断、保留失败 v1 → 新建 v2、费用与硬预算校验、锁定 `expected_version`），确认后入队并跟踪异步完成状态 | 未确认直接入队；突破项目硬预算；删除或覆盖失败旧版本 |
| **脚本预审 / 自然语言改稿** | 任务待审核或需按要求修改脚本 | 先用 `review_or_edit_script`（`ai_prereview` 规则预审，¥0），再用 `edit_preview` / `edit_generate_preview` 审阅 Diff 与费用说明，确认后保存新版本并核对飞书回写（`synced` / `reconciliation_required`） | 以规则预审替代人工审核；把脚本通过自动等同于视频付费批准 |
| **飞书 `1254002` 对账** | 影子副本或飞书回写返回 `1254002` 或写入结果未决 | **先确认调度已停机**，由管理员使用安装环境 Python 执行 `python -m video_factory.shadow_copy_recovery inspect` 只读核对冻结票据与远端状态（`matches_pending` / `matches_prior` / `neither`），再按 `resolve` 收口 | 清空写入意图、盲目重发远端写入，或把 `1254002` 误判为远端未提交 |
| **n8n 节点受控补丁** | n8n 工作流中允许字段配置错误（如分钟调度参数、便签、节点说明） | 调用 `propose_n8n_patch` 生成字段级 Diff 与结构校验；员工仅可确认创建**停用预览副本**；管理员复核后执行停用状态下安全保存、回读校验与回滚 | 修改凭据/连接/命令/敏感代码字段；员工直接写回或激活生产工作流 |
| **`submission_unknown` 禁重排** | `submission_unknown`、`reconciliation_required`、`PROVIDER_HTTP_<状态码>_NO_RESUBMIT` 或 `busy` | **禁止重排**；先查询既有供应商回执与飞书表记录，保持原锁与现场，交由管理员人工对账后再决定后续步骤 | 自动重排、清锁重发，或凭 404/429/500 断言“未扣费” |

---

## 三、员工 vs 管理员权限边界

当员工触发管理员专属动作时，系统仅展示诊断与提案并明确提示“已生成诊断与提案，需管理员确认执行”，禁用员工侧执行确认按钮。

| 能力项 | 员工（Employee） | 管理员（Admin / 负责人） |
| --- | --- | --- |
| 全部只读巡检、任务日志、引擎健康、n8n 节点只读查看、AI 预审 | ✅ 自由使用 | ✅ 自由使用 |
| 脚本自然语言改稿（预览 → 确认写回） | ✅ 预览后可确认 | ✅ 开放 |
| 明确失败任务的受控修复与重排（受项目硬预算约束） | ✅ 预览后可确认 | ✅ 开放 |
| 隔离副本受控源码修复（需独立授予 `repair` 范围） | ✅ 按授权范围执行 | ✅ 开放 |
| 创建 n8n 停用预览副本（不激活、不改生产） | ✅ 预览后可确认 | ✅ 开放 |
| 生产 n8n 正式工作流保存、启停、激活与回滚 | 🔒 仅可生成诊断与提案 | ✅ 确认执行 |
| 付费视频生成批准（`approve-video` / 计费确认） | 🔒 仅可准备诊断与计划提案 | ✅ 逐单确认执行 |
| 核心配置变更（`vf-setup`、模型/区域/成员/预算调整、员工签发与吊销） | 🔒 禁止 | ✅ 私有终端执行 |
| `submission_unknown` 人工对账与飞书 `1254002` 影子副本 `resolve` | 🔒 仅可上报只读证据 | ✅ 停机核对后收口 |

---

## 相关文档

- [排障与问题反馈](TROUBLESHOOTING.md)（完整分层错误码索引与脱敏 Issue 模板）
- [无 n8n 本机常驻指南](LOCAL_BUSINESS_SETUP.md)（`vf-setup` / `vf-run` / `vf-agent` / `vfctl local-service`）
- [第一次测试与让 Agent 帮你排障](FIRST_RUN_AND_AGENT_SUPPORT.md)
- [业务员工接入说明](BUSINESS_EMPLOYEE_ACCESS.md) 与 [业务源码修复说明](BUSINESS_SOURCE_REPAIR.md)
