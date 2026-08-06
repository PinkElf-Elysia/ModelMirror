# workflow-native 自研工作流设计

> **当前状态（2026-07-28）：** `/workflow` 是 classic React Flow 主入口，
> `/workflow-native` 是静态校验与设计实验线，`/rag` 是独立的本地知识系统。
> 本文前半部分保留增量时间线；涉及 Dify 主路径的早期表述已由当前状态取代。

> 2026-08-06 Agent Strategy V2：`agent.tool_first` 与
> `workflow_agent.mcp_tools` 共享 `server/xpert_runtime/agent_strategy/`
> 运行时。新增 `agentStrategy=auto|function_calling|react`；旧工作流缺失字段
> 时按 `auto`。`auto` 只在尚未执行工具且网关以 400/422 明确拒绝
> `tools/tool_choice/parallel_tool_calls` 时回退 ReAct。关闭
> `WORKFLOW_AGENT_STRATEGY_V2_ENABLED` 并重启即可恢复 ReAct-Lite，无需迁移数据。

> 2026-07-23 Prompt/Plugin：新增 `plugin_resource`，通过 `plugin-binding -> plugin` 绑定一个 `workflow_agent`，不参与控制流、变量可达性或节点调度。Xpert 发布固定 Plugin 与直接 Prompt Profile 版本；运行时把 Plugin 编译为固定 Toolset、命名空间化 Skill、已注册中间件预设和私有命令。别名、工具或中间件冲突 fail-closed。Slash Command 仍执行当前 Xpert，SSE 事件类型不变。

> 2026-07-23 Agent Features：Xpert 草稿和不可变版本已固定开场白、建议问题、会话标题/摘要、记忆回复、文件策略和 TTS/STT。会话摘要编译为输出 `workflow_agent` 的隐式 `context_compression`，文件关闭时附件不会进入运行或 Goal。`XpertAgentConfig` 的最大并发与递归限制约束整棵执行树；节点级工具预算仍是更窄的局部限制。Classic Workflow SSE 不新增事件类型。

> 2026-07-23 Toolset Semantics：`workflow_agent` 已支持单工具或有界并行 `tools` 决策。并行批次只接受只读、`parallel_safe`、非敏感、非终点工具，并受并发、总调用数、决策轮次和嵌套深度预算约束。固定 Toolset 版本同时保存敏感、终点、Tool Memory 与公共 App 语义；敏感工具必须有 HITL，terminal 成功后直接成为最终输出。SSE 事件类型保持兼容。

> 2026-07-23 Toolset Runtime：`toolset_resource` 已成为真实资源节点，通过 `toolset-binding -> toolset` 绑定单个 `workflow_agent`。节点引用可发布的 MCP、OpenAPI、OData 或内置 Provider Toolset；Xpert 发布时固定具体版本，运行时只暴露版本中启用的工具，并复用 Tool Policy、HITL、Audit 与 checkpoint。绑定边不进入控制流和变量可达性；公开 App 仅允许逐工具显式确认的固定版本安全只读能力。

> 2026-07-22 Resource Nodes：新增 `external_xpert` 与 `knowledge_base`。资源节点通过 `sourceHandle="expert-binding" -> targetHandle="expert"` 或 `sourceHandle="knowledge-binding" -> targetHandle="knowledge"` 绑定单个 `workflow_agent`，不参与控制流、变量可达性或节点调度。发布 Xpert 时外部专家解析为不可变版本；知识库继续读取活动索引。同步专家调用与异步 Handoff 是两套明确语义。

> 2026-07-19 Office Automation：`office_automation` 可通过 middleware binding 绑定到 `workflow_agent`，复用 `wait_kind=client_tool` 的持久暂停和恢复语义。22 个 Word/Excel/PowerPoint 工具由用户主动绑定的 Office.js Task Pane 执行；绑定边不参与控制流，修改工具必须有 HITL 覆盖，公开 App/API 禁止该中间件。完整契约见 `docs/XPERT_OFFICE_AUTOMATION.md`。

> 2026-07-19 File Memory Middleware：`xpert_file_memory` 可通过 middleware binding 绑定到 `workflow_agent`，提供索引、摘要 digest、正文选择和候选写回。绑定边不参与控制流；显式配置优先于旧 `memoryReadEnabled/memoryWriteEnabled` 字段。普通 Workflow 无 Xpert 上下文时安全跳过，公开 App 只允许显式开启的只读访问。完整契约见 `docs/XPERT_FILE_MEMORY.md`。

> 2026-07-18 Authoring Middleware：`xpert_authoring` 与 `skill_creator` 可绑定到 `workflow_agent`，通过 Runtime 工具模式创建版本化提案。审批后只写入 Xpert 或 Workspace Skill 草稿，发布与安装仍需用户显式操作；公开 App/API 禁止两类中间件，完整边界见 `docs/XPERT_AUTHORING.md`。

> 2026-07-18 Client Tools：`client_tools` 可通过 middleware binding 绑定到 `workflow_agent`。工具请求使用持久 `wait_kind=client_tool` 暂停并由配对 Chrome 当前标签页执行；绑定边、classic workflow definition 和既有 SSE 仍兼容。修改页面的工具必须有 HITL 覆盖，公开 Xpert App/API 禁止该中间件，完整边界见 `docs/XPERT_CLIENT_TOOLS.md`。

> 2026-07-16 Browser Runtime：`browser_automation` 可通过 middleware binding 绑定到 `workflow_agent`，仅增加受控 Browser Runtime 工具，不参与控制流。首次域名访问使用持久 `browser_domain` 审批，mutating 工具要求 HITL 覆盖；网络访问由独立 sidecar 双重阻断私网和本机。公开 Xpert App/API 禁止该中间件，完整边界见 `docs/XPERT_BROWSER.md`。

> 2026-07-08 路线重整：classic `/workflow` 后续节点规划改为按 Xpert 真实菜单分类推进。下一步不再盲目追加单点节点，而是先做节点注册表、调色板分类和右侧配置面板对齐；已有执行语义保持不变。
> 2026-07-08 工作空间入口：`/studio` 已纳入 Xpert 式工作空间资源 Hub，统一展示工作流、知识库、MCP、Skill、提示词、环境与 RunRegistry 摘要。Classic `/workflow` 仍是画布主入口，后续节点与配置面板对齐会从该 Hub 进入。
> 2026-07-09 配置侧栏：`agent` 与 `workflow_agent` 的右侧配置已进入 Xpert 式分区侧栏第一版，包含节点、参数、提示词/模型、中间件、知识库、工具、运行策略、输出结构、记忆写入。当前只保存新增配置草稿，不改变 runner、validate、SSE 或节点协议。
> 2026-07-09 知识流水线：`/rag` 已新增只读 Pipeline Draft API 与四段 stage UI，展示数据源、处理器、分块器、图像理解草稿。Classic workflow 的 `knowledge_citation` 节点语义不变，仍只读取 CitationAnchor 摘要 JSON。
> 2026-07-09 Runtime Ops：新增 `/runtime` 只读运维页，复用现有 MCP sessions、Tool Registry、RunRegistry checkpoints 与 Skill installed API。该页只做运行观测入口，不改变 workflow runner、SSE、MCP/Skill 管理协议或节点语义。
> 2026-07-09 Workspace Hub：`/studio` 已补齐快速创建 / 连接入口、资源标签过滤、API 工具与数据库待接入卡片，以及基于 RunRegistry 的轻量运行摘要。本轮只增强工作空间入口，不改变 `/workflow` 节点协议、SSE、validate 或 runner。

> 2026-07-09 Workflow Node Registry：新增 `GET /api/workflow/node-registry`，把工作流与知识流水线节点菜单元数据后端化。该 registry 只负责 palette 分类、可拖拽 item 与禁用 placeholder，不替代 `SUPPORTED_NODE_KINDS`、validate 或 classic runner；前端 API 失败时回退本地 registry。

> 2026-07-12 Knowledge Pipeline execution：`/rag` 已支持持久化 ingestion job、候选索引预览、人工激活和版本回滚；后续多模态增量将执行阶段扩展为 `load / vision / process / chunk / embed / store`。该迭代不新增工作流节点，也不改变 `knowledge_citation` 的输入输出协议；节点通过 `RagService` 自动读取知识库 active version，没有 active version 时继续兼容 legacy index。

> 2026-07-13 Advanced RAG V2：Knowledge Pipeline 候选版本支持递归/父子分块、分段标识符、向量与 FTS5 双索引、全文/向量/混合检索、权重、阈值和可选 Rerank。`knowledge_retrieval` 与 `knowledge_citation` 不新增节点字段，仍通过 `RagService` 自动消费 active version 固定 profile；候选预览参数不会修改版本配置，旧索引继续 vector-only 兼容。

> 2026-07-13 RAG Processor：Knowledge Pipeline 新增结构感知 TXT/Markdown/PDF 处理、General/QA/Summary 索引、逐文档失败与恢复。`knowledge_retrieval`、`knowledge_citation`、Chat、Xpert、Goal 与 App 的协议均不变，它们继续统一消费 active version；候选 Processor profile 和处理产物不会写入 workflow definition。

> 2026-07-13 Knowledge Canvas：新增 `/rag/:kbId/pipeline` 可执行知识流水线画布。画布 Graph 编译为现有 Pipeline Draft，并复用同一个 Job Executor；它不是 classic workflow 的新节点，也不改变 `knowledge_retrieval`、`knowledge_citation`、SSE 或 workflow definition。Classic workflow 继续只消费人工激活的知识版本。

> 2026-07-15 Multimodal Knowledge：Knowledge Canvas 的 `image_understanding` 已从占位升级为可选真实阶段，支持图片和扫描 PDF 的 VLM OCR/视觉摘要，并继续进入既有 Processor 与双索引。Classic workflow 不新增字段；`knowledge_retrieval` 与 `knowledge_citation` 自动消费 active version，并可获得页码、视觉类型与来源 block 的兼容扩展字段。

> 2026-07-16 Knowledge Evaluation：新增离线 Evaluation Set、跨版本指标运行与 Promotion Gate。该能力只约束候选知识版本的激活，不新增 workflow 节点，也不改变 `knowledge_retrieval`、`knowledge_citation`、SSE 或 workflow definition；已发布 workflow、Xpert、Goal 与 App 继续自动消费通过门禁后激活的 active version。

> 2026-07-16 Knowledge Agent：`workflow_agent` 可在既有 Runtime 工具模式下启用知识搜索、精确读取、引用和写入提议，并固定 1 至 5 个知识库作用域。写入只生成 Inbox 提议，批准后构建候选且必须通过 Evaluation Gate 才能推广；classic workflow 节点协议和 SSE wire format 不变。

> 2026-07-09 Workflow Agent 运行策略：`workflow_agent` 开始接入 Xpert 式侧栏的第一批真实运行语义：失败重试、备用模型、禁用输出、异常转空输出。该变更不改变节点协议或 SSE wire format，不影响普通 `agent`，也不接文件理解、并行工具调用、记忆写入或输出 schema 强校验。

> 2026-07-10 Runtime Ops 第二版：`/runtime` 已补充 MCP 状态细分、失败 run 摘要、checkpoint severity 统计、禁用的“重试待接入”入口，以及 `GET /api/runtime/environment-summary` 脱敏环境摘要。该变更仍只做运行观测，不触发真实重试、MCP 启停、Skill 安装/卸载或环境变量编辑。

## Xpert 工作流节点规划

真实 Xpert 画布把节点入口分成工作流、中间件、知识流水线、工具集等菜单。ModelMirror 后续仍基于现有 React/FastAPI classic workflow 迭代，但节点规划按 Xpert 分类收敛。

### 资源绑定边

资源绑定边把可复用资源编译为目标 Agent 的 Runtime Toolset，不是普通工作流步骤：

| 资源节点 | 绑定契约 | 执行语义 |
| --- | --- | --- |
| `external_xpert` | `expert-binding -> expert` | 调用发布时固定版本的外部 Xpert；复用 classic runner，不通过 HTTP 回环 |
| `knowledge_base` | `knowledge-binding -> knowledge` | 向目标 Agent 暴露限定知识库的 `knowledge_search/get/cite`；使用活动 Retrieval Profile |
| `toolset_resource` | `toolset-binding -> toolset` | 调用发布时固定的 MCP/OpenAPI/OData Toolset 版本；工具 Schema、别名、默认参数和白名单来自不可变快照 |
| `plugin_resource` | `plugin-binding -> plugin` | 编译固定 Plugin 版本内的 Prompt、Skill、Toolset 引用和中间件预设；不加载动态服务端代码 |
| `runtime_middleware` | `middleware-binding -> middleware` | 编译目标 Agent 的 middleware pipeline |

共同约束：

- 同一资源节点只能绑定一个 `workflow_agent`，不得同时连接控制流边。
- 绑定 Agent 必须启用 Runtime 工具模式；Tool Policy、HITL、Audit 和 checkpoint 继续生效。
- OpenAPI/OData 写操作必须由目标 Agent 绑定的 HITL 中间件按工具名或 `*` 覆盖；测试、Xpert 发布和运行时均会重复检查。
- Plugin 的工具名、Prompt 别名和同类中间件不得与目标 Agent 既有资源冲突；Plugin Toolset schema hash 必须与固定版本一致。
- 资源节点不进入拓扑排序、变量声明、可达性检查和节点执行队列。
- `external_xpert` 最大嵌套深度为 4，禁止自身调用和协作循环；公开 App 第一版禁止该资源。
- `knowledge_base` 第一版只读。写入仍通过 Knowledge Proposal/Inbox、候选版本、Evaluation Gate 和 Promote。
- `agent_handoff` / `handoff_router` 用于异步移交、后台执行和人工接管，不与同步 `external_xpert` 合并。
- 公共 App 拒绝 `plugin_resource`；安全 Prompt Command 必须直接绑定 Xpert 并固定版本。

### 工作流节点分类

| Xpert 分类 | Xpert 菜单示例 | ModelMirror 已有/对应节点 | 下一步 |
| --- | --- | --- | --- |
| 逻辑 | 触发器、路由、迭代、子流程、列表操作、变量聚合、变量赋值 | `input`、`condition`、`iteration`、`list_operation`、`variable_aggregator`、`variable_assign` | 用节点 registry 统一分类与元数据 |
| 转换 | 问题分类器、知识检索、代码执行、模板、JSON 序列化、JSON 反序列化、回答 | `question_classifier`、`knowledge_retrieval`、`knowledge_citation`、`code`、`template_transform`、`output` | 补 JSON 序列化/反序列化节点前先统一 registry |
| 工具 | HTTP、工具调用、智能体工作流、任务移交 | `http_request`、`mcp_tool`、`workflow_agent`、`agent_task`、`agent_handoff`、`handoff_router` | 继续收敛到 Runtime Toolset 与 RunRegistry |
| 记忆 | 数据库 | 暂无完整数据库/记忆节点，仅有 RunRegistry 与 RAG 元数据 | 等工作空间资源与记忆模型稳定后推进 |
| 其他 | 注释 | 暂无 | 作为 UI-only 节点，低优先级 |

### 中间件分类

当前已注册/实现的最小中间件包括 `system_prompt_injector`、`event_recorder`、`tool_policy`、`tool_audit`、`mcp_tools`。Xpert 真实菜单还包含文件记忆、浏览器自动化、上下文压缩、人机协同、插件 Hooks、结构化输出事件等。后续不直接追完整列表，先保证 middleware registry、配置表单、运行链路和观测一致。

### 知识流水线分类

Xpert 知识流水线菜单按数据源、处理器、分块器、图像理解组织。ModelMirror 当前已有 RAG pipeline 元数据、`knowledge_citation` 节点、四段草稿、版本化执行，以及 Advanced RAG V2 的递归/父子分块、向量/FTS5 双索引和混合检索。工作流节点不直接保存检索实现细节，而是统一消费知识库 active version 固定 profile；候选失败或回滚不需要修改 workflow definition。

### 对齐顺序

1. `XPERT-WORKFLOW-PALETTE-01`：节点注册表与 Xpert 分类菜单。
2. `XPERT-STUDIO-PANEL-01`：已完成 `agent` / `workflow_agent` 侧栏分区第一版，新增配置先存储为草稿。
3. `XPERT-KNOWLEDGE-PIPELINE-02`：已完成知识流水线草稿 schema 与 stage UI。
4. `XPERT-RUNTIME-OPS-01`：已完成 `/runtime` 只读运维页，聚合 MCP Runtime、工具注册表、Skill 与 RunRegistry 摘要。
5. `XPERT-WORKSPACE-HUB-02`：已在 `/studio` 上补资源快速入口、标签过滤、待接入资源卡片和运行摘要；下一步转向 `XPERT-WORKFLOW-REGISTRY-API-01`，评估节点 registry 后端化。

### 2026-07-08 增量：Xpert 分类节点库

`/workflow` 节点库已从平铺静态数组迁移为前端 `workflowNodeRegistry` 分类渲染。节点库仍位于画布顶部附近的浮层中，不恢复为常驻左栏；右侧仍使用 `配置 / 运行` tabs，避免节点库、配置和运行结果纵向堆叠。

当前节点库分为三个 tab：

- `工作流`：按逻辑、转换、工具、记忆、其他分组展示现有可运行节点。
- `中间件`：继续从 `GET /api/runtime/middleware-nodes` 拉取 runtime middleware metadata，并保持现有 JSON 拖拽 payload。
- `知识流水线`：classic workflow 暴露可运行的 `knowledge_citation` 节点；独立 `/rag/:kbId/pipeline` 画布负责数据源、视觉理解、处理器、分块器、Embedding 与索引执行，避免把两套节点协议混在一起。

本轮不新增后端节点类型，不修改 `NativeNodeKind`、validate、classic runner、SSE 协议或 React Flow 拖拽协议。数据库、注释、JSON 序列化/反序列化和知识流水线 stage 仅显示“待接入”占位，不会生成无法运行的画布节点。

### 2026-07-09 增量：Xpert 式智能体配置侧栏

`/workflow` 右侧 `NodeConfig` 已对 `agent` 与 `workflow_agent` 使用统一的智能体配置侧栏。该侧栏按 Xpert 真实界面拆成节点、参数、提示词/模型、中间件、知识库、工具、运行策略、输出结构和记忆写入分区。

本轮只保证可见、可编辑、可存储：

- 继续复用现有 `agentMode`、`instruction`、`modelId`、`rolePrompt`、`taskInput`、`toolMode`、`toolNames`、`maxIterations`、`promptSuffix`、`outputVariable` 执行字段。
- 新增 `disableOutput`、`enableFileUnderstanding`、`parallelToolCalls`、`retryOnFailure`、`fallbackModelId`、`exceptionHandling`、`outputSchemaMode`、`outputSchemaJson`、`memoryWriteEnabled`、`memoryWriteTarget`、`nodeParametersJson` 作为配置草稿字段。
- 中间件与知识库区块当前只提示继续使用画布上的 `runtime_middleware`、`knowledge_retrieval`、`knowledge_citation` 节点，不做节点内嵌语义。
- 本轮不修改后端 validate、runner、RunRegistry、SSE 和拖拽协议；非 Agent 节点仍使用原有配置表单。

> 2026-07-08 状态补充：Chat Toolset 运行观测进入最小闭环。`tool_mode=mcp_tools` 的聊天请求会登记 `chat` run，响应 header 返回 `X-ModelMirror-Runtime-Run-Id` / `X-ModelMirror-Runtime-Task-Id`；前端聊天页展示 run 状态、checkpoint、tool events 与 audit 摘要。普通聊天仍不创建 chat run，SSE wire format 不变。
> 2026-07-08 状态补充：`/api/chat` 已接入默认关闭的 Runtime Toolset 工具模式。前端聊天页可显式开启 MCP 工具循环，后端复用 `run_tool_with_runtime`、`tool_policy` 与 `tool_audit`，并继续使用现有 OpenAI SSE delta 结构输出最终答案。当前不是 OpenAI function calling，不自动创建 Handoff，也不改变 workflow、RAG、Skill 或 MCP 连接主路径。
> 2026-07-07 状态补充：RunRegistry Trace / Checkpoint 进入最小闭环。Workflow run、`workflow_agent`、`agent_task`、`agent_handoff` 会写入内存态 checkpoint；前端“运行观测”会读取 `GET /api/runtime/runs/{run_id}/checkpoints` 展示当前 run 与子 run 的时间线摘要。当前不做持久化、自动重试、队列调度或 checkpoint resume。
> 2026-07-07 状态补充：`workflow_agent` 已支持 Runtime Toolset 工具模式。`toolMode=none` 保持单步模型执行；`toolMode=mcp_tools` 使用轻量 JSON 决策协议调用 MCP 工具，并复用 `run_tool_with_runtime`、`tool_policy`、`tool_audit`。旧 `agent.tool_first` 也已收敛到同一条 runtime toolset 路径。当前不是 OpenAI function calling，不做自动 Handoff 或真实多 Agent 协作。

> 2026-07-06 状态补充：classic workflow 新增 `workflow_agent` 节点。该节点使用 `rolePrompt` 作为该节点 system prompt、`taskInput` 作为用户输入调用模型，流式输出结果并写入 `outputVariable`，同时登记 `workflow_agent` 子 run。当前是单步模型智能体执行，不接 MCP 工具、Handoff 自动调度或真实多 Agent 协作。

> 2026-07-06 状态补充：Handoff Queue 进入人工处理闭环。`accept/reject/complete` 会记录处理者、处理时间和结果/原因摘要，并同步到 `agent_handoff` run metadata；`WorkflowRun` 子 run 摘要会展示 handler/result。当前不做自动调度、目标 Agent 执行、持久化队列或权限系统。

> 2026-07-06 状态补充：Handoff 观测前端进入最小闭环。`GET /api/runtime/runs` 支持按 `parent_run_id` / `source_id` 查询，`WorkflowRun` 的“运行观测”可展示 workflow 下的 `agent_task` 与 `agent_handoff` 子 run；新增 `GET /api/runtime/agent-handoffs?task_id=&status=&target_agent=&limit=` 供 MetaAgent Handoff Inbox 查询。当前只做观测和手动状态操作，不做真实调度、队列或持久化。

> 2026-07-05 状态补充：classic workflow 已接入 RunRegistry 最小可观测闭环，并恢复 `/workflow` 画布的节点库浮层与配置/运行 tabs 布局。

## 2026-07-05 增量：RunRegistry 与工作流运行观测

Classic workflow 每次运行会登记一条 `workflow` run，并在 `workflow_meta` / `workflow_end` SSE 中携带 `run_id`。`workflow_agent` 节点执行模型智能体步骤时同步登记 `workflow_agent` run；`agent_task` 节点创建 AgentTask 时同步登记 `agent_task` run；`agent_handoff` 节点创建 Handoff 时同步登记 `agent_handoff` run。四类 run 通过 `parent_run_id` 与 metadata 互相关联，保留现有 workflow `task_id`、AgentTask `task_id` 与 Handoff `handoff_id` 协议不变。

新增 Runtime Run API 用于最小观测：

- `GET /api/runtime/runs?run_type=&status=&limit=`
- `GET /api/runtime/runs/{run_id}`
- `GET /api/runtime/runs/{run_id}/checkpoints`
- `POST /api/runtime/runs/{run_id}/cancel`

当前 RunRegistry 是内存态索引，不是调度器；取消 run 仅更新观测状态，不中断正在执行的 workflow、AgentTask 或 Handoff。Checkpoint 仅保存摘要和元信息，不保存完整 prompt、模型输出、工具结果或密钥。前端 `WorkflowRun` 的“运行观测”折叠区会展示当前 `run_id`、RunRegistry 摘要、当前 run 与子 run checkpoint，并继续展示 runtime events 与 tool audit records。

## 2026-07-05 增量：`/workflow` 画布布局恢复

`/workflow` 布局恢复为“画布 + 单一右侧工作台”：节点库位于画布顶部附近的下拉/浮层中，避免常驻左栏；右侧工作台使用 `配置 / 运行` tabs 承载 `NodeConfig` 与 `WorkflowRun`，点击运行时切到运行页。该调整只恢复布局体验，不改变节点数据结构、拖拽 payload、SSE 协议或后端执行逻辑。

workflow-native 是模镜自研工作流的渐进式实验线。它不替换当前稳定的
classic `/workflow`，也不另建第二套 `/rag`。该入口主要承担静态图校验与
设计验证；真实工作流执行继续复用 classic runner。

最后更新日期：2026-07-23
维护人：模镜团队

## 2026-07-10 增量：Xpert Handoff 自动执行

`agent_handoff` 与 `handoff_router` 已支持显式 Xpert 自动执行。节点新增 `executionMode`、`waitForCompletion`、`resultVariable` 与 `waitTimeoutSeconds`：

- `manual` 保持原有 Inbox 人工接受、拒绝和完成流程。
- `xpert_auto` 要求 `targetAgent=xpert:<slug-or-id>`，执行器固定目标发布版本并复用 classic Xpert runner。
- 异步模式继续把 `handoff_id` 写入 `outputVariable`；同步等待模式额外把目标结果写入 `resultVariable`。
- AgentTask/Handoff 可通过 Docker 文件卷恢复，并支持 lease、重试等待、死信和重新入队。

RunRegistry 记录 `source Xpert -> handoff -> target Xpert` 父子关系和尝试 checkpoint，不记录完整任务输入、模型输出、工具结果或密钥。当前仅支持单后端进程，不是 Redis/Celery 或数据库调度器。

## 目标与边界

目标：

- 在独立路由 `/workflow-native` 中承载自研实验，不影响 `/workflow`。
- 复用 classic 画布的 `WorkflowDefinition` 结构，避免前后端出现两套图模型。
- 提供 `/api/workflow-native/validate`，只做静态校验，不执行节点。
- 在 `/api/workflow/run` classic 运行器中试点 `variable_assign`、`http_request`、`list_operation`、`iteration` 四类本地节点。
- 为后续 `/api/workflow-native/run`、模板、版本迁移和 Dify 导入打接口基础。

本阶段不做：

- workflow-native API 自身不执行 LLM、Tool、MCP、RAG 或代码节点。
- 不实现跨节点子图循环，`iteration` 当前只在单节点内对逗号分隔文本做本地迭代。
- 不替换 classic `/workflow` 主入口。
- workflow-native 实验线不单独实现发布、版本管理或独立观测面板；Xpert Studio 的版本快照、RunRegistry 与聊天运行复用 classic runner，并不改变本实验线的边界。
- 不从 classic runner 分叉第二套运行行为。

## 2026-07-10 增量：Xpert Studio 发布与运行

新增 Xpert Studio 的草稿、发布和聊天运行入口：

- /agents/studio 提供 Xpert 列表、创建入口、草稿 revision、版本列表和发布预检。
- Studio 复用现有 WorkflowEditor；普通 /workflow 仍使用本地草稿，不被 Xpert Store 接管。
- 发布版本保存不可变 workflow 快照，后续草稿改动不会影响已发布版本。
- /agents/xpert/:xpertId/chat 从服务端加载已发布版本，通过 classic runner 执行，并复用 workflow_agent、Toolset、Knowledge、Middleware、Handoff、RunRegistry 与 checkpoint。
- 当前发布入口要求唯一输入和输出、可用 workflow_agent 配置，并禁止 human_intervention；已支持 Goal、文件/记忆、自动 Handoff、Knowledge Execute 与固定版本 App/API。

### Xpert App 执行边界

公开 App 复用 classic runner，但固定不可变 XpertVersion，使用 `run_type=xpert_app`。工具、Handoff 与 Xpert 记忆默认关闭；工具开启后仍必须先加载 `tool_policy`，否则默认拒绝。公开 JSON/SSE 只转发最终输出，不改变普通 `/workflow`、Xpert Chat、Goal 或 HandoffExecutor 的协议。

## 当前入口与 legacy Dify 关系

当前路由：

```text
/workflow         -> classic React Flow 主入口
/workflow/classic -> 同一 classic 画布的兼容入口
/rag              -> ModelMirror 本地知识系统
/workflow-native -> 自研工作流实验线
/api/dify/*       -> 未被主前端路由使用的 legacy compatibility
```

如果 native 实验出现问题，回滚方式是关闭或隐藏 `/workflow-native` 入口；
`/workflow`、`/workflow/classic` 和 `/rag` 不需要迁移数据。

## 图模型

前端类型：

```typescript
interface NativeWorkflowDefinition extends WorkflowDefinition {
  version: string;
  source: "workflow-native" | "classic" | "dify-import";
}
```

后端模型位于 `server/workflow_native/schemas.py`，字段对齐 classic 的 `WorkflowPayload`：

```json
{
  "id": "draft",
  "title": "linear",
  "version": "native-draft",
  "source": "workflow-native",
  "nodes": [
    {
      "id": "input",
      "type": "input",
      "data": {
        "kind": "input",
        "variableName": "user_input"
      }
    }
  ],
  "edges": [
    {
      "id": "e1",
      "source": "input",
      "target": "llm"
    }
  ]
}
```

## Dify 概念映射

| Native 节点 | Dify 概念 | 当前差异 |
| --- | --- | --- |
| `input` | `start` / user input | native 只声明变量名，Dify 支持完整输入表单。 |
| `llm` | `llm` | native 当前只校验 `modelId`、`prompt`、`outputVariable`。 |
| `condition` | `if-else` | native MVP 只支持 `equals`、`contains`。 |
| `code` | `code` | native 只允许安全内置字符串操作，Dify 使用沙箱代码执行。 |
| `variable_assign` | `variable-assigner` | native 把模板渲染进一个变量，不实现 Dify 的复杂变量写入策略。 |
| `template_transform` | `template-transform` | native 当前是长文本模板渲染器，不做文件导出。 |
| `variable_aggregator` | `variable-aggregator` | native 聚合字符串变量，输出文本或 JSON 字符串。 |
| `parameter_extractor` | `parameter-extractor` | native 复用现有模型调用链，返回 JSON 字符串；无 Key 时降级为空对象。 |
| `knowledge_retrieval` | `knowledge-retrieval` | native 复用本地 RAG 服务；索引未就绪时返回 warning，不中断流程。 |
| `document_extractor` | `document-extractor` | native 仅读取受限目录内本地文件，不提供上传 UI。 |
| `question_classifier` | `question-classifier` / 问题分类器 | native 仅支持关键词规则分类，可选 LLM 回退默认关闭。 |
| `agent` | `agent` | native 提供 ReAct-Lite Agent，支持直接回答和 MCP 工具循环两种模式。 |
| `mcp_tool` | `tool` / MCP 工具 | native 调用全局 MCP 工具注册表中已连接的工具，需先在 `/mcps` 建立 Server 会话。 |
| `time_tool` | 时间工具 | native 获取当前时间、时间戳或格式化日期文本，不依赖外部服务。 |
| `http_request` | `http-request` | native 仅支持 GET/POST 文本响应，默认关闭真实出站请求。 |
| `list_operation` | `list-operator` | native 当前基于逗号分隔字符串，尚无完整数组变量系统。 |
| `iteration` | `iteration` | native 当前只做节点内迭代，不执行跨节点子图。 |
| `output` | `end` / `answer` | native 输出指定变量，Dify 支持更丰富的结束响应。 |

参考点：Dify 工作流由节点、边、变量和运行态组成；native 当前只借鉴节点概念、拓扑顺序和静态校验分类，不复制 Dify 源码实现。

暂不接入的节点：问题理解、复杂多 Agent 协作、人工介入之外的复杂审批流。这些能力依赖更完整的编排或新的异步交互模型，需要独立设计文档和测试护栏后再进入 native 实验线。

## API 契约

### GET `/api/workflow-native/templates`

返回 native 模板列表。当前只提供一个线性三节点样例。

```bash
curl http://localhost:8000/api/workflow-native/templates
```

响应：

```json
[
  {
    "id": "native-linear-starter",
    "title": "输入 -> LLM -> 输出",
    "description": "用于验证 workflow-native 静态图校验的最小三节点样例。",
    "workflow": {
      "id": "native-linear-starter",
      "title": "Native linear starter",
      "version": "native-draft",
      "source": "workflow-native",
      "nodes": [],
      "edges": []
    }
  }
]
```

### POST `/api/workflow-native/validate`

只做静态校验。即使校验失败，HTTP 仍返回 `200`，用 `valid=false` 和 `issues` 表示图本身的问题，避免和网关或服务异常混淆。

合法三节点样例：

```bash
curl -X POST http://localhost:8000/api/workflow-native/validate \
  -H "Content-Type: application/json" \
  -d "{\"workflow\":{\"id\":\"draft\",\"title\":\"linear\",\"nodes\":[{\"id\":\"input\",\"type\":\"input\",\"data\":{\"kind\":\"input\",\"variableName\":\"user_input\"}},{\"id\":\"llm\",\"type\":\"llm\",\"data\":{\"kind\":\"llm\",\"modelId\":\"openai/gpt-4o-mini\",\"prompt\":\"请回答 {{user_input}}\",\"outputVariable\":\"llm_output\"}},{\"id\":\"output\",\"type\":\"output\",\"data\":{\"kind\":\"output\",\"outputVariable\":\"llm_output\"}}],\"edges\":[{\"id\":\"e1\",\"source\":\"input\",\"target\":\"llm\"},{\"id\":\"e2\",\"source\":\"llm\",\"target\":\"output\"}]}}"
```

响应：

```json
{
  "valid": true,
  "issues": [],
  "order": ["input", "llm", "output"],
  "node_count": 3,
  "edge_count": 2
}
```

带环图样例：

```bash
curl -X POST http://localhost:8000/api/workflow-native/validate \
  -H "Content-Type: application/json" \
  -d "{\"workflow\":{\"id\":\"draft\",\"title\":\"cycle\",\"nodes\":[{\"id\":\"input\",\"type\":\"input\",\"data\":{\"kind\":\"input\",\"variableName\":\"user_input\"}},{\"id\":\"output\",\"type\":\"output\",\"data\":{\"kind\":\"output\",\"outputVariable\":\"user_input\"}}],\"edges\":[{\"id\":\"a\",\"source\":\"input\",\"target\":\"output\"},{\"id\":\"b\",\"source\":\"output\",\"target\":\"input\"}]}}"
```

响应包含：

```json
{
  "valid": false,
  "issues": [
    {
      "code": "cycle_detected",
      "message": "Workflow graph contains a cycle.",
      "severity": "error"
    }
  ],
  "order": []
}
```

### 预留 POST `/api/workflow-native/run`

该接口暂不实现。未来会按 validate 通过后的拓扑顺序执行节点，并继续保持 `/api/workflow/run` classic 行为不变。

## 错误模型

`ValidationIssue` 字段：

```json
{
  "code": "missing_input_node",
  "message": "Workflow needs at least one input/start node.",
  "severity": "error",
  "node_id": "input",
  "edge_id": "e1"
}
```

当前错误码：

- `duplicate_node_id`
- `unknown_node_kind`
- `missing_input_node`
- `missing_output_node`
- `missing_input_variable`
- `invalid_variable_name`
- `missing_llm_model`
- `missing_llm_prompt`
- `missing_llm_output_variable`
- `invalid_condition_operator`
- `missing_condition_variable`
- `missing_condition_value`
- `invalid_code_operation`
- `missing_output_variable`
- `missing_template_variable`
- `missing_condition_variable_reference`
- `missing_output_variable_reference`
- `invalid_edge_reference`
- `cycle_detected`
- `missing_variable_assign_name`
- `invalid_variable_assign_name`
- `missing_variable_assign_template`
- `missing_http_request_url`
- `invalid_http_request_method`
- `invalid_http_request_headers_json`
- `missing_http_request_output_variable`
- `invalid_http_request_output_variable`
- `missing_http_request_body_variable_reference`
- `missing_template_transform_template`
- `missing_template_transform_output_variable`
- `invalid_template_transform_output_variable`
- `missing_aggregator_variable_names_empty`
- `invalid_aggregator_variable_name`
- `missing_aggregator_output_variable`
- `invalid_aggregator_output_variable`
- `missing_aggregator_variable_reference`
- `missing_parameter_extractor_input_variable`
- `missing_parameter_extractor_schema`
- `missing_parameter_extractor_model_id`
- `missing_parameter_extractor_output_variable`
- `invalid_parameter_extractor_output_variable`
- `missing_parameter_extractor_input_variable_reference`
- `missing_knowledge_retrieval_query_variable`
- `invalid_knowledge_retrieval_top_k`
- `missing_knowledge_retrieval_output_variable`
- `invalid_knowledge_retrieval_output_variable`
- `missing_knowledge_retrieval_query_variable_reference`
- `missing_document_extractor_source_path`
- `missing_document_extractor_output_variable`
- `invalid_document_extractor_output_variable`
- `missing_document_extractor_source_path_reference`
- `missing_list_operation_input_variable`
- `invalid_list_operation_operator`
- `missing_list_operation_separator`
- `missing_list_operation_output_variable`
- `invalid_list_operation_output_variable`
- `missing_list_operation_input_variable_reference`
- `missing_iteration_input_variable`
- `missing_iteration_variable`
- `invalid_iteration_variable`
- `missing_iteration_template`
- `missing_iteration_output_variable`
- `invalid_iteration_output_variable`
- `missing_iteration_input_variable_reference`

## 测试流程

后端测试：

```bash
python -m pytest server/tests/test_workflow_native_validate.py -q
```

全量后端回归：

```bash
python -m pytest server/tests/ -q
```

前端构建：

```bash
cd client
npm.cmd run build
```

## 2026-06-17 增量：人工介入节点

`human_intervention` 已进入 workflow-native / classic 共享实验线。它继续支持文本输入、既有 SSE 与 REST resume，同时底层暂停状态已统一写入 `WorkflowExecutionStore` / `RuntimeApprovalStore`，页面刷新或容器重启后可以恢复。当前仍不提供多人协作或用户权限体系。

### 节点映射

| Native 节点 | Dify 概念 | 当前差异 |
| --- | --- | --- |
| `human_intervention` | `human-in-the-loop` | classic 运行器通过持久 execution 暂停并兼容 `/api/workflow/run/{task_id}/resume`；Agent 工具与最终输出审批由绑定的 `human_in_the_loop` middleware 提供。 |
| `question_classifier` | `question-classifier` / 问题分类器 | native 仅支持关键词规则分类文本到预设类别，可选 LLM 回退；Dify 可扩展为分类模型。 |
| `agent` | `agent` | native 当前提供 ReAct-Lite：模型用 JSON 决策直接回答或调用已注册 MCP 工具；复杂多 Agent 编排后续独立设计。 |

### 校验规则

`human_intervention` 节点必须包含：

- `prompt`：展示给用户的提示文案，支持 `{{variable}}`。
- `outputVariable`：用户输入写入的变量名，必须是合法标识符。

新增错误码：

- `missing_prompt`
- `missing_output_variable`
- `invalid_human_intervention_output_variable`

若 `prompt` 引用不存在的变量，沿用 `missing_template_variable`。

### Classic 运行器事件

`POST /api/workflow/run` 会在 SSE 第一条发送：

```json
{"event":"workflow_meta","task_id":"...","ttl_seconds":1800}
```

遇到 `human_intervention` 节点时，运行器发送：

```json
{
  "event": "human_intervention_pending",
  "task_id": "...",
  "node_id": "human",
  "node_title": "人工确认",
  "node_type": "human_intervention",
  "prompt": "请确认：...",
  "output_variable": "human_input"
}
```

在等待期间每 15 秒发送一次：

```json
{"event":"heartbeat","task_id":"...","node_id":"human","at":1780000000}
```

前端应消费 heartbeat，但默认不展示到运行日志。

### Resume API

```bash
curl -X POST http://localhost:8000/api/workflow/run/<task_id>/resume \
  -H "Content-Type: application/json" \
  -d "{\"node_id\":\"human\",\"input_text\":\"确认继续\"}"
```

成功响应：

```json
{"ok":true,"task_id":"...","node_id":"human"}
```

任务不存在或 TTL 过期时返回 `404`；当前未暂停时返回 `400`；节点不匹配时返回 `409`。

### Status API

```bash
curl http://localhost:8000/api/workflow/run/<task_id>/status
```

响应：

```json
{
  "task_id": "...",
  "paused": true,
  "paused_node_id": "human",
  "created_at": 1780000000.0,
  "ttl_seconds_left": 1790.0
}
```

### 运行态与回退

- 兼容内存 task state 仍服务于活动连接，持久 execution 保存恢复所需队列、变量和已执行节点。
- SSE 连接断开不会自动取消持久等待；安全事件可通过 `/api/workflow/run/{task_id}/stream?after_sequence=` 重放。
- 审批超时不会自动批准。直接运行可重新打开；Goal/Handoff 转为 `needs_attention`。
- 若出现问题，可从前端隐藏 `human_intervention` 调色板条目，或在后端将 `WORKFLOW_HUMAN_INTERVENTION_ENABLED` 设为 `False` 降级。

## 2026-06-17 增量：问题分类器节点

`question_classifier` 已进入 workflow-native / classic 共享实验线。它对齐 Dify 的问题分类器概念，但保持 MVP 边界：默认仅使用关键词规则，不调用模型；只有用户显式设置 `useLlmFallback=true` 时才尝试一次轻量 LLM 回退。

### 字段

- `inputVariable`：待分类文本变量名。
- `categories`：JSON 字符串，格式为 `{"类别":["关键词1","关键词2"]}`。
- `outputVariable`：分类结果写入变量名。
- `defaultCategory`：规则未命中或异常时写入的默认类别，默认 `未知`。
- `matchMode`：`contains_any` 或 `contains_all`。
- `caseSensitive`：`true` 或 `false`。
- `useLlmFallback`：`true` 或 `false`，默认 `false`。
- `modelId`：启用 LLM 回退时必填。
- `llmFallbackPrompt`：可选回退提示词，支持 `{{variable}}`。

### 安全边界

- LLM 回退默认关闭，常规分类不产生模型调用成本。
- 开启 LLM 回退但未配置 API Key 或 `modelId` 时，运行器会记录 `error` 事件并写入 `defaultCategory`，不会中断工作流。
- `categories` 只接受 JSON 对象和字符串数组，不支持正则、脚本或 DSL。

## 2026-06-17 增量：MCP 工具与时间工具节点

`mcp_tool` 与 `time_tool` 已进入 workflow-native / classic 共享实验线。

- `mcp_tool` 字段：`toolName`、`argumentsJson`、`outputVariable`。运行前需要先在 `/mcps` 连接 MCP Server，工具进入全局注册表后才能被调用。`argumentsJson` 支持 `{{variable}}` 模板，模板替换后必须仍是 JSON 对象。
- `time_tool` 字段：`operation`、`formatString`、`outputVariable`。`operation` 支持 `now_iso`、`now_epoch`、`format`。
- 安全边界：`mcp_tool` 可通过 `WORKFLOW_MCP_TOOL_ENABLED=False` 降级为 no-op；`time_tool` 可通过 `WORKFLOW_TIME_TOOL_ENABLED=False` 降级为 no-op。工具调用失败时写入空字符串并继续后续节点。

`mcp_tool` 当前已通过 Runtime Toolset Capability 调用工具：`MCPToolsetProvider` 作为薄封装复用 `ToolRegistry` 的全局去重列表和 `MCPClientManager.call_tool()` 的会话执行能力，再经由 `MiddlewarePipeline.run_tool_call()` 进入 `wrap_tool_call` 中间件链。这个链路为工具审计、日志、权限与后续聊天 Agent / 多 Agent 复用预留统一入口。

工具调用会记录轻量运行时事件：`tool.call.started`、`tool.call.finished`、`tool.call.failed`。事件只保存工具名、参数数量、输出长度、content types 和错误摘要，不写入完整工具输出，避免泄露敏感内容。

Runtime Toolset 还提供了内存态的 `ToolPermissionPolicy` 与 `InMemoryToolAuditStore`。当前 workflow 默认使用 `allow_by_default=True`，因此不会改变既有 `mcp_tool` 行为；审计记录只保存工具名、状态、耗时、输出长度、content types 与错误摘要。后续可在此基础上扩展用户级权限、持久化审计和 tool preference。

为对齐 Xpert 的“智能体中间件”画布菜单，后端新增了 `server/xpert_runtime/middleware_registry.py` 与只读接口 `GET /api/runtime/middleware-nodes`。当前 registry 先暴露 5 个可拖拽元数据节点：`system_prompt_injector`、`event_recorder`、`tool_policy`、`tool_audit`、`mcp_tools`。本轮只提供 schema 与 metadata；下一步前端 `NodePalette` 可从该接口拉取分组、字段、图标和搜索内容，渲染“智能体中间件”拖拽菜单。再下一步才会把拖入画布的 `runtime_middleware.xxx` 节点接入 workflow validate 和 runner。

前端 `NodePalette` 已新增“智能体中间件”分组，并从 `/api/runtime/middleware-nodes` 拉取 metadata 渲染内置 middleware 节点。中间件拖拽 payload 使用 JSON 字符串，包含 `kind="runtime_middleware"`、`runtimeMiddlewareId`、`runtimeMiddlewareKind`、`fields` 与 `metadata`；下一步 `WorkflowEditor` 会解析该 payload 并生成可配置的 `runtime_middleware` 节点，再后续接入 NodeConfig 字段表单与 runner 语义。

### 运行时中间件节点（Runtime Middleware Node）

`runtime_middleware` 当前是可视化 + 渐进执行阶段：前端支持从 `NodePalette` 拖拽“智能体中间件”节点到画布，右侧配置面板会根据 `RuntimeMiddlewareField` 动态渲染 `text`、`textarea`、`boolean`、`number`、`select`、`json` 六类基础字段。后端 validate 已最小支持 `runtimeMiddlewareId` 与 `runtimeMiddlewareKind`，classic `workflow_stream` 会为中间件节点发出 `node_delta`，并按已支持的 middleware id 逐步启用真实效果。

`system_prompt_injector` 已具备最小真实执行：节点读取 `runtimeMiddlewareConfig.system_prompt`，先用当前 workflow 变量渲染 `{{variable}}` 模板，再写入运行态上下文；后续 `llm` 节点调用模型时会 prepend 一条 `system` message。若同一条路径上出现多个系统提示词注入器，后执行的节点覆盖前一个。`mcp_tools` 等中间件节点仍保持 no-op 原型，后续再接入 `MiddlewarePipeline` 的真实编排能力。

`tool_policy` 已进入最小真实执行：节点读取 `runtimeMiddlewareConfig.denied_tools`、`allowed_tools` 与 `allow_by_default`，支持换行或逗号分隔工具名，并创建 `ToolPermissionPolicy` 写入 `workflow_runtime_context`。后续 `mcp_tool` 节点优先使用 workflow 级 policy；无 `tool_policy` 节点时回退全局 `workflow_tool_policy`（默认 `allow_by_default=True`）。当 `denied_tools` 命中或 `allow_by_default=False` 且工具不在白名单时，`run_tool_with_runtime` 会抛出 `RuntimeToolError(code="tool_denied")`，classic workflow 记录 error event、写入空输出并继续后续节点。当前作用范围仅 classic workflow 的 `mcp_tool`，不做持久化权限系统或用户级/workspace 级权限。

`event_recorder` 已进入最小真实可见状态：classic workflow 每个 task 会创建独立 `RuntimeEventStore`，`mcp_tool` 节点通过 `MiddlewareContext.store` 将该 store 传入 `MiddlewarePipeline`，因此 `event_recorder.wrap_tool_call` 会记录 `tool.call.started`、`tool.call.finished`、`tool.call.failed`。事件按 `task_id` 隔离，并可通过 `GET /api/workflow/runtime-events/{task_id}` 查询；前端 `WorkflowRun` 的“运行观测”折叠区会展示事件类型、severity、工具名、输出长度和错误摘要。

`tool_audit` 当前是原型可见状态：每个 workflow task 默认拥有独立 `InMemoryToolAuditStore`，工具调用会记录 `tool_name`、`status`、`started_at`、`finished_at`、`duration_ms`、`output_length`、`content_types` 与 `error`。`runtime_middleware.tool_audit` 节点可读取 `runtimeMiddlewareConfig.max_records`，为本次运行重建指定上限的审计 store；观测 API 返回当前 task 的审计记录。该能力仍为内存态，后续再扩展 per-user/per-workspace 过滤、持久化审计与图形化 trace。

### Classic 工作流布局优化

`/workflow` 当前改为“画布 + 单一右侧工作台”的主布局：节点库不再作为常驻左侧长栏，而是在画布标题区提供“节点库”下拉浮层，拖入节点后自动收起；右侧工作台用 `配置 / 运行` tabs 承载 `NodeConfig` 与 `WorkflowRun`。该调整只改变布局，不改变节点数据结构、React Flow 拖拽协议、SSE 运行协议或后端执行逻辑。目标是让节点库、画布和运行结果集中在同一视野附近，避免窄屏或 Docker 本地验收时出现左侧节点库与右侧运行区纵向堆叠、需要大幅下滑的问题。

### Agent Task Runtime（Xpert 对齐）

当前为最小底座原型阶段，主线对齐 Xpert 的 Agent/Handoff/RunRegistry 思路，并在 ModelMirror 内原生实现为 `server/xpert_runtime/agent_tasks.py`。源码策略是“参考协议与分层，原生改写实现”：不迁移 Xpert 的 Nx/NestJS/Angular 主框架，不整文件复制上游源码，也不引入不兼容协议代码。

Agent Task Runtime 包含三层：

- `AgentTask`：任务实体，包含 `title`、`input`、`status`、`result`、`error`、`source_agent`、`assigned_agent`、`metadata` 与时间戳。
- `AgentHandoff`：Agent 间任务移交记录，包含 `source_agent`、`target_agent`、`reason`、`status` 与 metadata。
- `AgentTaskStore`：默认内存态、生产可选原子 JSON 持久化，支持任务更新、Handoff lease、重试等待、死信、重新入队和终态等待，并把状态事件写入 `RuntimeEventStore`。

后端开放任务与 Handoff 创建、查询、人工状态变更、立即执行、执行器状态和重新入队 API。人工状态仍遵循 `pending -> accepted/rejected` 与 `accepted -> completed`；自动路径增加 `retry_wait` 和 `dead_letter`。显式 Xpert 目标会执行不可变发布快照并回写结果。当前不接数据库或 Redis/Celery，多进程一致性留待后续持久化调度器。

classic workflow 已新增 `agent_task` 节点，作为 Xpert Agent/Handoff 对齐的第一步闭环。前端可从节点调色板拖入“智能体任务”，配置 `taskTitle`、`taskInput`、`assignedAgent` 与 `outputVariable`；运行时会渲染 `{{变量}}` 模板，调用 `AgentTaskStore.create_task(...)` 创建一条 AgentTask，并将新任务的 `task_id` 写入 `outputVariable`。该节点当前只负责创建任务和输出 ID，不做真实队列分派、专家协作或任务执行；完整任务详情继续通过现有 Agent Task API 查询。

classic workflow 已新增 `workflow_agent` 节点，作为 Xpert Workflow Agent 的最小执行闭环。前端可从节点调色板拖入“工作流智能体”，配置 `agentName`、`modelId`、`rolePrompt`、`taskInput`、`toolMode`、`agentStrategy`、`toolNames`、`maxIterations`、`parallelToolCalls`、`promptSuffix` 与 `outputVariable`；运行时会先渲染 `{{变量}}`，再以 `rolePrompt` 作为该节点 system prompt、`taskInput` 作为用户输入执行。

`toolMode=none` 时，节点保持直接回答。`toolMode=mcp_tools` 时，V2 默认先发送 OpenAI 兼容的 `tools`、`tool_choice` 与 `parallel_tool_calls`；强制 `react` 时使用 `Thought → Action JSON → Observation → FinalAnswer`，隐藏 Thought，只输出动作级 `node_delta`。所有真实工具调用继续经过 `run_tool_with_runtime`、Toolset、Middleware、权限策略和审计；checkpoint 只保存脱敏参数摘要、结果预览、耗时、调用 ID、策略和 token usage。关闭 V2 开关后恢复既有 ReAct-Lite JSON 决策协议。

首版 V2 只接管已注册 MCP 工具。绑定 Knowledge/Toolset/Browser/Client/Office/Sandbox、Todo、Automation、Authoring 或交互式 HITL 的 `workflow_agent` 继续走可恢复的 ReAct-Lite 路径，避免破坏现有暂停/恢复和幂等语义；这些能力完成 V2 transcript checkpoint 后再迁移。

Knowledge Agent 与其他 Runtime 工具复用同一共享策略运行时，不新增第二套 Agent runner。`knowledgeReadEnabled` / `knowledgeWriteEnabled` 仅对 `workflow_agent` 生效，且要求 `toolMode=mcp_tools` 与 1 至 5 个 `knowledgeBaseIds`。`toolNames` 继续只过滤 MCP 工具；Memory/Knowledge capability 分别由节点开关控制。Knowledge 工具仍经过 runtime middleware、policy、audit 和 checkpoint。模型只能提出写入，正式审批和候选构建统一由 `/rag/:kbId/inbox` 完成。

V2 当前只处理文本消息与文本 observation。既有文本历史可由 middleware 传入，但不扩展公开 `ChatMessage`；不会把独立 RAG context、图片、文件或二进制工具结果直接透传给策略模型。非文本工具结果只记录类型和安全摘要，完整多模态透传留待后续。

## 2026-06-17 增量：Agent 节点

`agent` 已进入 workflow-native / classic 共享实验线。工具模式复用 Agent Strategy V2；这是基于 Dify Agent 0.0.42 行为协议的独立适配实现，不引入 `dify_plugin`，也不逐行复制 SDK 代码。

### 字段

- `agentMode`：`tool_first` 或 `direct`。默认 `tool_first`。
- `agentStrategy`：`auto`、`function_calling` 或 `react`；缺失时按 `auto`。
- `instruction`：任务指令，支持 `{{variable}}` 模板。
- `modelId`：调用模型 ID。
- `toolNames`：可选，逗号分隔的工具白名单；留空代表全部已注册工具。
- `outputVariable`：Agent 最终输出变量。
- `maxIterations`：工具循环上限，默认 5，运行器最多允许 20。
- `temperature`：模型温度，范围 0-2。
- `promptSuffix`：可选补充提示词，支持 `{{variable}}` 模板。
- `parallelToolCalls`：仅 Function Calling 使用；ReAct 模式在 UI 中禁用。

### 安全边界

- `agent` 可通过 `WORKFLOW_AGENT_ENABLED=False` 降级为 no-op；V2 可通过 `WORKFLOW_AGENT_STRATEGY_V2_ENABLED=false` 回退 ReAct-Lite。
- `tool_first` 模式依赖 `/mcps` 已连接的 MCP Server；没有可用工具时会切换到直接回答。
- 未配置 API Key、模型调用失败或工具调用失败时，运行器发出 `error` 事件并写入空字符串，不中断后续节点。
- 一旦尝试真实工具调用，自动策略回退、整轮重试和备用模型切换均被禁止，避免重复外部副作用。
- 当前不扩展 `/api/chat`、Dify 代理或 `agent_task`，也不实现图片/文件二进制工具结果透传。

## 回退方案

如果 native 实验页影响体验：

1. 从 `client/src/App.tsx` 移除 `/workflow-native` 路由。
2. 从 `client/src/data/studio.ts` 移除实验卡片。
3. 后端可以保留 `/api/workflow-native/validate`，因为它不会影响稳定路径。
4. `/workflow`、`/workflow/classic`、`/rag` 不需要变更。

## 2026-07-08 增量：Handoff Router 工作流节点

Classic workflow 新增 handoff_router 节点，作为 workflow_agent -> Handoff Inbox 的人工可控自动编排雏形。节点字段包括 sourceVariable、taskTitle、targetAgent、sourceAgent、reasonTemplate 与 outputVariable。

运行时会读取 sourceVariable 的完整文本作为 AgentTask input，渲染 taskTitle 与 reasonTemplate，调用 AgentTaskStore.create_task(...) 创建任务，再调用 create_handoff(...) 创建 pending Handoff，并将 handoff_id 写入 outputVariable。节点会同步登记 agent_task 与 agent_handoff 子 run，并写入 checkpoint，供运行观测和 MetaAgent Handoff Inbox 查看。

当前边界：人工目标仍停留在 pending Inbox；显式 `xpert:` 目标可自动执行、等待结果、重试和进入死信。当前不做分布式队列、权限系统或多进程 worker。

## 2026-07-08 增量：Chat Runtime Toolset

聊天入口新增默认关闭的 Runtime 工具模式。旧请求仍走普通 `/api/chat` 流式上游路径；只有请求显式传入 `tool_mode=mcp_tools` 时，后端才要求模型输出轻量 JSON 决策：`{"tool":"工具名","arguments":{...}}` 或 `{"answer":"最终答案"}`。

工具调用统一经过 `MCPToolsetProvider`、`run_tool_with_runtime` 与 `MiddlewarePipeline.wrap_tool_call`，因此现有 `tool_policy`、`tool_audit` 和 `event_recorder` 可复用到聊天工具循环。`tool_names` 提供逗号或换行分隔的白名单，留空代表允许当前已注册 MCP 工具；`max_tool_iterations` 限制为 1-20，避免无限工具循环。

本轮补齐最小运行观测：工具模式请求会在内存态 RunRegistry 中创建 `chat` run，并通过响应 header 暴露 run/task id。后端新增 `GET /api/chat/runtime-events/{task_id}` 返回本次聊天的 runtime events 与 per-chat audit 摘要；前端“Runtime 工具模式 Beta”区域展示 run 状态、checkpoint、tool event 和审计摘要。

当前边界：不是 OpenAI function calling，不自动创建 Handoff，不接真实多 Agent 调度，也不保存完整 prompt、工具输出、模型回答或 API key 到运行元数据中。

最后更新日期：2026-07-23

## 长期 Goal 调度（2026-07-10）

`XPERT-CONVERSATION-GOAL-01` 不新增工作流节点。GoalCoordinator 负责依赖图与生命周期，步骤执行继续复用现有 AgentTask、`xpert_auto` Handoff、HandoffExecutor 和已发布 Xpert 的 classic workflow runner。

- Planner 输出必须经过服务端 DAG 校验和人工审核。
- 单 Goal 默认最多并发两个 ready 步骤。
- pause 只停止新派发；cancel 不强制中断已运行节点。
- 步骤失败进入 `needs_attention`，人工可重试、改派或跳过非最终步骤。
- 运行层级以 `goal` run 为根，checkpoint 不保存完整 prompt、模型输出、工具结果或密钥。

因此 classic `/workflow` 的节点数据、validate API、拖拽协议和 SSE wire format 均保持不变。详细契约见 `docs/XPERT_GOALS.md`。

## 2026-07-08 增量：Knowledge Citation 工作流节点

Classic workflow 新增 `knowledge_citation` 节点，用于把本地 RAG Knowledge Pipeline 的 `CitationAnchor` 变成可拖拽、可配置、可运行、可观测的工作流能力。前端字段为 `queryVariable`、`knowledgeBaseId`、`top_k`、`outputVariable`；`knowledgeBaseId` 留空时使用第一个知识库，`top_k` 静态校验范围为 1-10。

运行时读取 `variables[queryVariable]` 作为检索问题，调用 `RagService.create_pipeline_citations(kb_id, query_text, top_k=...)`，并将输出变量写成 JSON 字符串：

```json
{"citations":[{"chunk_id":"...","document_name":"...","score":0.91,"snippet":"..."}],"citation_count":1}
```

节点会登记 `knowledge_citation` 子 run，`parent_run_id` 指向 workflow run，并写入 `knowledge_citation.started/completed/failed` checkpoint。metadata 只保存知识库 ID、变量名、输出变量、引用数量等摘要，不返回本地文件路径、embedding、完整上传文件内容或密钥。该节点与既有 `knowledge_retrieval` 并存，不改变 `/api/rag/query`、聊天 RAG 或向量库行为。

> 2026-07-10 Knowledge Pipeline draft config: `/rag` Pipeline Draft now supports safe saved config and preflight observation. Classic workflow `knowledge_citation` is unchanged; it still reads CitationAnchor summary JSON and does not execute draft config.

## 2026-07-11 Xpert File and Memory Runtime

The Xpert Studio fields `enableFileUnderstanding`, `memoryReadEnabled`, `memoryReadScope`, `memoryWriteEnabled`, and `memoryWriteTarget` now affect `workflow_agent` execution for published Xpert runs.

- A normal local `/workflow` node still defaults these capabilities off.
- Published Xpert files are injected only when the run explicitly selects file asset IDs and the node enables file understanding.
- Memory recall is limited by scope and context budget. Model writes create approval candidates rather than immediately mutating durable memory.
- In `toolMode=mcp_tools`, memory tools and MCP tools share Runtime Toolset middleware, policy, and audit. The MCP whitelist only filters MCP tools.
- Goal/Handoff propagation carries explicit file references. It does not copy private conversation memory between Xperts.
- No workflow node, SSE wire type, RAG index, or `knowledge_citation` execution contract is changed by this milestone.

## 2026-07-16 Agent 级 Runtime Middleware

`runtime_middleware -> workflow_agent` 现在支持 `sourceHandle="middleware-binding" -> targetHandle="middleware"` 专用绑定边。绑定节点不参与控制流拓扑排序、变量可达性、节点调度或独立执行；同一 middleware 只能绑定一个 Agent，且不能混用普通控制边。旧的线性 middleware 节点继续保持原行为。

每个 `workflow_agent` 会按 `middlewarePriority` 和节点 ID 编译独立 pipeline。`context_compression`、`structured_output`、`todo_planner` 和 `llm_tool_selector` 已进入真实执行：模型直答与 ReAct 决策均运行模型 hooks，工具继续走 Runtime Toolset、policy 和 audit。结构化输出仍复用既有 SSE，仅替换最终正文为 schema-valid JSON；Todo 按 conversation/goal/handoff/workflow 隔离，公共 App 只使用临时 run 作用域。

节点库浮层、普通拖拽 payload、右侧 `配置 / 运行` tabs 和已有节点协议保持兼容。完整配置、顺序和安全边界见 `docs/XPERT_MIDDLEWARE.md`。

## 2026-07-16 可恢复 HITL Runtime Middleware

绑定到 `workflow_agent` 的 `human_in_the_loop` 已进入真实执行。工具审批发生在 allowlist / tool policy 之后和 audit / Provider 之前；支持批准、参数编辑与拒绝，编辑参数会再次通过 schema 和 policy 校验。最终答案可批准、人工替换或要求模型修订。

`RuntimeInterrupt` 不属于普通可降级异常，任何审批中断或审批存储错误都不得触发工具 fallback。runner 会持久化变量、队列、已执行节点、ReAct 消息和轮次，由 `ApprovalCoordinator` 使用 lease 恢复；容器重启后不重跑已完成节点，也不重复调用已批准工具。

新增 `runtime_approval_pending / runtime_approval_resolved` 兼容 SSE 事件和安全事件重放接口。GoalStep、AgentTask、Handoff 支持 `waiting_approval`；过期转 `needs_attention`。公开 Xpert App 部署预检拒绝两类交互式 HITL，普通 Workflow、私有 Xpert Chat、Goal 与 Handoff 保持可用。

## 2026-07-16 隔离 Sandbox / Skill Runtime Middleware

`sandbox_files`、`sandbox_shell` 与 `skills_runtime` 已可绑定到 `workflow_agent`。绑定只增加目标 Agent 的 Runtime 工具，不参与控制流；`toolMode=none` 配合 Sandbox 时不会隐式注册 MCP 工具。工作区按 conversation、goal/step、handoff 或 workflow task/node 隔离，Xpert Chat 的显式附件会复制到 `inputs/`。

实际文件与命令执行位于完全断网的 Docker sidecar。命令只接受 argv、固定白名单和有限超时；路径必须留在当前 workspace，副作用通过 operation ID 幂等。`sandbox_shell.require_approval=true` 时，静态校验和运行时均要求同一 Agent 的 HITL 覆盖该工具。公开 Xpert App/API 部署拒绝 Sandbox/Skill 中间件。详细边界见 `docs/XPERT_SANDBOX.md`。

## 2026-07-18 Automation Runtime Middleware

绑定到 `workflow_agent` 的 `scheduler` 提供作用域受限的 Automation Runtime 工具；它不改变控制流拓扑。自动化定义固定已发布 XpertVersion，支持单次、间隔和带时区五字段 Cron，并通过 occurrence ID、lease、重叠/误触发策略、预算、重试和死信持久执行。HITL 或 Client Tool 只会暂停当前 execution，解决后继续原执行。

`ralph_loop` 在节点最终输出提交前运行有界改进/验证循环，失败仍进入节点原有 retry、fallback 与 `exceptionHandling`。`knowledge_writer` 只创建 Knowledge Inbox pending proposal，不能直接写活动索引。`plugin_hooks` 只在无网 Sandbox 运行已安装 Skill 的显式 Hook manifest。四类中间件均不改变 SSE wire format；公开 Xpert App 部署会被预检阻断。完整边界见 `docs/XPERT_AUTOMATION.md`。

## 2026-07-18 Xpert / Skill Authoring Middleware

`xpert_authoring` 与 `skill_creator` 使用 Agent middleware binding，不参与控制流拓扑。两者要求 `toolMode=mcp_tools`，并从节点配置编译允许创建/更新的动作与目标 ID 范围。工具只创建或校验 proposal，不能调用 Xpert publish 或 Skill install；管理端批准后也只写草稿层。

中间件 registry 的 `config_version / execution_status / requires_tool_mode / app_policy / security_category` 是校验与部署预检共享的安全契约。公开 App 对 `app_policy=forbidden` 的中间件 fail-closed。详细状态机、revision、Skill 包白名单与 API 见 `docs/XPERT_AUTHORING.md`。

## 2026-07-22 Data X Indicators Middleware

`datax_indicators` 通过 middleware binding 绑定 `workflow_agent`，不新增控制流节点或第二套 runner。配置固定项目、语义模型、结果行数和 proposal 权限；运行工具继续经过选择器、policy、HITL、audit 与 checkpoint。Workflow、已发布 Xpert、Goal、Handoff 和 Automation 自动继承该语义，SSE wire format 不变。

画布节点现在可以通过选中后的删除按钮或 Delete/Backspace 删除，关联边同步清理。classic `/workflow/:id` 草稿（包括 MetaAgent 导入草稿）可显式转为服务端 Xpert 草稿，转换后进入同一 Xpert Studio 编辑和发布路径。

Data X 的数据模型、受限查询 DSL、提案/发布边界和 App 策略见 `docs/XPERT_DATAX.md`。
