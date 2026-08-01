# EvoAgentX 受控进化

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

## 9. 工作流结构进化

`EVOAGENTX-EVOLUTION-03B` 在同一 Store、Executor、Evaluator 和 Proposal 审批路径上
增加 `evolution_kind=structure`。它不允许模型输出代码或完整 workflow，只接受以下
类型化操作：

- `add/remove/replace_control_node`
- `add/remove_control_edge`
- `bind/unbind_resource`
- `bind/unbind_middleware`

候选局部 `ref` 只能引用同一候选中新建的节点。真实节点 ID、边 ID、位置和五类特殊绑定
handle 均由确定性编译器生成。输入和输出节点受到保护；现有 Agent 的 Prompt、模型和输出
契约不会被结构 mutation 改写。

## 10. 能力快照与静态门禁

Structure Run 固定 Meta Planner Capability Snapshot、用户授权范围和默认新增 Agent 模型。
控制节点取 Workflow Registry 与 Evaluator 安全集合的交集。外部 Xpert、Knowledge、
Toolset、Plugin 和 middleware 只有在用户显式授权后才可引用。

每个候选在评测前依次经过：

1. Mutation Pydantic Schema。
2. Capability Snapshot 与授权检查。
3. 确定性 mutation 编译。
4. Registry 与 `validate_workflow_graph`。
5. 资源版本、协作循环和名称冲突检查。
6. 无副作用发布预检。
7. Evaluator 只读安全预检。
8. 结构 checksum 去重。

静态失败候选保留安全 issue 摘要，但不会创建 Evaluation Run。候选图接口隐藏 Prompt、
middleware config、工具输出和凭据，只返回结构、位置和 diff。

## 11. 质量、成本与复杂度门禁

训练集排名依次比较总分、失败数、模型调用、estimated token、P95 延迟、图复杂度和
checksum。最多三个 finalist 与原始基线在隔离 Holdout 上比较。

默认晋级要求：

- 总分至少提升 `0.01`。
- 任一有权重指标回退不超过 `0.02`。
- 不新增失败、超时、预算耗尽或安全错误。
- 新增节点不超过四个。
- 模型调用、estimated token 和 P95 延迟相对基线的增加比例均不超过 `1.0`。

通过后只创建 pending `xpert_update` Proposal，并附 mutation manifest、结构 diff、能力
快照 hash 与评测报告。批准 Proposal 只更新 Xpert 草稿，仍需在 Studio 显式发布。

## 12. 结构候选 API

现有 Evolution API 通过 `evolution_kind` 保持 Prompt 请求兼容，并新增：

```text
GET /api/xpert-evolutions/runs/{run_id}/candidates/{candidate_id}/graph
```

容器重启后恢复代际、候选、子 Evaluation Run、排名和门禁。目标草稿 revision 变化时
run 标记 `stale`，不创建 Proposal。

## 13. 上游归因与路线收口

结构搜索只适配 EvoAgentX `v0.1.4@aad19b9` 中 SEW/AFlow 的有界候选、代际选择和早停
思想。ModelMirror 独立实现类型化 mutation、Workflow 编译、固定快照、Evaluator、
Runtime 和审批；未复制动态图、代码生成、文件替换或 EvoAgentX Runtime。

`EVOAGENTX-EVOLUTION-03B` 完成后暂停能力扩张。下一阶段只做进化收益、运行成本和技术债
审计，以真实 Dataset 和运行报告决定是否继续引入其他优化器。
