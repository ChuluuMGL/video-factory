---
name: video-factory-setup
description: Install, configure, resume, inspect and repair a customer's self-hosted Video Factory CLI and n8n/PostgreSQL stack. Use when setting up Video Factory, adding a project, connecting Feishu Base, inspecting a failed setup, or planning a backup, restore or version upgrade. Distinguish CLI installation, infrastructure, Feishu identity and actual task acceptance.
---

# Video Factory 安装与维护

这是客户自托管产品的操作 Skill，配合固定版本发行包中的 `vfctl`。每客户独立服务器、账户、数据库和密钥；一个客户内可建多个项目。Skill 是 Agent 的安装维护指南，飞书 Base 是团队默认的任务入口。产品边界见随包 `docs/product/PRODUCT_SHAPE.md`。

## 开始前

0. 从安装页面或首次对话进入时，先读 [首次进入与获取发行版](references/start.md)。向用户提供首次部署、新增项目、续接、检查修复四种入口；安装结束按 [交接模板](references/handoff.md) 报告。不要把内部 CLI 指令清单当作用户交付。
1. 读取 [操作流程](references/operations.md)，定位用户请求属于安装、续接、新项目、检查还是恢复。沿用当前会话已授权的对象和动作；缺少确切主机、目录或客户/项目时先做独立的只读检查，不猜测目标。
2. 确认当前机器确实是目标客户 Linux x86_64 主机。远程连接必须已有可信 SSH 主机指纹；禁止为了通过连接而关闭指纹检查。安装 CLI 当前要求 Python 3.12 + venv、root；运行服务另需本地 Docker/Compose。
3. 固定版本、来源提交、发行包 SHA256 和清单 SHA256。先验证归档，再解压或执行包内代码。校验值必须来自可信交付记录；包内自报哈希不是发布者身份验证。
4. 只用该包安装的 CLI 绝对路径。不要把 `isolated/vf` 或旧四项目 canary 当作客户安装器。不创建中央客户数据库，不自动启动旧 ECS、不另租服务器。

## 安装、配置与接入

- 读取 [完整安装路径](references/complete-install.md) 并核对固定发行包。已有的常驻网页审核实现不是默认安装步骤；不得为了完成安装而部署网页或配置业务子域名。

- 按操作流程安装 CLI；JSON 的 `cli_installed` 只说明 CLI。普通安装不启动服务、不安装 Docker、不修改客户原版本。
- 人工使用 `setup-run` 欢迎向导，在真实私有 TTY 中隐藏输入密码/App Secret。Agent 不代收秘密到聊天、命令参数、答案文件、日志或 Git。飞书本人授权在飞书完成，回到终端核对绑定计划；不部署产品网页。确实无法提供私有 TTY 时才显式启用只监听 loopback 的一次性输入页，并通过可信 SSH 隧道交给用户；不得放在公开域名或官网。
- Agent 自动填非秘密配置使用 `setup --json` 返回的 `next_question.input_schema`，逐题提交答案和当前 `revision`。不要把终端提示的 SKU 文件路径当作 Agent JSON 数组，不缓存旧字段格式。
- 同客户新项目用独立 session + `--from-session`，复用同一 stack；不同客户不能复用。目标变化后重新核对绑定与计划。
- 默认飞书 Base + n8n。a25 支持新建（默认）及绑定；新建选择 test / production、位置和员工身份，授权后审阅字段及初始化内容，再创建并回读实际 ID。绑定路径需要已有表/字段 ID。模型选择是配置意图，不能据此宣称所有模型已支持、已验证或已开始生成。
- 飞书连接完成后停止 Setup，回读 Base 和人员。发布含 `feishu-review` 的固定版本后，核对专用飞书应用对目标 Base 的管理权限，以及应用身份和用户身份的 `bitable:app`、应用身份的 `docs:event:subscribe`；先按随包 `docs/product/FEISHU_NATIVE_REVIEW.md` 启动私网 runner，由管理员在私有终端启用 Base 订阅。原任务表的“状态”必须是包含六个审核状态的单选列；旧文本列先核对整列数据再迁移，不能直接覆盖。首次配置随后在开发者后台添加记录变更事件、选择长连接并发布；已有事件核对配置，不因升级重复删除。用真实 Base 的非审核测试变更核对事件日志、服务器队列和处理回执。员工只在原任务表看稿、填意见并从单选状态中选择通过或退回，不使用终端。当前已交付版的 `stack-feishu-session` 仍只验证授权、导入与本地账本，不能代替员工流程。可选 `base-results` 是另一张结果快照表，不是审核入口。真实事件与真人验收通过前，不得称项目可供员工日常使用。
- 需要 n8n 自动推进已批准任务时，另行审阅并部署项目私网 `runner`，再执行 `production-setup`。它不开放员工网页或公网端口；具体步骤和未验收边界见[完整安装路径](references/complete-install.md)。

## 故障与验收

- 先查 [检查与修复入口](references/troubleshooting.md)，只读核对当前 CLI 版本、Setup、stack 状态及准确错误码，再判断失败层。不要重装覆盖未知状态或直接改数据库“修正”为成功。已有 Issue 只是线索，不能代替本机回读；新问题由 Agent 准备脱敏复现，交客户或实施人员审核后提交。
- 遇到 `DOCKER_ADDRESS_POOL_EXHAUSTED`，只读核对 Docker 网络及连接容器；只清理确认无容器且可重建的旧测试网络，再续接原 session。容器未启动时不得称飞书已授权。
- 新建 Base 回执保存在客户数据库。读取失败后用原 session 续接；创建结果未知或恢复了未完成检查点时，读取 `setup-feishu status` 并核对远端，禁止直接清理日志重建。详见随包 `docs/product/BASE_CREATION.md`。
- 已完成绑定的 Base 续接应保留用户后续修改的商品内容和测试任务脚本；核对原记录及 SKU、任务编号。身份字段或记录缺失时停止对账，不把内容改回初始化值。
- 已存在同版本 CLI 可校验复用；半安装 CLI 目录保留，改用新的空前缀诊断安装。客户 stack 与 session 必须放在 CLI 目录之外。
- `setup-run` 退出后使用原 session/stack 续接。恢复、升级及停机备份需依照用户已授权的具体对象和停机范围；未获授权时先给出具体计划，不自行扩展授权。
- 模型提交、付费重试、员工审核、外部发布分别遵循用户授权；安装或修复指令本身不包含这些动作。未知模型提交不得重发，先查询已有供应商回执。
- 明确认证拒绝且没有供应商回执时，更换密钥或接口区域后，可按 `docs/product/WORKER_USAGE.md` 使用 `stack-worker recover-auth`。保留失败尝试与原人工审核，重新核对并批准执行计划；恢复本身不会付费提交，不能用于结果未知或已有回执的任务。
- 输出阶段证据：`CLI 安装 / 服务启动 / 项目导入 / 飞书真实接入 / 任务执行 / 真人验收`。逐项记已通过、失败或未执行，并注明证据来源。绿色 CI、Mock OAuth、`plan_ready` 或 `business_ready=false` 不表示客户业务已验收。
- 结束给出当前版本、确切对象、完成步骤、剩余缺口和可续接命令；不输出秘密、一次性管理员链接或完整敏感日志。

## 结果同步注意

仅在实际 CLI 支持 `base-results` 时，按安装包内 `docs/product/BASE_RESULTS.md` 检查版本、项目和最小权限。a31 不含此能力。结果同步默认关闭，未知上传或恢复检查点须停止并核对，不能清日志重试；未完成真实验收前不向客户承诺该项目已跑通。
