# a38 发行与官网回执

2026-10-19。维护测试版；本次没有新增模型调用，历史业务验收按原版本保留。

## 固定发行

- [0.1.0a38](https://github.com/ChuluuMGL/video-factory/releases/tag/v0.1.0a38)：10 个公开附件，旧版标签与文件保留。
- 固定源码：`112dc83725bb67dcb3698c1a0352ccd0210d8eb9`。
- 归档 SHA256：`a6524cdb2ab8c25432c5fefb9423c5ca166959decafb4784f7b43802e94e0eac`。
- 清单 SHA256：`79c99d52d0ca3e4449ae62cf3d062600ff0c07d7bed67a4b874db10ce1018c1d`。
- 10 个附件哈希、14 个 Skill 源文件、独立 Skill 包和 44 个指南文件已与固定源码核对。

## 已包含的改动

- [便携原地升级](PORTABLE_UPGRADE.md)：预览并确认准确计划；保留工作流 ID、凭据引用、配置及执行账本。
- 升级前检查工作流已暂停、用户未修改托管内容、无待确认或未知执行。发生未知 PUT 后只回读对账，不自动重复更新。
- 修复 CLI 直接执行时辅助模块错误逸出的问题；返回可识别错误，不回显 Key 或异常堆栈。

安装和升级不会开启收费流程。产品仍是 Agent Skill / CLI 配置、飞书 Base 操作、客户自己的 n8n 执行。

## 已通过的检查

| 检查 | 云端回执 |
|---|---|
| 固定包、数据库迁移、冷恢复、断网空缓存安装 | [39175731136](https://github.com/ChuluuMGL/video-factory/actions/runs/39175731136) |
| Windows、macOS、Linux 原生与 Docker 实际 n8n | [39175734332](https://github.com/ChuluuMGL/video-factory/actions/runs/39175734332) |
| 固定源码隐私扫描 | [39175728062](https://github.com/ChuluuMGL/video-factory/actions/runs/39175728062) |
| 发布后匿名安装 | [39177773926](https://github.com/ChuluuMGL/video-factory/actions/runs/39177773926) |
| 已发布指南与完整 Skill | [39177776027](https://github.com/ChuluuMGL/video-factory/actions/runs/39177776027) |
| 公开附件隐私扫描 | [39177778229](https://github.com/ChuluuMGL/video-factory/actions/runs/39177778229) |

## 官网

[官网产品页](https://www.yueyu.tech/zh/products/video-factory/)已部署 a38。官网来源 `03f800615226c5396f8c186ec51864bddaf5896b`，合并为 `87590791eb27737f47bb04e2f01ba315c80cdaba`，树内容一致。云端构建与候选浏览器检查通过（私有官网仓库回执 39221652566、39221728499）。

正式网址复测 39224157005 通过：44 个交付文件哈希、四种屏宽的复制/下载、共用导航/页尾、多语言菜单、案例视频播放和错误反馈均完成核验。部署前后 137 个业务配置及上传文件、51 个已有图片与媒体文件保持一致；回滚副本及原始部署回执私有保留。

## 验收边界

历史真实飞书、DeepSeek、H3、负责人费用确认及原行审核见 [a36 回执](DELIVERY_A36_RESULT.md)。a38 自动化回归不代表这些真实业务在新版重新调用过。

第二账号、非作者安装及真实桌面退出/登录按维护者安排暂缓；n8n Cloud SaaS 本轮跳过。其他模型在 [PR #61](https://github.com/ChuluuMGL/video-factory/pull/61) 和 [PR #62](https://github.com/ChuluuMGL/video-factory/pull/62) 完成候选集成，未纳入本版；真实供应商调用、Seedream 独立图片路线、可选快照附件及精确历史账单仍单独记录在[工作清单](COMPLETION_WORK.md)。
