# 工作区证书轮换候选

此分支补齐证书管理中的安全轮换部分，尚未发行到 a31。下文 ACME 签发与定时续期是 a32 候选；真实域名验证仍属 #10 待办，不因为代码存在就宣称验收完成。

现有工作区的证书临近 30 天到期时，`workspace-tls status` 返回续期提示。Agent 使用固定 CLI 的 `workspace-tls plan` 核对项目、域名、当前证书及新证书，再用同一计划的摘要执行 `apply`；参数为 `--stack-root`、`--project`、`--certificate`、`--private-key`、`--expect-plan`。输入证书和私钥必须留在客户服务器的私有文件中。

新证书必须与域名 SAN、私钥公钥匹配，且至少还有七天有效期。证书和私钥写入新的一组私有文件，通过一次符号链接切换成对替换，保留旧版本。只校验并重载该项目的 nginx；不重启 n8n、任务执行器或其他项目。

重载后检查本机实际提供的新证书指纹。失败则恢复旧证书并再次读取；回退失败返回 `needs_attention`。进程中断的状态保留在客户本地私有回执中，可使用 `workspace-tls recover` 恢复上次证书。更换服务器实例或版本后必须重新核对，不能沿用旧轮换计划。

本机指纹验证不等于公网域名、可信 CA、浏览器 HTTPS 已通过；这些仍需在真实域名上独立验证。不会读取或替换官网、其他域名的证书，不上传任何私钥到 GitHub。

签发方案依据 [Certbot 官方指南](https://eff-certbot.readthedocs.io/en/stable/using.html)：候选采用隔离的配置目录，先测试 ACME 环境，再对已确认测试域名操作；不能复用系统其他站点的证书目录或自动改写全局 nginx 配置。

更换后的证书代次包含在加密冷备中；恢复只允许工作区自身的固定证书指针，不接受其他符号链接或越界路径。未完成的轮换必须先恢复，不能用下一次更换覆盖故障回执。云端回归包含实际 nginx 热重载、回退、新证书的可信连接、服务不重启以及证书代次冷备恢复。此检查仍不代表公网 ACME 验收。

## a32 ACME 候选（未发行）

候选 CLI 增加 `workspace-acme`。公开 a31 仍需实施方提供并维护证书；安装页继续指向 a31，不能在旧包上运行下面的流程。来源、目标服务器和同版 `vfctl --help` 必须先核对。

前提：Ubuntu 主机已安装发行版提供的 Certbot（`/usr/bin/certbot`，root 所有且不可被其他用户改写），独立域名直接解析到本机，入站 80 可达且没有其他服务占用。按客户授权在目标主机安装 `apt-get install certbot`；这一步不由普通 CLI 安装暗中执行。HTTP-01 不要求 Cloudflare API Token。代理、现有网站占用 80 或 DNS 不符时先解决部署条件，不停止其他网站或绕过检查。

Agent 操作顺序：

1. `workspace-acme plan --stack-root <私有目录> --project <项目> --origin https://<域名> --email <证书负责人邮箱> --expected-ip <本机公网IP> --environment staging`。仅支持已确认的公网 IPv4；所有解析必须与计划一致，有 AAAA 时先核对部署，不会忽略 IPv6 指向。计划绑定当前服务器实例、域名及 Certbot 文件摘要。
2. 客户已同意 CA 服务条款后，使用相同参数执行 `issue --accept-ca-terms --expect-plan <刚返回的摘要>`。先成功完成 staging，再重新生成 production 计划并执行一次签发。测试证书不能部署为正式员工入口。
3. `workspace-acme deploy --stack-root … --project …`。首次创建工作区；已有入口使用原子 TLS 轮换和失败回退。随后分别检查工作区状态和公网可信 HTTPS。
4. `workspace-acme enable --stack-root … --project …` 创建仅属于这个 stack/项目的 systemd 定时任务，每日检查，分散执行。到期不足 30 天才签发；已签发但未部署的证书先复用。停用用 `disable`，不影响其他站点的任务。
5. `workspace-acme status --stack-root … --project …` 查看签发、计划上下文、定时器是否活跃和最近一次续期结果。systemd 服务失败与状态中的 `failed` 都须由实施方监控；候选尚无邮件/飞书推送告警。

超时或签发失败会保留 `in_flight` 回执，定时器不得再次提交。使用同一环境的 `recover` 只读取那次私有签发输出；若 CA 未完成签发，保持待核对，由实施方检查原日志和 CA 状态，不能删回执自动重试。续期失败保留正在使用的旧证书；轮换故障另用 `workspace-tls recover`。

Certbot 的临时日志和符号链接只在 `acme-work`，不进入冷备。账户、证书、私钥规范化成私有普通文件后进入现有加密冷备。恢复到新实例后禁止直接续期，必须重新核对域名、计划及定时任务；备份不会搬运系统定时器。升级前先用旧版 CLI 停用旧定时器，升级后核对新版 CLI 路径再启用；不会覆盖内容不同的系统 unit。

云端测试使用 [Pebble 测试 CA](https://github.com/letsencrypt/pebble)，验证实际 Certbot 的 HTTP-01、账户复用和中断恢复；它不能替代真实域名的 Let's Encrypt staging / production 验收。真实域名签发、实际续期和公网访问完成前，#10 保持打开。
