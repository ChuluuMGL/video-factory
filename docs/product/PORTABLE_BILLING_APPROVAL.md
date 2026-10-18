# 便携路线：负责人身份与费用确认

这是 PR #49 的开发候选。代码、合成测试、真实飞书事件和第二账号隔离分别验收；本文不是稳定版已交付声明。

## 团队怎样操作

1. 员工在原任务行选择「申请脚本计划」或「申请视频计划」。视频计划不会由「脚本通过」自动触发。
2. n8n 写入「生成计划」并将状态设为「费用待确认」。计划包含本行商品、完整脚本、参考图、模型、费用账户、调用上限和有效期。
3. 指定负责人核对后，把计划中的 `id` 复制到同一行的「费用确认编号」。只修改状态或填写一个人员姓名都不算授权。
4. 接收器核对飞书官方事件中的应用、组织、Base、表、记录及操作人 `open_id`，n8n 再核对其保存的原计划和当前行内容。通过后最多提交一次，结果回到原表供人工审核。

计划默认有效 24 小时。同一项目一次只保留一个待确认计划；员工修改商品、脚本、参考图、意见或计划展示内容后，原确认不能执行。退回脚本或视频不授权再次收费。

调用上限当前为脚本最多 2048 输出 tokens，或 H3 视频请求 6 秒、768P、9:16，各最多提交一次且不自动重试。这是调用量上限，不是人民币账单封顶；实际价格与成片规格以供应商为准。负责人每次都能在计划中看到本次参数。

## 为什么本机无需公网入口

Python 接收器使用官方 `lark-oapi` 长连接主动连接飞书，收到事件后转交 n8n 的专用鉴权入口。本机 n8n 仍可只监听 loopback；远端 n8n 使用 HTTPS。它不是员工网页，也不承担生成任务，生成仍在 n8n 中执行。

接收器密钥和模型密钥分开。App Secret 只用于飞书连接；模型 Key 继续留在 n8n 凭据库。接收器的首次凭据配置使用隐藏终端输入，随后保存为加密文件并限制目录访问：POSIX 仅目录所有者，Windows 仅当前用户 SID。解密密钥也在此私有目录，能够读取整个目录的主机管理员仍能访问凭据；这不等于硬件密钥保险库。目录不能同步到公开仓库、共享网盘或安装页面。

**同一飞书应用的多个长连接会分流事件。** 不要同时启动两个独立接收器，也不要让旧系统与新系统抢同一应用的事件。接入前检查已有接收器；跨系统迁移必须先明确暂停与恢复范围。当前候选按一个接收器绑定一个项目，其他项目应使用独立应用；共用应用的多项目分发尚未提供。

## 安装人员配置

先完成 `welcome` 的 n8n/飞书/模型配置，并通过原有 schema 流程补充「生成计划」「费用确认编号」和相关状态选项。先核对负责人的同应用 `open_id` 与组织 `tenant_key`；不要凭同名人员或另一应用的 ID 推断身份。

在单独 Python 虚拟环境安装固定依赖：

```sh
python -m pip install cryptography==46.0.5 lark-oapi==1.7.3 websockets==15.0.1
```

以下路径与 ID 均为占位符，由 Agent 根据已有项目回执填写。`--root` 指向原有 `bindings` 目录；配置编号使用短的字母、数字和短横线。

```sh
python portable_n8n.py --root /PRIVATE/bindings approval-setup \
  --url http://127.0.0.1:5678 --project PROJECT \
  --base BASE_TOKEN --table TABLE_ID --app APP_ID \
  --tenant TENANT_KEY --billing-owner OWNER_OPEN_ID \
  --billing-account ACCOUNT_LABEL --configuration CONFIG_ID
```

默认导入**不含模型节点**的零费用验收流程，保持关闭。接收器在独立私有目录保存续接配置。飞书应用需已发布多维表格记录变更长连接事件，订阅此 Base，并有原表所需权限；没有权限时保留错误，不擅自扩权。

```sh
python portable_events.py --root /PRIVATE/bindings/PROJECT-events serve
```

在另一个终端显式开启验收入口：

```sh
python portable_n8n.py --root /PRIVATE/bindings approval-enable \
  --url http://127.0.0.1:5678 --project PROJECT
```

先完成一次真实负责人填写确认编号的零费用验收。只有 SDK 接收事件、计划被消费、回执确认零费用后，才允许提升到付费候选：

```sh
python portable_n8n.py --root /PRIVATE/bindings approval-promote \
  --url http://127.0.0.1:5678 --project PROJECT
```

此命令仅导入关闭的付费工作流，不生成内容。重启接收器以读取新入口，核对后再次 `approval-enable`。旧的人工收费测试流程保持关闭。启动时不重复输入 App Secret；n8n 管理命令仍通过隐藏输入验证 API Key。

同一配置目录有进程锁，重复启动会被拒绝；该锁不能发现其他电脑或旧系统中的接收器。

接收器可前台运行，也可安装后台自启。先停掉本项目的前台接收器，核对同一应用无其他长连接，再执行：

```sh
python portable_service.py --root /PRIVATE/bindings/PROJECT-events --component events --exclusive-app install
python portable_service.py --root /PRIVATE/bindings/PROJECT-events --component events status
python portable_service.py --root /PRIVATE/bindings/PROJECT-events --component events restart
```

首次在私有目录创建 Python 环境并安装固定依赖，随后复用已加密保存的凭据。Linux root 服务随开机启动；Mac、Windows 默认随当前用户登录启动；普通 Linux 用户见[后台运行说明](PORTABLE_N8N.md#后台运行与自启开发候选)。`status` 证明 OS 进程运行，不替代真实 SDK 身份回执。接收器等待 n8n 健康检查后才接收事件；未知派发仍保留为待核对，不因重启重发。

`stop` 暂停接收，`uninstall` 停止并取消自启，均保留凭据、事件队列和历史回执。同一应用旧接收器的暂停与恢复必须单独核对。服务启动不会激活 n8n 流程；已启用的流程仍按原确认规则执行。独立 ECS 的 systemd 生命周期、实际整机重启及注销恢复已经通过；桌面服务生命周期通过四平台 CI，Mac / Windows 实际退出系统并重新登录尚待验收。详见[当前记录](PORTABLE_N8N.md#2026-10-18-最终技术收尾)。

## 失败与停用

- `BILLING_OWNER_DENIED`：事件操作人不属于指定负责人，不调用模型。
- `APPROVED_INPUT_CHANGED` / `APPROVAL_TASK_OR_PLAN_CHANGED`：内容或计划改变，原确认无效。
- `APPROVAL_EXPIRED`：原计划过期；先核对并取消，再申请新计划。
- `needs_reconciliation` / `dispatch_intent`：发送结果未知或进程中断。先查原 n8n 结果与供应商回执，不能清队列重发。
- 已生成但飞书写回失败：沿用原 `recover` 流程补写，不重新生成。已消费的费用计划不能通过取消命令重置；恢复后仍需核对审批锁，不能直接删除数据表。

取消仅限尚未消费的确切计划：

```sh
python portable_n8n.py --root /PRIVATE/bindings approval-cancel \
  --url http://127.0.0.1:5678 --project PROJECT --expect-plan EXACT_PLAN_ID
```

停用本项目全部确认与计划入口：

```sh
python portable_n8n.py --root /PRIVATE/bindings approval-disable \
  --url http://127.0.0.1:5678 --project PROJECT
python portable_events.py --root /PRIVATE/bindings/PROJECT-events status
```

不需要再次生成视频来测试身份和费用控制。零费用真实事件验收与合成供应商测试通过后，第二账号隔离仍须另行完成。

## 已验证范围

2026-10-17，独立 ECS 上的真实负责人确认已通过：官方 SDK 接收飞书保存事件，操作人与配置一致，计划消费一次，模型请求为零。已保存事件的重复入队被拒绝，未再次派发；候选提升只导入关闭的付费流程，原任务字段保留、旧接收器恢复均已回读。详见[便携路线验收记录](PORTABLE_N8N.md#当前验收记录)。

本次没有验证第二个真实账号，也没有通过新确认入口再次付费生成。零费用验收不会把测试行标成已生成；以受保护计划和 SDK 回执判断结果，不能用该行仍显示「费用待确认」推断测试未收到。稳定发布与非开发者安装验收仍是后续步骤。
