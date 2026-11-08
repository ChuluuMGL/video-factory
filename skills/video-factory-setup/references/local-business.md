# 本机业务 Setup 候选

这是源码候选，不在 a41 中。先核对安装的核心含 `vfctl local-setup`；不能只更新本 Skill 就声称客户已获得新功能。

1. 使用同版本核心 wheel 与 `scripts/install_local_kernel.py --mode business --root <全新安装目录> --package <已校验的wheel>`。安装器创建私有环境和 `vf-setup`，不创建假模型数据。
2. 准备新的私有计划文件路径。运行 `<安装目录>/vf-setup --plan <计划文件> --interactive --bind`，按终端问题配置项目、SKU、专用飞书应用、已有测试表和模型。密钥只走隐藏输入；本人在飞书官方页面授权，产品不启动安装网页。
3. Agent 可整理同一非秘密计划 JSON，再用 `--check` 预检。不要把 Key 或密码写入计划。详细字段和限制见[候选指南](https://github.com/ChuluuMGL/video-factory/blob/codex/local-business-setup/docs/product/LOCAL_BUSINESS_SETUP.md)。
4. `--native-plan` 读取原表字段/订阅修改计划。核对表与应用范围，确认没有其他接收器后，才用 `--enable-native <计划摘要> --dedicated-app` 启用。
5. 中断后使用原计划和原目录续接，已保存 Key 自动复用。配置不一致、主密钥丢失或解密失败时停止，不删除账本、不重建来绕过错误。
6. 首次跳过视频后补配：核对安装核心支持 `--previous-plan`，正常停止该目录的服务，保留原计划并复制一份新计划，只改 `video`。执行 `vf-setup --previous-plan <原计划> --plan <新计划>`，视频 Key 走隐藏输入或本人 0600 文件。此操作保留已审核脚本、原表绑定和已有凭据，无需重建项目或再次审核脚本。完成后使用新计划启动；费用批准与生成仍单独执行。其他配置变更不支持通过此入口覆盖。

配置成功、真实绑定、原表审核启用、执行权限、费用审批和实际模型生成分别记录。本向导不调用模型、不发放执行权限、不自动启动调度。脚本模型目前为 DeepSeek，视频为 MiniMax-H3；其他便携 n8n 模型不自动等于本地内核支持。真实飞书闭环、自动启动及独立人员安装仍需验收。

## 已安装候选的运行与外部 Codex

同版本业务安装新增 `vf-run` 和 `vf-agent`。本人先运行 `vf-run inspect --plan <原计划>`，核对返回摘要后运行 `vf-run start --plan <原计划> --expect-plan <摘要>`。默认只读；正常停止用 Ctrl-C，最长两小时，到期不续权。

Codex 只使用返回的连接路径：`vf-agent --connection <connection.json> capabilities`，然后 `tasks/status`。请求 JSON 只包含非秘密参数；不要读取或输出相邻 token 文件。不要让模型运行管理员批准命令或收集管理员密码。`--allow-plans` 必须由负责人在 inspect/start 同时明确选择；本人官方飞书授权后可准备计划。`prepare-script` 使用 `record/request_id/expected_revision`，`submit-script` 使用 `plan_id/plan_sha256`；未获负责人批准必须停止，不尝试绕过。

执行是独立开关 `--execute-approved`，必须核对当前待处理任务及费用，完成原表接线并确认专用应用。它可能执行已经批准的模型任务，不是免费演示。管理员用隐藏输入独立批准/撤销精确摘要，Agent CLI 没有审批、Shell、任意地址或跨项目入口。

项目 API 权限不等于同一 OS 账户的 Shell 隔离。同事正式接入需独立账户或受限通道，不给管理员目录或 root。前台启动与自动启动分别验收；CI 或助手代操作不能替代同事本人验收。
# macOS Python 环境检查

若使用 uv 管理的 Python，采用修复后通过符号链接创建 venv 的同版本安装包，并保留基础 Python。旧包可能在 `ensurepip` 阶段因找不到 `libpython` 中止；保留失败检查点，在全新目录使用修复包重试，不覆盖已有账本。安装通过不代表真实飞书、模型或员工身份已通过验收。

## 业务员工接入候选

管理员可显式使用 `--employee-config` 为同一业务账本绑定一个飞书员工，最长两小时、默认只读。员工通过独立客户端和本人设备授权获取短期项目凭证；不共享管理员连接目录。管理员通过 employee-status/suspend/resume 查看与撤销接入，恢复或重启必须重新配对。员工计划仍需管理员逐项批准费用。详见 docs/product/BUSINESS_EMPLOYEE_ACCESS.md；合成身份验证不能替代真人验收。工作流观测只读管理员固定的短时快照，不是已部署生产修复。

可另选 `repair: {profile: "single-select-decoder-v1", allow_apply: true}`，仅为新隔离实例预授权固定源码类型的修复。调用同一员工 API 的 repair/inspect、propose、validate、apply、rollback，按返回候选版本和唯一请求标识操作。禁止把代码、路径或 Shell 当作参数，禁止执行生产补丁；暂停/恢复或重启后重新验证。该能力不要求 allow_plans 或 execute-approved，不因修复而生成脚本。详见随包 BUSINESS_SOURCE_REPAIR.md；它是产品源码样例的业务适配器，不等于完整生产工作流修复。
