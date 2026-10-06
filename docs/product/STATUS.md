# 当前产品状态

2026-10-02，当前公开版本 **0.1.0a31（MIT，测试版）**。源码、完整 Skill 与固定安装包可匿名获取；官网入口已同步。独立客户、不同员工账号和新部署真实模型生成仍未完成，不等于生产验收。

2026-10-06 产品形态复核：后续默认路径固定为 Skill/CLI 安装、飞书 Base 日常使用与客户自有 n8n 执行。现有 `workspace` 网页实现不作为默认安装步骤；本分支新增无公网端口的项目 `runner` 候选，尚待云端容器与独立业务验收。飞书内完整审核和结果回写也尚未通过独立验收。详见 [产品形态与验收边界](PRODUCT_SHAPE.md)。公开 a31 固定包及旧测试记录未改变，不能将本分支文档当作已发布的新版本。

## a31 公开发行

- 源码与固定发行：[GitHub](https://github.com/ChuluuMGL/video-factory) · [a31](https://github.com/ChuluuMGL/video-factory/releases/tag/v0.1.0a31)。GitHub 个人署名 Chuluu，官网公司署名月瑀科技。
- [完整云端检查](https://github.com/ChuluuMGL/video-factory/actions/runs/36978369426)与[匿名下载安装](https://github.com/ChuluuMGL/video-factory/actions/runs/36981979115)通过；发行源码 `50d99e7ccd2688a114bbb1f102a8a776d6218929`。
- 公开包仅带自有 wheel；首次安装从 PyPI 官方文件源按大小及 SHA-256 获取外部 wheel，完整获取后可断网复用。服务镜像从上游按固定摘要获取。
- 旧运行日志、内部镜像和历史附件留在原私有档案。10 个旧内部 Release 及其 40 个附件已从产品仓库移除，Git 源码标签保留；旧 CI 运行及附件此前已归档清除。
- 已开启 GitHub secret scanning、push protection 与 private vulnerability reporting。扫描不保证没有漏洞，见 [安全报告](../../SECURITY.md)。
- 官网静态安装页已部署；既有产品菜单顺序保留。[正式官网复测](https://github.com/ChuluuMGL/YUEYUTECH/actions/runs/36983815452)已通过。详见 [a31 发行回执](DELIVERY_A31_RESULT.md)。

## 发布后维护（2026-10-02）

[PR #7](https://github.com/ChuluuMGL/video-factory/pull/7) 已合并：测试与创建草稿解耦、隐私发现阻断、现行说明与官网验收文档同步。完整云端回归、隐私检查与官网线上复测通过，见 [维护回执](MAINTENANCE_2026_10_02.md)。固定 a31 安装包和 Skill 保持原摘要；真实业务验收及后续能力仍未关闭。

## 历史：a30 官网入口（2026-10-02）

官网产品与安装页：https://www.yueyu.tech/zh/products/video-factory/ 。页脚、桌面产品下拉和手机产品菜单均将 Video Factory 放在第一项。官网 [PR #19](https://github.com/ChuluuMGL/YUEYUTECH/pull/19) 已合并，部署版本 `1f7fccb65967b4974ceb4eb375e6d8258fd87125`。固定 a30 Skill 未改变，产品仓库和服务器安装包仍私有；独立客户安装及新部署真实生成验收仍待完成。

## a30 已发布（2026-10-01）

来源 `9debed8bfe4187925dbe145625973324789408d4` 的[五组云端检查与发布](https://github.com/ChuluuMGL/video-factory/actions/runs/36863768194) 全部成功。修复关闭连接后的端口误判，保留对真实监听者的拒绝；清理页面、客户指南和 Skill 中过期的版本表述。固定包、完整 Skill、镜像与原址私有安装页一致。a30 本轮没有重启真实 ECS 或提交付费模型，客户独立安装和生产验收仍待完成。

## a29 已发布（2026-10-01）

统一官网字体与 UI、重写安装说明和修复复制降级提示。固定发行来源 `b6aed2afa2bdf74fdfaa932fd1f0edf6565b049b` 的五组云端检查及发布全部通过（Actions 36844844199），含 245 项核心测试。安装指南原址、完整 Skill 和下载入口已同步为 a29，a28 保留。后续授权 ECS 实测已通过真实身份授权、新建测试 Base、导入防重、退回修订重审与绑定复用；同一账号由 Agent 操作。真实域名、模型与独立人员验收仍未完成。

## 已实现

- 专用服务器组件、管理员认证、加密凭据、CLI 与终端/Agent Setup。
- 同客户多项目、继续安装、固定版本、备份/升级/恢复入口。
- 飞书本人授权、新建或绑定 Base、脚本退回/修订/审核。
- 安装页面、四种 Agent 入口、完整 Skill 下载、统一安装交接。
- 客户文档白名单、归档校验、MIT 公开固定发行与匿名安装。

## 证据范围

a26 曾在授权云服务器完成真实飞书建表和脚本审核，由 Agent 操作同一真实账号；没有本轮付费生成，不是双人或非作者验收。原始证据保留私有旧仓库，不打入客户安装包。

a27 来源 `5d75f1b01aef76f7bd1c7522784543549c042f7c` 已通过全部四组云端检查并发布私有预发行版；含 227 项核心测试、实际页面交互、固定包安装、双项目、PostgreSQL、完整容器升级恢复和两种镜像存储断网安装。私有指南页面已部署。详见 [交付验证记录](DELIVERY_A27_RESULT.md)。这些结果不等于客户真人验收。

## a28 已通过云端检查（2026-10-01）

代码来源 `661d32fd25a6be00f9a806864a385fb098451158` 的五组检查全部成功：空白 Ubuntu、245 项核心回归与浏览器流程、PostgreSQL、真实 n8n/HTTPS/恢复及两种 Docker 存储断网安装。详见 [a28 记录](DELIVERY_A28_RESULT.md)。新增环境准备、私有浏览器输入、SKU 脚本与视频逐条授权、常驻员工工作区、调度启停和续期。最终发行来源 `8bfc2795a35d4771ebf7d7758bf30275b617202f` 的五组检查及发布也全部通过，私有指南原址更新成功。

## 未完成

- 实际域名/证书、新部署的真实模型调用、不同员工账号权限、非作者独立安装、第二项目真实接入与客户接管验收。
- 新生成任务或附件自动回写 Base；内置 ACME 自动签发和续期。当前由客户或实施方管理证书。
- 统一多项目门户；当前按项目部署员工入口。

剩余事项及关闭条件见 [工作清单](COMPLETION_WORK.md)。详细交付门槛见 [DELIVERY_ACCEPTANCE.md](DELIVERY_ACCEPTANCE.md)。云端合成检查、真实供应商、真人业务验收分别记录。
