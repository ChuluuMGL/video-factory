# 从 Setup 到客户主机安装及项目导入

交互安装与接入可使用 [统一终端向导 setup-run](SETUP_RUN_USAGE.md)。以下独立命令继续保留用于分阶段操作。

分阶段入口 `vfctl setup-deploy plan/apply/status`，将完成的 Setup 连接到真实容器安装和 PostgreSQL 项目/SKU 保存。本命令只负责服务和项目导入；飞书创建及身份由 setup-run 后续接入步骤处理，模型生成单独验收。本阶段返回 `business_ready=false`。

## 在哪台机器运行

本入口**在客户目标 Linux 主机上运行**。先通过已核对主机指纹的 SSH 登录；准备 Docker/Compose、管理 CLI 和受信任的完整 wheelhouse，要求与 [完整环境手册](STACK_USAGE.md) 相同。当前命令不从操作员电脑自动 SSH 部署，也不自动配置 Docker。

把 Setup 会话放在该主机 root 私有目录；可以在该目录运行交互向导，或安全转移已有会话后核对所有者和 0600 权限。会话仅含配置及凭据引用。不要将 Key 填入会话，不把管理员密码放在命令行。

## 审阅和执行

```sh
sudo vfctl setup --session /root/vf-private/customer.setup.json --interactive
sudo vfctl setup-deploy plan --session /root/vf-private/customer.setup.json --root /opt/video-factory --host customer.example.com --wheelhouse /root/verified-release/wheels
```

`--host` 必须逐字匹配 Setup 的目标。计划返回声明主机、本机 hostname、machine-id 哈希、目标目录、镜像与 wheel 哈希、端口、项目和 SKU 数量。它不靠 DNS 推断“这就是那台服务器”；管理员需要核对自己登录的目标和本机信息。原始 machine-id 不输出。

确认具体计划后，用返回的 `execution_sha256`：

```sh
sudo vfctl setup-deploy apply --session /root/vf-private/customer.setup.json --root /opt/video-factory --host customer.example.com --wheelhouse /root/verified-release/wheels --expect-plan RETURNED_EXECUTION_SHA256
```

这一个执行命令会隐藏询问产品管理员密码（新安装设置，已有安装验证），安装或续装完整组件，登录产品管理员，事务导入项目配置/SKU，重新读取验证并撤销临时登录会话。无终端时可以用 `--password-file /root/vf-private/admin-password`，必须是 owner-only 普通文件。

计划哈希绑定 Setup 配置、本机身份、目录、版本和端口；这些发生改变必须重新审阅。计划不创建安装目录；拒绝旧哈希时不会开始安装。会话在执行期间持有锁，不能一边安装一边改答案。

## 重复执行、新项目和冲突

同一计划再次执行复用已有项目和 SKU，不覆盖密钥、任务或人员。安装中断可用相同计划续装；不要改版本后把安装当成升级，版本变更使用 `stack upgrade`。如果服务已安装但项目导入失败，应先 `stack status` 判断服务，再检查管理员密码、计划与冲突代码。

为同一客户添加项目，用 `setup --from-session` 建立新会话，补充新项目问题，再对原安装目录 plan/apply。只有组织及部署配置完全一致才允许复用。原项目的 SKU、负责人、Base、模型意图变化会报 `SETUP_PROJECT_CHANGED_RECONCILIATION_REQUIRED`；本开发版不静默更新已导入项目，也不删除任务来消除冲突。已有但不属于 Setup 的同名运行项目不会被自动接管。

```sh
sudo vfctl setup-deploy status --session /root/vf-private/customer.setup.json --root /opt/video-factory --host customer.example.com
```

status 会验证本地管理员并读取当前数据库和基础服务。离线 `vfctl setup --json` 仍报告规划状态；它不会拿一份历史回执冒充当前服务在线。

## 结果含义

- `infrastructure_and_project_draft_ready`：组件及入口已就绪，项目配置和 SKU 已入客户数据库并回读。
- `requested_video_route` 保留用户的选择；`active_video_route=deferred` 明示尚未启用通用模型。
- 未配置的运行凭据/费用账户使用明确的 `unconfigured` 标记，不冒用客户选择或原项目 Key。
- `credentials_resolved=false`、`feishu_identity=not_verified`、`feishu_resources=not_created`：未读取外部 Key、没有把飞书引用当成身份验证，也没有创建飞书用户/表。
- `business_ready=false`：仍待飞书、通用模型和真人验收。保存 SKU 不等于向飞书导入 SKU。

测试以当前提交云端 Actions 为准：新增覆盖计划绑定、空目录安装、重复导入、第二项目、冲突和临时会话撤销；不在本地执行运行测试。
