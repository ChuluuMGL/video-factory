# 公开发布检查 / Publication review

所有者于 2026-10-02 确认公开现有 Video Factory 仓库，并采用 MIT 许可证。GitHub 使用个人署名 `Copyright (c) 2026 Chuluu`；官网沿用公司署名。客户资料与历史内部附件不随源码公开。

## 已处理

- 40 个历史 Release 附件、152 个 CI 附件和 63 份运行日志已转存原私有档案，共 33,575,870,571 字节，逐项校验 SHA-256 和长度。源码 Git bundle 与讨论记录也已备份。
- 已从产品库移除经核验备份的旧 CI 运行和附件。旧运行链接不是当前可访问的验证证据。历史临时 resume token 诊断留在私有档案。
- PR、评论与 Issue 文本经过敏感模式复核。当前源码未发现真实客户 Base 链接、客户配置或私钥；凭据扫描命中的是合成测试语句，已查看代码判断。
- 产品库无客户 Actions secrets、variables、environment 或 webhook；转存用的临时凭据已删除。历史开发机器提交身份保留，后续提交使用 GitHub 匿名邮箱。
- a31 发行使用 MIT，附依赖原始许可及固定 Psycopg 源码。公开发行不携带整套上游镜像。新 CI 只输出限定的阶段、状态和测试数量；原始日志不再作为公开附件。
- 原先的 Artifact 额度限制已通过调整交付流程解决：同一云端 runner 完成固定包、容器和离线验证，只将允许公开的固定发行文件保存为草稿。

最终验证及发布状态见 [当前状态](STATUS.md)。源码公开与安装验证不等于独立客户生产验收。

## 必须分别核对

| 范围 | 当前要求 |
|---|---|
| 当前源码、Git 分支和标签历史 | 扫描凭据并复核客户资料；不能只看 main |
| 提交者元数据 | 确认非 GitHub 匿名邮箱是否适合公开，不擅自改写历史 |
| Release 附件 | 含安装包、Skill、历史页面、截图和容器镜像；在云端扫描 |
| Actions | 检查日志和仍可下载的附件；过期、不可读取或未扫描对象明确列出 |
| 图片与视频 | 凭据扫描不能代替人工查看；检查人物、表格、身份信息和素材授权 |
| 依赖与镜像许可 | 核对直接及传递依赖、n8n 和所附镜像的许可及再分发条件；不可把个人源码许可当作第三方许可 |
| 双语与署名 | 中文/英文说明保持同一版本、权限和验收边界；NOTICE 不冒充开源 LICENSE |
| 公开决定 | 按已确认的 MIT 与公开范围执行，仍需核对发行与匿名访问 |

## 可复查的自动化检查

`.github/workflows/publication-audit.yml` 在 GitHub 云端运行固定版本 Gitleaks 8.30.1，并核对下载的扫描器 SHA-256。只读 `contents` 与 `actions`，不运行受检安装器、镜像或附件，不调用模型或客户服务器。

- 扫描所有已取得 Git 引用的历史与当前受版本控制文件。
- 扫描所有可枚举 Release 附件，包括历史发行包；记录下载摘要。
- 扫描可下载的 Actions 日志及不超过 32 MiB 的活动附件。大型 CI 镜像附件只列清单；Release 中的镜像另纳入扫描，二者不能未经摘要证明就视为相同。
- 发行与 CI 归档递归深度 5，编码递归深度 2；源码按文本扫描，历史二进制需另行查看；扫描不含图片 OCR，不能证明任意深度、二进制或其他编码中没有秘密。
- 工具原始输出和报告不上传。只保存对象、规则、路径、行号、提交、去值的赋值字段与长度等定位信息，不保存密钥值或个人邮箱。
- 扫描命中需要复核；退出成功表示扫描步骤完成，**不是无问题、开源就绪或业务验收通过**。下载失败、过期附件和未完成的任务必须保留在缺口记录中。

本轮范围、发现及未完成项见 [2026-10-02 检查记录](PUBLICATION_AUDIT_2026_10_02.md)。完整开源审查还需客户信息人工复核和第三方许可判断。

---

The owner approved public release under MIT. GitHub attribution is `Copyright (c) 2026 Chuluu`; the official website retains company attribution. Historical customer material and raw operational archives remain private. See STATUS.md for the latest release result.

Review current source, all Git references, author metadata, release assets, CI logs/artifacts, media and third-party licensing separately. The cloud workflow is read-only and uses a pinned, checksum-verified Gitleaks binary. It scans Git history, release assets and accessible CI logs/small artifacts. Large CI artifacts are inventoried, not treated as identical to released images without digest evidence. Expired or inaccessible objects remain gaps.

Archive depth is limited to five and decoding depth to two. Image OCR, arbitrary binary content, customer-data interpretation and licensing are outside the scanner's guarantees. Only redacted finding metadata is retained. A completed scan does not approve publication. Review results and choose licensing/visibility separately before making the repository public.
