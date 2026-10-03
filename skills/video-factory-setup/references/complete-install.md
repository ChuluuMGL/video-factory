# 完整安装路径（a28 及以后）

仅当固定发行包包含 bootstrap.sh、CLI 支持 --browser-input/workspace 时使用本页；旧版本不可照搬命令。读取同版客户文档 NEW_INSTALL_CAPABILITIES.md。

1. 从可信固定 Release 核验归档后，执行包内 `bash bootstrap.sh --check`；支持 Ubuntu 24.04 x86_64 root/systemd。已有 Docker CE 复用插件，不擅自卸载更换。授权安装后 `bash bootstrap.sh --apply` 准备系统依赖，可附带 start.py 参数继续。软件源或磁盘失败需解决实际原因，不能返回安装成功。
2. Agent 不具备私有人工 TTY 时，使用 start.py 的 `--browser-input`（默认8792）启动同一 Setup。SSH 只绑定操作者电脑的127.0.0.1，同端口转发到确切服务器127.0.0.1。把一次性 URL 交给用户，用户自行在浏览器输入；不要用浏览器工具读取秘密字段、截图或代填密钥。普通非秘密字段可在 setup --json 中提前完成。
3. Setup 需要飞书授权时，继续准备连接页端口隧道（默认8791）。用户完成后回读绑定，不能把开窗当成完成授权。
4. 常驻员工入口需要域名、解析、有效证书和私钥私有文件。使用 `workspace plan --stack-root … --project … --origin https://… --certificate … --private-key …`，再把返回哈希传入同参数 `workspace apply --expect-plan …`。实际服务是否运行用 workspace status；实际 HTTPS 和用户权限分别验证。
5. 证书续期由客户或实施方负责，续期后重新 plan/apply。冷备份停员工入口；升级或恢复后必须在新 stack 再 plan/apply，旧工作区配置不能自动使用旧目录或旧镜像。更新交接记录中的实际URL、证书到期责任人和恢复状态。

浏览器输入解决安全输入通道，不代表 Agent 可以接管用户身份。常驻员工入口也不意味着已经连接完整自动视频生产。

视频调度只使用 approved_execution 项目凭据和 dispatch-template。每任务的已审脚本、素材哈希及获授权人员的单次生成确认均是执行前提；不要将 queue_read 凭据换成管理员 Token，也不要给 n8n 提供通用管理凭据。未知提交保持待核对。

完成工作区后，使用同版 `production-setup --stack-root … --session … --browser-input` 引导用户在私有页面填写脚本/视频 API Key。Agent 先上传本项目实际 SKU 参考图到客户 stack 的 data/worker，使用10001所有者与私有权限，计算真实 SHA256 并准备素材映射，通过 production-setup 的 --video-assets 私有文件参数交给向导，让用户只核对 SKU 而不粘贴 JSON。用户不必自行编写JSON或记CLI；素材路径与规格是非秘密配置，可由Agent准备，Key必须用户本人输入。

脚本路线当前固定 DeepSeek Flash，视频固定 MiniMax H3；未实现的 Seedance/语音路线不能假装可选。向导生成项目级 n8n 流程并回读；启用调度会重启本客户 n8n，必须告知影响并遵循当前授权。调度授权有效90天，需要记录截止时间和续期责任。备份恢复后重新授权，绝不能恢复旧的未用付费权限。

第一次任务由员工选择SKU与需求，在页面确认脚本请求；生成后可以退回并生成下一版。通过脚本审核后，核对视频计划与费用账户，再授权单次H3生成。视频审核通过才显示交付下载。遇到提交未知先查回执，不用删除重建绕过一次提交限制。

a32 候选补充：若实际包支持 workspace-acme，按同包 WORKSPACE_TLS.md 引导 Certbot 准备、计划确认、测试签发、正式签发和续期监控；不要求客户手抄命令。此能力未发行到 a31，不用候选源码替换已固定的客户发行包。
