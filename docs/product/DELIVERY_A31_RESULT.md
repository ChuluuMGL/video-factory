# a31 公开发行回执

2026-10-02。MIT 开源测试版，署名 Chuluu；官网署名月瑀科技。

- 固定版本：0.1.0a31。来源：`50d99e7ccd2688a114bbb1f102a8a776d6218929`。
- 安装包 SHA-256：`1cf62684693fd375017d46bf22a5730c422911ee4403278a2affad392624a927`。
- 安装清单 SHA-256：`4d132bf17620ba39d5150d265a4bc8f7adf1227365e7e3ec4b5fb3c6a21c2cc5`。
- [固定 Release](https://github.com/ChuluuMGL/video-factory/releases/tag/v0.1.0a31) · [官网安装页](https://www.yueyu.tech/zh/products/video-factory/)。

## 实际通过

[完整云端验证](https://github.com/ChuluuMGL/video-factory/actions/runs/36978369426)：247 项核心测试；空白 Ubuntu 环境准备及复用；真实 PostgreSQL 与 n8n 容器；安装、续接、双项目合成配置、浏览器交互；升级与失败回退；完整冷恢复；classic/containerd 两种空 Docker 存储、无外网条件下的安装与恢复。

[发布后匿名验证](https://github.com/ChuluuMGL/video-factory/actions/runs/36981979115)：无 GitHub 登录下载固定包，核对预期来源与 SHA-256，实际安装、拒绝错误信任摘要和非法下载源，断网复用成功。第三方 wheel 不包含在公开归档中，由同一安装入口从上游获取并逐个校验。

[官网候选检查](https://github.com/ChuluuMGL/YUEYUTECH/actions/runs/36981877521)通过。官网 PR #21 合并为 `9f67de6302bece1a255df2c5fc5edab025959152`，部署只替换安装页静态目录，14 个文件摘要逐项回读；网站程序、业务数据、凭据和上传文件均未改变。[正式官网复测](https://github.com/ChuluuMGL/YUEYUTECH/actions/runs/36983815452)已通过：14 个线上文件与清单一致，桌面/平板/手机/窄屏复制与下载正常，四个公开视频可播放，五种语言页脚及桌面/手机产品入口保持正确顺序。

## 隐私与分发

旧 Release、CI 附件与日志共 255 个对象已逐项摘要核验并留在原私有档案。产品库旧 10 个内部 Release 的 40 个附件已移除；公开只提供 a31 的 10 个允许分发的附件。[发行附件扫描](https://github.com/ChuluuMGL/video-factory/actions/runs/36981770431)检查 10 个附件，无敏感项命中。477 个可达 Git blob 的客户名称、真实 Base 链接、飞书身份 ID 和私钥模式复核未命中。源码密钥扫描的两处命中来自同一合成测试语句，已人工复核。

已开启 GitHub 密钥扫描、推送保护与私密漏洞报告入口。源码许可不替代上游依赖、n8n、模型服务或客户素材的条款。

## 不包含的验收

没有本轮付费模型调用；没有不同员工本人操作或非作者独立客户验收；没有新部署真实供应商成片交付验收。以上属于公开分发与技术安装验证，不宣称生产业务已全部通过。
