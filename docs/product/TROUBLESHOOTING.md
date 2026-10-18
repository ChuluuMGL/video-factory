# 排障与问题反馈

本页供管理员、实施人员和 Agent 排查自托管安装。先确认正在运行的 `vfctl` 版本、项目、步骤和**准确错误码**，再读对应章节；不要仅凭页面提示或 CI 通过判断任务已完成。客户服务器、飞书 Base、n8n 和模型服务分别回读。

本机或已有 n8n 使用[a36 便携路线指南](PORTABLE_N8N.md)，固定服务器包仍要求 Ubuntu 24.04 x86_64。先核对路线与版本，不能把服务器包的限制套到便携路线。遇到 `UBUNTU_2404_X86_64_ROOT_REQUIRED` 等平台错误时，检查是否误用了服务器 bootstrap；保留现有 n8n，不绕过检查。

| 现象或错误 | 先检查 | 安全续接 |
|---|---|---|
| 安装中断、继续安装 | 固定包版本、原 Setup session、`vfctl stack status` | 使用原 session 续接；不要删除它或重建客户数据。见[安装操作](SETUP_RUN_USAGE.md)。 |
| 外部依赖下载中断 | 含下载错误分类的版本返回 `DEPENDENCY_DOWNLOAD_INTERRUPTED`；服务器访问官方依赖文件源的网络与空闲磁盘 | 在同一已验证发行目录重试，完整且哈希匹配的 wheel 会复用；下载不完整的字节不会成为 wheel。不输入产品密码、不重建服务、不调用模型。 |
| `DEPENDENCY_DOWNLOAD_HTTP_REJECTED` | 官方依赖源访问是否被代理或网络策略拒绝 | 恢复受信下载渠道后续接；不换未知源、不关闭 TLS 或哈希校验。若还只有 `CLI_BOOTSTRAP_FAILED`，保留现场并核对该固定版本是否含新分类。 |
| `DOCKER_ADDRESS_POOL_EXHAUSTED` | Docker 网络、附着容器的状态与 Compose 目录；区分当前栈、Runner 和停用演练栈 | 只释放已确认停用且保留恢复材料的旧测试栈网络；先复测授权 worker 和 Runner，再恢复原 session。见下节。 |
| 主机重启后所有容器都没起来 | `systemctl is-enabled docker`、`systemctl is-active docker`，再查目标 stack 的容器健康状态 | 若 Docker 被禁用，管理员先确认主机策略，再启用开机启动并做一次停机重启验收；容器健康不等于任务账本和事件队列已恢复。 |
| 飞书授权过期或 `FEISHU_OAUTH_DENIED_OR_EXPIRED` | 授权页时限、本人账号、终端是否仍在等待 | 重新发起**一次**本人授权；先回读本地状态，不因授权超时重建 Base。 |
| 新建 Base 结果不明 | `setup-feishu status`、远端 Base 和表是否已存在 | 停止自动重试；核对创建日志与远端 ID。见[Base 创建与恢复](BASE_CREATION.md)。 |
| 本人建表成功，但应用读取或启用审核被拒绝 | 专用应用 ID、目标 Base 的文档应用列表、应用 API 权限；保留具体飞书错误码 | 本人建表授权不等于应用已获该表访问权。在该 Base 的“更多 → 添加文档应用”核对专用应用及权限，提交前由管理员确认范围；先复测应用读取，再启用审核与订阅。不要重建 Base、换审核人或重试模型。见[原表审核](FEISHU_NATIVE_REVIEW.md)。 |
| 表内审核未推进 | 原行的状态、审核目标版本、SKU、脚本或视频、事件接收器及持久队列 | 先确认员工在原表操作及事件送达。退回必须先保存具体意见，再选择退回；事后修改意见不会改写回执。见[原表审核](FEISHU_NATIVE_REVIEW.md)。 |
| 停机期间改了审核状态 | 原表当前状态、事件队列、服务端任务版本与审核回执 | 表格状态不能补作已认证事件。先恢复接收器并确认在线；在同一测试记录由本人恢复为待审核，保存具体意见后重新选择通过或退回，再核对操作者、版本与回执。客户业务记录应先保留现场并制定修复计划，不直接代改状态。 |
| 模型提交超时、状态不明 | 本地任务状态、供应商回执 ID、已保存的产物 | 停止重发；按原任务核对供应商结果后再决定修复。不能为了排障再次付费提交。 |
| `PROVIDER_AUTH_REJECTED_CHECK_REGION_OR_KEY` | Key 所属区域、原计划摘要、是否确实没有供应商回执 | 含 `recover-auth` 的版本允许修正凭据后归档失败尝试，保留原脚本审核，再核对并授权新的生成计划。禁止用于未知提交或已有回执。见[执行器恢复](WORKER_USAGE.md#认证失败后更换密钥)。 |
| 视频或附件未回写 | 媒体文件解码和 SHA-256、附件上传回执、远端记录和写入意图 | 先回读并对账，不删除旧媒体或伪造“审核通过”。见[结果同步](BASE_RESULTS.md)。 |
| 升级或恢复后服务不可用 | 旧、新 stack 路径、容器、数据库、备份和恢复回执 | 确认当前运行版本及回退路径；备份存在不等于恢复成功。见[Stack 操作](STACK_USAGE.md)。 |

## a36 便携路线：终端与后台运行

| 现象 | 处理方式 |
|---|---|
| 菜单或地址输错 | 在当前向导重输；输入 `:quit` 保留已完成配置。继续时使用原目录与项目标识。 |
| `PRIVATE_TERMINAL_REQUIRED` | Agent 的输出管道不是私密输入终端。由安装人员在自己的终端输入；不要把 Key 发给 Agent 或写进命令参数。 |
| `N8N_API_PERMISSION_REQUIRED` | 核对实例地址与公共 API Key；向导允许最多三次只读认证，不重建项目。 |
| `SERVICE_START_NOT_VERIFIED_DATA_PRESERVED_RUN_STATUS` | 使用同一 `--root`、`--component` 运行 `status`，检查该服务的系统状态、执行路径和端口。数据保留，不删目录重装。 |
| `SERVICE_DEFINITION_CHANGED_PRESERVED` | 原启动定义被外部修改，安装器停止接管；先比较保存的定义与系统定义，不能强制覆盖其他人的配置。 |
| 关闭终端后运行，但重启电脑后没有启动 | Mac/Windows 默认在当前用户登录后启动；普通 Linux 用户需要登录或已配置 linger；Docker 需其自身服务启动。详见[自启方式](PORTABLE_N8N.md#后台运行与自启)。 |
| `SERVICE_N8N_NOT_READY_NO_EVENT_DISPATCH` | n8n 数据库尚未就绪；检查 `/healthz/readiness`，不能仅看 `/healthz`。等待恢复后再检查服务，保留事件队列；不要重发付费任务。 |
| 飞书接收器启动了但审核没有推进 | OS `running` 只证明进程存在。继续核对应用、事件订阅、同应用是否有其他接收器、真实 SDK 回执及未知派发状态。不要直接重发付费任务。 |

取消自启使用 `uninstall`，保留数据；`stop` 只暂停当前进程，后续登录或开机可能再次启动。

## 收到负责人确认，但没有开始生成

a36 便携路线的 `APPROVAL_RECORD_CHANGED`、`APPROVAL_STATUS_CHANGED`、`APPROVAL_CONFIRMATION_NOT_CURRENT`、`APPROVAL_DISPLAY_CHANGED` 分别表示当前任务、状态、确认编号、展示计划不匹配；`APPROVED_INPUT_CHANGED` 表示已审核内容改变。旧候选合并报 `APPROVAL_TASK_OR_PLAN_CHANGED`，不能据此确定是哪项改变或认定为网络故障。

先回读当前行、受保护审批与执行账本，以及 n8n 事件日志的节点轨迹。默认不保存完整执行数据，执行 API 没有记录不等于模型未调用。保留失败事件，不清队列、不自动重发；仅在确认未消费计划、未到模型节点、原计划未过期且当前内容一致后，再由负责人决定续接。原始日志仅保留在受控环境。详见[费用确认排查](PORTABLE_BILLING_APPROVAL.md#失败与停用)。

## a37 费用计划与打包检查

`APPROVAL_MODEL_PROFILE_CHANGED_REPLAN` 表示原计划版本、模型、接口或参数与当前执行流程不一致，已在模型调用前停止。先暂停该项目收费入口，核对原执行账本和供应商回执；没有在途或未知请求时，显式取消旧计划、申请新计划，再让负责人重新确认。不要修改账本中的计划版本、复用旧费用编号或直接重放事件。

维护者打包遇到 `TRACKED_SKILL_REQUIRED`、`SKILL_SOURCE_MUST_BE_REGULAR` 或 `SKILL_SOURCE_LINK_REJECTED`，应在干净 Git checkout 中核对 Skill 文件是否已跟踪、是否为普通文件，以及路径是否经过符号链接。不要通过把 `.env`、本地安装目录或客户配置加入 Git 来通过检查。客户直接使用固定 Release；无须自己打包。

## Docker 地址池用尽

停止容器后，Compose 网络仍可能占用地址段。多次升级和恢复演练会累积旧网络；当前基础服务健康，也不能证明新的授权 worker 或项目 Runner 能启动。这不是密码或模型 Key 错误。

1. 只读核对容器状态、网络附着关系及 Compose 工作目录，确认新栈健康和原审核记录、媒体摘要不变。
2. 对明确归属于旧测试栈、全部停用且可从配置重建的容器，先保存私有检查记录，再按该栈的 Compose 文件执行 `down`。保留原配置、数据、备份密钥、数据卷和镜像；不使用全局 prune 或删除卷。
3. 分别复测短时授权 worker、私网 Runner 的健康与端口，再续接原 Setup session。不要因网络失败重建 Base 或重发付费任务。

若失败后向导已关闭管理员会话，续接仍需本人隐藏输入密码；先完成启动预检，再让用户输入。n8n 调度恢复后还须回读实际执行记录，不能只凭发布成功提示验收。

上述恢复方式已在 a34 测试机验证；不同 Docker 地址池、网络归属或客户环境仍需现场核对，不能自动套用测试栈清理动作。

这些是诊断入口，不保证每种故障都能由 Agent 自动修复。涉及权限扩张、密钥轮换、数据恢复、模型重试或员工审核时，按各自的操作边界处理。Agent 可先做只读诊断，给出**证据、修复计划和验收结果**。

遇到本页没有覆盖的问题，先搜[现有 Issues](https://github.com/ChuluuMGL/video-factory/issues)，再用[问题模板](https://github.com/ChuluuMGL/video-factory/issues/new/choose)提交**脱敏的最小复现**：版本、阶段、错误码、期望与实际结果、已做的检查。不要提交密码、API Key、App Secret、服务器 IP、真实飞书 ID、客户内容、原始日志或未脱敏截图。安全漏洞使用[私密报告](https://github.com/ChuluuMGL/video-factory/security/advisories/new)。客户或实施人员审核后自行提交；Agent 不自动公开发送诊断资料。

Issue 是待核查的问题记录，不能直接当作已验证的修复方案。维护者修复并验证后，在对应 PR 中更新本页及相关操作文档，注明适用版本；每次固定发行前复查索引与失效链接。关闭 Issue 不等于客户实例已升级，也不等于客户任务已恢复。
