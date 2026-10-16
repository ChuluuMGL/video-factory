# 安装平台与模型接口

首次进入由 Skill 先问已有 n8n、飞书应用/Base 与执行服务位置。复用已有 n8n 是产品目标；当前固定包尚未交付完整接入，见[接入路线与验收条件](EXISTING_N8N_INTEGRATION.md)。下面的 Ubuntu 条件只属于现版完整自动部署。

先区分**操作电脑**和**运行服务器**。安装人员可以在 Windows、macOS 或 Linux 上使用 Agent，通过 SSH 管理客户服务器；这不会把服务安装到操作电脑。当前固定发行包只在客户自己的 **Ubuntu 24.04 x86_64** 主机上提供自动准备与完整栈部署。安装预检要求至少 4 GiB 内存、4 GiB **剩余**磁盘、Python 3.12、root、systemd、本地 Docker Engine 与 Compose 2.24+；4 GiB 剩余磁盘只是预检门槛，不是视频生产的容量建议。实际磁盘还要计入镜像、数据库、素材、成片和备份留存，按项目量规划并在上线前实测。安装器会核对本机 Unix Docker socket；不能指向远程 Docker daemon。

| 位置 | n8n 本身 | 当前 Video Factory 固定发行包 | 推荐做法 |
|---|---|---|---|
| Ubuntu 24.04 x86_64 服务器 | 可用 Docker Compose | 自动准备与完整部署路径 | 一客户一套服务，项目复用同一套 n8n |
| Windows 电脑或服务器 | 可在浏览器使用 n8n Cloud；本地可用 Node.js 或 Docker 运行 n8n | **不支持原生安装整套产品** | 在独立 Ubuntu x86_64 主机/虚拟机部署，Windows Agent 通过 SSH 操作 |
| macOS 电脑或服务器 | 可在浏览器使用 n8n Cloud；本地可用 Node.js 或 Docker 运行 n8n | **不支持原生安装整套产品** | 在独立 Ubuntu x86_64 主机/虚拟机部署，Mac Agent 通过 SSH 操作 |
| 既有独立 n8n | 可以独立管理 | 尚未支持直接接管其数据库、凭据和工作流 | 不导入产品的私网执行凭据到未知 n8n；先做隔离迁移设计 |

n8n 的编辑器本来就在浏览器中打开，浏览器界面并不能说明它运行在哪里。[n8n Cloud](https://docs.n8n.io/deploy/use-n8n-cloud.md) 由 n8n 托管，本机无需安装 Docker 或 n8n；[Node.js/npm 方式](https://docs.n8n.io/deploy/host-n8n/install-options/install-with-npm.md)可在本机启动并访问 `http://localhost:5678`，适合独立试用，但官方标注 npm 安装从 n8n 3.0 起弃用；自托管生产参考官方推荐的 [Docker Compose 安装](https://docs.n8n.io/deploy/host-n8n/install-options/install-using-docker-compose.md)。**单独启动 n8n 不等于完成 Video Factory 安装**：产品还需 PostgreSQL、运行服务、私网 Runner、固定镜像、密钥和备份。不要在 Windows PowerShell、macOS 终端或 WSL2 中直接执行本发行包的 `bootstrap.sh` 并绕过平台检查；WSL2 和 Apple Silicon 的完整栈还未验收。

如果只需**单独试用 n8n**，可直接注册 n8n Cloud，或在装有官方要求版本 Node.js 的 Windows/Mac 终端运行 `npx n8n`，然后打开 `http://localhost:5678`。`npx` 会下载依赖，不是“仅靠网页在本机运行”；请留意上述 npm 弃用公告。需要长期自托管 n8n 时，再按官方 Compose 指南准备持久存储、加密密钥、HTTPS、备份和更新。以上任一路线都不运行 `vfctl`，也不会自动获得 Video Factory 的任务、私网凭据或飞书接入。

如果目标是**安装 Video Factory**，请在 Windows/Mac 的 Agent 中选择“远程 Ubuntu 主机”，经已核验指纹的 SSH 连接；在 Ubuntu 主机上按[完整安装路径](https://github.com/ChuluuMGL/video-factory/blob/main/skills/video-factory-setup/references/complete-install.md)检查 `bootstrap.sh --check`，再部署产品自带的 n8n。已有独立 n8n 时，不用“再装一个 n8n”作为诊断：先核对是否属于本产品 stack；属于则续接原 session，不属于则保持隔离。

客户遇到 `UBUNTU_2404_X86_64_ROOT_REQUIRED`、`LINUX_X86_64_ROOT_ADMIN_REQUIRED` 或 `LOCAL_DOCKER_ENGINE_REQUIRED` 时，Agent 应先回读目标主机的 `uname -s`、`uname -m`、`/etc/os-release`、Docker context、Compose 版本和可用空间，再决定是连接错机器、依赖缺失，还是平台不受支持。不能只因 n8n 网页能打开就继续 Setup。Ubuntu 服务器已有 n8n 时，也先核对其所有权、数据与备份，不覆盖现有实例。

## 模型接口与验收状态

模型账户按客户、项目分别配置；密钥只在客户私有终端录入并加密存于客户服务器。安装本身不发起付费生成。选中模型后仍需单条任务计划确认、提交回执、超时不自动重发，以及飞书人工审核。

| 用途 | 接口 | 当前固定发行包 | 接入前仍需验证 |
|---|---|---|---|
| 脚本 | DeepSeek Flash | 已接入 | 客户自己的 Key、区域、余额、真实脚本和 Base 回写 |
| 脚本 | OpenAI GPT、Google Gemini、MiniMax 文本、阿里云千问、火山方舟豆包 | **当前固定发行包未包含**；开发候选提供固定域名的 Chat Completions 适配，但尚无真实 Key/付费验收 | 供应商账户和模型 ID、生成回执、飞书回写及费用；非 DeepSeek 当前无零费用只读鉴权 |
| 视频 | MiniMax H3 | 已接入单次授权、轮询和媒体回收 | 客户素材、实际 Key、费用和真人审核 |
| 视频 | 可灵 Kling、字节 Seedance、阿里云万相视频 | **未进入固定发行包**；开发候选有隔离的接口适配与合成测试，不能在正式 Setup 中选择 | 各自的上传格式、任务 ID、状态轮询、下载来源、内容限制、费用与未知提交恢复 |
| 图片 | 字节 Seedream（即梦图片） | **未接入固定发行包**；它是图片生成路线，不是 Seedance 视频接口 | 明确图片在任务流程中的用途，再验证模型、输出格式、费用与素材授权 |

“豆包”在此表指脚本大模型，“Seedance”指视频生成，“Seedream”指图片生成；千问脚本与万相视频也是不同接口。既有项目用过某个模型，只能作为适配需求来源。新接口必须在隔离云端通过合成合同测试，再用客户授权的真实 Key 逐条付费验收；没有回执和成片回写前，不能写成开箱即用。模型列表随官方 API 变化，客户输入的模型 ID、接口区域和 Key 必须来自同一账户；不能从历史项目搬运密钥。

开发候选中的脚本接口只接受内置供应商：`deepseek`、`openai`、`gemini`、`minimax-cn`、`minimax-global`、`qwen-cn`、`doubao-cn`。管理员在 `production-setup` 中选择供应商与模型 ID，再在私有终端输入该供应商 Key。默认模型仅是可编辑建议值，选择、保存或通过合成测试都不代表该账户具有模型权限。非 DeepSeek 接口目前不发起付费“测试请求”，因此会明确显示“Key 尚未验证”；不要将此状态报告为认证成功。已确认的脚本请求仍经原任务版本、单次付费计划、提交回执和飞书人工审核。视频仍只开放 H3，不能将文案中的其他供应商填进 H3 Key 字段。

官方参考：[OpenAI Chat Completions](https://platform.openai.com/docs/api-reference/chat/create)、[Gemini OpenAI 兼容接口](https://ai.google.dev/gemini-api/docs/openai)、[MiniMax OpenAI SDK](https://platform.minimax.io/docs/api-reference/text-openai-api)、[阿里云百炼兼容接口](https://help.aliyun.com/en/model-studio/compatibility-of-openai-with-dashscope)、[火山方舟兼容接口](https://docs.volcengine.com/docs/ark/compatible-with-openai-sdk?lang=zh)。这些说明供应商提供接口，不代表 Video Factory 已接入或验收。
