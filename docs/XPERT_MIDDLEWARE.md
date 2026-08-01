# Xpert Agent Middleware

## Office Automation（2026-07-19）

`office_automation` 已进入私有 Agent 真实执行。它复用 Client Tool V1 的配对、持久等待、HITL、Audit 和断点恢复，将 22 个类型化操作交给用户主动绑定的 Word、Excel 或 PowerPoint 当前文档。所有修改工具必须由同一 Agent 的 HITL 覆盖；删除操作还需要 `allowDeletes=true` 与 `confirm=true`。

读取动作可安全重派，执行中的修改断线后进入 `uncertain`，不自动重放。公开 Xpert App/API 拒绝 Office 中间件。HTTPS Task Pane、manifest、工具清单和安全边界见 `docs/XPERT_OFFICE_AUTOMATION.md`。

## 类型化文件记忆（2026-07-19）

`xpert_file_memory` 是真实 Agent 中间件，绑定到 `workflow_agent` 后为已发布 Xpert、Goal 和 Handoff 提供分层长期记忆召回。它先注入受限 `MEMORY.md` 索引，再注入相关摘要与少量正文；选择模型失效时使用确定性排序，普通 Workflow 缺少 Xpert 上下文时安全跳过。

写回只创建 create/update 候选，正式写入必须经过人工审批和 revision 冲突检查。旧扁平字段会编译为隐式配置，显式绑定优先。公开 App 只有 `allow_xpert_memory=true` 时可只读，候选生成与写回始终禁止。详见 `docs/XPERT_FILE_MEMORY.md`。

## Xpert / Skill 自编写（2026-07-18）

`xpert_authoring` 与 `skill_creator` 是 proposal-only Agent 中间件。它们要求绑定 `workflow_agent` 并启用 Runtime 工具模式，允许 Agent 在显式资源范围内读取草稿、创建或更新提案并运行校验；不暴露 publish、install、delete 或直接覆盖动作。

Registry 契约现包含 `config_version`、`execution_status`、`requires_tool_mode`、`app_policy` 与 `security_category`。Workflow 校验、Xpert 发布和 App 部署预检消费相同契约；两类自编写能力固定为 `app_policy=forbidden`。提案批准只写 Xpert/Workspace Skill 草稿，revision 冲突不得 fail-open。完整提案、Skill 包和 API 边界见 `docs/XPERT_AUTHORING.md`。

## Automation（2026-07-18）

`scheduler`、`ralph_loop`、`knowledge_writer` 与 `plugin_hooks` 已进入真实 Agent 执行。Scheduler 固定已发布 XpertVersion 并通过持久 Coordinator 执行单次、间隔和带时区 Cron；Ralph 对最终答案进行有界改进与严格验证；Knowledge Writer 只创建待审批 proposal；Plugin Hooks 只在无网 Sandbox 运行已安装 Skill 的显式 argv manifest。

自动化 execution 具有 occurrence 幂等、重叠/误触发策略、预算、lease、重试和死信，并能在 HITL/Client Tool 等待后继续。公开 Xpert App/API 拒绝这四类私有自动化中间件。完整契约见 `docs/XPERT_AUTOMATION.md`。

## Client Tools（2026-07-18）

`client_tools` 已进入私有 Agent 真实执行：Agent 调用会持久化为 Client Tool request，Chrome MV3 扩展在用户主动绑定的当前标签页执行固定 ARIA/ref 工具，结果返回后由 `ClientToolCoordinator` 从原 ReAct 断点继续。配对使用一次性短码和只展示一次的高熵 token；服务端只保存哈希。

修改页面的工具必须先经过同一 Agent 绑定的 `human_in_the_loop`，然后才会派发到 host。读取动作可在断线后安全重放，执行中的修改动作进入 `uncertain` 并禁止自动重放。公开 Xpert App/API 拒绝 `client_tools`。完整契约见 `docs/XPERT_CLIENT_TOOLS.md`。

## Browser Automation（2026-07-16）

`browser_automation` 已进入私有 Agent 真实执行：Playwright Chromium 位于独立联网 sidecar，主服务通过 Unix Domain Socket 调用受限 ARIA/ref 工具。首次访问新域名必须完成仅当前 session 有效的 `browser_domain` 审批；所有 mutating 工具还必须由绑定的 `human_in_the_loop` 覆盖。工具继续经过 policy、审批、audit 与安全 checkpoint。

Browser sidecar 不加入应用默认网络，不挂载源码、`.env`、Runtime Store 或 Docker Socket。内置 egress guard 与 Playwright route 双重拒绝私网、本机、Docker service、云元数据、危险协议和 DNS 混合解析。公开 Xpert App/API 的部署预检拒绝 Browser 中间件。完整契约见 `docs/XPERT_BROWSER.md`。

`XPERT-MIDDLEWARE-CONSOLIDATION-07` 已完成自编写能力与安全契约收口。客户端当前标签页桥、服务端 Browser sidecar、无网 Sandbox、Automation Coordinator 和自编写提案继续保持独立安全边界。

最后更新日期：2026-07-18

## 目标

ModelMirror 的 Agent 中间件复用 classic workflow runner。一次实现会同时覆盖本地 Workflow、已发布 Xpert、Goal、HandoffExecutor 和受策略约束的 Xpert App，不创建第二套 Agent 执行器。

当前提供上下文压缩、结构化输出、持久 Todo、LLM 工具选择、可恢复 HITL、Sandbox/Skill、隔离 Browser、Client Tools、Scheduler、Ralph Loop、Knowledge Writer、Plugin Hooks 以及 Xpert/Skill 自编写提案。Xpert 源码仅用于领域模型与交互参考，当前实现为 React/FastAPI 原生重写。

## 绑定模型

`runtime_middleware` 通过 `sourceHandle="middleware-binding" -> targetHandle="middleware"` 的边绑定到一个 `workflow_agent`。绑定边具有以下约束：

- 绑定节点不参与控制流拓扑、变量可达性、节点调度或独立执行。
- 一个中间件节点只能绑定一个 `workflow_agent`。
- 同一个中间件节点不能同时拥有绑定边和普通控制流边。
- 同一 Agent 的中间件按 `middlewarePriority` 升序、节点 ID 次序稳定编译。
- 普通边连接的旧中间件仍保持线性语义，并影响其后执行的 Agent。

每个 `workflow_agent` 会编译独立 `MiddlewarePipeline`。Agent 生命周期执行 `before_agent/after_agent`，直接模型调用和 ReAct 决策调用执行 `before_model/after_model`，工具继续经过相同 pipeline、tool policy 和 audit。

## 核心中间件

### Context Compression

`context_compression` 使用无 tokenizer 的保守 token 估算，在达到上下文预算触发比例时总结旧消息，并保留最近消息。Xpert Chat 的派生摘要写入 `XpertContextStore`，包含 revision、摘要模型和覆盖到的 message ID；原始消息不修改。

摘要失败时保留最近上下文并产生 warning。普通 Workflow、Goal 和 Handoff 使用运行态摘要；公共 App 只使用本次调用的临时摘要。

### Structured Output

`structured_output` 使用 Draft 2020-12 JSON Schema 校验 Agent 最终答案，不校验 ReAct 工具决策。第一次不合法时允许用当前执行模型修复一次；仍不合法时进入节点已有 retry、fallback 和 `exceptionHandling`。

成功结果会以规范 JSON 文本替换原答案，SSE 事件类型保持不变。现有 `outputSchemaMode/outputSchemaJson` 会编译为隐式结构化中间件，显式绑定配置优先。

### Todo Planner

`todo_planner` 提供 `todo_list`、`todo_create`、`todo_update` Runtime 工具，并继续经过 policy、audit 和 middleware。Todo 使用 revision 防止覆盖、遵守中间件配置的单作用域数量上限，并通过 `RuntimeTodoStore` 原子保存。

作用域：

- Xpert Chat：`conversation`
- Goal：`goal + step`
- Handoff：`handoff`
- 普通 Workflow：`task + node`
- Xpert App：`app_run`，只在本次进程运行内存在，不跨公共调用者持久化

Todo 是执行辅助状态，不替代 GoalStep，也不修改 GoalCoordinator 的依赖图。

### LLM Tool Selector

`llm_tool_selector` 在 Agent allowlist 和 tool policy 过滤之后运行。模型只能从已配置、已授权的工具中选择；Todo 等中间件必需工具强制保留。非法 JSON、超时或模型失败时使用确定性关键词排序回退。

选择结果只作用于当前 Agent run，不修改 Tool Registry、MCP session 或已发布 XpertVersion。

### Human In The Loop

`human_in_the_loop` 可以审批匹配的工具调用，也可以在最终答案写入变量前要求确认。工具审批顺序固定为 allowlist / tool policy、HITL、audit started、Provider、audit finished/failed；被 policy 拒绝的工具不会产生可绕过权限的审批入口。

- `interrupt_on_tools` 接受逗号或换行分隔工具名，`*` 表示审批所有工具。
- 工具决策支持批准、编辑参数和拒绝。编辑不能更换工具名，并会重新执行 schema 与运行时权限校验。
- 拒绝不会调用 Provider，人工原因作为合成工具结果返回 ReAct 循环，模型可以选择其他方案。
- `final_confirmation` 支持批准、人工替换或带反馈修订。修订轮数受 `max_revision_rounds` 限制。
- 审批超时范围为 30 秒至 24 小时，默认 1 小时；超时永不自动批准。

暂停由 `RuntimeApprovalStore` 和 `WorkflowExecutionStore` 原子持久化。执行状态保存队列、变量、已执行节点和当前 Agent 的 ReAct 轮次；审批决定后由单进程 `ApprovalCoordinator` 使用 lease 恢复。已完成节点不会重跑，待审批工具最多执行一次。服务重启会清除旧 lease 并恢复等待中的 execution。

Workflow 与 Xpert Chat 可重新连接 `GET /api/workflow/run/{task_id}/stream?after_sequence=` 读取安全事件日志。GoalStep、AgentTask 和 Handoff 在等待时使用 `waiting_approval`；审批过期后进入 `needs_attention`，不计入模型或工具重试。旧 `human_intervention` 节点继续保留既有 SSE 与 `/resume` API，但底层改用同一持久暂停存储。

## API

Todo 管理接口：

- `GET /api/runtime/todos?scope_type=&scope_id=&status=`
- `POST /api/runtime/todos`
- `PATCH /api/runtime/todos/{todo_id}`
- `DELETE /api/runtime/todos/{todo_id}`

审批与恢复接口：

- `GET /api/runtime/approvals?status=&task_id=&run_id=&scope_type=&scope_id=&limit=`
- `GET /api/runtime/approvals/{approval_id}`
- `POST /api/runtime/approvals/{approval_id}/decide`
- `POST /api/runtime/approvals/{approval_id}/reopen`
- `POST /api/runtime/approvals/{approval_id}/cancel`
- `GET /api/runtime/approval-coordinator/status`
- `GET /api/workflow/run/{task_id}/stream?after_sequence=`

审批决定必须携带当前 `revision`。重复或过期决定返回冲突，不会重复恢复执行。新增兼容 SSE 事件为 `runtime_approval_pending` 和 `runtime_approval_resolved`。

中间件菜单仍由 `GET /api/runtime/middleware-nodes` 提供元数据和配置 schema。执行支持仍由 workflow validate 与 classic runner 决定。

## 安全与观测

- RunRegistry checkpoint 只保存中间件名、状态、耗时、数量和错误摘要。
- 不记录 prompt、上下文正文、Todo 正文、工具输出、schema 修复输入或密钥。
- 工具选择器不能恢复被 policy 拒绝的工具。
- 中间件异常不能绕过节点的 retry、fallback 或 `exceptionHandling`。
- 公共 App 的 Todo 不持久化；其上下文摘要也不写入 Xpert 私有会话。
- 公开 Xpert App 与 OpenAI 兼容 API 禁止部署 `human_in_the_loop` 或 `human_intervention`，避免无客户端连接时悬挂请求。
- 审批列表、事件和 checkpoint 只保存脱敏参数、ID、状态和错误摘要；完整 prompt、工具结果与密钥不得进入公共接口。
- `RuntimeInterrupt` 与审批存储错误属于不可 fail-open 中断，工具 runner 不得降级为直接调用 Provider。

## Sandbox Files / Shell / Skills Runtime

`sandbox_files`、`sandbox_shell` 与 `skills_runtime` 已进入真实执行。绑定后的 `workflow_agent` 会按运行来源创建隔离工作区，并注册文件、命令、Skill 和产物工具；只启用 Sandbox 时不会隐式暴露 MCP 工具。

sidecar 完全断网且不挂载主服务源码、`.env` 或密钥。命令只接受 argv 数组和固定白名单，工作区路径拒绝绝对路径、`..` 与 symlink 逃逸。每个副作用工具调用使用稳定 operation ID，HITL 恢复不会重复执行已完成操作。

`sandbox_shell.require_approval=true` 时，validate 与运行时编译都要求同一 Agent 的 `human_in_the_loop.interrupt_on_tools` 覆盖 `sandbox_shell` 或 `*`。Skill 必须显式安装和选择，按需读取或 staging，脚本不会自动执行。产物发布到受控 `artifacts/` 后由管理 API 下载，物理路径不会外泄。详细契约见 `docs/XPERT_SANDBOX.md`。

公开 Xpert App/API 的部署预检与运行时都拒绝三类 Sandbox/Skill 中间件。

## 后续阶段

## Data X Indicators

`datax_indicators` 是绑定到 `workflow_agent` 的真实 Runtime Toolset 中间件。它要求 `toolMode=mcp_tools`，并在配置中固定 1–10 个项目、1–20 个语义模型、查询行数上限和是否允许创建提案。

读取工具只消费已发布指标，并通过受限 DSL 查询项目隔离 DuckDB；模型参数不能扩大配置 scope。写工具只创建带来源 Xpert、run、Goal 或 Handoff 元数据的 pending 提案。审批只生成指标草稿，显式发布后才进入线上检索与查询。公共 App 仅在 `allow_datax_read` 开启、存在 tool policy 且部署 scope 有效时获得只读工具，永远不注册提案工具。

详细数据模型、API 和安全约束见 `docs/XPERT_DATAX.md`。

Consolidation-07 之后已完成类型化文件记忆与 Office 实时自动化。Data X 指标闭环继续按真实使用价值推进；客户端副作用归入现有 Client Host 能力，不再单独拆出一轮目录型建设。

分布式调度、Workflow Engine V2 和公开 App 自动化仍不属于当前范围。
