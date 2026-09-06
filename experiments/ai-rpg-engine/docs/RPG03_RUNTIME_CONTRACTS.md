# RPG-03 runtime contracts

本文件说明同步纯数据合同层；调度、存储、HTTP 与受信插件宿主由独立实现层提供。格式均为独立严格闭合的 `0.1.0`；RPG-01 四个合同及 `/content` 不变。

## 公共接口

`/runtime` 的合同导出包含五个格式标识、十个 Schema、`canonicalJson`、请求/会话/回执/事件/插件授权验证器、`validateModelProposal` 与 `computeGenerationInputSha256`。03G 新增 `SET_PLUGIN_AUTHORIZATION_REQUEST_SCHEMA` 和对应验证器。验证结果为 `{valid, diagnostics}`；成功的 proposal/hash 另带 `value`。每项 diagnostics 只含固定的 `phase`、`severity`、`code` 和安全 JSON Pointer `path`；合同诊断不定位输入字段，因此 `path` 固定为空字符串，不转发 Ajv path、非法键、正文、凭据或堆栈。

03G 允许 create 请求显式附带 `pluginAuthorizations`，每插件最多一条 revision 0 authorize；read/resume 不接受这个字段。后续授权请求固定 sessionId、expectedRevision 和 authorization，记录 revision 必须为下一 revision。持久历史全局非降序、同插件严格递增，撤销须对应最近仍有效且版本/hash/证据类别相同的授权，并清空所有权限、范围和设置。重复设置键、重复初始授权和乱序历史均拒绝。宿主语义、启用与结果复核见 `RPG03_PLUGIN_HOST.md`。

`canonicalJson` 先用属性描述符检查普通 JSON：拒绝 getter/setter、非普通 prototype、symbol、函数/undefined/bigint、非有限数、循环、深度或节点超限，且不会读取 getter。对象键排序、数组保序。调用方注入的 hash 必须同步返回 64 位 SHA-256 十六进制；Promise 被拒绝。

## 请求与幂等身份

生成请求绑定 session/generation/exchange、expectedRevision、冻结 turn input、1 至 80 条已准备 system/user/assistant 纯文本消息、modelId，以及 temperature/maxTokens。单条最多 65536 字符，总量最多 262144，超限拒绝且不裁剪；maxTokens 合同上限 32768，真实验收的 512 上限由适配/批次门禁进一步收紧。

`inputSha256` 的 canonical 输入为 sessionId、session 资源 hash、exchangeId、input、messages、modelId、settings；刻意排除 expectedRevision 与 generationId。幂等键作用域是 `(sessionId,generationId)`：已有同 ID/同 inputSha256 返回既有记录，异 hash 冲突。实现层须先检查幂等记录，再检查 revision，避免自然重试因 started revision 变化而冲突。exchangeId 在会话内唯一。

## 会话、生成与提交

session 绑定 card id/version/canonical SHA-256 与 player setup id/canonical SHA-256。`state[{fieldRef,value}]` 必须完整且唯一地对应 card `stateFields`；初值来自 `initialValue`。`generations` 是 exchange 唯一所有者；pending 只保存 generationId/exchangeId 引用。一个 session 最多一个 active、一个 pending；pending 数量和引用必须与 generation status 一致。

状态为 active/pending/committed/discarded/cancelled/failed/interrupted。所有 revision 计数不得超过 `Number.MAX_SAFE_INTEGER`。startedRevision 必须为 requestRevision+1；非 active 必须有 finishedRevision。active generation 的 `modelId` 和 `evidenceKind` 是本次请求已解析的模型与证据种类，恢复时必须沿用，不能重新解析。finishedRevision/receipt.revision 表示模型生成终结并形成 pending 或失败的 revision；后续 turn.committedRevision 必须更大。pending/committed/discarded 保存已验证 exchange、成功 receipt，draftText 为空；cancelled/failed/interrupted 不保存 exchange，保留草稿并有对应非成功 receipt。active 不得已有 finishedRevision、exchange 或 receipt，且 active 与 pending 互斥。恢复层将遗留 active 原子改为 interrupted；对已有终态做后续解析时只新增 `resolvedRevision` 并推进 session revision，原 `finishedRevision` 与 `receipt` 保持不变，绝不重放。

恢复 pending 只推进 session revision，保持整个 generation 和完成回执不变；`resolvedRevision` 仅由之后的显式 commit/discard 增加。

03F 内部的取消恢复修复新增可选 `generation.cancelRequestedRevision`。它与当次取消请求的 draft、session revision 一起 CAS 落盘，必须大于 startedRevision、不大于当前 session revision，终结后还必须小于 finishedRevision。有此标记的生成不得成为 pending/committed/discarded，且终态 receipt 必须保留 `cancellation.requested=true`。进程在取消已接受、传输尚未结束时退出，恢复仍为 interrupted，但不能将已接受取消改写为 requested=false；clientAborted 与 upstreamConfirmed 不能仅凭标记推断。该修复只扩充本轮尚未交付的运行合同，不修改 RPG-01/02 的 67 个冻结文件或四种旧合同。

模型只返回 narrative、suggestedActions、informationModules、stateProposals、uncertainties 五键 proposal。runtime 添加冻结 turn envelope后调用 `validateTurnExchange`；草稿不能充当 proposal。

commit/discard 必须同时匹配 current revision、pending generationId 与 exchangeId。commit 的 acceptedStateFields 唯一且只能选 pending proposal中字段；query 必须为空。正式 turn 保存完整 exchange、generationId、committedRevision、acceptedStateFields。实现层须用 store compare-and-swap 在一次原子提交中同时更新 state、turn、generation、pending 与 revision。验证器可用 card 初值和按 committedRevision 排序的 turns 重放最终 state。

## 回执、事件与插件授权

generation receipt 绑定 session、card/player hash、generation/exchange、revision 与 evidenceKind(mock/real)，包含终态/outcome、requested/observed model、白名单 serverReceipt、三项 usage、未知 costUsd=null、output hash 和三态取消事实。成功时 `outputSha256` 精确定义为 `canonicalJson(exchange.proposal)` 的 SHA-256，不是 SSE 分片或原始空白文本；失败、取消、中断时为 null。serverReceipt 白名单对应现有 Managed Chat `route_receipt`：requested_model、actual_model、provider、strategy、engine、reason_codes、latency_ms、ttft_ms、tokens、response_cost_usd、cost_kind、fallback_attempts、cache_hit、request_id、version。它不保存原始错误 body。observedModel 可为 null，未知值不得用 requestedModel 补造；成功回执要求非空 observedModel 与 requestedModel 一致，失败回执可记录适配器真实观察到的不一致模型。取消必须声明 requested。取消请求晚于成功完成时可以诚实记录 requested=true，但 clientAborted/upstreamConfirmed=true 与成功互斥。这些只证明数据自洽，不证明真实 Provider 已通过。HTTP 适配器只提供文本和协议证据；完成冻结 turn 验证后才由运行核心形成成功回执。

event 是 draft/status/receipt 严格互斥 union，全部绑定资源、generation/exchange、revision、evidenceKind 和单调 seq；receipt event 还逐字段核对内嵌回执绑定。

plugin authorization 只记录 authorize/revoke、可信 plugin/version、manifest/artifact hash、显式 permissions/read/propose、primitive settings、revision 与证据种类。本批不加载代码，也不授予实际模型、网络、记忆或文件能力。
