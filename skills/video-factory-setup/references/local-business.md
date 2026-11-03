# 本机业务 Setup 候选

这是源码候选，不在 a41 中。先核对安装的核心含 `vfctl local-setup`；不能只更新本 Skill 就声称客户已获得新功能。

1. 使用同版本核心 wheel 与 `scripts/install_local_kernel.py --mode business --root <全新安装目录> --package <已校验的wheel>`。安装器创建私有环境和 `vf-setup`，不创建假模型数据。
2. 准备新的私有计划文件路径。运行 `<安装目录>/vf-setup --plan <计划文件> --interactive --bind`，按终端问题配置项目、SKU、专用飞书应用、已有测试表和模型。密钥只走隐藏输入；本人在飞书官方页面授权，产品不启动安装网页。
3. Agent 可整理同一非秘密计划 JSON，再用 `--check` 预检。不要把 Key 或密码写入计划。详细字段和限制见[候选指南](https://github.com/ChuluuMGL/video-factory/blob/codex/local-business-setup/docs/product/LOCAL_BUSINESS_SETUP.md)。
4. `--native-plan` 读取原表字段/订阅修改计划。核对表与应用范围，确认没有其他接收器后，才用 `--enable-native <计划摘要> --dedicated-app` 启用。
5. 中断后使用原计划和原目录续接，已保存 Key 自动复用。配置不一致、主密钥丢失或解密失败时停止，不删除账本、不重建来绕过错误。

配置成功、真实绑定、原表审核启用、执行权限、费用审批和实际模型生成分别记录。本向导不调用模型、不发放执行权限、不自动启动调度。脚本模型目前为 DeepSeek，视频为 MiniMax-H3；其他便携 n8n 模型不自动等于本地内核支持。真实飞书闭环、自动启动及独立人员安装仍需验收。
