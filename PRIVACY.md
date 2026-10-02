# 数据说明 / Data handling

这是产品的数据流说明，不代替客户自己的隐私告知或与供应商的协议。

- **安装网站**：提供说明、案例和 Skill 下载。不是客户项目账户、密码或 API Key 的输入入口。网站托管和访问日志仍按官网的运行方式处理；不能据此承诺“网站不记录任何数据”。
- **客户服务器**：保存项目配置、任务、审核记录和凭据。由客户控制服务器、账号、备份、保留期限及人员访问；不设置厂商统一的客户账户数据库。
- **外部服务**：连接飞书时，请求会到飞书；生成脚本或视频时，相关提示、素材或引用会发送到所选模型供应商。自托管并不代表所有处理都在服务器内完成。
- **AI 助手**：会接触安装说明与非秘密配置。密码、App Secret 和模型 Key 应通过用户直接操作的私有输入通道填写，不能放进聊天、命令参数、Git 或诊断附件。
- **排障资料**：只共享必要且脱敏的版本、错误码和复现步骤。原始客户表格、媒体、完整日志和人员 ID 不作为通用测试数据提交。
- **公开案例**：官网已发布案例与新客户安装产生的数据分开管理。案例存在不代表本版本已完成独立客户验收。

仓库可见性、客户数据保留和开源许可是不同事项。公开仓库前必须检查历史、发行附件及 CI 资料，见[发布检查](docs/product/PUBLICATION_REVIEW.md)。

---

This describes product data flows. It does not replace the customer's own privacy notice or provider agreements.

- **Installation website:** documentation, examples and Skill downloads; not a credential or customer-account input channel. Website hosting and access logging still apply; this is not a promise of zero website data collection.
- **Customer server:** stores project configuration, tasks, review records and credentials. Customers control hosting, accounts, backups, retention and access. No vendor-wide customer account database is required.
- **External services:** Feishu connections contact Feishu. Script/video generation sends relevant prompts, assets or references to the selected model provider. Self-hosting does not mean that all processing stays on the server.
- **AI agent:** reads installation instructions and non-secret configuration. Users enter passwords, application secrets and model keys through private input channels, not chat, command arguments, Git or diagnostic attachments.
- **Support:** share only necessary sanitized versions, error codes and reproduction steps. Do not submit raw customer tables, media, logs or personnel identifiers as generic fixtures.
- **Public examples:** already-published website examples are separate from data created by a new installation. Examples do not establish independent acceptance of this release.

Repository visibility, customer retention and open-source licensing are separate decisions. Review history, release assets and CI data before publication; see the [publication checklist](docs/product/PUBLICATION_REVIEW.md).
