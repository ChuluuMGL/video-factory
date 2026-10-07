# 飞书任务、员工身份与审核接入（a15）

这是客户服务上的连接器候选：读取指定 Base/表/记录，把不可变输入快照存入产品账本，再由经过飞书身份验证的项目成员审核。推荐用终端 `vfctl stack-feishu-session` 逐条处理；兼容命令 `vfctl feishu` / `vfctl stack-feishu` 仍接受操作者自行准备的私有用户 Token 文件。真实租户的逐任务授权与审核尚须在安装版验收。

## 默认终端操作

在客户服务器的私有 TTY 中运行。命令读取 Setup 已加密保存的飞书应用密钥，显示飞书官方授权地址；本人授权后，终端显示操作计划，输入 `yes` 才提交。飞书用户 Token 只留在短时容器内存，不写文件；不会打开 Video Factory 网页、调用模型或自动扫全表。

```sh
vfctl stack-feishu-session import --stack-root /srv/video-factory \
  --project brand --record rec实际记录ID --expected-revision 0

vfctl stack-feishu-session review --stack-root /srv/video-factory \
  --project brand --task 实际任务ID --revision 1 --stage script \
  --decision reject --feedback '请修正具体产品名称' --event 本次唯一事件ID
```

先核对项目、来源记录、版本和计划，再确认。导入后的 `awaiting_script_review` 不是视频生成完成；审核通过也不授予模型费用许可。`--event` 建议由操作员提供并在异常恢复时复用；省略时命令会打印生成的事件 ID。原始来源发生变化、计划失效或身份不符会拒绝提交。

来自 Setup 的项目优先使用 [接入向导](SETUP_FEISHU_USAGE.md)，逐题配置并核对已安装项目。向导绑定分开的 `script_reviewers` 与 `video_reviewers`；以下手工格式保留原有 `reviewers` 可审核两个阶段的语义。新增分阶段列表必须同时提供，且并集必须等于 reviewers。

## 绑定与身份

管理员为每个项目指定 tenant_key、Base token、表 ID、四个**文本字段 ID**和允许导入/审核的 open_id。不要从 URL 猜这些 ID；实际部署时先解析链接并读取结构。显示姓名、表格中的“审核人”文本及应用 tenant_access_token 都不能替代员工身份。

```json
{
  "tenant_key": "实际租户标识",
  "base_token": "实际BaseToken",
  "table_id": "实际表ID",
  "fields": {
    "task": "实际任务ID字段ID",
    "sku_id": "实际SKU字段ID",
    "script": "实际脚本文本字段ID",
    "source_revision": "实际来源版本文本字段ID"
  },
  "submitters": ["实际导入用户open_id"],
  "reviewers": ["实际审核用户open_id"]
}
```

上面只是形状说明，中文占位值不能执行。映射字段必须实际存在、类型为文本，公式和其他类型会拒绝。按字段 ID 解析当前名称，读取全部字段分页。若项目来自 Setup，SKU 还必须属于该项目已保存的产品列表。

用户授权 token 从私有文件读取，每次操作通过 `/authen/v1/user_info` 验证 tenant_key/open_id。账本只保存平台返回的身份标识，不保存原始用户 token、显示姓名、邮箱或电话；不自动改用应用身份。成员移除后连历史请求重放也会拒绝。恢复/迁移/升级/管理员恢复后，旧飞书绑定会暂停，管理员须重新核对成员并执行 bind（提供旧摘要）才能使用，避免旧备份恢复已撤销的权限。绑定更新必须提交旧摘要；同项目不能静默切换租户/Base/表/字段映射，换来源需要新项目。

## 容器中的操作步骤

在客户部署目录的 `data/worker` 准备 binding.json、管理员 token 和员工自己的用户授权 token。容器内路径为 `/work`；目录/私密文件须归 UID/GID 10001，权限分别为 700/600。用户 token 的获取和刷新尚未集成进 Setup，不应发送到聊天、写入 Git 或作为命令行明文。操作员准备的私密输入文件仍留在客户服务器，完整加密备份也会包含 data/worker；应按有效期自行清理输入文件。连接器不将这些原始 token 另存到数据库。

```sh
vfctl stack-feishu bind --stack-root /srv/video-factory --project brand \
  --configuration /work/binding.json --token-file /work/admin.token \
  --user-token-file /work/submitter.token

vfctl stack-feishu prepare-import --stack-root /srv/video-factory --project brand \
  --user-token-file /work/submitter.token --record 实际记录ID --expected-revision 0
```

核对输出的 plan（含来源、脚本、SKU、身份、配置及预计版本）后，以相同参数运行 `import` 并增加 `--expect-plan 返回的plan_sha256`。提交前重新读取身份/结构/记录，输入或权限变化会使计划失效。只导入这一条记录，不扫全表自动开工。来源记录的 task 字段必须是本项目稳定的任务 ID。

审核先运行 `prepare-review`：

```sh
vfctl stack-feishu prepare-review --stack-root /srv/video-factory --project brand \
  --user-token-file /work/reviewer.token --task 实际任务ID --revision 1 \
  --stage script --decision reject --feedback '请修正具体产品名称'
```

核对计划后，以同样参数运行 `review`，增加 `--event 本次操作唯一ID --expect-plan 返回的plan_sha256`。视频阶段使用 `--stage video`；计划同时绑定输出文件 SHA256 和位置。系统不能判定员工是否真正看过视频，这仍是人工验收职责。

退回必须带具体意见。先退回，再修改飞书脚本和来源版本文本，按最新本地 revision 重新 `prepare-import/import`，产生下一版本；原反馈、视频与供应商 ID 保留。新版本从脚本审核开始，不继承付费许可。旧版本、不同记录挪用相同任务 ID、未改变的来源版本和同 event 不同意见都会拒绝。

## 冲突与费用边界

产品执行依据账本内的不可变快照。审核时会回读来源并对比四个映射字段；不一致则要求核对，不覆盖员工内容。外部读取与本地提交之间不是分布式事务；这里只保证批准的是具体快照及任务版本，不声称远端行被锁定。若员工已提前修改当前待审记录，须由管理员核对并关闭旧任务版本，再导入新版本。

本版本对飞书只执行 GET，不创建、覆盖或回写记录。状态、审核意见和结果保存在产品账本；Base 回写、飞书卡片/页面操作仍是后续功能。飞书接入不会授予付费许可，模型仍走独立 `worker prepare/approve/step`。

容器连接器复用按次启动的中继；a14 新增固定 `open.feishu.cn:443` 出站目的地，继续验证 TLS 证书并拒绝私网解析。普通启动不启用中继；模型 worker 的 HTTP 客户端仍只允许自己的模型/素材域名。连接器支持飞书中国区，未声称支持国际 Lark 域名。

## n8n 读取队列

管理员通过本机管理 API `POST /v1/automation/keys`，以管理员 Bearer token 提交 `{"project":"brand","ttl_hours":24}`。返回一次性显示的 token、key_id 和到期时间；保存为本客户 n8n 的 HTTP Header Auth 凭据，Header 为 `Authorization: Bearer <token>`。凭据仅能读取一个项目的队列，不能审核、创建任务、取出模型 Key 或付费提交；最长有效七天，默认一天。撤销调用 `POST /v1/automation/revoke`，body 为 `{"key_id":"返回的key_id"}`。恢复、迁移、管理员恢复及升级会使旧队列凭据失效，需重新签发。

```sh
vfctl queue-template --project brand --credential-id 实际n8n凭据ID
```

生成默认禁用的 n8n JSON，调用本客户内部 `http://runtime:8787/v1/automation/queue`。这是待办读取工作流，不是生成调度器。接口返回当前版本的非 accepted 任务，每页最多 100 条；has_more 为 true 时用 next_after 继续读取。并发变化下不是全局快照，不能用首批记录做全量统计。

## 证据与未完成项

云端验收使用官方响应结构的回环 HTTP fixture、不同身份/完整字段分页、多次独立 CLI 进程、真实 SQLite/PostgreSQL、合成供应商和实际 FFmpeg 解码，演练脚本退回→新版本→视频退回→再修订→审核，以及真实 n8n 队列读取、跨项目拒绝、撤销和升级失效。只有对应提交的 Actions 通过后才记为技术验收；没有真实飞书账户、真实付费模型或真人操作验收。

API 契约参考飞书官方：[用户信息](https://open.feishu.cn/document/server-docs/authentication-management/login-state-management/get)、[记录读取](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/get)、[字段分页](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-field/list) 及 [官方 Python SDK](https://github.com/larksuite/oapi-sdk-python)。
