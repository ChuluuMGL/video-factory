# 按任务执行的 H3 worker 候选版

0.1.0a12 将执行逻辑从固定主机、固定四项目的脚本移到共享 SQLite/PostgreSQL 账本。它支持管理员明确批准的一条任务版本，具备提交、同 ID 查询、下载、完整解码和进入人工审核的路径。当前不是自动生产调度器；n8n 业务路线和飞书身份仍未接通，尚无这一新入口的真实供应商验收。

## 运行条件与范围

在客户自己的运行主机使用已安装的 vfctl，并准备 ffmpeg、ffprobe、管理员 token、秘密主密钥和私有素材/媒体目录。SQLite 使用原生 runtime 数据目录；PostgreSQL 使用同一账本接口及私有 `VF_DATABASE_URL_FILE`，必须从有数据库连接权限的环境运行。

a12 的容器不含 FFmpeg、没有生产出站路径。a13 增加了下文的 `stack-worker` 入口和容器工具链；原生 CLI 仍需本节列出的主机依赖。普通安装/升级不自动启动 worker 或出站中继。两种入口的真实供应商验收都要单独进行。

本候选只实现 MiniMax-H3、9:16、768P、4–15 秒、1–9 张本地图片参考。脚本直接使用该任务版本已经人工批准的 script，不临时改写。引用图片逐项验证路径和 SHA256；目前未实现完整图片尺寸/内容审核。Seedance、自动写脚本、语音克隆和视频/音频参考尚未加入。

## 凭据与明确任务

先使用 `runtime secret-set` 将客户的**原始 API Key（不带 Bearer 前缀）**加密保存为 `secret:alias`。worker 从本客户 vault 解析，不读取其他项目目录或历史四样本 Key。管理员的业务标签 `billing_owner` 是声明，不代表 API 已验证账户归属或精确价格。

项目与任务先经已有管理 API 创建，脚本审核通过后状态必须是 `ready`。任务身份由 project/task/revision 组成；相同 task ID 可以属于不同项目。模型提交许可独立于员工的脚本审核。

素材描述文件示例：

```json
{"duration":5,"references":[{"path":"reference.png","sha256":"REPLACE_WITH_ACTUAL_SHA256"}]}
```

prepare 不接触供应商，返回准确请求计划哈希，包含部署、任务版本、配置/脚本摘要、素材摘要、固定模型参数、区域、凭据引用及其修订、费用账户标签和单次提交上限：

```sh
vfctl worker prepare --root /absolute/runtime --token-file /absolute/private/admin.token --project brand --task task1 --revision 1 --assets-root /absolute/private/assets --specification /absolute/private/request.json --credential-ref secret:customer_h3 --billing-owner customer_account --region global
```

核对后以相同输入运行 `worker approve`，增加 `--expect-plan RETURNED_REQUEST_PLAN_SHA256`。批准只写入账本，不调用 API，有效期一小时；计划变化需重新核对。凭据修订改变会阻断旧许可。允许更新同一条尚未提交的许可；一旦提交意图已保存，就不能重新批准来绕过重复提交保护。

## 提交、查询和验收

```sh
vfctl worker step --root /absolute/runtime --token-file /absolute/private/admin.token --project brand --task task1 --revision 1 --master-key-file /absolute/private/master.key --media-root /absolute/private/media --allow-paid-submit
vfctl worker status --root /absolute/runtime --token-file /absolute/private/admin.token --project brand --task task1 --revision 1
```

`--allow-paid-submit` 仅允许已经批准的 ready 任务进行一次提交，不是开放全部任务，也不是可执行费用估算。查询同一任务仍用 `step`，可省略该开关。每次 step 最多提交或查询一次，不启动后台无限轮询，不自动选择备用模型。

提交前在数据库事务中保存 `submission_unknown`，并校验许可期限、版本和凭据修订。并发触发只能一个进程获得提交权。超时/断线/不明响应保留 unknown，不自动重发；如果 ID 已拿到但任务绑定中断，独立保存的供应商回执可在下一次 step 恢复。没有 ID 的未知请求必须人工核对，当前不提供自动猜测匹配。

明确拒绝或供应商失败记为 failed；之后可显式建立新任务修订，但仍需脚本审核和新的单次批准。查询失败不会退回 ready。已提交后更换配置或凭据会阻断后续处理，应保留原回执并人工核对，不通过新建任务掩盖旧的未知提交。

供应商成功后，仅从批准的 HTTPS 媒体域名下载，不向媒体地址发送 API Key，不跟随重定向。文件写入私有临时路径，检查音视频流、时长、竖屏比例，并以 ffmpeg 完整解码，再计算 SHA256。该检查不评价创意、人物/产品准确性或商业可用性，也不替代完整分辨率/编码质量门槛。通过后进入 `awaiting_video_review`，保留人工审核；坏媒体停留在 submitted，可修复下载/解码问题后继续，不重新生成。

恢复/迁移旧账本、升级候选或管理员恢复时，会撤销原有 worker 提交许可，但保留供应商回执和任务状态。恢复后若要为 ready 任务重新批准，须先核对备份时间之后是否已有供应商受理；从旧快照本身无法推断所有后续外部调用。未知提交仍不会自动重发。

## 云端证据边界

新增测试覆盖并发、超时未知、进程重启、凭据/素材/配置变化、供应商 ID 串用、失败后新修订和损坏媒体。云端 loopback fixture 使用真正 HTTP 和 FFmpeg、多个独立进程；PostgreSQL 使用真实数据库与模拟供应商检查并发提交。所有这些都没有真实付费请求；对应代码提交的 Actions 才是通过依据。

适配契约参考 MiniMax 官方 [创建任务](https://platform.minimax.io/docs/api-reference/video-generation-v2-create) 和 [查询任务](https://platform.minimax.io/docs/api-reference/video-generation-v2-query)。当前采用白名单区域地址，不接受任意供应商 URL；客户网络可达性和 CDN 差异需在后续真实联调中确认。

## a13 客户容器入口（候选，以当前提交云端 CI 为准）

`vfctl stack-worker` 在客户 Linux 主机运行，复用该客户的 PostgreSQL、密钥保险库和媒体目录。原生 `vfctl worker` 保留。新安装采用部署格式 2；格式 1 必须显式 `stack upgrade`，不会因 `stack up`、查看状态或恢复备份而自动换格式。

容器带有按 digest 固定的 FFmpeg/FFprobe 9.0.2，来源为 [static-ffmpeg](https://github.com/wader/static-ffmpeg)，构建及依赖说明随镜像保存在 `/usr/local/share/ffmpeg-doc`。产品镜像的 pip 构建仍禁网；镜像获取是安装/升级中的显式步骤。

准备已审核任务、保险库中的模型密钥、当前管理员 token 后，把输入放在 `<部署目录>/data/worker`。此目录以只读方式挂载为 `/work`，目录和私密 token 必须归容器 UID/GID `10001:10001`，目录权限 `700`、token `600`。素材目录也必须 `700`。不要把 token、原始 API key 或实际服务器配置放进 Git。

例如，部署目录 `/srv/video-factory` 下有 `data/worker/admin.token`、`data/worker/assets/reference.png` 和 `data/worker/specification.json`：

```bash
vfctl stack-worker prepare --stack-root /srv/video-factory \
  --token-file /work/admin.token --project brand --task task-001 --revision 1 \
  --assets-root /work/assets --specification /work/specification.json \
  --credential-ref secret:h3 --billing-owner customer --region global
```

核对输出的 `request_plan_sha256` 后，用相同参数将 `prepare` 改成 `approve`，增加 `--expect-plan <核对后的摘要>`。这两步不启动出站服务、不调用模型。首次付费提交必须再执行：

```bash
vfctl stack-worker step --stack-root /srv/video-factory \
  --token-file /work/admin.token --project brand --task task-001 --revision 1 \
  --allow-paid-submit
```

后续同一任务使用 `step` 查询/下载，可省略付费提交开关。`status` 仅查询本地账本。没有授权、材料变动、密钥轮换、审批过期会拒绝提交；结果不确定时先查询 `status`，禁止自行重试新任务。恢复或升级会撤销旧提交授权和旧登录，需要重新登录、重新核对尚未提交的任务。

供应商明确拒绝时，任务保持 `failed`，`step` 和 `status` 返回固定的 `failure_code`，例如 `PROVIDER_AUTH_REJECTED_CHECK_REGION_OR_KEY`。它只说明错误类别，不保存供应商响应文本或密钥；不会自动重发。先核对 Key 所属的 MiniMax 接口区域：`api.minimaxi.com` 对应 `cn`，`api.minimax.io` 对应 `global`，两者不能凭服务器所在地推断。修正配置后需要从原飞书记录导入新来源版本，重新由真人审核脚本和逐条批准模型请求；旧失败版本保留。

网络边界：worker 仅加入内部网络，经临时 CONNECT 中继连接固定 API/素材主机的 HTTPS 443。a14 的独立飞书连接器另允许 open.feishu.cn；模型客户端不使用该目的地。中继拒绝未列入域名、其他端口及解析出的非公网地址；按已校验的 IP 建立连接，避免 DNS 二次解析。TLS 由 worker 验证目标证书，中继不解密密钥，也没有客户目录/密钥挂载。普通 runtime/n8n 继续没有外网通路。

中继仅在显式 `step` 时拉起，结束/错误后停止；如果主机端命令被强制终止，中继最多存活十分钟且不自动重启。它不是任务调度器，不会自动提交积压任务。一次性 worker 自身也有七分钟硬时限；主机命令返回或超时时会清理本次唯一名称的容器。整个调用与备份/升级共用部署锁。

验收边界：云端测试使用合成凭据、隔离 HTTP/TLS 服务和合成视频；真实 H3、真实飞书员工操作、n8n 业务调度仍需分别验收。当前仍需管理员准备任务/token/素材，这部分尚未收敛为面向普通客户的一步 Setup。
## 认证失败后更换密钥

当状态为 `failed`、错误为 `PROVIDER_AUTH_REJECTED_CHECK_REGION_OR_KEY`，且没有供应商任务回执时，先通过私有终端更新项目密钥或接口区域。管理员可执行 `stack-worker recover-auth`，传入原项目、任务、版本、旧 `--expect-plan`，以及更新后的 `--credential-ref` 和 `--region`。

此命令保留原脚本和人工审核记录，将失败尝试归档并撤销旧付费许可；不会调用模型。随后重新执行 `prepare`、核对计划和 `approve`，获得付费重试授权后才执行 `step --allow-paid-submit`。结果未知、已有供应商回执或其他生成失败均不能使用此命令恢复。
