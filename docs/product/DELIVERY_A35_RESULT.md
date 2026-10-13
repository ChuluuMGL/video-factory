# a35 发行与官网回执

2026-10-13。本记录只记录已回读的阶段，不代替真人安装或跨账号验收。

- 固定版本：`0.1.0a35`，来源 `7b35385ca29dfb8b1cc9ebafb1d9d086298cec44`。[公开发行](https://github.com/ChuluuMGL/video-factory/releases/tag/v0.1.0a35)
- [正式完整云端回归](https://github.com/ChuluuMGL/video-factory/actions/runs/38348924741)及[发布后匿名安装](https://github.com/ChuluuMGL/video-factory/actions/runs/38352819448)通过。
- 发行归档 SHA256：`394f7282d0a29f2f0699d7c45b5a3d9fbf4ee3f04ac88ea8ada55f705e7455c1`；清单 SHA256：`c88a01e0123e0c171e2c2208daee6d518bd58429258e6af983b66e5995392a9e`。
- 官网产品介绍、完整 Skill 与安装来源同步为 a35。[官网候选构建](https://github.com/ChuluuMGL/YUEYUTECH/actions/runs/38354444606)和[正式网址复测](https://github.com/ChuluuMGL/YUEYUTECH/actions/runs/38357693800)通过，包括 31 个交付文件哈希、匿名下载、实际复制指引及桌面/手机菜单。
- 网站部署从现有生产基线准备候选，只变更产品指南。业务数据、上传目录及素材抽样不变，其他 77 个静态文件核对一致；回退包与原始部署凭据私有保留。
- 现有测试机已核对固定包归档、清单及依赖哈希并安装 a35 CLI。首次外部依赖下载中断不计为成功；复用逐个验证匹配的旧依赖后回读 `cli_installed`。旧服务当时仍运行 a34，不能把 CLI 安装当作运行服务升级。

## 仍需完成

现有实例的保留数据升级、Runner/调度恢复和原业务回读；非作者受支持主机安装与接管；具体模型账单核对；最后进行第二个真实员工权限验收。独立真实脚本生成与本人原表审核是 a34 的既有证据，不能移作 a35 升级后验收。

飞书直接申请生产与指定负责人逐条费用确认尚在开发，不属于这个固定包的已验收能力。脚本审核通过不会自动授权付费视频或外部发布。后续下载错误分类另见 [PR #33](https://github.com/ChuluuMGL/video-factory/pull/33)，不能宣称固定 a35 已包含未发行修复。
