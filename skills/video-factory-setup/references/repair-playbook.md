# 工作流排障与精准修复规则手册

本手册面向一线运营同事、实施人员以及接入本机 Codex / Claude / Cursor 或「月瑀助手」的 Agent，提供统一的工作流自主排障入口、标准化排查决策树与员工/管理员权限边界。

> **核心底线**：始终将**环境安装、项目配置、飞书身份、付费模型执行、人工验收**五层严格分离。Agent 可以连续执行只读诊断，但**绝不自动执行写操作、扣费或修改生产工作流**；任何写入、重排、改稿或补丁都必须先输出透明预览并经对应权限角色人工确认。禁止通过重装、清库、清锁或覆盖账本来掩盖未知状态。

---

## 一、同事如何在本机 Codex / Claude / Cursor 自主排查（无需找管理员）

同事无需登录管理员服务器、无需索取管理员口令或模型 API Key，只需在自己电脑的 Codex、Claude Desktop / Claude Code 或 Cursor 中挂接标准 stdio MCP 工具，即可在受控权限内自主完成排障、预审、改稿与受控修复。

### 1. 选择对应的 MCP 入口

| MCP 入口 | 适用场景 | 鉴权与隔离方式 | 边界说明 |
| --- | --- | --- | --- |
| **`employee-mcp`**（真实业务员工受限通道） | 日常线上/测试项目排障、查任务日志、脚本预审改稿、受控失败任务修复重排、隔离副本源码修复预演 | 由管理员提前为本项目签发短期员工配置（`vf-business-employee-v1`，最长 2 小时），同事通过本人飞书官方设备授权换取短期项目凭证（最长 1 小时），请求严格限定在 `/v1/employee/*` | 员工不接触安装目录、管理员主密钥、用户长效 token 或模型 API Key；管理员可随时 `employee-suspend` 吊销 |
| **`project-mcp`**（项目连接 / 本地隔离演练通道） | 连接已授权的项目受限委托凭证，或在本机 `fake-only` 隔离演练目录中验证排障与修复流程 | 仅读取当前受控会话的 `connection.json` 或受限委托凭证（`vfctl workbench-delegate issue` 按 `repair` / `edit` / `review` / `cost` 分角色签发） | 明确区分 `fake-only` 演练与真实项目；不得把本机管理员目录或 `owner.password` 直接交给员工 Agent 冒充权限隔离 |

> **版本核对**：完整外部 MCP 客户端需本机安装 Node.js 22+；若所用旧固定包（如 a41）尚未包含 v0.3/M6 诊断接口（`inspect_task_logs`、`inspect_workflow_health`、`inspect_n8n_workflow`、`propose_n8n_patch`），应按对应版本的只读命令（`vf-agent` / `vfctl`）降级核查，不虚构未上线工具。

### 2. 本机客户端挂接步骤（Codex / Claude / Cursor）

1. **获取管理员交付的连接入口**：
   - 真实业务员工向管理员领取当前项目的受限 `employee_origin` 地址（仅授权通道交付，无明文密码/Key）；
   - 本地项目连接仅引用 `vf-run` 启动回执返回的临时 `connection.json` 路径（勿读取或粘贴相邻 `.token` 文件）。
2. **在客户端 MCP 配置中登记 stdio 服务**（仅写入本机私有配置，严禁提交到 Git 或粘到群聊）：
   - **Codex**（`~/.codex/config.toml` 或客户端 MCP 设置）：新增 MCP 服务条目，指向团队交付的 `employee-mcp`（真实业务）或 `project-mcp`（项目/演练）启动命令，并将项目标识与受限地址写入本机环境变量。
   - **Claude / Cursor**（各自 `mcp.json` / MCP Servers 配置）：以同样的 stdio 命令与私有环境变量挂接；若同时加载本 `video-factory-setup` Skill，Agent 会自动遵循下文决策树。
3. **完成本人飞书设备授权**（`employee-mcp`）：
   - 首次调用时按返回的飞书官方授权地址完成本人确认，领取短期应用凭证；设备码与上游 token 不落盘到聊天记录。
4. **先做只读连通自检**：
   - 先调用 `capabilities`、`daily_triage` 或 `project_stats`，确认当前项目标识、后端类型（无 n8n 本机常驻 / 现有 n8n）、只读/写权限范围正常，再进入排障流程。

---

## 二、标准化排查决策树

无论在「月瑀助手」悬浮窗，还是在本机 Codex / Claude / Cursor 中，都必须按以下四步顺序排查，严禁跳过只读查证直接重试或改配置：

```text
Step 1 查今日巡检
  daily_triage / project_stats / list_failures / list_reviews
  └─ 锁定异常任务与状态分类（submission_unknown / failed / stuck / awaiting_review）
        │
        ▼
Step 2 查任务日志
  inspect_task_logs(project_id, task_key)
  └─ 读取脱敏后的错误码、阶段流转、版本号、SKU/参考图绑定与供应商回执摘要
        │
        ▼
Step 3 查引擎健康 或 n8n 节点
  ├─ 无 n8n 本地常驻引擎 ──► inspect_workflow_health（引擎在线态、SQLite 账本、飞书长连接、硬预算余量、用户级服务）
  └─ 现有 / 便携 n8n ────► inspect_n8n_workflow（节点参数、连线、允许字段与最近失败执行，自动脱敏凭据；或读取 vf-workflow-observation-v1 只读快照）
        │
        ▼
Step 4 精准分流修复（先生成透明预览卡 / Diff，人工确认后再执行）
  ├─ 分支 A：受控失败任务重试（failed 且确认无远端扣费）
  ├─ 分支 B：脚本预审与自然语言改稿（awaiting_review 或需修订）
  ├─ 分支 C：飞书 1254002 / 影子副本未决写入对账
  ├─ 分支 D：n8n 节点受控补丁（员工仅建停用副本，管理员负责正式保存）
  └─ 分支 E：submission_unknown 严禁自动重排（先对账）
```

### 五类故障精准分流与操作规范

| 故障分流 | 判定依据（须由只读步骤证实） | 允许的精准动作 | 红线与禁止事项 |
| --- | --- | --- | --- |
| **A. 任务重试 / 受控重排** | 状态明确为 `failed`，日志证实无远端供应商回执且未产生扣费（如明确的脚本生成前置失败、可恢复任务状态错误） | 生成 `repair_and_schedule` 预览卡（说明现状、保留失败 v1 → 新建 v2、明确预算/费用、锁定 `expected_version` 与 `confirmation_id`），人工确认后排入队列并跟踪异步完成状态 | 受项目持久化硬预算约束；未确认前不入队；不得覆盖或删除失败历史版本 |
| **B. 脚本预审与自然语言改稿** | 任务处于 `awaiting_review` 或员工提出明确改稿意见 | 先调用 `review_or_edit_script`（`ai_prereview` 规则预审，¥0），再调用 `edit_preview` / `edit_generate_preview` 展示新旧 Diff 与费用说明，确认后提交新版本并核对飞书回写状态（`synced` / `reconciliation_required`） | 规则预审仅供参考，不能代替人工审核；审核通过脚本绝不自动授权视频费用 |
| **C. 飞书 `1254002` / 影子副本对账** | 飞书写入返回 `1254002`、写入超时或 `board_writeback=reconciliation_required` | **先确认调度器已停机**，由管理员在安装环境运行 `python -m video_factory.shadow_copy_recovery inspect` 只读核对冻结票据与远端行（严格区分 `matches_pending` / `matches_prior` / `neither`），再按 `resolve` 显式收口 | **严禁清空写入意图或盲目重发远端写入**；`1254002` 不等于远端未提交 |
| **D. n8n 节点受控补丁** | 现有/便携 n8n 路线，经 `inspect_n8n_workflow` 定位到允许字段问题（如分钟调度参数、便签、节点说明等） | 通过 `propose_n8n_patch` 生成字段级 Diff 预览与结构校验；员工仅可确认创建**停用预览副本**供验收；管理员核验后可执行停用状态下安全保存、回读校验与一键回滚 | 严禁修改凭据、连接、任意命令或敏感代码字段；员工禁止直接写回或激活生产工作流；结构校验不等于运行验收 |
| **E. `submission_unknown` 禁重排** | 出现 `submission_unknown`、`reconciliation_required`、`PROVIDER_HTTP_<状态码>_NO_RESUBMIT`、`busy` 或无回执连接中断 | **立即停止重排**；优先查询原回执与供应商/飞书记录，保留现场和锁，交由管理员完成人工对账确认未重复生成或扣费后再决定下一步 | **绝对禁止自动重排、清锁或重复提交付费请求**；404/429/500 都不能当作“未扣费”的证明 |

---

## 三、员工 vs 管理员权限边界矩阵

同一套排障大脑和 MCP 工具箱面向不同身份执行严格的权限围栏。当员工发起超出自身权限的操作时，助手或 Agent **只输出诊断结论与结构化提案（标记 `requires_admin_approval`）**，禁用员工侧确认按钮并提示“已生成诊断与提案，需管理员确认执行”。

| 操作类别 | 员工（Employee） | 管理员（Admin / 负责人） | 说明与护栏 |
| --- | --- | --- | --- |
| 全部只读查数、巡检、查任务日志、查引擎/n8n 健康、AI 规则预审 | ✅ 自由使用 | ✅ 自由使用 | 零写操作、零模型扣费；输出自动脱敏 |
| 脚本自然语言改稿（预览 → 确认写回） | ✅ 预览后可确认 | ✅ 开放 | 锁定 `expected_version` 与摘要；保留旧版本 |
| 确认失败任务的受控修复与重排 | ✅ 预览后可确认 | ✅ 开放 | 仅限明确 `failed` 且无未知扣费；受项目硬预算上限约束 |
| 隔离账本的固定类型源码修复预演（`single-select-decoder-v1`） | ✅ 需管理员独立开启 `repair` 范围 | ✅ 开放 | 仅修改隔离副本，不含任意代码、路径或生产写入 |
| n8n 节点受控补丁 —— 创建**停用预览副本** | ✅ 预览后可确认 | ✅ 开放 | 副本默认停用，不改动、不激活生产工作流 |
| n8n 生产工作流正式保存、启停、激活与回滚 | 🔒 仅可生成诊断与补丁提案（需管理员批准） | ✅ 核验后确认执行 | 须先停用或回读版本指纹，保留回滚路径 |
| 付费视频生成批准（`approve-video` / 计费确认） | 🔒 仅可准备计划与诊断提案（需管理员批准） | ✅ 逐单确认执行 | 脚本通过绝不自动放行视频付费 |
| 核心配置变更（`vf-setup`、模型/区域/成员/预算上限、员工接入签发与吊销） | 🔒 禁止直接操作 | ✅ 在私有终端执行 | 改配置先停机，变更自动吊销旧员工短期凭证 |
| `submission_unknown` 人工对账与飞书 `1254002` 影子副本 `resolve` | 🔒 仅可只读上报证据，禁止重排 | ✅ 停机核对后收口 | 必须回读远端记录与供应商回执后再处理 |

---

## 四、排障收口与证据清单

每次排障或修复结束后，按以下结构给出简明收口报告，不输出密码、API Key、个人 token、私钥或原始敏感日志：

1. **排查轨迹摘要**：列出已执行的只读检查项及结果（如巡检 → 任务日志 → 引擎健康 / n8n 节点）。
2. **根因与分类**：明确归属（任务失败 / 脚本待改 / 飞书 `1254002` / n8n 节点 / `submission_unknown`）及精确错误码。
3. **执行与权限状态**：说明是员工已确认执行，还是已生成提案等待管理员确认；列出新版本号、飞书回写状态（`synced` / `reconciliation_required`）或 n8n 停用副本 ID。
4. **五层分离核对**：分别记录 `环境安装 / 项目配置 / 飞书身份 / 付费模型执行 / 人工验收` 当前状态；异步任务在进入 `awaiting_review` 并经真人审核前，不得宣称“已完成验收”。

更多分层错误码与底层命令，参见 [检查与修复入口](troubleshooting.md)、[无 n8n 本机常驻路线](local-business.md)，以及仓库文档 [工作流排障与精准修复规则手册](https://github.com/ChuluuMGL/video-factory/blob/main/docs/product/REPAIR_PLAYBOOK.md) 与 [排障与问题反馈](https://github.com/ChuluuMGL/video-factory/blob/main/docs/product/TROUBLESHOOTING.md)。
