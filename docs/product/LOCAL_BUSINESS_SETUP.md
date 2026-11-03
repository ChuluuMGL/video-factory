# 本机业务配置候选

本说明对应新增 `vfctl local-setup`，尚未发布到 a41。使用此分支构建并校验的核心 wheel；旧发行包和假模型体验包不会自动获得此命令。支持 macOS/Linux，Windows 原生另行验收。

本机业务数据必须放在**新目录**，不能把 `vfctl local` 的假模型账本转换成真实项目。现有 n8n/完整服务器部署不变。本步骤不安装容器、不启动执行、不提交任何模型请求。

## 操作员的准备

准备专用飞书应用、已有测试 Base 与任务表、本人可访问该表的账号、商品 SKU 和真实资料。应用权限及记录变更订阅按 [原表审核说明](FEISHU_NATIVE_REVIEW.md) 配置。暂不支持这个入口自动新建 Base。

Agent 可以帮助查找组织、Base、表和字段 ID，整理非秘密 JSON。密码、App Secret、模型 Key 在终端隐藏输入；也可使用本人所有、权限 0600 的独立文件。不要放到聊天、JSON 计划、命令参数值、Git 或日志。

当前内核脚本模型是 `deepseek-flash`，视频是 MiniMax-H3（cn/global）。此候选不把便携 n8n 的全部模型支持当成本机内核支持。可输入 `skip` 暂不配置视频。

## 首次配置

先安装本候选 wheel 到私有 Python 环境，使用该环境的 `vfctl`。在自己的目录准备一个仅本人可访问的计划目录：

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

这一步会按计划修改测试表审核字段/订阅，不调用模型。它不发放执行密钥，也不代表已开始后台调度。执行仍按 [本机内核](LOCAL_AGENT_KERNEL.md) 显式配置项目执行权限与每条费用审批；脚本通过不授权视频费用。本候选尚未整合自动启动、执行权限和非作者一键安装验收。

## 续接与新增项目

- **中断、网络失败或授权过期：**保留同一 root 和计划，再执行相同命令；已经保存的 Key 会复用，不要求重输。飞书授权过期只重新授权，未完成外部写入不自动重试。
- **新增项目：**使用同一个 root、不同的 project 标识和新的计划文件，沿用管理员密码；原项目不改。新项目的应用/模型凭据单独加密保存。
- **配置变更：**同项目的 SKU、模型、应用或目标表不一致会停止，不静默覆盖。当前向导是创建/续接入口，升级/修改已有项目要走单独审查流程。
- **文件保存：**`runtime.sqlite3` 保存配置和加密凭据，`master.key` 为本人所有的 0600 文件。保留两者的私有备份；加密不能抵御拥有同一 OS 账户读取权限的人。

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
