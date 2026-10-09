# 飞书原任务表审核（候选功能）

员工在项目的**原任务表**看脚本和视频，修改“状态”，退回时填写“审核意见”。终端只用于 Setup、管理员启用和排障。当前代码已提供此路径；发布与真实 Base 事件验收前，不把它标为已交付的员工流程。

## 启用条件

Setup 先完成项目、飞书本人授权和 Base 绑定。当前候选实现为**每个启用原表审核的项目使用独立飞书自建应用**；不要使用已经服务其他机器人或长连接客户端的共用应用。飞书长连接对同一应用采用集群分发，多个客户端中只有随机一个收到事件；本项目的接收器只处理本项目事件，复用应用会导致其他业务或本项目漏收。多个项目仍可复用同一客户服务器、数据库和 n8n，飞书应用凭据分别配置。Setup 会阻止在本机两个项目复用同一应用；外部已有客户端需由实施人员在启用前核对。

该应用需对目标 Base 有文档管理和编辑权限，并在开发者后台开通应用身份的 `bitable:app`、`docs:event:subscribe`，以及用户身份的 `bitable:app:readonly`、`contact:user.base:readonly`；添加 `drive.file.bitable_record_changed_v1` 事件，选择**长连接**并发布应用。应用还必须被授予目标 Base 的管理权限。Agent 核对这些条件，不让员工去终端审核脚本。管理员在启用前先检查文档事件订阅状态；权限不足时应在修改表结构前停止。

在私有终端，先为项目启动私网 runner，再由管理员启用审核（实际路径以固定发行包为准）：

```sh
vfctl runner plan --stack-root /opt/video-factory --project PROJECT
vfctl runner apply --stack-root /opt/video-factory --project PROJECT --expect-plan SHA256
vfctl runner status --stack-root /opt/video-factory --project PROJECT
vfctl feishu-review enable --stack-root /opt/video-factory --session /root/vf-private/customer.setup.json
vfctl feishu-review status --stack-root /opt/video-factory --session /root/vf-private/customer.setup.json
```

先核对 runner 计划和摘要，只有状态为 `running` 才启用审核；否则 CLI 会在询问密码前给出明确错误。恢复或升级到新 stack 根目录后，须在新路径重新应用 runner 计划。`running` 只证明组件存活，仍需在 Base 修改一条测试记录，验证事件确实送达。

若恢复后返回 `FEISHU_BINDING_RECONFIRM_AFTER_RECOVERY`，用原 Setup session 和现有 stack 重新运行 `setup-run`，由原管理员完成飞书本人授权，核对计划指向原 Base 后确认。向导会复用已完成的建表回执并回读绑定；不要清除恢复标记、手工修改数据库或新建另一张表。若回执或目标不一致，停止并先核对远端资源。

命令在原任务表补充“状态”“审核目标版本”“脚本摘要”“审核意见”“视频摘要”“视频”字段。新建 Base 在建表时已创建这些字段；旧 Base 缺少时由启用步骤补齐。已有“状态”文本或单选字段可复用，类型冲突停止。启用会用该项目应用身份订阅 Base 变更并回读订阅状态；无权限、写入结果不明或字段冲突时停止，不清表重建。字段已建好但订阅失败时，保留字段和本地意图，先查明订阅状态再续接，不能重建表或凭空标记启用。私网 runner 的 `events` 服务接收长连接事件，没有公网回调或员工网页入口。

## 员工怎样审核

任务必须先在该项目 Base 中有唯一“任务编号”和匹配的 SKU；提交生成前绑定该记录并冻结来源版本。生成的脚本、脚本摘要、审核目标版本及“脚本待审核”写到这一行。员工在同一行阅读脚本，然后把状态改为“脚本通过”或“脚本退回”；退回必须写审核意见。通过后，后续视频请求仍需单独的模型与费用授权。视频完整解码并校验 SHA-256 后，附件与“视频待审核”写回同一行；员工再改为“视频通过”或“视频退回”。

服务只接受飞书认证事件中的操作人 `open_id`。它核对项目、Base、表、记录、SKU、来源版本、审核目标版本、审核角色、远端脚本或视频附件；历史记录里的显示名与可编辑状态格本身均不算授权。事件先持久化，再由私网调度处理。无权、无关或已过期的审核事件记录为拒绝；网络或写入结果不明时停止并要求回读，不自动重试模型。管理员从事件/写回状态与 Base 记录定位故障，不能直接改本地账本伪造通过。

可选 `base-results` 是另一张只追加的诊断快照表，不是审核入口。`stack-feishu-session review` 是旧终端验证路径，不计入员工表内审核验收。

## 验收边界

代码回归、Base 字段创建、飞书长连接实际送达、本人表内退回/通过、视频附件、第二账号的允许与拒绝、备份恢复分别验收。只有一个真实账号时，第二账号隔离必须记“未执行”。`feishu-review status` 的 enabled 仅证明本地配置和订阅回读，不证明事件持续送达或员工业务已跑通。
