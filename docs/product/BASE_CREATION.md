# Setup 自动创建项目飞书 Base（a25）

新项目默认选择 `create`。客户无需提前建测试 Base，也无需手填新表的 ID。`bind` 保留给已有工作区；两种模式都使用客户自己的服务器、应用和飞书身份。

## 实际流程

1. `setup-run` 收集项目、SKU、分阶段审核人及模型意图，核对后安装或复用客户 stack。
2. 新建路径继续询问 `workspace_kind`（`test` / `production`）、`folder_token`（留空使用授权用户的云空间根目录）和提交人。当前没有文件夹浏览器或员工搜索器；自选文件夹仍需 Token，角色仍需真实 open_id。
3. 客户在私有终端保存应用密钥到服务器加密 vault，打开短时管理员窗口，以本人飞书身份授权。新建路径增加 `base:app:create`、`base:table:create`、`base:record:create`；原有绑定和员工入口维持只读表格及身份权限。应用也必须在飞书开放平台获得对应权限，授权可能被租户策略阻止。
4. 页面先展示 Base 名称、位置、商品表/任务表字段、全部商品及测试任务、提交人和审核人。点击确认前没有飞书写入。
5. 确认后创建 Base、商品表及商品记录、任务表；test 另写两条明确标记为安装验收的任务，production 不写测试任务。逐项读取字段和记录核对，再保存实际 Base/表/字段 ID 与分阶段权限。飞书创建 Base 时产生的默认表保留，不执行删除。
6. 窗口成功后关闭，终端回读绑定；员工使用自己的飞书身份进入审核窗口。Base 所有者需先在飞书授予员工访问权，配置产品角色并不自动分享文档。任务导入、脚本退回与修订、视频调用和真人验收分别执行。

命令及私有目录要求见 [统一入口](SETUP_RUN_USAGE.md)；分阶段 JSON 接口见 [飞书接入](SETUP_FEISHU_USAGE.md)。产品仍返回 `business_ready=false`，不会因创建测试表就自动提交付费模型。

## 续接与故障

客户数据库记录每次写入的意图、回执和实际 ID，不保存 OAuth token。读取失败时，原 session 可重新授权、审阅并续接；已取得回执的步骤只回读，不再创建。完成后重复确认也不会新增 Base、表或记录。SKU 和测试任务的记录回读不依赖接口返回顺序。

创建 Base/表的官方接口没有公开幂等参数。请求已经发出但回执丢失时，状态保留 `in_flight`，报 `FEISHU_PROVISION_OUTCOME_UNKNOWN_READ_STATUS` 并停止；不能通过删除 session、日志或换项目来“重试”。记录批量创建虽带 UUID 幂等键，此版本也保守停止未知提交。管理员使用同一版本的 `setup-feishu status` 读取创建日志，再核对飞书远端；**当前尚无自动对账/认领未知资源的命令，需要支持人员处理，不宣称所有故障可自助恢复。**

备份恢复/版本切换使新建项目进入复核。完整创建日志可重新授权回读后复用；缺失或未完成日志的恢复检查点会阻止创建，避免旧备份导致重复写入。不可直接编辑数据库解除保护。

## 官方接口依据与验证边界

- [创建 Base](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app/create)：POST，创建回执核对指定文件夹。
- [获取 Base](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app/get)：GET 不提供 folder_token；回读核对 Base token 和名称。
- [创建数据表](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table/create)：一次提交文本字段。
- [批量创建记录](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/batch_create)：UUID client_token，逐条读回实际内容。

自动化检查在 GitHub 云端使用合成飞书 HTTP/OAuth 接口，覆盖 SQLite/PostgreSQL、安装后的 CLI、Chrome 桌面/手机宽度、确认前零写入、创建后员工导入、并发/读取失败/未知提交/恢复阻断。通过结果以对应提交 Actions 为准；真实租户建表、真实员工和非作者安装仍须单独验收。
