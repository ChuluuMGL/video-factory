# 参与贡献 / Contributing

欢迎报告问题、提出改进建议，或提交小范围、可验证的 Pull Request。

## 提建议或报告问题

先查看已有 Issue。描述使用版本、期望行为、实际行为和最小复现步骤。功能建议说明要解决的工作问题，而不只是界面按钮。

常见故障先查[排障与问题反馈](docs/product/TROUBLESHOOTING.md)。Issue 是待核查问题；修复并验证后由维护者在同一 PR 更新适用版本的排障说明，固定发行前复查。客户的 Agent 可以准备脱敏草稿，但不能自动公开提交。

**不要上传密码、API Key、App Secret、飞书用户/表格标识、服务器登录信息、客户素材或原始运行日志。** 截图与样例必须脱敏；优先使用合成商品和测试身份。疑似漏洞或泄露按 [SECURITY.md](SECURITY.md) 私密报告，不发公开 Issue。

## 提交修改

1. 从当前 main 建立分支；一次 PR 解决一个明确问题。
2. 说明改动前后行为和验证范围。不要用 Mock 通过描述真实模型或员工验收通过。
3. 修改安装合同、版本或能力时，同步 README、客户指南、完整 Skill 与页面说明。
4. Python 与前端文件先做静态检查。涉及服务器、Docker、浏览器的集成验证使用隔离 Linux 云环境，不接生产凭据或真实客户数据。维护者负责固定发行验证。
5. 使用原创或具有兼容许可的代码；说明新依赖及其许可。不要把第三方商业组件重新标注为本项目许可证。

PR 中只放必要的源码与脱敏验证摘要，不放构建产物、数据库、密钥或完整诊断包。维护者可能要求补充复现、测试或文档；不承诺处理时限。

---

Suggestions, reproducible bug reports and focused pull requests are welcome.

- Search existing issues. Include the version, expected/actual behavior and minimal reproduction. Explain the workflow problem behind feature requests.
- Never upload credentials, customer media, Feishu identities/table identifiers, server access details or raw logs. Use synthetic fixtures and sanitized screenshots. Report suspected vulnerabilities privately under [SECURITY.md](SECURITY.md).
- Keep each PR focused. State behavior changes and verification scope; mock tests do not establish real-provider or employee acceptance.
- Keep README, customer documentation, the complete Skill and installation page aligned when changing installation contracts or capabilities.
- Run server/Docker/browser integration checks in an isolated Linux cloud environment without production credentials. Maintainers validate fixed releases.
- Submit original or compatibly licensed code and document new dependencies. Third-party terms remain separate from this project's license.

Include source and sanitized verification summaries only. Do not commit build artifacts, databases, keys or raw diagnostic bundles. Maintainers may request changes; no response SLA is promised.
