# 飞书结果回写候选

实现分支面向 **a32 候选**。公开安装页仍交付 a31；a31 不含此功能。实现、云端合成检查、真实 Base 验收和正式发行分别记录，不以代码存在代表可交付。

## 行为

管理员显式开启后，在项目已经绑定的 Base 中新建专用结果表。n8n 已批准任务调度每次另推进一个同步步骤；没有新模型任务时也可同步。后台使用该项目已加密保存的自建应用凭据，不保存员工用户令牌、不扩大员工工作区访问权限。

按调度观察到的任务状态追加快照：项目、任务、版本、SKU、状态、脚本、视频摘要及附件。原始任务表、人工备注及已有结果行均不修改。调度间短暂状态不保证逐个记录；客户服务器账本仍是完整审核依据。视频按 4 MiB 分片，最多 128 MiB；保留实际视频文件，不写内部路径、供应商链接或一次性下载地址。

暂停回写不暂停模型调度；模型授权与同步启停分别管理。飞书不可用不能导致重新提交付费模型请求。

## Agent 操作

须先完成该项目常驻工作区部署。服务器上的固定候选 CLI 提供 `base-results enable|pause|status|sync|repair|recover`，参数为同一客户的 `--stack-root`、本项目 `--session`，可加 `--browser-input`。密码仅在私有终端或用户专用输入窗口输入。日常用户无需手写这些命令。

`enable` 先读目标 Base、应用可见性和计划，再确认创建结果表。重复启用复用已记录的目标；不会创建第二张表。`sync` 是管理员单步诊断，不代替 n8n 调度。`status` 显示启停、待处理数及未知提交步骤；不能把这些计数当作真实业务签收。

应用需要目标 Base 的文档管理权限（包括高级权限下的完整读可见性）；代码只追加自己创建的结果表。最小 API 权限按当前飞书后台审核：`base:table:create`、`base:table:read`、`base:field:read`、`base:record:retrieve`、`base:record:create`、`docs:document.media:upload`。不申请记录编辑、删除或全部云盘管理权限；权限名称以实际 API 文档为准。

## 失败与恢复

每次写前将意图、唯一标识和步骤保存到数据库，网络调用不占数据库事务锁。

- 表创建响应丢失：按带随机后缀的准确表名回查；找不到时停住，不再次建表。
- 记录响应丢失：按同步标识回查，内容与附件令牌完全一致才签收；查不到不能盲目再提交。
- 人工修改、重复同步标识或字段变化：报告冲突，不覆盖远端。
- 上传预创建、分片或完成响应丢失：保留上传回执并停止，不重复创建附件。管理员可用 `repair --event 同步标识 --step 步骤` 查看计划并授权一次重试：记录沿用原幂等号，已知上传沿用原事务，最多共三次尝试。预上传无回执时可能留下未使用的旧事务；必须在确认提示中说明。未授权不重试，授权 15 分钟后失效。上传事务超过 23 小时停止，不能换一个新事务假装恢复成功。
- 恢复旧备份：强制停用回写，保留目标和历史回执，必须核对远端后恢复。先重新验证飞书绑定，再用 `recover` 回读检查点中已完成记录及附件；核对通过仍保持暂停，由管理员另行开启。未完成上传、记录或远端不一致会拒绝恢复，须保留备份及回执做单独对账，不能清日志或改数据库绕过。

## 验证

合成测试覆盖创建防重、丢响应、人工编辑、文件哈希、并发、凭据变更、项目范围与恢复停用。真实 Base、实际权限、附件上传下载哈希及独立员工验收仍需单独通过；不使用生产表验证。

API 依据：[批量创建记录](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/batch_create.md)、[查询记录](https://open.feishu.cn/document/docs/bitable-v1/app-table-record/search.md)、[素材预上传](https://open.feishu.cn/document/server-docs/docs/drive-v1/media/multipart-upload-media/upload_prepare.md)、[应用访问凭据](https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal.md)。
