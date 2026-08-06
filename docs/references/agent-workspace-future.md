# Agent Workspace 后续参考边界

本文件只记录未来适配方向，不代表当前存在对应 API、页面或运行能力。不得用
空壳页面、静态成功响应或提示词声明替代真实实现。

## Round 2：执行面

后续运行时在独立 `/api/agent-workspace` 命名空间内实现 SQLite Session、Task、
Message、审批和递增事件序号，不复用或改造 `/api/chat` 的 JSON 工具决策链。
九个工具必须经过 Workspace 路径限制、进程归属、审批策略与低权限执行护栏。

模型和凭据继续使用现有 newAPI / OpenRouter 配置；不新建模型库、价格中心或
密钥保险柜。模型不支持原生 Tool Calling 时返回明确能力错误，不模拟成功。

## Round 3：恢复与硬化

长任务插话、排队、`/compact`、`/goal`、上传、SSE 补发、快照导入导出和
重启恢复在安全约束完成后实现。同容器低权限执行只能作为 v1 护栏，不宣称是
恶意多租户级沙箱。

## Semantic Router

本阶段不引入 Semantic Router。未来评估时只允许它作为模型/意图路由策略层，
不得接管 Agent State、Session、工具审批或 Workspace 权限。接入前至少确定：

- 路由输入是否只包含最小必要的脱敏特征；
- 路由失败、超时和低置信度时的确定性回退；
- 路由结果是否可解释、可记录但不演变为成本/Trace 空壳模块；
- 不支持工具调用的模型不会被分配到需要九工具的任务。

## 未来画布 Agent 节点契约

第三轮只允许定义稳定契约，不修改经典工作流节点或 `NativeNodeKind`：

```text
input:
  agent_id: string
  session_id?: string
  workspace_id?: string
  message: string

events:
  session_id: string
  sequence: integer
  status: queued | running | awaiting_approval | completed | failed | interrupted
  type: task_status | text_delta | thinking_delta | tool_call | approval | tool_output | subagent | final

output:
  session_id: string
  workspace_id: string
  final_text: string
  terminal_status: completed | failed | interrupted
```

画布节点只能调用稳定 Agent Workspace API；不能读取 Agent State 内部路径，
不能绕过审批或直接控制命令进程。实际节点实现必须等第三轮整体能力完成并经过
独立 PR 与验收。

## 继续延后

以下能力保持延后：MCP 工具接入、新模型供应商管理、模型定价、成本中心、
Trace 观测、Benchmark、评估、Agent 自进化、Vault、定时任务，以及外部第 17
个 Skill。若未来启动，必须各自建立任务卡、验收命令、开关与回退方案。
