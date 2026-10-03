# OpenRouter 2026-10-02 模型更新帮助中心验收（0de311ec）

## 验收范围

- 基底：`origin/main@0de311ec8b6c9e728612c76a8266269bfc1018bf`。
- 待审增量：Ling 3.1 Flash、NVIDIA Switchyard、Deepgram Flux TTS 付费条目，以及同步的目录漂移。
- 独立预览器：`http://127.0.0.1:5207`，由本轮隔离工作树启动；未复用共享栈。
- 受影响入口：`/models`、`/help/review-specialized-models`。

## 可见界面核对

2026-10-02 使用独立预览器逐项搜索并读取实际卡片：

1. `inclusionai/ling-3.1-flash`：显示“立即面试”、文本输入、文字对话/推理分析/工具调用、262K 上下文和动态价格。
2. `nvidia/switchyard`：显示“立即面试”、文本输入、1M 上下文和动态价格。
3. `deepgram/flux-tts`：显示“文字转语音待适配”、语音合成、36 个英文声线、MP3 和 `$45/百万字符`说明。
4. `deepgram/flux-tts:free`：仍可检索，但明确显示“可能不可用”和“当前未出现在实时模型目录”。
5. `/help/review-specialized-models`：标题、摘要、八步核对流程、Flux TTS 免费档说明和验证边界均可见；文章图片可正常加载。

帮助文章沿用并复制此前已核验的图片/语音卡片截图到当前基线目录：

- `client/public/help-center/0de311ec/openrouter-image-model.png`
- `client/public/help-center/0de311ec/openrouter-specialized-models.png`

这些图片用于既有 Seedream 与 MAI Voice 步骤；Ling、Switchyard 和 Flux TTS 的本轮结论来自独立预览器的可见 DOM 核对，不把旧截图冒充为本轮新模型截图。

## 验证边界

- 没有输入提示词、上传文件、提交聊天、生成图片或合成音频。
- 没有读取、复制或暴露任何 API 密钥。
- 没有执行付费 Provider 调用，因此不声称真实可用性、延迟、生成质量或最终账单已经验收。
- Deepgram Flux TTS 付费条目仍保持人工验证门禁；旧免费条目仅保留入口，不声称可用。

## 回退

回退本轮帮助文章、模型目录和 TTS 契约文件即可；不会删除历史入口、数据库或外部数据。
