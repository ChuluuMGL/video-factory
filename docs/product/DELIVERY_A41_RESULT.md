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

后续状态文档更新不替换已冻结的 a41 安装包、完整 Skill 或官网交付文件；这些文件保留发行时内容和摘要。最新验收结论以本仓库的[工作清单](https://github.com/ChuluuMGL/video-factory/blob/main/docs/product/COMPLETION_WORK.md)为准。

第二真实账号、独立操作者和设备验收仍按维护者安排放到最后。其他尚无本产品真实调用证据的模型继续标为待验；n8n Cloud SaaS 实例测试继续跳过。私有账号、Base、任务编号、素材与原始回执不进入公开仓库。

## 2026-10-21 真实结果快照故障验收

使用已发布 a41 核心，在原隔离测试机内新建独立测试状态目录与合成输入表；不修改原部署数据库、原工作流或原任务。实际请求到达飞书后，由测试包装层丢弃成功响应，模拟未知提交结果。

- 建表响应丢失后重新查表并续接：仅一次建表 POST，精确名称只匹配一张表。
- 记录写入响应丢失后续接：查到已写记录并完成同步，仅一次追加 POST。
- 第二条测试快照响应丢失后，模拟操作者改动脚本字段：续接返回 `BASE_RESULTS_EXTERNAL_EDIT_CONFLICT`，修改保留，不追加重复记录。
- 原输入表前后内容摘要一致；模型调用为零，结束时结果同步已暂停。新建的测试表和故障证据保留。

这是实际飞书读写和持久化日志恢复的验收。身份层使用独立合成测试账号，不冒充第二个真实飞书身份；未向正在使用的客户工作流注入故障，也未覆盖附件上传中断的所有分支。此前真实视频附件下载与原文件摘要匹配的回执继续有效。首次测试夹具因未创建私有状态目录而停止，修正夹具后复用已建测试表与行，未重复建表；不是修改产品代码后掩盖失败。

## 2026-10-21 只读模型与账单复核

维护者将本轮模型检查范围收窄为 API 连通与鉴权，不再为验收新增付费生成或重复素材质量审核。DeepSeek 官方余额接口返回 HTTP 200；OpenAI、Gemini 的官方只读接口在现有测试主机返回网络不可达，不能据此判定 Key 无效，也不能记录为鉴权通过。随后维护者明确暂缓 OpenAI / Gemini，不再继续探测或将其作为本轮收尾阻塞项；原网络失败记录保留。其他独立接口没有新凭据回执时继续保留未核验状态；可灵免测决定不变。

已核对火山账户在原测试日期范围内的当期账单：存在 Seedance 与 Seedream 计费明细。账单尚未到最终出账期，且未提供每笔模型请求 ID 的对应关系，因此只记为账户日期范围核对，不声称全部历史请求已经财务结清。金额、账户明细和原始截图仅存私有验收目录；DeepSeek 当前余额不作为历史账单证明。本轮新增模型生成请求为零。

已通过的 H3 与脚本审核沿用原回执。后续 Seedance / Seedream 的文件与回写技术检查已通过；本轮不重复索要质量审核，也不替用户修改原任务的审核状态。

## English

The query repair passed actual n8n checks on Windows, macOS, Ubuntu and Docker, plus full server regression, cold restore and offline installation. A pending provider job now returns an explicit receipt without Base writes or another generation. A read-only query of an existing Seedance job also passed. The exact cause of the older live HTTP 500 remains unproven because its response was not retained.

One explicitly authorized Kling request returned HTTP 429 / code 1102, documented as an exhausted or expired prepaid resource pack. No job ID was returned; the receipt and lock remain, and isolated workflows are disabled. There was no automatic retry. This is not a successful Kling video acceptance or a billing clearance. The maintainer explicitly waived further Kling live testing based on prior project experience; it no longer blocks this closeout. The waiver does not claim a successful generation or shipped legacy AK/SK support. The a41 package is published; final packaging, anonymous installation and the official-site 52-file and browser checks passed. Distinct-account, independent-device and other provider acceptance remain separate.

The maintainer narrowed this closeout to read-only API connectivity/authentication with no new paid generation or repeated media-quality review. DeepSeek authentication passed; OpenAI and Gemini were unreachable from the current test host, so credential validity remains unestablished. Provisional account-date billing was reviewed privately; per-request billing reconciliation and final invoices are not claimed.

Real Feishu fault injection now passed for lost table-creation responses, lost record-write responses, and preservation of a concurrent script edit. The original input table stayed unchanged, no model was called, and synchronization was paused. Identity was a synthetic fixture; this does not replace distinct-user acceptance or claim all attachment interruption cases.

The maintainer subsequently explicitly deferred OpenAI and Gemini testing. No further probes are scheduled for this closeout; the prior network failures remain recorded, not converted to passes. Other provider credential gaps, final billing and deferred real-person/device gates remain distinct.
