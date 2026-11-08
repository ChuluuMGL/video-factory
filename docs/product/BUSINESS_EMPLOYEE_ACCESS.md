# 本地业务实例的员工接入候选

本接口使用真实业务安装的同一 SQLite 账本与 Agent API，默认关闭。管理员仍在本机 CLI 配置项目、保管文件、批准费用；员工使用独立客户端和本人飞书设备授权，不接触安装目录、主密钥、用户令牌或执行密钥。尚未发布，合成 OAuth 测试不能替代真人身份验收。

管理员准备私有 0600 配置，内容为 schema `vf-business-employee-v1`、与计划一致的 `project` 和 `tenant_key`、唯一 `open_id`、明确的 UTC 秒截止值 `expires_at`（未来两小时内）及布尔 `allow_plans`。只读为 false；若允许提出计划，必须同时在 inspect/start 开启 `--allow-plans`，且该 open_id 已在项目 submitters 名单内。不因审核人身份自动授予提出计划权限。

在原 `local-run inspect`、`local-run start` 命令同时加 `--employee-config /绝对私有路径/employee.json`。配置摘要进入启动计划；修改后重新检查计划，不复用旧哈希。服务仍只监听 127.0.0.1；不创建公网入口或远程通道。启动回执中的 employee_origin 是员工客户端目标地址，必须由管理员交付已有授权访问方式。

员工客户端向 connect/start 请求设备授权，用本人飞书账号在官方授权页确认，再 connect/poll 领取一次应用凭证；设备码和 Feishu token 不交给员工客户端或记录。凭证最长一小时且不超过当前窗口/上游身份有效期。只读凭证可读任务状态、版本和计划回执；计划凭证增加原表绑定任务的修订计划、内容和已批准计划提交。不能通过请求选择其他项目、改变身份、任意源代码、直接修复生产、批准费用或调用 Shell。

员工请求只允许进入 `/v1/employee/*`。即使使用同一 token 访问安装者 `/v1/agent/*`，也会拒绝；不会使用管理员已登录的飞书身份替员工准备计划。原表任务、审核反馈、计划摘要、未知结果和单次提交约束继续由现有内核检查。管理员暂停只撤销员工接入，不取消已由负责人批准的执行队列，也不声称撤回已完成修改。

管理员用 `local-run employee-status` 查看状态和最近审计；`employee-suspend` / `employee-resume` 需附 `--expected-employee-version` 与唯一 `--request-id`。暂停状态持久保存，切换状态会作废旧配对及凭证。恢复后重新配对；重启和停机也撤销员工凭证。配置换人或改变计划权限不能悄悄接管旧身份绑定，须另行准备新的隔离实例。

## 已有工作流的只读观测

可选 workflow_source 配置包含私有快照 path、精确 sha256 和 workflow_ids 白名单。快照 schema 为 `vf-workflow-observation-v1`，仅含项目、UTC 秒 observed_at、environment=`production-readonly`、workflow 列表（id、active、version_id、source_sha256、latest_execution）。latest_execution 仅允许 id 与枚举状态，不包含原始执行数据、配置、工作流代码或凭据。

必须由管理员通过既有授权只读渠道另行采集并核对；本入口不连接 SSH、不刷新生产、不修改 n8n。读取时检查同一文件摘要、名单和最长 15 分钟时效，明确返回 live=false。快照过期只阻止工作流观测，不自动重采或延长授权。故障诊断不能把旧快照、历史隔离样例或 status=success 当作生产交付证明。

验证应覆盖：同一业务实例管理员与员工共存、错误企业/人员拒绝、原表退回后员工本人身份重规划、费用批准隔离、撤权/重启、过期/篡改快照拒绝、无真实模型调用。现有生产仍须另有部署与回退决定；本接口不会自动升级它。

## 独立的源码修复授权

可另行选择固定的 `repair` 配置，让同一员工在业务账本的隔离副本里完成源码修复、验证、应用与回退；与付费计划权限独立，默认不开放。只有已审核的单选状态解析器源码类型可用，无任意代码、路径、生产写入。详见 [业务源码修复](BUSINESS_SOURCE_REPAIR.md)。
