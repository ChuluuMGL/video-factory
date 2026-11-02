# 安装续接与非作者验收准备（源码候选）

关联 [#79](https://github.com/ChuluuMGL/video-factory/issues/79)、[#80](https://github.com/ChuluuMGL/video-factory/issues/80)、[#81](https://github.com/ChuluuMGL/video-factory/issues/81)。这份说明属于源码候选，不改变 a41 固定包、官网已发布版本或任何客户实例。没有新的服务器、SSH/GitHub Secret 或真实模型调用前置。

## 欢迎、续接和新增项目

在已验证的完整 Skill 目录中使用同一份脚本；先核对发行来源及外部可信校验值。源码摘要只是检测续接时文件变化，不代替发布者身份或归档校验。

```sh
python scripts/portable_n8n.py --root /absolute/private/vf welcome
python scripts/portable_n8n.py --root /absolute/private/vf handoff-check
python scripts/portable_n8n.py --root /absolute/private/vf welcome --resume
```

Windows 使用对应的 Python 启动器和绝对路径，空格路径加引号。旧版没有这些参数时，不下载单个新脚本覆盖旧包。

- 首次选择已有实例、Node.js、Docker 或 Cloud。Cloud 本轮跳过真实验收；不会替用户创建云账号。
- 原目录 `welcome-state/session.json` 保存路线、端口、地址、项目、脚本目录摘要和阶段，不保存密码、Key、用户回答或原始错误。中断后使用 `--resume`，避免重新选错服务器/项目；仍在隐藏终端输入本轮 n8n API Key，已保存的飞书/模型凭据由 n8n 复用。
- 有现存 checkpoint 时，裸 `welcome` 拒绝覆盖。来源或目录变化时停止；先找回原来已验证的完整 Skill。源代码不匹配不等于自动允许升级。
- `handoff-check` 不要求 Key、不访问网络、不修复服务；输出当前阶段、受影响工作流 ID、历史验证状态及未知创建回执。它明确标注 `live_state: not_checked`，不能证明服务当前正常。
- `pending_resource_receipts` 非空时先在原 n8n 查资源，核对相同对象，不重发创建、不清回执。现有资源下层创建意图仍会阻止未知提交的盲目重试。
- 完成当前配置后，用 `welcome --new-project` 在同一实例新增项目；保存上一轮 session 历史和各项目的交接记录，重新选择项目 ID 与飞书目标。前次配置失败时先续接，不覆盖暂停现场。
- `configured_not_accepted` 只代表配置步骤返回；负责人、任务闭环、审核、真实设备与独立人员验收仍未通过。自启保持原来的显式选择，不自动启用收费工作流。

用户需人工完成的环节：n8n 首次账号/API Key；飞书应用/权限发布和 Base 选择；隐藏凭据输入；负责人费用确认与表内内容审核。安装器的运行时间、账户准备时间、模型等待时间分别记录，不将合成测试耗时宣传为“客户十分钟完成”。

## 私有交接与 Workflow Agent 对接

`bindings/handoff-项目.json` 增加 `installation`：脚本摘要、受管 n8n 的固定版本、真实实例版本未检查标记、绑定目录、已完成阶段、手工环节、待验事项。完整阶段和当前累计耗时以 session 为准。此文件只留客户受控目录，不能上传 GitHub Issue。

Workflow Agent 是同一个 Video Factory 产品的操作能力，不另造执行账本。不同执行来源不能靠任务名称自动合并：

| 来源 | 定位合同 | 当前边界 |
|---|---|---|
| 便携 n8n + 飞书 | `source=portable-n8n-feishu`、项目、Base、table、record ID、业务 task ID、实际版本/摘要 | 本候选只交接定位要求；尚未提供核心 HTTP Agent API 或跨账本自动映射。缺版本时写未知，不填 1 伪造版本 |
| 本机内核候选 [PR #82](https://github.com/ChuluuMGL/video-factory/pull/82) | 明确执行来源、`project`、`task`、整数 `revision`、服务端摘要 | 通过 `/v1/agent/capabilities` 决定可用接口；未合并能力不能当作便携包现有功能 |

Agent 先读私有交接与对应版本，再回读同一个任务。原任务未定位、任务重名、来源不同、版本改变、模型提交未知时停止，不创建替代任务或另建“已完成”记录。本轮不修改 SOFTSUB 连接器，也不让只读分析获得付费权限。

## 可执行的非作者验收流程

具体执行请用 [固定候选最短清单与空证据表](PR86_NONAUTHOR_CHECKLIST.md)，审查发现与边界见 [PR86 审查记录](PR86_REVIEW.md)。源码候选尚未发布；旧 CI 不能替代修复后的首次 welcome 路线验收。

实施人员先提供固定版本完整包、外部校验值、空的私有安装目录与这份步骤；操作者不能借用作者已有绑定、凭据、浏览器会话或完成记录。

1. 记录设备系统/架构、网络、包版本/摘要、已有 n8n 与否；记录计时起点和账户准备耗时。
2. 运行 `welcome`。每次手工命令、网页步骤、隐藏输入次数计数，不记录秘密。连接已有 n8n 时核对前后已有工作流与业务数据保持不变。
3. 在保存项目后退出一次；执行 `handoff-check`，记录停止阶段。用同目录 `welcome --resume`，验证地址/项目未重问、飞书凭据复用、没有重复业务表或生产工作流。零模型读取使用独立临时诊断流程，完成后须回读关闭状态；历史诊断回执保留。
4. 先用隔离的假服务/零模型任务读写，记录完整任务定位和预期状态；重开 Agent 会话，仅用交接记录找回同一任务，回读原状态/结果，确认无新模型提交。未接入的 Agent 适配器标为待补。
5. 核对脚本和视频流程仍关闭。真实飞书账号/费用负责人/生成/审核需要独立安排，不能用假服务替代。
6. 导出脱敏报告：实际耗时、命令数、网页步骤、各类输入次数、中断点、恢复结果、任务定位/回执证据、仍需协助处。真实退出登录、自启以及非作者操作完成之前保持未验。

## 本轮自动化证据的含义

`tests/portable/test_session.py` 覆盖版本/目录漂移、暂停、复用已保存身份、敏感信息不持久化、未知资源提示、新增项目历史、全新进程读取交接、计时排除离线间隔。

四平台已有实际 n8n CI 增加全新绑定目录的 welcome 中断/续接：使用真实 n8n API 保存加密测试凭据，模拟断开后只重输 n8n Key，复用原飞书凭据，对假飞书做读取检查，并由另一 CLI 进程读取交接。`install-welcome-*` artifact 只包含脱敏时间/步骤统计，不上传私有交接、日志或凭据。结果明确 `human_operator: not_tested`、`real_device_login_restart: not_tested`、真实付费调用 0。

这属于独立 CI 机器上的自动化准备，不是非作者安装或客户业务验收。只有本次 head 的云端记录可以支持本次候选，旧 a41 的通过记录不复用为本次证据。

## 同一任务的零模型回读

源码候选提供显式诊断命令（不是启动生成）：

```sh
python scripts/portable_n8n.py --root /absolute/private/vf task-check \
  --project test_project --record recExample --task TASK_1 --revision 1
```

使用原项目中已保存的飞书凭据，本轮只隐藏输入 n8n Key。该命令会在 n8n 创建独立、受鉴权的临时诊断工作流，执行后回读关闭状态并保留回执；因此需要工作流创建/启停权限，不是纯 GET 管理操作。对飞书只有 token 认证与指定记录读取，没有任务写入或模型请求。它不适合未经许可直接运行在客户实例。

必须同时匹配项目绑定的 Base/table、record ID 和「任务编号」；不会搜索同名任务作为替代。显式 `--revision` 还需匹配「审核目标版本」；原表没有此版本字段时省略该参数，结果记 `revision: null`，Agent 不得据此准备付费计划。输出是 `{identity: {source,project,base,table,record_id,task,revision},status,result_presence}`，只报告脚本/视频存在性，不输出正文、视频地址、密钥或原始日志。状态回读不等于已验证真人审核。

四平台 CI 在假脚本写回后回读 `CI_INSTALL_TASK_1` 的同一 record/版本/状态与脚本存在性，核对没有额外写任务或模型调用，诊断工作流已关闭。这是便携来源的定位适配，尚未接入 #82 的 HTTP Agent API，不可将 `core_task_mapping: not_configured` 当作已完成跨来源映射。
