# 本机业务配置候选

本说明对应新增 `vfctl local-setup`，尚未发布到 a41。使用此分支构建并校验的核心 wheel；旧发行包和假模型体验包不会自动获得此命令。支持 macOS/Linux，Windows 原生另行验收。

本机业务数据必须放在**新目录**，不能把 `vfctl local` 的假模型账本转换成真实项目。现有 n8n/完整服务器部署不变。本步骤不安装容器、不启动执行、不提交任何模型请求。

## 操作员的准备

准备专用飞书应用、已有测试 Base 与任务表、本人可访问该表的账号、商品 SKU 和真实资料。应用权限及记录变更订阅按 [原表审核说明](FEISHU_NATIVE_REVIEW.md) 配置。暂不支持这个入口自动新建 Base。

Agent 可以帮助查找组织、Base、表和字段 ID，整理非秘密 JSON。密码、App Secret、模型 Key 在终端隐藏输入；也可使用本人所有、权限 0600 的独立文件。不要放到聊天、JSON 计划、命令参数值、Git 或日志。

当前内核脚本模型是 `deepseek-flash`，视频是 MiniMax-H3（cn/global）。此候选不把便携 n8n 的全部模型支持当成本机内核支持。可输入 `skip` 暂不配置视频。

## 首次配置

候选源码安装器现支持 `--mode business`：

```sh
python3 scripts/install_local_kernel.py --mode business --root "$HOME/VideoFactory" --package /绝对路径/已校验的候选.whl
```

这会安装独立 Python 环境并生成 `~/VideoFactory/vf-setup`，不创建假模型账本。执行 `~/VideoFactory/vf-setup --plan /绝对路径/project.json --interactive --bind` 进入下面的问答，业务数据固定在 `~/VideoFactory/business`。尚未发布一键下载入口，必须使用同版本安装器和 wheel。

macOS/Linux 的独立环境通过符号链接使用安装时的基础 Python；请保留该 Python，不要在系统仍使用它时卸载。旧安装器在 uv 管理的 macOS Python 上可能因复制可执行文件后找不到 `libpython` 而中止；保留失败目录，取得包含修复的同版本安装包后选择全新目录重试。不要手改原 ZIP 或把部分安装标记为成功。

也可直接用已安装候选环境的 `vfctl`。在自己的目录准备一个仅本人可访问的计划目录：

```sh
mkdir -m 700 "$HOME/vf-local-config"
vfctl local-setup --root "$HOME/vf-business" \
  --plan "$HOME/vf-local-config/project.json" --interactive --bind
```

终端先询问项目、SKU、应用和测试表信息，再隐藏输入管理员密码与 Key。参考图放在权限 0700 的独立素材目录；向导检查绝对路径、符号链接、大小与 SHA256，不要求创建 `/work`，也不用 root。

最后只打开**飞书官方身份授权页面**。产品不启动安装网页，也不让 Agent 收集用户 token。完成授权后，程序只读核对组织和四个映射文本字段，保存绑定。配置保存和飞书核对分别报告；模型调用数为零。

成功回执 `configuration_saved: true` 仅表示配置已保存。`feishu_binding_verified: true` 表示这次读到了真实表结构；两者都不表示生成、订阅或使用验收成功。

## 原表审核：先看计划，再明确启用

```sh
vfctl local-setup --root "$HOME/vf-business" \
  --plan "$HOME/vf-local-config/project.json" --native-plan
```

核对返回计划中的表、字段及订阅范围。确认专用应用没有其他事件接收器后，用刚返回的计划哈希启用：

```sh
vfctl local-setup --root "$HOME/vf-business" \
  --plan "$HOME/vf-local-config/project.json" \
  --enable-native <刚核对的计划哈希> --dedicated-app
```

这一步会按计划修改测试表审核字段/订阅，不调用模型。它不发放执行密钥，也不代表已开始后台调度。执行仍按 [本机内核](https://github.com/ChuluuMGL/video-factory/blob/codex/local-business-setup/docs/product/LOCAL_AGENT_KERNEL.md) 显式配置项目执行权限与每条费用审批；脚本通过不授权视频费用。同版本限时启动和执行权限见下文；开机自启及非作者独立安装仍待验收。

## 续接与新增项目

- **中断、网络失败或授权过期：**保留同一 root 和计划，再执行相同命令；已经保存的 Key 会复用，不要求重输。飞书授权过期只重新授权，未完成外部写入不自动重试。
- **新增项目：**使用同一个 root、不同的 project 标识和新的计划文件，沿用管理员密码；原项目不改。新项目的应用/模型凭据单独加密保存。
- **补配视频：**首次选择 `skip` 的项目可使用下述显式续接，不重建项目或重审已通过的脚本。
- **其他配置变更：**同项目的 SKU、既有模型、应用或目标表不一致仍会停止，不静默覆盖。
- **文件保存：**`runtime.sqlite3` 保存配置和加密凭据，`master.key` 为本人所有的 0600 文件。保留两者的私有备份；加密不能抵御拥有同一 OS 账户读取权限的人。

### 脚本已跑通，之后补视频

此能力需要含 `--previous-plan` 的候选核心，旧安装包不会自动获得。先正常停止该业务目录的运行进程。保留原 `project.json`，复制为 `project-with-video.json`，仅把 `video: null` 改为 MiniMax-H3 的 `model/region/assets` 配置。商品、费用归属、飞书应用、绑定及人员均不得改变；参考图必须匹配该 SKU 和已审核脚本。

```sh
~/VideoFactory/vf-setup \
  --previous-plan /绝对路径/project.json \
  --plan /绝对路径/project-with-video.json
```

终端验证管理员并隐藏输入视频 Key；应用与脚本 Key 自动复用，不重新进行飞书授权。支持本人所有的 0600 `--password-file`、`--video-key-file`。程序在同一事务内保存视频凭据、配置和变更回执，保留原任务版本、审核事件、绑定及其他凭据。失败回滚；重复成功命令复用已有结果，不重复输入视频 Key。

成功后运行和续接改用新计划，原计划保留供审计。`video_added: true` 仅表示配置已补齐，不启动调度、不批准费用、不提交视频。使用新计划重新 `inspect`，明确核对单条视频费用计划后才执行。仍需要首次绑定的项目不会因此被算作绑定通过。

## 常见错误

| 提示 | 操作 |
| --- | --- |
| `LOCAL_SETUP_CHANGED_USE_NEW_PROJECT` | 原计划或已保存配置发生变化；回到原计划核对，不删除数据库重建 |
| `LOCAL_SETUP_RUNTIME_NOT_OWNED` / `LOCAL_FAKE_PROFILE_NO_EXTERNAL_IO` | 使用新业务目录；不能采用历史或假模型账本 |
| `SECRET_DECRYPT_FAILED` / `LOCAL_SETUP_MASTER_KEY_MISSING_RESTORE_BACKUP` | 停止，恢复匹配的私有主密钥；不可生成新密钥替换 |
| `DIRECTORY_OWNER_ONLY_REQUIRED` | 核对路径后为本人专用目录设置 0700，勿递归修改共享资料目录 |
| `FEISHU_OAUTH_DENIED_OR_EXPIRED` | 用原计划重新运行 `--bind`，本人重新授权；已保存配置仍在 |
| `FEISHU_MAPPED_TEXT_FIELD_MISSING_OR_CHANGED` | 核对四个字段 ID 与文本类型；不要把状态单选字段映射成脚本字段 |
| `LOCAL_RUNTIME_ALREADY_RUNNING` | 先正常停止这个业务目录的服务，再配置；不要杀死不明进程 |

## 验收记录

云端测试覆盖原子回滚、凭据不覆盖、项目隔离、配置漂移、错误主密钥、绑定失败续接、CLI 退出撤销会话和本机素材校验。测试使用模拟身份/接口，不替代真实飞书和真实模型验收。

非作者按同一候选安装后，分别记录：安装成功、配置成功、飞书授权/绑定、费用审批、脚本审核、视频回写。手动停止后重启核对历史任务和结果，无重复提交；真实退出登录/重启及自动启动仍需单独记录，不能用 CI 代替。

## 同版本启动与 Codex 入口候选

新业务安装同时交付 `vf-setup`、`vf-run`、`vf-agent`。原安装不会因只更新文档而增加命令；先核对同版本安装器与 wheel。

配置与绑定完成后，负责人在自己的终端执行：

```sh
~/VideoFactory/vf-run inspect --plan /绝对路径/project.json
~/VideoFactory/vf-run start --plan /绝对路径/project.json --expect-plan 刚返回的plan_sha256
```

密码由终端隐藏输入。默认只读，不启动飞书接收器、不执行任务。前台返回的 `connection` 路径可供同一受控环境中的 Codex 使用：

```sh
~/VideoFactory/vf-agent --connection /实际返回路径/connection.json capabilities
~/VideoFactory/vf-agent --connection /实际返回路径/connection.json tasks
```

CLI 读取专用项目凭据，不将其打印到输出。请求只能走固定回环地址的 Agent 路由，没有管理员审批、任意 URL 或 Shell 入口。连接最长两小时；Ctrl-C 正常停止，立即撤销本次创建的权限并清理临时文件。再次启动保留原账本；崩溃留下的凭据到期失效，不自动续期。

**允许提出计划：**两条负责人命令都加 `--allow-plans`。启动时本人通过官方飞书授权页验证提交身份。Agent 可查看项目资料、准备脚本修订和视频计划；尚未开启调度时不会生成。接线不把管理员密码、用户 token 或模型 Key 放进 Codex 上下文。

**允许执行已批准任务：**先完成原表接线，核对当前待处理任务清单，再在 inspect/start 都加 `--execute-approved`，start 另加 `--dedicated-app`。这个动作可能推进清单中已获准的真实模型任务，不能当作免费试用开关；真实使用需事先明确费用与调用范围。每个脚本/视频计划仍须负责人独立批准，脚本审核通过不等于视频费用批准。负责人使用 `vf-run approve-script/approve-video --plan ... --plan-id ... --plan-sha256 ... --accept-provider-charges`；撤销使用对应 `revoke-script/revoke-video`，不能撤回已经提交给供应商的请求。Agent 不拥有这些管理员动作。

这限制的是项目 API 权限，不能防住拥有服务器同一 OS 账户任意 Shell 权限的人。同事正式接入应使用独立 OS 账户或受限通道，只交付其专用连接权限；不要把管理员目录或 root SSH 当成 Agent 凭据。目前为前台启动，关闭终端即停止；开机自启和独立真人安装仍单独验收。

## 同核心版本的成片搬迁

业务安装包的停机备份可以保存已登记成片。恢复到全新目录时保留任务、成片摘要、原审核事件及原始路径描述；私有映射索引只把对应成片定位到新目录中的同摘要文件。读取仍验证权限、符号链接和文件摘要，不按文件名猜测来源。

这是同核心版本的离线恢复，不是跨版本升级。原商品参考图、私有项目计划仍须单独保留；恢复不启动调度、不恢复临时授权、不调用模型，也不替代人工审核。操作入口及安装包版本以安装包 BUSINESS_BUNDLE.md 为准。
