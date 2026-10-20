# a40 验证与发布记录

本页记录 a40 的技术检查和两次真实模型验收，**不等于已经公开发布或全部业务验收完成**。可下载版本以 [GitHub Releases](https://github.com/ChuluuMGL/video-factory/releases) 为准。

## 代码与云端检查

[PR #70](https://github.com/ChuluuMGL/video-factory/pull/70) 已合并；运行代码来源 `247c101947d034fc74d2b21804090fed9d01ae1a`，合并提交 `6524b07a697e9fe7aded509f15845cf68634c095` 的树内容一致。本页后续文档更新不改变这次实测的模型脚本。

- [完整云端回归](https://github.com/ChuluuMGL/video-factory/actions/runs/39438696745)：空白 Ubuntu、固定包、终端续接、PostgreSQL、实际 n8n、整栈升级与冷恢复、两种镜像存储模式的断网空缓存安装通过。
- [四平台实际 n8n](https://github.com/ChuluuMGL/video-factory/actions/runs/39438696782)：Windows、macOS、Ubuntu、Docker 通过。
- [桌面与手机指南](https://github.com/ChuluuMGL/video-factory/actions/runs/39438696796)、[隐私检查](https://github.com/ChuluuMGL/video-factory/actions/runs/39438696817)通过。

前一轮冷备份因组件仍在运行而拒绝继续。补充脱敏诊断后的完整复测通过；未将未复现的问题写成已定位的产品修复，原失败记录保留。实际发行包仍须通过它自己的打包检查。

## 2026-10-20 独立真实模型验收

两次调用均由维护者分别确认，使用既有 Key、同一张已核验参考图和独立测试行。每个模型仅提交一次生成请求，不自动重试，不覆盖旧失败记录或原已审核任务。

| 路线 | 请求 | 实际结果 | 飞书状态 |
|---|---|---|---|
| Seedance 2.0 | `doubao-seedance-2-0-260128`，5 秒、720P、9:16、有声 | 5.088 秒、720×1280、1 条音轨；供应商任务成功，链接回写，完整解码通过 | 视频待审核 |
| Seedream 5.0 Lite | `doubao-seedream-5-0-lite-260128`，2K、1 张、保留水印 | 2048×2048；返回精确官方别名 `doubao-seedream-5-0-260128`；附件与账本回执一致，下载字节数及完整解码通过 | 图片待审核 |

Seedance 首次私有验收 webhook 轮询返回错误；随后只读核对同一个供应商任务并完成原任务结果回写，没有再次生成。首次轮询错误未定位，记录保留，不能据此宣布所有运行场景稳定。

结束时两个账本均为空闲，隔离 n8n 的流程全部关闭，原已审核行未变。原始任务 ID、账号、素材、文件哈希和供应商回执仅保留在私有验收记录，不进入公开仓库。

## 仍须单独验收

- 本次两份结果的人工审核与业务质量；生成、回写成功不自动修改为“审核通过”。
- 可灵尚未获得完整成片回写；旧失败/未知记录保留，不自动重发。Seedance 2.5、万相及其他文字模型尚无本产品的完整真实账户验收。
- 第二个真实飞书账号的权限与费用确认隔离。
- 非作者独立安装、真实 Mac / Windows 退出系统并重新登录。
- n8n Cloud SaaS 真实实例本轮按维护者安排跳过。

## English

The exact runtime source passed cloud regression, actual n8n integration on four platforms, installation-page checks and privacy scanning. Separate, explicitly authorized single calls completed a Seedance 2.0 video and a Seedream 5.0 Lite image. Both were written to Feishu and fully decoded. Their human-review states remain pending.

The first private video-poll webhook returned an error; querying the existing provider job and polling it again completed writeback without another generation. The earlier cold-backup refusal and this polling error remain recorded. These results do not establish universal stability, output quality, other-provider acceptance, distinct-account isolation or independent-device installation. Public availability is determined by GitHub Releases.
