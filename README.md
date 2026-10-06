# Video Factory

> 通过 Agent Skill，把视频工作流部署到你自己的服务器。
>
> 默认连接飞书 Base 与 n8n，同一客户可持续新增项目。

**中文** | [English](README.en.md)

[官网安装指南](https://www.yueyu.tech/zh/products/video-factory/) · [固定发行版](https://github.com/ChuluuMGL/video-factory/releases/tag/v0.1.0a31) · [Setup Skill](skills/video-factory-setup/SKILL.md) · [安全报告](SECURITY.md)

## 它是什么

Video Factory 将商品资料、脚本生成、视频生成和人工审核连接为可维护的项目流程。管理员通过 AI 助手和 CLI 安装、配置；团队默认在飞书 Base 整理任务。

- **独立部署**：服务、数据库、账户和密钥由客户管理。
- **多项目复用**：同一客户新增项目时复用基础服务，分别配置商品、飞书和模型。
- **可续接维护**：同一 Skill 支持首次安装、新增项目、继续安装和故障排查。

安装程序运行在客户服务器；Skill 是 AI 助手的操作指南。官网用于介绍与安装，不保存客户项目账户或模型密钥。

## 当前版本与使用范围

**0.1.0a31 · MIT 开源测试版。**

官网、源码、完整 Skill 和固定服务器安装包均可公开读取。无需 GitHub 登录；客户自行提供服务器、飞书及模型账户。

a31 已通过 247 项核心回归、固定包匿名安装、浏览器交互、PostgreSQL/n8n 升级与恢复、两种 Docker 存储模式断网验证。此前 a29 实测过真实飞书建表、任务导入和脚本返工审核，由 Agent 操作同一账号。**当前版本的新部署真实模型生成、不同员工账号和独立客户完整验收仍未完成**；历史项目案例不代替本安装版验收。

详细范围：[发行验证记录](docs/product/DELIVERY_A31_RESULT.md) · [当前状态](docs/product/STATUS.md)。

## 开始安装

推荐从[官网安装指南](https://www.yueyu.tech/zh/products/video-factory/)下载完整 Skill，再交给能读取文件并操作服务器的 Agent。

**也可以直接从 GitHub 开始：**

1. 阅读[安装与交接指南](docs/product/CUSTOMER_GUIDE.md)。
2. 让 Agent 读取完整的 [`skills/video-factory-setup/`](skills/video-factory-setup/) 目录，保留其中的 `references/`。
3. 将下面的指引交给 Agent；由它按 Skill 获取和校验固定发行包，然后逐步引导配置。

```text
请使用本仓库 skills/video-factory-setup/ 中的完整 Skill，
带我在自己的服务器上首次安装 Video Factory，并配置第一个测试项目。
固定版本使用 0.1.0a31；先检查服务器和发行包，再逐步引导 Setup。
密码和 API Key 通过私有输入通道填写，不放在聊天中。
```

准备一台 Ubuntu 24.04 x86_64 服务器（至少 4 GiB 内存）、服务器访问方式、项目资料、飞书应用与模型账户。Agent 检查并准备 Python 3.12、Docker 与 Compose。默认 CLI 安装不要求业务子域名。

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
| 飞书 Base | 项目资料、来源任务与员工日常查看 |
| 受控审核命令 | Agent 使用本人飞书授权逐条导入和审核 |

公开 a31 不自动回写生成结果。代码中已有网页审核实现，但推荐的首次安装不部署常驻员工网页；飞书内完整审核、结果回写和独立员工验收仍待完成。见[产品形态与验收边界](docs/product/PRODUCT_SHAPE.md)。

## 文档与仓库范围

- 安装人员：[客户指南](docs/product/CUSTOMER_GUIDE.md)、[完整 Skill](skills/video-factory-setup/SKILL.md)、[验收指南](docs/product/INDEPENDENT_ACCEPTANCE.md)。
- 维护人员：[产品定义](docs/product/PRD.md)、[状态](docs/product/STATUS.md)、[迁移范围](docs/product/MIGRATION.md)、[公开发布检查](docs/product/PUBLICATION_REVIEW.md)。
- 安全与数据：[安全报告](SECURITY.md)、[数据说明](PRIVACY.md)。

此仓库保存通用源码、合成测试、安装器和产品文档。旧 TikTok 项目仓库保留为私有历史档案，不作为新客户入口，也不随此仓库一起公开。

## 维护、版权与授权

由 [Chuluu](https://github.com/ChuluuMGL) 维护；产品官网：[月瑀科技](https://www.yueyu.tech/zh/products/video-factory/)。

Copyright (c) 2026 Chuluu。署名与授权状态见 [NOTICE](NOTICE)。本项目自有源码和文档采用 [MIT](LICENSE)；第三方依赖、镜像和案例媒体保留各自条款，见 [第三方说明](THIRD_PARTY_NOTICES.md)。欢迎通过 Issue 和 Pull Request 提出改进，参见 [贡献指南](CONTRIBUTING.md)。

a31 公开包包含项目自身 wheel 和固定依赖下载清单。首次安装由同一入口从 files.pythonhosted.org 获取外部 wheel 并逐一校验 SHA-256；服务器也需能访问服务镜像源。已完整获取的依赖可离线复用，不重复下载。
