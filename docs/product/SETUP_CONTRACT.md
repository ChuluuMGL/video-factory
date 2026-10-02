# Setup 交互与状态契约

本文定义分阶段契约，最初写于 a7；其中状态名是设计要求，不代表每个 CLI 都返回同名字段。当前已实现离线规划、受控部署、加密凭据、飞书接入与常驻工作区，准确命令见 [客户指南](CUSTOMER_GUIDE.md)和[当前安装能力](NEW_INSTALL_CAPABILITIES.md)。真实模型与独立人员验收仍见 [当前状态](https://github.com/ChuluuMGL/video-factory/blob/main/docs/product/STATUS.md)。

## 入口与对象

安装页给命令或 Agent 提示词；CLI 进入欢迎流程。首次装组织环境与新增项目共用工具，后者不重装基础服务。Skill 不复制一套 shell 实现。

TTY 提供选项与隐藏输入，Agent 模式提供结构化下一步骤；共用会话、默认值和验证器。无 TTY 不等待不可见密码输入，而返回 needs_secret 并给出支持的安全交接方式。

| 对象 | 内容 |
|---|---|
| Organization | 客户组织、管理员身份引用，不依赖供应商中心账号 |
| Deployment | 准确主机/环境、组件版本、持久存储、连接健康 |
| Project | Base/表、SKU/事实、审核角色、分能力路线、凭据引用 |
| SetupSession | 会话 ID、schema、目标摘要、步骤、已核验回执、阻塞与时间 |
| SecretReference | 所属客户/项目或明确共享范围、用途、存储引用，无明文 |
| Capability | 脚本/视频/语音、供应商、模型、参数限制、适配版本、验证等级 |

调用者必须经过客户环境身份和角色核验，不能仅凭任意 project_id 访问 Key。共用 n8n 不等于员工可以进入管理员界面。

## 步骤

| 步骤 | 输入 | 成功证据 |
|---|---|---|
| welcome | 新装/继续/新项目；默认飞书+n8n | 明确模式与会话版本 |
| organization | 组织、管理员/恢复安排 | 客户侧身份绑定，不假称账号已创建 |
| server | 云主机、SSH 身份引用 | 主机身份、容量、端口、权限检查 |
| integrations | 准确飞书目标与 n8n | 身份、权限和对象回读 |
| models | 分能力模型、凭据、费用归属 | 明示适配与检查等级；鉴权不等于生成验收 |
| project | SKU、事实来源、审核人、表绑定 | 必填与准确对象检查，缺项提出具体问题 |
| plan | 创建/复用内容、费用相关动作 | 固定目标/操作摘要，计划不创建资源 |
| apply | 执行已确定计划 | 每步创建/复用/未知/冲突的持久回执 |
| verify | 服务、连接、项目、任务 | 分层结果，不将自动检查计为真人验收 |
| handoff | 客户接管、恢复说明 | 客户独立管理与临时支持权限撤销记录 |

已有授权且目标未变化时不反复请求相同许可。真实生成与费用须在计划中可见，不得以连接测试之名暗中付费。

## 状态与恢复

- 输入：needs_input / needs_secret / plan_ready。
- 执行：applying / needs_attention / installed_disabled。
- 独立验收字段：runtime_ready、integrations_verified、project_ready、technical_canary_passed、human_acceptance_passed，各附证据，不压成一个成功状态。
- 原子保存、并发锁、文件权限限制；日志、参数与 JSON 不含 Key、密码或原始认证头。
- 恢复读取检查点后再核对实际资源；目标变化需新计划，不沿用旧执行许可。
- 不确定写入保留意图；识别准确资源后才继续。不得删除客户数据或接管无关服务。
- 会话 schema 必须显式迁移，不支持的旧版本给恢复指引，不猜测字段。

首批云端测试覆盖 TTY/Agent 等价、中断、并发、秘密、目标漂移；接入真实服务后覆盖未知创建、准确回读、重复安装与越权。显示欢迎页不等于安装跑通。
