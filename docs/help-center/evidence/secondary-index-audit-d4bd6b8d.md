# 帮助中心二级索引事实审计

## 审计范围

- 基线：`d4bd6b8dc151b79001efc0bbe3f06e22716dae0a`
- 日期：`2026-08-26`
- 预览：隔离 Compose 项目 `modelmirror-help-r1-baseline-d4bd6b8d`
- 前端：`http://127.0.0.1:15296`
- 范围：现有八个一级模块下的二级功能。
- 不在本轮处理：工作流节点、模型条目、MCP 单个工具、Skill 条目、Prompt 分类、RAG 流水线步骤、Data X 项目内部页面等三级内容。

## 归类规则

1. 二级功能必须对应一个独立用户任务、稳定页面、页面一级页签或明确的实验/受限边界。
2. 一级模块页本身承担模块总目录，不再使用“模型概览”“Agent 概览”“运维概览”等伪二级索引。
3. 以预览器可见页面为主要事实；项目文档只用于确认稳定、实验、管理和安全边界。
4. 有路由但当前未在产品入口展示、且直接页面明确显示未启用的功能，不进入默认用户索引。
5. 首页每个模块仍只显示两个常用二级入口；点击一级模块后显示该模块的完整二级目录。

## 归类结果

### 模型

预览事实：`/models` 可见文本、图片、文件、音频、视频输入筛选，以及图片识别、图片生成/编辑、视频生成、实时语音、语音转写、语音合成和音乐生成任务筛选；模型卡片提供状态、能力、价格、比较和进入工作区入口。`docs/ARCHITECTURE.md` 与 `docs/REPOSITORY_FACTS.md` 确认聊天会按任务进入文本或多模态工作区。

二级索引：

1. 查找、筛选与比较 → `/models`
2. 智能路由 → `/models`
3. 文本与文件 → `/models`
4. 图片理解 → `/models`
5. 图片生成与编辑 → `/models`
6. 视频分析与生成 → `/models`
7. 实时语音 → `/models`
8. 语音转写 → `/models`
9. 语音合成 → `/models`
10. 音乐生成 → `/models`
11. 进入聊天与添加内容 → `/models`

不展开：具体模型、供应商、价格档位、输入格式和各工作区内部控件。

### Agent

预览事实：`/agents` 直接展示智能体发布中心、AI 工作流生成器、自动化任务、长期任务、Data X 数据分析和多智能体协作；独立页面 `/agents/evaluations`、`/agents/evolution` 分别提供评测与受控优化。`docs/ARCHITECTURE.md`、`docs/META_AGENT.md`、`docs/XPERT_RUNTIME.md`、`docs/EVOAGENTX_EVALUATOR.md` 和 `docs/EVOAGENTX_EVOLUTION.md` 确认这些页面各自的发布、执行和审批边界。

二级索引：

1. 寻找现成 Agent → `/agents`
2. Agent Studio → `/agents/studio`
3. AI 工作流生成器 → `/agents/meta-agent`，Beta
4. 自动化任务 → `/agents/automations`，Beta
5. 长期 Goal → `/agents/goals`，Beta
6. Agent 评测 → `/agents/evaluations`
7. Agent 受控优化 → `/agents/evolution`
8. Data X 数据分析 → `/datax`
9. 专家团 → `/expert-team`

边界：`/agents/workbench` 当前直接显示“Coding Worker 尚未启用”，且 `/agents` 不展示对应能力卡，本基线不放入默认二级索引。Agent 聊天、App/API 部署、Handoff 和具体评测集属于上述功能内部内容，本轮不展开。

### MCP

预览事实：`/mcps` 有“工具货架、已连接注册表、MCP Hub”三个一级页签；`/toolsets` 是独立 Toolset Runtime。`docs/MCP_INTEGRATION.md` 明确 `/mcps` 负责目录发现和预置适配器连接，`/toolsets` 负责通用 MCP、OpenAPI、OData 和内置 Provider 的版本化工具集。

二级索引：

1. 工具货架 → `/mcps`
2. 已连接注册表 → `/mcps?view=registry`
3. MCP Hub → `/mcps?view=hub`，受限
4. Toolset Runtime → `/toolsets`，管理功能

不展开：具体 MCP 项目、工具 Schema、凭据字段、Transport、OAuth 步骤和 Toolset 内工具。

### Skill

预览事实：`/skills` 有“技能市场、已安装、本地导入、工作区草稿、待审提案”五个一级页签，并提供“创建 Skill”和“重排治理”独立入口。`docs/SKILL_INTEGRATION.md` 与 `docs/SKILL_EXPERIENCE_AUDIT.md` 确认 Creator、导入、版本恢复、提案审批和语义重排治理互不自动越权。

二级索引：

1. Skill 市场 → `/skills`
2. 已安装 Skill → `/skills?tab=installed`
3. Skill Creator → `/skills/create`
4. 本地导入 → `/skills/import`
5. 工作区草稿 → `/skills?tab=drafts`
6. 待审提案 → `/skills?tab=proposals`
7. 语义重排治理 → `/skills/rerank`，管理功能

不展开：具体 Skill/SkillSet、安装来源、版本记录、Creator 阶段、评测案例和 Hook。

### 提示词

预览事实：`/prompts` 明确分为“模板库”和“Prompt Command”；`/plugins` 是独立声明式 Plugin 工作台。`docs/ARCHITECTURE.md`、`docs/FRONTEND.md` 与 `docs/REPOSITORY_FACTS.md` 将 Prompt 和 Plugin 都列为当前资源能力，并明确 Plugin 不加载服务端动态代码。

二级索引：

1. 模板库 → `/prompts`
2. Prompt Command → `/prompts?view=commands`
3. Plugin 资源包 → `/plugins`，管理功能

不展开：模板分类、具体模板、Prompt 版本、评测、演进和 Plugin 内资源绑定。

### 运维

预览事实：`/runtime` 的三个可见内容区为“运行记录、客户端宿主、运行资源”；运行资源内部再切换 MCP 连接、工具、Skill 和环境依赖。页面明确说明这里只集中诊断，管理操作仍在对应模块完成。

二级索引：

1. 运行记录 → `/runtime`
2. 客户端宿主 → `/runtime`
3. 运行资源 → `/runtime`

不把 MCP 连接、工具、Skill、环境依赖再提升为二级索引；它们是“运行资源”内部页签，属于本轮冻结的三级内容。

### 工作台与设置

预览事实：`/studio` 的工作台入口和快速创建区提供经典工作流、RAG、Coding、系统设置和本地数据表；`/workflow` 是稳定经典画布。`docs/ARCHITECTURE.md`、`docs/FRONTEND.md`、`docs/AGENT_TABLES.md` 和 `docs/CODING_AGENT_INTEGRATION.md` 确认这些页面的用途与实验边界。

二级索引：

1. 经典工作流 → `/workflow`
2. RAG 知识库 → `/rag`
3. 本地数据表 → `/data-tables`
4. Coding → `/coding`，当前基线未启用代码助手
5. 系统设置 → `/settings`，管理员功能

边界：`/studio` 是聚合入口，不重复作为二级功能。RAG Pipeline、Evaluation、Inbox，数据表 Schema/记录和工作流节点都属于三级内容，本轮不展开。

### 实验功能

预览事实：`/workflow-native` 明确显示“隔离实验入口”并只做图结构校验；访问 `/studio/science` 会回到 `/models`；`/matrix-oasis` 当前显示“世界仍在生成”的预告页。`docs/ARCHITECTURE.md`、`docs/workflow-native-design.md` 和 `README.md` 确认 Workflow Native 不替换经典工作流。

二级索引：

1. Workflow Native → `/workflow-native`，实验
2. Science → 当前无独立可用入口，实验
3. 矩阵绿洲 → `/matrix-oasis`，实验

不展开：Native 节点、校验问题、Science 未来能力和矩阵绿洲内部场景。

## 数量与首页约束

- 一级模块：8 个，数量和顺序不变。
- 二级索引：45 个。
- 首页默认展示：每个模块 2 个，共 16 个；完整列表只在一级模块页和统一左侧目录中展开。
- 专家团保持 Agent 二级索引；运维保持独立一级模块。

## 最新主线交叉检查

审计结束时，本工作树比本地记录的 `origin/main@cc49136c` 落后 10 个提交。只读比较确认 `client/src/App.tsx` 没有变化，因此本轮二级路由集合没有发现新增或删除；交叉文件集中在 MCP Hub、RAG Pipeline 和工作流内部能力。

- RAG Pipeline 和工作流节点属于本轮明确冻结的三级内容，不改变当前二级归类。
- MCP Hub 已从 R3A 前进到受控 Remote OAuth Runtime R3B，页面状态和说明会变化，但“工具货架 / 已连接注册表 / MCP Hub / Toolset Runtime”四项二级结构不变。
- 准备 PR 前仍必须从最新 `origin/main` 重建隔离预览，并重新核对 MCP Hub 文案、状态与受限边界；本文件不能替代该门禁。
