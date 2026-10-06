---
name: video-factory-setup
description: Install, configure, resume, inspect and repair a customer's self-hosted Video Factory CLI and n8n/PostgreSQL stack. Use when setting up Video Factory, adding a project, connecting Feishu Base, inspecting a failed setup, or planning a backup, restore or version upgrade. Distinguish CLI installation, infrastructure, Feishu identity and actual task acceptance.
---

# Video Factory 安装与维护

这是客户自托管产品的操作 Skill，配合固定版本发行包中的 `vfctl`。每客户独立服务器、账户、数据库和密钥；一个客户内可建多个项目。Skill 是 Agent 的安装维护指南，飞书 Base 是团队默认的任务入口。产品边界见随包 `docs/product/PRODUCT_SHAPE_2026_10_06.md`。

## 开始前

0. 从安装页面或首次对话进入时，先读 [首次进入与获取发行版](references/start.md)。向用户提供首次部署、新增项目、续接、检查修复四种入口；安装结束按 [交接模板](references/handoff.md) 报告。不要把内部 CLI 指令清单当作用户交付。
1. 读取 [操作流程](references/operations.md)，定位用户请求属于安装、续接、新项目、检查还是恢复。沿用当前会话已授权的对象和动作；缺少确切主机、目录或客户/项目时先做独立的只读检查，不猜测目标。
2. 确认当前机器确实是目标客户 Linux x86_64 主机。远程连接必须已有可信 SSH 主机指纹；禁止为了通过连接而关闭指纹检查。安装 CLI 当前要求 Python 3.12 + venv、root；运行服务另需本地 Docker/Compose。
3. 固定版本、来源提交、发行包 SHA256 和清单 SHA256。先验证归档，再解压或执行包内代码。校验值必须来自可信交付记录；包内自报哈希不是发布者身份验证。
4. 只用该包安装的 CLI 绝对路径。不要把 `isolated/vf` 或旧四项目 canary 当作客户安装器。不创建中央客户数据库，不自动启动旧 ECS、不另租服务器。

## 安装、配置与接入

- 读取 [完整安装路径](references/complete-install.md) 并核对固定发行包。已有的常驻网页审核实现不是默认安装步骤；不得为了完成安装而部署网页或配置业务子域名。

- 按操作流程安装 CLI；JSON 的 `cli_installed` 只说明 CLI。普通安装不启动服务、不安装 Docker、不修改客户原版本。
- 人工使用 `setup-run` 欢迎向导，在真实私有 TTY 中输入密码/App Secret。Agent 不代收秘密到聊天、命令参数、答案文件、日志或 Git；确实无法提供私有 TTY 时才使用只监听 loopback 的一次性浏览器输入，并通过可信 SSH 隧道交给用户。不得把它放在公开域名或官网。
- Agent 自动填非秘密配置使用 `setup --json` 返回的 `next_question.input_schema`，逐题提交答案和当前 `revision`。不要把终端提示的 SKU 文件路径当作 Agent JSON 数组，不缓存旧字段格式。
- 同客户新项目用独立 session + `--from-session`，复用同一 stack；不同客户不能复用。目标变化后重新核对绑定与计划。
- 默认飞书 Base + n8n。a25 支持新建（默认）及绑定；新建选择 test / production、位置和员工身份，授权后审阅字段及初始化内容，再创建并回读实际 ID。绑定路径需要已有表/字段 ID。模型选择是配置意图，不能据此宣称所有模型已支持、已验证或已开始生成。
- 飞书连接完成后停止 Setup，回读 Base 和人员；任务导入及审核使用本人授权的 `stack-feishu` 分阶段命令。当前发行包尚无飞书内完整审核和自动结果回写，不得把页面测试写成该能力已通过。

## 故障与验收

- 先读取当前 CLI 版本、Setup 状态、stack 状态及准确错误码，再判断是哪一层失败。不要用重装覆盖未知状态，或直接改数据库“修正”为成功。
- 新建 Base 回执保存在客户数据库。读取失败后用原 session 续接；创建结果未知或恢复了未完成检查点时，读取 `setup-feishu status` 并核对远端，禁止直接清理日志重建。详见随包 `docs/product/BASE_CREATION.md`。
- 已存在同版本 CLI 可校验复用；半安装 CLI 目录保留，改用新的空前缀诊断安装。客户 stack 与 session 必须放在 CLI 目录之外。
- `setup-run` 退出后使用原 session/stack 续接。恢复、升级及停机备份需依照用户已授权的具体对象和停机范围；未获授权时先给出具体计划，不自行扩展授权。
- 模型提交、付费重试、员工审核、外部发布分别遵循用户授权；安装或修复指令本身不包含这些动作。未知模型提交不得重发，先查询已有供应商回执。
- 输出阶段证据：`CLI 安装 / 服务启动 / 项目导入 / 飞书真实接入 / 任务执行 / 真人验收`。逐项记已通过、失败或未执行，并注明证据来源。绿色 CI、Mock OAuth、`plan_ready` 或 `business_ready=false` 不表示客户业务已验收。
- 结束给出当前版本、确切对象、完成步骤、剩余缺口和可续接命令；不输出秘密、一次性管理员链接或完整敏感日志。
