# 安装平台与模型接口

首次进入先选择已有 n8n、本机 Node.js、Docker 或服务器。**a36 完整 Skill 提供便携安装和已有实例接入**：Windows、Mac、Linux 与 Linux Docker 的真实 n8n 合成流程均通过，详见[安装使用与验收记录](PORTABLE_N8N.md)。已有实例通过公共 API 接入，不接管原数据库、不覆盖旧工作流。隔离环境的真实飞书、DeepSeek、单次 MiniMax H3 及当前负责人费用确认已有回执；第二账号和独立安装仍待验，Cloud SaaS 实测本轮跳过。

以下 Ubuntu 要求针对**旧的固定发行包完整服务栈**，不适用于上述便携路线。两条路线不能混用安装命令或回执。

先区分**操作电脑**和**运行服务器**。安装人员可以在 Windows、macOS 或 Linux 上使用 Agent，通过 SSH 管理客户服务器；这不会把服务安装到操作电脑。当前固定发行包只在客户自己的 **Ubuntu 24.04 x86_64** 主机上提供自动准备与完整栈部署。安装预检要求至少 4 GiB 内存、4 GiB **剩余**磁盘、Python 3.12、root、systemd、本地 Docker Engine 与 Compose 2.24+；4 GiB 剩余磁盘只是预检门槛，不是视频生产的容量建议。实际磁盘还要计入镜像、数据库、素材、成片和备份留存，按项目量规划并在上线前实测。安装器会核对本机 Unix Docker socket；不能指向远程 Docker daemon。

| 位置 | n8n 本身 | 当前发行可用路线 | 使用方式 |
|---|---|---|---|
| Ubuntu 24.04 x86_64 服务器 | 可用 Docker Compose | 自动准备与完整部署路径 | 一客户一套服务，项目复用同一套 n8n |
| Windows 电脑或服务器 | 可在浏览器使用 n8n Cloud；本地可用 Node.js 或 Docker 运行 n8n | 便携 Node.js 或 Docker；完整栈另需 Ubuntu | 用完整 Skill 的 welcome 向导安装或接入 |
| macOS 电脑或服务器 | 可在浏览器使用 n8n Cloud；本地可用 Node.js 或 Docker 运行 n8n | 便携 Node.js 或 Docker；完整栈另需 Ubuntu | 用完整 Skill 的 welcome 向导安装或接入 |
| 既有独立 n8n | 可以独立管理 | 通过公共 API 导入独立项目工作流 | 保留原数据库、凭据和工作流，配置本项目权限 |

n8n 的编辑器本来就在浏览器中打开，浏览器界面并不能说明它运行在哪里。[n8n Cloud](https://docs.n8n.io/deploy/use-n8n-cloud.md) 由 n8n 托管，本机无需安装 Docker 或 n8n；[Node.js/npm 方式](https://docs.n8n.io/deploy/host-n8n/install-options/install-with-npm.md)可在本机启动并访问 `http://localhost:5678`，适合独立试用，但官方标注 npm 安装从 n8n 3.0 起弃用；自托管生产参考官方推荐的 [Docker Compose 安装](https://docs.n8n.io/deploy/host-n8n/install-options/install-using-docker-compose.md)。**单独启动 n8n 不等于完成飞书与模型配置**。旧固定包的完整服务栈还需 PostgreSQL、运行服务、私网 Runner、固定镜像、密钥和备份。不要在 Windows PowerShell、macOS 终端或 WSL2 中直接执行本发行包的 `bootstrap.sh` 并绕过平台检查；WSL2 和 Apple Silicon 的完整栈还未验收。

如果只需**单独试用 n8n**，可直接注册 n8n Cloud，或在装有官方要求版本 Node.js 的 Windows/Mac 终端运行 `npx n8n`，然后打开 `http://localhost:5678`。`npx` 会下载依赖，不是“仅靠网页在本机运行”；请留意上述 npm 弃用公告。需要长期自托管 n8n 时，再按官方 Compose 指南准备持久存储、加密密钥、HTTPS、备份和更新。以上任一路线都不运行 `vfctl`，也不会自动获得 Video Factory 的任务、私网凭据或飞书接入。

如果明确选择**旧固定包的完整服务栈**，请在 Windows/Mac 的 Agent 中选择“远程 Ubuntu 主机”，经已核验指纹的 SSH 连接；在 Ubuntu 主机上按[完整安装路径](https://github.com/ChuluuMGL/video-factory/blob/main/skills/video-factory-setup/references/complete-install.md)检查 `bootstrap.sh --check`，再部署产品自带的 n8n。已有独立 n8n 时，不用“再装一个 n8n”作为诊断：先核对是否属于本产品 stack；属于则续接原 session，不属于则保持隔离。

客户遇到 `UBUNTU_2404_X86_64_ROOT_REQUIRED`、`LINUX_X86_64_ROOT_ADMIN_REQUIRED` 或 `LOCAL_DOCKER_ENGINE_REQUIRED` 时，Agent 应先回读目标主机的 `uname -s`、`uname -m`、`/etc/os-release`、Docker context、Compose 版本和可用空间，再决定是连接错机器、依赖缺失，还是平台不受支持。不能只因 n8n 网页能打开就继续 Setup。Ubuntu 服务器已有 n8n 时，也先核对其所有权、数据与备份，不覆盖现有实例。

## 模型接口与验收状态

模型账户按客户、项目分别配置；密钥只在客户私有终端录入，由所选实例的 n8n 加密保存。安装本身不发起付费生成。选中模型后仍需单条任务计划确认、提交回执、超时不自动重发，以及飞书人工审核。

下表是 a40 便携路线的配置能力；a38 及更早固定包不含新增供应商。实际可下载版本以 Releases 为准，详细实测见 [a40 记录](DELIVERY_A40_RESULT.md)。

| 用途 | 接口 | 已接入能力 | 真实验收 |
|---|---|---|---|
| 脚本 | DeepSeek | 安装、费用计划、生成和回写 | 已有隔离真实生成与本人审核 |
| 脚本 | GPT、Gemini、MiniMax 文本、千问、豆包 | 安装、固定官方域名请求、费用计划和回写；合成集成通过 | 各自真实账户未完成；非 DeepSeek 尚无零费用只读鉴权 |
| 视频 | MiniMax H3 | 单次授权、提交、轮询和回写 | 已有真实验收 |
| 视频 | Seedance 2.0 | 同上，按账号已开通版本选择 | 本次一次 5 秒有声视频生成、回写、完整解码通过，待本人审核 |
| 视频 | 可灵 | 单 API Key 路线；合成集成通过 | 维护者根据既有项目经验决定本轮免测，不作为收尾阻塞项；不冒充本产品成片实测通过 |
| 视频 | Seedance 2.5、万相 | 单次授权、提交、轮询和回写；合成集成通过 | 真实账户待验；不自动重试 |
| 图片 | Seedream 5.0 Lite | 独立费用计划、单张参考图生成、附件回写和恢复 | 本次 2K 图片、飞书附件和完整解码通过，待本人审核 |

“豆包”在此表指脚本大模型，“Seedance”指视频生成，“Seedream”指图片生成；千问脚本与万相视频也是不同接口。既有项目经验与本产品实测分开记录；本轮可灵免测是维护者的明确决定。新接口必须在隔离云端通过合成合同测试，再用客户授权的真实 Key 逐条付费验收；没有回执和成片回写前，不能写成开箱即用。模型列表随官方 API 变化，客户输入的模型 ID、接口区域和 Key 必须来自同一账户；不能从历史项目搬运密钥。

便携脚本接入见 [PR #61](https://github.com/ChuluuMGL/video-factory/pull/61)：`welcome` 选择 `deepseek`、`openai`、`gemini`、`minimax-cn`、`minimax-global`、`qwen-cn` 或 `doubao-cn`。便携视频接入见 [PR #62](https://github.com/ChuluuMGL/video-factory/pull/62)：H3、北京 Seedance 2.0/2.5（按账号已开通版本选择）、北京万相、北京/新加坡可灵。两者都连接 n8n 加密凭据、精确费用计划和原行回写；a38 固定包没有这些新增模型选项。旧完整服务栈的 `production-setup` 适配另在 PR #38/#42，不能混用命令。

可灵凭据不能混用：本版便携生成路线使用单个 API Key。旧 Access Key / Secret Key 的 JWT 鉴权只完成私有诊断，旧接口生成适配器尚未交付；不要把两条凭据拼接到 API Key 输入框。

配置中的默认模型只是配置起点，不代表该账户已有权限；保存 Key 不等于认证成功。已有项目续接沿用原供应商，不把旧 Key 自动发送到新平台。各供应商真实验收另行安排，费用计划变化必须重新确认。

官方参考：[OpenAI Chat Completions](https://platform.openai.com/docs/api-reference/chat/create)、[Gemini OpenAI 兼容接口](https://ai.google.dev/gemini-api/docs/openai)、[MiniMax OpenAI SDK](https://platform.minimax.io/docs/api-reference/text-openai-api)、[阿里云百炼兼容接口](https://help.aliyun.com/en/model-studio/compatibility-of-openai-with-dashscope)、[火山方舟兼容接口](https://docs.volcengine.com/docs/ark/compatible-with-openai-sdk?lang=zh)。这些说明供应商提供接口，不代表 Video Factory 已接入或验收。
