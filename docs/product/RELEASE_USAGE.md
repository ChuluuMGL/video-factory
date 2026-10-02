# 获取固定发行版

从[官网安装指南](https://www.yueyu.tech/zh/products/video-factory/)或本仓库完整 Setup Skill 开始。a31 起采用 MIT 许可，固定 GitHub Release 可匿名下载，无需 GitHub token。

云端验证通过的版本提供服务器安装包、完整 Skill ZIP、release.json、SHA256SUMS、依赖许可与 handoff.json。Psycopg 对应源码保留在包内；上游服务镜像由客户服务器按固定摘要获取，公开发行不附带整套离线镜像。

Agent 获取指定标签与摘要，在客户服务器核验后按 INSTALL.md 使用 start.py 进入安装和 Setup。已有服务新增项目沿用兼容版本，不因更新 Skill 自动升级服务器。

源码分支和临时 CI 产物不是长期发行地址。当前不提供独立签名或自动更新服务。仓库公开不授予客户服务的管理员权限；独立客户及真实模型验收另行记录。

a31 公开包包含项目自身 wheel 和固定依赖下载清单。首次安装由同一入口从 files.pythonhosted.org 获取外部 wheel 并逐一校验 SHA-256；服务器也需能访问服务镜像源。已完整获取的依赖可离线复用，不重复下载。

## 维护者复测与发行

`Cloud regression` 的 `create_draft` 默认为关闭，PR 和日常复测不发布。新版本只能从 main 手动勾选该项；先更新版本，再通过全部检查创建草稿。现有同名 Release 会拒绝创建，不能覆盖已经给客户使用的标签或摘要。

隐私检查发现待审查项返回失败，扫描异常也不通过；只有明确的合成凭据和运行时秘密文件引用可作为非秘密结果处理。检查通过不自动公开草稿，也不代表真人业务验收通过。
