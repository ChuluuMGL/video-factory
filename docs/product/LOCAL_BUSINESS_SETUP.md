# 无 n8n 本机常驻引擎一键安装与业务配置指南（v0.3）

本指南面向不会搭建 n8n、不租服务器的 macOS 与 Linux（Python 3.11+）用户，说明如何通过 Setup Skill 与 `scripts/install_local_kernel.py --mode business` 在本机一键建立独立环境、SQLite 加密账本与常驻业务引擎（`vf-setup`、`vf-run`、`vf-agent` 及 `vfctl local-service`）。a41 旧固定服务器归档未内置此入口，请使用已核验的 v0.3 版本源码或同版本核心 wheel；Windows 原生暂不支持该安装器（Windows 本机请参考 [便携 n8n 指南](PORTABLE_N8N.md)）。

本机业务数据必须放在**全新私有目录**，不能把 `vfctl local`（`--mode fake-only`）的假模型演练账本转换成真实项目。现有 n8n 与 Ubuntu 服务器完整栈部署保持不变。全流程将**环境安装、项目配置、飞书身份、付费模型执行、人工验收**五者在文案与回执中严格分离。

## 操作前准备

准备专用飞书应用、已有测试 Base 与任务表、本人可访问该表的账号、商品 SKU 和真实资料。应用权限及记录变更订阅按 [原表审核说明](FEISHU_NATIVE_REVIEW.md) 配置；当前本机入口绑定已有测试表，暂不自动新建 Base。

Agent 可协助读取环境、查找组织/Base/表/字段 ID 并整理非秘密计划 JSON。管理员密码（至少 14 位）、飞书 App Secret、模型 API Key 仅在真实私有终端隐藏输入，或通过本人所有、权限 `0600` 的独立文件传入；严禁放进聊天记录、JSON 计划、命令行明文参数、Git 或日志。

当前本机内核支持的脚本模型固定为 `deepseek-flash`，视频模型固定为 `MiniMax-H3`（`cn` 或 `global`）；不把便携 n8n 的扩展供应商直接等同于本机内核已支持。首次配置可输入 `skip`（或设 `video: null`）暂不配置视频，先跑通脚本审核闭环。

## 阶段一：一键安装独立环境（不收密钥、不启服务）

使用 v0.3 安装器指定 `--mode business` 与一个父目录已存在、无符号链接的全新绝对路径：

```sh
# 方式 A：在已核验的 v0.3 仓库目录直接安装
python3 scripts/install_local_kernel.py --mode business --root "$HOME/VideoFactory"

# 方式 B：使用已校验的本地核心 wheel（可选离线依赖目录）
python3 scripts/install_local_kernel.py --mode business --root "$HOME/VideoFactory" \
  --package /绝对路径/已校验的候选.whl --wheelhouse /绝对路径/wheels
```

安装器创建 `0700` 根目录、独立 `environment/` 虚拟环境，并生成三个绑定 `~/VideoFactory/business` 账本的固定启动器：`vf-setup`、`vf-run`、`vf-agent`（另含 `~/VideoFactory/environment/bin/vfctl`），不创建假模型账本、不安装容器、不启动进程、不调用模型。回执为 `installed: true`、`configuration_complete: false`、`production_ready: false`、`real_model_calls: 0`。

macOS / Linux 的独立虚拟环境通过符号链接引用安装时的基础 Python（修复旧安装器在 `uv` 管理的 macOS Python 上复制可执行文件导致丢失 `libpython` 的问题）；日常运行期间请保留该基础 Python。若安装中途异常中止，原目录与 `install.json`（`incomplete_preserved`）会保留供诊断，切勿手改半成品目录，修复前置条件后换全新目录重试。

## 阶段二：首次项目配置与参考图自动托管

准备一个仅本人可访问的私有配置目录，运行 `vf-setup`（或已安装环境中的 `vfctl local-setup --root "$HOME/VideoFactory/business"`）：

```sh
mkdir -m 700 "$HOME/vf-local-config"
"$HOME/VideoFactory/vf-setup" \
  --plan "$HOME/vf-local-config/project.json" --interactive --bind
```

1. **非秘密计划与离线预检**：终端先询问部署标识（`deployment`）、项目标识（`project`）、计费负责人（`billing_owner`）、专用飞书 `app_id`、SKU 列表（`sku_id`、`name`、`truth_source`）、测试表映射（`tenant_key`、`base_token`、`table_id` 及 `task`/`sku_id`/`script`/`source_revision` 四个字段 ID，`submitters`/`reviewers` 的 `open_id`）及模型配置。Agent 也可预先写入同一 `0600` JSON 并执行 `"$HOME/VideoFactory/vf-setup" --plan "$HOME/vf-local-config/project.json" --check` 做离线校验（`provider_requests: 0`、`writes: 0`）。
2. **秘密加密入库**：管理员密码、飞书 App Secret、DeepSeek Key 与（启用视频时的）MiniMax Key 在终端隐藏输入（或传入本人 `0600` 的 `--password-file`、`--app-secret-file`、`--script-key-file`、`--video-key-file`），由 `business/master.key`（`0600`）加密保存在 `business/runtime.sqlite3`。
3. **参考图自动核验与私有托管**：启用视频时，参考图可直接从本机普通可读目录选取（无符号链接绝对路径，支持 `.png`、`.jpg`、`.jpeg`、`.webp`，单张不超过 30 MiB，单 SKU 1–9 张合计不超过 45 MiB，视频时长 4–15 秒）。向导核验路径、大小与 SHA-256 后，自动复制到 `~/VideoFactory/business/references/<project>/<sku>/` 私有项目素材区（目录 `0700`、文件 `0600`，按 `<sha256>.<后缀>` 固化），原图保留在原位，无需建立 `/work` 或递归更改共享文件夹权限。

回执中 `configuration_saved: true` 仅代表本地配置与加密凭据已保存，`paid_execution_enabled: false`、`provider_requests: 0`。

## 阶段三：飞书官方身份授权与原表审核接线

1. **只读绑定核对（`--bind`）**：仅打开**飞书官方身份授权页面**（终端打印官方 `verification_uri` 与 `user_code`），产品不启动安装网页，也不让 Agent 收集用户 token。本人完成授权后，程序只读核对飞书租户与四个映射文本字段并保存绑定，返回 `feishu_binding_verified: true`。
2. **原表审核先审阅后启用**：
   ```sh
   "$HOME/VideoFactory/vf-setup" \
     --plan "$HOME/vf-local-config/project.json" --native-plan
   ```
   核对返回计划中的表、审核字段及订阅范围，确认专用飞书应用没有挂载其他长连接接收器后，使用刚返回的计划哈希显式启用：
   ```sh
   "$HOME/VideoFactory/vf-setup" \
     --plan "$HOME/vf-local-config/project.json" \
     --enable-native <刚核对的计划哈希> --dedicated-app
   ```
   此步骤仅修改测试表审核字段与订阅（返回 `native_review_enabled: true`），不调用模型、不发放执行密钥，也不自动启动后台调度；执行边界详见 [本机内核规范](https://github.com/ChuluuMGL/video-factory/blob/main/docs/product/LOCAL_AGENT_KERNEL.md)。

## 续接、停机增量改配置、补配视频与新增项目

- **中断、网络失败或授权过期**：保留同一安装目录与计划文件，重跑原命令即可自动复用已保存的加密 Key，无需重新输入；飞书授权过期仅需本人重新授权，未完成的外部写入不自动重发。
- **停机增量改配置（更新商品、参考图与成员）**：先正常停止该业务目录正在运行的服务，编辑私有 `project.json` 后重新运行 `"$HOME/VideoFactory/vf-setup" --plan "$HOME/vf-local-config/project.json"`：
  - 支持更新 SKU 列表、名称、`truth_source` 卖点规格、视频参考图（自动重新校验 SHA-256 并托管到私有素材区）以及 `binding` 中的 `submitters` / `reviewers` 成员。
  - 历史任务、审核记录与加密凭据完整保留，审计表记录变更前后 `plan_sha256`（返回 `updated: true`）；同时自动吊销旧的员工短期凭证，并使尚未提交给供应商的待执行批准失效，不影响已提交或结果未知的任务回执。
  - 已绑定的目标表（`tenant_key` / `base_token` / `table_id`）、专用 `app_id`、计费归属（`billing_owner`）和已启用视频项目的账号区域（`region`）保持不可变；更换这些边界请建立独立新项目。
- **脚本跑通后补配视频（`--previous-plan`）**：首次将 `video` 设为 `null`（`skip`）的项目，先正常停止该目录的运行进程，保留原 `project.json` 供审计，复制为 `project-with-video.json` 并仅补齐 MiniMax-H3 的 `model`/`region`/`assets` 配置（其余字段保持一致），执行：
  ```sh
  "$HOME/VideoFactory/vf-setup" \
    --previous-plan "$HOME/vf-local-config/project.json" \
    --plan "$HOME/vf-local-config/project-with-video.json"
  ```
  终端验证管理员并隐藏输入视频 Key（支持本人 `0600` 的 `--password-file`、`--video-key-file`），在同一事务内写入视频凭据、托管参考图与变更回执，保留原任务版本、已审脚本与飞书绑定（`video_added: true`）。后续 `vf-run` 改用新计划文件重新 `inspect`；视频费用仍按任务单独审批。
- **同底座新增项目**：使用同一个安装目录、不同的 `project` 标识和新的私有计划文件，沿用管理员密码；新项目的应用与模型凭据独立加密保存，原项目不受影响。
- **私有备份边界**：请将 `business/runtime.sqlite3`、`business/master.key`（`0600`）与私有计划文件妥善离线备份；文件加密不能抵御拥有同一操作系统账户读取权限的进程或人员。

## 阶段四：常驻运行、launchd / systemd 启停与逐单付费审批

### 1. 前台常驻与受限 Codex 接入

配置与绑定完成后，负责人在私有终端先核对启动计划摘要，再启动常驻引擎：

```sh
"$HOME/VideoFactory/vf-run" inspect --plan "$HOME/vf-local-config/project.json"
"$HOME/VideoFactory/vf-run" start --plan "$HOME/vf-local-config/project.json" \
  --expect-plan <刚返回的plan_sha256>
```

密码由终端隐藏输入（或传入 `0600` `--password-file`）。默认以**只读常驻**模式监听 `127.0.0.1:8788`（不连飞书接收器、不调模型；可在 `inspect` 与 `start` 同时加 `--max-runtime <秒>` 限制运行时长）。启动回执输出的临时 `connection.json` 可交给同一受控环境中的 Codex / Agent：

```sh
"$HOME/VideoFactory/vf-agent" --connection /实际返回路径/connection.json capabilities
"$HOME/VideoFactory/vf-agent" --connection /实际返回路径/connection.json tasks
"$HOME/VideoFactory/vf-agent" --connection /实际返回路径/connection.json project_stats
"$HOME/VideoFactory/vf-agent" --connection /实际返回路径/connection.json list_failures
```

Ctrl-C 正常停机时立即销毁临时会话并撤销本次授权；重启保留账本并清理上次异常退出遗留的本地会话凭据。

- **允许提出计划（`--allow-plans`）**：在 `inspect` 与 `start` 同时加 `--allow-plans`，启动时通过飞书官方授权验证提交人本人身份。Agent 可读取目录并准备脚本修订或视频计划，但未获负责人批准与执行开关前不会触发模型调用，也不会把管理员口令、用户 token 或模型 Key 暴露给 Agent。
- **允许执行已批准任务（`--execute-approved`）与逐单计费确认**：先完成原表审核启用并核对待处理任务，在 `inspect` 与 `start` 同时加 `--execute-approved` 及明确运行时长上限（`--max-runtime 1..604800` 秒或 `--hours 1..168`），`start` 另加 `--dedicated-app`。每条脚本或视频计划仍须负责人在私有终端独立确认计费：
  ```sh
  "$HOME/VideoFactory/vf-run" approve-script --plan "$HOME/vf-local-config/project.json" \
    --plan-id <计划ID> --plan-sha256 <计划摘要> --accept-provider-charges
  "$HOME/VideoFactory/vf-run" approve-video --plan "$HOME/vf-local-config/project.json" \
    --plan-id <计划ID> --plan-sha256 <计划摘要> --accept-provider-charges
  ```
  撤销尚未提交供应商的计划使用 `revoke-script` / `revoke-video`；脚本审核通过不自动授权视频费用。

### 2. 用户级后台常驻启停（macOS `launchd` / Linux `systemd`）

需要在关闭终端窗口后继续保持默认只读常驻时，先用相同计划（及可选 `--port`、`--max-runtime`）完成 `vf-run inspect` 拿到 `plan_sha256`，再使用安装环境中的 `vfctl local-service` 渲染 `0600` 用户服务定义并显式启停：

- **macOS (`launchd`)**：
  ```sh
  "$HOME/VideoFactory/environment/bin/vfctl" local-service render --platform launchd --name daily \
    --root "$HOME/VideoFactory/business" --plan "$HOME/vf-local-config/project.json" \
    --password-file "$HOME/vf-local-config/admin.pass" --expect-plan <已核对的plan_sha256> \
    --output "$HOME/Library/LaunchAgents/com.video-factory.daily.plist"
  "$HOME/VideoFactory/environment/bin/vfctl" local-service start --name daily
  "$HOME/VideoFactory/environment/bin/vfctl" local-service status --name daily
  "$HOME/VideoFactory/environment/bin/vfctl" local-service stop --name daily
  ```
- **Linux (`systemd --user`)**：
  ```sh
  "$HOME/VideoFactory/environment/bin/vfctl" local-service render --platform systemd --name daily \
    --root "$HOME/VideoFactory/business" --plan "$HOME/vf-local-config/project.json" \
    --password-file "$HOME/vf-local-config/admin.pass" --expect-plan <已核对的plan_sha256> \
    --output "$HOME/.config/systemd/user/video-factory-daily.service"
  systemctl --user daemon-reload
  "$HOME/VideoFactory/environment/bin/vfctl" local-service start --platform systemd --name daily
  "$HOME/VideoFactory/environment/bin/vfctl" local-service status --platform systemd --name daily
  "$HOME/VideoFactory/environment/bin/vfctl" local-service stop --platform systemd --name daily
  ```

渲染仅生成 `0600` 文件（回执 `{"state": "rendered", "enabled": false}`），不会静默注册开机自启，也不会对失败启动做无限自动重试（`KeepAlive=false` / `Restart=no`）。每次修改配置、补配视频或账本任务/待处理作业状态变动后，`start_plan` 的摘要会更新：请先 `stop` 服务，重新 `vf-run inspect` 并重新 `render` 模板（Linux 随后执行 `systemctl --user daemon-reload`），再启动并核对 `status` 为 `active`。电脑休眠或当前系统用户退出登录会暂停用户级服务。

### 3. 工作台受限委托、员工短期接入与影子副本恢复

- **工作台受限委托与观测动作**：已授权连接支持同项目只读观测（`project_stats` 按每项最新版本统计成功率，`list_failures` 分页返回失败与未知提交任务的脱敏错误码，`list_reviews` 与受限媒体预览令牌）。管理员可用 `"$HOME/VideoFactory/environment/bin/vfctl" workbench-delegate issue|revoke` 为项目内工作台主体按角色（`repair`、`edit`、`review`、`cost`，默认 24 小时、最长 168 小时）签发或吊销项目级短期凭证；受限写动作严格校验前序版本、原表投递状态与角色围栏。
- **员工短期接入与隔离修复**：管理员可通过 `--employee-config` 绑定单名飞书员工短期接入（详见 [业务员工接入说明](BUSINESS_EMPLOYEE_ACCESS.md)），或为隔离副本启用固定类型源码修复预演（详见 [业务源码修复说明](BUSINESS_SOURCE_REPAIR.md)）。项目 API 权限不能防住同一操作系统账户下的任意 Shell 访问；正式协作须隔离 OS 账户或受限通道。
- **影子副本未决写入对账**：停机且确认调度未运行时，可使用 `"$HOME/VideoFactory/environment/bin/python" -m video_factory.shadow_copy_recovery --help` 查看影子副本未决写入的只读 `inspect` 与显式 `resolve` 命令（详见 [影子副本写入恢复规范](https://github.com/ChuluuMGL/video-factory/blob/main/docs/product/SHADOW_COPY_WRITE_RECOVERY.md)），工具绝不盲目重发远端写请求。
- **同核心版本成片离线搬迁**：停机备份恢复到全新目录时保留任务、成片摘要与原审核事件，仅按同摘要定位成片文件并复核权限与无符号链接；恢复后不自动启动调度、不恢复临时授权、不替代人工审核。

## 常见错误与安全恢复

| 错误码 | 处理方式 |
| --- | --- |
| `LOCAL_SETUP_CHANGED_RUN_SETUP` / `LOCAL_SETUP_CONFIGURATION_DRIFT` | 计划更新请先停机运行 `vf-setup`；已存配置漂移须核对账本与私有备份，禁止强改数据库 |
| `LOCAL_SETUP_TARGET_OR_ACCOUNT_IMMUTABLE` | 已绑定的飞书表、`app_id`、计费归属或视频区域不可原地替换；请用新 `project` 建独立项目 |
| `LOCAL_SETUP_RUNTIME_NOT_OWNED` / `LOCAL_FAKE_PROFILE_NO_EXTERNAL_IO` | 必须使用全新业务目录，不能采用其他部署或 `--mode fake-only` 假模型账本 |
| `SECRET_DECRYPT_FAILED` / `LOCAL_SETUP_MASTER_KEY_MISSING_RESTORE_BACKUP` | 立即停止，从私有备份恢复匹配的 `master.key`；切勿生成新主密钥覆盖 |
| `DIRECTORY_OWNER_ONLY_REQUIRED` | 为本人专用计划/业务目录设置 `0700`（文件 `0600`），勿递归改动共享资料目录 |
| `FEISHU_OAUTH_DENIED_OR_EXPIRED` | 保留原目录与计划重跑 `--bind`，本人重新完成一次飞书官方授权 |
| `FEISHU_MAPPED_TEXT_FIELD_MISSING_OR_CHANGED` | 核对四个映射字段 ID 及其文本类型，勿将状态单选列映射成脚本字段 |
| `LOCAL_RUNTIME_ALREADY_RUNNING` | 先用 Ctrl-C 或 `vfctl local-service stop` 正常停止该目录服务，再执行配置变更 |
| `LOCAL_RUN_START_PLAN_CHANGED` / `LOCAL_SERVICE_FAILED` | 重新执行 `vf-run inspect` 核对最新 `plan_sha256`，使用用户服务时同步重跑 `local-service render` |

## 阶段五：人工验收边界

云端 CI 已覆盖 macOS 与 Linux 的独立虚拟环境安装（含带空格路径与 `uv` 独立 Python 符号链接）、原子回滚、参考图校验托管、停机增量改配置、项目隔离、只读常驻启停、工作台角色围栏与影子副本未决写入恢复。

合成回归不替代真实业务验收。交付与自检时须分别记录五项真实证据：**环境安装、项目配置、飞书授权与原表事件、单次付费模型执行与解码、团队真人在飞书原表内的审核（及本机退出重登/重启恢复）**；未执行的项目如实记为“未执行”。
