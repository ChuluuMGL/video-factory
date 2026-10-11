# a33 发行与官网回执

2026-10-11，公开 MIT 测试版 **0.1.0a33**。a32 标签和附件保留，不覆盖已发布文件。

| 项目 | 回执 |
|---|---|
| 固定发行来源 | `d694758369ce26726a90dd518a8c2819994134de` |
| 安装包 SHA-256 | `8377d90659614633a6d4b3cb99f614b53c667792e6e9481cb3582ad603cbc726` |
| 安装清单 SHA-256 | `6109e46b677019fbfe679cd4ef3e7baf541b2d2f372b50c369d2b503cfba5530` |
| 完整云端回归 | [38147056522](https://github.com/ChuluuMGL/video-factory/actions/runs/38147056522)，通过 |
| 发布后匿名安装 | [38148799187](https://github.com/ChuluuMGL/video-factory/actions/runs/38148799187)，通过 |
| 官网云端构建 | [38148845375](https://github.com/ChuluuMGL/YUEYUTECH/actions/runs/38148845375)，通过 |
| 正式官网线上复测 | [38149727362](https://github.com/ChuluuMGL/YUEYUTECH/actions/runs/38149727362)，通过 |
| 固定下载 | [v0.1.0a33](https://github.com/ChuluuMGL/video-factory/releases/tag/v0.1.0a33) |
| 产品与安装入口 | [正式官网](https://www.yueyu.tech/zh/products/video-factory/) |

完整回归覆盖空白 Ubuntu 安装、终端隐藏输入与续接、包校验和拒绝、原表事件及返工合成流程、PostgreSQL、媒体完整解码、容器升级和冷恢复，以及两种 Docker 镜像存储的断网安装。官网部署仅替换经过云端构建核对的产品指南静态文件，保留原指南备份，业务数据未改动；32 个线上指南文件与云端构建包一致。共享 Header/Footer、其他页面和应用程序包保持现有生产版本。

产品形态继续为 Skill/CLI Setup、飞书 Base 日常任务与审核、客户自有 n8n 和私网 Runner。a33 包含原表审核、明确认证拒绝后的受控恢复、中国区 H3 接口及配置失败的密钥清理修复。不会自动重试未知供应商提交，不新增员工网页安装步骤。

开发候选已完成单账号真实原表返工、单次 H3 生成及成片通过。上述发行回归使用合成账号和供应商，因此不把它计作固定新部署的真人业务验收。测试机升级后须重新认证并恢复项目调度；真实新 SKU 脚本模型、第二真实项目、非作者安装接管及最后的第二真实员工权限仍按 [交付检查](DELIVERY_ACCEPTANCE.md) 与 [工作清单](COMPLETION_WORK.md)逐项记录。原始凭据、人员、客户配置和供应商回执留私有环境。
