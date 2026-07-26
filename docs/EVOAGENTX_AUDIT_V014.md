# EvoAgentX v0.1.4 源码审计台账

最后更新日期：2026-07-25

## 1. 审计结论

EvoAgentX `v0.1.4` 适合作为元智能体规划、通用评估和候选搜索的 MIT
算法参考，但不适合作为 ModelMirror 的整体运行时依赖。

审计结论固定为：

- Workflow/Agent generation 与 task planning：`adapt`。
- Evaluator、Benchmark 抽象和少量纯指标：`adapt`，个别独立函数可在后续
  PR 完成逐文件归因后 `reuse`。
- Optimizer：先吸收候选、预算、档案和变异思想，不直接引入整套实现。
- Models、Tools、RAG、Storage、HITL、Memory Runtime：`reject/rewrite`，
  继续使用 ModelMirror 已有闭环。
- 本审计不复制任何 EvoAgentX 代码，不增加运行时依赖。

## 2. 来源锁定

| 项目 | 固定值 |
| --- | --- |
| 上游仓库 | `https://github.com/EvoAgentX/EvoAgentX` |
| 标签 | `v0.1.4` |
| Commit | `aad19b912f640161ea07e8904d9237cd34fde5f1` |
| 包版本 | `0.1.4` |
| Python | `>=3.10` |
| 主许可证 | MIT，Copyright (c) 2025 EvoAgentX |
| 上游第三方声明 | AFlow-derived MIT、LiveCodeBench MIT |
| ModelMirror 基线 | `main@93e5cc38becc7fe4f89efa113310698e6eda1971` |

验证命令：

```bash
git rev-parse HEAD
git describe --tags --exact-match
```

用户提供的本地归档没有 `.git`，但包版本为 `0.1.4`。对其中 257 个
`evoagentx/` 源文件统一 CRLF/LF 后，与官方固定提交逐文件一致。原始字节
哈希不作为来源证明，因为 ZIP 解压和 Git checkout 的换行策略不同。

官方仓库和 commit 始终是唯一可复用来源。本地归档仅作离线复核，不记录
个人绝对路径，也不读取或复制任何 `.env`。

## 3. 判定标准

| 判定 | 含义 |
| --- | --- |
| `reuse` | 文件足够独立、许可证明确、依赖兼容，可保留版权和 NOTICE 后移植 |
| `adapt` | 保留算法或契约思想，改写为 ModelMirror 数据模型、Store 和 runner |
| `rewrite` | 需求有价值，但上游实现与安全或架构边界冲突，必须独立实现 |
| `reject` | 已有成熟等价能力、依赖成本过高或不符合当前路线，不进入实现 |

每个未来复用项必须同时满足：

1. 固定上游 commit 和相对文件路径。
2. 记录文件 SHA-256、版权、许可证和第三方来源。
3. 不依赖 EvoAgentX 的 Model、Tool、RAG、Store 或 Workflow Runtime。
4. 建立对应本地单元测试和 ModelMirror 契约测试。
5. 只能生成候选草稿或评估报告，不能静默发布。

## 4. 模块审计矩阵

| 上游模块 | 文件数 | 职责 | ModelMirror 对应 | 判定 | 主要原因 | 目标阶段 |
| --- | ---: | --- | --- | --- | --- | --- |
| `actions/` | 7 | 结构化 Action、任务规划和 Agent 生成 | `server/meta_agent/`、模型网关 | adapt | Pydantic 输出思想可用，但 Action/BaseLLM Runtime 重复 | Meta Planner |
| `agents/` | 9 | Agent、Generator、Planner、Manager、Reviewer | XpertDefinition、workflow_agent、MetaAgent | adapt | 规划与审阅分层有价值；Agent Runtime 不兼容当前 Toolset/middleware | Meta Planner |
| `prompts/` | 24 | Planner、Generator、Optimizer 和工具提示模板 | MetaAgent prompts、Prompt Profile | adapt | 只吸收结构约束，不复制长 Prompt 或暴露 thought | Planner/Evolution |
| `workflow/` | 10 | WorkFlowGraph、生成、执行、Manager | NativeWorkflowDefinition、classic runner | adapt/reject | Generator 思路可适配；第二套 graph/runner 必须拒绝 | Meta Planner |
| `evaluators/` | 3 | 批量执行、指标聚合、AFlow 评估 | 未来 Xpert evaluation run | adapt | 接口与记录模型有价值；线程共享 AgentManager 和 Runtime 耦合需重写 | Evaluator |
| `benchmark/` | 20 | Dataset 抽象、任务集、指标、代码评测 | 未来 Benchmark Store、Sandbox | adapt/reject | 抽象可用；数据许可证、下载和代码执行需逐项隔离 | Evaluator |
| `optimizers/engine/` | 4 | 参数注册、入口和基础 Optimizer | 未来 candidate registry | adapt | 可映射到版本化候选与 revision，但不能修改线上对象 | Evolution |
| `optimizer_core.py` | 1 | Prompt registry、搜索和采样基础 | Prompt/Xpert candidate store | adapt | 纯候选思想可用，字段发现与运行模型需重写 | Evolution |
| `map_elites_optimizer.py` | 1 | MAP-Elites 档案搜索 | 未来 candidate archive | adapt | 实现相对独立且有测试，仍需改为持久候选与预算模型 | Evolution |
| `sew_optimizer.py` | 1 | Workflow 结构变异与 Prompt breeding | Xpert draft candidate | adapt | 使用 inspect/BPMN/动态 workflow 表示，不能直接操作当前 graph | Evolution |
| `aflow_optimizer.py` + utils | 6 | AFlow 图候选、经验和收敛 | Xpert draft + evaluator | adapt | MIT 来源明确；包含文件复制、动态图和 benchmark 下载假设 | Evolution |
| `mipro_optimizer.py` + utils | 5 | DSPy MIPRO Prompt 优化 | Prompt Profile candidate | rewrite | 直接耦合 DSPy/Optuna 和 EvoAgentX Workflow；只保留实验设计 | Evolution |
| `textgrad_optimizer.py` | 1 | TextGrad Prompt 梯度优化 | Prompt Profile candidate | rewrite | 直接依赖 TextGrad，导入时操作日志目录；必须隔离重写 | Evolution |
| `evoprompt_optimizer.py` | 1 | Prompt 变异与选择 | Prompt Profile candidate | adapt | 候选与选择思想可用，模型和评估适配需重写 | Evolution |
| `memory/` | 5 | 短期/长期记忆与上下文管理 | XpertContextStore、File Memory | reject | ModelMirror 已有作用域、审批、候选和恢复契约 | 不移植 |
| `hitl/` | 7 | HITL GUI、审批和 workflow editor | ApprovalStore、可恢复 execution | reject | 当前持久断点、lease 和跨入口语义更完整 | 不移植 |
| `rag/` | 35 | Reader、Chunk、Index、Retriever、Graph RAG | ModelMirror Knowledge Pipeline | reject | 已有 Processor、双索引、视觉、评估和审批写入闭环 | 不移植 |
| `storages/` | 16 | SQL、vector、graph storage | 文件 Store、Chroma/FTS5、DuckDB | reject | 会引入第二套持久化和迁移边界 | 不移植 |
| `models/` | 9 | OpenAI/OpenRouter/LiteLLM/DashScope | ModelMirror model registry/gateway | reject | 会绕过现有模型注册、网关和安全配置 | 不移植 |
| `tools/` | 68 | Browser、MCP、搜索、文件、数据库等 | Toolset、Sandbox、Browser、Client Tools | reject | ModelMirror 已有版本固定、Policy、HITL、Audit 和隔离执行 | 不移植 |
| `frameworks/` | 3 | Multi-agent debate | Goal、External Xpert、Handoff | defer | 当前没有已确认产品目标和评估基线 | 审计后再定 |
| `core/` | 12 | BaseModule、Registry、Message、Parser | Pydantic schemas、现有 registries | reject | 整体引入会形成第二套框架内核 | 不移植 |

文件数只统计固定提交中的 `evoagentx/` 文件，不包含文档、示例和测试数据。

## 5. 关键文件哈希与复用判定

| 相对路径 | SHA-256 | 判定 | 本地测试映射 |
| --- | --- | --- | --- |
| `LICENSE` | `76d86cfdc8861e4ffcd48bc7dbf6f438b42c35bf580804d7900195dbb22b07b1` | 来源证据 | 许可证检查 |
| `pyproject.toml` | `98dea4aebb378dca45c1c9e22514f18b31388f741fa9a884fa77b8c601ca14bf` | 依赖证据 | 依赖审计 |
| `evoagentx/workflow/workflow_generator.py` | `c8d25551e4a62f23f19d136ab5146ff656c35ea311d5e01383241a4993b020c6` | adapt | MetaAgent generation |
| `evoagentx/actions/task_planning.py` | `9eb378176f7a31e984a3c4db6729c7957806bd7a598b2d96c4b254b5cc94ce10` | adapt | Planner schema |
| `evoagentx/agents/agent_generator.py` | `6c3a6e2f0540d4b44a616ad9eb46005cbdb8cdc3cd1df3ad5869f68f5bcd35e8` | adapt | Resource selection |
| `evoagentx/evaluators/evaluator.py` | `6e45558e3c61f4782fa7f54b5475b15d150a6c7a991fc7b90a9edef5ec0d14ea` | adapt | Candidate/baseline evaluator |
| `evoagentx/benchmark/benchmark.py` | `bd4ffe0e2a77707af3e83d6beb80091964405e1a28f75d0060bb4ac3ebace259` | adapt | Dataset contract |
| `evoagentx/optimizers/optimizer_core.py` | `cd80f98947a65a3e74be7256bcafce1000f1a52b50b4bbf370f764f99cba8235` | adapt | Candidate registry |
| `evoagentx/optimizers/map_elites_optimizer.py` | `fbe8206284fb96e2db549c948eecb73a114790683e66a53ff69698179bc3ae03` | adapt | Archive/search tests |
| `evoagentx/optimizers/sew_optimizer.py` | `61a98ceef9b999a34cc059ad248db2a6e7e57fb0f79227d0eac290b4084130eb` | adapt | Graph mutation tests |
| `evoagentx/optimizers/aflow_optimizer.py` | `2b5000ccd3804aa542aab1c698bf052f9a5a1e2513c39a9d6e4f1f154cac90ca` | adapt | Candidate convergence |
| `evoagentx/optimizers/mipro_optimizer.py` | `938a368e54fa194804c8f8e067d6e3d8887a74abc416da26d87d510013b4894f` | rewrite | Prompt evaluation |
| `evoagentx/optimizers/textgrad_optimizer.py` | `2d2da5f30b24a1c98175edacf6b6ae814c9e8f0014dc3abc6072c6a28108d112` | rewrite | Prompt candidate tests |
| `evoagentx/memory/memory_manager.py` | `da0c0f8a414768f3c4730496d9974d36262d861c771988bf642765f4bbee5d3b` | reject | Existing context/memory tests |

这些哈希只锁定审计输入。未来代码 PR 必须再次核对实际移植文件，不能用本表
替代该 PR 的版权、NOTICE 和测试记录。

## 6. 依赖与许可证风险

上游 `pyproject.toml` 使用大量开放版本范围，没有完整 lockfile。因此禁止把
`evoagentx` 或 `evoagentx[all]` 直接加入 ModelMirror Runtime。

| 依赖组 | 观察 | 决策 |
| --- | --- | --- |
| Core | LiteLLM、DashScope、OpenAI、requests、numpy、pandas、networkx 等 | 不整体引入；继续使用现有网关和已有依赖 |
| Optimizers | TextGrad、DSPy、Optuna、cloudpickle、ujson | 按具体算法单独评估；不得成为 Meta Planner 前置依赖 |
| Benchmarks | sympy、antlr、数据集和代码执行工具 | Evaluator 首版使用本地小型数据集；代码评测必须走 Sandbox |
| RAG | LlamaIndex、FAISS、Neo4j、Transformers、Torch 等 | 全部拒绝进入本路线 |
| Tools | Docker、Selenium、browser-use、数据库和第三方 API SDK | 全部复用 ModelMirror 现有 Toolset/sidecar |

已核对的优化器相关上游仓库 SPDX 均为 MIT：TextGrad、DSPy、Optuna、
MetaGPT/AFlow 与 LiveCodeBench。该结论不替代具体发布版本和传递依赖审计。

EvoAgentX LICENSE 已包含 AFlow 和 LiveCodeBench 的 MIT 声明。任何未来直接
复用这些文件的 PR 必须把对应声明加入 ModelMirror 第三方 NOTICE。

## 7. 上游测试证据与缺口

固定提交包含约 225 个按 `test_` 命名的测试函数，主要分布在 Agent、
Benchmark、Core、Model、Storage 和 Workflow。

审计发现：

- Workflow graph 有较多结构测试，但 `WorkFlowGenerator`、`TaskPlanner` 和
  `AgentGenerator` 没有直接的端到端生成测试。
- Optimizer 只有 MAP-Elites、SEW workflow scheme 和包导入的有限测试；
  AFlow、MIPRO、TextGrad 与完整 SEW 优化闭环没有对应覆盖。
- Evaluator 有基础同步/异步测试，但实现与 EvoAgentX WorkFlow、
  AgentManager 和 Benchmark 强耦合。
- Coding Benchmark 包含任意代码执行和资源限制逻辑，不能在 ModelMirror
  server 进程直接复用。
- 上游测试不能替代 ModelMirror 的资源固定、审批、App 安全和 Store 恢复测试。

因此所有 `adapt/rewrite` 项都必须先建立 ModelMirror 自有测试，不能把“上游
有测试”视为兼容证明。

## 8. ModelMirror 契约映射

| EvoAgentX 概念 | ModelMirror 事实源 | 迁移规则 |
| --- | --- | --- |
| WorkFlowGraph | `NativeWorkflowDefinition` + validate | 生成当前节点和特殊绑定边，不导入上游 graph class |
| WorkFlowGenerator | `server/meta_agent/` | 分阶段规划、资源选择和校验，使用现有模型网关 |
| Agent | `workflow_agent` + XpertDefinition/Version | 只生成配置和绑定，不创建第二套 Agent Runtime |
| Tool/Toolkit | ToolsetVersion + RuntimeTool | 只引用固定资源，不导入上游工具 SDK |
| Evaluator | 未来 Evaluation Store/Executor | 固定输入、版本、模型、资源和预算后运行 classic runner |
| Benchmark | 未来 Dataset/Metric contract | 数据集许可证独立登记，代码执行进入 Sandbox |
| Optimizer | 未来 Candidate Proposal | 只创建候选 Xpert/Prompt 草稿和评估报告 |
| Memory | XpertContextStore/File Memory | 不迁移上游 Memory Manager |
| HITL | ApprovalStore/WorkflowExecutionStore | 候选和发布继续人工审批 |
| RAG | Knowledge Pipeline | 不迁移上游 RAG 或 Graph Store |
| Storage | 各领域文件 Store/Chroma/FTS5/DuckDB | 不引入上游 storage abstraction |

## 9. Meta Planner V2 审计输出

下一个功能 PR 必须实现以下闭环，而不是移植 EvoAgentX Runtime：

1. Planner 从后端 Workflow Node Registry、Middleware Registry 和资源选项读取
   当前事实，不能在 Prompt 中维护静态过时列表。
2. 规划结果区分控制流、资源绑定和 middleware binding。
3. 生成 `workflow_agent`、External Xpert、Knowledge、Toolset、Plugin/Prompt
   和当前真实节点配置。
4. 输出简洁 summary、assumptions、warnings 和资源选择依据，不要求或保存
   隐藏思维链。
5. 候选依次通过 schema、`validate_workflow_graph`、资源存在性、循环检测和
   Xpert 发布预检。
6. 只创建带 revision 的候选 Xpert 草稿；不发布、不运行、不覆盖人工草稿。
7. 为相同输入、Registry 快照和模型配置提供可重复测试 fixture。

## 10. 后续顺序

1. `EVOAGENTX-META-PLANNER-01`
2. `EVOAGENTX-EVALUATOR-02`
3. `EVOAGENTX-EVOLUTION-03` 的 Prompt 候选
4. `EVOAGENTX-EVOLUTION-03` 的工作流结构候选

Evaluator 必须在任何自动优化上线前提供固定基线、候选比较、预算统计和失败
隔离。所有进化结果必须经过人工批准和现有 Xpert 发布流程。
