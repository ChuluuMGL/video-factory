# Setup 后的飞书接入向导（a15）

交互安装与接入可使用 [统一终端向导 setup-run](SETUP_RUN_USAGE.md)。以下独立命令继续保留用于分阶段操作。

推荐统一入口 `setup-run`；分阶段仍可使用 `setup` → `setup-deploy plan/apply` → `setup-feishu configure/connect`。接入向导沿用已安装的租户、项目、SKU 和分阶段审核人。

从 a25 起，`base_mode=create` 询问用途、位置及提交人，授权确认后自动创建、核验并保存实际 ID；`bind` 仍询问已有表及四个文本字段 ID。详见 [新建 Base](BASE_CREATION.md)。Setup 离线草稿不表示 Base 已创建。浏览器设备授权的 token 仅留在内存；下面的用户 token 文件方式是已有 token 操作者的备用路径，不是首次安装必需步骤。

## 浏览器授权连接（a17 候选）

完成 `configure` 后，管理员可直接开启授权向导，无需手动获取用户 access token：

```sh
vfctl setup-feishu connect --root /srv/private/runtime \
  --session /srv/private/customer.feishu.json --token-file /srv/private/admin.token \
  --project YOUR_PROJECT --app-id cli_YOUR_APP \
  --app-secret-file /srv/private/feishu-app-secret --port 8791 --seconds 360
```

容器安装将 `--root` 换成 `--stack-root /srv/video-factory`，三个输入文件使用 `/work/...` 路径，对应 stack 的 `data/worker/`。文件需归容器用户 10001 所有且权限 0600；挂载仍为只读。管理员 token 与应用 secret 必须由客户自己保存在私有文件，不能放入 Git、命令参数或 Setup JSON。此版尚未统一管理员登录、应用 secret 录入和一键安装。

终端返回带一次性片段的私有链接；仅启动者可见，不分享或截图该链接。远程服务器通过同端口 SSH 隧道打开，例如 `ssh -N -L 8791:127.0.0.1:8791 user@customer-host`；浏览器访问终端给出的 127.0.0.1 链接。链接只可解锁一个浏览器会话，页面立即移除地址片段；关页、退出或过期后可重新运行命令。容器就绪日志也包含短时链接，仅服务器/Docker 管理员可读，精确临时容器退出后删除。窗口最长 360 秒，a18 在确认保存后自动关闭并返回终端；不是常驻公网管理页面。

页面顺序：欢迎 → 客户飞书应用设备授权 → 读取指定 Base 字段 → 展示租户、Base、表、字段、提交人、分阶段审核人和计划摘要 → 确认保存。取消不保存；最终提交再次验证管理员、当前飞书用户和字段。租户错误、原 Setup 与安装配置不符、草稿中途改动、管理员过期/撤销或计划变化均阻断。飞书用户 token 仅在窗口内存；保存或退出清除，不写入数据库、浏览器存储或配置文件。官方应用必须具备设备授权能力和 `bitable:app:readonly`、`contact:user.base:readonly` 权限；实际客户应用兼容性仍待真实租户验证。

保存后页面引导运行 [review-ui / stack-review](REVIEW_UI_USAGE.md) 开启员工审核。它不会自动启动员工窗口或付费任务，也不共用管理员页面。员工以各自飞书身份登录。旧 `plan/apply` 路径保留，供已有私有用户 token 的 Agent/运维使用。

以下云端检查使用模拟授权服务：首次绑定、取消/确认、计划变化与权限拒绝、SQLite/PostgreSQL、实际 Chrome 桌面/窄屏、安装包及真实容器窗口清理。是否通过以 a17 当前提交的 Actions 为准；不能等同真实租户授权或真人验收。

## 终端与 Agent

原生部署示例（路径为示意，应换成客户自己的绝对路径）：

```sh
vfctl setup-feishu configure \
  --setup-session /srv/private/customer.setup.json \
  --session /srv/private/customer.feishu.json --interactive
```

每个答案保存后可以退出续填。终端导入成员可输入逗号分隔的 open_id。Agent 使用同一命令的 `--json`，遵守返回的 `next_question.input_schema`，以 `--answers /绝对路径/answers.json --expect-revision 当前版本` 提交答案；也支持 `--answers -` 从 stdin 读取。成员答案必须是 JSON 数组，不能填姓名或文件路径。重复字段映射、未知字段、无效 ID 和过期 revision 会被拒绝。

```json
{"submitters":["ou_真实员工open_id"]}
```

以上是形状说明，不是真实可用 ID。连接草稿只存原 Setup 和非秘密答案，没有用户 token 或“已验证”的本地标记。原 Setup 改变后须重新创建连接草稿；服务端还会比对已安装项目，不接受伪造本地配置。

## 验证、保存和回读

```sh
vfctl setup-feishu plan --root /srv/private/runtime \
  --session /srv/private/customer.feishu.json \
  --token-file /srv/private/admin.token \
  --user-token-file /srv/private/operator.token
```

`plan` 校验产品管理员、已导入的完整 Setup、当前飞书用户租户以及字段实际结构，返回绑定目标、操作用户、字段名称、分阶段成员和 `plan_sha256`。此时没有保存飞书绑定。审核人列表是管理员指定的权限，只有审核人自己操作时才能验证其真实身份；此步骤不表示所有员工都已经登录。

核对上述对象后，使用相同参数运行 `apply --expect-plan 返回的摘要`。它再次读取身份和字段并核对计划；计划改变则拒绝，不会沿用过期授权。已绑定项目的租户/Base/表/字段不能静默切换，必须创建新项目。

`status` 使用相同 root、session、管理员 token；不需要飞书 token，也不连接飞书。它只报告账本中的绑定是否与草稿一致、是否需要恢复后重新核对，不能证明远端 token 仍有效。提交中断时先查 status，再重新 plan。

## 容器部署

在部署的 `data/worker` 私有目录内，以该目录的运行账户 UID/GID 10001 执行 configure 并保存 connection session；原 Setup 文件也需以 600 权限提供给该账户。不要把管理员 root 所有的 session 直接当成容器账户文件。向导的目录/文件检查要求当前账户所有、不可被其他账户写入；多进程保存有文件锁与 revision 检查。

在 Docker 主机上执行下列命令时，session 与 token 参数使用容器内 `/work` 路径，映射到该部署的 `data/worker`：

```sh
vfctl setup-feishu plan --stack-root /srv/video-factory \
  --session /work/customer.feishu.json \
  --token-file /work/admin.token --user-token-file /work/operator.token

vfctl setup-feishu status --stack-root /srv/video-factory \
  --session /work/customer.feishu.json --token-file /work/admin.token
```

apply 同样需要 `--expect-plan`。plan/apply 使用现有的一次性容器和短时飞书 HTTPS 中继；status 不启动出站中继。路径越界、旧部署格式、非健康服务会拒绝。原始凭据仅从客户私有文件读取；这些文件会被完整加密备份包含，应按有效期清理。

plan/apply/status 只读一个完整的草稿快照，不在只读挂载内创建锁或修改 session。configure 使用锁和原子替换保存。操作开始后修改的草稿供下一次命令读取；计划摘要绑定本次读取的具体快照，服务端另在事务内核对实际配置与权限。

## 员工权限与后续任务

Setup 指定的脚本审核人只可审核脚本，视频审核人只可审核视频；同一人担任两者时可处理两阶段。撤销权限后，历史事件重放也拒绝。旧手工 binding 若仅包含 `reviewers`，仍按其原来的“两个阶段均可审核”含义运行；没有自动扩大或改写旧权限。a15 的分阶段 binding 在不支持该结构的旧客户端中会拒绝，应使用同版本 CLI，不能将旧版作为降级后的可用业务客户端。

连接成功后，使用 [飞书任务手册](FEISHU_BRIDGE.md) 的 `prepare-import/import`、`prepare-review/review` 演练任务。连接本身不创建任务、不写飞书、不提交模型，也不代表员工已看过视频。恢复/升级会暂停旧绑定；管理员重新检查名单、plan/apply 后才恢复。

当前云端测试使用模拟飞书 HTTP、真实 SQLite/PostgreSQL 和独立 CLI 进程；真实飞书授权、员工界面和客户验收仍待完成。
