# a28 安装补齐路径

仅当固定发行包包含 bootstrap.sh、CLI 支持 --browser-input/workspace 时使用本页；旧版本不可照搬命令。读取同版客户文档 NEW_INSTALL_CAPABILITIES.md。

1. 从可信固定 Release 核验归档后，执行包内 `bash bootstrap.sh --check`；支持 Ubuntu 24.04 x86_64 root/systemd。已有 Docker CE 复用插件，不擅自卸载更换。授权安装后 `bash bootstrap.sh --apply` 准备系统依赖，可附带 start.py 参数继续。软件源或磁盘失败需解决实际原因，不能返回安装成功。
2. Agent 不具备私有人工 TTY 时，使用 start.py 的 `--browser-input`（默认8792）启动同一 Setup。SSH 只绑定操作者电脑的127.0.0.1，同端口转发到确切服务器127.0.0.1。把一次性 URL 交给用户，用户自行在浏览器输入；不要用浏览器工具读取秘密字段、截图或代填密钥。普通非秘密字段可在 setup --json 中提前完成。
3. Setup 需要飞书授权时，继续准备连接页端口隧道（默认8791）。用户完成后回读绑定，不能把开窗当成完成授权。
4. 常驻员工入口需要域名、解析、有效证书和私钥私有文件。使用 `workspace plan --stack-root … --project … --origin https://… --certificate … --private-key …`，再把返回哈希传入同参数 `workspace apply --expect-plan …`。实际服务是否运行用 workspace status；实际 HTTPS 和用户权限分别验证。
5. 证书续期由客户或实施方负责，续期后重新 plan/apply。冷备份停员工入口；升级或恢复后必须在新 stack 再 plan/apply，旧工作区配置不能自动使用旧目录或旧镜像。更新交接记录中的实际URL、证书到期责任人和恢复状态。

浏览器输入解决安全输入通道，不代表 Agent 可以接管用户身份。常驻员工入口也不意味着已经连接完整自动视频生产。

视频调度只使用 approved_execution 项目凭据和 dispatch-template。每任务的已审脚本、素材哈希及管理员单次生成授权均是执行前提；不要将 queue_read 凭据换成管理员 Token，也不要给 n8n 提供通用管理凭据。未知提交保持待核对。
