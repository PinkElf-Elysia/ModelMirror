# EvoAgentX Xpert Evaluator

最后更新日期：2026-08-08

## 1. 定位

`EVOAGENTX-EVALUATOR-02` 为 Meta Planner V2 和后续 Evolution 提供只读、
可恢复的评测闭环：

```text
DatasetVersion
  -> baseline/candidate snapshot
  -> read-only preflight
  -> bounded classic workflow execution
  -> metrics and budget aggregation
  -> immutable comparison report
```

Evaluator 只评价固定快照。它不会批准 Authoring Proposal、修改 Xpert 草稿、
发布版本或改变线上资源。

实现位于：

- `server/evaluations/models.py`
- `server/evaluations/store.py`
- `server/evaluations/service.py`
- `server/evaluations/executor.py`
- `server/evaluations/metrics.py`
- `server/evaluations/api.py`
- `server/benchmarks/`
- `client/src/components/evaluations/BenchmarkCatalogPanel.tsx`
- `client/src/pages/XpertEvaluationsPage.tsx`

## 2. 版本化数据集

数据集草稿使用 revision 乐观并发，发布后生成不可变递增版本。单数据集最多
500 条用例，单次运行最多选择 100 条。用例支持：

- 当前用户消息和最多 20 条 `system/user/assistant` 历史消息。
- 标签。
- 精确答案。
- 必须包含的文本。
- JSON Schema。
- Citation ID、chunk ID 或文档名。
- 必需工具、禁止工具和稳定工具调用顺序。
- 必需/禁止的语义控制流 outcome，以及预期成功或安全错误终点。
- LLM Judge rubric。
- 指标权重。

数据可通过管理页面人工编辑、JSON/CSV 导入，或从用户显式选择的 Xpert 会话
导入。会话导入不复制附件、记忆、物理路径或内部 Runtime 上下文。

### 2.1 标准 Benchmark 目录

`EVOAGENTX-BENCHMARK-CATALOG-01` 增加四个 ModelMirror 自有中英双语合成 Pack，
共 64 条固定用例。目录核心门禁只使用 `exact_match`、`contains` 和
`json_schema`，不依赖 LLM Judge 或外部数据。

目录 Pack 不可编辑。“添加到工作区”会在原 `XpertEvaluationStore` 中原子创建
Dataset，并自动发布与 Pack 完全一致的 v1。Dataset 的 `origin`、`catalog_ref`、
`provenance`、`coverage` 和 `calibration` 会固定到不可变版本；旧数据读取时默认为
`origin=manual`。完整契约见 [BENCHMARKS.md](./BENCHMARKS.md)。

### 2.2 针对性 Benchmark 生成

`EVOAGENTX-BENCHMARK-GENERATOR-02` 为 Xpert 草稿、发布版本、固定 Proposal 和
Prompt Profile + 固定宿主版本生成待审核 Dataset 草稿。生成任务固定目标 checksum、
模型、覆盖矩阵、seed 和显式选择的用户会话样例；最多执行一次生成和一次 JSON 修复。
服务端持有 locale、覆盖、难度、资源 ID、工具必选/禁用集合与 JSON Schema，模型只补专业
题面、历史和必要文本 Gold，避免回显大段服务端契约造成截断或授权漂移。
目标 Prompt、会话契约、Schema 和安全资源会被编译为可校验 `target_anchors`，并从中
提取有限专业 `focus_terms`。每条用例必须记录锚点引用、1–3 项能力矩阵、专业词、压力
类型、针对性理由、区分证据和难度；服务端要求输入包含精确 focus term，或至少两个来自
引用锚点的专业标记。工具、文档和命令别名仍须精确匹配。管理页逐例展示这些证据与校准
分数，因此“针对目标”不再只依赖数据集名称或模型自述。

多能力目标至少 60% 用例必须形成复合矩阵，并在可行时覆盖至少三种不同组合；edge 和
adversarial 还必须分别携带一个和两个可验证压力类型。目标 Prompt 若没有专业内容会在
预检中收到警告，避免把基础通用题包装成专业评测。

生成后自动复用 Evaluator 内部固定快照入口执行一次受限校准。校准同时运行专业目标与
同模型通用对照：通用对照保留工作流骨架和输出契约，但移除领域 Prompt、Prompt Command
与资源绑定。报告包含两者分数及针对性优势，默认优势低于 `0.10` 时产生 warning。校准
不会重写 Gold，只报告评分契约可执行性、重复、泄漏、难度和反事实区分度。生成数据集只有在相同 revision
达到 `calibrated`，或用户显式确认 `warning` 后才能发布；目标或用例漂移会转为
`stale`。完整契约见 [BENCHMARKS.md](./BENCHMARKS.md)。

生成失败诊断仅保留结束原因、响应字符数、契约存在性、候选顶层键名和 token 统计；不保存
reasoning 正文。可恢复的空内容、契约缺失和截断解析错误复用同一个单次修复预算。Benchmark
生成和修复固定使用低 reasoning effort，为最终 JSON 保留 completion 预算。

## 3. 固定快照

每次 Evaluation Run 固定：

- Dataset ID、版本、checksum 和选中的用例。
- 可选的已发布 XpertVersion 基线。
- 1–5 个已发布 XpertVersion 或固定 revision 的 Authoring Proposal 候选。
- 完整 workflow、资源固定版本、配置 checksum 和 Proposal revision。
- 创建时的 Knowledge 活动索引版本。
- Agent Table 查询固定 SchemaVersion、查询契约和同一只读事务内捕获的逐用例结果夹具。
- Data X 已发布资源及外部 Xpert 固定版本。
- seed、模型策略、Judge 模型和预算。

`snapshot` 模式保留每个目标自己的模型配置。`override` 模式只在评测临时快照中
替换模型，不修改 Xpert、Proposal 或已发布版本。

Proposal 在运行后发生变化时，旧报告标记为 `stale`，但运行快照和已完成结果
保持不变。

## 4. 只读安全预检

评测执行复用 classic workflow runner，不通过 HTTP 回环。进入 runner 前必须通过
fail-closed 预检：

- 禁止 Handoff、Automation、HITL、Human Intervention 和等待型执行。
- 禁止 Memory、Todo、Knowledge/Data X/Authoring 写入。
- 禁止 Browser、Client Tools、Sandbox 写入和 Skill/Plugin Hook。
- Toolset 只允许已发布版本中 `read_only=true` 且 `sensitive=false` 的工具。
- External Xpert 必须固定版本，并递归通过同一预检。
- Knowledge 查询固定到运行创建时的活动索引版本。
- Agent Table Query 仅在谓词可由运行输入、常量或确定性纯节点推导时允许；结果必须
  在运行创建时固化，重试和重启不得回退读取活表。
- 夹具捕获与真实运行共用同一套会话裁剪和 Prompt Profile `{{args}}` 渲染；持久化记录
  具有规范化内容 checksum，恢复时校验失败即关闭。私有夹具会从创建、取消、列表和
  详情响应中统一剥离。
- 使用 `error_output` 的资源节点仅在 `success` 路径产生 `result`；错误路径引用该输出
  会在控制流分析阶段被拒绝。
- Plugin 只有在展开后的 Toolset、middleware 和 Skill 均为只读安全能力时才允许。

评测模式不会创建会话、写记忆、生成提案或进入人工审批队列。任何
`RuntimeInterrupt` 或等待事件都按该样例失败处理。

## 5. 执行预算与恢复

`XpertEvaluationExecutor` 是单进程文件型后台执行器。每个 run 按
用例、目标和 repetition 保存工作项状态；容器重启后只重置未完成项，已完成项
不会重复执行。

预算范围：

- repetitions：1–3。
- max concurrency：1–4。
- 单用例 timeout：10–600 秒。
- 单目标用例模型调用、工具调用和估算 token 上限。
- 最终输出最大保存 20,000 字符。

模型和工具调用继续通过 `execution_operation` 计数。网关提供 usage 时优先使用；
否则报告明确使用保守 token 估算。单个样例失败、超时或耗尽预算时计 0 分，不影响
其他工作项。

RunRegistry 根类型为 `xpert_evaluation`，目标 Xpert 和节点 run 继续挂在其下。
checkpoint 仅记录 ID、状态、数量、耗时和安全错误摘要。

## 6. 指标与报告

内置指标：

- `exact_match`
- `contains`
- `json_schema`
- `citation_hit`
- `rubric_judge`
- `workflow_path_match`
- `workflow_resource_match`

`workflow_path_match` 只读取 classic runner 写入内部 checkpoint 的 Planner ref、
语义 outcome、终点来源和受限错误码。它不新增 Workflow SSE 事件，也不从物理节点 ID
或 native handle 猜测路径。缺少 Planner ref 的旧手工作业返回 unsupported warning。
预期错误终点必须同时匹配路径和安全错误码，并且不得与文本答案指标混用。

`workflow_resource_match` 只比较受限资源证据：Planner ref、资源 ID、固定知识版本或
SchemaVersion、查询契约 checksum、命中数以及可选 record/citation ID。Agent Table
夹具最多 1,000 个、单查询 200 行、合计 16 MiB，只保存在私有 Evaluation Store；API、
报告和 checkpoint 均不返回记录正文。未声明 `resource_reads` 的旧 Dataset 可继续运行，
但只读资源目标会标记 `resource_evidence=missing`，不能据此宣称资源能力已验证。

LLM Judge 使用固定模型、温度 0 和严格 JSON，只保存 0–1 分数、通过状态与最多
500 字符理由，不保存隐藏推理。

报告包含：

- 各目标加权总分和各指标分数。
- 候选相对基线的 score delta 和 win/tie/loss。
- 失败、超时和预算错误分布。
- 平均与 P95 延迟。
- 模型调用、工具调用和实际或估算 token。
- 资源漂移与外部 Provider 不完全可复现 warning。
- 逐样例截断输入、预期、最终输出和安全错误摘要。
- 逐样例的语义 outcome、成功来源或预期错误终点证据。
- 逐样例的固定资源快照、查询契约与受限资源读取匹配结果。

## 7. API

```text
GET/POST /api/xpert-evaluations/datasets
GET/PATCH /api/xpert-evaluations/datasets/{dataset_id}
POST      /api/xpert-evaluations/datasets/{dataset_id}/cases
POST      /api/xpert-evaluations/datasets/{dataset_id}/import
POST      /api/xpert-evaluations/datasets/{dataset_id}/import-conversations
POST      /api/xpert-evaluations/datasets/{dataset_id}/publish
GET       /api/xpert-evaluations/datasets/{dataset_id}/versions
POST      /api/xpert-evaluations/preflight
GET/POST  /api/xpert-evaluations/runs
GET       /api/xpert-evaluations/runs/{run_id}
POST      /api/xpert-evaluations/runs/{run_id}/cancel
GET       /api/xpert-evaluations/capabilities
GET       /api/benchmarks/capabilities
GET       /api/benchmarks/catalog
GET       /api/benchmarks/catalog/{pack_id}
POST      /api/benchmarks/catalog/{pack_id}/instantiate
POST      /api/benchmarks/generations/preflight
GET/POST  /api/benchmarks/generations
GET       /api/benchmarks/generations/{job_id}
POST      /api/benchmarks/generations/{job_id}/cancel
POST      /api/benchmarks/calibrations
GET       /api/benchmarks/calibrations/{job_id}
```

列表只返回摘要。可信管理面的 detail 会移除 workflow、Prompt、完整 Tool 配置和
私有 feature，只返回评测所需的截断内容。

## 8. 前端入口

- `/agents/evaluations`
- `/agents/evaluations/:runId`

Meta Planner V2 候选提供“评测候选”入口，并固定当前 Proposal revision。
Xpert Studio 的已发布版本提供“版本回归评测”入口。工作台按“标准基准 / 针对性生成 /
我的评测集 / 运行报告”组织，支持目录实例化、目标分析、生成与校准、数据集编辑/导入、
发布、基线与候选选择、模型策略、预算、预检、运行、取消和报告查看。

## 9. 安全边界

- 不执行真实副作用、附件、持久写入或交互审批。
- 不返回完整 prompt、工具原始输出、密钥、物理路径或 Runtime Store。
- 不把外部 Provider 可变响应描述为完全确定性结果。
- 不允许报告自动改变 Proposal 或 Xpert 状态。
- 不允许 Evaluator 成为线上流量入口。

## 10. 回归

最小验证：

```bash
python -m pytest server/tests/test_xpert_evaluations.py -q
python -m pytest server/tests/test_benchmark_catalog.py server/tests/test_benchmark_generator.py -q
python -m pytest server/tests/test_meta_agent.py server/tests/test_xpert_publish.py -q
cd client
npm.cmd run build
```

变更安全预检或 runner capture 时，还必须覆盖 Toolset、Knowledge、Data X、
Authoring、App 和 RunRegistry 回归。

## 11. Evolution 内部复用

Prompt Evolution 通过 Evaluator 的内部固定快照入口复用相同的只读安全预检、
执行预算、指标和报告计算。它不会创建临时 Authoring Proposal，也不会扩展公开
Evaluation Target 类型。

- 每代训练评测只接收固定 DatasetVersion 的优化集。
- 最终基线与 finalist 比较只接收验证集。
- 每个临时快照固定 workflow、资源、模型策略和 checksum。
- Evolution 取消时同时取消当前子评测；重启后跳过已经完成的子评测工作项。
- Evaluator 仍不审批 Proposal、不写草稿、不发布版本。

Prompt 搜索、非退化门禁和 Proposal 契约见
[EVOAGENTX_EVOLUTION.md](./EVOAGENTX_EVOLUTION.md)。

Structure Evolution 也复用该内部入口。候选只有在类型化 mutation 编译、Workflow
校验、资源授权、发布预检和只读安全预检全部通过后才创建 Evaluation Run；静态失败
候选不消耗评测预算。结构 finalist 的 Holdout 报告额外用于模型调用、Token、P95
延迟和图复杂度门禁，Evaluator 本身仍不批准 Proposal 或修改草稿。
