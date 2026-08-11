# Xpert 对齐总纲

> **状态：冻结后的历史增量记录。** 当前维护边界以
> [XPERT_FREEZE.md](./XPERT_FREEZE.md) 为准。本文保留 `Xpert` 内部契约名和
> 当时用语，但用户界面统一使用“智能体”“Agent Studio”和“Agent App”。

## 2026-07-25 当前路线：Xpert 正式冻结

Xpert 功能扩张已在
`main@93e5cc38becc7fe4f89efa113310698e6eda1971` 正式冻结。本页保留此前的
逐轮增量作为历史记录，但其中按当时上下文写下的“下一步”不再代表当前优先级。

当前唯一权威状态与维护边界见：

- [XPERT_FREEZE.md](./XPERT_FREEZE.md)：稳定能力、维护项、延期项、不采用项和回归入口。
- [EVOAGENTX_ALIGNMENT.md](./EVOAGENTX_ALIGNMENT.md)：冻结后的功能主线。
- [EVOAGENTX_AUDIT_V014.md](./EVOAGENTX_AUDIT_V014.md)：官方
  `EvoAgentX v0.1.4` 的逐模块复用审计。

冻结后只接受安全修复、致命缺陷、数据兼容和既有闭环回归。GraphRAG、企业权限、
多租户、远程市场、动态插件、更多 Provider 和 UI 像素对齐均进入延期清单。
下一项功能增量固定为 `EVOAGENTX-META-PLANNER-01`。

知识能力的冻结后维护增量已补齐可解释 Strategy Router 与 Benchmark Auto Tuner。
它们复用既有 Pipeline、Evaluation Gate 和 Promotion，不改变 Xpert Runtime 协议：Router
只更新草稿；Tuner 只生成 `promotion_required` 候选，活动索引仍由人工显式推广。完整
执行契约见 [RAG_INTEGRATION.md](./RAG_INTEGRATION.md)。

## 2026-07-23 增量：XPERT-PLUGIN-PROMPT-03

Xpert 冻结前最后一轮功能增量已进入真实闭环：

- `/prompts` 支持 Prompt Profile 草稿、revision、模板预览、不可变版本、归档和全局唯一命令别名。Xpert 发布会把 `latest` 固定为具体版本。
- 已发布 Xpert Chat 支持 `/alias args`，保留原始用户命令并只向模型注入渲染后的当前任务；未知命令不会调用模型，`//text` 可转义为普通 `/text`。
- `/plugins` 支持导入、校验和发布可信本地声明式 ZIP。Plugin 可聚合 Prompt、命名空间化 Skill、固定 Toolset 引用和已注册中间件预设，但不能加载动态后端代码或初始化脚本。
- `plugin_resource -> workflow_agent` 使用专用非控制绑定边。运行时将固定 Plugin 版本编译到目标 Agent 的 Toolset、Skill、Sandbox Hook、中间件与私有命令；别名、工具或中间件冲突会阻断发布。
- 公共 App 仅允许直接绑定且显式安全的 Prompt Command，manifest 隐藏模板；包含 Plugin 资源的 Xpert 仍 fail-closed。

该轮之后的 `XPERT-ALIGNMENT-FREEZE-04` 与
`EVOAGENTX-ALIGNMENT-AUDIT-01` 已完成；此处保留为历史交付记录。远程插件市场、
动态代码插件、组织权限、计费与企业治理不再继续扩张。

## 2026-07-23 增量：XPERT-AGENT-FEATURES-02D

Toolset 收尾路线的 Agent Features 已进入真实执行闭环：

- Xpert 草稿与不可变发布版本现固定开场白、starter questions、问题建议、会话标题、会话摘要、记忆回复、文件上传和 TTS/STT 配置；草稿修改不影响既有发布版本。
- Xpert Chat 按当前发布版本展示开场白与建议问题，模型回答后可生成下一轮问题建议，并在首轮回答后生成会话标题。生成失败均 fail-soft，不影响主回答。
- 会话摘要复用 `context_compression`，只对输出 Agent 编译隐式中间件；原消息不被改写，摘要 revision 和覆盖边界继续持久化。
- 文件能力按版本限制开关、扩展名与单轮数量。附件仍属于会话，关闭文件能力时不会注入运行或 Goal，也不能误触候选知识构建。
- 高置信记忆回复只使用当前 Xpert/会话允许访问的受限记忆；命中不足时继续正常模型执行，不把低置信结果伪装成回答。
- TTS/STT 显式选择模型注册表中的 speech/transcription 模型，通过现有 OpenAI 兼容网关调用；不硬编码供应商 SDK，网关未配置时返回可操作错误。
- `XpertAgentConfig.max_concurrency` 与 `recursion_limit` 是整个 Xpert 执行树的全局预算；`maxToolConcurrency`、`maxToolCalls`、`maxToolDepth` 和 `maxIterations` 仍是单个 Agent 工具循环的局部护栏。

该轮之后的 `XPERT-PLUGIN-PROMPT-03` 已完成；剩余 Xpert 工作只做冻结审计。企业 Provider 治理、远程市场和任意 Code Toolset 继续延期。

## 2026-07-23 增量：XPERT-TOOLSET-SEMANTICS-02C

Toolset 已从“可连接、可发布”推进到真实影响 Agent 行为的统一工具语义：

- `ToolDefinition`、不可变 `ToolsetVersion` 与 `RuntimeTool` 统一固定 `sensitive`、`terminal`、`memory_mode`、`parallel_safe` 和 `public_app_allowed`，旧版本按保守默认值加载。
- `/toolsets` 的内置 Provider 已可创建 Tavily 与 Todo 实例。Tavily 使用加密 Credential 与受限 HTTP 调用；Todo 直接复用 `RuntimeTodoStore`，不复制状态层。Knowledge、Memory 和 Data X 继续复用原 Provider，并暴露同一语义元数据。
- `workflow_agent` 支持有界并行只读工具调用，以及 `maxToolConcurrency`、`maxToolCalls`、`maxToolDepth` 和既有 `maxIterations` 四层预算。并行批次包含敏感、终点、写入或非并行安全工具时整批拒绝，结果按模型决策顺序稳定返回。
- 敏感工具必须由同一 Agent 的 HITL 覆盖；`terminal` 工具成功后直接结束 ReAct；run 级 Tool Memory 只在当前执行生效，conversation 级只在私有 Xpert 会话持久化并可查看/清除。
- 公共 App 不再绝对禁止 `toolset_resource`。只有固定已发布版本、全部工具只读、非敏感、显式 `public_app_allowed`、无 conversation memory 且绑定 Tool Policy 时才能部署；公共调用强制使用 run 级记忆。

下一轮进入 `XPERT-AGENT-FEATURES-02D`，补齐开场白、问题建议、标题/摘要、文件能力收口和显式 TTS/STT。远程市场、任意 Code Toolset、企业 Provider 治理和浏览器 OAuth 继续延期。

## 2026-07-23 增量：XPERT-TOOLSET-API-02B

Toolset Runtime 已从 MCP 扩展到可发布的 API Toolset：

- OpenAPI 3.0/3.1 支持 JSON、YAML、文件和受控 URL 导入，按 operation 编译参数 JSON Schema、HTTP 方法、路径与请求体。
- OData v4 支持 CSDL metadata 导入，首批编译 EntitySet 的受控查询、按键读取和创建操作，以及可安全表达的 Action/Function。
- API Key、Bearer、Basic 与 OAuth2 client credentials 全部只引用加密 Credential ID；定义、版本和普通 API 不保存明文。
- HTTP 执行器默认阻断回环、私网、云元数据、URL credentials 和跨域重定向，限制超时、重定向与响应大小，不接受任意 URL 或任意请求模板。
- 写操作在管理测试中要求显式二次确认；发布 Xpert 时要求目标 Agent 的 `human_in_the_loop` 覆盖所有变更工具，运行时再次 fail-closed 检查。
- OpenAPI/OData 草稿导入与刷新不会改变已发布 Toolset 或已发布 Xpert；远端移除操作、新增必填参数或改变方法/路径会记录 breaking drift。

当前明确延期：远程 `$ref`、multipart 上传、浏览器 OAuth flow、OData `$batch` 与通用 API 脚本。公共 App Toolset 已在 02C 通过逐工具安全语义受控开放。

## 2026-07-23 增量：XPERT-TOOLSET-MCP-02A

Toolset 收尾路线已从“命名白名单打包”修正为完整运行闭环：

1. `XPERT-TOOLSET-MCP-02A`：本轮已实现 MCP Toolset 草稿、加密凭据、Stdio / Streamable HTTP / 旧 SSE 连接、工具发现与 Schema 配置、受策略约束的测试调用、不可变版本发布和 Agent 资源绑定。
2. `XPERT-TOOLSET-API-02B`：已实现 OpenAPI 3.x 与 OData v4 导入、加密鉴权、受控 HTTP 执行、Schema 漂移与写操作 HITL 门禁。
3. `XPERT-TOOLSET-SEMANTICS-02C`：已实现内置 Provider、工具敏感/终点/记忆语义、受限并行调用、并发和递归预算。
4. `XPERT-AGENT-FEATURES-02D`：最后收口开场白、问题建议、标题/摘要、文件与记忆能力和显式音频模型。

`toolset_resource -> workflow_agent` 使用 `targetHandle="toolset"`，不进入控制流、变量传播或节点调度。Xpert 发布会把 `latest` 解析为具体 Toolset 版本；运行时只暴露该快照中启用的工具，Schema 发生不兼容漂移时拒绝调用。公开 App 只允许逐工具显式标记为安全的固定版本只读能力。

## 2026-07-22 路线切换：资源节点收尾与 EvoAgentX 主线

Xpert 对齐进入收尾阶段。当前优先补齐真实画布语义，而不是继续扩展企业治理、远程市场或运维目录：

1. `XPERT-RESOURCE-NODES-01`：新增 `external_xpert` 与 `knowledge_base` 资源节点，通过 `expert` / `knowledge` 特殊绑定边向单个 `workflow_agent` 提供同步协作者工具和限定知识工具。绑定边不参与控制流、变量传播或节点调度；外部 Xpert 在发布时固定不可变版本，知识库继续消费活动索引。
2. `XPERT-TOOLSET-RUNTIME-02`：按 MCP、API、工具语义、Agent Features 四轮补齐完整 Toolset；MCP 02A 已进入真实运行闭环。
3. `XPERT-PLUGIN-PROMPT-03`：交付可信本地插件清单与版本化 Prompt Profile 的编辑、发布、Agent 绑定和斜杠命令最小闭环，不建设远程市场、计费或组织权限。
4. `XPERT-ALIGNMENT-FREEZE-04`：已完成；冻结清单见
   [XPERT_FREEZE.md](./XPERT_FREEZE.md)。
5. `EVOAGENTX-ALIGNMENT-AUDIT-01`：已完成；官方来源、许可证与模块判定见
   [EVOAGENTX_AUDIT_V014.md](./EVOAGENTX_AUDIT_V014.md)。

资源绑定与异步协作保持两条清晰路径：`external_xpert` 是 Agent 在 ReAct 中同步调用的固定版本协作者；`agent_handoff` / `handoff_router` 仍是任务移交、后台执行、等待和人工接管机制。公开 Xpert App 第一版禁止外部专家资源，知识资源继续受只读策略约束。

## 2026-07-22 增量：XPERT-DATAX-INDICATORS-10

Data X 已从资源占位进入可运行闭环：CSV、XLSX 与 Parquet 固定为不可变快照并导入项目隔离 DuckDB；用户可以建立 1–5 实体的受限语义模型，定义基础/派生指标，预览后发布不可变版本，并通过 KPI、表格、折线和柱状结果消费。

`datax_indicators` 中间件为 `workflow_agent` 提供作用域、模型上下文、维度成员、指标检索、查询、展示和提案工具。所有查询由受限 DSL 编译，Agent 只能提出指标草稿；审批不等于发布。Workflow、Xpert Chat、Goal、Handoff 与 Automation 复用同一路径。公开 App 只有显式开启 `allow_datax_read` 后可只读固定 scope 内的已发布指标，任何提案能力均被部署预检和运行时双重阻断。

本轮同时修复 classic 画布节点删除和“工作流草稿转 Xpert 草稿”入口。完整契约见 `docs/XPERT_DATAX.md`。

## 2026-07-19 增量：XPERT-MIDDLEWARE-OFFICE-09

私有 Workflow、Xpert Chat、Goal、Handoff 与 Automation 已具备 Office 当前文档实时自动化闭环。Word、Excel 和 PowerPoint 共 22 个工具复用 Client Tool 的配对、持久等待、HITL、Audit 与断点恢复；读取可安全重派，修改断线进入 `uncertain` 并禁止自动重放。可选 `office-host` Compose profile 提供受信任 localhost HTTPS Task Pane 和 add-in-only XML manifest。

该能力要求用户主动绑定当前文档，Host 类型、Requirement Set 与 schema hash 必须匹配；所有修改工具均需 HITL，删除另有配置和 `confirm=true` 双门禁。公开 Xpert App/API fail-closed。完整边界见 `docs/XPERT_OFFICE_AUTOMATION.md`；其后的 Data X 指标闭环已由本页最新增量完成。

## 2026-07-19 增量：XPERT-MIDDLEWARE-FILE-MEMORY-08

Xpert 级长期 Memory 已升级为类型化 Markdown 文件记忆：`MEMORY.md` 摘要索引与 `user / feedback / project / reference` 四类正文通过原子 Store 持久化，旧 Xpert Memory 在首次访问时兼容懒迁移并保留 ID；会话级 Memory 继续保持原隔离存储。

`xpert_file_memory` 现可绑定到 `workflow_agent`，按索引、摘要 digest 和少量正文三层召回，并使用模型选择器或确定性 fallback。自动整理仅创建带 revision 的 create/update 候选，人工批准后才写入；冲突不得覆盖人工修改。Goal 与 Handoff 只读取目标 Xpert 自身记忆，公开 App 仅在显式策略允许时只读且永不写回。完整契约见 `docs/XPERT_FILE_MEMORY.md`。

## 2026-07-18 增量：XPERT-MIDDLEWARE-CONSOLIDATION-07

私有 Workflow、Xpert Chat、Goal、Handoff 与 Automation 已具备受审批的平台自编写闭环。`xpert_authoring` 和 `skill_creator` 通过 Runtime Toolset 分析允许范围内的资源，只能创建版本化提案；管理端审核通过后分别写入 Xpert 草稿或 Workspace Skill 草稿，不能直接发布 Xpert、安装 Skill 或修改不可变发布版本。

提案与 Skill 草稿采用文件型 Store、原子写入、revision 和 `base_revision` 冲突保护。Skill 包限制在 `SKILL.md`、`scripts/`、`references/`、`assets/` 与 `agents/openai.yaml`，安装仍是 `/skills` 的显式二次操作，脚本继续只在隔离 Sandbox 中执行。公开 App/API 通过统一中间件契约禁止两类自编写能力。该轮延后的显式文件记忆、Office 实时自动化与 Data X 指标闭环现均已完成。详细契约见 `docs/XPERT_AUTHORING.md` 与 `docs/XPERT_DATAX.md`。

## 2026-07-18 增量：XPERT-MIDDLEWARE-AUTOMATION-06

私有 Workflow、Xpert Chat、Goal 与 Handoff 已进入可靠自动化闭环：已发布 Xpert 可以按单次、间隔或带时区五字段 Cron 固定版本执行，并具备 occurrence 幂等、重叠/误触发策略、运行预算、lease、重试和死信。`/agents/automations` 提供创建、暂停、恢复、立即运行和 execution 处理入口；HITL 与 Client Tool 等待解决后会继续同一自动化 execution。

本轮同时交付 `scheduler`、`ralph_loop`、`knowledge_writer` 和 `plugin_hooks` 四个真实 Agent 中间件。知识写入仍只创建待审批 proposal；Skill Hooks 仅在无网 Sandbox 执行显式 manifest；公开 App/API 拒绝这些私有自动化能力。其后的 `XPERT-MIDDLEWARE-CONSOLIDATION-07` 已完成 Xpert/Skill 自编写与安全契约收口。详细契约见 `docs/XPERT_AUTOMATION.md` 与 `docs/XPERT_AUTHORING.md`。

## 2026-07-18 增量：XPERT-MIDDLEWARE-CLIENT-05

私有 Workflow、Xpert Chat、Goal 与 Handoff 已具备客户端宿主闭环：`workflow_agent` 可通过 `client_tools` 请求用户已配对 Chrome 在主动绑定的当前标签页执行 snapshot、read、受审批交互和 screenshot。请求持久化等待，扩展断线或容器重启后由 Coordinator 从原 ReAct 断点继续。

安全边界固定为一次性配对、高熵哈希 token、`activeTab` 临时授权、固定工具 schema、opaque ref、mutating HITL、跨 host/作用域拒绝和幂等恢复。服务端 Browser sidecar 与客户端当前标签页桥保持分离；公开 Xpert App/API 禁止 Client Tools。后续自动化阶段现已完成。

## 2026-07-16 增量：XPERT-MIDDLEWARE-BROWSER-04

私有 Workflow、Xpert Chat、Goal 与 Handoff 已具备隔离联网浏览器闭环。`browser_automation` 通过独立 Playwright Chromium sidecar 提供导航、ARIA snapshot、页面读取、受审批交互、同作用域文件上传、下载与产物发布；会话、域名授权和操作幂等状态可在容器重启后恢复。

网络边界固定为公网访问、私网阻断与首域名逐 session 审批。egress guard 和 Playwright route 双重拒绝本机、Docker service、云元数据、危险协议和混合 DNS；写操作继续经过 tool policy、HITL 与 audit。公开 Xpert App/API 禁止部署 Browser。该阶段规划的客户端宿主桥现已由上方 `XPERT-MIDDLEWARE-CLIENT-05` 增量完成，并继续与服务端 Browser 保持边界分离。

最后更新日期：2026-07-23
维护人：模镜团队

## 2026-07-16 增量：XPERT-MIDDLEWARE-HITL-02

Agent 级中间件已补齐可恢复的人机审批闭环。`human_in_the_loop` 可在 tool policy 通过后暂停工具调用，也可在最终答案写入变量前要求确认；批准、编辑参数、拒绝反馈、人工替换与模型修订都沿用同一 classic workflow runner。

Workflow execution、审批请求和安全事件序列使用文件 Store 原子持久化。`ApprovalCoordinator` 通过 lease 从原 ReAct 动作恢复，已完成节点不会重跑，待审批工具不会被 fail-open 调用。WorkflowRun、Xpert Chat、Goal 和 MetaAgent Handoff Inbox 共用审批卡片；Goal/Handoff 超时进入 `needs_attention`。旧 `human_intervention` 保持 SSE 与 `/resume` 兼容，但底层也进入持久暂停路径。

公开 Xpert App 与 OpenAI 兼容 API 继续拒绝交互式 HITL 部署。下一阶段进入受控 Sandbox、命令、Skill 与浏览器工具，不把可信本地审批误表述为完整用户权限系统。详细契约见 `docs/XPERT_MIDDLEWARE.md`。

## 2026-07-16 增量：XPERT-MIDDLEWARE-SANDBOX-03

私有 Workflow、Xpert Chat、Goal 与 Handoff 已具备隔离 Agent 工作区：附件可进入 `inputs/`，Agent 可在 `work/` 读写和搜索文件，通过严格 argv 白名单执行本地命令，按需 staging 已安装 Skill，并将结果发布为可下载产物。所有能力复用 Runtime Toolset、policy、可恢复 HITL、audit 和 checkpoint。

执行由完全断网的独立 sidecar 承担。sidecar 不挂载仓库、`.env`、密钥、Docker Socket 或主服务 Store；路径逃逸、symlink、配额、超时进程组终止和操作幂等均有服务端护栏。公开 Xpert App/API 拒绝 Sandbox/Skill 部署。下一阶段单独进入 `XPERT-MIDDLEWARE-BROWSER-04`，不在当前无网 Sandbox 中偷偷放开网络。

## 2026-07-16 增量：XPERT-MIDDLEWARE-CORE-01

`runtime_middleware` 已从线性工作流步骤扩展为可绑定到单个 `workflow_agent` 的 Agent 级执行能力。绑定边使用 `sourceHandle="middleware-binding" -> targetHandle="middleware"`，不参与控制流拓扑、变量传播或节点调度；每个 Agent 独立编译 middleware pipeline，直接模型调用、ReAct 决策与工具调用共享一致的 policy、audit 和 checkpoint 边界。

首批真实能力包括上下文压缩、JSON Schema 结构化输出、按运行来源隔离的持久 Todo，以及不能绕过权限策略的 LLM 工具选择器。Xpert Chat 提供 Todo 管理与会话摘要状态，Goal、Handoff 和 App 复用同一 classic runner；公共 App 的 Todo 和摘要保持请求级临时态。

后续中间件路线依次为 HITL、Sandbox、Automation 和 Consolidation。每轮必须形成可运行闭环，不单独交付目录或纯观测页面。详细契约见 `docs/XPERT_MIDDLEWARE.md`。

## 2026-07-16 增量：XPERT-KNOWLEDGE-AGENT-01

成熟 RAG 基础路线已完成最后一轮闭环：已发布 Xpert 可通过受作用域约束的 Knowledge Runtime Toolset 检索活动知识版本、获取原文、生成稳定引用，并提出待审批知识写入。Knowledge Inbox 是唯一审批入口；批准只会基于活动来源快照自动构建候选版本，必须通过 Evaluation Gate 后才能推广，绝不自动激活。

- `workflow_agent` 通过 `knowledgeReadEnabled`、`knowledgeWriteEnabled` 和 1 至 5 个 `knowledgeBaseIds` 固定访问边界。
- Goal 与 Handoff 复用发布版本配置，并将安全来源 ID 写入提议。
- Xpert App 默认禁止动态知识工具；显式策略只允许读取，任何写入配置都无法部署。
- 活动版本、候选隔离、双索引、Processor、Knowledge Canvas、多模态理解、离线 Evaluation 和审批写入形成统一消费闭环。

本轮完成后暂停知识功能扩张。GraphRAG、实体关系抽取、社区摘要、图检索和新检索后端继续暂缓；下一阶段先根据真实使用反馈进行质量基线、性能、安全与技术债审计。

## 2026-07-16 增量：XPERT-KNOWLEDGE-EVAL-01

Knowledge Pipeline 已补齐版本化离线检索评估与 Promotion Gate。用户可以为知识库维护带 revision 的 Evaluation Set，使用稳定来源文档、chunk/source block 与页码引用标注期望命中，并让后台执行器在同一评估快照上对最多 5 个不可变候选版本进行对比。

- 指标固定覆盖 Recall@1/5、MRR@10、nDCG@10、Citation Hit/Coverage、无结果率、错误率和 P95 延迟。结果只保存安全排名、分数和 ID，不保存完整问题、正文、snippet、prompt 或密钥。
- 版本化 namespace 现在向外提供稳定 `source_document_id`，避免候选版本前缀导致跨版本标签漂移。Evaluation Run 固定评估集 revision、目标版本与检索配置，运行中的编辑不会改变已启动结果。
- `KnowledgeEvaluationExecutor` 是可恢复的单进程 worker，使用 `knowledge_evaluation` RunRegistry run 和摘要 checkpoint；服务重启会恢复 queued/running 运行。
- `/rag/:kbId/evaluation` 提供评估集创建/导入、检索预览标注、多版本运行、逐问题排名、指标对比和 Promote 操作。
- Promotion Gate 支持 `advisory / required`。required 模式只有在运行成功、知识库/候选版本匹配、评估集 revision 未过期且阈值通过时才允许激活；advisory 保留既有人工激活兼容路径。

该评估门禁现已成为 Knowledge Agent 提议候选的强制推广前置。图片向量、多模态 Embedding、版面坐标和 GraphRAG 继续暂缓。

## 2026-07-15 增量：XPERT-MULTIMODAL-KNOWLEDGE-01

Knowledge Pipeline 已补齐图片与扫描 PDF 的真实知识理解闭环。PNG、JPEG、WebP 和扫描 PDF 会先进入可选的 `image_understanding` stage，由显式选择的视觉模型生成 OCR、图片、表格与图表语义块，再继续复用 General/QA/Summary Processor、递归或父子分块以及向量/FTS5 双索引。

- PDF 页面通过 `pypdfium2`/PDFium 渲染，图片通过 Pillow 校验；上传时检查格式、MIME、损坏文件和 40MP 解压像素上限。图片与扫描 PDF 标记为 `pipeline_required`，不会污染 legacy 即时索引。
- 自动页面选择会处理文字少于 80 字符或图片覆盖率至少 12% 的页面；预览可强制全页。视觉模型固定使用 `ocr_visual_summary_v1` JSON 契约，并按现有 LLM Gateway、OpenRouter 顺序降级。
- 视觉块统一进入 `ProcessedDocument`，块类型为 `image_ocr / image_description / visual_table / visual_chart`，保留页码、来源 block、模型 ID 与截断状态。可靠 PDF 文本层与 OCR 重复时只保留一份正文。
- Job 现按 `load / vision / process / chunk / embed / store` 执行。逐页结果以 source hash、模型和视觉配置 hash 缓存；重试与重启只重跑失败页。`strict` 阻断候选 ready，`continue_on_error` 仅在仍有可索引内容时带 warning 完成。
- 候选版本固定 `vision_profile` 与视觉统计，Citation 增加可选 `page_number / visual_kind / source_block_id`。激活后 Chat、Workflow、Xpert、Goal 和 App 无需新协议即可消费视觉知识。
- 依赖许可证和归属见 `server/THIRD_PARTY_NOTICES.md`。Xpert 与 Dify 仅作行为参考，没有复制 AGPL 或许可证不明确实现。

离线评估、Promotion Gate 与知识审批写入已由顶部增量完成。图片向量、多模态 Embedding、版面坐标和 GraphRAG 继续暂缓。

## 2026-07-13 增量：XPERT-KNOWLEDGE-CANVAS-01

Knowledge Pipeline 已从表单式 Draft 推进为可执行 React Flow 画布。新增 `/rag/:kbId/pipeline`，用户可配置数据源、结构化处理器、递归或父子分块、Embedding、向量/FTS5 双索引和检索 profile，并在同一工作台完成节点预览、图校验、保存、执行、候选版本观察与激活。

- 后端 `pipeline_graph.py` 只负责 DAG、端口、阶段完整性和配置编译；编译结果写回现有 Pipeline Draft，执行继续复用唯一的 `KnowledgePipelineExecutor`。
- Graph 使用独立递增 `graph_revision` 和乐观并发检查；保存会原子生成新的 Draft version。旧 `/rag` 表单更新会同步图节点配置并保留位置。
- Job 固定 `graph_id / graph_revision / draft_version`，服务重启后仍按固定快照恢复。非法图、过期 revision 或双索引不完整不会创建 Job 或污染 active version。
- 节点预览最多返回 20 条截断摘要，不写 Draft、Job、索引或版本。图像理解节点已在后续多模态增量中启用，但必须显式选择视觉模型并通过渲染器/网关预检。
- Chat、Workflow、Xpert、Goal 和 App 不新增消费协议，继续统一读取人工激活的知识版本。

该画布基线已由多模态、评估与知识审批增量扩展为可选视觉阶段、版本 Promotion Gate 和受控写入闭环。GraphRAG 继续暂缓。

## 2026-07-13 增量：XPERT-RAG-PROCESSOR-01

Advanced RAG V2 已补齐索引前的成熟文档处理闭环：候选 Job 现在先把 TXT、Markdown 与 PDF 解析为稳定的 `ProcessedDocument / DocumentBlock`，再按固定 `processor_profile` 进入 General、QA 或 Summary 索引，最后重建同一候选版本的向量与 FTS5 双索引。

- 结构块覆盖标题、段落、列表、表格、代码块与 PDF 页面，保留标题路径、页码、字符偏移和稳定 block ID；PDF 可移除重复页眉页脚。
- `general` 保留结构正文并继续复用递归/父子分块；`qa` 索引模型生成的问题，召回时返回答案与来源段；`summary` 索引文档/章节摘要，召回时提升对应原文。
- Processor 模型继续复用 newAPI/OpenRouter 注册模型，不新增供应商 SDK；每个生成批次最多尝试两次，严格 JSON 无效、超时或网关失败都进入逐文档错误。
- Job 持久化 source hash、处理状态、尝试次数和私有处理产物。重试复用配置与内容均未变化的完成文档，只重跑失败文档；索引阶段仍从全部成功产物原子重建。
- `continue_on_error` 允许至少一个文档成功时生成带 warning 的候选；`strict` 任一失败均阻断候选 ready。所有文档失败不会创建版本。
- `/rag` 已提供处理模式、模型、失败策略、清洗选项、文档预览、逐文档 Job 结果和固定 Processor profile 展示。Preview 与 API 不返回本地路径、正文全集、prompt、embedding 或密钥。

该 Processor 阶段之后的 Knowledge Canvas、OCR/VLM、离线检索评估和知识审批写入均已完成：数据源、处理器、分块器、Embedding 与索引配置编译为现有 Knowledge Pipeline Job，候选版本通过 Evaluation Run 与 Promotion Gate 验收。GraphRAG 继续暂缓。

## 2026-07-13 增量：XPERT-RAG-RETRIEVAL-V2-01

Knowledge Pipeline 已补齐成熟 RAG 基础闭环的第一层：候选版本现在固定 `index_schema_version=2`、分块配置、Embedding profile 与 Retrieval profile，并在同一隔离版本中同时构建 Chroma/本地向量索引和 SQLite FTS5 全文索引。

- 分块支持 `recursive_character` 与 `parent_child`。两种模式均可配置有序分段标识符；父子模式索引子段，命中后返回父段上下文，同时保留子段作为 CitationAnchor。
- 检索支持 `vector / fulltext / hybrid`，混合模式使用加权归一化 RRF；版本 profile 固定权重、Top-K、score 阈值、候选倍数和 Rerank 策略。
- Rerank 优先使用专用 API，允许回退到 OpenRouter/newAPI 注册模型的严格 JSON 排序；超时或非法输出 fail-open，保留融合排序并返回 warning。
- 候选版本只有在向量和全文索引均完成时才可进入 ready。任一写入失败会清理两个候选 namespace，且不会改变 active version。
- `/api/rag/query`、Citation API、Chat RAG、workflow、Xpert、Goal 与 Xpert App 继续经 `RagService` 统一解析 active version；旧知识库不自动迁移，继续走 vector-only legacy 路径。

后续 Processor、Canvas、多模态、Evaluation 与 Knowledge Agent 增量均已完成。GraphRAG、知识图谱、实体关系抽取与社区摘要继续暂缓，先进入真实使用反馈和技术债审计。

## 2026-07-12 增量：XPERT-KNOWLEDGE-EXECUTE-01

Knowledge Pipeline 已从“保存草稿与预检”进入真实可执行闭环。`/rag` 可以把固定 draft revision、知识库文档和用户显式选择的 Xpert 会话附件提交为持久化 ingestion job；后台执行器依次完成 load、process、chunk、embed、store 五个 stage，并生成隔离的候选索引版本。

- 候选版本不会自动影响线上检索，必须先预览，再由用户手动激活。
- 激活操作只切换知识库的 active version 指针；历史 ready 版本保留，可作为回滚目标。
- 普通 RAG 查询、Chat RAG、`knowledge_retrieval` 与 `knowledge_citation` 自动读取 active version；没有 active version 的旧知识库继续读取 legacy index。
- Job、stage、候选版本与 active pointer 持久化到 RAG metadata；服务重启会恢复 queued/running job，并为失效的内存 RunRegistry 引用创建 recovery run。
- Xpert Chat 只允许用户把本次显式选中的附件提升为候选知识版本，不会自动索引整个会话。

当前执行器仍是单进程、单并发的本地 worker；不引入数据库、Redis/Celery、分布式 lease、自动激活或真实图像理解。`XPERT-APP-API-01` 已完成固定版本部署、未列出分享、兼容 API、凭据、配额和回滚闭环。

## 2026-07-10 增量：XPERT-HANDOFF-EXECUTOR-01

已发布 Xpert 现在可以通过 Handoff 形成真实协作链路。源 Xpert 使用 `agent_handoff` 或 `handoff_router` 创建显式 `xpert:<slug-or-id>` 目标后，后台 `HandoffExecutor` 会领取任务、固定目标当前发布版本、运行目标 Xpert，并把结果回写到 AgentTask 与 Handoff。

- `waitForCompletion=false` 时源工作流立即获得 `handoff_id`，目标 Xpert 在后台继续执行。
- `waitForCompletion=true` 时源工作流等待目标完成，并把结果写入 `resultVariable` 供下游节点继续使用。
- 自动执行具有 60 秒 lease、最多 3 次尝试、退避重试、`retry_wait` 与 `dead_letter` 状态；死信可从 MetaAgent Inbox 重新入队。
- AgentTask/Handoff 使用可选原子 JSON Store；Docker 通过 `AGENT_TASK_STORAGE_DIR` 持久化，重建或重启容器后仍可恢复。
- RunRegistry 形成 `source Xpert -> agent_handoff -> target Xpert -> node runs` 父子链，checkpoint 只记录版本、尝试次数、ID、长度和错误摘要。

普通 Agent 名称继续走人工 Inbox，不会被自动猜测或执行。当前可靠性边界是单后端进程文件队列，不承诺多进程锁，不使用 Redis/Celery 或数据库。Handoff Executor 已作为 Conversation Goal 的步骤执行底座。

## 2026-07-10 增量：XPERT-RUNTIME-OPS-02

`/runtime` 从第一版只读聚合页推进到运维观测第二版：MCP Runtime KPI 细分为活跃、异常、已关闭和未知状态；MCP 表格支持状态筛选；RunRegistry 列表会突出 `failed` / `cancelled` run，展示错误摘要、checkpoint severity 统计和“重试待接入”占位；新增 `GET /api/runtime/environment-summary` 提供脱敏环境与依赖摘要。

当前边界保持保守：环境摘要只返回布尔就绪态和更新时间，不返回 `.env` 内容、API key、本地路径或命令输出；重试入口只是禁用占位，不触发真实 retry worker；MCP start/stop、Skill 安装/卸载和环境变量编辑仍留在既有页面或后续规划中。

## 2026-07-10 增量：XPERT-STUDIO-PUBLISH-01

Xpert 对齐主线已从资源目录和配置草稿转入可运行的智能体平台。新增 Xpert 文件型 Store、草稿 revision、不可变发布版本、发布预检和已发布 Xpert 聊天入口：

- Xpert 草稿复用 classic workflow 定义；普通 /workflow 仍保持 localStorage 草稿和原有运行协议。
- 发布会对 workflow 做静态图校验与聊天契约预检，要求唯一聊天输入、唯一最终输出、可用的 workflow_agent 配置，并暂时禁止 human_intervention。
- 每次发布生成不可变 workflow 快照和递增版本号。后续修改草稿不会改变已发布版本。
- 已发布版本通过 /agents/xpert/:xpertId/chat 使用同一 classic runner 运行，复用 Toolset、Knowledge、Middleware、Handoff 和 RunRegistry。
- Xpert run 使用 run_type=xpert，并在 SSE workflow_meta 中附带 xpert_id、xpert_version；RunRegistry 与 checkpoint 只保存版本、ID、长度和错误摘要。

当前边界：Store 是本地文件持久化，不是 workspace 数据库；长期 Goal、文件/记忆与公开 App/API 已由后续模块补齐，组织权限和多人协作仍未实现。

## 2026-07-09 增量：XPERT-STUDIO-PANEL-02

`workflow_agent` 已开始消化 Xpert 式智能体配置侧栏里的第一批运行策略字段。当前真实生效范围只包括失败重试、备用模型、禁用输出和异常处理：

- `retryOnFailure=true`：模型调用或工具循环失败后最多额外尝试一次。
- `fallbackModelId`：主模型失败后切换到备用模型再尝试。
- `exceptionHandling=empty_output`：节点失败时输出空字符串并让 workflow 继续完成；`none/fail` 保持既有失败路径。
- `disableOutput=true`：节点仍执行并写入 checkpoint，但不写入 `outputVariable`。

本轮只影响 `workflow_agent` runner，不改变普通 `agent` 节点语义，不接文件理解、并行工具调用、记忆写入或结构化输出强校验。RunRegistry checkpoint 只记录 attempt、model_id、fallback_used、error 摘要和 output_disabled 等元信息，不保存完整 prompt、模型输出或工具结果。

## 2026-07-09 增量：XPERT-WORKFLOW-REGISTRY-API-01

classic `/workflow` 节点库元数据已从纯前端 registry 推进为后端只读 Workflow Node Registry API：`GET /api/workflow/node-registry`。该接口返回 Xpert 式 `workflow / knowledge` tab、工作流分类 section、可拖拽 item 与禁用 placeholder，用于降低后续新增节点时的前后端元数据漂移风险。

当前边界保持不变：registry 只负责菜单元数据，不决定执行能力；真正可运行节点仍以 `SUPPORTED_NODE_KINDS`、validate 与 classic runner 为准。前端 `NodePalette` 优先消费该 API，接口失败时回退本地 registry；中间件 tab 继续使用 `/api/runtime/middleware-nodes`，拖拽 payload、SSE 与 runner 协议不变。

## 2026-07-09 增量：XPERT-WORKSPACE-HUB-02

该 2026-07-09 增量把 `/studio` 从只读资源总览推进到工作空间 Hub 第二版，
但当时 `API 工具` 与 `数据库` 仍是待接入卡片。截至 2026-07-28，API 工具已
指向 Toolset，数据库能力由 Data X 表达，用户标签也已移除“Xpert 对齐”旧文案；
当前状态以 `StudioHomePage` 和 [GLOSSARY.md](./GLOSSARY.md) 为准。

本轮仍是前端只读聚合视图，不新增后端聚合 API，不引入 Workspace 权限、持久化资源表或资源创建编排。任一资源 API 失败时只影响对应卡片，工作空间页面整体保持可打开。

## 对齐原则

历史 Xpert 对齐采用“领域模型与交互证据参考 + ModelMirror 原生实现改写”；
项目继续保留 React、FastAPI、Pydantic、pytest 架构，不迁移 Xpert 的
Nx、NestJS、Angular 主框架，也不复制 AGPL 实现。冻结后不再使用本地源码路径
或截图差异驱动新功能。

EvoAgentX 主线已锁定官方
`v0.1.4@aad19b912f640161ea07e8904d9237cd34fde5f1`。后续按 Meta Planner V2、
Evaluator、Prompt 候选和工作流结构候选顺序推进。MIT 代码只允许在逐文件审计后
选择性移植并保留版权与 NOTICE；任何结果只能形成候选 Xpert 草稿和评估报告，
不能静默修改线上发布版本。

## 历史对齐主线

以下产品骨架解释 Xpert 能力如何形成，不再代表当前开发排期：

1. 工作空间资源：统一呈现智能体、知识库、MCP 工具集、API 工具、Skill、提示词、环境、运行记录。
2. Xpert Studio 画布：对齐智能体画布、节点库、右侧配置面板、预览/发布/运行入口。
3. Agent / Handoff / RunRegistry：建立任务、移交、运行记录、checkpoint、人工处理与未来调度的稳定底座。
4. Toolset / MCP / Plugin / Skill：统一工具来源、工具权限、工具审计、插件市场和技能库。
5. Knowledge Pipeline：把现有 RAG 逐步拆成 FileAsset、Artifact、Chunk、CitationAnchor、流水线草稿与执行观测。
6. Environment / Sandbox / Memory / Observability：补齐环境变量、受限执行、文件工作区、记忆写入、日志与监测面板。

## 能力矩阵

| 能力域 | 当前状态 | 已完成 | 当前边界 | 下一步 |
| --- | --- | --- | --- | --- |
| 工作空间资源 | 部分实现 | `/studio` 已作为 Xpert 式资源 Hub，聚合智能体、工作流、知识库、MCP、API 工具、Data X、Skill、提示词、环境、运行记录，并支持快速入口、标签过滤和运行摘要 | 当前不做 workspace 权限或通用资源创建编排；API 工具仍为待接入卡片，Data X 已进入真实工作台 | 基于真实使用反馈收敛聚合模型 |
| Xpert Studio / 发布 | 稳定冻结 | classic `/workflow`、智能体配置侧栏、Xpert 草稿、不可变版本、聊天运行、Goal、自动 Handoff、文件/记忆、固定版本 App/API、外部 Xpert/知识库资源，以及固定 Prompt/Plugin 绑定 | 使用文件型 Store 和 classic runner；资源边不参与控制流；不做组织权限、多人协作或数据库迁移 | 仅安全、缺陷与兼容维护 |
| Runtime Middleware | 部分实现 | Agent 绑定、上下文压缩、结构化输出、Todo、工具选择、HITL、Sandbox/Skill、Browser、Client Tools、Scheduler、Ralph Loop、Knowledge Writer、Plugin Hooks、Xpert/Skill 自编写提案、类型化文件记忆、Office 实时自动化、Data X 指标工具 | 文件型单进程协调器；公开 App 禁止交互式、客户端、Office、自动化与自编写中间件，Data X 仅显式只读 | 真实使用反馈与契约收敛 |
| Data X | 部分实现 | 文件快照、DuckDB 项目隔离、语义实体/连接、基础和派生指标、不可变发布版本、受限查询 DSL、词法/本地向量检索、提案审批、Agent 中间件和 App 只读门禁 | 文件数据源优先；单进程导入协调；不接外部数据库、不开放任意 SQL、写回数据或通用 Dashboard | 外部 SQL Connector、凭据保险箱和定时刷新按真实需求另行规划 |
| Agent Task | 部分实现 | AgentTask API、MetaAgent 任务工作台、workflow `agent_task` 节点、可选文件持久化、Goal 步骤派发 | 单进程文件 Store，不是分布式任务队列 | 为文件与记忆任务补安全上下文 |
| Handoff | 部分实现 | Handoff API、workflow `agent_handoff`、`handoff_router`、人工 Inbox、目标 Xpert 自动执行、同步结果回传、重试、死信与 Goal 协作 | 仅显式 `xpert:` 目标自动执行；单进程 lease，不做分布式调度 | 扩展文件与记忆上下文传递 |
| RunRegistry / Trace | 部分实现 | workflow/xpert/chat/goal/agent_task/agent_handoff run、checkpoint、workflow/chat/Xpert/Goal 观测与 `/runtime` 运维总览 | 内存态，可观测索引，不是调度器；Goal 重启恢复会创建 recovery run | 为文件、记忆与知识执行提供护栏 |
| Workflow Agent | 稳定冻结 | `workflow_agent` 节点、模型执行、Runtime Toolset、文件理解、结构化输出、类型化记忆召回/候选写回、失败重试、备用模型、异常策略、有界并行工具调用、全局执行预算、版本化会话功能和固定 Prompt/Plugin 资源 | 轻量 JSON 决策，不是 function calling；并行仅允许安全只读工具；音频依赖兼容网关 | `EVOAGENTX-META-PLANNER-01` 只增强候选生成，不替换 Runtime |
| Chat Toolset | 部分实现 | `/api/chat` 可选 MCP 工具模式，chat run 与 checkpoint | 默认关闭，不改变普通聊天；无自动 handoff | 补工具偏好、安全提示和观测 UI |
| Toolset / MCP / API | 部分实现 | `/toolsets` 支持 MCP、OpenAPI 3.x、OData v4、Tavily 与 Todo；具备加密凭据、受控执行、工具 Schema/语义/测试、漂移诊断、不可变发布、Agent 绑定、有界并行、Tool Memory、Plugin 固定引用和受控 App 只读开放 | 远程 ref、multipart、OData batch、任意 Code Toolset、企业 Provider 治理和浏览器 OAuth 暂缓 | 冻结，不继续扩张 Provider 生态 |
| Plugin / Skill | 稳定冻结 | `/plugins` 声明式 ZIP、不可变版本、Prompt/Skill/Toolset/中间件聚合、Plugin 资源绑定；另有 `/skills`、Workspace 草稿、Sandbox staging 与显式 Hook | 不加载动态后端代码；无远程市场、组织权限、计费；公共 App 禁止 Plugin | 仅安全、缺陷与兼容维护 |
| Knowledge Pipeline | 已实现 | FileAsset/Artifact/Chunk/CitationAnchor、结构感知 Processor、General/QA/Summary、可执行 Graph、逐文档/逐视觉页恢复、图片与扫描 PDF 的 OCR/VLM、版本化 ingestion job、递归与父子分块、向量/FTS5 双索引、混合检索、Rerank、离线评估、Promotion Gate、Knowledge Toolset、审批写入、预览、激活/回滚 | 成熟 RAG 基础闭环完成；仍为本地单进程 worker，旧上传保留 legacy index；评估标签需人工维护；图片向量、版面坐标与 GraphRAG 暂缓 | 真实使用反馈与技术债审计 |
| Prompt / Slash Command | 已实现 | `/prompts` 草稿、校验、预览、不可变版本、全局命令别名、Xpert 固定绑定、`/alias` 执行和 `//` 转义 | 模板仅允许 `{{args}}`；命令始终执行当前 Xpert；App 只开放直接绑定的安全 Profile | 冻结，按真实反馈收敛模板契约 |
| Environment / Sandbox | 部分实现 | `/runtime` 已提供脱敏环境与依赖摘要，展示模型网关、OpenRouter、git/node/npm/npx/python 是否就绪 | 不展示密钥值，不编辑环境变量，不提供沙箱实例或文件工作区语义 | 评估 Xpert 式环境变量管理和沙箱资源模型 |
| Memory / Logs / Monitor | 部分实现 | 会话记忆、四类 Xpert 文件记忆、三层召回、候选审批、revision 冲突、使用信号、RunRegistry events/checkpoints/audit 摘要 | 文件型单机 Store；不做向量记忆、跨 Xpert 私有共享或组织级审计 | 以召回质量和冲突率评估是否需要向量化 |

## 已实现基线

- `/api/chat` 默认保持普通 SSE 聊天；显式启用 `tool_mode=mcp_tools` 时进入 Runtime Toolset 工具循环，登记 chat run、checkpoint、tool events 与审计摘要。
- Classic workflow 已支持 `workflow_agent`、`agent_task`、`agent_handoff`、`handoff_router`、`knowledge_citation`、`mcp_tool`、`runtime_middleware` 等 Xpert 对齐节点。
- `/workflow` 节点库已从平铺数组收敛为前端 `workflowNodeRegistry`，按工作流、中间件、知识流水线 tab 和逻辑、转换、工具、记忆、其他等 Xpert 分类渲染；拖拽协议和运行语义不变。
- `/workflow` 中 `agent` 与 `workflow_agent` 的右侧配置已对齐为 Xpert 式分区侧栏，包含节点、参数、提示词/模型、中间件、知识库、工具、运行策略、输出结构和记忆写入；其中高级区块当前只保存配置草稿。
- `MCPToolsetProvider`、`CapabilityRegistry`、`run_tool_with_runtime` 已成为 MCP 工具调用主路径。
- `ToolPermissionPolicy` 与 `InMemoryToolAuditStore` 已对 workflow/chat 工具调用提供最小权限与审计。
- `/runtime` 已作为 Xpert Runtime Ops 入口，只读聚合 MCP sessions、Tool Registry、RunRegistry checkpoints、Skill 安装摘要和脱敏环境依赖状态。
- AgentTask/Handoff API 已支持创建、查询、accept、reject、complete、立即执行和重新入队；MetaAgent 页面可查看人工移交、自动执行、重试与死信。
- RunRegistry 已支持 workflow、workflow_agent、agent_task、agent_handoff、chat 等 run 类型，并提供 checkpoint 查询。
- 本地 RAG 之上已有 Knowledge Pipeline 只读元数据视图：FileAsset、Artifact、KnowledgeChunk、CitationAnchor，并已派生数据源、处理器、分块器、图像理解四段 stage 草稿。
- `/studio` 已成为 Xpert 对齐工作空间入口，前端以软降级方式读取现有 RAG、MCP、Skill 与 RunRegistry API，展示资源卡片、分类筛选、搜索和最近运行摘要。

## Xpert UI 证据摘要

详见 `docs/XPERT_UI_REFERENCE.md`。本轮截图与源码侦察确认 Xpert 的产品骨架主要包括：

- Xpert Studio：左侧工作区导航、中心画布、右侧节点配置、顶部预览/发布/功能入口。
- 节点库菜单：工作流、中间件、知识流水线、工具集分别有清晰分类。
- 智能体配置侧栏：参数、中间件、知识库、工具、失败重试、备用模型、异常处理、输出结构、记忆写入。
- 工作空间资源：数字专家、内置工具、MCP 工具集、API 工具、知识库、数据库、Skill、提示词、环境。
- 运维与市场：MCP Runtime 运维、插件市场、技能市场、提示词工作流、环境变量面板。

## 分阶段路线

以下五阶段是 Xpert 对齐形成当前能力面的历史建设路线，不再是未来功能排期。
当前路线以本页顶部冻结声明和 EvoAgentX 文档为准。

### 阶段 1：资源与导航归拢

目标：先让用户能从一个 Xpert 式工作空间入口理解系统中有哪些资源，而不是继续在分散页面之间跳转。

- `XPERT-WORKSPACE-HUB-01`：已完成第一版工作空间资源总览，聚合智能体、知识库、MCP、Skill、提示词、环境、Run 入口。
- 当前边界：只读聚合、软降级、不破坏现有入口；暂不引入 workspace 权限、资源创建编排或后端聚合 API。

### 阶段 2：画布节点体系收敛

目标：把 classic workflow 节点从散落的静态列表收敛为 Xpert 分类节点注册表。

- `XPERT-WORKFLOW-PALETTE-01`：已完成第一版前端节点 registry，按逻辑、转换、工具、记忆、其他、中间件、知识流水线分类渲染节点库。
- 当前边界：registry 先放前端本地；未实现的数据库、注释、知识流水线 stage 只显示禁用占位，不生成节点；拖拽 payload、validate、runner 和 SSE 协议保持不变。

### 阶段 3：智能体配置面板对齐

目标：把当前节点配置表单升级为 Xpert 式智能体配置侧栏，但不一次性实现全部高级语义。

- `XPERT-STUDIO-PANEL-01`：已完成第一版 `agent` / `workflow_agent` 配置侧栏分区，补齐参数、中间件、知识库、工具、失败重试、备用模型、异常处理、输出结构、记忆写入等 UI 区块。
- 当前边界：除已有模型调用与工具模式外，新区块只保存配置草稿；执行语义按后续小步接入。

### 阶段 4：知识流水线从只读到草稿

目标：从当前 RAG 元数据视图推进到可视化知识流水线草稿。

- `XPERT-KNOWLEDGE-PIPELINE-02`：已引入数据源、处理器、分块器、图像理解四类 stage 的只读草稿 API 与 `/rag` UI。
- 当前边界：不迁移向量库，不改变 `/api/rag/query`，不执行真实图像理解；只新增可观测草稿层。

### 阶段 5：运行与工具运维收口

目标：补齐 Xpert 的运行、插件、工具、环境和观测管理体验。

- `XPERT-RUNTIME-OPS-01`：已完成 `/runtime` 第一版运维入口，聚合 MCP Runtime、Tool Registry、RunRegistry 与 Skill Runtime 摘要。
- 当前边界：只读观测，不替代 `/mcps` 或 `/skills` 的管理操作；不新增后端协议、不展示密钥、完整 prompt 或工具输出。

## 已完成的目录路线（历史）

1. `XPERT-WORKFLOW-REGISTRY-API-01`：评估是否把前端节点 registry 升级为后端统一 registry API，避免未来前后端元数据漂移。
2. `XPERT-STUDIO-PANEL-02`：逐项接入重试、备用模型、输出结构、记忆写入等真实执行语义。
3. `XPERT-RUNTIME-OPS-02`：已完成 `/runtime` 失败摘要、重试入口占位、MCP runtime 状态细分和环境观测。
4. `XPERT-KNOWLEDGE-PIPELINE-03`：在 stage 草稿稳定后评估可编辑流水线草稿和执行观测，不迁移现有 RAG 主路径。
5. `XPERT-WORKSPACE-RESOURCE-MODEL-01`：当 Hub 入口稳定后，再评估是否抽象 workspace 资源模型与后端聚合 API。

## 近期功能闭环顺序

后续优先级从目录和观测页面转为可保存、发布、运行、协作的 Xpert，每步都有独立验收和回退边界：

1. XPERT-STUDIO-PUBLISH-01：已完成第一版 Xpert 草稿、不可变版本、发布预检和已发布聊天运行。
2. XPERT-HANDOFF-EXECUTOR-01：已完成显式目标 Xpert 的自动领取、版本固定、执行回写、同步等待、lease、重试与死信。
3. XPERT-CONVERSATION-GOAL-01：已完成从对话创建 Goal、人工审核 DAG、并发步骤派发、暂停、取消、恢复、人工处理与最终结果汇总。
4. XPERT-FILE-MEMORY-01：把会话附件接入 FileAsset/Artifact，并提供显式、可观测的 memory search/get/write。
5. XPERT-KNOWLEDGE-EXECUTE-01：已完成持久化 ingestion job、版本化 chunk/index、候选预览，以及人工确认后的 active version 原子切换与回滚。
6. XPERT-APP-API-01：已为发布版本提供稳定 App/API、访问控制、调用配额、版本回滚与审计，功能闭环路线收尾。

## 2026-07-13 增量：XPERT-APP-API-01

已发布 Xpert 可创建一个未列出 App，并显式部署任意不可变版本。分享 token 与 API key 仅显示一次，服务端只保存哈希和前缀；OpenAI 兼容 JSON/SSE 只公开最终回答。App 工具、Handoff 与 Xpert 记忆默认关闭，工具策略未加载时默认拒绝。RunRegistry 新增 `xpert_app` run，记录固定版本、deployment revision、访问类型与脱敏 key prefix。

Knowledge Pipeline Execute 与 App/API 完成后，近期功能闭环路线收尾。后续迭代优先依据真实使用反馈、安全审计和技术债重新排序，而不是继续扩展目录页。

## 验收护栏

每个后续对齐任务至少包含：

- 后端语法检查：`python -m py_compile server/main.py server/xpert_runtime/*.py server/workflow_native/*.py`
- 相关 pytest：按模块新增或更新 `server/tests/test_xpert_runtime_*.py`、workflow 节点测试或 RAG 测试。
- 前端构建：涉及前端时运行 `cd client && npm.cmd run build`。
- Docker smoke：影响主路径时运行 `docker compose -p modelmirror up -d --build --force-recreate` 与 `/api/health`。
- 文档更新：同步更新本总纲或相关模块文档，说明状态、边界和下一步。
- 安全检查：不得提交 `.env`、真实 API key、完整工具输出、完整 prompt、embedding、本地绝对文件路径或密钥。

## 开源与参考边界

- Xpert：只参考领域模型、交互结构、文案分类、运行时分层和测试思路；默认参考改写，不复制源码。
- EvoAgentX：Xpert 收尾后转为下一条主线；只移植经过 commit/许可证/测试审计的 MIT 模块并保留归因，不整体引入为运行时依赖。
- 第三方仓库：可用于确认公开能力边界和术语，但不得直接搬运实现；如必须引用片段，先确认许可证兼容并在文档记录来源。

## 2026-07-11 Update: XPERT-FILE-MEMORY-01

Published Xperts now have a durable conversation context layer. Xpert Chat can create and reopen conversations, upload explicitly selected TXT/Markdown/PDF files, inject bounded file excerpts into `workflow_agent`, recall conversation/Xpert memories, and manage human-approved memory candidates.

- `XpertContextStore` persists conversations, file metadata/artifacts, active memories, and write candidates through atomic JSON/file replacement under the existing runtime storage mount.
- `enableFileUnderstanding`, `memoryReadEnabled`, `memoryReadScope`, `memoryWriteEnabled`, and `memoryWriteTarget` now have runtime meaning for `workflow_agent`.
- `memory_search`, `memory_get`, and `memory_propose_write` are exposed as a dedicated Runtime Toolset capability and continue through middleware, policy, and audit.
- Model-originated writes remain pending until approved. A user-initiated "remember this" action writes an active record directly.
- Conversation Goals persist explicit file references and pass them through AgentTask/Handoff metadata. Source conversation memories are not implicitly shared across Xperts.
- Attachments are not indexed into RAG and do not create a knowledge base. Promotion and versioned ingestion remain the responsibility of `XPERT-KNOWLEDGE-EXECUTE-01`.

## 2026-07-10 Update: XPERT-KNOWLEDGE-PIPELINE-03

The `/rag` Knowledge Pipeline has moved from a read-only four-stage view to a saved draft config plus preflight observation flow. `GET /api/rag/pipeline/draft` now returns draft metadata, version, `updated_at`, `editable`, and safe per-stage config. `PATCH /api/rag/pipeline/draft/{kb_id}` persists safe draft config. `POST /api/rag/pipeline/draft/{kb_id}/preflight` returns stage checks, warnings, and document/artifact/chunk counts.

Historical boundary at that increment: draft config did not affect indexing and image
understanding remained planned/disabled. The vision stage became optional and executable
on 2026-07-15; current behavior is documented in
[RAG_INTEGRATION.md](./RAG_INTEGRATION.md). Responses still must not expose local paths,
full chunk text, embeddings, prompts, or secrets.

## 2026-07-10 增量：XPERT-CONVERSATION-GOAL-01

Xpert 对齐主线已从一次性发布与移交推进到长期 Goal 执行闭环。用户可在已发布 Xpert 聊天中把最近对话转成 Goal，由已发布 Planner Xpert 生成 2 到 20 步的依赖计划；计划必须人工审核后才会启动。

- GoalStore 以 `goals.json` 原子持久化目标、步骤、固定版本、结果和错误。
- GoalCoordinator 按 DAG 派发 ready 步骤，单 Goal 默认最多并发 2 步。
- 每一步复用 AgentTask、显式 `xpert_auto` Handoff、HandoffExecutor 和已发布 Xpert classic runner。
- 支持暂停、恢复、取消、失败重试、改派、显式跳过与 `needs_attention` 人工处理。
- RunRegistry 新增 `goal` run，形成 Goal、Planner、Task、Handoff、目标 Xpert 和节点 run 的父子链。
- 前端新增 `/agents/goals` 工作台，并在 Xpert Chat、`/agents`、`/studio` 提供入口。

当前边界仍是单进程文件 Store；暂停和取消不强制杀死运行中的模型请求，RunRegistry 重启后通过 recovery run 恢复索引。下一步进入 `XPERT-FILE-MEMORY-01`，把附件和显式记忆读写接入已发布 Xpert 与长期 Goal。
