# Xpert 长期 Goal

最后更新日期：2026-07-10

## 目标

长期 Goal 把一次 Xpert 对话转成可持续执行的协作任务：Planner Xpert 生成依赖计划，用户审核后，GoalCoordinator 按依赖派发已发布 Xpert，并汇总最终步骤结果。

当前实现坚持人工审核。自动规划完成后状态进入 `awaiting_review`，不会直接执行。

## 数据模型

`ConversationGoal` 保存目标、Planner 与固定版本、有限对话快照、计划 revision、最终步骤、并发上限、结果、错误和 Goal run ID。

`GoalStep` 保存 instruction、目标 Xpert 与固定版本、依赖、执行状态、AgentTask/Handoff/Xpert run ID、结果、错误和尝试次数。

生产环境使用与 AgentTask 相同的 `AGENT_TASK_STORAGE_DIR`，原子写入 `goals.json`。当前只保证单后端进程内一致性。

## 状态机

Goal：

`planning -> awaiting_review -> running -> paused / needs_attention / completed / cancelled`

Step：

`pending / running / completed / failed / blocked / skipped / cancelled`

- pause 只停止派发新步骤，已运行请求允许完成并保留结果。
- cancel 禁止后续派发，不强制终止已发出的模型请求。
- dead-letter 或 rejected Handoff 会使步骤 failed、下游 blocked、Goal 进入 needs_attention。
- retry、reassign、skip 是显式人工恢复动作。

## Planner 契约

Planner 必须是已发布 Xpert。输入包含 objective、最近最多 20 条对话，以及最多 200 个已发布 Xpert 的 ID、slug、名称和简介。

Planner 只返回 JSON：

```json
{
  "summary": "计划摘要",
  "final_step_id": "deliver",
  "steps": [
    {
      "step_id": "research",
      "title": "收集资料",
      "instruction": "输出关键结论",
      "target_xpert_id": "published-xpert-id",
      "depends_on": []
    }
  ]
}
```

服务端要求 2 到 20 步、ID 唯一、依赖存在、图无环、目标 Xpert 已发布，并且最终步骤可传递依赖所有其他步骤。非法计划进入 `needs_attention`。

计划保存必须携带 `plan_revision`，旧 revision 返回冲突，避免覆盖其他编辑。

## 调度与执行

单 Goal 默认最多并行两个 ready 步骤。每一步创建 AgentTask 与显式 `xpert_auto` Handoff，HandoffExecutor 使用固定发布版本运行目标 Xpert。

步骤输入包含 Goal objective、当前 instruction 和依赖结果，总长度上限 20,000 字符；超限时带标记截断。最终步骤完成后，其结果写入 `Goal.result`。

运行层级：

`goal run -> planner xpert run / agent_task run -> handoff run -> target xpert run -> node runs`

checkpoint 只记录状态、ID、长度、版本和错误摘要，不保存完整 prompt、工具输出或密钥。

## API

- `POST /api/runtime/goals`
- `GET /api/runtime/goals?status=&search=&limit=`
- `GET /api/runtime/goals/{goal_id}`
- `POST /api/runtime/goals/{goal_id}/plan`
- `PATCH /api/runtime/goals/{goal_id}/plan`
- `POST /api/runtime/goals/{goal_id}/start`
- `POST /api/runtime/goals/{goal_id}/pause`
- `POST /api/runtime/goals/{goal_id}/resume`
- `POST /api/runtime/goals/{goal_id}/cancel`
- `POST /api/runtime/goals/{goal_id}/steps/{step_id}/retry`
- `PATCH /api/runtime/goals/{goal_id}/steps/{step_id}`
- `POST /api/runtime/goals/{goal_id}/steps/{step_id}/skip`
- `GET /api/runtime/goal-coordinator/status`

前端入口为 `/agents/goals` 和 `/agents/goals/:goalId`。已发布 Xpert 聊天页可将最近对话转为长期 Goal。

## 恢复与边界

服务重启后，文件 Store 恢复 Goal。running Goal 会重新进入协调循环；RunRegistry 是内存态，因此恢复时创建新的 goal run，并记录前一个 run ID。

Goal 仍不实现数据库、Redis、Celery、强制取消模型请求或跨进程 lease。文件/记忆和公开 App/API 已由后续独立模块提供，但 App 调用不会创建 Goal，也不会开放附件或记忆写入。
