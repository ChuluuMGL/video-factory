# Video Factory 固定版本发行包

本包交付固定版本 CLI、完整依赖 wheels、哈希锁、Skill 和操作手册。它不包含 Docker 引擎或服务镜像，不是已公开发布的下载地址，也不代表真实客户业务验收。

## 准备与来源核对

客户服务器需 Linux x86_64、Python **3.12** 及 venv/ensurepip、root、CLI 至少 512 MiB 空闲空间。Python 3.11/3.13、ARM/macOS 不属于本发行包目标。服务另需本地 Docker/Compose ≥2.24、至少 4 GiB 内存和空闲磁盘、首次固定镜像获取网络。客户或安装同事先按自身服务器管理流程准备这些前置条件。

从可信交付渠道取得版本、来源提交、归档 SHA256 和 manifest SHA256（`release.json`）。先独立核对归档校验值，再解压、执行包内文件。`SHA256SUMS` 若与归档一起被替换，不能证明发布者身份；当前没有独立签名或公开自动更新服务。

以 root 将已验证归档解压到私有目录；所有上级目录必须由 root 拥有且不允许其他用户写入，禁止符号链接。不要把下载包直接放在公共 `/tmp` 下执行。包解压后不要编辑或增加文件，否则完整性检查会拒绝。 归档固定 root:root 所有权，root 可使用 `tar -xzf` 解压。

## 从完整 Skill 进入

用户阅读 CUSTOMER_GUIDE.md，将完整 Skill 交给 Agent。Agent 获取固定版并校验归档，在客户服务器调用同包 start.py：先安装或复用 CLI，再直接进入 setup-run。

start.py 参数由 Agent 根据实际目标组织：manifest 摘要、独立 CLI prefix、session、stack root；新项目使用 --mode new-project --from-session，续接使用 --mode resume。无私有 TTY 可 --prepare-only，仅准备工具并交接后续步骤，不伪装 Setup 完成。

## 安装器内部入口（由 Skill 操作）

把路径、版本和摘要替换为本次可信交付值：

```sh
python3.12 /root/verified-release/install.py --prefix /opt/vf-cli-VERSION --manifest-sha256 TRUSTED_MANIFEST_SHA256
```

安装器在私有独立前缀中保留该包、创建虚拟环境、按哈希离线安装并校验实际 `vfctl --version` 与依赖。不会覆盖全局 Python 或已有版本，不访问模型、不问密码、不启动服务。成功 JSON 返回 `cli_installed`、CLI/Skill 路径和下一命令。

同一发行包重复执行会核验已安装文件并复用，不重新安装。目标非空、发行变更或已安装文件变化则拒绝；安装中断留下的部分目录保留用于诊断，改用新的空前缀，不自动删除任何目录。CLI、session 和客户 stack 分开放置，因此 CLI 安装不会覆盖业务配置。

## 配置与启动服务

客户先创建 `/root/vf-private` 为 0700，再在私有真实终端使用回执中的 `next_command_example`（替换成自己的项目路径）。向导需要人实际输入密码/App Secret；不要粘贴到 Agent 聊天中。

在 SKU 问题前，将已安装版本目录中的 `release/templates/products.json` 复制到 `/root/vf-private/products.json`，改成自己的商品资料；保留 `sku_id`、`name`、`variant`、`truth_source` 四个字段。模板只有虚构样例，不是业务资料。填写问题时输入副本的绝对路径，勿修改发行包内的原文件，否则重复安装校验会拒绝。

安装同事可使用包内 `docs/product/OPERATOR_ACCEPTANCE.md` 逐项记录结果。统一向导默认新建 Base，也支持绑定已有 Base。新建模式无需提前准备表或字段 ID。

详见 发行包中的 `docs/product/SETUP_RUN_USAGE.md`：欢迎选项 → 安装计划确认 → 管理员 → 加密应用凭据 → 飞书授权 → 员工入口。支持新建与绑定 Base；个人身份和权限必须实际验证。`setup-run` 可通过同一 session 续接；同客户新项目可复用同一服务。

模型 API Key 的引用与模型选项仍属于配置，付费生成必须单独按任务授权；安装完成不自动生成视频。当前常驻 HTTPS、真实客户/员工接管、完整自动业务调度尚未验收。

## Skill 与维护

包内 `skill/video-factory-setup/` 可由客户复制到所用 Agent 支持的自定义 Skill 目录；它随包提供，不会自动写入安装同事电脑或客户 Agent 设置。先检查同名 Skill 并保留旧版本。Skill 不依赖某一家 Agent 才能运行，普通操作员可以直接运行 CLI。

升级 CLI 使用新版本目录；升级运行服务另按 发行包中的 `docs/product/STACK_USAGE.md` 创建新部署与恢复检查点。冷备会停服务，不会自动启动；新安装 CLI、备份文件存在、绿色 CI，都不能替代服务回读或实际恢复。

服务镜像无法下载时，使用随包 `docs/product/OFFLINE_IMAGES.md` 的可信离线镜像包，并在 `setup-run` 增加 `--image-bundle` 与 `--image-manifest-sha256`。不要绕过镜像校验或切换未知镜像源。

a31 公开包包含项目自身 wheel 和固定依赖下载清单。首次安装由同一入口从 files.pythonhosted.org 获取外部 wheel 并逐一校验 SHA-256；服务器也需能访问服务镜像源。已完整获取的依赖可离线复用，不重复下载。
