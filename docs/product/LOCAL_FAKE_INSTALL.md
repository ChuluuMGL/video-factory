# 无 n8n 本地安装候选

本路线用于 macOS/Linux 隔离试用，Python 3.11+ 即可开始。不安装 n8n、Docker、Node.js 或数据库服务；使用独立 Python 环境、SQLite 和现有内置调度器。当前源码候选尚未进入 a41 发行包，不把旧包命令当作新功能。

## 安装与启动

先取得包含本文件的已核对源码候选，在仓库根目录运行一次：

```sh
python3 scripts/install_local_kernel.py --root "$HOME/VideoFactory-Trial"
"$HOME/VideoFactory-Trial/vf-local"
```

首次安装仅从配置的包源获取 Python 依赖，不收集飞书或模型密钥；运行阶段禁止出站网络和启动外部进程。安装到全新私有目录，不修改已有 n8n、系统服务、生产账本或全局 Python。也可用 `--package /absolute/verified.whl` 指定已核对 wheel，并用 `--wheelhouse /absolute/wheels` 完全离线安装。Python 与其 venv 支持由操作者预先准备。

启动后只监听 `127.0.0.1:8788`，保持当前终端运行；Ctrl-C 停止，再运行同一条启动命令恢复。重复启动被目录锁拒绝。安装成功不等于启动；原目录存在时安装器直接拒绝，运行 `vf-local` 恢复，而非重装。失败安装保留 `install.json` 和原件供排查，不自动删目录或覆盖重试。

## 假模型完整脚本循环

另开终端，或交给有本机目录权限的 Codex 顺序执行：

```sh
"$HOME/VideoFactory-Trial/vf-local" plan --request-id trial-plan-1 --brief '展示一个日常使用场景'
"$HOME/VideoFactory-Trial/vf-local" status
"$HOME/VideoFactory-Trial/vf-local" review --task trial_001 --expected-revision 1 --request-id review-1 --decision reject --feedback '减少产品特写'
"$HOME/VideoFactory-Trial/vf-local" plan --task trial_001 --expected-revision 1 --request-id trial-plan-2
"$HOME/VideoFactory-Trial/vf-local" status
"$HOME/VideoFactory-Trial/vf-local" review --task trial_001 --expected-revision 2 --request-id review-2 --decision accept
```

每次 `plan` 后先用 `status` 确认调度器已生成对应版本、状态为 `awaiting_script_review`，再审核。旧版本、重复排队、相同请求号不同内容会拒绝。相同请求号重放不重复生成；修订引用原审核意见，版本与审核记录保留，重启后仍可读取。`accept` 后状态为 `ready`，代表脚本通过；本候选不启动视频生成，不能当作已生成成片或已投产。

本机操作者持有操作系统目录权限，这条 CLI 是安装者/测试者入口，并非员工权限隔离接口。工作台/MCP 的同事修复权限仍由另一套已绑定身份的服务端能力控制，不能将 `owner.password`、整个目录或管理 CLI 交给员工 Agent 来冒充受限权限。私有 owner 密码及只读 Agent token 自动写入 0600 文件，不打印到对话或日志。

## 验收边界

云端 Linux/macOS 测试实际安装 wheel、从新目录初始化、启动真实子进程、锁冲突、排队生成、审核退回、按意见修订、审核通过、停止和重启。业务表、模型输出全部合成；没有真实飞书连接、真实模型、生产迁移或旧 n8n 工作流导入。非作者真人安装、真实同事修复、后台自启、完整升级回退与 Windows 是后续验收项。当前不自动替换公司线上系统。
