# Xpert Automation Runtime

最后更新日期：2026-07-18

## 目标

Automation Runtime 把已发布 Xpert 固定为可持久调度的私有执行单元：

`once / interval / cron -> 固定 XpertVersion -> classic workflow runner -> wait / retry / dead letter -> result`

它复用 Xpert、Toolset、HITL、Client Tools、Knowledge、RunRegistry 与 checkpoint，不创建第二套 Agent 执行器。公开 Xpert App/API 不开放自动化中间件。

## 调度模型

- `AutomationDefinition` 保存名称、任务提示、目标 Xpert、固定发布版本、触发器、重叠/误触发策略、重试和预算。
- 触发器支持单次时间、固定秒数间隔和五字段 Cron；Cron 必须携带 IANA 时区。
- `overlap_policy=skip|allow` 控制上一次仍运行时是否创建新 execution。
- `misfire_policy=skip|latest|catch_up` 控制服务离线期间错过的时间点。
- 每日/总运行次数和最长运行时间属于服务端预算，不能由模型绕过。
- occurrence ID、execution lease 和固定 XpertVersion 保证单进程重启恢复与幂等派发。
- 临时失败按配置重试，耗尽后进入 `dead_letter`；人工可以重试或取消。

Automation 通过文件 Store 原子保存到 Runtime storage。RunRegistry 使用 `run_type=automation`，子级仍是 Xpert 和节点 run。checkpoint 只记录 ID、版本、状态、耗时和安全错误摘要。

## 等待与恢复

自动化执行可以进入 `waiting_approval` 或 `waiting_client`。HITL/Client Tool 协调器解决等待后会更新同一个 AutomationExecution，继续原 execution 而非重复创建调度 occurrence。等待、失败和恢复都不得重复调用已经完成的有副作用工具。

## Agent 中间件

### Scheduler

`scheduler` 向私有 `workflow_agent` 提供 `automation_create/list/get/pause/resume/run_now/archive`。工具只能操作当前已发布 Xpert 的固定版本，并继续经过 tool policy、middleware 和 audit。使用该能力要求 Runtime 工具模式。

### Ralph Loop

`ralph_loop` 在 Agent 最终结果阶段执行有界的“继续改进 -> 严格 JSON 验证”循环。它限制迭代次数与累计输出字符，检测连续无进展，并可使用独立 verifier 模型。验证未完成会进入节点现有 retry、fallback 和异常处理，不静默返回伪成功。

### Knowledge Writer

`knowledge_writer` 复用 Knowledge Agent 的审批写入链。显式工具调用或配置开启的已验证最终输出只能创建 pending proposal；仍须在 Knowledge Inbox 审批、构建候选、通过 Evaluation Gate 后推广，不能直接修改活动索引。

### Plugin Hooks

`plugin_hooks` 只读取用户已安装 Skill 中显式的 `modelmirror-hooks.json`。支持 `SessionStart`、`PreToolUse`、`PostToolUse` 和 `SessionEnd`。Hook 文件先安全 staging 到当前隔离工作区，再通过无网 Sandbox 以 argv 方式运行；不允许 shell 字符串、在线下载、仓库或密钥访问。可配置 fail-open 或 fail-closed。

## 管理接口

- `GET/POST /api/runtime/automations`
- `GET/PATCH /api/runtime/automations/{automation_id}`
- `POST /api/runtime/automations/{automation_id}/pause|resume|run-now|archive`
- `GET /api/runtime/automation-executions`
- `POST /api/runtime/automation-executions/{execution_id}/retry|cancel`
- `GET /api/runtime/automation-coordinator/status`

前端入口为 `/agents/automations`。工作台用于创建、筛选、暂停、恢复、立即运行、查看历史和处理死信，不承担新的执行逻辑。

## 安全与边界

- 只有存在且已发布的 Xpert 可以被固定和调度；草稿变化不会改变已创建自动化。
- Automation Toolset 不能跨 Xpert 管理其他自动化。
- 日志、API、audit 和 checkpoint 不保存完整 prompt、模型输出、Knowledge proposal 内容、Hook 输出或密钥。
- `scheduler`、`ralph_loop`、`plugin_hooks` 和 `knowledge_writer` 均被 Xpert App 部署预检拒绝。
- 当前实现面向单后端进程和文件 Store，不宣称分布式调度、精确一次语义或组织级权限。

## 验收护栏

变更自动化路径至少运行 Automation Store/Coordinator、Ralph、Plugin Hooks、workflow validate、workflow Agent、App 预检、HITL/Client continuation、RunRegistry 和全量后端测试，并完成前端生产构建。Docker 验收必须覆盖固定版本、Cron 时区、重叠/误触发、预算、重试/死信、等待恢复、Knowledge pending proposal、离线 Hook 和容器重启恢复。
