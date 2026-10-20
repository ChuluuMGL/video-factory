# 便携视频模型接入（开发候选）

此候选连接安装选择、n8n 加密凭据、负责人费用计划、单次提交、已有任务轮询和飞书结果回写。它不包含在 a38 固定包；可灵、Seedance 和万相仍需分别通过真实账户验收，不因历史项目成功或合成检查通过而算作已验收。

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
