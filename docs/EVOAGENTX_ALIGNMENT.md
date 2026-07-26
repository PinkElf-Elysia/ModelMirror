# EvoAgentX 对齐总纲

最后更新日期：2026-07-25

## 当前定位

Xpert 对齐已在 ModelMirror `main@93e5cc38becc7fe4f89efa113310698e6eda1971`
进入功能冻结。当前主线转向 EvoAgentX 的元规划、评估与候选进化能力，不再以
Xpert 页面差异继续扩张功能。

EvoAgentX 的唯一复用基线是：

- 官方标签：`v0.1.4`
- 官方提交：`aad19b912f640161ea07e8904d9237cd34fde5f1`
- 许可证：MIT
- 审计结论：[EVOAGENTX_AUDIT_V014.md](./EVOAGENTX_AUDIT_V014.md)
- Xpert 冻结快照：[XPERT_FREEZE.md](./XPERT_FREEZE.md)

本地主线副本只用于验证官方快照的文件差异。任何后续复用都必须从上述官方
提交逐文件取材，并记录来源、许可证、内容摘要、适配方式和本地测试责任。

## 为什么需要重建 MetaAgent

当前 `server/meta_agent/` 仍主要实现早期的
`goal -> sub_tasks -> inferred edges`：

- 只能稳定生成基础控制流节点。
- 不理解资源绑定边和 `targetHandle` 契约。
- 不能完整选择 Agent middleware、Toolset、Plugin、Prompt 和知识资源。
- 不固定已发布资源版本，也不生成发布预检所需配置。
- 没有任务数据集、候选执行、基线比较和优化报告。

与此同时，classic workflow 已支持可发布 Xpert、Goal、Handoff、资源绑定、
Agent middleware、Toolset、Knowledge、Memory、Data X、Sandbox、Browser、
Client Tools、Automation、Plugin 和 Prompt。Meta Planner V2 已补齐候选生成与当前
执行面的主要契约；评测、基线比较和进化收益证明仍未实现。

## 复用规则

| 判定 | 含义 |
| --- | --- |
| `reuse` | 文件许可证和依赖均已审计，可在保留版权、NOTICE 和测试的前提下复用。 |
| `adapt` | 借用可识别的算法或数据结构，但改写为 ModelMirror 契约。 |
| `rewrite` | 只保留行为目标，基于 ModelMirror Runtime 独立实现。 |
| `reject` | 不引入模块、依赖或运行时；继续使用 ModelMirror 现有能力。 |

强制边界：

- 不把整个 EvoAgentX 包作为 ModelMirror Runtime 依赖。
- 不迁移其模型 Provider、RAG、Storage、HITL、Memory 或 Tool Runtime。
- 不复制 Xpert AGPL 源码。
- 不允许 Planner 或 Optimizer 发布 Xpert、覆盖人工草稿或修改不可变版本。
- 不允许使用未锁定提交、来源不明的本地文件或未完成许可证审计的第三方实现。

## 能力矩阵

| 能力域 | ModelMirror 当前状态 | 审计判定 | 目标产物 |
| --- | --- | --- | --- |
| Workflow generation | V2 已生成当前 Agent DAG 与五类绑定边 | `adapt` | Meta Planner V2 已交付 |
| Agent generation | V2 已覆盖当前 Agent 配置、资源与 middleware | `adapt` | 候选 Xpert 草稿已交付 |
| Task planning | V2 已生成 1–8 个带依赖与契约的任务 | `adapt` | 结构化规划已交付 |
| Evaluator | RAG 有专项评估，通用 Xpert 缺任务级基线 | `adapt` | 固定版本 Evaluator |
| Benchmark | 无统一任务数据集与预算报告 | `adapt` | Benchmark Suite |
| Prompt optimizer | 仅人工编辑与版本化发布 | `rewrite` | Prompt 候选与对比报告 |
| Workflow optimizer | 无受控结构进化 | `adapt/rewrite` | 结构候选 Xpert 草稿 |
| Memory / RAG / Tool Runtime | 已有更完整 ModelMirror 实现 | `reject` | 继续复用现有 Runtime |

## 交付顺序

### 1. `EVOAGENTX-META-PLANNER-01`

Meta Planner V2 必须：

- 从后端节点、middleware 和资源 Registry 读取真实能力，不维护第二套静态清单。
- 生成完整控制流节点、资源绑定边和 middleware 绑定边。
- 支持 External Xpert、Knowledge、Toolset、Plugin、Prompt 和发布配置。
- 只生成带 revision 的候选 Xpert 草稿。
- 依次通过 workflow validate、资源存在性、版本固定、循环检测和发布预检。
- 返回计划摘要、关键假设、资源选择理由、warning 和结构化 validation issues。
- 不返回隐藏推理过程，不自动运行或发布。

### 2. `EVOAGENTX-EVALUATOR-02`

- 建立版本化任务数据集、输入夹具、预期结果和可插拔指标。
- 候选与基线固定同一 XpertVersion、模型、资源版本、输入和预算。
- 记录质量、成本、延迟、工具调用和失败分布的安全摘要。
- 失败候选不得覆盖草稿或进入发布流程。

### 3. `EVOAGENTX-EVOLUTION-03`

- 第一阶段只生成 Prompt Profile / Agent prompt 候选。
- 评估闭环稳定后，再开放节点、边、资源绑定和 middleware 的结构候选。
- 每个候选必须附来源、变更说明、预算、评估报告和回退目标。
- 只有人工批准后才能写入 Xpert 草稿；发布仍是独立显式操作。

## 固定评估护栏

- 同一比较必须固定数据集 revision、XpertVersion、模型 ID、资源版本和随机参数。
- 候选与基线必须使用相同调用次数、token、工具调用、并发和超时预算。
- 不得访问 `.env`、API key、Runtime Store 物理路径或公开 App token。
- 评估输出只保存必要摘要；敏感输入、完整工具结果和内部 prompt 不进入审计报告。
- 任何并发草稿编辑都使用 revision 冲突保护，禁止 last-write-wins 覆盖人工工作。

## 明确暂缓

- 无人工审核的持续自进化。
- 用 EvoAgentX 替换 classic workflow runner。
- 迁移 EvoAgentX Provider、RAG、Memory、HITL、Storage 或 Tool Runtime。
- GraphRAG、企业权限、多租户、远程插件市场和 Xpert UI 像素对齐。

## 验收标准

- 所有移植文件都有官方 commit、相对路径、SHA-256、许可证和本地测试映射。
- Meta Planner 生成的绑定边不进入控制流拓扑或变量传播。
- 候选草稿通过当前后端 Registry 和发布预检，而非测试专用静态 schema。
- Evaluator 的候选与基线可重复执行且预算一致。
- Planner、Evaluator 和 Optimizer 均不能静默发布、覆盖草稿或修改线上版本。

## 当前交付状态

### `EVOAGENTX-META-PLANNER-01`：已实现

- 已上线安全 Capability Snapshot，聚合 Workflow Node Registry、Middleware
  Registry、已发布 Xpert/Toolset/Plugin/Prompt Profile、知识库和安全模型标签。
- 已实现任务规划、能力编译和一次定向修复，单次生成最多三次模型调用。
- 已实现 `workflow_agent` DAG，以及 `expert`、`knowledge`、`toolset`、`plugin`、
  `middleware` 五类特殊绑定边的确定性编译。
- 已实现资源版本固定、授权检查、变量与控制流校验、协作循环和名称冲突检查。
- 已复用 Authoring Proposal 作为唯一候选存储；支持 Create/Update、revision 冲突、
  刷新恢复、画布编辑、预检和批准写入草稿。
- 已提取并复用 Xpert 发布预检；预检不创建版本、不修改 Store。
- 旧 `generate-workflow` 接口继续兼容。

本轮采用 `adapt`，只借鉴 EvoAgentX `task_planning.py`、
`workflow_generator.py`、`agent_generator.py` 的分层概念。没有复制上游源码，也没有
引入 EvoAgentX Provider、RAG、Store、Workflow Runtime 或其他运行时依赖。

### 下一步：`EVOAGENTX-EVALUATOR-02`

下一轮进入版本化评测集、固定基线与候选快照、可插拔指标、预算约束和退化报告。
Evaluator 仍只能评价候选，不得写入 Xpert 草稿或发布版本。
