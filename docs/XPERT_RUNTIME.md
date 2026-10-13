# Xpert Runtime Contract

## 运行目录单实例所有权

后端在构造 Runtime Store 前独占 `AGENT_TASK_STORAGE_DIR`（缺省为
`server/xpert_runtime/storage`），不能等到 startup 才领取，因为 Store 加载
已可能恢复并写回运行状态。Windows 使用字节范围锁，POSIX 使用文件锁；
同目录的第二个后端拒绝启动，错误码为 `RUNTIME_DIRECTORY_BUSY`。不支持多个
后端进程共享目录、预加载后 fork worker、共享目录的多 worker 或跨主机分布式锁。
该保护是后端入口的进程所有权，不是让单独构造的 Store 类自动变成多进程安全。

锁文件 `.runtime-owner.lock` 不包含凭据、运行数据或 PID 权威，退出后保留。
文件存在不代表被占用；进程正常退出或被终止后，OS 释放锁，下一进程可恢复。
不要删除/替换锁文件抢占，也不要在原进程存活时替换运行目录。锁持有至进程
退出而非 ASGI shutdown，避免在途请求尚未退出时与新后端交叠。startup 检查
当前 PID 和文件身份，拒绝继承旧句柄；独立预览必须使用自己的运行目录。
这是可信本地文件系统上的协作排斥，不防御同一用户主动删除锁或绕过入口写盘。

离线 `execution_store_recovery restore-backup` 同样要求独占目录，活后端存在时
不修改快照；`inspect` 保持只读但不承诺读取多个文件的原子视图。操作顺序仍为
停止原后端、检查并保存证据、明确恢复、核对外部效果后重启。锁拒绝只给出安全
错误码，不回显目录或 OS 异常。备份恢复仍可能丢失最近状态，不能自动重发未知
请求。回退必须先停写并保留快照，不能用删除锁文件解除运行中的排斥。

## Versioned Conversation Features

`XpertDraft.features` and immutable `XpertVersion.features` define the conversation contract for each published Xpert. Opening copy and starter questions, generated follow-up questions, conversation titles, context summaries, memory replies, file policy, TTS, and STT are resolved from the selected published version. Editing a draft or publishing a later version never mutates an older deployment.

Conversation title and follow-up generation use the existing OpenAI-compatible gateway and fail softly. Versioned summaries compile to an implicit `context_compression` middleware on the final output Agent. Character budgets are conservatively converted into estimated token budgets; original messages remain intact and only the derived summary state is updated.

File policy controls whether selected conversation assets enter the run, their allowed extension, and the per-run maximum. A disabled file feature preserves stored conversation files but excludes their IDs from Xpert and Goal execution. High-confidence memory replies only use memories already visible to that Xpert/conversation and fall back to the normal model path when confidence is insufficient.

Published private Xperts may contain `vision_understanding`. The runner injects only the selected `file_asset_ids` plus a convenience `selected_file_asset_id`; the node must resolve the asset through the owning Xpert and conversation recorded in runtime metadata. Goal and Handoff runs inherit only the explicitly shared file IDs. Cross-conversation lookup fails closed, while archived files remain readable only for already-started runs that retain an explicit reference. Public Xpert App deployment rejects this node because the public surface does not accept attachments.

Audio endpoints reuse `LLM_GATEWAY_URL` / `LLM_GATEWAY_KEY` or the OpenRouter-compatible fallback:

- `GET /api/xperts/{xpert_id}/audio-capabilities`
- `POST /api/xperts/{xpert_id}/audio/transcriptions`
- `POST /api/xperts/{xpert_id}/audio/speech`

The published feature config must explicitly enable audio and select a compatible registered model. Audio bytes, prompts, credentials, and unbounded message text never enter checkpoints.

`XpertAgentConfig.max_concurrency` and `recursion_limit` govern the entire Xpert execution tree. Per-Agent tool settings (`maxToolConcurrency`, `maxToolCalls`, `maxToolDepth`, and `maxIterations`) remain narrower local budgets and cannot expand the global limits.

预算准入在当前进程、同一事件循环的活动父子预算上统一检查与扣减；任一层拒绝时
所有层的计数保持不变。同一预算实例重复绑定只约束一次，不同实例仍各自约束。
模型/工具操作先检查余额，取得全部并发槽位后再次原子检查并扣减；排队取消不计
调用，并释放已经取得的槽位。进入操作体之后的错误或取消保留计数，不能以此
推断外部请求未发生或自动返还额度。此处计数是运行准入记录，不等同于实际账单。
仅有进程内预算上下文不足以证明后台子任务继承了根预算；已有持久父预算的
Handoff 与内联子调用还必须完成下文的服务端委托登记与双向绑定。

Legacy Workflow 流式回退不共用一次调用额度：原请求已消耗的准入计数保留，
回退派发前重新检查当前执行权限并原子扣减活动父子及持久根预算。两个顺序请求
继续占用同一并发槽位直至流关闭，不重复申请槽位。余额不足、取消、失权或
预算落盘失败时不得发出第二次请求。没有预算上下文的旧调用保持既有回退行为；
本修复不扩大回退条件，超时、损坏或未完整结束的响应不因此获得自动重试许可。

等待 continuation 已保存的 `steps_used/model_calls/tool_calls` 在 Xpert、App、
Evaluator 及普通 Workflow 视觉 V2 恢复时使用同一校验，不通过字符串转换、
截零或重新初始化来放宽预算。字段必须完整且仅含非负整数，模型与工具调用数
之和不能超过总步数。非法快照拒绝恢复且不回显原文；node_retry 保留
`NODE_RETRY_STATE_INVALID`，其他等待使用 `WORKFLOW_EXECUTION_BUDGET_INVALID`。
缺失或 null 的旧预算快照仍可读取，但没有历史消耗证据，不证明跨重启限额。
预算绑定现有 WorkflowExecutionStore 的私有 `execution_budget`，保存 version=1、
固定 limits 和 usage。首次绑定可读取旧 continuation；后续以持久计数为准，
允许 continuation 滞后但拒绝超前或限额漂移。恢复预算不一致明确失败，不能
误判为执行租约被接管。准入在操作体前原子落盘；同一 Store 的活动父子预算
全成或全败，不同 Store 权威拒绝派发。保存异常后 Store 不可继续使用，须重新
加载核对；若替换已完成但确认失败，保留可能已消耗的额度，不自动重置。

Managed Workflow 的文本流、Agent 文本/工具决策、文本 unary 与 JSON 修复调用
同样经过统一预算，资格及发生标识检查在前，预算落盘在 `mark_dispatched` 和
网络派发之前。并发槽位覆盖响应读取；未知结果保留消耗，发生标识阻止自动
重放。视觉委托 `vision_json_unary` 仍由视觉服务逐页包装，不重复扣减。
后台 Handoff 与内联子调用的持久根预算绑定见下文；进程所有权见运行目录单实例
章节。跨执行期限与综合恢复仍需验收，不能把本地准入计数描述为供应商实际账单。

## Office Client Runtime

Office Host 作为 `host_type=office` 复用 Client Tool V1 WebSocket、持久 request、operation ID 和 `wait_kind=client_tool`。宿主额外固定 `office_app`、随机 `document_binding_id`、Requirement Sets 与工具 schema hash；旧 Host 兼容迁移为 `host_type=chrome`。

`office_automation` 只在私有入口注册 `office_tools` capability。修改调用顺序固定为 Tool Policy、HITL、Client dispatch、Office.js 和 Audit；读取断线可回到 pending，运行中的修改断线进入 `uncertain`。Runtime 只记录宿主类型、工具、状态、耗时和结果长度，不保存文档正文、单元格值、图片 Base64、Host token 或密钥。详细契约见 `docs/XPERT_OFFICE_AUTOMATION.md`。

## Typed File Memory Runtime

`XpertFileMemoryStore` 在 Xpert Context storage 中维护派生 `MEMORY.md` 索引、四类 Markdown 正文、manifest 和安全使用信号。正式记忆写入使用原子替换和 revision；旧 Xpert 级 Memory 首次访问时懒迁移，会话级 Memory 不迁移。

`xpert_file_memory` 在 `workflow_agent` 模型调用前执行三层召回，并受单轮和单会话正文预算约束。选择模型失败时确定性降级，不能阻断主回答。模型写回只产生候选，审批冲突不允许覆盖较新 revision。Goal/Handoff 读取目标 Xpert 记忆；公开 App 仅支持显式开启的只读访问。运行观测只保存数量、长度、耗时和策略，完整边界见 `docs/XPERT_FILE_MEMORY.md`。

## Authoring Runtime

`AuthoringProposalStore` 在 Runtime storage 中原子保存 Xpert/Skill 创建与更新提案。运行工具仅能读取 Agent 配置允许的目标并创建 pending proposal；每个 run 最多 5 条，每个来源最多 20 条 pending。管理端编辑、校验、批准、拒绝与取消均使用 revision，目标更新额外固定 `base_revision`。

`AuthoringService` 是唯一草稿写入边界。批准 Xpert 提案只创建或更新 `XpertDraft`，不调用 publish；批准 Skill 提案只创建或更新 `WorkspaceSkillDraft`，不调用安装。公开 App 通过 middleware registry 的 `app_policy` 统一阻断自编写 capability。执行 audit/checkpoint 不保存提案正文、Skill 文件、prompt 或密钥，完整管理正文只由可信接口返回。详见 `docs/XPERT_AUTHORING.md`。

## Client Tool Host Runtime

Private Workflow、Xpert Chat、Goal 与 Handoff 可以把 `client_tools` 绑定到 `workflow_agent`。Chrome MV3 扩展只在用户主动授权的当前标签页执行固定工具；服务端通过持久请求、通用 `wait_kind=client_tool` 和 `ClientToolCoordinator` 从原执行断点恢复。

Client host 使用一次性配对码和哈希 token 认证。工具必须同时通过 Agent 配置、host capability/schema hash、ToolPermissionPolicy 和 mutating HITL。读操作断线可重放，执行中的写操作进入 `uncertain`。GoalStep、AgentTask 和 Handoff 使用 `waiting_client` 表示等待，不消耗模型/工具重试。公开 App/API 禁止 Client Tools。详见 `docs/XPERT_CLIENT_TOOLS.md`。

## Isolated Browser Runtime

Private Workflow, Xpert Chat, Goal, and Handoff runs can bind `browser_automation` to a `workflow_agent`. Chromium runs in a dedicated Playwright sidecar reached only through a Unix Domain Socket. A public-only egress guard and Playwright request routing both reject local, private, metadata, Docker service, mixed-DNS, and unsupported-protocol destinations.

Browser sessions, per-session domain grants, idempotent operations, screenshots, and downloads use a file-backed store. Conversation, goal/step, handoff, and workflow task/node scopes remain isolated. Mutating actions require durable HITL coverage; public Xpert App/API deployments reject Browser middleware. API, audit, event, and checkpoint payloads expose only safe metadata. See `docs/XPERT_BROWSER.md`.

Last updated: 2026-07-23

## Purpose

This document describes the first ModelMirror-native Xpert publishing contract. It aligns with Xpert concepts while retaining the repository's React, FastAPI, Pydantic, pytest, and classic workflow runner architecture.

The implementation does not copy Xpert source code or migrate its Angular, NestJS, Nx, or persistence stack.

## Resource Model

The server/xperts package owns a file-backed XpertStore. Its default storage location is server/xperts/storage/xperts.json, overridable through XPERT_STORAGE_DIR.

- XpertDefinition: identity, slug, name, description, tags, starter prompts, status, draft revision, and published-version pointer.
- XpertDraft: editable workflow snapshot plus the chat input, history, and output variable names.
- XpertVersion: immutable published workflow snapshot, version number, draft revision, release notes, checksum, and timestamp.

The Store uses an in-process lock and atomic temporary-file replacement. It is deliberately an adapter boundary: a future database migration must keep the API contract stable.

## Lifecycle

1. Create a draft Xpert with the default chat workflow: input(user_input) -> workflow_agent(agent_output) -> output(agent_output).
2. Edit and save the draft. Every save increments draft_revision.
3. Request validation. The server runs the existing graph validator plus the Xpert chat contract.
4. Publish. The server verifies the revision did not change during preflight, then records an immutable version.
5. Run a published version. The server injects the current user message and a bounded conversation-history JSON summary, then delegates to the existing classic workflow runner.

Draft updates never mutate a published snapshot. Archived Xperts remain inspectable but cannot run.

## Publish Contract

The first chat release requires:

- exactly one input node whose variable matches input_variable;
- exactly one output node whose variable matches output_variable;
- at least one workflow_agent with modelId, rolePrompt, taskInput, and outputVariable;
- reachable template variables, except the configured history variable injected by the published-chat runtime;
- private Xpert Chat may use `human_intervention` or bound `human_in_the_loop` because it exposes durable approval and resume UI; public Xpert App deployments reject both interactive forms.

Existing middleware, MCP Toolset, knowledge, AgentTask, Handoff, and RunRegistry validation remains in force.

## Durable Human Approval And Resume

Interactive private runs persist approval requests in `RuntimeApprovalStore` and continuation state in `WorkflowExecutionStore`, both under the Runtime storage directory. A continuation includes the bounded workflow queue, variables, executed-node set, and current workflow-agent ReAct state. Public APIs expose only redacted approval arguments and a safe sequenced event journal.

Tool execution is ordered as policy, approval, audit start, Provider, then audit completion. Approval interrupts are fatal control signals rather than ordinary middleware errors, so fail-open handling can never invoke the Provider while approval is pending. Edited arguments are schema-validated and policy-checked again before execution. A rejected tool returns an artificial tool result to the model without invoking the Provider.

`ApprovalCoordinator` claims resumable executions with a lease and continues from the suspended Agent action. Restart recovery clears stale process leases. Completed workflow nodes and approved tool calls are not repeated. Approval timeout never implies consent: direct Workflow/Xpert runs remain reopenable, while Goal/Handoff work moves to `needs_attention`.

The safe event stream is available at `GET /api/workflow/run/{task_id}/stream?after_sequence=`. Existing workflow SSE remains compatible and adds only `runtime_approval_pending` and `runtime_approval_resolved`. The legacy `/resume` endpoint remains valid for `human_intervention`.

## Public API

- GET /api/xperts?status=&search=&limit=
- POST /api/xperts
- GET /api/xperts/{xpert_id}
- PATCH /api/xperts/{xpert_id}
- POST /api/xperts/{xpert_id}/validate
- POST /api/xperts/{xpert_id}/publish
- GET /api/xperts/{xpert_id}/versions
- GET /api/xperts/{xpert_id}/versions/{version}
- POST /api/xperts/{xpert_id}/run

List responses expose summaries only. Detail and version endpoints expose the workflow only when it is required for editing or version inspection.

## Execution and Observability

Published runs use run_type=xpert and the same SSE event family as classic workflow execution:

- workflow_meta
- node_start
- node_delta
- node_end
- workflow_end
- error

For Xpert runs, workflow_meta also exposes xpert_id and xpert_version. RunRegistry metadata links the Xpert ID, slug, version, draft revision, and checksum. Tool, knowledge, AgentTask, and Handoff node runs remain children of the Xpert run.

Checkpoints store titles, event types, status, lengths, IDs, and error summaries. They must not store complete prompts, complete model or tool outputs, API keys, local absolute paths, embeddings, or raw secrets.

## Conversation Files and Memory

`XpertContextStore` uses the existing runtime storage mount and atomic replacement. It owns conversation messages, file metadata, extracted local artifacts, active memories, and model-proposed memory candidates. `XPERT_CONTEXT_STORAGE_DIR` can override its location and otherwise falls back to `AGENT_TASK_STORAGE_DIR`.

Run requests may include a conversation ID and up to five file asset IDs. A `workflow_agent` only receives extracted file context when `enableFileUnderstanding=true`. Each file contributes at most 10,000 characters and the combined injected context is limited to 30,000 characters. Files remain conversation resources and are not automatically added to RAG.

Memory reads are explicit node configuration. `memoryReadScope` is `conversation`, `xpert`, or `both`; automatic recall is bounded to ten records and 8,000 characters. Model writes create pending candidates. Only user approval activates a candidate, while a direct user "remember" action creates an active record immediately.

The `memory_tools` capability exposes `memory_search`, `memory_get`, and `memory_propose_write`. In ReAct-Lite tool mode these tools share the existing middleware, policy, and audit path with MCP tools. Normal streaming mode remains available and only receives bounded automatic recall.

Goal file sharing is opt-in. Explicit file references are carried through AgentTask and Handoff metadata and can be consumed by target Xperts. Conversation-scoped memory is not copied or exposed to another Xpert; a handoff target may only recall its own Xpert-scoped memory.

## Xpert Handoff Execution

Automatic execution is explicit. Only a Handoff with `execution_mode=xpert_auto` and `target_agent=xpert:<slug-or-id>` is eligible. Other targets remain in the manual MetaAgent Inbox.

The executor resolves the target by ID or slug, pins its current published version on the first claim, and invokes the same classic workflow runner used by the public Xpert chat endpoint. It does not call the server through loopback HTTP. The target receives `user_input`, `handoff_reason`, `source_agent`, and `source_task_id`.

The queue uses the following states:

- `pending -> accepted -> completed`
- `accepted -> retry_wait -> accepted`
- `accepted/retry_wait -> dead_letter`
- `dead_letter -> pending` through the requeue API

Claims use a lease token. The default lease is 60 seconds, the maximum attempt count is three, and transient failures use short exponential backoff. Missing, unpublished, or invalid target Xperts are permanent errors and move directly to dead letter. A delegation depth limit of five prevents unbounded Xpert cycles.

`agent_handoff` and `handoff_router` always write the Handoff ID to `outputVariable`. With `waitForCompletion=true`, they also wait up to `waitTimeoutSeconds` and write the target result to `resultVariable`. With waiting disabled, the source workflow continues while the worker executes the target in the background.

Production enables file persistence through `AGENT_TASK_STORAGE_DIR`. The Store uses atomic temporary-file replacement and an in-process lock. This is durable across a single container restart, but it is not a multi-process or distributed queue.

Public executor interfaces:

- `GET /api/runtime/handoff-executor/status`
- `POST /api/runtime/agent-handoffs/{handoff_id}/execute`
- `POST /api/runtime/agent-handoffs/{handoff_id}/requeue`

## Conversation Goal Coordination

Conversation Goals add a durable orchestration layer above AgentTask and Handoff. A published Planner Xpert produces a JSON dependency plan, the user reviews it, and GoalCoordinator dispatches ready steps through explicit `xpert_auto` handoffs. The default per-Goal concurrency is two.

Planner and target Xpert versions are pinned before execution. A step receives the Goal objective, its instruction, and completed dependency results. The combined input is capped at 20,000 characters and marked when truncated.

Pause stops new dispatch while allowing in-flight work to settle. Cancel prevents future dispatch but does not force-terminate an active model request. Exhausted Handoff retries move the Goal to `needs_attention`; users may retry, reassign, or explicitly skip a non-final step.

Goal state is atomically persisted in `goals.json` under `AGENT_TASK_STORAGE_DIR`. RunRegistry adds `run_type=goal` and links planner, task, handoff, target Xpert, and node runs. Since RunRegistry remains in memory, recovery creates a new Goal run with `recovery_of_run_id` metadata.

See `docs/XPERT_GOALS.md` for the model, state machine, API, planner contract, and safety limits.

## Versioned Knowledge Pipeline Execution

`RagService` persists pipeline drafts, jobs, immutable candidate versions, and the active-version pointer in RAG metadata. `KnowledgePipelineExecutor` is a single-process background worker that claims queued jobs and runs six ordered stages: load, vision, process, chunk, embed, and store. The vision stage is skipped safely when it is not configured.

A job pins its draft version and snapshots every selected source before execution. Knowledge-base documents are copied into a private job source area. Explicitly selected Xpert conversation files are resolved through `XpertContextStore`, deduplicated, and snapshotted; archived source assets remain usable by an already-created job, while cross-Xpert references are rejected.

Successful execution creates a `ready` candidate index in an isolated vector namespace. It never changes retrieval automatically. The candidate must be queried through the preview API and explicitly activated. Activation atomically updates the active-version pointer, and activating an older ready version is the rollback mechanism. Normal RAG, Chat RAG, `knowledge_retrieval`, and `knowledge_citation` resolve the active namespace centrally; a knowledge base without an active version retains legacy-index behavior.

Pipeline jobs and versions survive process restarts. Running jobs are returned to the queue. RunRegistry remains in memory, so a persisted job whose old run is absent creates a `knowledge_pipeline` recovery run with `recovery_of_run_id` metadata before recording new checkpoints. Checkpoints contain job, stage, version, count, and error summaries only.

The executor is intentionally limited to one local worker and one concurrent job. Cancellation is cooperative between stages. Failed or cancelled attempts delete their candidate namespace and cannot change the active version. See `docs/XPERT_KNOWLEDGE.md` for APIs, states, and operational checks.

## Advanced RAG Retrieval V2

An index schema v2 candidate pins its chunking, embedding, and retrieval profiles. It supports recursive-character chunks or parent-child chunks with ordered separators. Parent-child candidates index the child chunks; retrieval returns bounded parent context while retaining the matched child and offsets as the citation anchor.

Each v2 candidate owns two coordinated indexes: the existing vector namespace and a SQLite FTS5 namespace with normalized Latin tokens plus CJK unigram/bigram tokens. A candidate is marked ready only when both indexes contain the expected chunks. Any load, parse, chunk, embedding, vector, or lexical failure removes both candidate indexes and leaves the active-version pointer unchanged.

Retrieval modes are `vector`, `fulltext`, and `hybrid`. Hybrid retrieval over-fetches bounded candidate sets, deduplicates by chunk ID, and applies weighted normalized reciprocal-rank fusion. Optional reranking prefers a dedicated rerank provider and can fall back to an OpenAI-compatible LLM strict-JSON ranking response. Provider timeout or invalid output is fail-open: the fused order is returned with a warning.

Normal RAG, Chat RAG, workflow knowledge nodes, published Xperts, Goals, and Xpert Apps resolve the active version through `RagService`; clients cannot silently select a candidate. Legacy indexes remain vector-only and are not migrated automatically. Retrieval checkpoints and diagnostics may contain mode, counts, scores, model labels, and warnings, but never the full question, chunk body, embedding, local path, or credential.

## Knowledge Evaluation And Promotion

`KnowledgeEvaluationStore` persists revisioned evaluation sets, immutable run snapshots, gate policies, aggregate metrics, and safe per-case rankings in the RAG storage directory. Expected citations use stable source document IDs plus optional chunk, source block, and page references, not candidate namespace IDs.

`KnowledgeEvaluationExecutor` is a restart-safe single-process worker. It queries immutable candidate versions with answer generation disabled, then calculates Recall@1/5, MRR@10, nDCG@10, citation hit/coverage, no-result rate, error rate, and P95 latency. RunRegistry uses `run_type=knowledge_evaluation`; checkpoints contain only IDs, counts, status, duration, and safe error summaries.

Promotion Gate supports advisory and required modes. Required promotion verifies that the evaluation run succeeded, evaluated the same knowledge base and candidate version, used the current evaluation-set revision, and passed every configured absolute and regression threshold. Advisory mode retains the previous direct activation path for compatibility. Promotion switches the existing active-version pointer only; it does not rebuild indexes or mutate the immutable candidate.

## Structured Processor And Generated Indexes

Each pipeline job pins a `processor_profile` before execution. `StructuredDocumentProcessor` converts TXT, Markdown, PDF, and extracted Xpert files into stable blocks. Markdown preserves heading paths, tables, lists, and fenced code; PDF blocks retain page numbers and can remove repeated page headers and footers. Normalization removes control characters and collapses redundant blank lines without flattening table or code content.

Processor modes are `general`, `qa`, and `summary`. General blocks continue into the configured recursive or parent-child splitter. QA batches structural blocks through the existing OpenAI-compatible gateway, indexes the generated question, and stores the grounded answer plus source block as lifted context. Summary indexes document/section summaries and returns the corresponding original blocks. Generated JSON is validated strictly and retried at most twice per batch.

The job owns a private processed artifact per source. Public payloads expose only status, attempts, counts, duration, warning, and safe error summaries. A retry reuses an artifact only when both source content hash and processor config hash still match. Failed sources are rerun, then both candidate indexes are rebuilt from the complete successful artifact set. `continue_on_error` can produce a warned candidate when at least one source succeeded; `strict` blocks candidate readiness after any source failure.

`GET /api/rag/processor-capabilities` returns safe parser/model readiness labels. Processor preview accepts one document and an optional config override, returns at most 20 truncated blocks or generated items, and never persists output. Active-version resolution for Chat, workflow, Xpert, Goal, and App remains unchanged.

## Knowledge Agent Read And Approval Write

`workflow_agent` can opt into a dedicated `knowledge_tools` capability while using the existing Runtime tool loop. `knowledgeReadEnabled`, `knowledgeWriteEnabled`, and one to five `knowledgeBaseIds` are fixed in the published workflow. The model cannot select an undeclared knowledge base. `knowledge_search`, `knowledge_get`, `knowledge_cite`, and `knowledge_propose_write` all pass through `run_tool_with_runtime`, middleware, tool policy, audit, and checkpoint handling.

Read tools resolve only the active knowledge namespace. Search can merge several declared knowledge bases with stable score ordering and bounded excerpts; exact lookup requires the active namespace plus chunk ID. A single-library failure becomes a warning, while total failure becomes a runtime tool error. Tool output, audit, and checkpoints retain IDs, score diagnostics, lengths, status, and safe errors only.

`knowledge_propose_write` creates a durable pending proposal. It never edits a document or index. The per-knowledge-base Inbox is the sole approval surface and uses revision-based optimistic concurrency. Approval snapshots the exact active version sources when one exists, adds a managed Markdown source, and queues the existing Pipeline executor. The resulting candidate carries `promotion_required=true`; direct activation is rejected and only a passed Evaluation Gate followed by `/promote` can change the active pointer. Rejecting a proposal creates no document, job, or version.

Goal and Handoff execution reuse the same published workflow settings and attach safe source IDs to proposals. Public Xpert Apps may opt into read-only knowledge access with `allow_knowledge_read`; dynamic knowledge write is always rejected at deployment and runtime.

## Current Limits

- File persistence is local and is not a workspace database.
- Public Xpert Apps now provide fixed-version unlisted sharing and an OpenAI-compatible API. They remain a trusted-local management feature without organization permissions or a collaborative editor. See `docs/XPERT_APP_API.md`.

## Xpert App Deployment

`XpertAppStore` shares the Xpert storage directory and persists App metadata, immutable deployment history, credential hashes, prefixes, status, limits, and daily usage through atomic replacement. Raw share tokens and API keys are returned once and never persisted.

App execution uses `run_type=xpert_app` and the same classic runner. The deployment fixes one immutable XpertVersion. Tool, Handoff, and Xpert-memory capabilities are disabled by default; tool execution also requires an active `tool_policy`, otherwise the runtime denies the call. Public JSON/SSE responses expose only the final output.
- Automatic Handoff execution is limited to a single backend process and explicit `xpert:` targets.
- Knowledge ingestion, evaluation, and approval-triggered candidate builds are local and single-process; they have no distributed lease or automatic activation. Image understanding, evaluation, and Knowledge Agent approval writes are available, while multimodal embeddings, layout coordinates, and GraphRAG remain out of scope.
- A normal /workflow run remains unchanged and continues to use its existing local-draft behavior.

## Isolated Sandbox Runtime

Private Workflow, Xpert Chat, Goal, and Handoff runs may compile `sandbox_files`, `sandbox_shell`, and `skills_runtime` into the target workflow Agent. A dedicated Docker sidecar owns the workspaces and exposes only a Unix Domain Socket. It has no network, no host port, a read-only root, dropped capabilities, resource limits, and no mount of the repository, `.env`, credentials, or Runtime stores.

Workspaces are scoped to conversation, goal/step, handoff, or workflow task/node. Inputs, editable files, staged Skills, artifacts, and idempotency records use separate directories. File paths are relative and symlink-safe. Shell calls accept argv arrays only, use an explicit executable allowlist, terminate process groups on timeout, truncate output, and replay completed operation results instead of repeating side effects.

Sandbox and Skill tools still pass through the Agent pipeline, permission policy, durable HITL, audit, and safe checkpoint handling. `sandbox_shell.require_approval` is enforced during validation and again when the Agent runtime is compiled. Published Xpert Apps reject these middleware types. See `docs/XPERT_SANDBOX.md`.

Private Agents may optionally enable local verified-catalog discovery and approval-gated fixed-SHA Skill installation. Activation remains scoped to the current run, while the installed package is global. See `docs/SKILL_RUNTIME_ROUTER.md`.

## Agent-Bound Middleware Core

Classic workflow supports a non-control binding edge from `runtime_middleware` to `workflow_agent` through `sourceHandle="middleware-binding"` and `targetHandle="middleware"`. Binding nodes are excluded from topological scheduling, variable reachability, and independent execution. A middleware node can bind to one Agent only and cannot simultaneously participate in control flow. Bound middleware is ordered by priority and node ID; legacy linear middleware remains compatible.

Each workflow Agent compiles an isolated `MiddlewarePipeline`. Agent hooks wrap the complete Agent run, model hooks run for direct streaming and every ReAct decision, and Runtime tools reuse the same pipeline, tool policy, event recorder, and audit store. Context compression can persist a derived Xpert conversation summary without modifying original messages. Structured output buffers the final answer, validates Draft 2020-12 JSON Schema, and optionally repairs once before entering existing exception handling.

The Todo capability exposes scope-bound `todo_list`, `todo_create`, and `todo_update`. Conversation, Goal, Handoff, and Workflow scopes are atomically persisted; Xpert App scopes remain run-local. The LLM tool selector operates only after allowlist and policy filtering and cannot restore denied tools. Checkpoints retain names, counts, status, timing, and safe errors only. See `docs/XPERT_MIDDLEWARE.md`.

## Automation Runtime

`AutomationStore` persists definitions and executions atomically in the Runtime storage directory. A definition pins one immutable published Xpert version and supports once, fixed interval, or five-field Cron triggers with an IANA timezone. Occurrence IDs, leases, overlap/misfire policies, budgets, bounded retries, and dead-letter states provide the single-process reliability boundary.

`AutomationCoordinator` invokes the fixed Xpert through the same classic workflow runner and creates an `automation` RunRegistry parent. Approval and Client Tool continuations update and resume the same execution. Scheduler Runtime tools are scoped to the current private published Xpert and cannot manage another Xpert's definitions.

新 Automation 发生在首次领取时固定 `runtime_budget_seconds`、`started_at` 和
`runtime_deadline_at`。等待、续租、派发前重试及重启均消耗同一个墙钟期限；
修改定义不会扩展在途发生的限额。普通人工重试不得重置已耗尽期限，重新执行
必须创建新的显式发生。旧已开始且缺少期限字段的快照仍可读，但没有可证明的
固定期限，不从当前配置猜测历史授权；新字段损坏必须拒绝加载。

期限到达时父状态及 `runtime_expired_at` 先落盘，随后取消匹配发生身份的本地
子执行并投影 RunRegistry；投影失败可在后续轮询补齐。等待恢复入口和结果提交
也检查期限，迟到完成不能覆盖失败。正在执行的取消属于协作式取消，不承诺
硬实时抢占或远端请求撤销。已派发但结果未知仍为 `AUTOMATION_DISPATCH_UNCERTAIN`，
包括租约先失效的发生；停止后续子执行不证明原请求未执行，不能自动重派。
这些期限检查不取代下文 Handoff 的持久根调用次数绑定；Goal 全局预算仍不在
本轮承诺范围内。

Runner 在流执行作用域绑定服务端派发检查器：节点计步、模型和 Runtime 工具
准入均重查当前父状态，取得并发槽位/预算锁后再次检查；没有计数预算时也不能
绕过。Managed Workflow 三处 HTTP 派发、Legacy unary/stream（含已有 fallback）
在实际发送前复核，避免客户端初始化期间过期仍外发。检查器不由模型配置、
请求或持久快照提供；内层上下文不能移除上层检查，退出及取消后恢复原上下文。
提前拒绝不计数；已准入但发送前被拒绝的计数保守保留，不等同实际账单。
派发检查器只覆盖这些已接入入口，不代替 Handoff 的持久根预算绑定，也不
宣称 Goal 具有全局预算、所有外部 IO 均已检查或已发出的远端请求能被撤销。

后台根预算内核提供服务端专用的委托登记与消费接口。父执行必须在仍持有效
所有权时登记发生；每个登记只绑定一个子执行，绑定前子预算不得已消耗或已
派生后代。私有 `execution_budget_parent/children` 双向关系保存在同一执行
快照，不能用 `runtime_metadata` 或用户输入构造；损坏、成环和重复消费拒绝。
链最多 16 层、每父最多 1000 个登记。祖先可等待，子执行必须运行且持当前
lease；终态或运行中租约失效的祖先阻止继续派发。

每次准入在一次快照提交中扣减子执行与所有祖先，共有祖先只扣一次。恢复以
持久计数为准；旧父对象不覆盖后台子执行新增计数。相同 Store 的预算对象共享
整链并发槽位，同一预算身份不允许跨事件循环复制额度；不同根任务互不阻断。
这不是多进程分布式锁。
排队后的身份变化、父取消与保存结果不确定均停止派发；已准入计数不退款。
并发槽位不持久化，也不能据此把重启前不确定的外部请求重新派发。

已有预算的 Workflow/Xpert 现在在 Handoff 就绪前登记委托，固定发布版本和
输入、附件授权等语义摘要。后台执行在首个节点前消费唯一登记并绑定子预算，
不依赖 ContextVar 继承或可变 `workflow_task_id` 推断父任务。领取返回独立
快照，每次派发核对固定 lease 与当前 Store；父取消、契约漂移或过期阻止外发。
已完成的子结果可复用；已登记子执行但结果不确定时必须恢复原执行，不能新建
任务重发。等待后由原执行 lease 及持久父关联恢复，不延用过期 Handoff lease。

离线验证使用真实 Runner/Executor/Coordinator 与合成模型输出，不是付费模型
证据。Goal 全局预算尚无独立产品契约，不能把各步骤预算称为 Goal 总额；所有
等待组合和完整恢复验收仍未关闭。旧无关联快照按旧预算读取，
不猜测补写父身份。回退保留关联字段及读取，不能抹除后按新根任务恢复执行。

内联 External Xpert 在活动父预算内，从服务端预算作用域取得真实执行及 lease，
调用 metadata 只用于一致性核验，不能授予父身份。节点、迭代与批次位置构成
稳定调用位置，目标发布版本、输入及授权来源摘要固定该发生；子 Runner 在首个
操作前消费登记，持久关联原父与根。Store 重开后，完成项复用已提交结果，
未成功结算项阻止自动重发，也不能通过更换模型调用位置绕过；原位置输入或
版本漂移拒绝执行。不新增重试、嵌套等待或附件传播能力。

External Xpert 工具委托仍计一次工具准入，但不在等待子 Runner 时占用其根
并发槽；实际子模型和工具继续取得整链槽位并原子计数，避免单槽自锁。
普通工具不变，调用参数不能伪装成委托以绕过并发。预算、所有权和未结算错误
沿 fatal 路径传播；等待/取消不是成功结果，返回子结果前仍复核父权限。
生产 External Xpert 继续走原策略兼容路径，不因 V2 开关开启而扩大适用范围。
无持久父预算的旧调用保留旧准入；不能据此声称历史快照有跨重启根限额证据。
回退须先暂停内联派发并保留关联及结果，不得删除关联后作为新根重跑。

HandoffExecutor 结算现在在 AgentTaskStore 同一锁内检查领取 token、有效 lease
和父任务非终态，再将 Task/Handoff 一次持久化。迟到成功、异常、重试与等待
不能覆盖接管者；领取失败也不授权死信结算。取消先提交则拒绝后到的结算，
完成/失败先提交则终态不回退，等待/重试之后仍可取消。
RunRegistry 只在持有源 Store 锁时读取当前权威状态并投影，锁顺序为
AgentTaskStore → RunRegistry，不在此区间调用模型、重入 Store 或外部 IO。
checkpoint 观测回调在源锁外执行；返回后重新核对权威状态，若期间取消或接管
则重新投影，不拿旧回执覆盖新状态。持续漂移最多重查八次，随后明确失败。
重复投影不重复写同一结算 checkpoint。它不是跨 Store 的磁盘事务；投影失败
后可以依据已持久化状态重新同步，不得重新执行目标。结算持久化异常后该 Store
实例停止写入和投影，须重新加载核对磁盘，不猜测保存前后结果。
上述结算隔离有离线故障注入证据；整个 R8-A 的全量、上游集成与独立预览门禁
仍需分别关闭，不以局部回归代替真实验收。

Workflow Handoff 的超时决定现在在 AgentTaskStore 一次持久化交接死信与任务取消，
同时撤销领取权限。已完成、已失败或人工取消的决定优先，不由迟到的超时覆盖。
`HANDOFF_TIMEOUT` 终态也是跨 Store 取消协调的持久依据：父流程恢复前须完成
子执行取消及可用 RunRegistry 的投影，传播失败则保留待协调状态，下次扫描或
重启仅重试本地取消，不重发目标请求。旧快照中仅交接超时、任务未取消的分裂
状态也可据此补齐；终态相互矛盾则阻断。持久化结果不确定时须重新加载确认。
该机制不保证已发出的远程请求立即停止，也不撤销已提交副作用或退还预算；
单个条目传播失败不阻塞其他可恢复条目。回退前停止协调器，保留原超时回执。

Ralph Loop performs bounded continuation and strict verification before the Agent output is committed. Knowledge Writer creates pending proposals only and retains the existing Inbox, pipeline, evaluation, and promotion gates. Plugin Hooks stage an installed Skill's explicit manifest into the offline Sandbox and execute argv only. Public Xpert Apps reject all private automation middleware. See `docs/XPERT_AUTOMATION.md`.

## Xpert Evaluation Runtime

Vision V2 通过编译器单附件输入与固定 Managed Binding 执行，每页纳入统一并发和调用
预算。普通 Xpert 必须明确共享唯一附件；评测保持 `xpert_evaluation` 身份，按执行项解析
固定 FileAsset，不借用会话或其他活文件。已派发未完成项重启后不自动重发，安全回执保留
已知 usage 与不可验证标记。V1/RAG 视觉行为不迁移，详见 `docs/META_PLANNER_VISION.md`。

`XpertEvaluationExecutor` reuses the classic workflow runner in an internal capture mode and
creates a `xpert_evaluation` RunRegistry parent. Every run fixes its DatasetVersion,
XpertVersion or Authoring Proposal revision, workflow checksum, resource versions, model
policy, seed and budget before any sample executes.

评测默认只读且失败关闭；Controlled Writes V2 只在服务端私有初始化表中有条件执行。
It blocks waiting, Handoff, Automation,
interactive approval, persistent writes, Browser, Client Tools, Sandbox writes and unsafe
Toolset/Plugin capabilities. Knowledge queries are pinned to the active index observed when
the run is created. External Xperts recurse through the same preflight.

The executor persists work items and resumes only unfinished items after restart. Budget
counters cover model calls, tool calls and actual or explicitly estimated tokens. Reports and
checkpoints store only truncated outputs, safe citations, counts, timing and error summaries.
Evaluator never approves a Proposal, writes an Xpert draft or publishes a version. See
`docs/EVOAGENTX_EVALUATOR.md`.

### 受控 Agent Table V2

正常私有 Workflow/Xpert 的三种 V2 写节点固定 Schema、逐表操作授权、业务字段与影响上限。
更新/删除只消费实际 Query/Insert 收据及 revision；Agent 业务值经 JSON Deserialize V2 和
事务字段校验。每节点事务独立，后续错误不会撤销先前成功写入；不新增重试、等待或补偿。
私有执行日志在首次派发前冻结请求，恢复优先核对幂等账本，不能用新输入重做旧 operation。

`xpert_evaluation` 保持真实运行身份，使用绑定 run/item/target/case/workflow 的私有 Backend。
所有查询和写入共享本项隔离状态；旧版写节点不能借此回退业务 Store。公开 checkpoint 只保存
表、Schema、操作、计数、checksum 和耗时，记录正文与初始化留在私有存储。
公共 App、嵌套写入及 Optimizer 入口不获得此权限。详见 `docs/META_PLANNER_CONTROLLED_WRITES.md`。

## Prompt Evolution Runtime

`XpertEvolutionExecutor` is a persistent, bounded optimizer layered on the internal
Evaluator snapshot entry. A run fixes one Xpert or Prompt Profile draft revision, one
DatasetVersion, a deterministic train/validation split, model policy and execution budget.
It never changes the workflow graph, model selection, resource bindings or middleware.

Each generation creates deduplicated Prompt candidates, evaluates them with the same
read-only safety preflight and budget, and records only hashes, scores, lengths and safe
errors in checkpoints. Candidate generation receives training-case failure summaries only.
Finalists are compared with the original Prompt on the validation split; the validation
cases are never included in optimizer context.

The non-degradation gate requires a minimum total-score improvement, limits every weighted
metric regression and rejects new failures, timeouts, budget exhaustion or safety errors.
Passing creates one pending Authoring Proposal. Failing produces a `no_improvement` report.
A changed target revision marks the run stale and prevents Proposal creation. Proposal
approval updates a draft only and cannot publish either an Xpert or Prompt Profile. See
`docs/EVOAGENTX_EVOLUTION.md`.

## Structure Evolution Runtime

`evolution_kind=structure` uses the same persistent executor and Evaluator, but candidates
are compiled from a fixed typed mutation language. The optimizer cannot submit a complete
workflow or executable code. `StructureMutationCompiler` creates stable IDs, positions,
control edges, and resource binding handles, then executes workflow, resource, publish, and
Evaluator safety gates before any candidate receives an Evaluation Run.

The run fixes the Xpert draft revision, Capability Snapshot, explicit resource scope,
DatasetVersion, default model for newly added Agents, mutation limits, model policy, and
budget. Existing Agent prompts, models, and output contracts remain immutable inside the
search. Static failures are persisted as safe issue summaries and consume no evaluation
budget.

Final Holdout promotion compares quality, weighted metric regressions, failures, model
calls, estimated tokens, P95 latency, and graph complexity. Passing creates one pending
`xpert_update` Proposal containing a mutation manifest and safe structural diff. It never
changes the draft or publishes a version without later human approval. See
`docs/EVOAGENTX_EVOLUTION.md`.

## Data X Runtime

`DataXStore` atomically persists project, source snapshot, import job, semantic model, indicator, immutable version, proposal, and result-artifact metadata. Each project owns a separate DuckDB file. Imported source bytes are fixed by SHA-256 and never exposed through API paths.

Queries do not accept SQL. `DataXService` compiles a bounded DSL of published indicators, dimensions, parameterized filters, time ranges, ordering, and limits. Draft edits do not affect the immutable published snapshot used by runtime queries. Derived expressions are evaluated from published metric results with a restricted arithmetic AST.

The `datax_indicators` Agent middleware compiles explicit project/model scopes into `datax_tools`. Workflow, Xpert Chat, Goal, Handoff, and Automation share this provider. Agent writes are proposal-only; approval creates a draft and explicit publication remains a separate operation. Public Apps require `allow_datax_read`, an active tool policy, and valid project/model scope, and never receive proposal tools. See `docs/XPERT_DATAX.md`.

## Workflow NodeContract V3

Workflow node static facts are centralized in `NodeContractRegistry`. The
workflow Registry API, Meta Planner Capability Snapshot, Xpert publish
preflight, Evaluator, public App deployment, and Structure Evolution consume
the same safe projection or `NodePolicyService` decision. Resource pinning,
Toolset safety, middleware validation, and cycle checks remain in their domain
services.

A complete contract does not enable a node in Planner or a public entrypoint.
Compatibility contracts preserve classic runner behavior and default Planner
to disabled. See `docs/NODE_CONTRACT_V3.md` for the checksum and migration
contract.

Meta Planner 候选使用 Graph IR V3：模型只输出 Graph Intent，服务端依据本契约和资源
Store 解析执行效果、端口、Handle 与固定版本。语义 data 边不会进入 classic runner
调度；新 Proposal 保存安全 IR、Graph checksum 和编译产物 checksum，人工编辑后将
IR 标记为 stale。Typed IR V2 仅作为无损双读兼容输入。

Capability Snapshot V6 additionally exposes five deterministic pure-node
adapters: JSON serialize/deserialize V2, variable aggregation V2, data
aggregation, and dataset comparison. Their native variable bindings and output
schemas are compiler-derived. They cannot satisfy plan tasks, bind resources or
middleware, or become the final output. Unversioned JSON nodes retain their
legacy behavior; Planner-generated V2 nodes fail closed on invalid JSON, schema
mismatch, or the 5 MiB boundary.

## Versioned MCP And API Toolset Runtime

`ToolsetStore` persists editable MCP, OpenAPI, OData, and builtin Provider Toolset definitions plus immutable published versions. A version fixes the transport, API, or Provider profile, credential references, enabled tools, aliases, default arguments, JSON Schema hashes, tool semantics, prefix, and release metadata. Xpert publication resolves `latest` to a concrete Toolset version; later discovery, import, or draft edits cannot expand an already published Xpert.

Stdio profiles accept argv only and run inside the MCP sandbox boundary. Streamable HTTP is the preferred remote transport and legacy SSE remains compatibility-only. Remote transports reject URL credentials and, under the default policy, resolve and block loopback, private, link-local, reserved, multicast, Docker-local, and metadata targets. Reconnect attempts and operation timeouts are bounded.

Secrets are referenced by credential ID. The encrypted credential file never stores plaintext; create and rotate responses reveal a value only once, while normal APIs return mask, prefix, kind, and status. Losing or replacing the local master key marks existing credentials unavailable and never falls back to plaintext.

OpenAPI 3.0/3.1 documents are compiled into bounded operation schemas; OData v4 CSDL is compiled into controlled EntitySet reads, key lookup, creates, and supported primitive operations. The executor does not accept caller-supplied URLs or arbitrary HTTP templates. It validates the fixed base URL for every request and redirect, blocks private/reserved targets by default, rejects cross-origin redirects that could leak credentials, and limits timeout, redirects, and response bytes. API Key, Bearer, Basic, and OAuth2 client-credentials authentication resolve encrypted references only at call time.

`toolset_resource` is a non-control binding into `workflow_agent`. The runtime exposes only tools enabled in the fixed version. Calls merge versioned default arguments, validate the resulting object against the fixed JSON Schema, then enter the existing permission policy, durable HITL, audit, middleware, and checkpoint path. Mutating API operations require explicit management-test confirmation and bound HITL coverage in published Xperts; the runtime checks this again before dispatch. A missing tool, a newly required parameter, or changed method/path is a hard Schema-drift condition.

## Tool Semantics And Builtin Providers

Every fixed Toolset tool now carries conservative runtime semantics: `read_only`, `requires_approval`, `sensitive`, `terminal`, `memory_mode`, `parallel_safe`, and `public_app_allowed`. Sensitive tools require matching HITL coverage even when the normal permission policy allows them. A successful terminal tool returns its bounded output as the Agent answer without another model call.

The builtin registry exposes Tavily and Todo Provider instances. Tavily resolves a credential reference only at dispatch and uses fixed endpoints with bounded timeouts and response sizes. Todo delegates to the existing scope-aware `RuntimeTodoStore`; management tests use an isolated workflow test scope, while published runs retain conversation, Goal, Handoff, Workflow, or App-run scope.

The ReAct-Lite contract accepts one `tool` or an ordered `tools` batch. Parallel execution is limited to read-only, parallel-safe, non-sensitive, non-terminal tools. `maxToolConcurrency`, `maxToolCalls`, `maxToolDepth`, and `maxIterations` bound a run; failures do not cancel already completed siblings and results return in decision order. Each call still receives its own policy, audit, and checkpoint record.

Run memory remains inside one Agent execution. Conversation memory is available only to private Xpert conversations, persists a redacted normalized summary of at most 8 KB, and can be listed or archived through the conversation API. Public App calls always downgrade memory to run scope.

Public Apps may deploy a fixed Toolset only when `allow_tools` is enabled, a Tool Policy is bound, and every enabled tool is read-only, non-sensitive, explicitly public, and not conversation-memory scoped. Provider credentials remain server-side and never enter the manifest or public response.

## Prompt Command And Declarative Plugin Runtime

`PromptProfileStore` persists editable Prompt drafts and immutable published versions. A Profile fixes one to five aliases, the `{{args}}` template, argument hint, tags, and App policy. Xpert publication resolves every enabled `latest` binding to a concrete Prompt version. Later draft edits and releases do not change an existing XpertVersion.

The Xpert run boundary parses slash commands before invoking the classic workflow runner. `/alias raw arguments` renders only the fixed Profile template and then executes the current published Xpert; it never starts another workflow or replaces the Xpert role prompt. The conversation retains the original user command while the model receives the rendered task. Unknown commands fail before model execution, and a leading `//` escapes one slash.

`PluginStore` imports trusted local ZIP packages containing `modelmirror-plugin.json`. A published Plugin version fixes its package checksum, embedded Prompt snapshots, namespace-installed Skill IDs, Toolset version/schema-hash references, and registered middleware presets. Packages cannot load Python/Node modules or initialization scripts. Skill scripts remain explicit Sandbox operations.

`plugin_resource` is a non-control binding to one `workflow_agent`. At runtime its fixed version is compiled into the Agent Toolset, Skill resolver, middleware pipeline, and private Prompt alias map. Toolset, Skill, middleware, and Prompt execution retain their existing Policy, HITL, Audit, Sandbox, and checkpoint boundaries. Name or middleware conflicts fail validation instead of silently overriding a resource.

Public Apps reject `plugin_resource`. They may expose only directly bound fixed Prompt Profiles marked `public_app_allowed`; manifests list safe command metadata and never include template content. See `docs/XPERT_PLUGIN_PROMPT.md`.
