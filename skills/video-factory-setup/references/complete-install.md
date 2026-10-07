# CLI 与飞书优先安装路径

核对固定发行包与其能力，不把候选功能当作公开版。产品形态以 `docs/product/PRODUCT_SHAPE.md` 为准；旧版本不可照搬新命令。

1. 从可信固定 Release 核验归档后，执行包内 `bash bootstrap.sh --check`；支持 Ubuntu 24.04 x86_64 root/systemd。已有 Docker CE 复用插件，不擅自卸载更换。授权安装后 `bash bootstrap.sh --apply` 准备系统依赖，可附带 start.py 参数继续。软件源或磁盘失败需解决实际原因，不能返回安装成功。
2. 默认由用户在客户服务器的私有 TTY 中完成 `setup-run` 问答。Agent 确实不能提供私有人工 TTY 时，才用 `start.py --browser-input`（默认 8792）启动同一问答；SSH 只转发操作者电脑与服务器的 127.0.0.1。用户自行输入秘密，Agent 不读取字段、截图或代填。用完关闭监听；不得接入 `video.yueyu.tech` 或其他公开域名。
3. Setup 需要飞书授权时，终端显示飞书官方授权地址与短码；用户在飞书完成本人授权，然后在原终端核对创建或绑定计划并确认。回读真实 Base 绑定，不能把取得授权码当成完成接入。
4. 回读服务与 Base 的真实状态，交接 Base 链接、项目、人员和未执行的验收。按 `FEISHU_BRIDGE.md` 使用本人授权的 `stack-feishu` 导入或审核一条任务；Setup 本身不启动员工网页。
5. 常驻 `workspace` 是已存在的网页候选能力，不是 CLI 安装完成的条件。需要自动调度时，先执行 `vfctl runner plan --stack-root ROOT --project PROJECT`，核对摘要后执行 `runner apply --expect-plan SHA`；它只在 Docker 私网提供执行器。旧 workspace 与 runner 不能在同一项目并行；迁移须单独计划和验收。

浏览器输入只解决安全输入通道，不代表 Agent 可以接管用户身份。

视频调度只使用 approved_execution 项目凭据和 dispatch-template。每任务的已审脚本、素材哈希及获授权人员的单次生成确认均是执行前提；不要将 queue_read 凭据换成管理员 Token，也不要给 n8n 提供通用管理凭据。未知提交保持待核对。

模型配置和付费生成是安装后的独立验收。`production-setup` 在 runner 健康后可导入项目级 n8n 调度，默认停用；保存 API Key 或运行向导不代表真实生成通过。Agent 只在用户确认测试对象和费用后准备实际 SKU 素材、核对模型账户并进行受控测试；Key 仍由用户在私有输入通道填写。

脚本路线当前固定 DeepSeek Flash，视频固定 MiniMax H3；未实现的 Seedance/语音路线不能假装可选。无网页的 runner 与 n8n 调度仍须云端实测，未通过前不得承诺自动推进。备份恢复后旧付费授权不得复用。

第一次任务从 Base 的真实记录开始，逐条核对来源、版本和操作者身份。生成、退回、单次视频授权与成片审核分别留回执；缺少飞书内交互或 Base 回写时明确记录，不用网页候选的成功冒充该闭环。遇到提交未知先查回执，不删除重建绕过限制。
