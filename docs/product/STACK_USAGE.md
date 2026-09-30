# 完整运行环境：安装、迁移、备份和产品升级

0.1.0a13 开发版。部署包含产品服务、n8n、PostgreSQL 和仅供 SSH 转发访问的 Nginx 入口。不是中央客户管理平台，每个客户在自己的 Linux 主机上持有数据、账号和密钥。

容器逐任务执行入口与出站边界见 [worker 使用说明](WORKER_USAGE.md)。普通 `stack up` 不启动 worker、不调用模型。

## 已实现的安装入口

先在客户 Linux x86_64 主机准备 Docker Engine、Docker Compose >=2.24、Python >=3.11 的管理 CLI，以及来自受信任构建的完整 wheelhouse。至少 4 GiB 内存和 4 GiB 可用磁盘；容量阈值是启动下限，后续媒体需要额外容量。容器内 Python 固定为 3.12.14，CLI 的依赖 wheel 必须匹配管理主机 Python ABI。

```sh
sudo vfctl stack install --root /opt/video-factory --deployment customer-a --wheelhouse /absolute/verified-release/wheels
```

命令检查本机 Docker、平台、容量、端口，然后隐藏输入产品管理员密码，生成配置和凭据、获取固定摘要镜像、构建产品镜像并启动组件。再次运行同一命令继续同一安装，必须匹配原部署 ID 和 wheel 文件摘要；不会变成浮动更新。错误主机/版本变化必须通过单独升级处理。无 TTY 可用 `--password-file /absolute/private/file`，文件只能由当前管理员读取，不把密码放命令行。

支持预先审阅和逐步执行：

```sh
sudo mkdir -m 700 /opt/video-factory
sudo vfctl stack preflight --root /opt/video-factory
sudo vfctl stack prepare --root /opt/video-factory --deployment customer-a --wheelhouse /absolute/verified-release/wheels
sudo vfctl stack fetch --root /opt/video-factory
sudo vfctl stack build --root /opt/video-factory
sudo vfctl stack up --root /opt/video-factory
sudo vfctl stack status --root /opt/video-factory
```

`prepare` 原子提交新目录，失败不留下半套客户配置；`fetch` 只获取固定摘要镜像；构建中的 pip 禁用网络并只使用已校验 wheel。重试安装可以修复中断后尚未重写的派生 Compose 文件，但其他管理命令会拒绝被手工改写的 Compose，防止执行不明挂载或特权配置。

只支持本机 root 管理的 Docker socket，不接受远程 Docker context。安装目录是 root 私有目录；容器内产品用户为 10001、n8n 用户为 1000，各自只获得所需目录和秘密。Docker 管理员仍是可信权限边界。

## 访问与账号

主机仅监听 127.0.0.1:8787（产品管理 API）和 127.0.0.1:5678（n8n 管理页面），可通过安装参数改端口。管理员在自己电脑使用 SSH 本地转发后访问；默认不开放公网端口。n8n 首个 owner 仍由客户在 n8n 自带欢迎页面创建，与产品管理员、SSH 账号分开保存。

```sh
ssh -L 8787:127.0.0.1:8787 -L 5678:127.0.0.1:5678 customer-server
```

产品、n8n 和数据库位于禁止出站的内部网络。无凭据的固定代理连接管理网络，负责把本机请求转发到这两个固定服务；它不是任意转发代理。产品和 n8n 的连接数据库分别为 `vf_runtime` 和 `vf_n8n`，使用不同角色；n8n 角色没有连接产品数据库的权限。

当前 n8n 可执行内部路由，产品付费模型适配、飞书连接仍未启用。`infrastructure_ready` 同时检查容器健康和主机实际入口；它不表示业务或真人验收通过。设置为默认离线的原因是尚未接入客户业务凭据，不能把基础环境启动误当作生产启用。

## 数据与密钥

`data/` 包含 PostgreSQL 数据、产品状态、n8n 状态和媒体目录。`secrets/` 保存数据库密码、n8n 加密材料、产品秘密主密钥等；Compose 和安装清单只保存引用。产品首次初始化完成后移除引导密码文件，数据库只保留密码哈希。

`runtime backup/restore` 仍只适用于旧 SQLite 账本。PostgreSQL 部署使用下述完整 stack 备份，避免遗漏 n8n、媒体或密钥。

## 停机备份与完整恢复

```sh
sudo vfctl stack keygen --root /opt/video-factory --output /absolute/private/backup.key
sudo vfctl stack backup --root /opt/video-factory --backup-key-file /absolute/private/backup.key --output /absolute/private/customer.vfb
sudo vfctl stack up --root /opt/video-factory
```

备份会停止组件并验证 PostgreSQL 正常退出，之后保持停止，直到显式 `up`。备份文件含两个数据库、状态目录、媒体、恢复密钥材料和固定 wheel 发行输入，整体加密；**备份解密密钥本身不包含在备份里**，需单独安全保存并保留异地副本。工具不会自动把客户文件上传给维护方。

本开发版限制压缩归档 256 MiB、解包数据 1 GiB。超过时停止并报错，不截断媒体或假装完成；大规模对象存储和自动保留策略仍需后续版本。

恢复到全新私有目录，使用相同固定组件镜像和 Linux amd64；不得覆盖现有部署：

```sh
sudo mkdir -m 700 /opt/video-factory-restored
sudo vfctl stack restore --root /opt/video-factory-restored --source /absolute/private/customer.vfb --backup-key-file /absolute/private/backup.key
sudo vfctl stack fetch --root /opt/video-factory-restored
sudo vfctl stack build --root /opt/video-factory-restored
sudo vfctl stack up --root /opt/video-factory-restored
```

恢复不自动启动。首次启动先撤销产品旧登录会话，再开放入口；未知模型提交状态保持未知。恢复创建独立 Compose 实例标识，同时保留客户部署身份，旧目录和数据不覆盖。不要同时运行原环境和恢复环境的业务处理器。此冷备份不支持跨 PostgreSQL 大版本直接恢复，工具拒绝不同组件锁。

## SQLite 账本迁移

停止旧原生服务，并为目标准备专用空 PostgreSQL 数据库及 owner-only DSN 文件。该操作不在已有数据库上覆盖或合并。

```sh
sudo VF_DATABASE_URL_FILE=/absolute/private/target.dsn vfctl runtime migrate-sqlite --root /absolute/private/target-state --source /absolute/private/old-sqlite-state
```

`--source` 是旧 `runtime.sqlite3` 所在目录。迁移检查 SQLite 完整性，在一个 PostgreSQL 事务中复制配置、任务、审核、密码哈希和加密秘密；旧会话撤销，源文件保留。迁移失败回滚目标建表与数据。迁移后还需带上原秘密主密钥，核对目标台账后再切换服务；不可同时启动两份写入器。

## 产品版本升级与失败恢复

```sh
sudo mkdir -m 700 /opt/video-factory-next
sudo vfctl stack upgrade --root /opt/video-factory --candidate-root /opt/video-factory-next --wheelhouse /absolute/verified-new-release/wheels --backup-key-file /absolute/private/backup.key --output /absolute/private/pre-upgrade.vfb
```

必须从健康源环境开始。命令先停机备份，再恢复到新目录、替换产品 wheel、构建和启动新版本；只有所有组件及主机入口健康才返回 `upgraded`。成功后改用新目录管理，旧目录保持停止且原数据保留。新版本失败则停止候选环境，重新启动旧环境，并仅在回读健康后返回 `rolled_back`（命令返回非零，表示升级没有成功）。若旧环境也无法启动则返回 `needs_attention`。

升级只接受代码内明确支持的旧/新部署模板和镜像锁，数据库 schema 保持不变。a13 支持格式 1→2，为新副本增加固定 FFmpeg 工具链和默认停用的 worker/出站中继；读取、恢复和启动旧备份不会自行换格式。n8n/PostgreSQL 大版本升级和未知数据库迁移会阻断，不自动尝试。升级中连接断开时应分别读取源和候选 `stack status`，核对检查点；不要创建第三份环境或盲目重复升级。

## 云端验证范围

[a10 云端回执](https://github.com/ChuluuMGL/TikTok-Video-Factory/actions/runs/36514514181) 已通过真实 PostgreSQL、SQLite 迁移、n8n 内部工作流、完整冷恢复与凭据解密，以及 a9→a10 升级和损坏候选回退。后续补充的单命令安装/续装检查须以其提交自身回执为准。

这不是飞书真人流程、真实供应商生成、新客户实际主机、物理异机恢复或宿主机重启验收。容器使用 restart 策略，但不能把进程/容器重启当成整机断电演练。

实现参考：[Docker Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/)、[n8n Docker 与 PostgreSQL](https://docs.n8n.io/deploy/host-n8n/install-options/install-with-docker.md)、[psycopg 事务](https://www.psycopg.org/psycopg3/docs/basic/transactions.html)。镜像摘要以 `video_factory/deployment/images.json` 为准。
