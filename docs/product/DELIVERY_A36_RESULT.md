# a36 发行与官网回执

2026-10-18。当前发布为 MIT 开源 alpha 测试版；本记录不代替第二账号、独立安装或桌面会话验收。

## 来源与文件

- [公开版本 0.1.0a36](https://github.com/ChuluuMGL/video-factory/releases/tag/v0.1.0a36)，10 个公开附件，保留旧版本。
- 服务归档来源：`6255eb43888015f277e03569e81e249f20879edb`。
- 服务归档 SHA256：`1f4a09ba8abb548ebafcc8ecf4b31a2c3c63bf396dc1b71a1e54cca5ef9a9f90`。
- 清单 SHA256：`fc4a53d685b8af2ecb5b7fd10bed6d18434bf0b80ab0a687b3f4dbdf69165206`。
- 指南/Skill 打包来源：`324bdd642e528d8ca8623768b525df86c787f4f2`；[PR #51](https://github.com/ChuluuMGL/video-factory/pull/51) 合并为 `b733b6e59ebbb2e239c5d4aaca384ff65b81f8fa`，树内容一致。发布前发现并移除了构建产生的 Python 缓存；13 个 Skill 源文件与固定服务归档来源一致，服务归档未改动。两种来源明确记入 `delivery-manifest.json`。

## 已通过的检查

| 检查 | 回执 |
|---|---|
| 四平台真实 n8n 合成集成 | [39122080955](https://github.com/ChuluuMGL/video-factory/actions/runs/39122080955) |
| main 固定包完整构建与回归 | [39123702101](https://github.com/ChuluuMGL/video-factory/actions/runs/39123702101) |
| 打包修复后的冷恢复与断网空缓存安装 | [39125727586](https://github.com/ChuluuMGL/video-factory/actions/runs/39125727586) |
| 打包修复隐私审查 | [39125727513](https://github.com/ChuluuMGL/video-factory/actions/runs/39125727513) |
| 发布后匿名安装 | [39126104288](https://github.com/ChuluuMGL/video-factory/actions/runs/39126104288) |
| 已发布版本的指南与 Skill | [39126108760](https://github.com/ChuluuMGL/video-factory/actions/runs/39126108760) |

早期固定版页面检查 39125865231 因只读 CI 令牌无法获取未公开草稿而失败；保留失败记录，公开后上述精确版本检查通过，没有将失败冒充成功。

## 官网

[正式产品路径](https://www.yueyu.tech/zh/products/video-factory/)已同步 a36。网站适配层原有的服务器限定复制文案已修复：同步脚本从校验过的交付指南生成指引，避免维护两份不同文本。正式站点 42 个交付文件哈希、实际复制、下载、导航、移动端与案例视频检查通过。官网沿用共用页头、页尾和品牌样式。

部署前后 137 个配置/业务数据/上传文件及 51 个已有图片和媒体文件保持不变；回滚包及部署回执私有保留。网站构建、审批、安全审查和正式网址复测均已通过；私有网站仓库中的运行编号为 39126076139、39126078948、39126615223。

## 真实业务与边界

隔离环境已有真实飞书、DeepSeek、单次 H3、原表人工审核和负责人费用确认回执。新增脚本任务从真实确认事件首次正常完成，一次模型提交、持久结果、原行回写和人工审核逐项回读；其他原记录未改动。测试付费流程已关闭，原接收器恢复。此发布批次没有新增模型生成。

还未完成的功能与验收统一见 [当前剩余工作](COMPLETION_WORK.md)，不能将“a36 技术发布通过”理解为所有模型、可选结果快照、跨账号和客户独立安装均已验证。
