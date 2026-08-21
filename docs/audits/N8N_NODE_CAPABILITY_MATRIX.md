# 工作流能力域与节点类型对照审计（#213 + R0/R1/R1.5/R1.6）

- 审计日期：2026-08-21
- 唯一基线：PR #213 合并提交 `911593f505b05b01037769f578e21f22d2a1c9af`
- R0 基线事实：NodeContract V3、37 个 `NativeNodeKind`、35 个画布目录项、20 个冻结 compatibility 合同
- R1 结果：新增 4 个完整合同，并将既有 `llm` 提升为完整合同；自研节点总数 41、画布目录项 39、当前 19 个冻结 compatibility 合同；四节点与 `llm` Planner 均关闭
- R1.5 PR1 结果：新增完整合同 `failure_event_entry`；自研节点总数 42、画布目录项 40、compatibility 白名单不增长；Planner 关闭且 Xpert 内嵌入口禁止
- R1.5 PR2 结果：新增完整合同 `workflow_call_entry` 与 `invoke_workflow`；自研节点总数 44、画布目录项 42、compatibility 白名单不增长；仅支持私有同步固定版本调用，Planner 关闭且 Xpert 内嵌入口禁止
- R1.6 结果：新增完整合同 `terminate_error`、`multi_route`、`data_aggregate`，并将 `list_operation` 提升为完整合同；自研节点总数 47、画布目录项 45、当前 18 个冻结 compatibility 合同；四类均允许经典工作流和 Xpert 使用，Planner 关闭
- 参考清单：563 条节点名称/类型，其中 `.ee` 2 条仅保留名称审计

## 结论与许可证边界

本表只把节点名称和粗粒度能力类型作为事实输入，最终分类使用模镜自己的能力域、节点名、合同和运行语义。括号列仅保留参考原名。未复制或改写 n8n 代码、参数 Schema、文案、图标、测试或 UI；`.ee` 条目排除实现参考。此工程边界降低但不能替代正式法律意见。

R1 为单实例、原子文件持久化版本，不宣称多 Worker、HA 或多租户就绪。私有 HTTP 原始入站载荷不进入触发记录或运行事件；进入 timer continuation 前，事件和正文变量会替换为大小、哈希与“恢复后不可用”标记。无同步回执的 HTTP 链路可先返回 202 再持久挂起；HTTP 回执上游仍禁止挂起，HTTP 发布版本仍禁止运行时中间件和其他交互式 continuation。为支持幂等重复返回，用户显式配置的回执正文会作为回执保存，因此回显入站数据属于用户可见的持久化选择。

## 状态汇总

- 已实现：18
- 部分实现：95
- 通用节点可覆盖：276（不等于已有专用连接器）
- 目录声明：0
- 未实现：174

| 能力域 | 总数 | 已实现 | 部分实现 | 通用覆盖 | 目录声明 | 未实现 |
|---|---:|---:|---:|---:|---:|---:|
| 触发与事件 | 112 | 6 | 1 | 0 | 0 | 105 |
| 流程控制与编排 | 8 | 4 | 3 | 0 | 0 | 1 |
| 数据变换与计算 | 17 | 6 | 10 | 0 | 0 | 1 |
| 文件与内容处理 | 20 | 0 | 5 | 10 | 0 | 5 |
| 网络与接口 | 3 | 1 | 1 | 1 | 0 | 0 |
| 数据库与存储 | 63 | 0 | 1 | 61 | 0 | 1 |
| 消息与协作 | 33 | 0 | 0 | 33 | 0 | 0 |
| 业务应用连接 | 119 | 0 | 1 | 117 | 0 | 1 |
| 开发、运维与可观测 | 32 | 0 | 0 | 32 | 0 | 0 |
| 安全与身份 | 13 | 0 | 0 | 9 | 0 | 4 |
| AI 模型与生成 | 55 | 0 | 55 | 0 | 0 | 0 |
| 智能体与任务协作 | 3 | 0 | 3 | 0 | 0 | 0 |
| 智能体工具与协议 | 14 | 0 | 3 | 11 | 0 | 0 |
| 知识检索与向量 | 48 | 0 | 8 | 0 | 0 | 40 |
| 记忆与上下文 | 9 | 0 | 0 | 0 | 0 | 9 |
| 解析、评测与护栏 | 4 | 0 | 4 | 0 | 0 | 0 |
| 交互、人工与表单 | 1 | 0 | 0 | 1 | 0 | 0 |
| 画布、评测与内部元数据 | 9 | 1 | 0 | 1 | 0 | 7 |

## 本轮直接闭环

| 模镜能力域 | 模镜自主节点名 | 内部 ID | 原名仅供参考 | 当前状态 |
|---|---|---|---|---|
| 触发与事件 | 定时启动 | scheduled_start | (Schedule Trigger) | 已实现 |
| 触发与事件 | 异常事件入口 | failure_event_entry | (Error Trigger) | 已实现 |
| 触发与事件 | 子流程入口 | workflow_call_entry | (Execute Workflow Trigger) | 已实现 |
| 触发与事件 | HTTP 事件入口 | http_event_entry | (Webhook) | 已实现 |
| 流程控制与编排 | 多路分派 | multi_route | (Switch) | 已实现 |
| 流程控制与编排 | 挂起等待 | suspend_wait | (Wait) | 已实现 |
| 流程控制与编排 | 主动终止 | terminate_error | (Stop and Error) | 已实现 |
| 流程控制与编排 | 子流程调用 | invoke_workflow | (Execute Sub-workflow) | 已实现 |
| 数据变换与计算 | 数据汇总 | data_aggregate | (Summarize) | 已实现 |
| 数据变换与计算 | 数据聚合 | data_aggregate | (Aggregate) | 已实现 |
| 数据变换与计算 | 数据排序 | list_operation | (Sort) | 已实现 |
| 数据变换与计算 | 数据去重 | list_operation | (Remove Duplicates) | 已实现 |
| 数据变换与计算 | 数据筛选 | list_operation | (Filter) | 已实现 |
| 网络与接口 | HTTP 事件回执 | http_event_reply | (Respond to Webhook) | 已实现 |
| 数据库与存储 | 内置数据表 | data_table_query / data_table_insert / data_table_update / data_table_delete | (Data table) | 部分实现 |
| 画布、评测与内部元数据 | 画布注释 | annotation | (Sticky Note) | 已实现 |

完整逐条对照见 [n8n-node-capability-matrix.csv](./n8n-node-capability-matrix.csv)。

## 门禁

- `/api/workflow/node-registry` 是新增节点的唯一权威目录；Registry 故障时本地目录全部只读。
- 前端 `WorkflowNodeKind`、后端 `NativeNodeKind`、NodeContract Registry 必须完全一致。
- Palette 必须是 NodeContract 合法子集；每个启用项必须有默认数据和配置入口。
- compatibility 合同不得超过 #213 冻结白名单；新节点必须直接提供完整合同。
- Planner 只接受完整合同、匹配 checksum 且显式启用的节点；R1、R1.5 和 R1.6 新增节点均禁止 Planner 自动生成，Planner 可生成类型仍固定为 7 类。
