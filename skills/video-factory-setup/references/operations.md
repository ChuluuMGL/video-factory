# 操作流程

以下命令中的 `/opt/vf-cli-VERSION`、客户主机、session 与 stack 必须替换成经确认的实际对象；不要直接执行示例占位符。版本、摘要从可信交付记录取得。已有授权覆盖的可逆工作继续执行；涉及新目标、业务写入、费用或停机且未获授权时，先准备可审阅计划。

## 1. 安装 CLI

在可信渠道拿到 `.tar.gz`、`SHA256SUMS`、`release.json`。在客户服务器私有目录先核对归档 SHA256，再解压。目录及其上级必须 root 拥有、不可被其他用户写入，不能经符号链接访问。查看发行包 `INSTALL.md`，使用发行包里的安装器：

```sh
python3.12 /root/verified-release/install.py --prefix /opt/vf-cli-VERSION --manifest-sha256 TRUSTED_MANIFEST_SHA256
```

安装器不访问网络，不询问业务密码，不覆盖全局 Python，不注册开机服务。成功回执含 `cli`、`skill`、`version`、`source_commit`，以及示例下一命令。重复安装必须是同一清单；有不同版本或损坏/不完整目录则拒绝。不要为绕过拒绝而删除客户目录。

Skill 本身由客户在其 Agent 支持的自定义 Skill 目录中安装：复制完整 `video-factory-setup` 文件夹，保留 `SKILL.md` 与 `references/`。先检查目标是否已有同名 Skill，按客户选择安装/更新；发行包安装器不会修改本机 Agent 设置。无 Skill 的操作员也能直接运行 CLI。

## 2. 启动欢迎向导

先检查 Python、Docker、Compose、CPU、磁盘、实际连接主机。当前经过云端验证的是 Linux x86_64 / Python 3.12；Docker 本地 Unix socket、Compose 至少 2.24，服务至少 4 GiB 内存与可用磁盘。默认在线镜像首次获取需要网络；网络受限时使用 a22 的独立离线镜像包。离线 CLI 包本身不含服务镜像。

session 父目录由客户创建为 0700，位于 stack 和 CLI 前缀之外。人工在客户私有终端运行：

```sh
/opt/vf-cli-VERSION/venv/bin/vfctl setup-run --session /root/vf-private/customer.setup.json --root /opt/video-factory --wheelhouse /opt/vf-cli-VERSION/release/wheels
```

若提供离线镜像包，先读发行包 `docs/product/OFFLINE_IMAGES.md`，取得可信交付记录中的清单摘要，在上述命令增加 `--image-bundle /root/vf-image-bundle --image-manifest-sha256 TRUSTED_IMAGE_MANIFEST_SHA256`。包目录 700、文件 600、归 root 所有；必须与当前发行 wheels 匹配。不要自动采信包内自报摘要、切换未知镜像源或将大镜像包下载到安装人员电脑。

按欢迎问题填写组织、主机、项目、SKU、审核人及模型意图。先把已安装前缀下 `release/templates/products.json` 复制到客户私有目录并填入实际商品，勿修改校验包；SKU 终端输入使用副本的 JSON 文件绝对路径。确认安装摘要后，密码与 App Secret 隐藏输入，App Secret 在客户 vault 中加密保存。a25 默认新建 Base，也可绑定已有表。新建模式选择用途（测试会放入两条测试任务）、位置及提交人，授权并核对清单后才创建和绑定；无需预建测试 Base。创建回执未知时停止并读取状态，不盲目重建。

管理员默认在私有终端完成 Setup，飞书本人授权使用飞书官方地址，回终端确认 Base 计划；无需 Video Factory 网页或端口隧道。旧版管理员接入/员工审核窗口分别默认 8791/8790，仅供兼容旧安装时限时使用，不得改成公网裸露端口。

Setup 后，在新建的测试 Base 中核对实际任务记录 ID。下面的私有终端操作只验证导入和本地审核账本，不是员工的日常审核方式；产品验收必须另见原任务表中的脚本/视频回写与员工表内决定：

```sh
/opt/vf-cli-VERSION/venv/bin/vfctl stack-feishu-session import --stack-root /opt/video-factory --project PROJECT_ID --record RECORD_ID --expected-revision 0
/opt/vf-cli-VERSION/venv/bin/vfctl stack-feishu-session review --stack-root /opt/video-factory --project PROJECT_ID --task TASK_ID --revision 1 --stage script --decision reject --feedback '具体修改意见' --event UNIQUE_EVENT_ID
```

每条命令独立进行飞书本人授权并在终端核对计划后输入 `yes`。操作结果只证明指定记录或审核事件已入账；不触发模型费用，也不表示员工已在飞书内完成全流程。

当固定发行包确实包含 `feishu-review` 且飞书应用已配置记录变更事件与长连接时，先按随包 `docs/product/FEISHU_NATIVE_REVIEW.md` 核对计划并启动该项目私网 runner，再由管理员在私有终端启用原任务表审核：

```sh
/opt/vf-cli-VERSION/venv/bin/vfctl feishu-review enable --stack-root /opt/video-factory --session /root/vf-private/customer.setup.json
```

这一步会补齐原任务表字段并订阅 Base 事件。runner 必须已是 `running`；恢复或升级后要对新 stack 根目录重新应用 runner 计划。仍需另行回读真实脚本、审核操作者和视频附件。不能用上面的终端 `review` 命令代替员工在飞书表内的通过或退回。

## 3. Agent 规划、续填和同客户新项目

```sh
/opt/vf-cli-VERSION/venv/bin/vfctl setup --session /root/vf-private/customer.setup.json --json
```

读取当前 `revision` 和 `next_question.input_schema`。使用 `--answers` 与 `--expect-revision` 提交严格类型的非秘密答案；引用只能是 `env:NAME` 或 `secret:alias`。更新前重读版本。普通 `setup` 只生成计划。

使用原命令恢复人工向导。第二项目使用新的 session，并增加 `--from-session /root/vf-private/customer.setup.json`，指定同一 stack/root 和当前发行 wheels。项目 ID 和 Base 必须重新核对，不能复制别人的业务来源。

## 4. 检查与修复

```sh
/opt/vf-cli-VERSION/venv/bin/vfctl --version
/opt/vf-cli-VERSION/venv/bin/vfctl stack status --root /opt/video-factory
```

先确定 CLI、安装输入、Docker、数据库、项目计划、OAuth、字段权限或任务状态中的具体故障。只读状态不包含业务完成证明。不要把临时故障处理成“重新提交模型”。退出码 130 是中断；凭据已保存、连接待确认应使用原 session 继续。

半安装 CLI 选择新的空版本目录，不影响外部 stack/session。正常结束会清理短时窗口；SIGKILL、断电、Docker 故障后检查该 stack 的临时 worker、出站中继与 `data/worker/.setup-run-*`，只有确认本次窗口已停后，才按已授权范围清理确切残留。

## 5. 备份、恢复和升级

发行包 `docs/product/STACK_USAGE.md` 是完整操作手册，执行前同时读当前 CLI `stack --help`，核对源 stack、归档、独立备份密钥、新目录、端口、发行清单和停机范围。这里只给流程，避免在信息不全时套用破坏性命令。

- 冷备会停止全部 stack 服务，成功不会自动重启。按已授权维护计划创建加密归档，校验文件并保留异机副本；完成后明确报告服务是否启动。
- 恢复到新的空目录；主密钥、数据库、n8n 和媒体必须一并恢复。恢复后旧产品会话/队列凭据撤销，飞书绑定需要重核。归档存在不证明恢复成功，需实际启动/回读和恢复演练。
- 升级先将新 CLI 安装到新版本前缀。再通过当前支持的 `stack upgrade`、新 stack 目录和冷备检查点进行服务迁移。仅安装新 CLI 不会升级运行中的服务；不得直接覆盖旧 wheel、旧数据库或旧部署。
- 离线部署须将镜像包与业务备份分开留存。新机恢复先 `stack fetch` 加原镜像包的两个参数，再 `stack build` / `stack up`；离线升级必须提供候选版本的镜像包，缺包应在停原服务前拒绝。
- 如候选版只更换产品 wheel、其他依赖和固定镜像不变，可按随包 `docs/product/OFFLINE_IMAGES.md` 使用 `stack export-images --source-root <原离线栈>`，在同一 Linux 主机从已验证镜像制作候选包。先核对新清单摘要，再按正常升级流程冷备和克隆；导出本身不升级旧服务。
- 出现 `rolled_back` / `needs_attention` 按失败或待处理记录；核对哪个目录和端口实际运行。客户发布、付费调用与真人验收单独记录。
