# CW10 控制依赖诊断真实复测

## 结论

本次真实预览器复测失败，不能通过 CW10 人工验收或 PR 门禁。模型在唯一修复中新增了控制边，但仍未得到路径安全的候选。不得把三次完整响应、Runtime 的 `completed` 或待审批 Proposal 的存在解释为有效生成。

本轮仅复测与取证，没有继续修改生产代码、扩大授权、追加调用或调整业务条件。

## 冻结范围

- 工作树：`codex/meta-planner-controlled-writes-10-closeout@a7d99925584e818e7f2df84b1646256abfc08ed6`，保留既有未提交变更。
- 用户本轮明确授权复测；沿用原目标、OpenRouter / `deepseek/deepseek-v4-flash-0731`、一次生成、最多三次 completion。
- 合成表 `table_7565c77441414604b64c01739977f1d5`，仅 Query 及 `update/status/最多 1 行` 的候选授权。活表零记录，不执行写节点或评测。
- Agent 上限 2，temperature 0.2；不启用 Toolset、中间件、知识库、视觉、插入或删除。
- 请求 checksum：`eae0f5aeb6511264f54d13bd3ce96d85ddfddaeb00d7ac0c8483bbce0f5f7fe8`，与上一轮一致。
- 相对上次冻结的 106 个文件，仅三个约定生产文件变化，另纳入新测试和修复审计，共固定 108 个文件。变更内容见 `META_PLANNER_CONTROL_DEPENDENCY_REPAIR_20260919.md`。
- 独立后端重新加载修复；复用原有 egress/调用预算守卫，不修改历史启动器和账本。页面刷新后恢复同一表单配置，再实际点击一次“生成候选智能体”。没有通过脚本 POST 代替 UI 操作。

## 实测结果

- 开始时间：2026-09-19 23:29:27，America/Phoenix；UTC 为 2026-09-20 06:29:27。
- Run：`7d4d9a5e-e223-4963-a399-2bafd6eee925`。
- Proposal：`proposal_33709f32c5f147b9aa071d206b800641`，`pending`，revision 1。
- `validation_valid=false`，`repair_used=true`，`candidate_origin=server_synthesized_fallback`。

| 阶段 | 真实响应 | 耗时 | Provider 报告 Token |
| --- | --- | --- | --- |
| 任务规划 | HTTP 200，完整 stop | 27.914 秒 | 3,133 |
| 能力编译 | HTTP 200，完整 stop | 53.678 秒 | 15,612 |
| 唯一 Graph Patch 修复 | HTTP 200，完整 stop | 5.408 秒 | 17,013 |

合计 3 次派发、3 次完整响应、0 次失败传输、0 次不确定派发，报告 Token 35,758。这里只记录 Provider usage，未核验账单。总调用额度已经用完，没有自动重发。

## 已确认失败边界

首次候选记录 6 项契约问题；唯一修复完成 4 项操作：`update_node` 1 项、`connect_data` 1 项、`connect_control` 2 项。Patch parse、normalization、apply 通过，原始和归一化操作数量均为 4；控制图 checksum 确实变化。错误数量变化不是质量改善证据。

修复后仍有五项模型候选问题：

1. `check_score` 的可达输入会触发 `CONDITION_FIELD_REQUIRES_OBJECT`，路径保护不足。
2. 场景 4 同时到达成功终点 `explain_agent` 和错误终点 `terminate_not_found`。
3. `query_batch.result` 不保证在 `update_status.records` 的所有到达场景中可用。
4. `query_batch.result` 不保证在 `serialize_query.value` 的所有到达场景中可用。
5. `query_result` 不是 `serialize_query` 的控制可达变量。

统一校验阶段记为 `authorization` 失败，后续 resolve/compile/publish 被阻断。该阶段包含契约与控制流检查，不应误读为用户没有授予本表 update 权限。

页面展示的是诊断占位候选；其中 `missing_workflow_agent_model` 等占位图问题与上述模型原图问题分列，不作为模型遗漏模型配置的证据。

## 传输与归因边界

- 三阶段实际请求 Schema checksum 与预期契约一致。
- 三阶段 Provider 到 collector 的 body checksum 一致；结构投影未发现变化。
- 能力编译结果在 Provider、collector 和 validator 的严格 Schema 检查均为 false；Graph Intent 接受/归一化与严格投影检查是不同层级，不能据此声称网络截断或 collector 丢图。
- 该阶段 collector 与 validator 的 body checksum 不同，但结构投影相同；任务规划和 Patch 两阶段 body checksum 均一致。不将所有 hash 宣称为完全相同。
- 本次完整原始模型 Graph、Prompt 和隐藏推理未落盘。新增依赖反例在真实 repair Prompt 中的具体内容也未单独捕获。源码冻结及离线测试证明机制已加载，但不能用一次新生成的结果建立“新增反例导致模型接控制边”的因果结论。
- 与上次不同，本次不能归因为“唯一修复没有修改控制边”。已验证的是：修改了控制边仍然没有满足整体路径约束；无法据此判断单模型架构或模型能力的绝对上限。

## 安全与既有门禁

- 108 个冻结文件、9 份历史账本、原始 guard/server 和本次 launcher hash 均核验不变。
- 后端只记录一次生成 POST；Workflow 和 Evaluation Store hash 不变。
- 合成表复测后仍为 0 条记录、0 项操作；没有批准 Proposal、发布 Xpert 或执行 Workflow/Evaluator。
- 页面加载视觉目录时出现 `ACCEPTANCE_EGRESS_NOT_APPROVED` warning：独立守卫拒绝未授权目录网络请求，视觉未启用，不是本次 Planner 校验失败原因。
- 前一修复批的 117 项重点测试、1,119 项相邻回归与构建证据已核对 hash，本次不宣称重新执行这些检查。
- 全量入口仍保留此前 94 通过、1 个既有 Worker 断连测试失败、余下未运行的结果。全量门禁未通过，不混入本次修复。

## 证据与下一步边界

本次临时目录 `.tmp-cw10-control-dependency-retest-20260919/` 被 Git 忽略，不提交 Runtime 数据、日志或调用账本。

| 证据 | SHA-256 |
| --- | --- |
| `source-receipt.json` | `5d598dc081cbe6c7e336a40f4f6c3f45b633fd423407b2d192f7152ea8c4ddfe` |
| `calls.json` | `4ee134fe0578ee157c8827e6628bfa3afd2b41dc3051c8130c21b940ff5a2cc8` |
| `retest-proof.json` | `3e591298ac1355667be1ed0dabb81a3ef0b31f35f4d085fb77bd4195c354aeb0` |
| `validation-proof.json` | `87bcac6df84abac4bbcab670a306696432536d9e5aa5f142bca7ec855dbbaabd` |

下一步应先针对本次“空值保护、终点互斥、控制先后”联合失败做离线核对，再决定是否需要改变修复策略；不继续按单条错误盲目追加提示词或边。本次未实施该后续工作，未创建 Commit、Push 或 PR。
