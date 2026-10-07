# 元智能体集成说明

最后更新日期：2026-08-31
维护人：模镜团队

## 定位

元智能体工作台用于把自然语言目标拆解为可编辑的经典工作流/Xpert 草稿。Meta Planner V2
已经可以从实时 Registry 编译 `workflow_agent`、资源绑定、中间件和发布预检所需配置；
旧生成器仍保留用于兼容经典工作流导入与既有 AgentTask/Handoff 操作。

当前实现是 **Capability Snapshot V10 + Graph IR V3 单写、Typed IR V2 双读**。后续升级已经锁定为
“V3 十轮 + V4 轮次待定”，唯一方向文档是
[META_PLANNER_V3_V4_ROADMAP.md](./META_PLANNER_V3_V4_ROADMAP.md)。V3 先补齐
Graph IR、无头编排、节点 Adapter、效果语义和评测，再逐类开放真实节点；V4 只有在
V3 收口审计后才能确定轮次。路线文档描述目标，不表示对应能力已经实现。

早期实现参考 EvoAgentX 的 `goal -> sub_tasks -> inferred edges` 规划形态，归因保留在 `server/meta_agent/NOTICE.md`。Xpert 已在 `main@93e5cc38becc7fe4f89efa113310698e6eda1971` 冻结，EvoAgentX 官方 `v0.1.4@aad19b912f640161ea07e8904d9237cd34fde5f1` 的源码审计也已完成。Meta Planner V2 是冻结后的第一项功能增量：生成当前完整节点、资源绑定与中间件，而不是继续输出过时的 `agent` 长链。

## 功能范围

- 前端入口：`/agents/meta-agent`。
- 后端生成接口：`POST /api/meta-agent/generate-workflow`。
- 后端模块：`server/meta_agent/`。
- 任务运行时：复用 `POST /api/runtime/agent-tasks`、`GET /api/runtime/agent-tasks`、`GET /api/runtime/agent-tasks/{task_id}`、`POST /api/runtime/agent-tasks/{task_id}/cancel`。
- Handoff Inbox：任务工作台会查询 `GET /api/runtime/agent-tasks/{task_id}/handoffs` 展示选中任务的移交记录，并通过 `GET /api/runtime/agent-handoffs?status=&target_agent=&limit=20` 提供 Handoff Inbox Beta；pending 移交支持手动接受/拒绝，accepted 移交支持填写完成结果并提交。
- 输出目标：生成 `WorkflowDefinition`，可导入经典自研画布、保存为 Xpert 草稿并通过 `/api/workflow/run` 执行。
- 校验路径：生成后的工作流会调用 `workflow_native.validate_workflow_graph` 做静态校验。

## 实现边界

- planner、schema 和 prompt 放在 `server/meta_agent/`，不要继续堆进 `server/main.py`。
- 生成接口依赖模型网关；测试必须 mock `collect_chat_completion_text`，不能请求真实模型。
- 前端负责提交目标、展示任务拆解、创建 AgentTask 记录、展示任务工作台和导入经典画布。
- HandoffExecutor 与 GoalCoordinator 已能执行固定版本 Xpert；元智能体生成器本身仍只负责规划和草稿，不直接发布、不静默调度，也不具备自进化评估闭环。
- V2 已覆盖 `external_xpert`、`knowledge_base`、Agent 级 middleware、Toolset、
  Plugin 与 Prompt Profile；任意新增节点或资源必须先进入后端 Registry，不能只修改
  Planner prompt。
- Docker 镜像必须复制 `server/meta_agent/`，否则 `server/main.py` 导入会失败。

## Meta Planner V2 契约

`EVOAGENTX-META-PLANNER-01` 的实现边界在本轮审计中固定如下，后续功能
PR 不得重新定义另一套 Planner Runtime。

### 能力来源

- 节点必须来自后端 Workflow Node Registry 和 `SUPPORTED_NODE_KINDS`。
- Agent middleware 必须来自 Runtime Middleware Registry。
- External Xpert、Knowledge、Toolset、Plugin 和 Prompt 必须来自各自只读资源
  options API 或对应 Store service，不能写死 ID 或维护测试专用副本。
- 发布配置必须使用当前 Xpert 草稿/版本 schema。

### 输出形态

- 输出一个带 `draft_revision` 的候选 Xpert 草稿和完整
  `WorkflowDefinition`。
- 控制流边、资源绑定边和 middleware 绑定边必须显式区分。
- 资源边通过既有 `targetHandle` 契约表达，不参与拓扑排序、变量传播或节点调度。
- 输出计划摘要、关键假设、资源选择理由、warning 和结构化 validation issues。
- 不输出或持久化模型隐藏推理过程。

### 校验顺序

1. Pydantic/schema 校验。
2. `validate_workflow_graph` 静态校验。
3. 资源存在性、状态和发布版本检查。
4. External Xpert 自调用、协作循环和最大深度检查。
5. Toolset/Plugin/Prompt schema hash、别名和工具冲突检查。
6. Xpert 发布预检，但不实际发布。

### 写入和执行边界

- Planner 只能创建候选草稿，不能运行、发布或覆盖当前人工草稿。
- 保存候选必须使用 revision 冲突保护。
- 候选失败时保留原草稿，并返回可操作 issues。
- 模型、资源、Toolset 和 Plugin 的选择必须在候选中可追溯。
- 测试必须 mock 模型和 Registry，覆盖确定性生成、非法资源、绑定边和发布预检。

## 请求示例

```bash
curl -X POST http://localhost:8000/api/meta-agent/generate-workflow \
  -H "Content-Type: application/json" \
  -d "{\"goal\":\"为一个新产品发布生成包含需求拆解、风险评估和上线清单的工作流。\",\"model_id\":\"deepseek/deepseek-chat\",\"temperature\":0.2,\"max_tasks\":5}"
```

## 验证命令

```bash
python -m py_compile server/main.py server/meta_agent/*.py
python -m pytest server/tests/test_meta_agent.py -q
cd client
npm.cmd run build
docker compose -p modelmirror up -d --build --force-recreate
```

容器启动后检查：

```bash
curl http://localhost:8000/api/health
curl http://localhost:5173/agents/meta-agent
```

## 路线状态

EvoAgentX 审计、Meta Planner V2、Evaluator、Prompt Evolution 和受限 Structure
Evolution 已作为历史基线交付。下一阶段不再按“多开放几个节点”的方式零散扩张，
而是执行 [Meta Planner V3/V4 锁定路线](./META_PLANNER_V3_V4_ROADMAP.md)：

- V3 固定十轮，先升级 IR 与 Authoring 协议，再按纯节点、控制流、只读资源、视觉、
  受控写入和长运行的顺序开放节点，最后做规划质量增强和收口审计。
- V4 轮次待定，只在 V3 收口报告、真实 Benchmark、稳定 Adapter/Policy/Evaluator
  均具备后规划。
- 单轮实施细节可以依据证据调整；改变依赖顺序、安全边界、Runtime 权威或 V3/V4
  分界必须重新审计并由用户确认。

EvoAgentX 的来源与已交付历史见
[EVOAGENTX_ALIGNMENT.md](./EVOAGENTX_ALIGNMENT.md)。

## Meta Planner V2 已实现契约

`EVOAGENTX-META-PLANNER-01` 已将元智能体从单次工作流生成器升级为候选 Xpert
规划闭环，同时保留原 `POST /api/meta-agent/generate-workflow` 兼容入口。

新增入口：

- `GET /api/meta-agent/capabilities`
- `POST /api/meta-agent/generate-xpert-candidate`

执行顺序固定为：

1. 任务规划：生成 1–8 个带依赖和输入输出契约的任务。
2. 能力编译：从实时 Capability Snapshot 中选择真实节点、资源和中间件。
3. 定向修复：本地确定性门禁失败时，最多调用模型修复一次。

上述为初始生成预算，最多三次 completion，不变。CW10 D 另提供用户显式授权的失败图修复：
先查看外发正文、精确模型和一次调用预算，再单独确认。最终返回的 Graph Patch 仅是建议，
必须人工载入编辑区并重新预览、确认应用，不增加自动修复轮数，不批准提案或运行写节点。
没有安全保留原图的失败不能使用此入口；未知派发不自动重放。详见
[Headless 显式模型修复](./META_PLANNER_HEADLESS_AUTHORING.md#显式模型修复)。
此功能当前离线验证，不代表真实模型成功率已提高；真实验收仍需独立授权。

生成侧使用私有 Schema，不直接修改公共 TaskPlan/Graph IR：首次任务规划和任务修复
共用 `GenerationTaskPlan`，仅接受 expert 任务及声明字段。辅助节点摘要从真实 Adapter
推导；任务的文字输入输出契约不能创造记录身份、返回字段或变量。Query/Insert 返回完整
记录，Update/Delete 只返回 matched/affected，再次修改需要新 Query 的 revision。

CW10 批次 C 的首次生成使用私有 `GenerationRecipeV1`，降低为 GraphIntent V3；已解析描述的
唯一自动修复使用 `recipe_edits_v1` 受限操作，不重新复制整图。
模型只声明业务配置、来源端口和结构化顺序/分支/并行，变量、输出类型及控制边由服务端派生。
旧完整 GraphIntent 不作为新模型输出接受，但旧候选读取及 Patch 重编译不变。
详见 [简化生成描述](./META_PLANNER_GENERATION_RECIPE.md)。
新失败产物安全配对保留 Recipe 与 Intent；显式模型修复在同一 Recipe 层处理，服务端生成有界
Patch 并核对语义往返，再进入原 Preview/Apply。旧 Intent-only 产物继续使用兼容 Patch，不猜测
原 Recipe。控制诊断区分已通过、失败与被前置问题阻断的证明，不用空错误列表冒充已验证。
严格且安全保留但未展开的 Recipe 同样支持受限语义编辑及逐次授权模型建议；服务端绑定
revision/checksum 后执行同一完整 Preview/Apply。资源/输入失配草稿仍只开放各自窄人工入口。
修复未到达旧失败检查时报告 `not_rechecked`，不将缺失观测当作旧问题已经消失。
生成及 Patch 修复共用 Adapter 配置定义和 NodeContract 端口事实。
生成 Schema 按授权 kind 分支；Patch 为新增 kind 和已存在 ref 关联配置 Schema，共用
`$defs` 避免重复正文。它们是模型输入契约，不等同于 Provider 约束解码，也不能替代
配置自定义校验、跨节点类型/变量解析、资源授权及发布预检。模型违约仍失败关闭，不增加
第四次调用，不自动补边、猜测变量或扩大权限；旧 TaskPlan/Proposal 读取不受私有投影影响。

受控写值的模型契约按本次授权且可用的 Adapter 投影。`input` 业务值一律要求直接来自
JSON Deserialize V2 的字段对象，不只是 Agent 生成的数据。该生产者不可用时，生成及
修复 Schema 只提供显式 `literal`，并要求提供 `values`；目标不足以确定固定值时仍应
失败，不自动切换模式、填写业务值或增加权限。旧配置的默认值与 Runtime 行为不变。

前置诊断与最终解析共用来源身份校验；未知 ref/port、歧义端口、变量不匹配和非法写值
生产者独立进入 `source_contract_issues`，不能因路径证明先失败而被遗漏。联合修复清单
同时给出权威路由输入形状、路由失败码、冲突终点、已到达控制根与缺失值反例；不保存
业务记录或 witness 值，也不推导修复连线。路径事实最多投影 64 条，省略量明确记录。
先审查完整目标路径，再输出同一个原子 Patch；只改端口或仅减少错误数量不视为修复。
这些离线契约及反例测试不代表真实模型生成成功率，真实验收仍须独立授权。

生成诊断 V2 增加受限端口计数、绑定位置、声明类型与 checksum。动态谓词 ref、未知变量
及完整 Schema 不进入该摘要；最多记录 64 条新增绑定明细并明确省略数。`declared_*`
表示该次 GraphIntent 中的声明，不是执行证据；旧图可能由模型声明，批次 C 降低后的类型
来自 Adapter/资源解析，不能误称模型自行填对了类型。`data_graph_checksum` 仅用于对照数据连线，
不能代替 Graph IR/候选 checksum 或权限门禁。

生成取证 V1 在同一次 completion 内配对实际请求的私有 Schema、Provider 公开 `content`、
collector 结果及实际校验输入。只记录 Filter 形状、端口、声明类型、位置、白名单枚举和
checksum；未知名称仅记录 hash，不保存 Prompt、业务字面值、完整 Schema 或隐藏推理。
`schema_valid` 只说明该份 JSON 是否符合实际发送的私有 Schema，不表示授权或语义校验通过。
`same_structure` 仅表示上述受限字段一致，不能说明完整正文或整个图合法；正文另有独立 hash。
摘要限制为三次调用、32 个节点、64 条绑定和 64 KiB；截断、缺失、重复观察分别标为
`incomplete`、`unavailable`、`ambiguous`，不伪造完整配对。记录内容无法用于完整响应重放。

取证完全在内存中进行，不在收包路径写文件，不增加重试或 completion。观察或汇总失败只
标记证据不可用，不覆盖原结果；预算账本和 Proposal Store 的失败语义不变。有候选时摘要
写入既有 `meta_planner_report.generation_evidence`；候选创建前失败则仅经错误响应和内存
RunRegistry 返回，重启不保证保留，付费验收必须在重启前导出安全响应。证据缺失时停止
进一步付费诊断，不推断模型或平台为根因。

表更新/删除的 `records` 静态声明类型从 NodeContract 投影到私有生成 Schema，保留契约
允许的 object、nullable、对象数组及 union；同表真实来源、revision 和跨边类型关系仍由
既有语义与 Runtime 门禁负责，不因私有 Schema 合格而跳过。

面向用户和画布展示的候选名称、任务标题、节点标题、说明、提示词及安全错误文案
统一使用简体中文。资源 ID、Planner ref、字段名、错误码等机器标识保持原值，避免翻译
破坏 Schema、资源绑定或确定性 checksum。编译器管理的输入、输出节点也遵守同一展示
语言约束。

### NodeContract V3 能力门禁

Meta Planner 的节点事实统一来自 `NodeContractRegistry`。Capability Snapshot V10
只暴露满足以下全部条件的节点：契约状态完整、Planner 显式启用、编译模式真实存在、
Adapter 版本一致，并且契约与 Adapter 的 compiler checksum 匹配。UI Registry 中出现
节点不等于 Planner 可以生成该节点。

当前开放范围为 `input`、`output`、`workflow_agent`、四类资源绑定，以及
`json_serialize`、`json_deserialize`、`variable_aggregator`、`data_aggregate`、
`dataset_compare` 五种无副作用类型化纯节点和 `condition`、`multi_route`、
`data_merge`、`terminate_error` 四种受限控制流节点，以及 `knowledge_retrieval`、
`data_table_query` 两种只读动态资源节点、显式附件 `vision_understanding` V2，以及
`data_table_insert/update/delete` 三种显式授权的 V2 写节点，共 22 类。
NodeContract V3 与 Planner IR 独立演进。Capability Snapshot 当前为 V10，
`ir_version=3` 且声明 `supported_ir_versions=[2,3]`。旧 V2 Snapshot 保持可读，详见
[NODE_CONTRACT_V3.md](./NODE_CONTRACT_V3.md)。

### Graph IR V3 编译边界

候选生成现在由模型输出 `GraphIntentV3`，服务端再依据 NodeContract、Capability
Snapshot 和资源 Store 解析为 `ResolvedGraphIRV3`。模型不能指定资源版本、Handle、
执行效果或安全属性。Resolved IR 显式区分 `control/data/binding/metadata` 四类边；
data 边只表达类型化变量来源，不进入 classic runner 的拓扑或调度。

外部 Xpert、Toolset、Plugin 和 Prompt Profile 在解析时固定发布版本；知识检索保持
活动版本指针语义并记录观察版本，Agent Table 查询固定不可变 SchemaVersion 与
checksum。Graph checksum 排除布局和时间戳，编译产物另有
独立 checksum。新 Proposal 保存完整安全 IR 和两个 checksum；人工编辑候选后 IR
标记为 `stale`，审批仍以实际 Workflow 为权威。

旧 `MetaPlannerTypedBlueprintV2` 继续可读，但只有变量来源能够唯一恢复时才升级为
V3；歧义来源返回结构化 `lossy_conversion`，不得猜测。生成入口已经单写 V3，模型
调用预算仍为任务规划、能力编译和最多一次修复。

### Headless Authoring 边界

管理端候选编辑不再把完整 Workflow 作为可信持久化输入。V3 候选及可无损恢复的 V2
候选统一经过 `editor-diff -> Graph Patch preview -> checksum-bound apply`。服务端从实际
Proposal、NodeContract、Adapter 与资源 Store 重新解析并预检，Preview 不持久化，Apply
只更新 pending Proposal 一次。Proposal、目标 Xpert 或相关资源发生漂移时 fail-closed。

旧整包 Proposal PATCH 保持兼容并将 IR 标记为 `stale`；有损 V2 候选不得进入 Headless
Apply。操作、接口、安全 receipt 和回退边界见
[META_PLANNER_HEADLESS_AUTHORING.md](./META_PLANNER_HEADLESS_AUTHORING.md)。

### Typed IR V2 兼容边界

旧候选可继续读取 `MetaPlannerTypedBlueprintV2`，其中显式声明节点引用、任务覆盖、
类型化输入/输出变量、控制边、资源/中间件目标和唯一最终输出。任务和 Agent 不再
强制一一对应：一个 Agent 可以覆盖多个任务，一个任务也可以由多个节点共同完成。

Capability Snapshot V10 只暴露当前存在且与 NodeContract、Adapter checksum 校验一致的
编译能力。`workflow_agent` 的 `task_binding=required`，每个计划任务仍必须由 Agent
覆盖；五种纯节点的 `task_binding=forbidden`，只能作为 Agent 之间的确定性辅助步骤，
不能承担任务或成为最终输出。`input/output` 由编译器管理，外部 Xpert、知识库、
Toolset 和 Plugin 通过绑定记录编译。

Meta Planner 只生成 JSON `contractVersion=2`：Deserialize 必须携带受限
`expectedSchema`，输入、解析结果和输出均受 5 MiB 限制，非法 JSON 或类型不符立即
失败。缺少版本的旧 JSON 节点继续保留历史 inline-error/null 行为，不被 Planner
生成或自动升级。Variable Aggregator V2、Data Aggregate 和 Dataset Compare 复用
现有 Runtime，并由 Adapter 从 Graph IR data 边派生原生变量绑定。

控制流使用 `control_flow_contract_version=2`。模型只引用语义 outcome：普通节点为
`success`，Condition 为 `matched/unmatched`，Multi Route 为
`case_1...case_8/default`；Native handle、路由 ID 和变量均由 Adapter 推导。确定性分析器
限制最多 8 个路由节点和 256 个符号场景，阻止循环、死路、不可达、影子规则、路径上
不保证存在的数据，以及无法证明互斥的多成功终点。Output V2 从 1-8 个受信任来源中
严格选择恰好一个到达值；零个或多个均失败。Data Merge 仅用于两个保证到达的 fanout
分支，Terminate Error 只能携带固定安全错误码和消息。

路由 witness 必须同时满足生产端口的权威类型与输入绑定类型；把输入声明放宽为
`any` 不能制造源端口不可能产生的 null、数字或布尔值。必填对象字段会保留在
witness 中；无法找到合法 witness 的 outcome（包括 default）继续阻断。独立的
非终点死路和带出边的成功终点同样不可接受。

旧 Planner 快照可能只有 `plannerRef` 而没有语义 outcome 映射，此类工作流继续
执行，不补造路径证据；显式存在但损坏的 outcome 映射仍会失败。Output V1 的旧
运行不能被当作已经验证了 Output V2 路径合同。

只读资源节点通过节点自有 `resource_ref` 授权，不能与 Agent 的 `bind_resource` 混用。
知识检索只允许 `top_k=1..10` 和 `context/result` 输出；Agent Table 查询只接受固定
Schema 中的字段、受限条件树、排序与 `limit=1..200`。表默认不授权，必须由用户显式
选择；模型不能提交原生资源字段、版本、Schema、Handle 或 checksum。两类节点均可走
`success/error`，但不能承担计划任务或直接成为最终交付来源。

视觉节点默认不授权，必须单独固定 `vision_model_id` 及有效 Managed Binding。输入只允许
编译器管理的单附件端口，Planner 不接收实际文件。视觉结果必须被下游 Agent 消费；
Binding 漂移、目标关闭文件输入或非唯一附件在外发前阻断。详见
[显式附件视觉契约](./META_PLANNER_VISION.md)。

写节点默认关闭，必须逐表、操作、字段和影响上限授权，查询授权不隐含写权限。
更新/删除只接受真实 Query/Insert 的同表记录及 revision；评测只写服务端私有初始化表，
不回退业务表。事务、恢复、效果证据和限制见[受控写入契约](./META_PLANNER_CONTROLLED_WRITES.md)。

`variable_assign`、`list_operation`、`object_transform`、
循环、等待、HITL、Handoff、Trigger 和 `question_classifier` 仍无 Planner Adapter，
不会进入授权快照。

旧 `MetaPlannerBlueprint` 仅用于 Expert Team Agency 等兼容入口，进入编译器前会
转换为 Typed IR。旧计划也必须只有一个终点。更新已有 Xpert 时，如目标工作流含
当前无适配器的节点，服务会在调用 Planner 模型前 fail-closed，避免生成完整替代
草稿时静默丢失节点。

同次请求模型调用总数最多为 3。系统只保存计划摘要、公开假设、选择理由、快照
hash、验证结果和安全统计，不保存隐藏推理。

候选统一写入现有 `AuthoringProposalStore`：

- 创建模式生成 `xpert_create`。
- 更新模式生成 `xpert_update` 并固定目标 `base_revision`。
- 人工编辑使用 Proposal revision 乐观并发控制。
- 批准只创建或更新 Xpert 草稿，不创建发布版本，也不触发运行。
- 发布仍由用户在 Xpert Studio 中显式完成。

前端 `/agents/meta-agent` 复用受控 `WorkflowEditor` 编辑候选，刷新或容器重启后
可恢复 pending Proposal。高风险中间件默认不进入模型授权范围，只有用户显式勾选后
才会进入 Capability Snapshot scope。

Meta Planner V2 的最小回归命令：

```bash
python -m pytest server/tests/test_meta_planner_v2.py server/tests/test_meta_agent.py -q
cd client
npm.cmd run build
```

## 候选评测

Meta Planner V2 的 pending Proposal 可以从候选面板直接进入
`/agents/evaluations`。入口固定当前 `proposal_id + revision`，评测运行会保存完整
不可变快照；之后继续编辑 Proposal 只会把旧报告标记为 stale，不会改变已完成结果。

Evaluator 只读运行候选并生成报告，不调用 Proposal approve，也不会创建 Xpert 草稿
或发布版本。安全、预算和报告契约见
[EVOAGENTX_EVALUATOR.md](./EVOAGENTX_EVALUATOR.md)。

## Prompt 受控进化

`EVOAGENTX-EVOLUTION-03A` 在 Evaluator 之上增加了有界 Prompt 搜索，但不扩展
Meta Planner 的发布权限。入口为：

- `GET /api/xpert-evolutions/capabilities`
- `POST /api/xpert-evolutions/preflight`
- `GET/POST /api/xpert-evolutions/runs`
- `GET /api/xpert-evolutions/runs/{run_id}`
- `POST /api/xpert-evolutions/runs/{run_id}/cancel`

Xpert 模式一次只固定一个草稿 revision，并最多联合优化三个
`workflow_agent.rolePrompt` 或 `promptSuffix` 字段。Prompt Profile 模式固定 Profile
草稿 revision 和一个已发布 XpertVersion 作为评测宿主，只优化单一 `{{args}}`
模板。节点、边、模型、资源绑定和中间件均不允许变化。

DatasetVersion 按 seed 进行 80/20 优化集与验证集拆分。候选生成器只能看到优化集的
安全失败摘要；最终排名只使用独立验证集。少于五条用例时允许共享样例，但运行、
报告和 Proposal 都会标记高过拟合风险。

只有验证集总分提升、单指标没有越过退化上限且没有新增超时、预算或安全错误时，
系统才创建 pending `xpert_update` 或 `prompt_profile_update` Proposal。运行期间目标
revision 变化会使结果变为 stale，并阻止 Proposal 创建。批准 Proposal 只更新草稿，
发布仍需用户在 Studio 或 Prompt 页面显式完成。完整契约见
[EVOAGENTX_EVOLUTION.md](./EVOAGENTX_EVOLUTION.md)。

## 工作流结构受控进化

`EVOAGENTX-EVOLUTION-03B` 复用 Meta Planner Capability Snapshot，但不重新调用
Meta Planner 生成完整 workflow。Optimizer 只能提出类型化 mutation，编译器负责稳定
ID、布局、控制边和五类特殊资源绑定边。

用户在 `/agents/evolution` 的“工作流结构”模式显式授权可生成节点、只读资源和安全
中间件。未授权能力、交互等待、副作用工具、任意 Code、Handoff、Sandbox、Browser 和
Automation 均在评测前 fail-closed。

候选必须先通过 Registry、classic workflow validate、资源循环与冲突、发布预检和
Evaluator 只读预检。静态失败保留 issue，不消耗评测预算。通过 Holdout 的质量、成本和
复杂度门禁后只创建 pending `xpert_update` Proposal，批准后仍只更新草稿。
