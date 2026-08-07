<!-- context7 -->
Use Context7 MCP to fetch current documentation whenever the user asks about a library, framework, SDK, API, CLI tool, or cloud service -- even well-known ones like React, Next.js, Prisma, Express, Tailwind, Django, or Spring Boot. This includes API syntax, configuration, version migration, library-specific debugging, setup instructions, and CLI tool usage. Use even when you think you know the answer -- your training data may not reflect recent changes. Prefer this over web search for library docs.

Do not use for: refactoring, writing scripts from scratch, debugging business logic, code review, or general programming concepts.

## Steps

1. Always start with `resolve-library-id` using the library name and the user's question, unless the user provides an exact library ID in `/org/project` format.
2. Pick the best match (ID format: `/org/project`) by exact name match, description relevance, code snippet count, source reputation, and benchmark score. Use version-specific IDs when the user mentions a version.
3. `query-docs` with the selected library ID and the user's full question.
4. Answer using the fetched docs.
<!-- context7 -->

--- project-doc ---

# AGENTS.md - 模镜协作与 Harness Engineering 规则

本文件是模镜仓库内 AI Agent、人类开发者和自动化任务的项目级操作说明。任何代码生成、重构、测试、提交和发布都必须优先遵守本文档。

最后更新日期：2026-08-07
维护人：模镜团队

## 1. 项目边界

模镜 ModelMirror 是 AI 资源浏览与协作平台，当前主要模块包括：

- 前端：React + TypeScript + Tailwind CSS + Vite。
- 后端：FastAPI + httpx + Pydantic。
- 模型调用：优先通过 `LLM_GATEWAY_URL` / `LLM_GATEWAY_KEY` 接入 newAPI 或其他 OpenAI 兼容网关，未配置时回退 OpenRouter。
- 聊天：`/api/chat` 使用 SSE，支持文本、多模态输入和图片生成模型输出。
- 智能体：`/agents` 是智能体入口，`/agents/studio` 管理可保存、版本化发布的 Xpert，`/agents/meta-agent` 是元智能体任务工作台。
- 工作流：`/workflow` 默认使用经典自研 React Flow 画布，`/workflow-native` 是实验线。
- 协作 Runtime：AgentTask、HandoffExecutor、Conversation Goal 与 RunRegistry 共同提供单进程文件型协作闭环。
- RAG：`/rag` 是本地资料库、版本化 Knowledge Pipeline 与检索增强页面。
- Data X：`/datax` 提供文件快照、语义模型、版本化指标、受限分析查询和指标提案审批。
- 上下文：Xpert Chat 支持会话附件、文件理解、显式记忆和待确认记忆候选。
- 运行观测：`/runtime` 聚合 MCP、Tool Registry、RunRegistry、Skill 和脱敏环境状态。
- 设置：`/settings` 内嵌 newAPI 控制台。
- 资源页：模型、智能体、MCP/Toolset、Skill、版本化 Prompt Command、声明式 Plugin、专家团。
- 平台自编写：私有 Xpert 可创建版本化 Xpert/Skill 提案；批准只写草稿，发布与安装必须由用户另行确认。

稳定入口：

- `/models`
- `/agents`
- `/agents/meta-agent`
- `/agents/studio`
- `/agents/goals`
- `/agents/xpert/:xpertId/chat`
- `/apps/:appSlug`
- `/chat/:modelId`
- `/workflow`
- `/workflow-native`
- `/rag`
- `/datax`
- `/mcps`
- `/skills`
- `/studio`
- `/toolsets`
- `/runtime`
- `/settings`

## 2. Harness Engineering 原则

Harness Engineering 的意思是：先搭护栏，再做功能。任何变更都必须有明确范围、验证方式、回退路径和可观测结果。

强制原则：

1. 小步交付：一次只改一个可验证目标。
2. 先读代码：实现前必须确认真实文件、接口和数据结构。
3. 先定义验收：每个任务必须有可运行的 acceptance check。
4. 稳定路径优先：实验功能不得替换主入口，除非用户明确要求并完成验证。
5. 可回退：影响主路径的变更必须写明回退方案。
6. 不泄密：不得提交 `.env`、API Key、token、日志中的敏感信息。
7. 不破坏：不得重置、删除或回滚用户未授权的文件。

### 2.1 证据等级

仓库内的工程判断必须标注其证据等级，不得把推测写成事实：

- **已证实事实**：能指向当前仓库中的代码、配置、测试、命令输出或已批准需求。
- **合理推断**：由多个事实推导，但尚未获得产品或运行证据；必须写明推断依据。
- **建议方案**：尚未实施的设计选择，不得写成已经存在的能力。
- **待确认**：缺少负责人决定或可靠证据，必须保留给用户或产品负责人确认。

目标客户、用户故事、商业目标、SLA、组织权限和合规承诺若没有明确输入，统一标记为“待确认”，禁止 AI 自行补写。

### 2.2 任务开工契约

开始修改前必须完成并记录：

1. 当前分支、工作树状态和未跟踪文件。
2. 与任务直接相关的入口、实现、数据结构和测试证据。
3. 本次目标、允许修改路径、禁止修改路径、公共接口变化和数据迁移影响。
4. 最小验收命令、完整回归命令和失败回退方式。
5. 风险等级及是否涉及密钥、网络、文件、子进程、持久化或公开 API。

默认单批最多修改 5 个文件。超过时必须说明为什么无法安全拆分，并保持单一可验收目标。任务卡模板见 `docs/templates/task-card.md`。

### 2.3 停止条件

出现以下任一情况时停止写入并先处理或请求确认：

- 发现用户未说明的同文件冲突，且无法在不覆盖其改动的前提下继续。
- 需求依赖未知产品规则、目标客户、权限模型、数据保留策略或外部契约。
- 必须读取、打印或提交真实密钥才能继续。
- 需要破坏性迁移、删除持久化数据、重写 Git 历史或扩大公共 API。
- 关键测试失败且失败原因不属于本次改动，无法证明继续修改是安全的。
- 实际变更范围显著超过任务卡，或回退路径不再成立。

### 2.4 受保护路径

| 路径或资源 | 保护原因 | 修改要求 |
| --- | --- | --- |
| `server/.env`、任何 token/key 文件 | 真实凭据 | 只允许本地配置，不得暂存、提交或输出原值。 |
| `server/*/storage/`、`new-api-data/`、上传与索引目录 | 用户持久化数据 | 不纳入提交；删除、迁移或清空必须获得明确授权。 |
| `client/package-lock.json`、`server/requirements.txt` | 依赖与供应链 | 只有任务确需依赖时修改，并记录许可证、版本和回退。 |
| `SECURITY.md`、`.github/workflows/` | 安全报告与自动化证据 | 必须保留私密报告入口、最小权限和真实门禁状态；不得把普通检查冒充 required gate。 |
| `docker-compose.yml`、Dockerfile、sidecar 配置 | 部署与隔离边界 | 必须运行 Compose 配置/构建与安全冒烟。 |
| `/api/chat`、classic workflow runner、Xpert/App 执行链 | 稳定主路径 | 必须有针对性测试、全链路回归和兼容说明。 |
| 已发布 Xpert/Toolset/Knowledge 版本 | 不可变线上快照 | 只创建新版本或显式回滚，不得原地修改。 |

### 2.5 依赖和验证真实性

- 优先使用现有依赖；新增依赖必须说明必要性、固定版本、许可证、镜像体积与安全影响。
- 不得通过关闭类型检查、跳过校验、删除测试或降低断言来制造“通过”。
- 验证结果只允许使用：`通过`、`失败`、`未运行`、`不适用`。
- “通过”必须附实际执行命令和可核对摘要；不得把预计结果写成已运行结果。
- 修改后必须检查 `git diff`、`git diff --check`、暂存文件清单和敏感信息扫描结果。
- 本治理 PR 包含既有 `.github/workflows/multimodal-readiness.yml` 和新增 `.github/workflows/quality.yml`；后者执行前端 typecheck、单元测试与构建、后端测试和 Compose 配置检查。`main` 的 required rules 尚未开启，因此工作流结果是自动化证据，不是强制合并门；只有实际 GitHub run 成功后才能表述为“CI 已通过”。

## 3. 红线

严禁：

- 将真实 `OPENROUTER_API_KEY`、`LLM_GATEWAY_KEY`、`DIFY_API_KEY`、GitHub token 写入仓库。
- 在前端代码中硬编码后端密钥。
- 通过公开 Issue、Pull Request、Discussion、提交信息或 CI 日志报告漏洞、粘贴 secret；安全问题必须按 `SECURITY.md` 私密报告。
- 未测试通过就修改 `/api/chat`、`/workflow`、`/rag` 等主路径。
- 使用不安全批量替换处理中文源码。
- 提交 `node_modules/`、`client/dist/`、日志、临时目录、RAG 存储数据和 Docker 持久化数据。
- 为了“快速修复”禁用类型检查、安全检查或输入校验。

## 4. 推荐工作流

每次任务按以下顺序推进：

1. Inspect：读取相关文件、路由、接口和测试。
2. Plan：列出变更范围和验收命令。
3. Implement：小步修改，避免无关重构。
4. Verify：运行最小必要检查。
5. Document：更新 README、模块文档、术语表或 harness。
6. Commit：使用清晰提交信息。

## 5. 验证命令

前端：

```bash
cd client
npm.cmd run typecheck
npm.cmd run test:run
npm.cmd run build
```

后端语法（按变更模块补充显式文件）：

```bash
python -m py_compile server/main.py server/rag/*.py server/xpert_runtime/*.py server/workflow_native/*.py server/xperts/*.py
```

重点测试：

```bash
python -m pytest server/tests/test_meta_agent.py -q
python -m pytest server/tests/test_xpert_runtime_foundation.py -q
python -m pytest server/tests/test_xpert_runtime_chat.py -q
python -m pytest server/tests/test_xpert_runtime_toolset.py -q
python -m pytest server/tests/test_xpert_publish.py -q
python -m pytest server/tests/test_xpert_handoff_executor.py -q
python -m pytest server/tests/test_xpert_conversation_goals.py -q
python -m pytest server/tests/test_xpert_context.py -q
python -m pytest server/tests/test_xpert_file_memory.py -q
python -m pytest server/tests/test_rag_pipeline.py -q
python -m pytest server/tests/test_rag_pipeline_execute.py -q
python -m pytest server/tests/test_rag_retrieval_v2.py -q
python -m pytest server/tests/test_xpert_app_api.py -q
python -m pytest server/tests/test_xpert_runtime_authoring.py -q
python -m pytest server/tests/test_workspace_skill_drafts.py -q
```

全量后端测试：

```bash
python -m pytest server/tests/ -q
```

Docker Compose：

```bash
docker compose -p modelmirror up -d --build --force-recreate
docker ps
```

健康检查：

```bash
curl http://localhost:8000/api/health
curl http://localhost:5173/models
curl http://localhost:5173/studio
```

容器内 MCP 安装运行时检查：

```bash
docker compose -p modelmirror exec server node --version
docker compose -p modelmirror exec server npm --version
docker compose -p modelmirror exec server npx --version
```

## 6. `/api/chat` 与图片输出规则

聊天链路是高风险主路径。修改以下文件时必须同步验证：

- `server/main.py`
- `client/src/utils/fetchChatStream.ts`
- `client/src/pages/ChatPage.tsx`
- `client/src/utils/extractImages.ts`

规则：

- 不新增不必要的 SSE 事件类型；优先兼容 OpenAI SSE 的 `choices[0].delta` / `choices[0].message`。
- 纯文本模型的流式追加行为不得改变。
- 图片生成模型可能返回 `content` 字符串、多模态 parts、`delta.images`、`message.images`、`image_url.url` 或 `data:image/...`。
- 接收到图片 URL 时统一转换为 `![图片](URL)` 或等价图片卡片，让 `ChatPage` 走已有 Lightbox。
- 用户上传图片的发送逻辑和 `message.images` 展示逻辑不得被破坏。

## 7. workflow-native 与经典工作流规则

workflow-native 是实验线。经典工作流是 `/workflow` 默认入口。任何新增节点、运行器分支或校验规则必须遵守：

- 前端节点类型、后端 `NativeNodeKind`、validate 规则、测试用例和文档必须同步更新。
- `/api/workflow-native/validate` 只做静态校验，不调用模型、RAG、MCP 或外部 HTTP。
- 涉及外部请求、文件读取、模型调用的节点必须有默认关闭或安全降级路径。
- 每新增一类节点至少补一条合法样例和一条非法样例测试。
- 工作流执行面板应按节点聚合 `node_delta`，不得把同一节点的流式片段无限堆成大量独立卡片。
- React Flow Controls 在深色画布上必须保持图标可见；修改 `client/src/index.css` 后需在 `/workflow` 手动检查。

## 8. 元智能体规则

元智能体用于把自然语言目标拆解为可编辑的经典工作流草稿。

- 前端入口：`/agents/meta-agent`。
- 后端接口：`POST /api/meta-agent/generate-workflow`。
- 后端实现放在 `server/meta_agent/`，不得把 planner 逻辑继续堆进 `server/main.py`。
- 元智能体生成的 workflow 必须经过 `workflow_native.validate_workflow_graph` 校验后再返回。
- Docker 镜像必须复制 `server/meta_agent/`，否则容器会因 `ModuleNotFoundError: meta_agent` 无法启动。
- 变更元智能体时必须运行：

```bash
python -m pytest server/tests/test_meta_agent.py -q
```

### 8.1 EvoAgentX Meta Planner V2 护栏

Meta Planner V2 只生成候选 Xpert，并使用 Authoring Proposal 作为唯一持久化来源：

- Capability Snapshot 必须实时来自 Workflow Node Registry、Middleware Registry
  和当前资源 Store；禁止在 Planner 内维护第二份可生成节点或资源 ID 清单。
- Snapshot 只包含安全元数据和规范化 hash，不得包含凭据、完整 Tool Schema、知识正文、
  Plugin/Skill 文件、工具输出或本地物理路径。
- Sandbox、Browser、Client Tools、Automation、Authoring 等高风险能力默认不授权；
  只有用户在当前请求中显式选择后才能进入模型可见 scope。
- 单次候选生成最多三次模型调用：任务规划、能力编译、一次定向修复。禁止无界修复循环。
- 不保存模型隐藏推理；只保存计划摘要、公开假设、选择理由、验证报告和安全统计。
- 生成结果必须依次通过 Pydantic、Registry、workflow validate、特殊绑定边、资源版本、
  冲突/循环和无副作用发布预检。
- Create/Update 候选均写入 `AuthoringProposalStore`；更新必须固定 `base_revision`。
- Planner、人工候选编辑和批准均不得创建发布版本或启动运行。批准只写 Xpert 草稿，
  最终发布必须在 Studio 中显式完成。
- 变更 Meta Planner V2 时至少运行：

```bash
python -m pytest server/tests/test_meta_planner_v2.py server/tests/test_meta_agent.py -q
cd client
npm.cmd run build
```

### 8.2 EvoAgentX Evaluator 护栏

- Dataset 草稿必须使用 revision，Evaluation Run 只能引用不可变 DatasetVersion。
- 基线和候选必须在创建 run 时固定 XpertVersion 或 Authoring Proposal revision、
  workflow checksum、资源版本、模型策略、seed 和预算。
- 评测只能使用 classic runner 的内部只读 capture；不得通过 HTTP 回环或复制第二套
  Workflow Runtime。
- 安全预检必须 fail-closed 拒绝等待、Handoff、Automation、HITL、Memory/Todo/
  Knowledge/Data X/Authoring 写入、Browser、Client Tools、Sandbox 写入和不安全 Plugin。
- Toolset 仅允许固定版本中 `read_only=true` 且 `sensitive=false` 的工具。External
  Xpert 必须固定版本并递归通过同一预检。
- Knowledge 查询必须固定 run 创建时的活动索引版本，不能在运行中漂移到新索引。
- 预算必须覆盖 repetitions、并发、单用例超时、模型调用、工具调用、token 和输出长度。
  usage 缺失时允许保守估算，但报告必须明确标记。
- 重启恢复只能重跑未完成 work item；已完成项、终态工具和报告不得重复生成。
- Evaluator 只能生成报告；不得批准 Proposal、写 Xpert 草稿、发布版本或改变线上资源。
- `server/Dockerfile` 必须复制 `evaluations/`；新增后端包不能只依赖宿主机挂载或 pytest
  的仓库根路径，必须通过重建镜像验证真实 import。
- 修改 Evaluator 时至少运行：

```bash
python -m pytest server/tests/test_xpert_evaluations.py -q
python -m pytest server/tests/test_meta_agent.py server/tests/test_xpert_publish.py -q
cd client
npm.cmd run build
```

### 8.3 EvoAgentX Prompt Evolution 护栏

- Evolution 只能修改固定 revision 下选定的 `workflow_agent.rolePrompt`、
  `promptSuffix` 或单个 Prompt Profile `template`，不得修改图结构、模型、资源或中间件。
- DatasetVersion 必须按固定 seed 拆分优化集和验证集；验证集正文不得进入候选生成上下文。
- optimizer 每代最多一次生成和一次 JSON 修复；候选必须保留模板变量、通过敏感信息与长样例复制检查。
- 候选执行必须调用 Evaluator 的内部固定快照入口，继续使用只读 fail-closed 预检和相同预算。
- 最终 Proposal 只能由独立验证集的非退化门禁创建。目标 revision 漂移时必须标记 stale，禁止写回。
- `prompt_profile_update` 批准后只更新 Profile 草稿；Xpert/Profile 均禁止自动发布。
- RunRegistry checkpoint 不得保存完整 Prompt、用例正文、模型输出全文、隐藏推理、工具结果或密钥。
- `server/Dockerfile` 必须复制 `evolutions/`；修改 Evolution 时至少运行：

```bash
python -m pytest server/tests/test_xpert_evolutions.py server/tests/test_xpert_evaluations.py -q
python -m pytest server/tests/test_xpert_runtime_authoring.py server/tests/test_meta_agent.py -q
cd client
npm.cmd run build
```

### 8.4 EvoAgentX Structure Evolution 护栏

- Structure Evolution 只能接受 `server/evolutions/models.py` 定义的类型化 mutation；
  不得执行模型生成的代码、任意 JSON Patch、任意节点 ID 或任意 binding handle。
- Capability Snapshot、Xpert draft revision、DatasetVersion、资源版本、模型策略和预算必须在
  run 创建时固定。未授权、消失或 Schema 漂移的资源必须在评测前阻断。
- 输入和最终输出节点不可删除或替换；现有 Agent 的 Prompt、模型和输出契约不可由结构
  mutation 修改。新增 Agent 只能使用用户固定的默认模型和编译器生成的安全初始配置。
- 只允许 Evaluator-safe 控制节点、只读资源和安全中间件。Handoff、HITL、Human
  Intervention、HTTP、直接 MCP、Code、Sandbox、Browser、Client Tools、Automation
  与写入能力不得进入候选。
- 特殊绑定边必须由编译器生成固定 `expert / knowledge / toolset / plugin / middleware`
  handle，且不得进入控制流拓扑、变量可达性或普通节点调度。
- 每个候选必须依次通过 mutation schema、授权、Registry、workflow validate、资源循环与
  冲突、无副作用发布预检和 Evaluator 只读预检。静态失败候选可保留安全 issues，但不得
  创建 Evaluation Run 或消耗评测预算。
- Holdout 不得进入 optimizer 上下文。最终晋级除质量和指标门禁外，还必须满足模型调用、
  estimated token、P95 延迟和图复杂度门禁。
- 通过门禁后只能创建 pending `xpert_update` Proposal。目标 revision 漂移时必须标记
  stale，禁止覆盖草稿或自动发布。
- 候选图 API 不得返回 Prompt、middleware config、凭据、工具输出、数据集正文或隐藏推理。
- 修改 Structure Evolution 时至少运行：

```bash
python -m pytest server/tests/test_xpert_structure_evolutions.py server/tests/test_xpert_evolutions.py -q
python -m pytest server/tests/test_xpert_evaluations.py server/tests/test_meta_agent.py -q
cd client
npm.cmd run build
```

### 8.5 Benchmark Catalog 与生成护栏

- Benchmark 产品层只能统一 Manifest、目录和任务来源；不得复制
  `XpertEvaluationStore`、`KnowledgeEvaluationStore` 或 Agent Workspace/Penguin Runtime。
- 内置 Pack 必须是仓库可证明来源的自有合成数据，固定 source、license、版本和规范化
  SHA-256；不得在运行时联网下载或静默更新。
- Agent 标准 Pack 的核心门禁只允许 `exact_match`、`contains` 和 `json_schema`。
  LLM Judge 只能作为附加维度，不能决定标准回归是否通过。
- Catalog Pack 不可编辑。实例化必须在一个 Store 原子写入中创建草稿和完全一致的不可变
  v1；后续草稿修改不得改写 v1，并应把 calibration 标记为 stale。
- 旧 Dataset 缺少元数据时必须按 `origin=manual` 兼容读取，不得要求破坏性离线迁移。
- 后续定向生成必须固定目标 revision/version、能力摘要、模型、资源版本和 Dataset revision；
  生成结果只能进入待审核草稿，不得批准 Proposal、修改线上 Xpert 或自动发布。
- 校准只能验证评分契约、重复、泄漏、难度和基线表现，不得使用当前回答或 Top-K 结果
  改写预先固定的 Gold。
- Benchmark API、报告和 checkpoint 不得保存完整 Prompt、知识正文、工具参数/结果、隐藏
  推理、路径、凭据或未选择的会话内容。
- `server/Dockerfile` 必须复制 `benchmarks/`。每轮在完成非 Docker 验证后停止，等待用户
  确认共享栈空闲，再执行 `--build --force-recreate` 和人工验收。
- 修改 Benchmark Catalog 或 Dataset 元数据时至少运行：

```bash
python -m pytest server/tests/test_benchmark_catalog.py server/tests/test_xpert_evaluations.py -q
cd client
npm.cmd run build
```

## 9. MCP 开发规则

MCP 原生集成属于后端进程管理和工具执行能力，开发时必须：

- 后端代码放在 `server/mcp/` 包内。
- 使用官方 `mcp` Python SDK 的 `ClientSession` 抽象。
- MCP Server 工作目录限制在 `server/mcp/sandboxes/`。
- 校验 `server_command`，禁止 shell 拼接和特殊字符注入。
- 连接必须有超时、断开、重试和清理逻辑。
- 前端从 `client/src/data/mcpProjects.ts` 读取命令，不硬编码命令。
- MCP 一键安装依赖容器内 `npm` / `npx`。服务端 Dockerfile 通过 Node runtime stage 提供这些命令，不应使用 Debian `apt install npm` 的长依赖链。

## 10. Xpert 发布、Goal 与 Handoff 规则

可发布 Xpert 是当前智能体主路径。修改 `server/xperts/`、Xpert Studio、HandoffExecutor 或 GoalCoordinator 时必须遵守：

- 草稿修改不得改变已发布版本；发布版本是不可变 workflow 快照。
- Xpert Chat 和自动 Handoff 必须复用 classic workflow runner，不通过服务自身 HTTP 回环调用。
- 自动 Handoff 只领取显式 `xpert:<slug-or-id>` 目标；普通 Agent 名称继续由人工 Inbox 处理。
- Planner 与目标 Xpert 在 Goal/Handoff 启动时固定发布版本，重试不得静默切换版本。
- 暂停只阻止新任务派发；取消不承诺强杀正在运行的模型请求，迟到结果只能进入审计。
- 文件型 Store 必须使用进程内锁和原子临时文件替换；测试默认使用临时目录。
- 修改该链路至少运行 Xpert Store、HandoffExecutor、Goal、RunRegistry 与对应前端构建测试。

## 11. 文件、记忆与 Knowledge Pipeline 规则

- 会话附件只能在用户显式选择时注入 Xpert、共享给 Goal 或提升到知识流水线。
- 跨 Xpert 的 conversation memory 不得隐式共享；模型提出的长期记忆必须先生成候选并由用户批准。
- API、SSE、checkpoint 和日志不得暴露本地绝对路径、附件全文、完整 prompt、embedding、vector namespace 或密钥。
- Knowledge Pipeline Job 必须固定 draft version 和源快照，按 `load/vision/process/chunk/embed/store` 顺序更新持久化 stage 状态；视觉未启用时 `vision` 必须安全跳过。
- Knowledge Pipeline Graph 只允许编译为现有 Draft，不得直接写向量/FTS5 索引或创建第二套 executor。Job 必须同时固定 `graph_revision` 与 `draft_version`。
- Graph 保存使用乐观 revision；非法 DAG、错误端口、缺失阶段、双分块器、孤立启用节点或过期 revision 不得修改 Draft。旧 Draft 表单更新图配置时必须保留节点坐标。
- Graph 节点预览不得写 Draft、Job、索引或版本，最多返回 20 条截断摘要。Embedding、双索引和检索预览只能返回安全能力/profile；图像预览不得返回原图、Base64 或完整 OCR 正文。
- 图片与扫描 PDF 必须标记为 `pipeline_required`，不得进入 legacy 即时索引。上传必须校验扩展名、声明 MIME、真实格式、10MB 文件限制、损坏文件和 40MP 解压像素上限。
- `image_understanding` 只能作为 `data_source -> image_understanding -> structured_processor` 的可选阶段；启用时必须显式固定视觉模型，并通过 PDFium/Pillow 与网关能力预检。
- 视觉模型输出必须使用严格 JSON 契约并转换为统一 DocumentBlock。逐页缓存必须绑定 source hash、视觉模型和配置 hash；重试只复用成功页，失败页必须重跑。
- 视觉 `strict` 策略任一选中页失败都阻止候选 ready；`continue_on_error` 仅在仍有可索引内容时允许带 warning 继续。checkpoint 只记录来源 ID、页码、状态、耗时和安全错误摘要。
- Processor 必须先产出稳定 `ProcessedDocument / DocumentBlock`。标题、段落、列表、表格、代码块和 PDF 页码不得在分块前退化为无结构字符串；表格和代码块清洗不得破坏内容。
- `general / qa / summary` 模式、模型 ID、生成上限、清洗选项和失败策略必须固定进候选版本 `processor_profile`。QA 索引问题并返回答案与来源，Summary 索引摘要并返回对应原文。
- 逐文档处理状态与产物必须持久化。重试只重跑 source hash 和 processor profile 未命中的失败文档，但 vector/FTS5 索引必须始终从完整成功产物重新原子构建。
- `continue_on_error` 仅在至少一个文档成功时允许生成带 warning 的候选；`strict` 任一文档失败都必须阻断 ready；所有文档失败不得产生版本。
- Processor preview 不得写草稿、Job 或索引，只能返回最多 20 个截断结构块/生成项。生成请求、preview、Job API 和 checkpoint 不得暴露正文全集、问答全文、prompt、路径或密钥。
- 候选索引不得自动上线。必须支持隔离预览、人工激活和历史版本回滚。
- Advanced RAG V2 的向量与 FTS5 索引必须同生共灭；任一索引构建或计数校验失败，必须清理两个候选 namespace，且不得生成 ready 版本。
- 分块、Embedding、检索模式、权重、Top-K、阈值和 Rerank 必须固定在候选版本 profile；预览覆盖不得静默修改版本配置。
- 父子分段只索引子段，返回父段上下文时必须保留命中子段、字符偏移和 CitationAnchor，避免引用漂移。
- Rerank 外部调用必须有超时、严格响应校验和 fail-open；降级只返回 warning，不得记录完整问题、文档正文、工具输出或密钥。
- 离线评估用例必须使用稳定 `source_document_id`、chunk/source block/page 等安全引用，不得依赖候选版本内部 namespace 或版本前缀文档 ID。
- Evaluation Set 修改必须使用 revision 乐观并发；Evaluation Run 必须固定评估集快照、目标版本和检索配置，运行中的编辑不得改变既有结果。
- 评估 checkpoint、RunRegistry metadata 和安全排名只记录 ID、rank、分数、耗时、数量与错误摘要，不得保存完整问题、文档正文、Citation snippet、prompt、工具输出或密钥。
- `required` Promotion Gate 必须校验评估运行成功、知识库与候选版本匹配、评估集 revision 仍为当前版本且门禁通过；`advisory` 模式保留人工激活兼容路径。
- Knowledge Runtime Toolset 只能访问 `workflow_agent.knowledgeBaseIds` 显式声明的 1 至 5 个知识库。模型不得通过工具参数扩展作用域；`toolNames` 仍只过滤 MCP 工具。
- `knowledge_search/get/cite/propose_write` 必须继续经过 Runtime middleware、tool policy、audit 和 checkpoint。工具输出与审计不得保存完整知识正文或写入提议正文。
- 模型写入只能生成 `KnowledgeWriteProposal`，不得直接修改知识库。`/rag/:kbId/inbox` 是唯一正式审批入口；pending 编辑必须使用 revision 乐观并发。
- 批准提议只能创建受管文档和候选 Pipeline Job，不得自动激活。提议候选必须标记 `promotion_required=true`，通过 Evaluation Gate 后才能由 `/promote` 切换活动版本。
- 批准创建 Job 失败必须回滚受管文档并保持提议 pending；拒绝不得创建文档、Job 或候选版本。
- 旧索引不得静默迁移。没有 V2 active version 时继续使用 vector-only legacy 路径，并在能力/诊断信息中明确降级状态。
- 失败、取消或重启恢复不得改变 active version；失败/取消必须清理未完成 candidate namespace。
- 普通 RAG、Chat RAG、`knowledge_retrieval` 与 `knowledge_citation` 统一读取 active version；未激活版本的旧知识库保持 legacy index 兼容。
- Xpert 与本地 Dify 只用于核对领域维度和异常行为；不得复制 AGPL 或许可证不明实现。GraphRAG 在检索评估与 Knowledge Agent 读写审批闭环稳定前保持暂缓。
- 修改知识流水线必须运行 `test_rag_pipeline_graph.py`、`test_rag_processor.py`、`test_rag_pipeline.py`、`test_rag_pipeline_execute.py`、`test_rag_retrieval_v2.py`、`test_rag_vision.py`、`test_rag_evaluation.py`、`test_xpert_runtime_knowledge_toolset.py`、RAG integration 和 workflow citation 回归，并重建 Docker 做 Graph revision、视觉节点预览、逐页恢复、Processor、双索引、混合检索、Rerank、评估门禁、知识审批与版本切换验收。

## 12. 持久化与工作区隔离

- `server/xperts/storage/`、`server/xpert_runtime/storage/`、`server/rag/storage/`、`pipeline_processed/` 与上传目录是运行数据，不得提交。
- 影响文件型 Store 时必须覆盖进程重载、原子写入、损坏/缺失数据降级和并发更新边界。
- 当前主工作区有无关脏改动时，优先创建独立 `codex/` worktree/branch；不得把 APK、根级临时 package 文件、`.env` 或其他轮次改动混入提交。
- Docker 重建前先确认 compose 挂载覆盖 Xpert、Runtime 与 RAG 持久化目录；重启后执行恢复验收。

## 13. Xpert App/API 高风险规则

- App 必须固定不可变 `XpertVersion`；客户端不得指定模型、替换 workflow 或绕过部署版本。
- 分享 token 和 API key 只允许显示一次，服务端只能保存哈希、前缀、状态和脱敏用量。
- URL 分享凭据必须放在 fragment，前端读取后立即从地址栏移除；日志、checkpoint 和错误响应不得记录原始凭据。
- App 工具与 Handoff 默认关闭。工具开启时必须存在并先执行 `tool_policy`；策略未加载时默认拒绝。
- App 动态知识读取默认关闭，只能通过 `allow_knowledge_read` 显式启用；公开 App 永远不能部署启用了 `knowledgeWriteEnabled` 的 Xpert，也不能调用 `knowledge_propose_write`。
- 公开 SSE 只输出最终回答，不转发节点变量、工具结果、内部 trace 或完整 checkpoint。
- App 不开放附件上传，不生成记忆候选；管理 API 外网部署时必须由反向代理保护。
- 修改 App/API 必须运行 `test_xpert_app_api.py`、Xpert publish、Toolset、Memory、Goal、Handoff、RunRegistry 回归和前端构建。
- Docker 验收至少覆盖 JSON/SSE、token 轮换、key 撤销、配额、版本切换、回滚和重启持久化。

## 14. Agent 级中间件规则

- `sourceHandle="middleware-binding" -> targetHandle="middleware"` 是非控制绑定边，禁止计入拓扑、变量可达性和节点调度。
- 一个 `runtime_middleware` 只能绑定一个 `workflow_agent`，禁止同时使用绑定边和普通控制边。
- 模型中间件必须覆盖直答和 ReAct 决策调用；工具仍必须经过 Runtime Toolset、tool policy、audit 和 checkpoint。
- 结构化输出只校验最终答案；修复失败必须回到节点既有 retry、fallback 与 `exceptionHandling`，不得静默绕过。
- 工具选择器只能缩小 policy 过滤后的集合，绝不能恢复 denied 工具。
- Todo 必须按 conversation、goal/step、handoff、workflow task/node 隔离；公共 App 只允许 run 内临时 Todo。
- checkpoint 和日志不得记录上下文正文、派生摘要正文、Todo 正文、schema 修复输入、工具输出或密钥。
- 修改该路径至少运行 `test_xpert_runtime_core_middlewares.py`、`test_xpert_runtime_todos.py`、`test_workflow_native_validate.py`、`test_xpert_context.py` 和前端生产构建。
- HITL 工具顺序必须固定为 allowlist/policy、审批、audit started、Provider、audit finished/failed；`RuntimeInterrupt` 和审批存储错误禁止 fail-open。
- 审批恢复必须使用 revision 与 execution lease；已执行节点不得重跑，同一批准工具最多调用一次，超时不得自动批准。
- 审批 API、safe event、RunRegistry 和 checkpoint 只能保存脱敏参数与摘要；私有 continuation 不得通过公共序列化接口暴露。
- Goal/AgentTask/Handoff 等待审批使用 `waiting_approval`，过期进入 `needs_attention`；公开 App/API 部署必须拒绝 `human_in_the_loop` 和 `human_intervention`。
- 修改 HITL 路径必须额外运行 `test_xpert_runtime_approvals.py`、workflow Agent 恢复用例、Xpert publish、Goal、Handoff、App 与 RunRegistry 回归，并执行容器重启恢复验收。

## 15. Sandbox 与 Skill Runtime 规则

- Sandbox 必须运行在独立 `network_mode: none` sidecar，不得挂载仓库、`.env`、Docker Socket、主服务 Runtime Store 或任何密钥目录。
- 主服务只能通过 Unix Domain Socket 调用 sidecar；不得新增宿主机 TCP 端口或通过服务自身 HTTP 回环执行命令。
- 所有文件路径必须限制在当前 workspace，拒绝绝对路径、`..`、symlink 逃逸、超限文件和跨作用域访问。
- `sandbox_shell` 只接受 argv 数组；禁止 shell 字符串、管道、重定向、命令替换和任意可执行文件。超时必须终止整个进程组，输出必须截断。
- Sandbox 副作用操作必须使用稳定 operation ID。HITL 恢复、页面刷新或容器重启不得重复执行已完成命令或文件写入。
- 执行顺序必须为 allowlist/policy、HITL、audit started、sidecar、audit finished/failed。`require_approval=true` 时静态校验和运行时均须确认 HITL 覆盖 `sandbox_shell` 或 `*`。
- Skill 必须来自用户显式安装的本地包；staging 必须排除 `.git`、symlink、路径逃逸和超限文件。Skill 不得自动执行脚本。
- 产物 API 只能返回逻辑元数据和受控下载，不得暴露物理路径、文件正文全集、命令完整输出、附件内容、prompt 或密钥。
- 公开 Xpert App/API 必须拒绝 `sandbox_files`、`sandbox_shell` 和 `skills_runtime`；普通 Workflow、私有 Xpert Chat、Goal 和 Handoff 才能使用。
- 修改 Sandbox 路径必须运行 `test_xpert_runtime_sandbox.py`、middleware registry、workflow validate、Xpert publish/App、HITL、Goal、Handoff 回归，并执行断网、重启恢复和产物下载 Docker 验收。

## 16. Browser Runtime 规则

- Browser 自动化必须运行在独立 Playwright sidecar，只通过 Unix Domain Socket 接收主服务请求；不得加入应用默认网络、暴露宿主机端口或挂载仓库、`.env`、Runtime Store、Docker Socket和密钥目录。
- egress guard 与 Playwright route 必须同时拒绝 loopback、private、link-local、reserved、multicast、云元数据、Docker service、`.local`、危险协议和混合公网/私网 DNS。任一策略组件异常必须 fail-closed。
- 首次顶层域名访问必须使用 `browser_domain` 持久审批，授权只对当前 session 生效并可撤销；页面跨域链接不得隐式扩大授权范围。
- Snapshot 只能暴露受限 ARIA/role/name 与短期 opaque ref。禁止任意 JavaScript、DevTools、未受控 CSS/XPath、密码、支付卡和验证码自动填写。
- Browser mutating 工具必须使用稳定 operation ID，并由同一 Agent 的 HITL 覆盖。审批恢复、请求重放或容器重启不得重复点击、填写、提交、上传、下载或关闭页面。
- 上传只允许同作用域 Sandbox `inputs/`、已发布 artifact 或同作用域 Browser artifact；下载必须校验大小、文件名、MIME/signature 并登记 Runtime artifact。API 不得暴露物理路径。
- Browser 事件、audit 和 checkpoint 只保存域名、操作、状态、耗时、标题和字节数，不得保存正文、表单值、Cookie、storage state、截图、下载正文、prompt 或密钥。
- 公开 Xpert App/API 必须拒绝 `browser_automation`；普通 Workflow、私有 Xpert Chat、Goal 和 Handoff 才允许使用。
- 修改 Browser 路径必须运行 `test_xpert_runtime_browser.py`、middleware registry、workflow validate、Xpert App、HITL、Sandbox、Goal/Handoff 回归，并执行 sidecar 网络阻断、重启恢复、操作幂等和 artifact 下载 Docker smoke。

## 17. Client Tool 与 Chrome 宿主规则

- Client Tools 只允许私有 Workflow、Xpert Chat、Goal 和 Handoff；公开 Xpert App/API 必须在部署预检和运行时双重拒绝 `client_tools`。
- 配对码必须短期、单次使用；host token 只展示一次，服务端只保存哈希和前缀。token、配对数据、截图和请求 Store 不得提交。
- Chrome 扩展只申请 `activeTab`、`scripting`、`storage`、`alarms` 与精确本地后端 host permission，不得申请 `<all_urls>`。
- 用户必须主动绑定当前标签页；导航到新 origin、关闭标签页或解绑后授权必须失效。
- 页面修改工具必须按 `Tool Policy -> HITL -> client dispatch -> audit` 执行。审批、Store 或 schema 校验失败不得 fail-open 派发。
- 请求结果必须匹配 request、operation、tool-call 和 host 标识；读取可重放，执行中的修改动作断线后必须进入 `uncertain`，不得自动重放。
- Snapshot/ref 不得支持任意 JavaScript、DevTools、CSS/XPath 或跨 origin 复用；敏感表单字段不得读取或自动填写。
- 修改 Client Tools 至少运行 `test_xpert_runtime_client_tools.py`、workflow validate、Goal/Handoff/App 回归、扩展 manifest/schema 检查和前端生产构建。

## 18. Automation Runtime 规则

- Automation 只能固定存在且已发布的 XpertVersion；后续草稿或新发布版本不得静默改变既有调度。
- once、interval 和五字段 Cron 必须统一通过 `AutomationStore` 计算；Cron 必须校验 IANA 时区。不得在前端自行计算下一次运行。
- occurrence ID、execution lease、重叠/误触发策略、预算、重试和死信必须保持幂等；容器重启不得重复派发已完成 occurrence。
- HITL/Client Tool 等待必须继续同一个 AutomationExecution；恢复不得重复调用已完成的有副作用工具。
- `scheduler` Runtime 工具只能管理当前私有已发布 Xpert 的自动化，且必须经过 tool policy、middleware 和 audit。
- Ralph Loop 必须有迭代和输出预算、无进展检测及严格验证；失败必须进入节点既有 retry/fallback/exception handling，不能伪造成功。
- Knowledge Writer 只能创建 pending proposal，禁止绕过 Knowledge Inbox、Pipeline、Evaluation Gate 或 Promotion；Plugin Hook 只能执行已安装 Skill 的显式 manifest，并限制在无网 Sandbox argv。
- 公开 Xpert App/API 必须拒绝 `scheduler`、`ralph_loop`、`knowledge_writer` 和 `plugin_hooks`。
- 修改 Automation 路径至少运行 `test_xpert_runtime_automations.py`、`test_xpert_runtime_ralph_loop.py`、`test_xpert_runtime_plugin_hooks.py`、workflow Agent/validate、App、HITL/Client continuation、RunRegistry 回归和前端生产构建。
- Docker 验收必须覆盖固定版本、Cron 时区、暂停恢复、预算、重试/死信、等待续跑、知识提议、离线 Hook 和容器重启持久化。

## 19. 类型化文件记忆规则

- Xpert 级长期记忆必须通过 `XpertFileMemoryStore` 写入类型化 Markdown；会话级 Memory 继续留在 `XpertContextStore`，不得混迁移。
- `MEMORY.md` 是派生摘要索引，不是正文真源。正式正文只允许 `user / feedback / project / reference` 四类，文件名必须使用稳定 ID，不得使用用户标题或暴露真实路径。
- 旧 Xpert Memory 采用幂等懒迁移并保留 `memory_id`；无法分类时使用 `project + legacy-import`。迁移不得触碰会话级记忆。
- 记忆编辑、归档、候选修改和审批必须校验 revision。`update` 候选还必须校验 `target_memory_id + base_revision`，冲突不得 fail-open。
- 模型自动写回只能创建候选，不能直接修改正式记忆。公开 App 即使允许 Xpert Memory，也只能读取，禁止候选生成和写回。
- Goal/Handoff 只能读取目标 Xpert 自身长期记忆，不得隐式共享来源 Xpert 的会话记忆。
- recall/audit/checkpoint 只记录 ID、类型、数量、长度、策略、耗时和错误摘要，不得记录正文、prompt、物理路径或密钥。
- 修改该链路至少运行 `test_xpert_file_memory.py`、`test_xpert_context.py`、workflow validate、App policy、前端生产构建和敏感信息扫描。

## 20. Office 实时自动化规则

Office 自动化是高风险客户端副作用路径。修改 `server/xpert_runtime/office_toolset.py`、Client Tool Store/API、`server/office_addin/` 或 `server/office_host/` 时必须同步验证 Client Tool、HITL 与 App 预检。

强制规则：

- Office Host 必须由用户主动绑定当前文档，Host 类型和 schema hash 不匹配时 fail-closed。
- 所有文档修改工具必须经过同一 Agent 的 HITL；删除还要求配置许可和 `confirm=true`。
- 修改操作执行中断线必须进入 `uncertain`，不得自动重放；稳定 operation receipt 用于避免恢复后重复修改。
- Task Pane 不得读取或持有模型密钥、Runtime Store、本地路径或其他文档内容。
- 证书、私钥、Host token、Office 文档和操作结果数据不得提交。
- 公开 Xpert App/API 必须拒绝 `office_automation`。
- 至少运行 Office、Client Tool、workflow validate、App preflight 重点测试、前端构建和带 `office` profile 的 Docker smoke。

详细契约见 `docs/XPERT_OFFICE_AUTOMATION.md`。

## 21. Data X 高风险路径

- CSV、XLSX、Parquet 必须先固定为不可变 SHA-256 快照，再导入项目隔离的 DuckDB；导入失败不得切换 ready 状态。
- Data X API 和 Runtime Toolset 禁止接受任意 SQL。查询必须从已验证的指标、字段、过滤和排序 DSL 编译，并使用参数绑定处理值。
- 语义模型最多包含 5 个实体，只允许显式 `inner` / `left` 等值连接；字段必须属于已声明实体和来源快照。
- 指标草稿不得改变线上语义。只有显式发布产生的不可变 `IndicatorVersion` 可供 Agent、Goal、Handoff、Automation 和 App 查询。
- 派生指标表达式只允许已发布指标 code、数字、括号和 `+ - * /`；必须拒绝函数、属性访问、循环依赖和除零。
- `datax_indicators` 必须绑定 `workflow_agent`、启用 Runtime 工具模式，并显式限制项目和模型范围。模型不能通过参数扩大 scope。
- Agent 只能创建指标提案；批准只生成草稿，仍需人工预览和显式发布。
- 公共 App 的 Data X 默认关闭。启用 `allow_datax_read` 后仍只允许固定 scope 内的已发布指标；提案、原始明细和文件导出永远禁止。
- API、audit 和 checkpoint 不得保存上传数据、完整查询结果、DuckDB 路径、展开 SQL、密钥或未脱敏工具输出。
- 修改 Data X 必须运行 `server/tests/test_datax.py`、workflow validate、Xpert App preflight、前端生产构建和容器重启持久化验收。

## 22. Workflow 资源绑定与 EvoAgentX 复用规则

- `external_xpert`、`knowledge_base` 与 `toolset_resource` 必须通过专用 binding handle 连接 `workflow_agent`；资源边不得进入控制流拓扑、变量传播或节点调度。
- 同一资源节点只能绑定一个 Agent，且不得混用控制流边。新增资源类型必须同步更新 schema、validate、topological order、runner、registry、前端 handle 和专门测试。
- 外部 Xpert 草稿可跟随当前发布版，但 Xpert 发布时必须解析为具体不可变版本；运行时禁止自身调用、协作循环和超过 4 层嵌套。
- 外部 Xpert 必须复用 classic runner，不得通过本服务 HTTP 回环；调用继续经过 Tool Policy、HITL、Audit、middleware 和 RunRegistry 父子链。
- 知识资源只能访问显式绑定的知识库和活动索引；无活动版本时安全返回空结果，不得回退到其他知识库。审批写入仍走 Knowledge Inbox。
- 公开 Xpert App 必须拒绝 `external_xpert`；知识资源继续受 `allow_knowledge_read` 与 Tool Policy 双门禁。
- 修改资源节点至少运行 `test_workflow_resource_nodes.py`、workflow validate、Xpert publish、Knowledge Toolset、App preflight 和前端生产构建。
- MCP Toolset 草稿与发布版本必须分离。Xpert 发布时 `latest` 必须解析为具体 Toolset 版本；新发现工具、草稿别名和 Schema 变化不得静默扩展已发布 Xpert。
- Stdio Toolset 只接受 argv，工作目录必须位于 MCP sandbox；远程 Toolset 默认阻断私网、回环、元数据和 URL credentials，旧 SSE 仅作兼容。
- Toolset Header、环境变量和 Provider key 只能引用加密 Credential ID。普通 API、版本 JSON、日志、audit 和 checkpoint 不得返回明文；主密钥错误时必须 fail-closed。
- 管理侧工具测试也必须经过参数 Schema、Tool Policy 和 Audit。固定版本遇到工具消失或必填参数不兼容漂移时必须拒绝调用。
- OpenAPI/OData Toolset 只能从固定 base URL 与编译后的 operation 执行。禁止任意 URL、任意 HTTP 模板、远程 `$ref`、跨域凭据重定向和未经校验的 OData `$filter`。
- API Toolset 默认阻断回环、私网、link-local、reserved、云元数据和 URL credentials；`trusted_private` 只允许可信管理面显式选择，不能由模型参数开启。
- API Key、Bearer、Basic 与 OAuth2 client credentials 均只能引用 Credential ID。OAuth token endpoint 必须经过同一网络策略，不得把 token 或认证 header 写入响应摘要。
- OpenAPI/OData 写操作默认 `requires_approval=true`。管理测试需显式确认；已发布 Xpert 必须由同一 Agent 的 HITL 覆盖，运行时再次检查，任何异常不得 fail-open。
- Toolset 工具语义必须固定在不可变版本中。`sensitive` 必须 HITL；`terminal` 成功后直接结束 Agent；conversation Tool Memory 只允许私有 Xpert 会话并保存受限脱敏摘要。
- 并行工具批次只允许 `read_only + parallel_safe + !sensitive + !terminal`，并逐调用经过 policy、audit 和 checkpoint。必须同时限制并发数、总调用数、决策轮次和 External Xpert 嵌套深度。
- 内置 Provider 必须复用现有 Store 与执行器。Todo 不得创建第二套 Todo Store；Knowledge、Memory 和 Data X 不得复制已有 Provider 逻辑。
- 公共 App Toolset 必须固定已发布版本，要求 `allow_tools`、Tool Policy，以及全部工具显式 `public_app_allowed`、只读、非敏感、非 conversation memory。凭据只在服务端解析。
- 修改 Toolset Runtime 至少运行 `test_toolset_semantics.py`、`test_toolset_store.py`、`test_toolset_service.py`、`test_toolset_api.py`、`test_toolset_api_compiler.py`、`test_toolset_api_runtime.py`、`test_workflow_toolset_resource.py`、MCP/Toolset/Workflow/Xpert/App 回归和前端生产构建。

### Xpert 版本化会话功能

- 开场白、问题建议、会话标题/摘要、记忆回复、文件策略和 TTS/STT 必须固定进不可变 `XpertVersion.features`。草稿更新不得改变已发布版本的聊天行为。
- 会话摘要必须复用 `context_compression`，保留原消息，并只持久化派生摘要、revision 和覆盖边界；不得把完整消息正文写入 checkpoint。
- 文件能力关闭时，历史附件仍可查看，但不得注入 Xpert、Goal 或知识候选。扩展名和每轮文件数必须在前后端双重校验。
- TTS/STT 只能使用模型注册表中显式选择的 speech/transcription 模型和既有 LLM Gateway/OpenRouter 兼容配置；不得在前端或源码硬编码供应商密钥。
- 记忆直答必须满足明确的高置信阈值和作用域检查；不确定时继续走原模型执行，不得静默返回模糊记忆。
- `XpertAgentConfig.max_concurrency` 与 `recursion_limit` 约束整个 Xpert 执行树。节点级 `maxToolConcurrency`、`maxToolCalls`、`maxToolDepth` 和 `maxIterations` 只能收紧局部工具循环。
- 修改这些能力至少运行 `test_xpert_agent_features.py`、`test_xpert_publish.py`、`test_xpert_context.py`、`test_xpert_file_memory.py`、workflow agent、Toolset/App 回归和前端生产构建。

### Xpert 冻结与 EvoAgentX 来源护栏

- Xpert 功能面已在 `main@93e5cc38becc7fe4f89efa113310698e6eda1971` 冻结。当前状态以 `docs/XPERT_FREEZE.md` 为准；截图差异、目录补齐和像素对齐不得单独启动功能轮次。
- 冻结后只接受安全修复、致命缺陷、数据兼容和已实现闭环回归。延期能力必须先修改冻结文档并获得明确任务授权。
- EvoAgentX 的唯一复用基线是官方 `v0.1.4@aad19b912f640161ea07e8904d9237cd34fde5f1`。本地无 Git 副本只能用于差异验证，不得作为提交来源。
- EvoAgentX 只能逐文件选择性移植。每个文件必须在 `docs/EVOAGENTX_AUDIT_V014.md` 或后续台账中记录官方相对路径、commit、SHA-256、许可证、第三方依赖、`reuse/adapt/rewrite/reject` 判定和本地测试映射。
- 不得把整个 EvoAgentX 包、Provider、RAG、Storage、HITL、Memory 或 Tool Runtime 作为运行依赖引入。复用代码必须保留原版权、MIT License 和 NOTICE。
- Planner 只能生成带 revision 的候选 Xpert 草稿，并依次通过 workflow validate、资源存在性、循环检测和发布预检；不得运行、发布或覆盖人工草稿。
- Evaluator 的候选与基线必须固定同一数据集 revision、XpertVersion、模型、资源版本、输入、随机参数和调用/token/工具/并发/超时预算。
- Optimizer 只能产生候选草稿、变更说明与评估报告。任何 Prompt 或结构进化都必须人工批准后才能写入草稿，发布仍是独立显式操作。
- 审计和评测不得读取或记录 `.env`、API key、Runtime Store 物理路径、完整工具结果、公开 App token 或不必要的完整用户内容。

## 23. Prompt Command 与声明式 Plugin 规则

- Prompt Profile 草稿与不可变版本必须分离。Xpert 发布时必须把 `latest` 解析为具体 Profile 版本；后续草稿修改不得改变已发布 Xpert。
- 第一版模板只允许 `{{args}}`。斜杠命令必须在模型调用前解析；未知命令不得调用模型，`//text` 必须按普通 `/text` 消息处理。
- 会话可以保存用户输入的原始命令，但模型只能接收渲染后的当前任务。checkpoint、audit 和 App manifest 不得暴露模板正文。
- `plugin_resource` 必须通过 `plugin-binding -> plugin` 绑定单个 `workflow_agent`；该边不得进入拓扑排序、变量传播、可达性检查或节点调度，也不得与控制流边混用。
- Plugin ZIP 只能承载声明式 Prompt、Skill 文件、固定 Toolset 引用和已注册中间件预设。禁止动态 Python/Node 模块、初始化脚本、绝对路径、`..`、symlink、`.git` 和可疑隐藏路径。
- Plugin Toolset 引用必须固定 `toolset_id + version + schema_hash`，且不得包含 Credential。schema 漂移、工具名、Prompt 别名或中间件冲突必须 fail-closed。
- Plugin Skill 必须命名空间化并继续通过 `skills_runtime` 和无网 Sandbox 显式执行；导入或发布 Plugin 不得自动执行 Skill 脚本。
- 公共 Xpert App/API 必须拒绝 `plugin_resource`。仅直接绑定、固定版本且 `public_app_allowed=true` 的 Prompt Profile 可作为 App 命令，manifest 只返回安全命令元数据。
- `server/Dockerfile` 必须复制 `prompts/` 与 `plugins/`；新增后端包时必须用重建后的镜像验证真实 import，不能只依赖宿主机测试。
- 修改该路径至少运行 `test_xpert_plugin_prompt.py`、`test_workflow_native_validate.py`、`test_workflow_resource_nodes.py`、`test_workflow_toolset_resource.py`、Xpert publish/App 回归和前端生产构建。
- Plugin 上传包、Prompt/Plugin Store、生成 Skill、`.env`、API key、构建产物和 APK 不得提交。

详细契约见 `docs/XPERT_PLUGIN_PROMPT.md`。

## 24. Git 规范

### 自编写高风险路径

- `xpert_authoring` 与 `skill_creator` 只能创建提案，不得加入发布、安装、删除或直接覆盖工具。
- Xpert 更新必须固定 `base_revision`；冲突时保留人工草稿并将提案标记为 `conflict`。
- Skill 提案批准后只能进入 Workspace Skill 草稿；显式安装前不得出现在已安装 Skill Runtime。
- Skill 文件只能位于 `scripts/`、`references/`、`assets/` 或 `agents/openai.yaml`，必须拒绝绝对路径、`..`、隐藏路径、`.git` 与 symlink。
- 公开 Xpert App/API 必须依据 middleware registry 的 `app_policy=forbidden` 阻断自编写能力。
- 变更相关实现必须运行 authoring、Skill draft、workflow validate 和 App preflight 测试。

提交前必须确认：

```bash
git status --short
git diff --cached --name-only
```

提交信息：

```text
type: 简短中文说明
```

示例：

```text
fix: 修复图片生成模型输出显示
docs: 更新聊天图片输出 harness
feature: 添加 MCP stdio 客户端管理器
```

## 25. 交付格式

最终回复应包含：

- 改动摘要
- 文件列表
- 验证命令与结果
- 未完成项或阻塞
- 风险和回退建议
