# 获取固定发行版

起点是安装指南页面和 完整 Setup Skill（随包 skill/video-factory-setup/）。当前为私有受控测试版，安装者需要 ChuluuMGL/video-factory 读取权限，或由获授权同事安全交付已核验的包。

全部云端检查通过后，流水线创建固定版本预发行版，包含 CLI 包、完整 Skill ZIP、release.json、SHA256SUMS、同版离线镜像与 handoff.json。大文件只在云端下载，不要求客户粘贴维护者 token。

Agent 获取指定标签与可信摘要，核验后按 INSTALL.md 使用 start.py 进入安装和 Setup。已有服务新增项目沿用兼容版本，不因新 Skill 自动升级服务器。

源码分支与 CI 临时产物不是长期发行地址。当前没有公开下载、独立签名或自动更新服务。GitHub 读取权限不等于产品管理员权限。
