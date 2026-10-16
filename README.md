# Video Factory

> 通过 Agent Skill，把视频工作流部署到你自己的服务器。
>
> 默认连接飞书 Base 与 n8n，同一客户可持续新增项目。

**中文** | [English](README.en.md)

[官网安装指南](https://www.yueyu.tech/zh/products/video-factory/) · [固定发行版](https://github.com/ChuluuMGL/video-factory/releases) · [Setup Skill](skills/video-factory-setup/SKILL.md) · [安全报告](SECURITY.md)

## 它是什么

Video Factory 正在把商品资料、脚本生成、视频生成和人工审核连接为可维护的项目流程。管理员通过 AI 助手和 CLI 安装、配置；团队的目标日常入口是飞书 Base 的项目任务表。

- **独立部署**：服务、数据库、账户和密钥由客户管理。
- **多项目复用**：同一客户新增项目时复用基础服务，分别配置商品、飞书和模型。
- **可续接维护**：同一 Skill 支持首次安装、新增项目、继续安装和故障排查。

安装程序运行在客户服务器；Skill 是 AI 助手的操作指南。官网用于介绍与安装，不保存客户项目账户或模型密钥。

## 当前版本与使用范围

**MIT 开源测试版。固定版本与摘要以公开 Release 为准。**

官网、源码、完整 Skill 和固定服务器安装包均可公开读取。无需 GitHub 登录；客户自行提供服务器、飞书及模型账户。

云端回归覆盖空白 Ubuntu、固定包匿名安装、私密终端 Setup、飞书授权模拟、PostgreSQL、真实 n8n、冷恢复和两种 Docker 存储模式断网安装。各项结果以对应源码提交的 Actions 为准。

开发版已在隔离测试 Base 完成单账号脚本退回、第二版复审、单次真实 H3 生成、成片回写和本人表内视频通过；认证事件与服务器同一修订回读为 `accepted`。**新固定包部署、真实脚本模型、第二项目、不同员工权限和非作者完整安装仍需分别验收。**历史案例和安装成功不代替这些检查。

详细范围：[云端验证](https://github.com/ChuluuMGL/video-factory/actions/runs/37562989085) · [当前状态](docs/product/STATUS.md)。

## 先看安装目标

把完整 Skill 交给 Agent 后，**先选已有 n8n 的位置、飞书应用/Base 的准备情况，以及希望运行产品执行服务的位置**。有 n8n 就先盘点并保留，没 n8n 再选择安装方式；不要求一上来就租服务器。Skill 附带只读的 `scripts/choose_install_path.py`，用于把这些答案整理成当前可执行路线。

| 你要做什么 | 怎么开始 | 与 Video Factory 的关系 |
|---|---|---|
| 只想在浏览器里使用 n8n | 注册 [n8n Cloud](https://docs.n8n.io/deploy/use-n8n-cloud.md)，本机无需安装 n8n 或 Docker | 这是独立的 n8n，**不是** Video Factory 安装 |
| 只想在 Windows/Mac 本机试用 n8n | 按 [n8n 官方 Node.js 要求](https://docs.n8n.io/deploy/host-n8n/install-options/install-with-npm.md)安装 Node.js，运行 `npx n8n`，再打开 `http://localhost:5678`；官方已提示 npm 安装方式从 n8n 3.0 起弃用 | 适合单独试用 n8n，**不会**安装本产品 |
| 安装完整 Video Factory | 在自己的 Ubuntu 24.04 x86_64 服务器运行下方 Setup；Windows/Mac 上的 Agent 可通过 SSH 操作服务器 | 安装产品服务、PostgreSQL、自带 n8n；按需部署私网执行器 |

已有 n8n Cloud 或本机 n8n 的工作流和凭据，当前安装包**还不能直接接入**。这是待补的产品能力，不是要求客户放弃已有环境。下面的 Ubuntu 要求仅针对**当前固定包的完整自动部署**。复用已有 n8n 的[接入路线与验收条件](docs/product/EXISTING_N8N_INTEGRATION.md)已列明；当前只读环境识别不代表完整安装通过。

## 模型支持到哪一步

| 用途 | 固定发行包可选 | 开发候选或待接入 |
|---|---|---|
| 脚本 | DeepSeek Flash | GPT、Gemini、MiniMax 文本、千问和豆包的脚本适配在[开发候选 PR #38](https://github.com/ChuluuMGL/video-factory/pull/38)，尚未完成各自真实账户验收 |
| 视频 | MiniMax H3 | 可灵、Seedance 和万相视频的接口适配在[开发候选 PR #42](https://github.com/ChuluuMGL/video-factory/pull/42)，尚未接入正式 Setup 或完成真实生成验收 |
| 图片 | 暂无独立图片生成路线 | Seedream（即梦图片）列在[待办 Issue #45](https://github.com/ChuluuMGL/video-factory/issues/45)；它不是 Seedance 视频接口 |

旧项目用过某个模型、合成测试通过，都不等于它已在本产品的固定发行包中可选。不同接口仍需用各自账户完成调用、结果回写和人工审核验收；安装本身不触发付费生成。

## 安装完整 Video Factory

推荐从[官网安装指南](https://www.yueyu.tech/zh/products/video-factory/)下载完整 Skill，再交给能读取文件并操作服务器的 Agent。

**也可以直接从 GitHub 开始：**

1. 阅读[安装与交接指南](docs/product/CUSTOMER_GUIDE.md)。
2. 让 Agent 读取完整的 [`skills/video-factory-setup/`](skills/video-factory-setup/) 目录，保留其中的 `references/`。
3. 将下面的指引交给 Agent；由它按 Skill 获取和校验固定发行包，然后逐步引导配置。

```text
请使用本仓库 skills/video-factory-setup/ 中的完整 Skill，
先问我已有 n8n、飞书应用/Base 和希望运行产品服务的位置，保留现有配置，
再告诉我当前版本可完成哪条路线；可安装时配置第一个测试项目。
使用 GitHub 最新公开测试发行的固定标签与摘要；先检查环境和发行包，再逐步引导 Setup。
密码和 API Key 默认在客户服务器的私密终端隐藏输入，不放在聊天中。
```

**若选择当前固定包的完整自动部署**，准备 Ubuntu 24.04 x86_64 主机（至少 4 GiB 内存）、访问方式、项目资料、飞书应用与模型账户。Agent 检查并准备 Python 3.12、Docker 与 Compose。默认 CLI 安装不要求业务子域名。

在 Windows 或 Mac 上使用 Agent 安装时，Agent **远程连接这台 Ubuntu 服务器**。当前发行包不能原生部署整套服务到 Windows/Mac；这不影响单独使用 n8n Cloud 或本机 n8n。

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
| 产品服务、PostgreSQL、n8n | 客户服务器，保存配置并执行任务 |
| 可选项目 runner | 客户服务器 Docker 私网，推进已批准任务，无网页入口 |
| 飞书 Base | 项目资料、来源任务与员工日常查看 |
| 当前测试用审核命令 | `stack-feishu-session` 在私有终端完成本人飞书授权，逐条核验导入和审核；尚非飞书 Base 内的日常审核入口 |

[原任务表审核](docs/product/FEISHU_NATIVE_REVIEW.md)把脚本、版本和成片写回同一任务，由员工在飞书表内退回或通过。可选 [Base 结果同步](docs/product/BASE_RESULTS.md)只追加另一张结果快照表。终端审核用于技术验收；首次安装不部署常驻员工网页，见[产品形态与验收边界](docs/product/PRODUCT_SHAPE.md)。

## 文档与仓库范围

- 安装人员：[客户指南](docs/product/CUSTOMER_GUIDE.md)、[平台与模型支持矩阵](docs/product/PLATFORMS_AND_MODELS.md)、[完整 Skill](skills/video-factory-setup/SKILL.md)、[验收指南](docs/product/INDEPENDENT_ACCEPTANCE.md)。
- 维护人员：[排障与问题反馈](docs/product/TROUBLESHOOTING.md)、[产品定义](docs/product/PRD.md)、[状态](docs/product/STATUS.md)、[迁移范围](docs/product/MIGRATION.md)、[公开发布检查](docs/product/PUBLICATION_REVIEW.md)。
- 安全与数据：[安全报告](SECURITY.md)、[数据说明](PRIVACY.md)。

此仓库保存通用源码、合成测试、安装器和产品文档。旧 TikTok 项目仓库保留为私有历史档案，不作为新客户入口，也不随此仓库一起公开。

## 维护、版权与授权

由 [Chuluu](https://github.com/ChuluuMGL) 维护；产品官网：[月瑀科技](https://www.yueyu.tech/zh/products/video-factory/)。

Copyright (c) 2026 Chuluu。署名与授权状态见 [NOTICE](NOTICE)。本项目自有源码和文档采用 [MIT](LICENSE)；第三方依赖、镜像和案例媒体保留各自条款，见 [第三方说明](THIRD_PARTY_NOTICES.md)。欢迎通过 Issue 和 Pull Request 提出改进，参见 [贡献指南](CONTRIBUTING.md)。

公开包包含项目自身 wheel 和固定依赖下载清单。首次安装由同一入口从 files.pythonhosted.org 获取外部 wheel 并逐一校验 SHA-256；服务器也需能访问服务镜像源。已完整获取的依赖可离线复用，不重复下载。

本机和已有 n8n 的实际安装/接入正在 [PR #49](https://github.com/ChuluuMGL/video-factory/pull/49) 验收，包含原生安装、Docker 和公共 API 导入。参见 [候选操作说明](docs/product/PORTABLE_N8N.md)；尚不是稳定发行。
