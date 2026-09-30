# Setup P1 使用与验收边界

版本：0.1.0a8（Setup 契约保持兼容）。实现了可恢复的**配置与计划阶段**，不是远程安装器。以下命令用于已经装好 CLI 的管理环境；客户一条命令安装整个服务仍属于 P2/P6。

## 终端

在属于自己的现有私有目录保存会话。会话、锁文件权限为 0600；父目录不能由其他用户写入。实现面向 POSIX（Linux/macOS）；本轮只在云端 Linux 验证，未跑本地 macOS 测试，Windows 原生终端尚未支持。

```sh
vfctl setup --session /absolute/private/customer.setup.json --interactive
```

流程收集组织、管理员飞书身份引用、部署主机/SSH 身份引用、飞书租户与凭据引用、项目/SKU/审核人员和模型规划。选择题支持数字或选项值；回车采用显示的默认值。输入 `:back` 返回上一题、`:quit` 保存退出，Ctrl+C 或输入结束也保留此前已保存步骤。重新运行同一路径继续；不会从头清空配置。

SKU 输入一个绝对路径 JSON 文件，其内容为数组，每项含 sku_id、name、variant、truth_source，最多 100 项。文件在选择时读入并保存为本次配置快照，之后修改源文件不会暗中改变计划；需要重新提交该字段才更新。

凭据题隐藏输入，但**只允许引用**：`env:FEISHU_CREDENTIAL` 或 `secret:customer_ssh`。这里不读取环境变量、不连接秘密存储、不设置管理员密码。误贴可识别的 Key 会被拒绝；绝不要在名称/说明字段输入密码。管理员/审核人填写 `feishu:用户ID`，不会被当作已验证的身份。

## Agent / 无终端

```sh
vfctl setup --session /absolute/private/customer.setup.json --json
```

返回 session_id、revision、status、next_question、remaining_questions。next_question.input_schema 描述答案类型与选项；SKU 的 type=array，Agent 直接提交数组，只有终端交互层读取文件路径。需要凭据引用时状态是 needs_secret，且 secret_input.mode=reference_only；不得要求用户把原始 Key 发到聊天或答案 JSON 中。

答案是字段到值的 JSON 对象，例如：

```json
{"organization.id":"example_org","organization.name":"Example customer"}
```

提交答案文件或通过 stdin 输入，使用刚收到的 revision：

```sh
vfctl setup --session /absolute/private/customer.setup.json --json \
  --answers /absolute/answers.json --expect-revision 0
```

revision 不匹配返回 SETUP_REVISION_CONFLICT；重新读取状态再处理，不用旧答案盲目覆盖。并发锁冲突返回 SETUP_SESSION_BUSY。缺少 TTY 时自动返回 JSON；显式要求 --interactive 但没有 TTY 会返回 SETUP_TTY_REQUIRED_USE_JSON，不会挂起等待。

填完所有当前问题后返回 plan_ready，包含配置和目标摘要、计划哈希、组件及 11 个待发现资源意图。所有实际验证仍为 not_run，execute_allowed=false，paid_requests_allowed=false。计划变更会改变哈希；更换组织、服务器、租户或 Base 模式还会清理相关旧绑定。

## 新增项目草稿

```sh
vfctl setup --session /absolute/private/second.setup.json --json \
  --from-session /absolute/private/customer.setup.json
```

源会话必须已填齐组织和部署信息。只复制该两组配置；新项目从空配置开始，使用独立会话 ID 和 revision 0。源文件不变，任何实时验收状态都不会被继承。目标会话已存在时拒绝再次导入源文件。

## 数据与错误

当前会话 schema=1。保存采用文件锁、预期版本检查、临时文件写入/fsync/原子替换；在替换前失败仍保留旧检查点。拒绝符号链接、硬链接、非普通文件和宽松会话权限。会话文件是客户侧配置草稿，不是身份凭证；同一 OS 用户的恶意程序不在此文件锁隔离范围内。

未知字段、未知选项、重复 SKU、重复 JSON key、过大输入、不可识别 schema 及可识别秘密模式均拒绝，错误不回显输入。没有实现历史 schema 迁移，不能声称可升级旧会话。

JSON 模式正常获取问题/计划退出码 0，非法输入或存储冲突退出码 2，交互中断退出码 130。0 不代表已安装，只表示本次规划操作成功。

## Setup 与实际执行的连接状态

实际服务器预检、原生 SSH 安装、管理员/秘密存储、容器启动、迁移、升级及备份恢复已提供独立命令，见 [完整环境手册](STACK_USAGE.md) 和 [原生运行底座](RUNTIME_ALPHA.md)。a11 新增独立的 [setup-deploy 执行入口](SETUP_DEPLOY_USAGE.md)，将本会话连接安装和项目/SKU 持久化；飞书建表和通用模型接入仍未完成。离线 setup 的 plan_ready 仍只代表计划，真实状态使用 setup-deploy status 回读。

## 已验证范围

[云端运行 36437397635](https://github.com/ChuluuMGL/TikTok-Video-Factory/actions/runs/36437397635)通过 91 项回归，其中 26 项为本次 Setup 测试，并完成真实 PTY 和已安装 wheel 的跨进程检查。没有使用真实客户凭据或执行远程安装；通过结果限于本页描述的规划能力。

运行底座安装与管理命令另见 [RUNTIME_ALPHA.md](RUNTIME_ALPHA.md)。Setup 的 plan_ready 仍只代表规划完成；它不会自动把运行底座安装、飞书连接或模型路线标为已验收。
