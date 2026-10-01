# Video Factory

通过 Agent Skill，在客户自己的服务器上安装和管理视频项目。默认使用飞书 Base 与 n8n；同一客户可管理多个项目，不局限于任何发布平台。

**当前是私有受控测试版。安装、真实飞书建表与脚本审核有历史实测；完整视频自动生产和独立客户自助交付尚未验收。**

a30 已发布：端口检测修复、247 项核心回归与全部五组云端检查通过；安装指南和完整 Skill 已同步。a29 另已完成授权云服务器上的真实飞书建表、任务导入、脚本返工审核和 Setup 续接，由 Agent 操作同一真实账号。真实域名与模型、不同员工账号、独立人员验收仍未完成。

## 从哪里开始

**[打开安装指南页面](https://video-factory-install-guide.fresh-note-6263.chatgpt.site)** · 当前为所有者私有预览。

**[下载经过云端验证的 0.1.0a30 候选版](https://github.com/ChuluuMGL/video-factory/releases/tag/v0.1.0a30)** · 需要本仓库读取权限。

[本次交付验证记录](docs/product/DELIVERY_A30_RESULT.md)明确区分已通过与未完成范围。

- **安装者**：[安装与交接指南](docs/product/CUSTOMER_GUIDE.md) → [完整 Setup Skill](skills/video-factory-setup/SKILL.md)。安装页面源文件在 `delivery/site/`，同版本网页与 Skill 包由云端检查后生成。
- **已有客户新增项目**：调用同一 Skill，选择“新增项目”，复用已有服务；不重装 n8n，不默认复制旧项目的业务凭据。
- **维护者**：[产品定义](docs/product/PRD.md)、[当前状态](docs/product/STATUS.md)、[交付验收](docs/product/DELIVERY_ACCEPTANCE.md)。

## 产品由哪些部分组成

| 部分 | 在哪里 | 谁使用 |
|---|---|---|
| 安装页面 | 交付网站 | 客户和安装同事 |
| Setup Skill | 操作人员的 Agent | 管理员和实施人员 |
| 产品服务、PostgreSQL、n8n | 客户服务器 | 持续运行项目与任务 |
| 飞书和审核入口 | 客户工作环境 | 日常员工 |

安装程序是 Skill 调用的服务器部署工具，不是员工必须另装的桌面软件。Skill 更新与服务器升级分别进行。当前服务器目标为 Linux x86_64 / Python 3.12 / Docker Compose。

## 仓库范围

这是正式产品整理仓库，保持私有。只迁入产品源码、通用测试、安装器、Skill 和产品文档；历史四客户运行目录、ECS 回执、客户数据和旧工作流文件不迁入。保留一个 a19 运行底座源码基线，仅用于云端升级回归，不导入旧仓库的完整历史。

原 TikTok 仓库保留作为私有历史档案，不是新客户安装入口。此仓库尚未完成开源审查和许可证选择，不能把“仓库整理完成”写成“已开源”。

固定发行包通过本仓库的云端检查后发布为私有预发行版；Skill 和安装页必须指向同一发行版。首次拉取需要安装者自己的读取权限。不要把开发分支、临时 Actions 下载地址或“latest”冒充固定产品版本。
