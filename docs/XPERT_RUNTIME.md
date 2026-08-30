# Xpert Runtime Contract

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

Ralph Loop performs bounded continuation and strict verification before the Agent output is committed. Knowledge Writer creates pending proposals only and retains the existing Inbox, pipeline, evaluation, and promotion gates. Plugin Hooks stage an installed Skill's explicit manifest into the offline Sandbox and execute argv only. Public Xpert Apps reject all private automation middleware. See `docs/XPERT_AUTOMATION.md`.

## Xpert Evaluation Runtime

`XpertEvaluationExecutor` reuses the classic workflow runner in an internal capture mode and
creates a `xpert_evaluation` RunRegistry parent. Every run fixes its DatasetVersion,
XpertVersion or Authoring Proposal revision, workflow checksum, resource versions, model
policy, seed and budget before any sample executes.

Evaluation mode is read-only and fail-closed. It blocks waiting, Handoff, Automation,
interactive approval, persistent writes, Browser, Client Tools, Sandbox writes and unsafe
Toolset/Plugin capabilities. Knowledge queries are pinned to the active index observed when
the run is created. External Xperts recurse through the same preflight.

The executor persists work items and resumes only unfinished items after restart. Budget
counters cover model calls, tool calls and actual or explicitly estimated tokens. Reports and
checkpoints store only truncated outputs, safe citations, counts, timing and error summaries.
Evaluator never approves a Proposal, writes an Xpert draft or publishes a version. See
`docs/EVOAGENTX_EVALUATOR.md`.

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
