# EvoAgentX Prompt 受控进化

## 1. 边界

`EVOAGENTX-EVOLUTION-03A` 在 Meta Planner V2 和 Xpert Evaluator 之上提供
Prompt 候选搜索。它只允许修改以下内容：

- 一个 Xpert 草稿中最多三个 `workflow_agent.rolePrompt` 或
  `workflow_agent.promptSuffix` 字段。
- 一个 Prompt Profile 草稿的 `template`。

节点、边、模型、资源绑定、中间件、Toolset、Knowledge 和 Plugin 均被冻结。
Evolution 不批准 Proposal、不写草稿、不发布版本，也不替换线上 Xpert。

## 2. 固定快照

每个 run 固定以下输入：

- 目标 ID 与草稿 revision。
- 已发布 DatasetVersion。
- Prompt 字段集合，或 Prompt Profile 的固定评测宿主 XpertVersion。
- optimizer、Evaluator model policy、judge model、seed 和执行预算。
- `min_score_delta` 与 `max_metric_regression` 门禁。

Xpert 候选使用完整临时 Xpert 快照执行。Prompt Profile 候选只改变传给固定
宿主版本的 `{{args}}` 渲染模板。两种模式都复用 Evaluator 的只读
fail-closed 预检和 classic workflow runner，不创建临时 Authoring Proposal。

## 3. 数据隔离

- 用例不少于五条时，按固定 seed 做互斥的 80/20 优化集与验证集拆分。
- 用例少于五条时允许共用，但报告必须保留高过拟合风险 warning。
- optimizer 只能看到优化集及前一轮的安全失败摘要。
- 验证集仅用于最终基线与最多三个 finalist 的比较。
- DatasetVersion 在运行期间不可漂移。

## 4. 有界搜索

默认搜索为两代、每代四个候选。允许范围为一至三代、每代二至五个候选。
每一代最多调用 optimizer 一次，并在 JSON 契约无效时修复一次。

候选必须：

- 精确保留每个字段原有的模板变量集合。
- Prompt Profile 精确保留一个 `{{args}}`。
- 不包含凭据、本地路径、隐藏推理请求或长评测样例复制。
- 通过 Xpert 发布预检和 Evaluator 只读安全预检。

候选按规范化 hash 去重。训练排名依次使用总分、失败数、estimated token、
延迟和 checksum，保证结果稳定。达到满分或本代无提升时可以早停。

## 5. 非退化门禁

最终验证默认要求：

- 总分相对基线至少提升 `0.01`。
- 任一指标回退不超过 `0.02`。
- 不新增失败、超时、预算耗尽或安全错误。
- 目标草稿 revision 未发生变化。

通过后只创建一个 pending Proposal：

- Xpert 模式：`xpert_update`。
- Prompt Profile 模式：`prompt_profile_update`。

批准 Proposal 只写目标草稿。用户仍需在 Xpert Studio 或 Prompt 页面显式发布。
未通过时 run 进入 `no_improvement`，保留候选、diff 和评测报告，不创建 Proposal。

## 6. API

```text
GET  /api/xpert-evolutions/capabilities
POST /api/xpert-evolutions/preflight
GET  /api/xpert-evolutions/runs
POST /api/xpert-evolutions/runs
GET  /api/xpert-evolutions/runs/{run_id}
POST /api/xpert-evolutions/runs/{run_id}/cancel
```

管理侧 run detail 可以查看 Prompt diff。RunRegistry checkpoint 只保存候选 ID、
hash、分数、长度、预算和错误摘要，不保存完整 Prompt、样例正文或模型隐藏推理。

## 7. 恢复与取消

`XpertEvolutionStore` 使用 Runtime 持久化目录和原子 JSON 写入。每一代候选、
Evaluator run ID、排名和最终门禁均持久化。容器重启后：

- 已完成的 optimizer 生成不会重复。
- 已存在的 Evaluator run 继续由 Evaluator 恢复。
- 已完成候选不会重新执行。
- 取消会向当前子评测传播，且不会误建 Proposal。

## 8. 上游归因

本轮仅适配 EvoAgentX `v0.1.4@aad19b9` 中 EvoPrompt 的有界 mutation、
selection 和 early-stop 概念。ModelMirror 独立实现 Store、模型网关、Evaluator、
Runtime、日志、数据集和审批；未复制 EvoAgentX Runtime 或第三方优化器实现。

下一步 `EVOAGENTX-EVOLUTION-03B` 只允许类型化的工作流结构 mutation，并继续
使用同一 Evaluator 与人工审批门禁。
