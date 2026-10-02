# a31 发布后的维护回执

2026-10-02。[产品 PR #7](https://github.com/ChuluuMGL/video-factory/pull/7) 已合并，测试提交 `7197c6cd30e41bffb128e036d27e3ac266045bf9`，合并提交 `816ac3352c37590aade5bc39b121efef860160ce`。

## 本轮完成

- PR 和手动回归默认只测试；创建 Release 草稿须在 main 手动选择，现有发行不覆盖。
- 隐私扫描覆盖 PR。未审查发现、未知分类和扫描异常返回失败；历史合成命令的例外限定到规则、路径及代码行摘要。
- 统一 GitHub 现行文档中的公开下载、官网入口、历史接口与验收边界。修正两处安装包内会失效的文档相对链接。
- 官网验收文档移除旧仓库授权要求，文件摘要随之更新；[官网 PR #22](https://github.com/ChuluuMGL/YUEYUTECH/pull/22) 已合并并完成静态文件部署。

## 已完成验证

| 检查 | 结果 |
|---|---|
| [完整云端回归](https://github.com/ChuluuMGL/video-factory/actions/runs/36989490222) | 三组作业全部通过；29 个阶段回执无失败，247 项核心测试通过；未触发草稿发布 |
| [隐私阻断与源码检查](https://github.com/ChuluuMGL/video-factory/actions/runs/36989490094) | 七项策略测试通过；无待审查发现，无扫描错误 |
| [官网云端构建与浏览器检查](https://github.com/ChuluuMGL/YUEYUTECH/actions/runs/36988518315) | 通过 |
| [正式官网线上复测](https://github.com/ChuluuMGL/YUEYUTECH/actions/runs/36989013843) | 14 个文件摘要、桌面及移动布局、四场景复制、Skill 下载、案例播放及菜单顺序通过 |

首次回归发现文档链接越出客户打包白名单，修复后完整复测通过。隐私策略首次拦住历史合成命令，人工核对后用精确摘要登记例外，并验证命令变化会失去例外；不是忽略整份文件。

本轮没有替换 a31 已发布的安装包或 Skill，没有改产品运行逻辑；GitHub 当前文档的修订将在后续维护发行中打包。固定版本与原摘要继续有效。本轮未启动真实测试服务器或提交付费模型任务。

## 仍未关闭

- [真实业务与独立安装验收 #8](https://github.com/ChuluuMGL/video-factory/issues/8)：隔离部署、可信 HTTPS、真实模型、第二项目、不同员工及客户接管。当前仍缺测试入口和第二位真实测试者，不能用 Agent 代签。
- [Base 回写 #9](https://github.com/ChuluuMGL/video-factory/issues/9)。
- [证书签发续期 #10](https://github.com/ChuluuMGL/video-factory/issues/10)。
- [多项目统一入口 #11](https://github.com/ChuluuMGL/video-factory/issues/11)。

后三项已登记开发与验收条件，尚未实现。公开记录不包含真实账号、资源标识、凭据或客户素材。
