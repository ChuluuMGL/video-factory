# a41 查询修复与验收记录

本版本修复便携视频查询的处理中回执。公开可下载版本以 [GitHub Releases](https://github.com/ChuluuMGL/video-factory/releases) 为准；源码合并、固定包发行、项目升级和真实模型验收分别记录。

## 已通过的代码检查

[PR #72](https://github.com/ChuluuMGL/video-factory/pull/72) 的运行源码为 `914ece2f402e906dfa8c2ec7732d0dfc8f8b3b20`，合并提交 `1375f7d71c409b52471e690e83b4e4fe8dea223e` 的树内容一致。

- [四平台实际 n8n](https://github.com/ChuluuMGL/video-factory/actions/runs/39515010164)：Windows、macOS、Ubuntu、Docker 通过；三种原生环境各运行 88 项便携测试。
- [完整云端回归](https://github.com/ChuluuMGL/video-factory/actions/runs/39515010129)：固定包、空白 Ubuntu、PostgreSQL、升级、冷恢复及两种镜像存储模式的断网空缓存安装通过。
- [安装页](https://github.com/ChuluuMGL/video-factory/actions/runs/39515010173)与[隐私检查](https://github.com/ChuluuMGL/video-factory/actions/runs/39515010154)通过。

真实 n8n 复现了旧流程的空结果问题：供应商任务仍在处理中时，执行成功但 webhook 返回 HTTP 500。修复后 queued/running 返回明确 pending 回执，不写飞书、不重复生成。查询 HTTP 错误和任务编号不匹配保留原任务并停止。最初测试发现文本响应字段不匹配，显式指定 `body` 后四平台复测通过。

历史那次现场 500 没有保存原始响应，因此不能断言它一定由空结果导致。使用修复后的节点查询已有 Seedance 任务成功，未重新生成或修改飞书行。

## 可灵单次真实验收

维护者批准一次独立的北京区 `kling-3.0-turbo` 请求：5 秒、720p、有声、比例随首帧。仅提交一次，供应商回执为 HTTP 429、业务码 1102，未返回任务编号。

[官方错误码](https://klingai.com/document-api/api/get-started/error-codes)将 1102 定义为预付费资源包用完或过期。只读任务列表查询可正常鉴权，限定时间范围内没有返回任务；这不替代账单核对，也不能倒推旧失败请求的原因。原回执与锁保留，隔离工作流已关闭，没有自动重试。

维护者于 2026-10-21 明确决定：参考此前项目的使用经验，本轮跳过可灵成片复测，不再作为技术收尾阻塞项。记录为“维护者接受既有经验、本产品本轮实测豁免”，不改写为本次生成成功；不再要求充值、重复输入 Key 或重新提交测试。原失败回执保留，既有 Seedance 视频、Seedream 图片和原已审核任务保持不变。

凭据类型须区分：当前公开便携路线采用单个 API Key；旧版 Access Key / Secret Key 的 JWT 鉴权只完成私有诊断，没有作为本版生成适配器交付。免测不表示两种接口可以混用。

## 发布与待办

[v0.1.0a41](https://github.com/ChuluuMGL/video-factory/releases/tag/v0.1.0a41) 已于 2026-10-21 发布，固定源码 `469cdec88a732bc52fbd98c98bd4056ecae9ca1d`。版本、交付记录和 Skill 排障指引已同步，模型执行源码与上述已验证提交一致。

- [最终固定包检查](https://github.com/ChuluuMGL/video-factory/actions/runs/39519111191)通过，包括升级、冷恢复及两种离线镜像存储模式。
- [发布后匿名安装](https://github.com/ChuluuMGL/video-factory/actions/runs/39521805804)、[页面检查](https://github.com/ChuluuMGL/video-factory/actions/runs/39518526938)及[隐私检查](https://github.com/ChuluuMGL/video-factory/actions/runs/39518526977)通过。页面检查第一次因外部字体参考页面等待超时失败，第二次通过；保留第一次记录。
- [官网同步 PR #45](https://github.com/ChuluuMGL/YUEYUTECH/pull/45)已合并并部署。[公网复测](https://github.com/ChuluuMGL/YUEYUTECH/actions/runs/39522540656)确认 52 个文件摘要、四种屏宽、复制与降级复制、Skill 下载、共用菜单和页脚、语言路由及四个案例视频通过。仅同步产品静态目录，其他页面、运行配置和上传资料保持原状。
- 发行归档 SHA-256：`d867285f63ca34d752c42eeab0d8e1de6047eaf9b25c152ad4a6737ee7b014bd`；内部清单 SHA-256：`35d67eb9440307331312bec7e482619ccff2257eab3e0ebe5bc4684318d9e4c8`。

后续状态文档更新不替换已冻结的 a41 安装包、完整 Skill 或官网交付文件；这些文件保留发行时内容和摘要。最新验收结论以本仓库的[工作清单](COMPLETION_WORK.md)为准。

第二真实账号、独立操作者和设备验收仍按维护者安排放到最后。其他尚无本产品真实调用证据的模型继续标为待验；n8n Cloud SaaS 实例测试继续跳过。私有账号、Base、任务编号、素材与原始回执不进入公开仓库。

## English

The query repair passed actual n8n checks on Windows, macOS, Ubuntu and Docker, plus full server regression, cold restore and offline installation. A pending provider job now returns an explicit receipt without Base writes or another generation. A read-only query of an existing Seedance job also passed. The exact cause of the older live HTTP 500 remains unproven because its response was not retained.

One explicitly authorized Kling request returned HTTP 429 / code 1102, documented as an exhausted or expired prepaid resource pack. No job ID was returned; the receipt and lock remain, and isolated workflows are disabled. There was no automatic retry. This is not a successful Kling video acceptance or a billing clearance. The maintainer explicitly waived further Kling live testing based on prior project experience; it no longer blocks this closeout. The waiver does not claim a successful generation or shipped legacy AK/SK support. The a41 package is published; final packaging, anonymous installation and the official-site 52-file and browser checks passed. Distinct-account, independent-device and other provider acceptance remain separate.
