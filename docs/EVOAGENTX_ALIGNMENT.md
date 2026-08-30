# EvoAgentX 对齐总纲

最后更新日期：2026-08-28

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

## V3/V4 当前路线

Meta Planner 后续方向已经锁定为 **V3 十轮 + V4 轮次待定**，唯一规范见
[META_PLANNER_V3_V4_ROADMAP.md](./META_PLANNER_V3_V4_ROADMAP.md)。首轮 Graph IR V3
已经交付为单写格式，Capability Snapshot 升级为 V4，旧 Typed IR V2 继续双读兼容。
当前仍不能把完整 NodeContract、画布可见节点或 Runner 已支持节点误报为 Planner 已支持。

V3 先建立 Graph IR V3 和 Headless Authoring，再依次开放纯节点、控制流、只读资源、
视觉、受控写入和经审计的长运行能力；节点语义稳定后才增强 EvoAgentX 规划质量，
最后完成兼容与收益审计。V4 的多智能体规划、案例检索、评测反馈、自适应修复和
跨平台编译只是候选研究域，必须在 V3 收口后重新审计并决定轮次。

该路线允许参考 EvoAgentX 以外的开源项目，但每次实现必须锁定官方 commit 和文件，
核验许可证及第三方依赖，并记录 `reuse/adapt/rewrite/reject`、NOTICE 和测试映射。
MIT/Apache-2.0 等宽松许可证不等于可以省略归因；AGPL、source-available、混合或
不明确来源默认仅作行为参考并独立重写。

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
执行面的主要契约；Evaluator 已补齐版本化评测、固定预算和基线比较；Prompt 与
受限结构候选的受控进化也已交付。当前主要缺口已转为 Graph IR、节点 Adapter、
动态资源、效果语义和复杂路径评测，按 V3/V4 锁定路线继续收口。

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
| Workflow generation | Graph IR V3 已表达 Agent DAG、类型化 data 边与五类绑定 | `adapt` | V3 首轮已交付，节点开放继续分轮 |
| Headless authoring | 类型化 Graph Patch、无副作用预览、冲突绑定 Apply | `rewrite` | 复用 ModelMirror NodeContract、Adapter、Proposal 与发布预检，不引入上游 Runtime |
| Agent generation | V2 已覆盖当前 Agent 配置、资源与 middleware | `adapt` | 候选 Xpert 草稿已交付 |
| Task planning | V2 已生成 1–8 个带依赖与契约的任务 | `adapt` | 结构化规划已交付 |
| Evaluator | 已支持只读安全的固定版本/Proposal 快照评测 | `adapt` | Evaluator 已交付 |
| Benchmark | 已支持版本化数据集、固定预算和基线报告 | `adapt` | Dataset/Report 已交付 |
| Prompt optimizer | 仅人工编辑与版本化发布 | `rewrite` | Prompt 候选与对比报告 |
| Workflow optimizer | 无受控结构进化 | `adapt/rewrite` | 结构候选 Xpert 草稿 |
| Memory / RAG / Tool Runtime | 已有更完整 ModelMirror 实现 | `reject` | 继续复用现有 Runtime |

## 已完成的 V2 历史交付顺序

### 1. `EVOAGENTX-META-PLANNER-01`

Meta Planner V2 必须：

- 从后端节点、middleware 和资源 Registry 读取真实能力，不维护第二套静态清单。
- Capability Snapshot 只公布具有版本化编译适配器的能力；Registry 标记为可见但
  编译器尚不支持的节点不得进入 Planner scope。
- 使用 Typed IR V2 显式表达节点、任务覆盖、类型化端口、控制边、绑定目标和唯一
  最终输出；任务与 Agent 不得继续硬编码为一一对应。
- 生成完整控制流节点、资源绑定边和 middleware 绑定边。
- 支持 External Xpert、Knowledge、Toolset、Plugin、Prompt 和发布配置。
- 只生成带 revision 的候选 Xpert 草稿。
- 依次通过 workflow validate、资源存在性、版本固定、循环检测和发布预检。
- 返回计划摘要、关键假设、资源选择理由、warning 和结构化 validation issues。
- 不返回隐藏推理过程，不自动运行或发布。
- 更新目标含无适配器节点时必须在模型调用前 fail-closed，禁止完整替代候选静默
  删除未知节点。

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

## NodeContract V3 收口

ModelMirror 已将节点配置、端口、边、资源、执行安全和入口可用性统一到
`NodeContractRegistry`。该契约内核是 ModelMirror 自有实现，只适配 EvoAgentX
参数 Schema 与分层验证思想，不复制或引入 EvoAgentX Runtime。

Capability Snapshot 已升级为 V3，但 Typed IR 继续保持 V2。Structure Evolution
必须消费生产 Registry 生成的 Snapshot，不能在测试或运行时伪造可生成节点集合。
完整契约不自动授予 Planner 权限；只有拥有有效 Adapter 且 checksum 一致的能力才能
进入 Snapshot。当前可生成节点仍为既有七类，其他完整契约用于后续轮次的安全准备。

详细契约、迁移边界和策略矩阵见 [NODE_CONTRACT_V3.md](./NODE_CONTRACT_V3.md)。

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

### `EVOAGENTX-EVALUATOR-02`：已实现

- 已实现 revision 化数据集草稿、JSON/CSV/会话导入和不可变版本发布。
- 已固定 XpertVersion、Authoring Proposal revision、workflow、资源版本、
  Knowledge 活动索引、模型策略、seed 和预算。
- 已实现只读安全预检，副作用、等待、写入工具和不安全 Plugin 均 fail-closed。
- 已复用 classic workflow runner，以 `xpert_evaluation` 根 run 运行并捕获最终输出、
  Citation 和安全 usage。
- 已实现 exact、contains、JSON Schema、Citation 和严格 JSON LLM Judge。
- 已实现重启恢复、取消、并发/超时/模型调用/工具调用/token/输出预算。
- 已实现基线 delta、win/tie/loss、延迟、错误和资源 warning 报告。
- 已实现 `/agents/evaluations` 工作台，以及 Meta Planner 候选和 Studio 版本入口。

Evaluator 不批准 Proposal、不写 Xpert 草稿、不发布版本。完整契约见
[EVOAGENTX_EVALUATOR.md](./EVOAGENTX_EVALUATOR.md)。

### `EVOAGENTX-EVOLUTION-03A`：已实现

`EVOAGENTX-EVOLUTION-03A` 已形成受控闭环：

- 固定 Xpert 或 Prompt Profile 草稿 revision、DatasetVersion、模型策略、seed 和预算。
- Xpert 模式仅修改最多三个 `workflow_agent.rolePrompt/promptSuffix` 字段。
- Profile 模式只修改 `template`，并使用固定 XpertVersion 作为评测宿主。
- 五条及以上用例采用互斥 80/20 Holdout；小数据集显式标记过拟合风险。
- 每代候选由有界 JSON 生成和最多一次修复产生，并经过变量保持、敏感信息和样例复制检查。
- 训练与验证均复用现有 Evaluator 内部固定快照入口，不扩展公开 Evaluation Target。
- 验证集总分提升、单指标非退化和错误不增加后，才创建 pending Authoring Proposal。
- Proposal 批准只更新草稿，发布继续由用户显式完成。

完整契约见 [EVOAGENTX_EVOLUTION.md](./EVOAGENTX_EVOLUTION.md)。

### `EVOAGENTX-EVOLUTION-03B`：已实现

结构进化继续复用同一 Evolution Store、Evaluator 和 Authoring Proposal：

- 模型只输出九类类型化 mutation，不输出代码、完整 workflow 或任意 binding handle。
- Capability Snapshot 与授权范围固定；只允许安全控制节点、只读资源和
  Evaluator-safe middleware。
- 编译器确定性生成节点、边、位置和资源绑定，并保护输入、输出及现有 Agent 契约。
- 静态失败候选保留 issues，但不进入 Evaluator。
- 训练和 Holdout 排名同时考虑质量、失败、模型调用、Token、P95 延迟和图复杂度。
- 通过门禁后只创建 pending `xpert_update` Proposal，revision 漂移时标记 stale。

完整契约见 [EVOAGENTX_EVOLUTION.md](./EVOAGENTX_EVOLUTION.md)。

### `EVOAGENTX-BENCHMARK-CATALOG-01`：已实现

- 已交付四个 ModelMirror 自有中英双语合成 Pack，共 64 条固定用例。
- 核心回归仅使用 exact、contains 和 JSON Schema，不把 LLM Judge 作为门禁。
- Catalog Pack 不可编辑；实例化复用 `XpertEvaluationStore` 并自动发布一致的 v1。
- Dataset 已兼容 origin、catalog provenance、coverage 和 calibration 状态。
- `/agents/evaluations` 已增加标准基准、我的评测集和运行报告视图。

### `EVOAGENTX-BENCHMARK-GENERATOR-02`：已实现

- 已支持 Xpert 草稿、发布版本、固定 Proposal 与 Prompt Profile + 固定宿主版本。
- 已按真实目标能力生成指令、结构输出、多轮、工具路由、知识引用与命令覆盖矩阵。
- 已实现一次生成、一次 JSON 修复、显式会话样例、重复/泄漏和未知资源校验。
- 已增加确定性 `tool_call_match`，只捕获工具名和稳定顺序。
- 已复用 Evaluator 固定快照完成自动校准；校准不会修改 Gold。
- 已实现生成 Job 重启恢复，以及 pending/warning/failed/stale 发布门禁。
- Studio、Meta Planner、Prompt 与 Evolution 已提供一键生成入口。

### 当前路线：Benchmark 闭环

Meta Planner、Evaluator、Prompt Evolution 和 Structure Evolution 已形成第一阶段闭环，
Xpert 标准数据与针对性生成也已补齐。当前按以下独立轮次完成 RAG 和兼容性收口：

1. 已完成标准 Xpert Benchmark 目录。
2. 已完成 Xpert/Prompt/Profile/结构目标的一键生成与受限校准。
3. 已完成 RAG 引擎标准 Pack 与版本化 Gold 引用；该 Pack 仅用于检索一致性和回归。
4. 已完成 `XPERT-RAG-BENCHMARK-GENERATOR-04`：固定具体知识版本、生成定向 Gold、执行真实
   检索校准并由人工审核发布。
5. 下一轮进入 `BENCHMARK-COMPATIBILITY-05`，只收口目录与现有 Agent Workspace/Penguin
   Runtime 的兼容映射，不替换其执行与评分契约。
5. 最后只对 General Agent Workspace 做目录和运行摘要适配，不替换 Penguin Runtime。

每轮在共享栈空闲并完成 Docker 人工验收后独立提交。完整共享契约见
[BENCHMARKS.md](./BENCHMARKS.md)。
