# a30 端口修复与固定发行验证

2026-10-01，固定私有测试版 `0.1.0a30` 已发布，来源 `9debed8bfe4187925dbe145625973324789408d4`。旧归档保留，原安装页面已同步。

## 修复

安装预检与网关重启使用相同的端口复用规则，避免已关闭连接的 TIME_WAIT 被误报为仍有服务占用。仍拒绝真实监听者，包括回环和全地址监听，不启用共享监听端口。

[PR #4](https://github.com/ChuluuMGL/video-factory/pull/4) 的来源 `b2d3bf67d4f57fad7cf6d116bbd5b7481929fb38` 已通过[五组云端检查](https://github.com/ChuluuMGL/video-factory/actions/runs/36859790732)，包含 247 项核心测试和真实 Linux socket 回归；已合并至 main。之后统一页面、客户指南和 Skill 中旧版本的描述；修复分支结果不代替以下固定发行验证。

## 已通过的固定发行验证

[a30 最终云端流水线](https://github.com/ChuluuMGL/video-factory/actions/runs/36863768194) 的五组检查和私有发布全部成功。

- 247 项核心回归，包括真实 Linux TIME_WAIT 复现和真实监听者拒绝。
- 同一固定归档离线安装、同版本复用、完整 Skill、Setup 问答/退出/续接、SSH 安装和双项目回归。
- 空白 Ubuntu 24.04 依赖安装与复用，实际 Docker daemon 核对。
- PostgreSQL 飞书导入防重、脚本返工与视频审核；自动化身份和模型为合成数据。
- 页面桌面/手机操作、四种 Agent 入口、复制降级、完整 Skill 下载、内部链接与私有输入。
- 实际 n8n 工作流、HTTPS 工作区、权限拒绝、调度启停续期、a19→a30 升级、失败回退与完整冷恢复。
- classic/containerd 两类空缓存 Docker 存储断网安装、重启与恢复；两者镜像清单摘要相同。

两次中间流水线因补正文案主动取消（36863207686、36863628147），未记为通过，也未发布它们的产物。

## 固定交付

- [a30 私有测试版](https://github.com/ChuluuMGL/video-factory/releases/tag/v0.1.0a30)：7 个文件全部 uploaded；安装归档、镜像和 Skill 的 GitHub 资产摘要与回执一致。大镜像仅在云端流转。
- [安装指南原地址](https://video-factory-install-guide.fresh-note-6263.chatgpt.site)：继续所有者私有访问；14 个文件按同次云端清单校验，Skill ZIP 与 Release 中相同。
- 指南源码 `33883d205fea6a7d2df0b4bf8a8f85badf88840c`；部署 `appgdep_6abe5a649610819191bca05f9f65a009` 于 2026-10-01 21:04:43（中国时间）返回 succeeded。

固定摘要：

- archive_sha256: `0c554aedd0e45b620ef4c54638774a2bfc1659ada4fbc1fcd7aeac1a3b4d42f1`
- manifest_sha256: `4f19166782e37507be92b6bf0d52dd47da5cd80f4e04f8532e70a6005ff7fdf7`
- image_archive_sha256: `7cc3567e473ae3dd8e9f5726c573d522da5d7a33fd1a2cf2e83723092d50f470`
- image_manifest_sha256: `bd6d35360603519ba03154023e9917e678023a84e22161570cfc36a5f42eca2e`

## 真实验收边界

[a29 真实云端实测](DELIVERY_A29_RESULT.md) 已验证真实飞书身份、建表、脚本返工审核与重复 Setup，但不含此修复。a30 自动化使用合成业务数据，不能代替独立客户安装、不同真实员工账号权限、真实域名证书、新部署的付费模型生成及成片验收。

本轮未启动 ECS、未提交付费模型。Mac 仅编辑、Git、静态文件校验、站点打包和读取小型证据；所有运行测试在云端。邀请同事前仍需准备其可访问页面/安装包、确切测试服务器和域名、管理员与独立员工身份。
