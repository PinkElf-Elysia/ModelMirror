# 元智能体集成说明

最后更新日期：2026-07-25
维护人：模镜团队

## 定位

元智能体工作台用于把自然语言目标拆解为可编辑的经典工作流/Xpert 草稿。当前模块能生成基础节点和 inferred edges，也能进入 AgentTask、Handoff、RunRegistry 与 Xpert 草稿链路；但它尚不能可靠生成后续新增的资源绑定、中间件、知识能力和完整发布契约。

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
- 当前 generator 对 `external_xpert`、`knowledge_base`、Agent 级 middleware、Toolset 资源和知识画布配置的生成能力滞后，进入 EvoAgentX 审计后的首批修复范围。
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
2. `EVOAGENTX-META-PLANNER-01`：按上述固定契约生成当前完整
   WorkflowNodeKind、资源绑定边、Agent middleware 与发布配置。
3. `EVOAGENTX-EVALUATOR-02`：增加任务数据集、可插拔指标、候选执行和基线对比。
4. `EVOAGENTX-EVOLUTION-03`：先做 Prompt 优化，再做工作流结构优化；输出候选草稿与评估报告，必须人工批准后才能发布。

完整路线见 [EVOAGENTX_ALIGNMENT.md](./EVOAGENTX_ALIGNMENT.md)。
