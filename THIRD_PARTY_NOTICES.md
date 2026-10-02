# 第三方组件 / Third-party components

Video Factory 的源码许可仅适用于本项目拥有版权的代码和文档。外部服务、镜像、依赖包、商标及案例媒体不因此获得相同许可。

## Python 依赖

公开包不重新分发第三方 wheel；客户服务器按固定哈希直接从 PyPI 官方文件源获取。发行构建保留依赖清单和每个 wheel 自带的许可文件，并生成包含名称、版本、摘要与许可元数据的清单。Psycopg 采用 LGPL 条款；它及二进制组件的对应源码与许可应一并提供，不能只附本项目的许可证。Cryptography、cffi、pycparser 和其他传递依赖分别保留上游条款。以固定发行包中的依赖清单和许可原文为准。

## 服务器镜像

Python、PostgreSQL、n8n、nginx 和 FFmpeg 由客户服务器从各上游镜像仓库按固定摘要获取。公开发行不提供早期测试用的整套离线镜像包，也不改变上游许可。网络受限环境应由有权使用组件的部署方按固定清单准备镜像，不能任意换源或混用版本。

- [Python 容器源码与许可](https://github.com/docker-library/python)
- [PostgreSQL 许可](https://www.postgresql.org/about/licence/)
- [n8n 许可原文](https://github.com/n8n-io/n8n/blob/master/LICENSE.md)与[官方使用场景说明](https://support.n8n.io/article/can-i-use-your-license-for-my-use-case)
- [nginx 许可](https://nginx.org/LICENSE)
- [FFmpeg 法律与许可说明](https://ffmpeg.org/legal.html)；固定镜像提供方：[mwader/static-ffmpeg](https://github.com/wader/static-ffmpeg)

**n8n 不是由本项目重新授权的组件。** 客户自有环境中的内部使用、协助客户部署、提供托管服务和嵌入式产品是不同场景；官方对这些场景的许可要求不同。Video Factory 的默认架构是客户独立服务器上的内部工作流。若改为集中托管客户工作流、对外出售 n8n 访问或白标嵌入服务，应按实际用途向 n8n 核对授权，不能依据本项目的开源许可作结论。

## 模型与素材

飞书、脚本/视频模型和媒体资源受各自的服务与内容条款约束。开源不包含客户账户、模型额度、客户素材的再分发许可，也不授权复用月瑀科技品牌或官网案例媒体。

---

The project license covers only Video Factory's own code and documentation. Dependencies, container images, external services, trademarks and example media retain their respective terms.

Public archives do not redistribute third-party wheels. Customer servers fetch hash-locked files directly from the official PyPI file host. Releases retain wheel license texts and dependency/version/hash metadata. Psycopg and its binary component use LGPL terms and require their corresponding source and notices; the project license alone is insufficient. Other direct and transitive dependencies retain upstream terms.

Servers obtain pinned upstream images directly. Public releases do not redistribute the former offline image bundles. Network-restricted deployments must prepare appropriately licensed images without substituting arbitrary registries or versions.

n8n is not relicensed by Video Factory. Customer-owned internal use, setup consulting, managed hosting and embedded products have different licensing requirements. The default architecture uses each customer's own internal deployment. Verify upstream authorization for hosted or embedded offerings; Video Factory's license cannot grant it.

Feishu/model accounts, generation credits, customer assets, company branding and website case media are not licensed for redistribution merely because the product source is public. See the upstream links above and the exact release notices.
