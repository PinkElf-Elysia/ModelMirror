# Xpert UI 参考记录

最后更新日期：2026-07-23
维护人：模镜团队

> **状态：冻结参考。** 本文用于追溯内部映射和验收证据，不是当前 UI 文案
> 指南。用户界面不得恢复“Xpert 对齐”等旧标签；当前用语见
> [GLOSSARY.md](./GLOSSARY.md)。

## 2026-07-23：Xpert 会话功能映射

真实 Xpert 的“功能”面板将开场白、问题建议、会话总结、长期记忆、记忆回复、标题、文件和语音能力作为智能体级配置，而不是单个工具参数。ModelMirror 的第一版映射将这些配置保存到 `XpertDraft.features`，发布时固定进不可变版本：

- Studio 统一编辑开场白、starter questions、问题建议、标题、摘要、记忆回复、文件策略和显式音频模型。
- Xpert Chat 依据所选版本展示开场白、建议问题、标题、摘要状态、文件开关与 TTS/STT 操作。
- 文件、记忆、音频和摘要继续复用现有 Context Store、Runtime middleware 与模型网关，不建立平行运行器。
- 最大并发请求与递归限制属于整个 Xpert 的全局设置；工具并发和调用次数继续在 Agent 工具分区单独配置。

## 目的

本文记录 2026-07-08 对真实 Xpert 前端界面与本地 `xpert-main` 源码的观察结果，用于指导 ModelMirror 后续对齐。本文只记录概念、字段、交互和源码参考位置，不复制 Xpert 源码实现。

## 参考来源

- 上游审计快照：本地临时副本，未纳入仓库；具体路径不作为可复现证据。
- 本地真实界面：`http://localhost:8088/xpert/w/2b9d44a2-b95e-44e2-8a76-97e0d9bb86be`
- 用户提供的 2026-07-08 Xpert 前端截图
- 主要源码参考点：
  - `apps/cloud/src/assets/i18n/zh-Hans.json`
  - `apps/cloud/src/app/features/xpert`
  - `apps/cloud/src/app/features/xpert/studio`
  - `apps/cloud/src/app/features/xpert/knowledge`
  - `apps/cloud/src/app/features/operations/mcp-runtimes.component.*`
  - `apps/cloud/src/app/features/setting/plugins`
  - `packages/plugin-sdk/src/lib/workflow`
  - `packages/plugin-sdk/src/lib/toolset`
  - `packages/server-ai/src/xpert-toolset`
  - `packages/server-ai/src/xpert-workspace`

## 产品骨架

Xpert 的核心不是单个工作流节点，而是一套工作空间级产品骨架：

1. 工作空间资源首页：统一展示数字专家、内置工具、MCP 工具集、API 工具、知识库、数据库、Skill、提示词、环境。
2. Xpert Studio：左侧资源/设置导航，中间画布，右侧节点配置，顶部预览、环境、功能、发布入口。
3. 节点库：通过浮层菜单添加智能体、外部专家、知识库、工具集、中间件、工作流、知识流水线。
4. Runtime 运维：MCP Runtime 运维页展示实例统计、筛选器、空态和刷新入口。
5. 市场与资产：插件市场、技能市场、智能体广场、提示词工作流、环境变量面板。

## 画布与节点库

### 顶层菜单

截图中的画布左下菜单包含：

- 新建智能体
- 添加外部专家
- 添加知识库
- 添加工具集
- 添加中间件
- 添加工作流
- 添加知识流水线
- 删除
- 粘贴到这里

ModelMirror 对齐策略：先在 classic `/workflow` 中完成节点注册表和分类菜单，不一次性复制完整 Xpert Studio。

### 工作流节点分类

Xpert “添加工作流”菜单按以下分类组织：

| 分类 | 节点 |
| --- | --- |
| 逻辑 | 触发器、路由、迭代、子流程、列表操作、变量聚合、变量赋值 |
| 转换 | 问题分类器、知识检索、代码执行、模板、JSON 序列化、JSON 反序列化、回答 |
| 工具 | HTTP、工具调用、智能体工作流、任务移交 |
| 记忆 | 数据库 |
| 其他 | 注释 |

ModelMirror 已有对应基础：`input`、`condition`、`iteration`、`list_operation`、`variable_aggregator`、`variable_assign`、`question_classifier`、`knowledge_retrieval`、`code`、`template_transform`、`mcp_tool`、`workflow_agent`、`agent_task`、`agent_handoff`、`handoff_router`、`knowledge_citation`、`output`。

`XPERT-WORKFLOW-PALETTE-01` 已完成第一版本地映射：ModelMirror 在前端建立 `workflowNodeRegistry`，并让 `/workflow` 节点库以“工作流 / 中间件 / 知识流水线”三个 tab 呈现。工作流 tab 内按 Xpert 的逻辑、转换、工具、记忆、其他分类渲染；中间件 tab 继续读取 `/api/runtime/middleware-nodes`；知识流水线 tab 先暴露可运行的 `knowledge_citation`，并以禁用占位呈现数据源、处理器、分块器、图像理解等后续 stage。

`XPERT-WORKFLOW-REGISTRY-API-01` 已把“工作流 / 知识流水线”菜单元数据后端化为 `GET /api/workflow/node-registry`。前端仍保留本地 registry 作为 fallback；后端 registry 仅暴露概念、分类、标题、描述、图标、标签与禁用占位，不复制 Xpert 源码，也不改变节点执行语义。中间件菜单继续由现有 middleware registry 提供，避免两个 registry 在本轮混用职责。

当前映射边界：

- 普通工作流节点仍使用原有 `application/modelmirror-node = kind` 拖拽协议。
- runtime middleware 仍使用现有 JSON payload，不改变 `WorkflowEditor` 解析逻辑。
- 数据库、注释、JSON 序列化/反序列化、知识流水线 stage 等 Xpert 节点暂不创建真实节点，只显示“待接入”占位，避免用户拖入无法运行的节点。
- registry 先放在前端本地，后续如节点元数据继续扩张，再评估后端统一 registry API。

## 中间件菜单

截图中的“添加中间件”菜单包括：

- Xpert 编写中间件
- 知识库写入器
- 技能中间件
- 技能创建中间件
- 沙箱服务
- 沙箱命令行工具
- Xpert 文件记忆
- 浏览器自动化
- 客户端工具中间件
- 上下文压缩中间件
- 人机协同中间件
- Office 自动化
- Ralph 循环
- 定时任务
- 插件 Hooks
- Data X 指标管理
- 待办事项中间件
- LLM 工具选择器
- 客户端副作用中间件
- 沙箱文件工具
- 结构化输出事件中间件

ModelMirror 已有最小底座：`system_prompt_injector`、`event_recorder`、`tool_policy`、`tool_audit`、`mcp_tools`。后续不要直接追逐完整列表，应先补齐中间件 registry、配置表单、运行链路和观测。

## 知识流水线菜单

截图中的“添加知识流水线”菜单按 stage 分类：

| 分类 | 示例 |
| --- | --- |
| 数据源 | 默认、PDF 图文、文本 |
| 处理器 | 处理器类节点 |
| 分块器 | 递归字符、Markdown 递归、父子关系 |
| 图像理解 | 视觉语言模型 |

ModelMirror 当前已在本地 RAG 上完成只读 Knowledge Pipeline 元数据视图、`knowledge_citation` 节点，以及 `/rag` 中的数据源、处理器、分块器、图像理解四段 stage 草稿 UI。该草稿层实时派生自现有 FileAsset / Artifact / Chunk 元数据，不迁移向量库、embedding 策略或 `/api/rag/query`。

`XPERT-KNOWLEDGE-CANVAS-01` 将这组 stage 映射为 `/rag/:kbId/pipeline`：中间是 React Flow 执行图，画布附近提供节点库，右侧工作台使用“配置 / 预览 / 运行”tabs，顶部集中保存、校验和执行。真实节点覆盖数据源、可选图像理解、结构化处理、两种分块、Embedding、双索引和检索；图像节点要求显式选择视觉模型，并显示页面策略、渲染限制、失败策略和预览。该映射复用 ModelMirror 现有 Draft/Job/Version，不复制 Xpert Angular 实现，也不产生第二套知识执行器。

## 工具集菜单与市场

截图中的工具集菜单有 Provider、内置、MCP、API 四个页签，示例包括对话 BI、对话数据库、指标管理、语义模型管理、规划、Tavily、SearchApi、电子邮件、Bing、钉钉、Slack、Discord、Serper。

ModelMirror 已完成 MCP/OpenAPI/OData/内置 Provider Toolset 的创建、凭据、发现、测试、发布和 Agent 绑定。`/plugins` 进一步提供可信本地声明式资源包，但明确不建设远程市场、计费、组织治理或动态代码插件。

## 智能体配置侧栏

截图中的右侧配置侧栏包含：

- 节点基础信息：标题、描述、模型、提示词。
- 节点开关：禁用输出、文件理解、并行工具调用。
- 参数：名称、可选、操作。
- 中间件：已添加中间件列表。
- 知识库：召回设置与知识库列表。
- 工具：工具卡片列表。
- 运行策略：失败时重试、备用模型、异常处理。
- 输出结构：content/string 等结构化输出。
- 记忆写入：将结果写入记忆或文件工作区。
- 消息历史、附件和文件变量。

ModelMirror 当前的 `NodeConfig` 已开始从节点表单收敛为 Xpert 式智能体配置侧栏。

`XPERT-STUDIO-PANEL-01` 的第一版映射范围：

- 适用节点：先覆盖 `agent` 与 `workflow_agent`，其他 workflow 节点保留原有表单。
- 已映射分区：节点、参数、提示词/模型、中间件、知识库、工具、运行策略、输出结构、记忆写入。
- 保持真实语义：`workflow_agent` 的模型调用、工具模式、输出变量仍沿用现有 runner；`agent` 的 direct/tool_first 行为不变。
- 草稿配置：禁用输出、文件理解、并行工具调用、参数 JSON、失败重试、备用模型、异常处理、输出 schema、记忆写入等字段先写入 `WorkflowNodeData`，暂不影响执行。
- 边界：不复制 Xpert 侧栏实现代码，不接真实记忆写入、附件变量、内嵌中间件或知识库召回设置。

`XPERT-STUDIO-PANEL-02` 的第一批真实语义：

- 适用节点：仅 `workflow_agent`。
- 已生效字段：`retryOnFailure`、`fallbackModelId`、`exceptionHandling`、`disableOutput`。
- 运行观测：失败尝试、重试、备用模型切换、异常转空输出、禁用输出均写入 RunRegistry checkpoint 摘要。
- 当前不生效字段：`enableFileUnderstanding`、`parallelToolCalls`、`memoryWriteEnabled`、`outputSchemaMode`、`outputSchemaJson`、`nodeParametersJson` 仍为后续能力承载字段。

## ModelMirror Xpert Studio 与发布入口

真实 Xpert 画布的顶部包含预览、环境、功能和发布入口。ModelMirror 第一版不复制其页面或框架，而是将相同产品关系映射为：

- /agents/studio：我的 Xpert 列表，区分草稿、已发布和已归档状态，并显示 revision、当前版本和版本数量。
- /agents/studio/new：创建默认聊天 Xpert，其初始图为 input(user_input) -> workflow_agent(agent_output) -> output(agent_output)。
- /agents/studio/:xpertId：复用现有 WorkflowEditor，通过后端草稿保存而不是替换 classic /workflow 的 localStorage 行为。
- 发布：服务端进行 workflow graph 与聊天契约预检；成功后保存不可变版本快照，后续草稿修改不影响旧版本。
- /agents/xpert/:xpertId/chat：只加载已发布版本，显示 starter prompts、版本选择、SSE 节点轨迹、RunRegistry、checkpoint 与工具审计摘要。

当前边界：已补齐未列出 App、固定版本、访问 token、自动 Handoff、Goal、文件与记忆；仍不实现组织权限、多人协作编辑或 Xpert 的 Angular/NestJS 技术栈。

## ModelMirror Xpert App 映射

- Studio 的“App 与兼容 API”面板负责固定版本部署、回滚、安全策略、配额和凭据。
- `/apps/:appSlug` 对应未列出文本 App，只保留 starter prompts、多轮历史和流式最终回答。
- API 使用 OpenAI `chat/completions` JSON/SSE 形状，但模型和 workflow 始终由部署快照决定。
- 分享凭据只显示一次并经 URL fragment 传递；运行观测仅记录脱敏 prefix、版本和 deployment revision。

## 运维与资源页面

截图中已确认的 Xpert 资源/运维页：

- MCP Runtime 运维：总实例数、活跃、失败、已关闭、筛选器、空态。
- 插件市场：已安装、探索插件市场、标签/来源筛选、插件卡片。
- 技能页：已安装技能、文件树、文件内容、上传/仓库注册。
- 提示词页：工作流列表、创建表单、标签、模板。
- 数据库页：表列表、状态、版本、激活时间、消息。
- 环境页：环境列表与变量表。

ModelMirror 已完成 Workspace Hub、Runtime Ops、版本化 `/prompts` 与声明式 `/plugins`。该 UI 映射以本地可信资源为边界，不复刻 Xpert 的远程插件市场。

## ModelMirror `/runtime` 第一版映射

`/runtime` 已作为 Xpert MCP Runtime 运维页的第一版本地映射。它不替代 `/mcps` 或 `/skills` 的管理能力，只把现有运行态元信息集中到一个可扫读的只读工作台：

- MCP Runtime：读取 `/api/mcp/sessions`，展示 session、状态、工具数量、运行时间与启动命令摘要；无 session 时显示“未找到 MCP runtime”空态。
- Tool Registry：读取 `/api/registry/tools`，展示工具名、来源、描述和参数数量，供 workflow/chat/runtime toolset 观测。
- RunRegistry：读取 `/api/runtime/runs?limit=20`，支持类型和状态过滤；点击 run 后读取 `/api/runtime/runs/{run_id}/checkpoints` 展示 checkpoint 摘要。
- Skill Runtime：读取 `/api/skills/installed`，展示已安装 Skill 摘要并跳转 `/skills`。
- 工作空间入口：`/studio` 的运行资源卡片已指向 `/runtime`，顶层资源导航新增“Runtime 运维”入口。

当前边界：该页只显示元信息，不展示 `.env`、API key、完整 prompt、完整工具输出、embedding、本地文件路径或密钥；MCP 连接、Skill 安装和运行器协议仍由原页面和后端保持不变。

## ModelMirror `/runtime` 第二版映射

`XPERT-RUNTIME-OPS-02` 在第一版只读聚合上补齐了更接近 Xpert 运维台的诊断信息：

- MCP Runtime：KPI 拆分为总数、活跃、异常、已关闭和未知状态，表格增加状态筛选 chips，未知状态不再被误算为活跃。
- RunRegistry：失败和取消的 run 在列表中突出显示，直接展示 `error` 摘要；选中 run 后展示 checkpoint severity 统计、最近 checkpoint 和禁用的“重试待接入”入口。
- 环境与依赖：新增脱敏环境摘要，显示模型网关、OpenRouter、git、node、npm、npx、python 的布尔就绪态。

当前边界：该页仍然不执行重试、MCP 启停、Skill 安装/卸载或环境变量编辑；环境观测不展示 `.env`、API key、本地路径或命令输出。

## ModelMirror `/studio` 第一版映射

`/studio` 已作为 Xpert 工作空间资源首页的第一版本地映射。它不复制 Xpert 页面实现，只对齐资源组织方式：

- 数字专家：链接 `/agents` 与 `/agents/meta-agent`，展示本地智能体数量和样例。
- 工作流：链接 classic `/workflow`，提示当前承载 workflow agent、AgentTask、Handoff、Toolset 与 RunRegistry。
- 知识库：读取 `/api/rag/knowledge_bases`、`/api/rag/pipeline/assets`、`/api/rag/pipeline/artifacts`，展示知识库、FileAsset、Artifact 摘要。
- MCP 工具集：读取 `/api/mcp/sessions` 与 `/api/registry/tools`，展示运行会话与全局工具注册表数量。
- Skill：读取 `/api/skills/installed`，并结合本地 Skill 市场候选数据展示安装状态。
- Prompt/Plugin：`/prompts` 管理版本化命令，`/plugins` 管理声明式本地包；环境仍指向 `/settings`。
- 运行记录：读取 `/api/runtime/runs?limit=8`，展示 workflow/chat/agent/handoff 等最近 run 摘要。

该页采用独立资源加载和软降级策略：任一资源 API 失败时，只在对应卡片显示“暂不可用”，不影响工作空间首页和其他入口。

## ModelMirror `/studio` 第二版映射（历史）

`XPERT-WORKSPACE-HUB-02` 在第一版资源总览上补齐了更接近 Xpert 工作空间首页的操作骨架：

- 顶部“快速创建 / 连接”入口集中展示创建工作流、生成工作流草稿、管理知识库、连接 MCP、安装 Skill 和查看 Runtime 运维，减少用户在资源页之间寻找入口的成本。
- 资源卡片在该轮增加主操作、次操作、标签和计划状态；当时仍显示“待接入”
  和“Xpert 对齐”等标签。
- 该轮把 `API 工具` 与 `数据库` 作为未接通卡片展示。
- 运行观测摘要基于 `/api/runtime/runs?limit=8` 展示最近 run，并按状态和 run_type 做轻量统计；深入排查仍跳转 `/runtime`。

截至 2026-07-28，`/studio` 仍是前端只读聚合页，但用户文案已经改为
“智能体可用”，API 工具通过 Toolset 提供可配置入口，数据库能力通过 Data X
呈现。本文旧标签不得用于恢复当前 UI。

## 对齐风险

- 风险：继续按单个节点补功能，会导致 UI、运行器、文档和资源模型继续发散。 
  缓解：下一步先做工作空间资源总览，再做节点注册表。

- 风险：直接复制 Xpert Angular/NestJS 代码会带来许可证、架构和技术债。  
  缓解：只参考领域模型、交互结构和文案分类，ModelMirror 内用 React/FastAPI 原生实现。

- 风险：Knowledge Pipeline、Toolset、Plugin、Skill 同时推进会互相污染边界。  
  缓解：按资源总览、节点 registry、配置侧栏、知识流水线、Runtime Ops 的顺序推进。

## 下一步建议

Xpert 功能增量到 `XPERT-PLUGIN-PROMPT-03` 为止。下一步只执行 `XPERT-ALIGNMENT-FREEZE-04` 缺口归档，随后切换到 `EVOAGENTX-ALIGNMENT-AUDIT-01`。远程市场、GraphRAG、企业治理与剩余像素级 UI 对齐进入冻结清单。

## ModelMirror `/rag` Draft Config Mapping

At the `XPERT-KNOWLEDGE-PIPELINE-03` increment, image understanding was still
planned/disabled. This was superseded on 2026-07-15 by the optional executable vision
stage documented in [RAG_INTEGRATION.md](./RAG_INTEGRATION.md).
