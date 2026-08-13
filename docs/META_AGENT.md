# 元智能体集成说明

最后更新日期：2026-07-25
维护人：模镜团队

## 定位

元智能体工作台用于把自然语言目标拆解为可编辑的经典工作流/Xpert 草稿。Meta Planner V2
已经可以从实时 Registry 编译 `workflow_agent`、资源绑定、中间件和发布预检所需配置；
旧生成器仍保留用于兼容经典工作流导入与既有 AgentTask/Handoff 操作。

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

## 后续路线

1. `EVOAGENTX-ALIGNMENT-AUDIT-01`：已完成，见
   [EVOAGENTX_AUDIT_V014.md](./EVOAGENTX_AUDIT_V014.md)。
2. `EVOAGENTX-META-PLANNER-01`：已完成，按上述固定契约生成当前
   WorkflowNodeKind、资源绑定边、Agent middleware 与发布配置。
3. `EVOAGENTX-EVALUATOR-02`：已完成版本化数据集、只读候选执行、固定预算和基线对比。
4. `EVOAGENTX-EVOLUTION-03`：先做 Prompt 优化，再做工作流结构优化；输出候选草稿与评估报告，必须人工批准后才能发布。

完整路线见 [EVOAGENTX_ALIGNMENT.md](./EVOAGENTX_ALIGNMENT.md)。

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

### NodeContract V3 能力门禁

Meta Planner 的节点事实统一来自 `NodeContractRegistry`。Capability Snapshot V3
只暴露满足以下全部条件的节点：契约状态完整、Planner 显式启用、编译模式真实存在、
Adapter 版本一致，并且契约与 Adapter 的 compiler checksum 匹配。UI Registry 中出现
节点不等于 Planner 可以生成该节点。

当前开放范围仍严格保持为 `input`、`output`、`workflow_agent`、
`external_xpert`、`knowledge_base`、`toolset_resource` 和 `plugin_resource`。
NodeContract V3 与 Typed IR 独立演进，本轮 Capability Snapshot 升级为 V3，
`ir_version` 仍为 2。旧 V2 Snapshot 保持可读，详见
[NODE_CONTRACT_V3.md](./NODE_CONTRACT_V3.md)。

### Typed IR V2 编译边界

能力编译阶段现在输出 `MetaPlannerTypedBlueprintV2`，显式声明节点引用、任务覆盖、
类型化输入/输出变量、控制边、资源/中间件目标和唯一最终输出。任务和 Agent 不再
强制一一对应：一个 Agent 可以覆盖多个任务，一个任务也可以由多个节点共同完成。

Capability Snapshot V3 只暴露当前存在且与 NodeContract 校验一致的编译能力。首轮可执行 IR 节点只有
`workflow_agent`；`input/output` 由编译器管理，外部 Xpert、知识库、Toolset 和
Plugin 通过绑定记录编译。JSON、Agent Table、知识检索和视觉理解尚无 Planner
适配器，因此不会进入授权快照，也不会被模型生成。

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
