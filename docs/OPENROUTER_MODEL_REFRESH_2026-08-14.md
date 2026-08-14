# OpenRouter 模型快照更新（2026-08-14）

## 结果

- 本地快照由 537 个更新为 543 个：483 个当前条目、55 个可能不可用保留条目、5 个明确过期条目。
- 批处理服务档位由 62 个更新为 63 个；`google/gemini-3.7-flash:batch` 并入基础模型，不计入快照总数。
- 用户指定的 7 个模型均已收录或刷新。其中 `qwen/qwen3-reranker-8b` 已存在于上一轮快照，因此本轮实际新增 6 个基础模型。
- 7 个模型均通过确定性散列分散到前五排之后，没有进入首页推荐位或固定特殊位置。

## 模型与调用契约

| 模型 | 目录契约 | 模镜入口与状态 |
| --- | --- | --- |
| `qwen/qwen3-reranker-8b` | 文本 → 重排；专用 `POST /api/v1/rerank` | 资料库重排候选；沿用已有入口 |
| `voyageai/voyage-code-4` | 文本 → 向量；32K；`POST /api/v1/embeddings` | 资料库向量模型候选 |
| `google/gemini-3.7-flash` | 文本/图片/视频/文件/音频 → 文本；1,048,576 context | 普通多模态对话；Batch 仅在对话页设置中出现 |
| `bytedance-seed/seedream-5-0-lite` | 文本/图片 → 图片；非流式 `POST /api/v1/images` | 图片生成界面；支持 2K/4K、18 种比例、1–4 张输出、最多 14 张参考图和 seed |
| `mistralai/voxtral-mini-3b-2507` | 音频 → 转写 | 独立转写界面；已适配、待真实短音频人工验收 |
| `mistralai/voxtral-small-24b-2507-stt` | 音频 → 转写 | 独立转写界面；已适配、待真实短音频人工验收 |
| `nvidia/nemotron-3.5-asr-streaming-multilingual-0.6b` | 音频 → 转写 | 独立转写界面；已适配、待真实短音频人工验收 |

## 多模态契约证据

- STT 请求已对齐当前官方 JSON 契约：`input_audio.data` 传原始字节的 Base64，`input_audio.format` 传音频格式；不再向 OpenRouter 上游发送 multipart 文件体。支持 WAV、MP3、FLAC、M4A、OGG、WebM、AAC，本地仍保留 25 MiB 上限和格式魔数校验。
- Seedream 5.0 Lite 的专用图片端点当前声明 `supports_streaming=false`，输出价格为 `$0.035/张`。专用目录能力优先于通用模型目录中继承或漂移的字段。
- Gemini Batch 的请求模型 ID 为基础 slug `google/gemini-3.7-flash`，输入能力在产品层收窄为纯文本，completion window 为 24h，数据保留提示为 30 天。目录价为实时档位的 50%：输入 `$0.1875/M`、输出 `$0.9375/M`。
- Rerank 与 Embeddings 都保留为资料库专用操作，不进入普通 Chat 候选池。

来源：

- `https://openrouter.ai/api/v1/models?output_modalities=all&sort=newest&offset=0&limit=1000`
- `https://openrouter.ai/api/v1/models?output_modalities=transcription`
- `https://openrouter.ai/api/v1/embeddings/models?offset=0&limit=1000`
- `https://openrouter.ai/api/v1/images/models`
- `https://openrouter.ai/api/v1/images/models/bytedance-seed/seedream-5-0-lite/endpoints`
- `https://openrouter.ai/docs/guides/overview/multimodal/stt`
- `https://openrouter.ai/docs/guides/overview/multimodal/image-generation`
- `https://openrouter.ai/docs/api/api-reference/embeddings/create-embeddings`
- `https://openrouter.ai/docs/api/api-reference/rerank/create-rerank`
- `https://openrouter.ai/docs/batch-quickstart`

## 验证边界

- 已验证实时目录身份、架构、价格字段、图片端点参数、生成价格和本地请求结构。
- 已通过 MockTransport 覆盖 STT JSON Base64、图片目录/价格、图片参数校验和 Batch 归并。
- 未使用真实 OpenRouter 密钥产生计费调用；三款 STT、Seedream 5.0 Lite 和 Gemini Batch 仍不宣称真实供应商行为验收通过。
- “可直接调用”仍采用既有运行时证据口径，本轮不修改其算法或历史的 523 环境证据。

## 回退

回退本轮提交即可同时撤销 6 个新增快照、Gemini Batch 档位、三款 STT 手工验收档案、Seedream Lite 价格兜底和市场筛选元数据；不会影响已有模型服务连接或密钥。
