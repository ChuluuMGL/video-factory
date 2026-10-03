---
name: video-factory-setup
description: Install, configure, resume, inspect and repair a customer's self-hosted Video Factory CLI and n8n/PostgreSQL stack. Use when setting up Video Factory, adding a project, connecting Feishu Base, inspecting a failed setup, or planning a backup, restore or version upgrade. Distinguish CLI installation, infrastructure, Feishu identity and actual task acceptance.
---

# Video Factory 安装与维护

这是客户自托管产品的操作 Skill，配合固定版本发行包中的 `vfctl`。每客户独立服务器、账户、数据库和密钥；一个客户内可建多个项目。Skill 是 Agent 操作指南，实际服务由客户服务器上的容器运行。

## 开始前

0. 从安装页面或首次对话进入时，先读 [首次进入与获取发行版](references/start.md)。向用户提供首次部署、新增项目、续接、检查修复四种入口；安装结束按 [交接模板](references/handoff.md) 报告。不要把内部 CLI 指令清单当作用户交付。
1. 读取 [操作流程](references/operations.md)，定位用户请求属于安装、续接、新项目、检查还是恢复。沿用当前会话已授权的对象和动作；缺少确切主机、目录或客户/项目时先做独立的只读检查，不猜测目标。
2. 确认当前机器确实是目标客户 Linux x86_64 主机。远程连接必须已有可信 SSH 主机指纹；禁止为了通过连接而关闭指纹检查。安装 CLI 当前要求 Python 3.12 + venv、root；运行服务另需本地 Docker/Compose。
3. 固定版本、来源提交、发行包 SHA256 和清单 SHA256。先验证归档，再解压或执行包内代码。校验值必须来自可信交付记录；包内自报哈希不是发布者身份验证。
4. 只用该包安装的 CLI 绝对路径。不要把 `isolated/vf` 或旧四项目 canary 当作客户安装器。不创建中央客户数据库，不自动启动旧 ECS、不另租服务器。

## 安装、配置与接入

- a28 及以后带环境准备、私有浏览器输入和常驻 HTTPS 候选功能时，读取 [完整安装补齐路径](references/complete-install.md)，使用包内实际能力；不对 a27 宣称这些功能已存在。

- 按操作流程安装 CLI；JSON 的 `cli_installed` 只说明 CLI。普通安装不启动服务、不安装 Docker、不修改客户原版本。
- 人工使用 `setup-run` 欢迎向导，在真实私有 TTY 中输入密码/App Secret。Agent 不代收秘密到聊天、命令参数、答案文件、日志或 Git；不能提供私有人工终端时，a28 及以后使用仅用户操作的浏览器输入向导；旧版先完成非秘密配置并交接私有输入步骤，不伪装交互已完成。
- Agent 自动填非秘密配置使用 `setup --json` 返回的 `next_question.input_schema`，逐题提交答案和当前 `revision`。不要把终端提示的 SKU 文件路径当作 Agent JSON 数组，不缓存旧字段格式。
- 同客户新项目用独立 session + `--from-session`，复用同一 stack；不同客户不能复用。目标变化后重新核对绑定与计划。
- 默认飞书 Base + n8n。a25 支持新建（默认）及绑定；新建选择 test / production、位置和员工身份，授权后审阅字段及初始化内容，再创建并回读实际 ID。绑定路径需要已有表/字段 ID。模型选择是配置意图，不能据此宣称所有模型已支持、已验证或已开始生成。

## 故障与验收

- 先读取当前 CLI 版本、Setup 状态、stack 状态及准确错误码，再判断是哪一层失败。不要用重装覆盖未知状态，或直接改数据库“修正”为成功。
- 新建 Base 回执保存在客户数据库。读取失败后用原 session 续接；创建结果未知或恢复了未完成检查点时，读取 `setup-feishu status` 并核对远端，禁止直接清理日志重建。详见随包 `docs/product/BASE_CREATION.md`。
- 已存在同版本 CLI 可校验复用；半安装 CLI 目录保留，改用新的空前缀诊断安装。客户 stack 与 session 必须放在 CLI 目录之外。
- `setup-run` 退出后使用原 session/stack 续接。恢复、升级及停机备份需依照用户已授权的具体对象和停机范围；未获授权时先给出具体计划，不自行扩展授权。
- 模型提交、付费重试、员工审核、外部发布分别遵循用户授权；安装或修复指令本身不包含这些动作。未知模型提交不得重发，先查询已有供应商回执。
- 输出阶段证据：`CLI 安装 / 服务启动 / 项目导入 / 飞书真实接入 / 任务执行 / 真人验收`。逐项记已通过、失败或未执行，并注明证据来源。绿色 CI、Mock OAuth、`plan_ready` 或 `business_ready=false` 不表示客户业务已验收。
- 结束给出当前版本、确切对象、完成步骤、剩余缺口和可续接命令；不输出秘密、一次性管理员链接或完整敏感日志。

## 证书轮换候选

仅在当前 CLI 确实支持 `workspace-tls` 时，按 [证书轮换说明](https://github.com/ChuluuMGL/video-factory/blob/main/docs/product/WORKSPACE_TLS.md) 检查到期状态并核对计划。轮换只操作本项目私有证书；失败先核对旧证书和回执。此入口不代表 ACME 自动签发或定时续期已实现。公开 a31 不包含该候选。
