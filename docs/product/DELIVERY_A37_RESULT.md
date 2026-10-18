# a37 发行与官网回执

2026-10-18。维护测试版；历史真实业务回执保留，本次没有新增模型生成。

## 来源与文件

- [公开版本 0.1.0a37](https://github.com/ChuluuMGL/video-factory/releases/tag/v0.1.0a37)，10 个附件；旧标签与附件保留。
- 固定源码、服务归档和指南/Skill 均为 `290d6b6de46ebbaedcbd455979a9668741ab873f`，与候选 `c6fa133b262beb0ae105f67d20a6252b9bb6c07c` 树内容相同。
- 归档 SHA256：`0530ff1670009037ebfe6207fc6fc68cfeec1f8b94f0d950ecff938d2c8e5d90`。
- 清单 SHA256：`06ea3693e234caf9705d27f023f17c8009db39e6f222921a75659f2509a1910e`。
- 发布前全部附件校验通过；13 个 Skill 源文件、独立 Skill 压缩包、指南内嵌 Skill 与固定源码逐字节相同。42 个指南文件均符合交付清单。

## 本次修复

- 费用确认绑定实际执行的模型、接口和参数；配置变化后必须重新准备计划，不能迁移旧确认。
- 对明确拒绝、尚未创建远端表和提交记录的空结果快照提供显式恢复；未知提交仍停止对账。
- 发行归档只收录 Git 跟踪的普通 Skill 文件，排除本地缓存、私有运行文件和符号链接。

便携原地升级命令已在 [PR #57](https://github.com/ChuluuMGL/video-factory/pull/57) 合并并通过四平台和完整回归，不属于此固定发行包。不要删除原绑定文件绕过模板检查。a37 的完整服务器栈升级继续遵循[升级前检查](RELEASE_USAGE.md#a37-升级前检查)。

## 已通过的检查

| 检查 | 回执 |
|---|---|
| 固定 main 包、数据库迁移、冷恢复、断网空缓存安装 | [39136881604](https://github.com/ChuluuMGL/video-factory/actions/runs/39136881604) |
| 同树候选四平台实际 n8n 合成集成 | [39134584427](https://github.com/ChuluuMGL/video-factory/actions/runs/39134584427) |
| 同树候选安装页 | [39134584417](https://github.com/ChuluuMGL/video-factory/actions/runs/39134584417) |
| 固定 main 源码隐私扫描 | [39136868536](https://github.com/ChuluuMGL/video-factory/actions/runs/39136868536) |
| 公开附件隐私扫描 | [39139032576](https://github.com/ChuluuMGL/video-factory/actions/runs/39139032576) |
| 已发布版本指南与完整 Skill | [39139029525](https://github.com/ChuluuMGL/video-factory/actions/runs/39139029525) |
| 发布后匿名安装 | [39139026318](https://github.com/ChuluuMGL/video-factory/actions/runs/39139026318) |

## 官网与范围

[官网产品页](https://www.yueyu.tech/zh/products/video-factory/)已部署 a37。官网来源 `393b690a9e476b35862cd59ebee68ec683d7f915`，合并为 `a65557c297600517773dfba7b8db83a1611aaa21`，树内容一致。云端构建与页面测试通过（私有网站仓库回执 39139073192、39139073421）。公网复测 39141255844 已通过：42 个正式交付文件哈希、实际复制、下载、菜单、移动端和案例交互均完成核验。

部署前后 137 个业务配置和上传文件、51 个已有图片与媒体文件完全一致；旧版回滚目录和部署原始回执私有保留。官网保留共用导航、页头、页尾与案例展示。

历史真实飞书、DeepSeek、一次 H3、负责人费用确认和原行人工审核见 [a36 回执](DELIVERY_A36_RESULT.md)。本次技术回归不替代第二账号权限、独立安装、桌面退出/登录、其他模型、可选快照真实附件或精确账单核对。当前未完成项统一见[工作清单](COMPLETION_WORK.md)。n8n Cloud SaaS 继续按维护者决定跳过。

## 后续源码维护

便携原地升级来源 `86387ddbe5aa16f4f77fcdd5a2a048d6ea22b384`，合并为 `ccbe366c42d71a7e40784f12419049e4ec80599a`，树内容一致。[四平台](https://github.com/ChuluuMGL/video-factory/actions/runs/39138952136)、[完整冷恢复与断网回归](https://github.com/ChuluuMGL/video-factory/actions/runs/39138952106)、[页面](https://github.com/ChuluuMGL/video-factory/actions/runs/39138952137)、[隐私](https://github.com/ChuluuMGL/video-factory/actions/runs/39138952173)通过。含预览、精确计划确认、保留凭据与账本的关闭状态更新，以及不重发未知 PUT 的回读恢复。源码改动不覆盖已发布 a37 附件，固定版发布仍为后续工作。
