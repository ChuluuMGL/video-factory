---
name: video-factory-setup
description: Install, configure, resume, inspect and repair a customer's self-hosted Video Factory CLI and n8n/PostgreSQL stack. Use when setting up Video Factory, adding a project, connecting Feishu Base, inspecting a failed setup, or planning a backup, restore or version upgrade. Distinguish CLI installation, infrastructure, Feishu identity and actual task acceptance.
---

# Video Factory 安装与维护

这是 Video Factory 的安装与维护 Skill。首次进入先识别用户已有的 n8n、飞书和运行环境，再选择路径；不能预设每位用户都需要新购 Ubuntu 服务器。已安装客户可复用同一套服务新增项目。Skill 是 Agent 的安装维护指南，飞书 Base 是团队默认的任务入口。产品边界见随包 `docs/product/PRODUCT_SHAPE.md`。

## 开始前

0. 从安装页面或首次对话进入时，先读 [首次进入与获取发行版](references/start.md)。向用户提供首次部署、新增项目、续接、检查修复四种入口。**首次部署先运行或照着 `scripts/choose_install_path.py` 的三个问题询问**：现有 n8n 在哪里、飞书应用/Base 是否已有、产品执行服务打算放在哪里。这个脚本只识别环境，不安装软件、不读取密钥、不扫描远端。已有 n8n 时先保留原实例与凭据，不创建第二套 n8n。安装结束按 [交接模板](references/handoff.md) 报告。
1. 读取 [操作流程](references/operations.md)，定位用户请求属于安装、续接、新项目、检查还是恢复。沿用当前会话已授权的对象和动作；缺少确切主机、目录或客户/项目时先做独立的只读检查，不猜测目标。
2. 先读 [平台与模型矩阵](https://github.com/ChuluuMGL/video-factory/blob/main/docs/product/PLATFORMS_AND_MODELS.md)。按识别结果告知当前可执行的路线和缺口。**服务器完整栈部署**在 Ubuntu 24.04 x86_64 主机执行，要求 Python 3.12 + venv、root 和本地 Docker/Compose；远程连接须核对可信 SSH 主机指纹。已有本机 Node.js/Docker n8n、其他服务器 n8n 或 n8n Cloud 时，可先只读检查 API 和飞书环境；a37 可使用下方便携向导配置已有实例；不要运行完整栈命令覆盖已有 n8n。接入与验收按 [现有 n8n 路线](https://github.com/ChuluuMGL/video-factory/blob/main/docs/product/EXISTING_N8N_INTEGRATION.md) 完成并验收。
3. 固定版本、来源提交、发行包 SHA256 和清单 SHA256。先验证归档，再解压或执行包内代码。校验值必须来自可信交付记录；包内自报哈希不是发布者身份验证。
4. 只用该包安装的 CLI 绝对路径。不要把 `isolated/vf` 或旧四项目 canary 当作客户安装器。不创建中央客户数据库，不自动启动旧 ECS、不另租服务器。

## 便携安装与已有 n8n 接入（a37 测试版）

便携视频参考图支持「参考图附件」的候选接入（JPEG/PNG/WebP，合计最多 20 MiB）和原有 HTTPS 链接，合计最多 9 张；附件在模型调用前由 n8n 按本行权限读取并校验，不需要用户把图片公开。附件读取已通过四平台合成回归与真实飞书原图校验；一次明确授权的真实 H3 提交、查询及飞书待审核写回已完成。成片实测 6.584 秒、768×1344，与请求的 6 秒、9:16 有差异；维护者已接受本次流程测试，未将成片质量与精确规格作为通过条件。不能据此声称自动生产已交付。保留实际下载权限错误，不擅自扩权。

完整 Skill 提供实际执行脚本 `scripts/portable_n8n.py` 和通用工作流模块 `scripts/n8n_workflows.py`。便携路线优先运行 `welcome` 连续向导：先连接或安装 n8n，再复用项目凭据完成 Base 检查、字段准备及脚本/视频/恢复流程导入。续接沿用原目录、地址和项目标识；不要让用户逐个工作流重新输入同一把 Key。凭据轮换在 n8n Credentials 显式处理。按 [便携安装说明](https://github.com/ChuluuMGL/video-factory/blob/v0.1.0a38/docs/product/PORTABLE_N8N.md) 区分本机安装与已有实例接入；不要将候选的合成测试说成稳定发行或全业务验收。已有实例优先通过 `welcome` 连续配置，`check/connect` 仅作分步排障，不强制新装 Ubuntu。原生安装超时先检查磁盘和网络，保留原目录续接；必要时在子命令前显式设置 `--dependency-timeout 2400`，不要删除数据重装。收费入口需先完成本项目的真实负责人事件验收，再按单次计划显式启用。

负责人身份与费用确认使用 `portable_approval.py` 和官方长连接 `portable_events.py`，先按 [负责人确认指南](https://github.com/ChuluuMGL/video-factory/blob/v0.1.0a38/docs/product/PORTABLE_BILLING_APPROVAL.md) 做零费用验收。不能用可编辑人员列、最后修改人或状态文字充当操作人认证。不得跳过真实事件验收直接启用付费入口；同一应用已有其他长连接时先处理分流冲突，不擅自停用其他项目。 更换模型、接口或参数后必须重新准备并确认费用计划；不得把旧确认迁移给新配置。升级前保留执行回执，未知提交先对账。

便携后台自启使用 `scripts/portable_service.py`，支持 `install/status/start/stop/restart/uninstall`。只接管本安装器创建的原生 n8n；已有实例保持原管理方式。桌面端明确告知是“当前用户登录后启动”，Linux root 才是系统开机启动，普通用户不自动开启 linger。事件服务必须先核对同一应用无其他接收器，才传 `--exclusive-app`；不因开机自启擅自启用收费工作流。安装成功回执与接收器均等待 n8n `/healthz/readiness`；不能将 `/healthz` 的进程存活视为数据库就绪。安装后读取 OS 状态、重启后检查数据与事件回执；卸载注册保留所有业务数据。向导交接记录为 `bindings/handoff-PROJECT.json`，不要把“后台进程运行”写成业务验收通过。自启生命周期已通过对应四平台 CI；整机重启、桌面重新登录与独立安装的当前结果见 `docs/product/COMPLETION_WORK.md`，固定发行包能力以版本回执为准。

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
- 发起脚本生成前检查固定发行 CLI 是否支持 `stack-feishu-session generate-script`；a33 尚不支持。支持时只读取用户指定的原任务表记录，核对真实 SKU、创作要求和版本，本人飞书授权并确认单次付费计划后入队。不得启用旧网页代替此入口；脚本及成片人工审核始终留在原表。按随包 `docs/product/FEISHU_BRIDGE.md` 操作，未知提交不得重复生成。
- 已存在同版本 CLI 可校验复用；半安装 CLI 目录保留，改用新的空前缀诊断安装。客户 stack 与 session 必须放在 CLI 目录之外。
- `setup-run` 退出后使用原 session/stack 续接。恢复、升级及停机备份需依照用户已授权的具体对象和停机范围；未获授权时先给出具体计划，不自行扩展授权。
- 模型提交、付费重试、员工审核、外部发布分别遵循用户授权；安装或修复指令本身不包含这些动作。未知模型提交不得重发，先查询已有供应商回执。
- 配置脚本密钥时，核对实际 CLI 是否包含只读认证预检；不要用模型生成来测试 Key。支持预检且私网 Runner 已运行时，认证失败应保留旧凭据、报告错误码，不继续提交。认证通过只证明该 Key 可读取配置模型，不证明余额或真实生成完成。
- 审核字段接入后需要继续配置模型和调度时，先检查 `feishu-review enable --help` 是否包含 `--configure-models`。支持且本轮这些步骤已获授权时，使用连续向导复用同一内存中的短期管理认证；退出撤销，不保存密码或管理员 token 到文件。未支持的旧版本仍按独立向导操作，不能虚构已复用授权。
- 明确认证拒绝且没有供应商回执时，更换密钥、接口区域或升级官方接口地址后，可按 `docs/product/WORKER_USAGE.md` 使用 `stack-worker recover-auth`。中国区固定为 `api.minimax.cn`，国际区为 `api.minimax.io`。保留失败尝试与原人工审核，重新核对并批准执行计划；恢复本身不会付费提交，不能用于结果未知或已有回执的任务。
- 输出阶段证据：`CLI 安装 / 服务启动 / 项目导入 / 飞书真实接入 / 任务执行 / 真人验收`。逐项记已通过、失败或未执行，并注明证据来源。绿色 CI、Mock OAuth、`plan_ready` 或 `business_ready=false` 不表示客户业务已验收。
- 结束给出当前版本、确切对象、完成步骤、剩余缺口和可续接命令；不输出秘密、一次性管理员链接或完整敏感日志。

## 结果同步注意

仅在实际 CLI 支持 `base-results` 时，按安装包内 `docs/product/BASE_RESULTS.md` 检查版本、项目和最小权限。a31 不含此能力。结果同步默认关闭，未知上传或恢复检查点须停止并核对，不能清日志重试；未完成真实验收前不向客户承诺该项目已跑通。

## 已有便携项目升级（从 a38 起提供）

使用同一绑定目录，先停止本项目事件接收器并用 `approval-disable` 暂停原入口；核对正在执行的任务回执。执行 `portable_n8n.py --root <原绑定目录> upgrade-plan --url <原 n8n 地址> --project <原项目>`，向用户展示项目、工作流数量、变更数及计划摘要。明确确认该计划后，执行 `upgrade-apply` 的同样参数并加 `--expect-plan <完整摘要>`。API Key 只在隐藏终端输入。升级只修改已有关闭工作流并保留凭据引用、ID、Webhook 与账本；不创建表、不调用模型、不自动启用。未完成费用计划先用原 `approval-cancel` 逐条取消，未知执行先对账。PUT 结果未知时只能续接同一计划回读，不能删检查点或重新导入；读回仍是旧值也不自动重试。完成后做零费用检查，再明确恢复接收器和入口。旧版本不支持此命令时，不要删除绑定文件绕过校验。

## 便携多脚本模型候选

仅当完整 Skill 中包含 `scripts/portable_models.py` 时，`welcome` 才提供脚本供应商选择。该源码候选不在 a37 / a38 固定包；按 `docs/product/PORTABLE_SCRIPT_MODELS.md` 核对范围。续接沿用原模型和凭据；不同供应商使用独立项目及凭据，不能猜测 Key、改接口后沿用旧费用确认。其他模型尚需本项目真实验收，不把历史项目或合成测试说成已通过。
