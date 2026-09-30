# 原生 SQLite 运行底座（0.1.0a8 起）

本文保留原生/SQLite 路径及其恢复边界。当前 a10 的完整容器部署、PostgreSQL 迁移、含密钥和媒体的全组件备份及升级，参见 [STACK_USAGE.md](STACK_USAGE.md)；下述“未接通 n8n 组合”是 a8 的历史范围，不是当前容器路径的状态。

这是可安装的 **单主机运行账本 Alpha**，不是完整视频工厂发行版。它补齐管理账号、加密秘密、持久任务/版本/审核、安装入口与账本恢复；飞书身份、通用付费 worker、n8n 组合、自动媒体验证仍未接通。不能用这些命令宣称 P2–P5 整体完成。

## 本轮实现及技术选择

- `vfctl host preflight/install`：明确 SSH 主机、用户、私钥和已有 known_hosts；严格检查主机密钥，不自动接受新指纹。输入走 stdin。验证 Linux x86_64、Python >=3.11、512 MiB 可用空间和端口；离线 wheel 安装进独立 venv，固定文件哈希清单、单安装锁、同清单续装，拒绝换目标/版本后直接覆盖。
- `vfctl runtime`：客户 OS 用户私有目录，独立管理员密码（scrypt）、一小时会话、失败登录限流、项目范围 reviewer、撤销用户、OS 所有者恢复管理员并注销全部会话。当前是本地客户账号，**不是飞书身份认证**。
- 秘密采用 cryptography Fernet 认证加密；数据库只存密文，主密钥通过私有文件单独保管；有凭据覆盖、主密钥事务轮换。没有 HTTP 解密或管理员恢复入口。主密钥文件与异机恢复副本由客户保管，不包含在账本备份。
- 多项目/多任务/多修订 SQLite 账本，事务领取、配置摘要核对、审核事件重放、旧版审核拒绝、供应商 ID 绑定。领取后状态先持久化为 submission_unknown，重启不重新领取。没有真实模型适配器，配置只允许 deferred，避免把状态机当成已接通模型。
- loopback HTTP 服务（127.0.0.1），通过 SSH 隧道管理，不提供公网密码页面。输入限制、固定错误码、不记原始请求日志。
- 加密一致性账本备份，在全新空目录恢复并注销旧会话；错误密钥/篡改/非空目标阻断。包含加密秘密、项目、任务和审核，但不含主密钥、外部媒体及 n8n。

这一开发里程碑使用单主机 SQLite 和原生 venv，便于先验证持久语义；没有悄悄把 PRD 规划的 PostgreSQL/容器组合标为完成。后者仍需迁移、升级与完整组件恢复验收。该版本不支持集群或多主机共享数据库。

## 在客户主机使用

先安装从受信任构建获取、已校验的 wheel 及其锁定依赖，再创建仅本人可访问的目录。以下为真实存在的命令；本地管理员权限是可信边界，不能防御同一 OS 用户或 root。

```sh
mkdir -m 700 /absolute/customer-data
vfctl runtime install --root /absolute/customer-data --deployment customer-alpha
vfctl runtime doctor --root /absolute/customer-data
vfctl runtime keygen --root /absolute/customer-data --output /absolute/private/master.key
vfctl runtime login --root /absolute/customer-data --output /absolute/private/admin.token
vfctl runtime secret-set --root /absolute/customer-data --token-file /absolute/private/admin.token --master-key-file /absolute/private/master.key --alias model
vfctl runtime serve --root /absolute/customer-data --port 8787
```

密码和凭据默认隐藏输入；无 TTY 必须提供 owner-only 普通文件，不能在命令行写秘密值。token 只写新建私有文件，不打印值；过期后删除旧 token 文件再登录。主密钥/备份密钥请保存恢复副本。

SSH 安装使用 `vfctl host preflight` 或 `install`，共同参数：`--host HOST --user USER --identity /private/id --known-hosts /private/known_hosts --root /remote/private/customer --deployment customer-alpha`。install 另需 `--wheelhouse /release/wheels`（产品 wheel 和全部依赖），并隐藏输入管理员密码。目录的父目录必须存在、属于 SSH 用户且不可被他人写入；安装目标必须为专用新目录，续装必须匹配原清单。散列防止传输/续装漂移，**不替代发行者签名或信任来源校验**。首次可信主机指纹需通过客户云控制台等独立渠道取得。

安装生成 `video-factory.service`，不会偷偷启用系统服务。客户管理员将该 unit 安装到自己的 systemd user 配置后，运行 `systemctl --user daemon-reload` 及 `systemctl --user enable --now video-factory`；若需要注销后持续运行，由服务器管理员为该客户 OS 用户开启 linger，再验收开机启动。当前 CI 验证进程重启，不冒充操作系统重启。

## 管理 API

使用 SSH 转发到 127.0.0.1:8787；`POST /v1/login` 输入 name/password，其他请求使用 Bearer 会话。所有 POST 必须为 JSON，最多 64 KiB。

| 路径 | 用途 |
|---|---|
| `/v1/projects` | 管理员创建配置；变更必须带原 expected_digest |
| `/v1/users`、`/v1/users/revoke` | 管理员配置或撤销客户本地项目审核人 |
| `/v1/tasks` | 管理员创建任务；返工带 expected_revision，旧版保留 |
| `/v1/tasks/read` | 管理员或本项目审核人读取版本 |
| `/v1/reviews` | 认证会话人员提交 event/project/task/revision/stage/decision/feedback |
| `/v1/doctor` | 管理员查看脱敏数据库健康和状态计数 |

配置字段为 video_route（仅 deferred）、credential_ref（secret:别名）、billing_owner。任务 payload 为 sku_id/script/source_revision。审核 stage 为 script/video，decision 为 accept/reject；拒绝必须有意见。此入口不创建或修改飞书记录，不冒充员工已在飞书审核。

## 备份、恢复与轮换

```sh
vfctl runtime keygen --root /absolute/customer-data --output /absolute/private/backup.key
vfctl runtime backup --root /absolute/customer-data --backup-key-file /absolute/private/backup.key --output /absolute/private/ledger.vfb
mkdir -m 700 /absolute/restored-data
vfctl runtime restore --root /absolute/restored-data --backup-key-file /absolute/private/backup.key --source /absolute/private/ledger.vfb
vfctl runtime recover-admin --root /absolute/restored-data
```

恢复后必须重新登录，并另行配置原秘密主密钥；未确定的提交保持未确定，不再自动生成。备份工具限制数据库 64 MiB，此 Alpha 不提供媒体归档或自动保留删除。

主密钥轮换先离线保存新旧密钥，再执行 `runtime key-rotate --root ... --token-file ... --master-key-file OLD --new-key-file NEW`；成功后客户更新 worker 的密钥引用。事务失败保留原密文；成功后旧备份仍需旧密钥，因此不要提前销毁恢复材料。管理员恢复依赖客户 OS 访问权，不能远程仅凭姓名重置。

## 下一道真实验收仍需完成

1. 隔离云端的真实 SSH 安装/续装已通过；客户主机、systemd 开机启动及全部组件回读仍需验收。
2. n8n/PostgreSQL 组合、迁移及产品升级已补齐并在云端验证；物理异机恢复与正式发行待验收。
3. 飞书租户真实身份、新 Base/SKU、真人退回/返工/通过、外部字段冲突处理。
4. 通用模型 worker、素材/完整媒体解码、真实供应商提交与查询，以及精确费用授权。
5. 非作者安装与交接；完整安装 Skill/页面需在实际命令契约验收后发布。

密码哈希与加密实现参考 [Python scrypt](https://docs.python.org/3/library/hashlib.html#hashlib.scrypt)、[cryptography Fernet](https://cryptography.io/en/latest/fernet/)。这些是实现依据，不是安全审计或业务验收证明。

验证回执与范围见 [STATUS.md](CUSTOMER_GUIDE.md)。112 项回归、云端原生安装、SSH 安装、重启和账本恢复已通过；测试未使用客户凭据或调用模型。
