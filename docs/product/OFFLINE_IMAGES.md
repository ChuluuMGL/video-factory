# 镜像网络受限时的部署

CLI 离线安装不等于服务镜像已准备。a22 增加与发行 wheels 和固定镜像摘要绑定的镜像包。交付方在可访问官方镜像仓库的 Linux amd64 云端导出；客户主机加载已验证镜像 ID，不连接仓库、不现场构建 runtime。默认入口仍为 `setup-run`。

## 交付方

使用同一候选归档安装的 CLI 及 `release/wheels`。Docker/Compose 已准备，目录归 root 所有且为 700：

```sh
install -d -m 700 /root/vf-image-bundle
/opt/vf-cli/venv/bin/vfctl stack export-images \
  --root /root/vf-image-bundle --wheelhouse /opt/vf-cli/release/wheels
```

输出 `images.tar`、`manifest.json` 和 JSON 回执。将回执的 `manifest_sha256` 经可信交付渠道提供，不能让 Agent 自动采用下载包自身声称的摘要。manifest 绑定完整 wheel 哈希、原始官方仓库摘要、linux/amd64、每个加载后的镜像 ID，以及归档哈希和长度。不会导出客户数据、密钥或账户。

GitHub 的 `cloud-offline-images-<run>-<attempt>` 私有候选产物保存 14 天，包含相同文件和实测回执。镜像包体积较大；不自动下载到安装人员电脑，不代替正式长期发布渠道。

## 客户主机

通过已有受信文件传输渠道将两文件放到 `/root/vf-image-bundle`。目录 700、文件 600、归 root 所有；不要放进 CLI、stack 或可被其他用户修改的目录。预先保证 Docker/Compose、Python/venv 和磁盘空间充足（归档上限 6 GiB，加载展开还需额外空间）。CLI 安装方式保持不变。

```sh
/opt/vf-cli/venv/bin/vfctl setup-run \
  --session /root/vf-customer/setup.json --root /opt/vf-customer-stack \
  --wheelhouse /opt/vf-cli/release/wheels \
  --image-bundle /root/vf-image-bundle \
  --image-manifest-sha256 <可信交付记录中的摘要>
```

向导会显示离线来源及计划摘要。输入 SKU、人工私有 TTY 密码和飞书凭据的规则不变；安装并不自动提交付费任务。已有在线安装在镜像拉取阶段失败、尚未构建 runtime 时，可用原 session/stack 加以上参数续接。已运行的在线部署转换为离线镜像要走升级，不原地替换。

错误摘要、不同版本、平台不匹配、篡改或符号链接会拒绝。镜像加载后再回读 ID 和平台。加载失败可能留下部分镜像缓存，保留错误回执，用同一完整包重试；不自动删缓存或改镜像源。同版本成功安装再次运行可复用；不能用新包改变现有部署绑定。

## 恢复与升级

镜像包和加密业务备份分开保存。镜像包不含业务数据。离线部署在新主机冷恢复后，先用同一原包 `stack fetch --root <恢复目录> --image-bundle ... --image-manifest-sha256 ...`，再执行 `stack build`（校验并选择已加载 runtime）与 `stack up`。

离线升级要提供候选版 CLI、wheels 和候选镜像包：在已有 `stack upgrade` 命令上加上述两个镜像参数。缺少候选包时会在停止原服务之前拒绝。升级仍需原来的加密检查点、独立候选目录和验收回读。不得把新版本镜像包用于旧版本恢复。

已有健康的离线栈、候选版只替换产品 wheel 且其他依赖与固定镜像完全相同时，可以在同一 Linux 主机复用已验证的旧镜像制作候选包，不再访问镜像仓库：

```sh
vfctl stack export-images --root /root/vf-next-images \
  --wheelhouse /root/vf-next-wheels --source-root /opt/video-factory
```

这一步只构建候选 runtime 镜像并导出四个角色镜像；校验旧栈清单、已加载镜像、依赖哈希和新产品 wheel。输出的候选 manifest 摘要仍须从可信回执独立传递，之后按正常 `stack upgrade` 冷备份、克隆和回读。旧栈保持运行，导出失败不能视为升级成功。

## 验收边界

云端新增空 Docker 镜像库、无外部网络命名空间验收，验证校验拒绝、加载、安装、重复安装、重启、冷恢复。此项通过仍不替代原 ECS 的复测，也不等于真实飞书/真人操作/付费生成验收。

### a23：跨镜像存储兼容检查

a22 的实际 ECS 复测发现，按裸镜像 ID 导出时，归档的 Docker `manifest.json` 可含四个镜像，但 OCI `index.json` 仅可达一个镜像。原来的 classic Docker 回归未覆盖 containerd 导入器，因此不能据此宣称客户主机已验收。

a23 按角色和内容摘要创建明确的导出标签，保存四个标签；导出后同时核对 Docker 清单与 OCI 索引可达的配置摘要集合。客户仍按可信清单里的镜像 ID 运行，不依赖可变标签。云端回归分别使用空的 classic/containerd 镜像库，断网执行安装、续接、重启和恢复。导入后缺少镜像会报告 `IMAGE_BUNDLE_REQUIRED_IMAGE_MISSING`，不会输出 Docker 原始错误里的潜在秘密。

### a24：固定摘要在两种 Docker 存储中的识别

containerd 的镜像 ID 使用 OCI 摘要，classic 使用配置摘要。a24 的镜像清单 v2 同时记录这两个摘要，并从已经校验的归档索引证明二者对应关系。导入后只接受这两个固定摘要之一；将主机实际识别的摘要写入部署配置，Compose 继续按摘要运行并禁止拉取。重新构建恢复栈时重新识别，因此备份不把原主机的存储类型强加给目标主机。旧清单 v1 仍可在支持其配置 ID 的引擎中使用。

a23 首轮回归：classic 完整通过，containerd 缺失配置 ID 的检查失败。a24 双摘要修复必须重新通过云端测试和原 ECS 验收，不能沿用 a23 的部分通过结论。
