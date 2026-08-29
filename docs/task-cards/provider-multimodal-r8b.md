# 任务卡：R8B 图片、Vision 与原生 PDF Provider 控制面

## 范围

- 实施基线：`origin/main@be056e994d99fca2dcc158bd42cc71e8ebfc3db7`，已包含 R8A。
- 接管 `chat_image`、`chat_document_native`、`rag_vision`、Workflow 交互/部署 Vision、
  `xpert_vision` 和 `image_generation`。
- 只统一精确 Binding、Adapter、资格、出口、单次派发和 Receipt；Chat SSE、Vision JSON、
  原生 PDF 与 Images 响应继续使用各自协议。
- 不接管 STT/TTS、Chat Audio、音频生成、视频或 Realtime，不改变 Catalog 与提示词选择器口径。

## 安全边界

- 所有 R8B Feature Flag 默认关闭；Policy 为 `legacy` 时原路径保持不变。
- `managed_required` 只接受当前模型、连接指纹、scope、资格和 Adapter 完全匹配的 Binding。
- 一个逻辑调用最多派发一个 POST；派发后不得切换第二 IP、连接、模型、Adapter 或 legacy。
- 图片与原生 PDF、工具或其他未经同形态认证的组合请求在 POST 前失败关闭。
- OpenAI-compatible Images Adapter 固定使用 `/images/generations`、`size` 与
  `response_format=b64_json`；未验证的高级参数不静默透传。
- 控制面不保存图片、PDF、Prompt、Vision 结果、生成图片或完整上游错误体。

## 验收

- 固定 PNG、有效单页 PDF 和低尺寸图片生成资格各自独立；资格不能跨 shape 继承。
- Chat 图片、原生 PDF、RAG/Workflow/Xpert Vision 和图片生成的 Receipt 与实际目标一致。
- HTTP 错误、超时、断流、取消和响应超限均不重放；确定失败与 uncertain 可区分。
- Managed 图片生成要求 `Idempotency-Key`；重复键在任何第二次 POST 前阻断。
- R8A、R5—R7、多模态 legacy、Chat 文件与 SSE 回归通过，并完成严格证伪测试。
- 独立预览、真实资格和各用户入口 Smoke 分别验收；付费调用逐次授权。

## 回滚

关闭对应 R8B Feature Flag 并重启即可恢复 legacy。保留 v18 表、资格和脱敏 Receipt；
不得删除 Router SQLite、Provider 凭据、媒体任务或 newAPI 数据。

## Help Center Impact

- 影响用户体验：是。Settings 会把 R8B 七个入口显示为“数据面已接入”，其余 R8 入口仍保持阻断。
- 正式文章：`client/src/content/help-center/articles/recover-unavailable-feature.md`。
- 独立预览证据：`docs/help-center/evidence/provider-multimodal-r8b.md`。
