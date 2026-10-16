# 本机安装与复用 n8n

本路线把「n8n 在哪里运行」与「Video Factory 的工作流」分开。Mac、Windows、Docker 和已有服务器使用同一个飞书与模型接入模块，不要求购买 Ubuntu 服务器。n8n Cloud 使用相同的公共 API；本轮按维护者决定跳过真实 Cloud 账号验收。

**当前是 PR #49 的开发候选，尚未发布为稳定安装包。** 原生安装、API 导入和合成业务测试分别记录；禁止把导入成功说成真实模型或完整生产验收通过。收费工作流仅供人工逐条验收；新增 n8n 自带数据表中的项目执行锁与结果保存机制，并发与恢复已通过合成测试；真实审核身份及费用授权验收通过后才能开启自动生产。

## Agent 怎么引导

1. 问是否已有 n8n。已有就确认地址和公共 API，不再安装第二套；没有才选择本机 Node.js、本机 Docker 或服务器。
2. 确认现有飞书应用和测试 Base。缺少字段由专用字段工作流补齐，保留原有列和单选选项；同名异类型会停止，不改旧字段类型。
3. 通过安装人员的私有终端隐藏输入 n8n API Key 和飞书 App Secret。模型 Key 仅在安装对应模型工作流时输入。不要把秘密交到聊天、配置示例、命令参数或 Git。
4. 导入关闭状态的工作流，核对导入结果，先执行不调用模型的连通检查和字段准备。
5. 再逐条验证脚本、飞书审核、视频提交与查询。真人审核始终在飞书表格完成。`脚本通过` 不会自动授权视频生成。

## 没有 n8n：本机 Node.js

需要 Python 3.11 或以上。安装器复用可用的 Node.js 24；没有合适版本时会下载校验固定版本到专用目录，不更改全局 Node.js。原生模块回退编译时，Mac 需要 Xcode Command Line Tools，Windows 需要 Visual Studio C++ Build Tools，Linux 需要 C/C++ 编译工具。安装器会在专用目录准备 Python 构建依赖，不修改系统 Python，不全局安装 npm 包。

首次可以直接运行 `python3 portable_n8n.py welcome`，按问答复用已有实例或新装 n8n。每次向导只输入一次 n8n API Key；飞书与模型凭据按项目保存在 n8n 的加密凭据库，续接时直接复用。它执行零模型连通检查和字段准备后，继续询问是否配置脚本与视频流程，一并导入关闭状态的生成、查询和失败恢复工作流。配置过程不会调用模型。下面的分步命令供 Agent 续接和排障使用。

在完整 Skill 的 `scripts` 目录运行（Mac/Linux 用 `python3`；Windows 用 `py -3`）：

```sh
python3 portable_n8n.py --root /YOUR/PRIVATE/vf-n8n install --mode node
python3 portable_n8n.py --root /YOUR/PRIVATE/vf-n8n start
```

安装器默认给 npm 20 分钟，每 30 秒报告进度。若超时，会停止本次进程并保留目录、计划和缓存；检查磁盘空间与网络后用原目录、模式、端口续接。慢盘可在子命令前指定 `--dependency-timeout 2400`，最多 7200 秒。不要通过删除安装目录或业务数据来重试。优先选择本机磁盘，避免把依赖安装在网络盘或云同步目录。

Windows 示例目录可以是 `C:\Users\YOUR_NAME\VideoFactory\n8n`。路径含空格时加引号。安装目录必须为空，或者包含这次安装留下的原计划；不允许覆盖其他 Node 项目。端口占用时先确认是否为已有 n8n，可以复用，或者通过 `--port` 明确选择其他端口。

首次创建 n8n 账号和 API Key 在 **n8n 自己的编辑器**完成。这不是 Video Factory 员工网页，也不是另建产品安装网站。已有实例不需要重复建账号。

```sh
python3 portable_n8n.py --root /YOUR/PRIVATE/vf-n8n stop
python3 portable_n8n.py --root /YOUR/PRIVATE/vf-n8n start
```

停止不删除 n8n 数据。原生后台进程仅在进程身份与安装回执一致时终止；不会按端口随意杀进程。此启动方式是当前登录用户的本机进程，电脑休眠或关机会暂停任务；开机自动启动和长期运维应单独配置。

## 没有 n8n：Docker

先准备可用的 Docker Desktop（Mac/Windows）或 Docker Engine + Compose（Linux）。安装器创建独立 Compose 项目和数据卷，只绑定本机端口；不操作现有容器和数据卷。

```sh
python3 portable_n8n.py --root /YOUR/PRIVATE/vf-n8n install --mode docker
python3 portable_n8n.py --root /YOUR/PRIVATE/vf-n8n start
```

固定镜像使用摘要校验。`stop` 仅停止本项目，不执行 `down -v`、`prune` 或删除数据。Docker Desktop 本身的安装、许可和开机启动仍按所在系统准备。

## 已有 n8n：本机、服务器或 Cloud

直接接入，无需运行上面的 `install`。远端实例用 HTTPS；本机允许 `http://127.0.0.1:5678`。已有服务器也可以用可信 SSH 隧道连本机端口。当前新增实例固定测试 n8n 2.40.7；已有实例还需检查节点与公共 API 能力，执行锁使用 Data Tables API 及相应权限。缺少能力时停止并说明，不会自动升级或覆盖原实例。Cloud 需要套餐开放公共 API；无 API 时可导出 JSON，由用户在编辑器导入并手动绑定凭据。

```sh
python3 portable_n8n.py check --url https://YOUR_N8N_HOST
python3 portable_n8n.py --root /YOUR/PRIVATE/vf-bindings connect \
  --url https://YOUR_N8N_HOST --project YOUR_PROJECT \
  --base YOUR_BASE_TOKEN --table YOUR_TABLE_ID --flow probe
```

推荐先用 `welcome` 连续向导完成接入和模型流程配置。项目中断后用同一目录、地址和项目标识续接，已创建的凭据和工作流会回读复用。需要更换 Key 时，在 n8n Credentials 编辑该项目对应的凭据，不必重建工作流；不能删除本机回执以绕过未知状态。

`connect` 是独立排障入口，使用 n8n 公共 API 创建专用凭据与工作流，保存本机回执。再次执行相同参数会核对原资源，不重复导入。工作流保持关闭，API 返回后还会读取节点、连接和设置进行核对。远端凭据由 n8n 加密保管，本机回执不包含 Key 或 App Secret。

先在 n8n 执行 `probe`。确认 Base 权限后，用同样命令把 `--flow probe` 换成 `--flow schema`，审阅并执行字段补齐。最后才进入人工测试工作流：

| flow | 明确操作 | 结果 |
|---|---|---|
| `script` | 指定负责人把一行改为「请求脚本」，安装人员运行一次工作流 | 取一条任务、写入处理中、调用一次 DeepSeek、脚本回写为「脚本待审核」 |
| `video-submit` | 负责人明确确认视频费用后，把一行改为「视频生成已确认」 | 提交一次 MiniMax H3，保存任务 ID；不根据「脚本通过」自动收费 |
| `video-poll` | 查询已有视频任务 | 查询原任务并回写视频链接，不重新生成 |
| `recover` | 飞书写回失败后恢复 | 读取 n8n 中已保存的生成结果，核对原任务，再补写；不调用模型 |

脚本和视频提交共用该项目在 n8n 自带数据表中的执行锁；不要求额外数据库或常驻产品网页。模型结果在飞书写回前保存，结果不明时不会自动释放锁并重新收费。

分步 `connect` 的前三个模型工作流需要显式 `--experimental-manual` 才能通过 CLI 导入。API Key 会在该终端隐藏输入。人工检查和逐条调用是当前候选限制，**尚未交付从飞书自动执行的完整便携生产流程**；不能把这部分限制移交给客户后声称产品已安装完成。

脚本修改时保留旧脚本与意见，再明确申请下一次生成；退回不等于再次付费授权。视频中国区接口与主产品一致使用 `https://api.minimax.cn`。参考图目前需 1–9 个模型服务可访问的 HTTPS 链接，填写在「参考图链接」，以空白分隔；不能假设飞书私有附件链接可直接被模型读取。输出视频链接也不等于永久归档。

## 失败与恢复

- 导入 POST 的结果不明确：回执保留 `pending`，阻止盲目重试。先在 n8n 核对资源是否已创建；不要删除回执绕过。
- 模型请求或结果写回失败：记录保持处理中，不自动重新收费。先核对供应商任务和原执行结果，再决定是否补写或重新申请。
- 关闭/重开 n8n：保留原目录和 Docker 数据卷，继续使用原绑定回执。
- 并发执行由同项目 n8n 数据表中的条件更新争抢执行权。不要复制出使用另一个锁表的工作流，或手动从模型节点中途重跑。原请求结果不明确时保留锁，交人工核对；不能为了继续运行直接清空锁表。便携候选仍不应开启定时付费生产，审核身份与真实业务验收尚未完成。
- 当前未提供自动删除凭据的回滚命令。工作流可先停用；删除前按回执核对专用资源，不能删除后来被其他工作流复用的凭据。

## 没有公共 API 时导出

```sh
python3 portable_n8n.py export --base YOUR_BASE_TOKEN --table YOUR_TABLE_ID \
  --flow probe --out YOUR_NEW_FILE.json
```

导出只包含工作流和凭据引用，不包含密钥。手动导入后，在 n8n Credentials 创建 **Custom Auth**（`httpCustomAuth`），JSON 使用 `body.app_id` 和 `body.app_secret`；在飞书 token 节点绑定。模型节点使用 **Header Auth**，名称 `Authorization`，值 `Bearer ` 加对应模型原始 Key。不要在工作流 Code 节点或导出的 JSON 里粘贴秘密。

脚本和视频提交的手动导出也必须使用 `--experimental-manual`，并提供 `--ledger-table` 与 `--ledger-row`。先在 n8n 手动创建执行数据表，添加 `status`、`owner`、`payload`、`result` 四个文本列，创建一行并把 `status` 设为 `idle`；同一项目的提交工作流必须引用同一行。不要直接删掉锁节点来绕过配置。缺少这两项时导出会停止，不会产生无执行保护的收费工作流。Cloud 无公共 API 的完整业务路线仍需用户自行验证，不能将这份说明视为实测结论。

## 当前验收记录

2026-10-17（北京时间）的[云端矩阵运行](https://github.com/ChuluuMGL/video-factory/actions/runs/38929115815)，源码 `2a8942f`，四条路线全部通过：

| 云端测试环境 | 实际运行 | 结果 |
|---|---|---|
| Windows Server 2022 x64 | 原生 Node.js + n8n | 安装、启动与完整合成流程通过；该步骤约 6 分 36 秒 |
| macOS 14 ARM64 | 原生 Node.js + n8n | 通过 |
| Ubuntu 24.04 x64 | 原生 Node.js + n8n | 通过 |
| Ubuntu Docker | 固定摘要 n8n 镜像 | 通过 |

检查包含安装续接、已有 n8n 公共 API 接入、保留无关工作流、连续配置/凭据复用、字段补齐、脚本/视频状态、重复提交阻断、并发抢占、结果恢复写回、人工修改保护和重启。模型与飞书接口使用合成数据，真实模型调用为零。Docker Desktop 在 Windows/Mac 上的图形安装过程不属于上述 Linux Docker 实测。

Windows 原超时发生在依赖下载/展开阶段。调整测试机安装磁盘、把 npm 缓存放到安装目标所在磁盘后，使用原 npm 安装器完成了完整测试；没有改用未经验证的包管理器，也没有仅靠延长时限判为通过。此结果不保证每台 Windows 电脑的磁盘或网络速度。

Cloud SaaS 实例测试按维护者决定跳过。自动生产的负责人身份与费用确认、便携路线的真实飞书/模型验收仍是发布前待办。以上是候选源码证据，不代表固定发行包或官网已经更新。
