# 公开发布检查 / Publication review

**仓库保持私有。补齐文档和扫描完成均不自动授权公开。**

2026-10-02 开始补齐 GitHub 中英文入口、NOTICE、安全报告与数据说明。GitHub 个人署名使用 `Copyright (c) 2026 Chuluu`；官网继续使用公司身份。未选择开源许可证，未修改旧项目仓库或既有发行版。

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
| 公开决定 | 所有问题处理后，另行明确许可方案和仓库可见性 |

## 可复查的自动化检查

`.github/workflows/publication-audit.yml` 在 GitHub 云端运行固定版本 Gitleaks 8.30.1，并核对下载的扫描器 SHA-256。只读 `contents` 与 `actions`，不运行受检安装器、镜像或附件，不调用模型或客户服务器。

- 扫描所有已取得 Git 引用的历史与当前受版本控制文件。
- 扫描所有可枚举 Release 附件，包括历史发行包；记录下载摘要。
- 扫描可下载的 Actions 日志及不超过 32 MiB 的活动附件。大型 CI 镜像附件只列清单；Release 中的镜像另纳入扫描，二者不能未经摘要证明就视为相同。
- 发行与 CI 归档递归深度 5，编码递归深度 2；源码按文本扫描，历史二进制需另行查看；扫描不含图片 OCR，不能证明任意深度、二进制或其他编码中没有秘密。
- 工具原始输出和报告不上传。只保存对象、规则、路径、行号、提交等定位信息，不保存密钥值、匹配原文或个人邮箱。
- 扫描命中需要复核；退出成功表示扫描步骤完成，**不是无问题、开源就绪或业务验收通过**。下载失败、过期附件和未完成的任务必须保留在缺口记录中。

实际结果在完成后单独记录；不预填“全部通过”。完整开源审查还需客户信息人工复核和第三方许可判断。

---

The repository remains private. GitHub attribution is `Copyright (c) 2026 Chuluu`; the official website retains company attribution. No open-source license has been chosen.

Review current source, all Git references, author metadata, release assets, CI logs/artifacts, media and third-party licensing separately. The cloud workflow is read-only and uses a pinned, checksum-verified Gitleaks binary. It scans Git history, release assets and accessible CI logs/small artifacts. Large CI artifacts are inventoried, not treated as identical to released images without digest evidence. Expired or inaccessible objects remain gaps.

Archive depth is limited to five and decoding depth to two. Image OCR, arbitrary binary content, customer-data interpretation and licensing are outside the scanner's guarantees. Only redacted finding metadata is retained. A completed scan does not approve publication. Review results and choose licensing/visibility separately before making the repository public.
