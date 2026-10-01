# 从交付页面开始

先读与此 Skill 同版本交付的 release.json。若只有 Skill、尚无发行包，按下面流程取得；不要让用户自行查找内部构建文件。

## 1. 选择操作

用简短选项询问：第一次安装 / 新增项目 / 继续上次安装 / 排查问题。沿用用户已经提供的答案。首次遇到 Agent、Skill、Base 或 SKU 时，用一句话解释含义；面向用户报告具体对象和下一步，不使用“受控候选”“回读”“同版验证”等内部简写。

- 已有服务：先读取客户交接中的主机、stack、CLI 和 session，实际查询版本与状态；不用最新包覆盖旧服务。
- 新增项目：原 session 只作配置来源，新 session 使用独立路径；同一 stack。继续按 operations.md 第 3 节完成。
- 首次部署：确认真实目标服务器和连接后，再获取安装包。
- 继续安装：先读原状态，不创建替代 session、不删除未知 Base 创建日志。

## 2. 安装本 Skill

用户从指南下载的 ZIP 顶层为 video-factory-setup，需保留 SKILL.md 和 references/。只安装到当前 Agent 支持的自定义 Skill 目录，不猜所有 Agent 使用同一路径。已有同名 Skill 时比较版本，保留本地修改，不静默覆盖。用户只要使用一次可以先读取完整目录，无需重复注册。

## 3. 取得服务器发行包

当前是私有候选交付。指南提供准确 Release 页面；按发行版而非浮动分支获取 archive、release.json 和 SHA256SUMS。GitHub 私有仓库需要安装者自己的读取权限。优先在目标云端使用已有授权的 gh release download 获取指定标签；不要在聊天中索要 GitHub token，不在员工电脑下载大型服务镜像。

若客户没有仓库权限，由已有权限的安装同事把固定包安全传到客户服务器，并提供可信摘要。不能把 Actions 的 14 天临时地址当作正式交付入口。链接不存在、权限不足或版本缺少验证回执时明确报告获取未完成，不猜地址或悄悄切回旧版。

校验完整归档和 manifest 摘要后，使用同包 start.py 连接安装和 Setup；底层 install.py 保留。

版本来源：https://github.com/ChuluuMGL/video-factory/releases 。首次部署读取页面固定标签；已有服务先核对安装版本，不盲选 latest。

start.py 参数为 --manifest-sha256、--prefix、--session、--root；新增项目用 --mode new-project --from-session 原会话，恢复用 --mode resume。a28 没有私有 TTY 时优先使用 --browser-input，由用户在 SSH 隧道中的私有页面输入。--prepare-only 仅准备工具，不表示配置完成。镜像网络受限时选匹配版本的离线镜像包，不混用 wheels 和镜像。大包只在云端流转。

## 4. 引导而不是转交命令

Agent 负责整理非秘密的配置和 SKU、读取 JSON 问题 schema、核对目标并执行已授权操作。一次问一个阶段的必要信息，不向客户堆内部命令。

服务器环境未就绪时列出准确缺项及可执行的准备方案。a28 的环境准备流程支持在 Ubuntu 24.04 x86_64 上准备 Python、Docker 和 Compose，但不会安装操作系统；必须检查实际执行结果，再报告环境准备完成。根据用户已有授权执行环境准备，不能把不受支持的系统强行当作已支持。

首次主机内交互用 setup-run；无私有 TTY 时按 operations.md 的 JSON 分阶段接口完成非秘密步骤。密码和 App Secret 必须由用户直接在安全输入界面录入。不能录屏、回显或自动读取用户输入的秘密到模型上下文。

完成或中断后按 handoff.md 给出结果，保留客户侧 session 定位信息。用户添加后续项目时仍用本 Skill，不重复安装 Skill 或整套服务。
