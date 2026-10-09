# 排障与问题反馈

本页供管理员、实施人员和 Agent 排查自托管安装。先确认正在运行的 `vfctl` 版本、项目、步骤和**准确错误码**，再读对应章节；不要仅凭页面提示或 CI 通过判断任务已完成。客户服务器、飞书 Base、n8n 和模型服务分别回读。

| 现象或错误 | 先检查 | 安全续接 |
|---|---|---|
| 安装中断、继续安装 | 固定包版本、原 Setup session、`vfctl stack status` | 使用原 session 续接；不要删除它或重建客户数据。见[安装操作](SETUP_RUN_USAGE.md)。 |
| `DOCKER_ADDRESS_POOL_EXHAUSTED` | Docker 网络、网络所连接的容器、目标 stack | 只处理确认为空且可重建的旧测试网络，再用原 session 续接。 |
| 飞书授权过期或 `FEISHU_OAUTH_DENIED_OR_EXPIRED` | 授权页时限、本人账号、终端是否仍在等待 | 重新发起**一次**本人授权；先回读本地状态，不因授权超时重建 Base。 |
| 新建 Base 结果不明 | `setup-feishu status`、远端 Base 和表是否已存在 | 停止自动重试；核对创建日志与远端 ID。见[Base 创建与恢复](BASE_CREATION.md)。 |
| 表内审核未推进 | 原行的状态、审核目标版本、SKU、脚本或视频、事件接收器及持久队列 | 先确认员工在原表操作及事件送达。退回必须先保存具体意见，再选择退回；事后修改意见不会改写回执。见[原表审核](FEISHU_NATIVE_REVIEW.md)。 |
| 模型提交超时、状态不明 | 本地任务状态、供应商回执 ID、已保存的产物 | 停止重发；按原任务核对供应商结果后再决定修复。不能为了排障再次付费提交。 |
| 视频或附件未回写 | 媒体文件解码和 SHA-256、附件上传回执、远端记录和写入意图 | 先回读并对账，不删除旧媒体或伪造“审核通过”。见[结果同步](BASE_RESULTS.md)。 |
| 升级或恢复后服务不可用 | 旧、新 stack 路径、容器、数据库、备份和恢复回执 | 确认当前运行版本及回退路径；备份存在不等于恢复成功。见[Stack 操作](STACK_USAGE.md)。 |

这些是诊断入口，不保证每种故障都能由 Agent 自动修复。涉及权限扩张、密钥轮换、数据恢复、模型重试或员工审核时，按各自的操作边界处理。Agent 可先做只读诊断，给出**证据、修复计划和验收结果**。

遇到本页没有覆盖的问题，先搜[现有 Issues](https://github.com/ChuluuMGL/video-factory/issues)，再用[问题模板](https://github.com/ChuluuMGL/video-factory/issues/new/choose)提交**脱敏的最小复现**：版本、阶段、错误码、期望与实际结果、已做的检查。不要提交密码、API Key、App Secret、服务器 IP、真实飞书 ID、客户内容、原始日志或未脱敏截图。安全漏洞使用[私密报告](https://github.com/ChuluuMGL/video-factory/security/advisories/new)。客户或实施人员审核后自行提交；Agent 不自动公开发送诊断资料。

Issue 是待核查的问题记录，不能直接当作已验证的修复方案。维护者修复并验证后，在对应 PR 中更新本页及相关操作文档，注明适用版本；每次固定发行前复查索引与失效链接。关闭 Issue 不等于客户实例已升级，也不等于客户任务已恢复。
