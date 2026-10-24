# 便携视频模型接入（开发候选）

a40 便携路线连接安装选择、n8n 加密凭据、负责人费用计划、单次提交、已有任务轮询和飞书结果回写。a38 固定包不含新增模型。本轮 Seedance 2.0 的单次生成、回写和完整解码通过，待本人审核；可灵、Seedance 2.5 和万相仍未完成真实闭环，见 [a40 记录](DELIVERY_A40_RESULT.md)。历史项目或合成检查不能代替客户验收。

## 配置

完整候选 Skill 的 `portable_n8n.py welcome` 在「配置视频流程」中提供以下选择。分步 `connect` / `export` 支持 `--video-provider` 和 `--video-model`。首次配置隐藏输入该平台原始 API Key，n8n 加密保存；续接复用原凭据。不同供应商或区域使用新项目，不将旧 H3 Key 发往新接口。

| 选项 | 默认模型 | 本候选单次计划 |
|---|---|---|
| `minimax-cn` | `MiniMax-H3` | 原 6 秒、768P、9:16 路线不变 |
| `seedance-cn` | `doubao-seedance-2-0-260128`，可选 `doubao-seedance-2-5-260628` | 北京；5 秒、720p、9:16、有声 |
| `wan-cn` | `wan2.7-i2v-2026-04-25` | 北京；5 秒、720P，比例随首帧 |
| `kling-cn` | `kling-3.0`，可选 `kling-3.0-turbo` | 北京；5 秒、720p，比例随首帧、有声 |
| `kling-global` | 同上 | 新加坡；其他参数同上 |

Seedance 2.0 与 2.5 是独立模型选项。安装人员先核对现有 Key 所属账号的模型开通状态，再选择对应版本；无需新建 Key，也不会自动开通新模型或把旧计划换成另一版本。2.0 使用官方模型 ID `doubao-seedance-2-0-260128`（[官方说明](https://docs.volcengine.com/docs/ark/seedance-2-0?lang=zh)）。

生成费用计划前先检查脚本长度、首帧数量、附件格式和大小；不符合要求时不占用确认计划。执行前仍核对相同输入，下载后验证图片内容。

新视频路线只接受一张保存在「参考图附件」的 PNG/JPEG 首帧，脚本最多 2500 字符；不会丢掉多余图片后继续收费。万相不自动扩写提示词，可灵不启用多镜头；各项参数包含在负责人确认的计划中。模型可用性和账户区域需独立核对，不跨区降级或重试。

安装只导入关闭的流程。先完成负责人零费用验收，再显式启用生产入口。脚本通过不等于视频付费授权；负责人确认准确计划后只提交一次。返回任务 ID 后只轮询，不重新 POST。未知结果保留执行账本，先对账；结果写回失败复用已保存回执，不重做生成。

本候选将供应商视频链接写回原行供人工审核。它不承诺供应商链接永久有效，也不声称已把成片复制进飞书附件。可选结果快照附件功能是另一项验收。Seedream 是图片模型，不混入视频选择。

接口依据：[可灵图生视频](https://kling.ai/document-api/apiReference/model/imageToVideo)、[Seedance 创建任务](https://docs.volcengine.com/docs/ark/create-video-generation-task-api?lang=zh)、[万相图生视频](https://help.aliyun.com/en/model-studio/image-to-video-general-api-reference)。

## 新星数科企业版（a41 之后的源码候选）

配置项 `seedance-novai` 独立于官方 `seedance-cn`，固定 `https://xinghezhiyun.com/api/v3/contents/generations/tasks`，支持当前配置列出的 Seedance 2.0 / 2.5 ID，默认 2.0。使用企业版平台自己的 Key，不复用官方方舟 Key；不同域名或区域需另核对，不能因品牌同名自动切换。请求和费用计划均固定 `auto_asset=false`，不隐式启用平台素材入库。

已核对[供应商公开 API 规范](https://xinghezhiyun.com/docs/openapi.json)的生产域名、`/v1/models` 与方舟兼容路径；现场只读认证及模型列表通过，未做该平台的付费生成、轮询、结果回写验收。合成测试覆盖请求参数、计划一致性、任务 ID / 模型回执匹配和查询不重发。它不在 a41 固定包，不能与官方 Seedance 已有验收混为一谈。
