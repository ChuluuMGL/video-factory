# PR #86：独立安装最短验收单（未发布候选）

给没有参与开发的人使用；本单准备完毕不等于真人验收通过。候选固定为 `49e7d374af7b95dd240adafdafb0e7670172ce3d`，不是 main、a41 或可随时变化的分支。完整指纹见 [候选清单](acceptance/PR86_CANDIDATE.json)。先由实施人员核对 PR #86 的新候选云端结果；旧 `e8e8649` 的通过记录不覆盖首次 welcome 安装修复。

本清单、候选指纹和审查记录由实施人员单独附给操作者；它们是后续文档，不在上述固定运行提交中。运行提交自带原 INSTALL_RESUME_ACCEPTANCE.md。不要为了取得新版文档切换运行代码；本单的固定指纹也须由实施人员通过可信渠道核对。

## 操作者先准备

一台允许安装测试软件的非生产电脑；Git 和 Python 3.12；若选 Docker，准备已启动的 Docker Desktop/Engine。Node.js 路线会检查并按需准备 Node.js 24。不要使用作者原有绑定目录、凭据或浏览器会话。已有 n8n 只使用专用隔离实例，并记录安装前工作流列表。没有 n8n 不要求租服务器。

真实飞书检查另需操作者自己的测试应用与空测试 Base/任务表，以及该应用的 Base 访问权限。App ID、Base token、table ID 可由自己的 Agent 在授权范围内核对；Secret 与 n8n API Key 只在本机隐藏终端输入。不具备这些前提就停在对应阶段，不能填写“安装全通过”。本轮不需要模型 Key、SSH 权限或 GitHub Secrets。

## 复制给自己的 Agent

> 请按 Video Factory PR86-install-resume-r1 做零模型独立安装验收。只使用下面固定提交的完整仓库与 Skill，先核对提交和候选指纹；读取本单及同目录 INSTALL_RESUME_ACCEPTANCE.md。检查我的系统、Python 和已有 n8n，给出对应路线。使用新的空私有目录，不接管任何生产实例。每条命令和网页步骤计数，隐藏输入只计次数不保存内容。不能代我设置密码、同意平台授权或确认费用。到“为这张表补齐审核字段？”时先输入 :quit 保存退出；重新开启一个 Agent 对话，用原目录做 handoff-check，再 welcome --resume。字段补齐只对我的专用空测试表执行；所有模型配置和自启本轮选否。最后按下方原任务回读步骤验收，返回证据表。遇到未知资源、来源变更、权限或身份不匹配停止，不重装、不清回执、不提交模型。

## 操作顺序

1. **固定来源。** Agent 在新目录克隆并切到确切提交；检查 `git rev-parse HEAD` 和 Skill tree。这里下载源码，尚未开始安装。

   ```sh
   git clone https://github.com/ChuluuMGL/video-factory.git vf-pr86-r1
   cd vf-pr86-r1
   git checkout --detach 49e7d374af7b95dd240adafdafb0e7670172ce3d
   git rev-parse HEAD
   git rev-parse HEAD:skills/video-factory-setup
   ```

   Skill tree 必须为 `5860a2b80574822ac8680261bac666c860d473b2`。可信来源与确切提交仍需核对，摘要本身不是发布者签名。源码候选没有独立发行包；不要从最新 main 拼接脚本。

2. **首次配置并有意退出。** 从仓库目录运行以下命令。Mac/Linux 将占位路径换为新的绝对目录并使用 `python3`；Windows 使用 `py -3.12` 和实际 Windows 路径。全程使用同一个目录。

   ```sh
   python3 skills/video-factory-setup/scripts/portable_n8n.py --root "/absolute/private/vf-pr86-r1" welcome
   ```

   已有隔离实例选 1；新装 Node.js 选 2；Docker 选 3。Cloud 本单不验收。新 n8n 首次账户与 API Key 在它自己的编辑器设置。完成飞书读取后，在补字段问题处输入 `:quit`；预期保存到 `schema` 阶段，已有服务和凭据保留。若从未到达该提示，记录实际失败点，不假装完成中断测试。

3. **换一个 Agent 对话续接。** 只提供固定源码目录、原私有安装目录及本单；不提供旧对话、密码或 Key。

   ```sh
   python3 skills/video-factory-setup/scripts/portable_n8n.py --root "/absolute/private/vf-pr86-r1" handoff-check
   python3 skills/video-factory-setup/scripts/portable_n8n.py --root "/absolute/private/vf-pr86-r1" welcome --resume
   ```

   确认来源一致、阶段匹配、未知创建回执为空；若非空先停止核对。预期不重问路线/地址/项目，只重输本轮 n8n API Key，飞书 Secret 复用。允许补齐专用测试表字段；脚本、视频、图片配置及自启均选否。核对交接记录保存、诊断工作流关闭。没有模型流程不算失败，这是本单的零模型范围。

4. **回读同一条零模型任务。** 操作者在专用表准备一行：文本字段「任务编号」=`INSTALL_READBACK_1`，数字字段「审核目标版本」=`1`，单选「状态」=`脚本待审核`，文本「脚本」=`仅用于安装回读，不生成内容`。当前自动 schema 不创建任务编号/审核目标版本，缺少时只在本测试表手动增加。不要修改既有生产表。由自己的 Agent 通过授权的 Base 链接/API 确认这行实际 record ID，不能用任务号代替。

   ```sh
   python3 skills/video-factory-setup/scripts/portable_n8n.py --root "/absolute/private/vf-pr86-r1" task-check --project test_project --record recActual --task INSTALL_READBACK_1 --revision 1
   ```

   `test_project` 换成步骤 2 的真实项目 ID，`recActual` 换成该行实际 record ID。一次只运行一个诊断命令。它会创建并短暂启用受鉴权的 n8n 诊断工作流，随后关闭；对飞书只读，无模型请求。确认返回的是同一项目/Base/table/record/task/revision，状态与脚本存在性一致，原行未写入。结果未知时只查原执行，不重复提交诊断或生成。

5. **填写证据并停止。** 用下表记录，无需自动上传。保留私有配置与回执，不删除 n8n；后续是否停测试服务由操作者决定。真实退出/登录自启、第二账号权限、付费内容和人员审核另行安排。

## 证据表（逐项填写，不预填通过）

| 项目 | 实际值/结果 | 脱敏证据或失败点 |
|---|---|---|
| 操作者是否参与开发 | 未填写 | 作者自测填“是”，不得算独立安装 |
| 日期、系统、架构 | 未填写 | 不上传设备序列号 |
| source commit / Skill tree / scripts SHA256 | 未填写 | 与候选清单逐项对比 |
| 路线、Python/n8n/Node/Docker 实际版本 | 未填写 | 只写使用的组件 |
| 私有根目录 | 本地保存 | 对外用别名，不上传完整私有交接 |
| 账户准备 / 下载安装 / 人工操作耗时 | 未测 | 分开计时 |
| 手工命令数 / 网页步骤数 / 隐藏输入次数 | 未测 | 不记录答案、密码、Key |
| 新装 welcome 或已有实例保留检查 | 未执行 | 记录实际路线；不混用结论 |
| 退出阶段 / handoff-check / resume | 未执行 | 是否重问身份、是否复用飞书凭据 |
| 原实例工作流与数据保留 | 未执行 | 对比安装前后；诊断流程单列 |
| 同一任务定位与版本 / 状态 / 结果存在性 | 未执行 | 用本地回执保存准确 ID；公开报告用别名 |
| 诊断流程关闭 / 任务写入数 / 模型调用数 | 未执行 | 预期关闭 / 0 / 0，必须回读 |
| 需要作者协助的次数与原因 | 未填写 | 不能隐去卡点 |
| 第二账号 / 桌面退出登录 / 付费生成 | 未执行 | 本单不覆盖 |
| 结论 | 未验收 | 通过范围、失败、未执行分别写明 |

本单不承诺三条命令或十分钟完成；它测量真实门槛。完成零模型回读也不表示真实视频、人员审核或生产交付通过。
