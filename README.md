# Video Factory

> 通过 Agent Skill，在自己的 n8n 上搭建视频工作流。
>
> 默认连接飞书 Base 与 n8n，同一客户可持续新增项目。

**中文** | [English](README.en.md)

[官网安装指南](https://www.yueyu.tech/zh/products/video-factory/) · [固定发行版](https://github.com/ChuluuMGL/video-factory/releases) · [Setup Skill](skills/video-factory-setup/SKILL.md) · [安全报告](SECURITY.md)

## 它是什么

Video Factory 正在把商品资料、脚本生成、视频生成和人工审核连接为可维护的项目流程。管理员通过 AI 助手和 CLI 安装、配置；团队的目标日常入口是飞书 Base 的项目任务表。

- **独立部署**：服务、数据库、账户和密钥由客户管理。
- **多项目复用**：同一客户新增项目时复用基础服务，分别配置商品、飞书和模型。
- **可续接维护**：同一 Skill 支持首次安装、新增项目、继续安装和故障排查。

Skill 是 AI 助手的操作指南；安装位置取决于所选路线，可为本机或服务器。官网用于介绍与安装，不保存客户项目账户或模型密钥。

## 当前版本与使用范围

**MIT 开源测试版。固定版本与摘要以公开 Release 为准。**

官网、源码、完整 Skill 和固定服务器安装包均可公开读取。无需 GitHub 登录；客户提供运行环境、飞书及模型账户。

云端回归覆盖空白 Ubuntu、固定包匿名安装、私密终端 Setup、飞书授权模拟、PostgreSQL、真实 n8n、冷恢复和两种 Docker 存储模式断网安装。各项结果以对应源码提交的 Actions 为准。

隔离测试已完成真实飞书连接、DeepSeek 脚本生成、本人表内审核和一次 MiniMax H3 视频回写。负责人在飞书确认费用后，首次自动生成、单次计划消费和原行写回均通过，脚本已由本人审核。第二账号权限、非作者安装及桌面实际退出/登录仍保留待验。

详细范围：[云端验证](https://github.com/ChuluuMGL/video-factory/actions/runs/39175731136) · [当前状态](docs/product/STATUS.md)。

## 先看安装目标

a38 测试版包含本机安装与已有 n8n 接入。Windows、Mac、Linux、Docker 的真实 n8n 自动化测试通过；真实飞书与模型的隔离验收另有回执，见[便携安装与验收记录](docs/product/PORTABLE_N8N.md)。新增[保留原项目的升级指引](docs/product/PORTABLE_UPGRADE.md)。发布与检查见 [a38 回执](docs/product/DELIVERY_A38_RESULT.md)。公开下载版本以 Release 为准。

把完整 Skill 交给 Agent 后，**先选已有 n8n 的位置、飞书应用/Base 的准备情况，以及希望运行产品执行服务的位置**。有 n8n 就先盘点并保留，没 n8n 再选择安装方式；不要求一上来就租服务器。Skill 附带只读的 `scripts/choose_install_path.py`，用于把这些答案整理成当前可执行路线。

| 你要做什么 | 怎么开始 | 与 Video Factory 的关系 |
|---|---|---|
| 只想在浏览器里使用 n8n | 注册 [n8n Cloud](https://docs.n8n.io/deploy/use-n8n-cloud.md)，本机无需安装 n8n 或 Docker | 这是独立的 n8n，**不是** Video Factory 安装 |
| 只想在 Windows/Mac 本机试用 n8n | 按 [n8n 官方 Node.js 要求](https://docs.n8n.io/deploy/host-n8n/install-options/install-with-npm.md)安装 Node.js，运行 `npx n8n`，再打开 `http://localhost:5678`；官方已提示 npm 安装方式从 n8n 3.0 起弃用 | 适合单独试用 n8n，**不会**安装本产品 |
| 在本机安装或接入已有 n8n | 使用完整 Skill 的 `portable_n8n.py welcome`，选择已有实例、Node.js 或 Docker | 配置飞书和模型工作流；无需另租 Ubuntu，安装后核对本项目的权限与任务 |
| 安装服务器完整栈 | 在自己的 Ubuntu 24.04 x86_64 服务器运行下方 Setup；Windows/Mac 上的 Agent 可通过 SSH 操作服务器 | 安装产品服务、PostgreSQL、自带 n8n；按需部署私网执行器 |

**按所选路线安装：**完整 Skill 提供本机 Node.js、Docker 和已有 n8n 接入，保留原工作流及凭据；服务器完整栈另要求 Ubuntu。Cloud 真实实例本轮未测。本机/已有实例按[便携安装指南](docs/product/PORTABLE_N8N.md)操作，服务器完整栈使用对应 bootstrap。

便携路线提供[后台运行与自启](docs/product/PORTABLE_N8N.md#后台运行与自启)：Linux root 随开机启动，Mac/Windows 随用户登录启动；已有 n8n 保留原管理方式。

便携路线的[负责人身份与费用确认](docs/product/PORTABLE_BILLING_APPROVAL.md)：先做真实飞书零费用确认，再显式启用按计划执行。尚非稳定版验收声明。

## 模型支持到哪一步

| 用途 | 固定发行包可选 | 开发候选或待接入 |
|---|---|---|
| 脚本 | DeepSeek Flash | GPT、Gemini、MiniMax 文本、千问和豆包的安装、费用计划和执行接入见 [PR #61](https://github.com/ChuluuMGL/video-factory/pull/61)；四平台合成测试通过，未包含在 a38，真实账户验收待完成 |
| 视频 | MiniMax H3 | 可灵、Seedance 和万相视频的安装、费用确认、提交和轮询接入见 [PR #62](https://github.com/ChuluuMGL/video-factory/pull/62)；未包含在 a38，真实生成验收待完成 |
| 图片 | 暂无独立图片生成路线 | Seedream（即梦图片）列在[待办 Issue #45](https://github.com/ChuluuMGL/video-factory/issues/45)；它不是 Seedance 视频接口 |

旧项目用过某个模型、合成测试通过，都不等于它已在本产品的固定发行包中可选。不同接口仍需用各自账户完成调用、结果回写和人工审核验收；安装本身不触发付费生成。

## 安装完整 Video Factory

推荐从[官网安装指南](https://www.yueyu.tech/zh/products/video-factory/)下载完整 Skill，再交给能读取文件并操作目标电脑或服务器的 Agent。

**也可以直接从 GitHub 开始：**

1. 阅读[安装与交接指南](docs/product/CUSTOMER_GUIDE.md)。
2. 让 Agent 读取完整的 [`skills/video-factory-setup/`](skills/video-factory-setup/) 目录，保留其中的 `references/`。
3. 将下面的指引交给 Agent；由它按 Skill 获取和校验固定发行包，然后逐步引导配置。

```text
请使用本仓库 skills/video-factory-setup/ 中的完整 Skill，
先问我已有 n8n、飞书应用/Base 和希望运行产品服务的位置，保留现有配置，
再告诉我当前版本可完成哪条路线；可安装时配置第一个测试项目。
使用 GitHub 最新公开测试发行的固定标签与摘要；先检查环境和发行包，再逐步引导 Setup。
密码和 API Key 在所选电脑或服务器的私密终端隐藏输入，不放在聊天中。
```

**若选择当前固定包的完整自动部署**，准备 Ubuntu 24.04 x86_64 主机（至少 4 GiB 内存）、访问方式、项目资料、飞书应用与模型账户。Agent 检查并准备 Python 3.12、Docker 与 Compose。默认 CLI 安装不要求业务子域名。

Windows/Mac 用户可直接选择本机 Node.js、Docker 或已有 n8n。只有选择 Ubuntu 完整栈时，Agent 才通过 SSH 连接服务器。

固定发行包可匿名下载。不要把开发分支或临时 Actions 附件当作正式安装包。

## 已有安装，继续使用

| 告诉 Agent | 处理方式 |
|---|---|
| 新增一个项目 | 复用当前服务，建立独立项目配置，无需重装 Skill 或 n8n |
| 继续上次安装 | 核对原 session 和部署状态，续接未完成步骤 |
| 排查这个项目的问题 | 先只读诊断，定位服务器、飞书、模型或任务故障 |
| 升级服务 | 核对兼容版本，先备份并保留回退路径；更新 Skill 不等于升级服务 |

## 安装后有哪些部分

| 部分 | 位置与用途 |
|---|---|
| Setup Skill | 管理员或实施人员的 Agent，负责安装和维护 |
| n8n 与项目工作流 | 所选本机或服务器，保存配置并执行任务；完整栈另含产品服务和 PostgreSQL |
| 可选项目 runner | 客户服务器 Docker 私网，推进已批准任务，无网页入口 |
| 飞书 Base | 项目资料、来源任务与员工日常查看 |
| 负责人费用确认 | 在飞书核对并确认单次生成计划；审核通过不会自动授权下一次付费调用 |

[原任务表审核](docs/product/FEISHU_NATIVE_REVIEW.md)把脚本、版本和成片写回同一任务，由员工在飞书表内退回或通过。可选 [Base 结果同步](docs/product/BASE_RESULTS.md)只追加另一张结果快照表。首次安装不部署常驻员工网页，见[产品形态与验收边界](docs/product/PRODUCT_SHAPE.md)。

## 文档与仓库范围

- 安装人员：[客户指南](docs/product/CUSTOMER_GUIDE.md)、[平台与模型支持矩阵](docs/product/PLATFORMS_AND_MODELS.md)、[完整 Skill](skills/video-factory-setup/SKILL.md)、[验收指南](docs/product/INDEPENDENT_ACCEPTANCE.md)。
- 维护人员：[排障与问题反馈](docs/product/TROUBLESHOOTING.md)、[产品定义](docs/product/PRD.md)、[状态](docs/product/STATUS.md)、[迁移范围](docs/product/MIGRATION.md)、[公开发布检查](docs/product/PUBLICATION_REVIEW.md)。
- 安全与数据：[安全报告](SECURITY.md)、[数据说明](PRIVACY.md)。

此仓库保存通用源码、合成测试、安装器和产品文档。旧 TikTok 项目仓库保留为私有历史档案，不作为新客户入口，也不随此仓库一起公开。

## 维护、版权与授权

由 [Chuluu](https://github.com/ChuluuMGL) 维护；产品官网：[月瑀科技](https://www.yueyu.tech/zh/products/video-factory/)。

Copyright (c) 2026 Chuluu。署名与授权状态见 [NOTICE](NOTICE)。本项目自有源码和文档采用 [MIT](LICENSE)；第三方依赖、镜像和案例媒体保留各自条款，见 [第三方说明](THIRD_PARTY_NOTICES.md)。欢迎通过 Issue 和 Pull Request 提出改进，参见 [贡献指南](CONTRIBUTING.md)。

公开包包含项目自身 wheel 和固定依赖下载清单。首次安装由同一入口从 files.pythonhosted.org 获取外部 wheel 并逐一校验 SHA-256；服务器也需能访问服务镜像源。已完整获取的依赖可离线复用，不重复下载。
