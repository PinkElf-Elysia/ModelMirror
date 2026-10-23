# OpenRouter 2026-10-22 更新验收记录

## 范围与基线

- 工作树 `C:/tmp/modelmirror-openrouter-oct21`，分支 `codex/openrouter-oct21-refresh`，基线 `9d1cc1ba`。
- 单一目标：补齐十个指定模型并同步目录漂移。涉及目录、专用注册、价格护栏、统计、测试与用户帮助，无法在五个文件内保持完整契约，因此按同一目标跨文件修改。
- 不改变默认模型、公共请求结构、数据库或部署配置；不接触共享栈、凭据及其他工作树。
- 原始四源窗口位于 `C:/tmp/openrouter-oct22-window/manifest.json`，UTC 2026-10-22 07:46:21–07:46:39（Phoenix 00:46:21–00:46:39），各源哈希见清单。

## 适配与边界

- Flint：文字聊天、工具与推理目录声明；不更换任何默认模型。
- Mammoth / Elephant / Drex Max：专用 Decisions 请求白名单，复用文字状态及 noul/choice/score 校验；最多 50 题，不转为聊天。
- Nemotron 3.5 Embed 1B/8B 与 Rerank 8B：复用现有文字检索入口和兼容接口；上游图像输入没有本地适配，不声明完整多模态支持。嵌入空间仍须独立资格验证，不迁移既有索引。
- Voxtral TTS 3：四个目录已发布预设音色、既有 MP3 输出；字符费用覆盖 $13/M。无克隆入口，保留人工验证门禁。
- Voxtral Small/Mini Transcribe 3：复用 JSON 原始 Base64 音频转写，保留格式检查；分别约 $0.24/$0.21 每小时，秒价保留目录精度。未开放时间戳、说话人分离和词汇偏置。
- 按现有后排稳定排序加入卡片，不占旗舰位。移除失效 `google/gemini-2.5-flash-lite:batch`，实时条目保留。同步价格、分类和生命周期；最新上游撤回三条过期时间，按权威目录恢复。

官方契约来源：OpenRouter 公共模型及端点目录、市场 display_pricing，官方文档 `guides/overview/multimodal/stt.mdx`、`tts.mdx`、`api_reference/embeddings.mdx` 与 `cookbook/evaluate-and-optimize/rag.mdx`。未发送推理请求。

## 验证

- 最终严格 `audit-current.ps1`：clean，目录、分类、价格、生命周期、Batch、readiness 均通过；报告 `C:/tmp/openrouter-oct22-final/summary.json`。
- 747 条快照 = 613 在列 + 106 可能不可用 + 28 过期；719 为现有适配分母，不是实调可用数；79 个 Batch 不计数。
- 前端 Vitest：models.refresh、models.oct22、models.oct16、models.oct20、openrouterBatch、helpContent，共 136 通过。
- 后端 pytest：oct22_contracts、openrouter_decisions、multimodal_tts、multimodal_stt、rag_embedding_contract、rag_rerank_contract、openrouter_batch，共 140 通过，4 个既有 FastAPI 弃用警告。
- 更新脚本与价格护栏 Node tests：25 通过。
- `npm.cmd run build`：通过，保留既有大 chunk 警告。
- 真实供应商、网关资格、预览重放、截图、全量后端测试：未运行。既往轮次豁免不继承。
- Commit / Push / PR / Merge / Deploy：未执行。

## 本轮发布授权与豁免

2026-10-22 用户明确请求提交 PR，并批准仅豁免本轮预览重放与截图门禁。两项均记录为未运行，不标记通过；不豁免代码、契约、价格与审计测试，也不授权付费调用或部署。

提交前四源清单为 `C:/tmp/openrouter-oct22-pr-window/manifest.json`。复核发现 GLM 5.3 Flash 及其 latest 别名供应商字段变化，已同步市场快照；严格复核报告 `C:/tmp/openrouter-oct22-pr-final/summary.json` 为 clean。真实调用、网关资格及 Nemotron 图像检索仍未验证或未适配。

## 回退

需要回退时定向撤销本轮目录与注册改动，不删除历史模型、运行数据或其他工作树；发布后可回退本轮提交。
