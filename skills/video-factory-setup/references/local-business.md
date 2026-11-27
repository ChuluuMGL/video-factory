# 无 n8n 本机常驻路线（v0.3 一键安装与业务运维）

面向不会搭建 n8n、不租服务器的用户，在 macOS 或 Linux（Python 3.11+）本机直接安装独立 SQLite 加密账本与常驻业务引擎。执行前先核对当前源码或 wheel 包含 `scripts/install_local_kernel.py`、`vfctl local-setup`、`vfctl local-run` 与 `vfctl local-service`（a41 旧固定服务器归档不含此入口；不能只更新本 Skill 就宣称旧环境已获得该功能）。

全流程将**环境安装、项目配置、飞书身份、付费模型执行、人工验收**五者在操作与回执中严格分离。完整产品说明见 [本机无 n8n 常驻指南](https://github.com/ChuluuMGL/video-factory/blob/main/docs/product/LOCAL_BUSINESS_SETUP.md)。

## 1. 一键安装独立环境（不收密码、不调模型）

准备一个父目录已存在、无符号链接的**全新绝对路径**（已存在目录不会被覆盖或接管；假模型 `--mode fake-only` 目录也不可转为真实项目）：

```sh
# 从已核验的 v0.3 仓库目录直接安装
python3 scripts/install_local_kernel.py --mode business --root "$HOME/VideoFactory"

# 或使用已校验的本地核心 wheel（可选离线依赖目录）
python3 scripts/install_local_kernel.py --mode business --root "$HOME/VideoFactory" \
  --package /绝对路径/已校验的候选.whl --wheelhouse /绝对路径/wheels
```

- 安装器创建 `0700` 根目录、独立 `environment/` 虚拟环境，以及三个绑定 `<安装目录>/business` 的启动器：`vf-setup`、`vf-run`、`vf-agent`（另含 `<安装目录>/environment/bin/vfctl`）。
- macOS / Linux 通过符号链接引用基础 Python（解决 `uv` / 独立 Python 复制二进制后丢失 `libpython` 的问题），日常使用期间请保留该基础 Python。安装中断时原目录与 `install.json`（`incomplete_preserved`）保留供排查，重试请换全新目录。
- 成功回执 `installed=true`、`configuration_complete=false`、`production_ready=false`、`real_model_calls=0`。

## 2. 项目配置与参考图自动托管

1. **准备私有计划目录（`0700`）**：例如 `mkdir -m 700 "$HOME/vf-local-config"`。
2. **两种方式生成非秘密计划 `project.json`（`0600`，严禁写入密码或 API Key）**：
   - **终端交互问答**：本人在真实私有 TTY 运行 `"$HOME/VideoFactory/vf-setup" --plan "$HOME/vf-local-config/project.json" --interactive --bind`。
   - **Agent 辅助整理 + 离线预检**：Agent 按用户提供的商品资料与飞书表字段 ID 生成同一结构的 `0600` JSON（顶层字段固定为 `deployment`、`project`、`billing_owner`、`products`、`app_id`、`binding`、`script_model`、`video`），先运行 `"$HOME/VideoFactory/vf-setup" --plan "$HOME/vf-local-config/project.json" --check` 校验（`provider_requests=0`、`writes=0`），再交由本人在私有终端录入秘密。
3. **隐藏输入秘密**：管理员密码（至少 14 位）、飞书 App Secret、DeepSeek 脚本 Key、MiniMax 视频 Key 仅在真实私有 TTY 隐藏输入，或通过本人所有的 `0600` 独立文件传入（`--password-file`、`--app-secret-file`、`--script-key-file`、`--video-key-file`）。秘密经 `master.key`（`0600`）加密存入 `business/runtime.sqlite3`。当前本机内核脚本模型固定为 `deepseek-flash`，视频模型固定为 `MiniMax-H3`（`cn` 或 `global`，也可设 `video: null` / 输入 `skip` 先跑纯脚本）。
4. **参考图自动校验与私有托管**：配置视频时，参考图可直接从本机普通可读目录选择（无符号链接绝对路径，支持 `.png` / `.jpg` / `.jpeg` / `.webp`，单张 ≤30 MiB，单 SKU 1–9 张合计 ≤45 MiB，视频时长 4–15 秒）。向导核验文件格式、大小与 SHA-256 后，自动复制到 `<安装目录>/business/references/<project>/<sku>/` 私有素材区（目录 `0700`、文件 `0600`，文件名固定为 `<sha256>.<后缀>`），原图保留不动，无需手工建 `/work` 或修改共享目录权限。
5. 回执 `configuration_saved=true` 仅代表本地配置与加密凭据已保存，`paid_execution_enabled=false`、`provider_requests=0`。

## 3. 飞书身份授权与原表审核接线

1. **绑定核对（`--bind`）**：仅打开飞书官方设备授权页面（终端显示官方 `verification_uri` 与 `user_code`，不启动任何安装网页，Agent 不代收用户 token）。本人完成飞书授权后，程序只读核对租户与四个映射文本字段（`task`、`sku_id`、`script`、`source_revision`），返回 `feishu_binding_verified=true`。
2. **原表审核启用（先看计划再确认）**：
   ```sh
   "$HOME/VideoFactory/vf-setup" --plan "$HOME/vf-local-config/project.json" --native-plan
   "$HOME/VideoFactory/vf-setup" --plan "$HOME/vf-local-config/project.json" \
     --enable-native <刚核对的计划摘要> --dedicated-app
   ```
   先核对表、字段与订阅范围并确认该专用飞书应用无其他长连接接收器，再启用（返回 `native_review_enabled=true`）。此步骤仍不发放付费执行权，也不启动后台进程。

## 4. 增量改配置、补配视频与新增项目

- **停机增量更新配置**：先正常停止该业务目录正在运行的服务（否则报 `LOCAL_RUNTIME_ALREADY_RUNNING`），编辑私有 `project.json` 后直接重跑 `"$HOME/VideoFactory/vf-setup" --plan "$HOME/vf-local-config/project.json"`：
  - **可更新项**：`products`（增改 SKU、商品名称、`truth_source` 卖点规格）、视频参考图与时长规格（自动重新核验并托管到私有素材区）、`binding` 内的 `submitters` 与 `reviewers` 成员列表。
  - **固定不可变项**：`deployment`、`project`、`app_id`、`billing_owner`、已绑定的飞书目标表（`tenant_key` / `base_token` / `table_id`）及已开启视频项目的 `region`；更换这些目标须新建独立项目。
  - **安全围栏**：已存加密凭据自动复用（无需重输 Key），历史任务与审核记录完整保留，审计表记录变更前后 `plan_sha256`；同时自动吊销旧的员工短期授权并使尚未提交给供应商的待执行批准失效（`updated=true`），已提交或未知回执保持原状。
- **脚本跑通后补配视频（`--previous-plan`）**：首次视频为 `null`（`skip`）的项目，先停服务，保留原计划不变并复制为 `project-with-video.json`（仅填写 `video` 配置），执行：
  ```sh
  "$HOME/VideoFactory/vf-setup" \
    --previous-plan "$HOME/vf-local-config/project.json" \
    --plan "$HOME/vf-local-config/project-with-video.json"
  ```
  验证管理员并隐藏输入 MiniMax 视频 Key，保留既有任务、已审脚本与飞书绑定（`video_added=true`）。后续 `vf-run` 改用新计划文件。
- **中断续接与同底座新项目**：中断或飞书授权超时后沿原目录与原计划重跑即可复用已存凭据；同一客户新增项目使用同一安装目录、不同 `project` 标识与新计划文件，沿用管理员密码，项目间凭据与账本隔离。配置漂移或 `master.key` 缺失时立即停止并从私有备份恢复，严禁删库重建。

## 5. 前台常驻、Codex 接入与逐单付费审批

1. **默认只读常驻**：
   ```sh
   "$HOME/VideoFactory/vf-run" inspect --plan "$HOME/vf-local-config/project.json"
   "$HOME/VideoFactory/vf-run" start --plan "$HOME/vf-local-config/project.json" --expect-plan <返回的plan_sha256>
   ```
   默认只读监听 `127.0.0.1:8788`（可用 `--port` 调整、用 `--max-runtime <秒>` 限时），Ctrl-C 正常停机即撤销本次会话凭据。
2. **外部 Codex / Agent 受限调用**：只使用启动输出中的 `connection.json` 路径：
   ```sh
   "$HOME/VideoFactory/vf-agent" --connection /实际返回路径/connection.json capabilities
   "$HOME/VideoFactory/vf-agent" --connection /实际返回路径/connection.json status
   "$HOME/VideoFactory/vf-agent" --connection /实际返回路径/connection.json project_stats
   "$HOME/VideoFactory/vf-agent" --connection /实际返回路径/connection.json list_failures
   ```
   请勿读取或输出相邻 `.token` 文件，Agent CLI 不含管理员审批、任意 URL 或 Shell 入口。
3. **允许准备计划（`--allow-plans`）**：负责人在 `inspect` 与 `start` 同时加 `--allow-plans`，启动时验证提交人本人飞书身份；Agent 可调用 `prepare-script`（`record`/`request_id`/`expected_revision`）、`submit-script`（`plan_id`/`plan_sha256`）、`prepare-video`、`submit-video`，未获负责人批准前不会调用供应商。
4. **允许执行已批准任务（`--execute-approved`）与逐单计费确认**：须已完成原表接线，并在 `inspect` 与 `start` 同时加 `--execute-approved` 及明确运行时长上限（`--max-runtime 1..604800` 秒或 `--hours 1..168`），`start` 另加 `--dedicated-app`。每笔脚本或视频费用仍须负责人在私有终端独立确认：
   ```sh
   "$HOME/VideoFactory/vf-run" approve-script --plan "$HOME/vf-local-config/project.json" \
     --plan-id <计划ID> --plan-sha256 <计划摘要> --accept-provider-charges
   "$HOME/VideoFactory/vf-run" approve-video --plan "$HOME/vf-local-config/project.json" \
     --plan-id <计划ID> --plan-sha256 <计划摘要> --accept-provider-charges
   ```
   撤销未提交计划用 `revoke-script` / `revoke-video`；脚本审核通过绝不自动授权视频费用。管理员还可用 `<安装目录>/environment/bin/vfctl workbench-delegate issue|revoke` 为项目工作台主体按角色（`repair`、`edit`、`review`、`cost`）签发或吊销短期受限凭证。

## 6. launchd / systemd 用户级后台常驻启停

需要关闭终端后保持默认只读常驻时，先用相同计划与可选 `--max-runtime` / `--port` 执行 `vf-run inspect` 取得 `plan_sha256`，再通过安装环境内的 `vfctl local-service` 显式渲染与控制（只生成 `0600` 文件，回执 `enabled=false`，不静默开启开机自启，进程异常退出不自动无限重试）：

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

- `--plan` 与 `--password-file` 均须为本人所有、权限 `0600` 的无符号链接绝对路径。
- 每次增量修改配置、补配视频或账本任务/待处理作业变化后，`start_plan` 的 `plan_sha256` 会改变：请先 `stop` 原服务，重新运行 `vf-run inspect` 取得新摘要，删除或改新路径重新 `render`（Linux 需再执行 `systemctl --user daemon-reload`），再 `start` 并用 `status` 核对 `state=active`。
- 电脑休眠或退出当前系统用户会影响用户级服务；项目 API 权限不替代同一 OS 账户的 Shell 隔离。服务 `active` 不等于业务验收完成，员工表内审核与退出重登仍须真人独立验收。

## 7. 业务员工短期接入与隔离源码修复

- 管理员可显式使用 `--employee-config` 为同一业务账本绑定一名飞书员工（最长两小时、默认只读），员工经本人飞书设备授权获取短期项目凭证，不共享管理员目录；管理员通过 `vf-run employee-status|employee-suspend|employee-resume` 查看与吊销，重启后须重新配对，费用仍由管理员逐单批准。详见随包 `docs/product/BUSINESS_EMPLOYEE_ACCESS.md`。
- 可另选 `repair: {profile: "single-select-decoder-v1", allow_apply: true}`，仅在隔离实例中调用员工 API 的 `repair/inspect`、`propose`、`validate`、`apply`、`rollback` 验证固定类型修复，禁止传入代码、路径或 Shell，不修改生产工作流，详见随包 `docs/product/BUSINESS_SOURCE_REPAIR.md`。
- 停机状态下若影子副本存在未决写入（如飞书 `1254002`），先确认调度已停止，再使用 `<安装目录>/environment/bin/python -m video_factory.shadow_copy_recovery --help` 只读 `inspect` 对账，不盲目重发远端写入。
