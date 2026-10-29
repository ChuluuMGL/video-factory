# 本机内核与 Workflow Agent 接口候选

关联 [#80](https://github.com/ChuluuMGL/video-factory/issues/80) 与 [#81](https://github.com/ChuluuMGL/video-factory/issues/81)。本文是开发候选说明，不在客户安装文档白名单内，不改变 a41 固定包、官网安装入口或现有项目。

## 本次范围

- `vfctl up`：在 macOS/Linux 前台运行一个指定项目的 SQLite 内核与 loopback HTTP 服务，默认只读。
- 在同一账本复用 `dispatch_one`、`Worker.step` 和 `ScriptJobs.step`；有明确执行开关、有效项目执行凭据和既有任务批准时，才推进任务。
- 游标在供应商 I/O 前保存，失败或持续排队的首条任务不会阻塞其他任务。一次扫描最多 100 条，扫描到底后从头继续。休眠唤醒后不补发错过的调度次数。
- 锁覆盖整个本机 SQLite runtime 目录，比每项目一把锁更严格；同一目录只能运行一个 `vfctl up`。后续多项目单进程管理另行设计。
- 默认 Agent 只能读取能力、任务元数据和当前进程状态。第二阶段候选增加显式启用的视频计划准备与已批准计划入队，见 [视频计划合同](AGENT_VIDEO_PLANS.md)；没有 Agent 批准、审核、shell、任意 URL 或模型选择接口。

## 启动前提与停止

当前候选要求已有的独立、私有 SQLite runtime 和项目。可用既有管理员工具准备隔离测试账本；不能拿正式账本复制出一个会继续生产的测试环境，也不能将便携 n8n 的数据目录视为兼容 SQLite runtime。

默认目录：macOS `~/Library/Application Support/VideoFactory`，Linux `~/.local/share/video-factory`。目录及文件必须属于当前用户且只有本人可访问，传入路径不得经过符号链接。未初始化时明确报错，不覆盖或恢复旧数据。

```sh
vfctl up --root /absolute/private/runtime --project test_project
```

默认端口 8788，仅监听 `127.0.0.1`，无自动安装、自启动、后台守护或真实飞书接入。Ctrl-C/SIGTERM 停止；正常停止等待当前执行结束后才释放锁。强制终止时由既有账本的提交意图保护未知结果。重新启动同一目录保留游标及任务回执。

明确执行候选需要同时提供 `--execute-approved`、`--execution-key-file`、`--master-key-file` 和 `--media-root`。凭据只从已有私有文件读取，不放进参数值、聊天或输出。执行密钥必须是该项目既有的 `approved_execution` 凭据；不会自动创建或续期执行权限。启动执行模式可能消费原账本中已批准任务的模型费用，因此不能用于未经核对的真实目录。

启动开关不是每任务批准。现有 Worker/ScriptJobs 仍核验版本、输入、配置、凭据版本和批准期限；只有已有任务的有效批准才能提交，结果不确定不重发。

本 PR 未在任何真实项目启动执行模式。实际执行需后续独立验收。

## Agent 认证与接口

新增专用 `agent_read` 凭据，与管理员会话、`queue_read` 和 `approved_execution` 都不互通。管理员通过私有文件签发：

```sh
vfctl agent-key --root /absolute/private/runtime --project test_project \
  --admin-token-file /absolute/private/admin.token \
  --output /absolute/private/agent.token --ttl-hours 24
```

输出仅含项目、权限、到期时间和撤销用的 key ID；原始 token 保存到新建的 0600 文件。已有文件拒绝覆盖。最多有效 168 小时，使用既有管理员 automation revoke 接口撤销；恢复账本或管理员恢复也会撤销/过期相关权限。

Workflow Agent 的服务端从受控配置读取 token，在 `Authorization: Bearer …` 中发送。token 不进入模型上下文或浏览器。请求必须使用精确 loopback Host；带 Origin 的浏览器请求拒绝，不提供 CORS。未来远程部署的认证与传输不在此版本开放。

以下均为 JSON POST，必须带 `project`；未知字段拒绝：

| 路径 | 额外字段 | 返回 |
|---|---|---|
| `/v1/agent/capabilities` | 无 | `vf-agent-read-v1`、当前能力和未开放能力 |
| `/v1/agent/status` | 无 | 进程内调度状态、最近 tick/任务、固定错误码；飞书未接入状态 |
| `/v1/agent/tasks` | 可选 `after` | 最新版本任务分页，最多 100 项、`has_more` 与 `next_after` |
| `/v1/agent/task` | `task`、正整数 `revision` | 当前版本元数据；旧版本拒绝，要求重新读取 |

元数据只含任务 ID、版本、状态、输入摘要与配置摘要，不包含原始输入、脚本、文件路径、凭据引用、产物 URL、供应商响应或审核人信息。`/healthz` 只证明进程可响应。任务读取不能证明飞书已写回或人工已验收。

## 下一阶段接线合同

后续准备、批准和提交接口必须绑定项目/任务/版本、请求 ID、输入摘要、具体模型与凭据版本、调用上限、计划到期时间。Agent 提出方案，不持有管理员权限或自动生成用户批准。

Video Factory 保存执行事实及产物；Workflow Agent 保存讨论和方案引用。通用 `prepare_plan`、`approve_plan`、`submit_plan` 仍未开放。视频专用能力由启动开关与凭据权限共同决定；[脚本计划候选](AGENT_SCRIPT_PLANS.md)使用独立显式开关及固定飞书提交人委托。脚本内容、预览和局部返修尚未提供。不得从一个 HTTP 200 推断完整 Agent 创作闭环已经上线。

## 尚未接入与验证

- 飞书事件、审核处理和结果写回没有接入此本机生命周期；现有容器接收器仍保持原限制。原表费用计划入口依赖单独审查的 #37。
- 本机向导、依赖安装、媒体工具检查、login/logout/开机自启、Windows、多个项目服务、迁移旧安装均不在本 PR。
- 不改变已发布模型支持范围；不合并未验收的其他模型 adapter。
- 新增 Ubuntu/macOS CI 使用合成数据、内存假 provider 和本机回环 fixture。验证真实 CLI 子进程、锁、SQLite、HTTP 权限、重启续接与付费意图保护，不需要服务器/SSH/GitHub Secrets。
- CI 通过后仍需 Workflow Agent 接线、隔离真实飞书与有预算的模型验收，以及非作者安装和真实人员审核。#80/#81 在这些阶段完成前保持打开。
