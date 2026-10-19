# 便携脚本模型接入（开发候选）

本页对应源码候选，不属于 a37 / a38 固定发行包。DeepSeek 的历史真实验收保留；其他供应商完成云端合成测试后仍需要各自真实账户的独立验收，不能把接口兼容当作已通过付费生成。

## 安装与续接

使用包含 `scripts/portable_models.py` 的完整 Skill，运行 `portable_n8n.py welcome`。选择「配置脚本流程」后，选择供应商并输入其模型 ID。Key 在终端隐藏输入，由 n8n 加密保存；本地配置只保存供应商、模型和凭据引用。配置仅导入关闭状态的流程，不调用模型。

已有项目续接自动沿用原模型和 Key。新供应商请使用新的项目标识，单独配置其 Key 和负责人验收；不会将已有 DeepSeek Key 自动发送给另一家供应商。直接编辑本地模型文件会触发凭据或审核作用域校验。已有项目原地升级保留模型选择，不恢复成默认 DeepSeek。

分步接入和导出支持 `--script-provider`、`--script-model`，人工测试仍需 `--experimental-manual`。负责人流程显示并确认实际接口、模型及输出 token 上限；确认后的配置变更会拒绝执行，要求重新准备计划。旧计划不迁移。

| 选项 | 官方接口区域 | 默认模型 | 单次输出限制 |
|---|---|---|---|
| `deepseek` | DeepSeek 官方 | `deepseek-flash` | 2048 tokens |
| `openai` | OpenAI 官方 | `gpt-4.1-mini` | 2048 tokens |
| `gemini` | Gemini Developer API | `gemini-3.8-flash` | 2048 tokens |
| `minimax-cn` | MiniMax 中国区 | `MiniMax-M2.7` | 4096 completion tokens（含思考） |
| `minimax-global` | MiniMax 国际区 | `MiniMax-M2.7` | 4096 completion tokens（含思考） |
| `qwen-cn` | 百炼北京传统域名 | `qwen-plus` | 2048 tokens |
| `doubao-cn` | 火山方舟北京 | `doubao-seed-2-1-lite-260915` | 2048 tokens |

供应商默认模型只是配置起点，须确认自己账户已开通。当前候选只支持表列官方接口，不接受任意第三方地址、不猜测 Key 所属平台、不跨区域自动重试。百炼工作空间专属域名暂未纳入此候选；不能用北京 Key 接其他区域。思考内容不写入脚本，截断或格式不符停在待核对状态，不自动重发模型请求。

视频候选另见 [便携视频接入](PORTABLE_VIDEO_MODELS.md)；脚本接入不能代替视频或 Seedream 图片的真实验收。

接口依据（2026-10-19 核对）：[Gemini OpenAI 兼容](https://ai.google.dev/gemini-api/docs/openai)、[MiniMax OpenAI 兼容](https://platform.minimax.cn/docs/api-reference/text-openai-api)、[百炼 OpenAI 兼容](https://help.aliyun.com/en/model-studio/compatibility-of-openai-with-dashscope)、[火山方舟 Chat API](https://docs.volcengine.com/docs/ark/chat-api?lang=zh)。
