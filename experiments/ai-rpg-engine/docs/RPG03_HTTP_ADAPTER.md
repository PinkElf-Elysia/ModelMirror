# RPG-03 ModelMirror HTTP adapter

离线门禁及官方 Luna 真实复测均通过，两次连续回复明确提交，取消的上游确认仍未知。本轮待人工验收。

## 端口

`/runtime/node` 导出 `createModelMirrorAdapter({baseUrl,evidenceKind="real",timeoutMs=60000,maxOutputTokens=512})`，返回稳定报告，成功时 `value` 为冻结适配器。其 `evidenceKind` 是调用方显式选择的证据标签，不能单独证明真实调用。必须先 `await adapter.initialize()`；之后 `generate(request,{signal,onText})` 消费 03C 的生成请求，返回 `{valid,diagnostics,value}`，失败也保留可得的草稿和合法回执。

`value` 包含 `status/outcome/dispatched/text/observedModel/serverReceipt/cancellation/usage`。`dispatched=true` 只表示已尝试向模镜发起 POST，未知送达按可能消耗处理，不是 Provider 已派发的独立证明；03I 必须与服务端 run/attempt 和调用账本核对。生成 ID、资源绑定、proposal hash 与运行回执由 03F 负责。

## 受控路径

地址只接受宿主配置的 HTTP(S) origin，禁止用户信息、query、fragment、路径前缀和重定向。不接受供应商 key、卡片地址或动态请求地址。初始化读取 `/openapi.json`，沿实际 `/api/chat` POST requestBody 引用确认 `require_managed_route` 为布尔字段；重新初始化失败会清除就绪标记。

每次生成都读取 `/api/models/provider-chat-control`，核对精确 model、`chat_text`、路由合同、feature/data-plane/available 以及受控 preferred/required 模式。POST 恒带 `require_managed_route=true`、`gateway=default`、`tool_mode=none`、`output_mode=none`、`compression.mode=off`。资格查询至派发间的竞态继续由 03B 服务端守卫处理。不新增 fallback 或重试；服务器受控 preferred 的预检备用路由可以通过，legacy 不可通过。

## 流与取消

严格 UTF-8 解码并跨分片处理 CR/LF/CRLF，支持注释和多行 data。成功必须依次具有 `finish_reason=stop`、合格 `route_receipt`、`[DONE]` 和完整 EOF。可接受一次 stop 后、回执前的标准 usage 尾帧，允许固定的 OpenAI 元信息和 token details；已知 token 必须与回执一致，不允许重复或在回执后改写用量。缺失、重复、不符模型或不完整流均不能形成成功结果。HTML 和正文始终是文本，工具、媒体和非文本 choice 结构被拒绝。

仅实际流中的 model 字段可成为 `observedModel`；缺失时为 null。服务器 header/receipt 的 actual_model 可能来自 requested model 回填，不能冒充独立上游观察。失败 HTTP/SSE 回执只在白名单结构和请求绑定可验证时保留，不转发原错误正文。当前成功资格固定匹配本基线服务器的 version 2 受控回执；价格在运行回执顶层保持未知，不能由文本估算。

取消使用 AbortSignal，区分 requested、clientAborted 和未知的 upstreamConfirmed。回调等待可被中断，同一已缓冲 chunk 的后续事件不能绕过取消。完成后的取消竞态不改写既有结果，后续运行层另行报告操作结果。客户端中止不证明上游已停止，也不撤销已发生费用。

## 固定限制与验证

OpenAPI 4 MiB、控制面/错误 JSON 1 MiB，均按实际字节有界读取；SSE 总量 8 MiB、单事件 1 MiB 字符、单事件最多 4096 data 行、最多 4096 事件；草稿最多 1 MiB 字符。超限拒绝，不截断。timeout 为 1～60000 ms。本轮适配上限为 1～512 输出 token；03C 数据合同仍为 32768，不通过扩大本轮适配限额消耗额外调用。

`node --test tests/runtime-adapter.test.mjs`：原 19 项全部保留，新增 2 项参数化用量尾测试，共 21/21 通过。包括旧 OpenAPI、控制拒绝、无重定向/重试、Unicode、跨片 CRLF、EOF、受控备用、失败回执、用量漂移、取消和永不完成的观察回调。仅使用 loopback 假服务。边界 10/10、冻结 67 和 `git diff --check` 通过。最终代码另经真实本地 ModelMirror HTTP＋假上游完成提交、恢复和取消，回执见 `RPG03_I_REPAIR_RECEIPT.json`；真实 Provider 复测最终通过，见 RPG03_REAL_ACCEPTANCE.json。

## 03I 用量尾兼容与证据边界

固定 newAPI 提交 `bc14c18f6024e79cba1c08d02cd007796e12d668` 的 [Usage DTO](https://github.com/QuantumNous/new-api/blob/bc14c18f6024e79cba1c08d02cd007796e12d668/dto/openai_response.go) 会输出额外 token 字段。根据该公开结构构造的尾帧能稳定复现旧适配器的 `RUNTIME_ADAPTER_EVENT_INVALID`。真实失败未留存原始 SSE，因此这是源码与症状相符的复现，不把推断写成已直接观察的致错帧。

现在允许 `input_tokens`、`output_tokens` 及两种 Claude cache creation 计数扩展，值必须为非负安全整数。三类 details 对象分别校验输入/输出字段白名单，只接受 null 或非负安全整数叶值，不接收未知字段、数组或嵌套数据；保留原有 details=null 兼容。唯一权威用量仍是 prompt/completion/total 三字段，并与服务回执核对；扩展不会形成价格、权限或状态语义。

此修复覆盖固定 DTO 的常规必出字段，不承诺兼容其所有输出。`prompt_cache_hit_tokens`、`usage_semantic`、`usage_source`、`billing_usage`、`cost` 等可选顶层字段仍拒绝；遇到这些形态须保存私有证据再单独审计，不能自动放宽为任意字段。真实测试使用的网关 force_format=true、reasoning_effort=none 也须记录，不能从此测试推断原始直通 SSE 或 OpenRouter 已验收。

最终真实账本 5/5：认证 1、历史失败 1、正常成功 2、取消 1。另行批准的第 5 次额度已消耗，剩余 0。原失败证据保留；复测 SSE 仅保存在私有目录。两个自有实例已停止。03J 聚合通过，待人工验收。
