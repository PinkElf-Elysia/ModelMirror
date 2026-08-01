# EvoAgentX Xpert Evaluator

最后更新日期：2026-07-25

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
- LLM Judge rubric。
- 指标权重。

数据可通过管理页面人工编辑、JSON/CSV 导入，或从用户显式选择的 Xpert 会话
导入。会话导入不复制附件、记忆、物理路径或内部 Runtime 上下文。

## 3. 固定快照

每次 Evaluation Run 固定：

- Dataset ID、版本、checksum 和选中的用例。
- 可选的已发布 XpertVersion 基线。
- 1–5 个已发布 XpertVersion 或固定 revision 的 Authoring Proposal 候选。
- 完整 workflow、资源固定版本、配置 checksum 和 Proposal revision。
- 创建时的 Knowledge 活动索引版本。
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
```

列表只返回摘要。可信管理面的 detail 会移除 workflow、Prompt、完整 Tool 配置和
私有 feature，只返回评测所需的截断内容。

## 8. 前端入口

- `/agents/evaluations`
- `/agents/evaluations/:runId`

Meta Planner V2 候选提供“评测候选”入口，并固定当前 Proposal revision。
Xpert Studio 的已发布版本提供“版本回归评测”入口。工作台支持数据集编辑/导入、
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
