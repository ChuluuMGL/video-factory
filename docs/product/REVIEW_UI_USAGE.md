# 限时员工审核入口（a16 Alpha）

这是客户自己服务器上的审核窗口，由管理员显式启动。员工打开页面后，用自己的飞书账号授权，查看本项目脚本、视频和审核历史，先核对操作再提交通过或退回意见。导入权限和脚本/视频审核权限沿用 `setup-feishu` 的成员绑定。

目前每次最多 360 秒，通过同端口 SSH 隧道访问。窗口结束后重新开启并授权；没有常驻公网入口、刷新授权或中央客户账户服务。这是验证员工流程的 Alpha 入口，不能作为已完成的日常团队交付方案。

## 启动前

1. 客户基础 stack、项目/SKU 导入及 [飞书成员绑定](SETUP_FEISHU_USAGE.md) 已完成，恢复后的绑定已重新核对。
2. 使用该客户的飞书自建应用 App ID 和 App Secret。管理员在飞书侧确认应用可用范围及用户授权权限；代码请求 `bitable:app:readonly`、`contact:user.base:readonly`，不请求 `offline_access`。真实租户是否允许本应用的设备授权仍需现场验证。
3. App Secret 存在客户服务器私有文件中，不放命令行参数、Setup JSON、GitHub、浏览器或日志。容器方式把该文件放进 stack 的 `data/worker/`，文件属主为容器 UID 10001、权限 0600；目录沿用安装器的私有权限。准备凭据文件属于客户管理员操作。

## 容器安装的开启方式

在客户服务器运行（替换目录、项目和应用 ID）：

```sh
vfctl stack-review --stack-root /srv/video-factory/customer \
  --project brand --app-id cli_CUSTOMER_APP_ID \
  --app-secret-file /work/review-app-secret --port 8790 --seconds 360
```

保持该命令运行。只有返回 `status=ready` 后入口才可用。管理员终端中的 URL 为 `http://127.0.0.1:8790`；远程员工在自己电脑另开 SSH 隧道：

```sh
ssh -N -L 127.0.0.1:8790:127.0.0.1:8790 user@customer-host
```

随后用浏览器打开 `http://127.0.0.1:8790`。本地和服务器端口必须一致，不能改成 `localhost` 或公网域名。端口占用时，在两处一起换端口。SSH 访问由客户自己管理；员工 SSH 授权分发与更长期的 HTTPS 访问方案尚待完善。

命令持有 stack 运维锁，避免与升级、恢复或其他一次性任务并发。正常到期或处理异常时，清理本次容器并关闭临时出站中继。进程被强杀或主机故障时不能声称立即清理成功；容器还有 420 秒硬截止、中继有 600 秒截止，之后应通过 `vfctl stack status` 和宿主机容器/监听端口回读确认。

容器保持在原有私有网络中。宿主机入口只绑定回环地址，并只转发至本次容器在该部署私有网络中的实际地址；不让带有凭据的 worker 加入有外网路由的管理网络。退出时关闭回环监听及本次容器。

原生安装可在服务器运行 `vfctl review-ui --root /private/runtime --media-root /private/media --project brand --app-id cli_CUSTOMER_APP_ID --app-secret-file /private/app-secret --port 8790 --seconds 360`。同样仅监听回环地址，不需要新服务器。

## 员工流程

点击“连接飞书”，打开飞书授权链接，核对应用及授权码并授权，返回页面等待结果。服务端通过实时 `tenant_key/open_id` 和本项目成员列表验证身份；浏览器只取得随机会话 cookie，用户 token 仅保留在服务端本次进程内存，不保存 refresh token。

选择任务后查看固定版本脚本和历史。如果处于视频审核阶段，先播放并人工检查视频；页面显示与账本绑定的 SHA256，读取媒体时验证私有文件路径和实际摘要。技术播放成功不等于视频内容通过人工验收。

云端播放检查使用支持 H.264 的正式 Google Chrome，并记录实际版本；其他浏览器、Safari 和真实手机尚未验收。若播放器提示失败，先处理格式或文件读取问题，不要直接通过审核。[Playwright 的媒体解码器说明](https://playwright.dev/docs/browsers#media-codecs)解释了测试内核与正式浏览器的区别。

选择通过或退回；退回必须写具体意见。点“核对审核”后再点“确认提交”。来源、角色、版本或计划发生变化会拒绝，需要重新核对。重复提交同一确认不会增加审核事件。

具有导入权限的员工可填写指定飞书记录 ID。返工先关闭旧版，在飞书修改脚本和来源版本，再以旧版号导入新修订；页面不会替用户写回飞书。旧意见保留，新版不会继承旧版的付费许可。

审核页面没有模型生成、管理员配置或付费按钮。脚本通过后仍由已存在的逐任务 worker 授权流程决定是否生成。

## 验证范围和仍待完成

云端检查使用官方结构的模拟 OAuth/飞书 HTTP 服务、真实安装包、Google Chrome、数据库与 FFmpeg；浏览器授权动作由测试 fixture 模拟，不能据此宣称真实飞书登录成功。实际容器启动测试不提交授权请求，也不调用模型。

真实租户授权、真实员工操作、常驻 HTTPS 入口、凭据录入向导贯通、自动建表/写回、意见驱动脚本修复、正式安装页和分发 Skill 仍是后续交付项。

设备授权协议参考飞书官方 CLI 的 [设备授权实现](https://github.com/larksuite/cli/blob/main/internal/auth/device_flow.go) 和 [端点定义](https://github.com/larksuite/cli/blob/main/internal/auth/paths.go)。产品固定调用官方域名，不提供用户可配置的 OAuth 代理目标。


a18 可从 [setup-run 统一向导](SETUP_RUN_USAGE.md) 在绑定回读后直接选择启动员工窗口，使用已保存在本机加密 vault 的应用凭据。独立窗口命令也接受 `--app-secret-ref secret:实际别名`，与 `--app-secret-file` 二选一；容器固定使用本部署主密钥，原生命令则必须额外提供私有 `--master-key-file`。这些参数是引用/文件路径，不接收原始密钥值。
