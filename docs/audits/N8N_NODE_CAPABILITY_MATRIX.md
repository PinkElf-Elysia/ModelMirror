# 工作流能力域与节点类型对照审计（#213 + R0/R1/R1.5/R1.6/R1.7/R1.8/R1.9/R2.0/R2.1/R2.2/R2.3/R2.4）

- 审计日期：2026-08-25
- 唯一基线：PR #213 合并提交 `911593f505b05b01037769f578e21f22d2a1c9af`
- R0 基线事实：NodeContract V3、37 个 `NativeNodeKind`、35 个画布目录项、20 个冻结 compatibility 合同
- R1 结果：新增 4 个完整合同，并将既有 `llm` 提升为完整合同；自研节点总数 41、画布目录项 39、当前 19 个冻结 compatibility 合同；四节点与 `llm` Planner 均关闭
- R1.5 PR1 结果：新增完整合同 `failure_event_entry`；自研节点总数 42、画布目录项 40、compatibility 白名单不增长；Planner 关闭且 Xpert 内嵌入口禁止
- R1.5 PR2 结果：新增完整合同 `workflow_call_entry` 与 `invoke_workflow`；自研节点总数 44、画布目录项 42、compatibility 白名单不增长；仅支持私有同步固定版本调用，Planner 关闭且 Xpert 内嵌入口禁止
- R1.6 结果：新增完整合同 `terminate_error`、`multi_route`、`data_aggregate`，并将 `list_operation` 提升为完整合同；自研节点总数 47、画布目录项 45、当前 18 个冻结 compatibility 合同；四类均允许经典工作流和 Xpert 使用，Planner 关闭
- R1.7 结果：新增完整合同 `dataset_compare`，并将 `http_request`、`condition` 提升为完整合同；自研节点总数 48、画布目录项 46、当前 16 个冻结 compatibility 合同；Planner 仍固定为 7 类
- R1.8 结果：新增完整合同 `file_output`、`object_transform`，并将 `document_extractor`、`time_tool` 提升为完整合同，同时扩展 `list_operation`；自研节点总数 50、画布目录项 48、当前 14 个冻结 compatibility 合同；文件节点仅允许经典工作流和私有 Xpert，Planner 仍固定为 7 类
- R1.9 结果：不新增普通节点，将 `parameter_extractor`、`question_classifier` 提升为完整 V2 合同，并在既有 `runtime_middleware` 下增加 `content_policy` 文本策略；自研节点总数 50、画布目录项 48、当前 12 个冻结 compatibility 合同，Planner 仍固定为 7 类
- R2.0 结果：不新增普通节点，将 `human_intervention`、`mcp_tool`、`variable_assign` 提升为完整 V2 合同，并退役旧知识引用新增入口；当前 50 Native、48 个可新增 Palette 项、41 个完整合同、9 个 compatibility 合同、7 个 Planner 节点
- R2.1 PR1 结果：不新增 `NativeNodeKind`，将 `code` 提升为只执行预定义操作的“安全文本加工 V2”完整合同，并从 Palette 移除退役 `template_transform`；旧草稿和既有激活版本继续兼容，模板文本能力由 `variable_assign` V2 承接；当时 50 Native、47 个可新增 Palette 项、42 个完整合同、8 个 compatibility 合同、7 个 Planner 节点
- R2.1 PR2 结果：新增完整合同 `data_merge`，并将经典运行器升级为带持久化边到达账本的 Scheduler V2；支持可靠 Fan-in、有界数组拼接和受限一对一 inner join；当时 51 Native、48 个可新增 Palette 项、43 个完整合同、8 个 compatibility 合同、7 个 Planner 节点
- R2.2 PR1 结果：将 `variable_aggregator` 提升为“变量打包”V2 完整合同，修正元智能体新图的报告汇总，并为 563 行参考清单增加 exact/limited/composable/none 证据门禁；当时 51 Native、48 个可新增 Palette 项、44 个完整合同、7 个 compatibility 合同、7 个 Planner 节点
- R2.2 PR2 结果：将 `agent_task`、`agent_handoff`、`handoff_router` 提升为类型化 V2 合同，新增 occurrence 幂等索引、原子 Router 与持久 Handoff 恢复，并退役旧 `agent` 新增入口；当时 51 Native、47 个可新增 Palette 项、47 个完整合同、4 个 compatibility 合同、7 个 Planner 节点
- R2.3 结果：不新增节点类型，将 `iteration` 提升为“批量处理”V2 完整合同；本地模式执行严格数组模板映射，工作流模式以最多 32 项顺序调用固定发布版本并复用稳定子执行；当前保持 51 Native、47 个可新增 Palette 项、48 个完整合同、3 个 compatibility 合同、7 个 Planner 节点
- R2.4 结果：不新增节点类型，将 `document_extractor` 升级为“内容解析”V3；可把安全 HTTP 响应或明确共享文件解析为受限 HTML、Markdown、XML 结构或带不可信边界的文本，不提供网页渲染、选择器抽取或 XML Schema/XPath/XSLT；Registry 数量不变
- 当前 Registry 事实：51 Native、47 个可新增 Palette 项、48 个完整合同、3 个 compatibility 合同、7 个 Planner 节点
- 参考清单：563 条节点名称/类型，其中 `.ee` 2 条仅保留名称审计

## 结论与许可证边界

本表只把节点名称和粗粒度能力类型作为事实输入，最终分类使用模镜自己的能力域、节点名、合同和运行语义。括号列仅保留参考原名。未复制或改写 n8n 代码、参数 Schema、文案、图标、测试或 UI；`.ee` 条目排除实现参考。此工程边界降低但不能替代正式法律意见。

R1 为单实例、原子文件持久化版本，不宣称多 Worker、HA 或多租户就绪。私有 HTTP 原始入站载荷不进入触发记录或运行事件；进入 timer continuation 前，事件和正文变量会替换为大小、哈希与“恢复后不可用”标记。无同步回执的 HTTP 链路可先返回 202 再持久挂起；HTTP 回执上游仍禁止挂起，HTTP 发布版本仍禁止运行时中间件和其他交互式 continuation。为支持幂等重复返回，用户显式配置的回执正文会作为回执保存，因此回显入站数据属于用户可见的持久化选择。

## 状态汇总

- 已实现：35
- 部分实现：70
- 通用节点可覆盖：271（不等于已有专用连接器）
- 目录声明：0
- 未实现：187

覆盖等级用于表达证据强度：`exact` 只允许完整 NodeContract 且必须绑定运行/测试证据；`limited` 必须写明语义缺口；`composable` 只表示受控通用组合路径，不代表专用连接器；`none` 表示没有运行合同。

| 能力域 | 总数 | 已实现 | 部分实现 | 通用覆盖 | 目录声明 | 未实现 |
|---|---:|---:|---:|---:|---:|---:|
| 触发与事件 | 112 | 6 | 1 | 0 | 0 | 105 |
| 流程控制与编排 | 8 | 6 | 1 | 0 | 0 | 1 |
| 数据变换与计算 | 17 | 11 | 4 | 0 | 0 | 2 |
| 文件与内容处理 | 20 | 2 | 8 | 6 | 0 | 4 |
| 网络与接口 | 3 | 2 | 0 | 1 | 0 | 0 |
| 数据库与存储 | 63 | 0 | 1 | 61 | 0 | 1 |
| 消息与协作 | 33 | 0 | 0 | 33 | 0 | 0 |
| 业务应用连接 | 119 | 0 | 1 | 117 | 0 | 1 |
| 开发、运维与可观测 | 32 | 0 | 0 | 32 | 0 | 0 |
| 安全与身份 | 13 | 0 | 0 | 9 | 0 | 4 |
| AI 模型与生成 | 55 | 2 | 40 | 0 | 0 | 13 |
| 智能体与任务协作 | 3 | 0 | 3 | 0 | 0 | 0 |
| 智能体工具与协议 | 14 | 1 | 2 | 11 | 0 | 0 |
| 知识检索与向量 | 48 | 0 | 8 | 0 | 0 | 40 |
| 记忆与上下文 | 9 | 0 | 1 | 0 | 0 | 8 |
| 解析、评测与护栏 | 4 | 4 | 0 | 0 | 0 | 0 |
| 交互、人工与表单 | 1 | 0 | 0 | 0 | 0 | 1 |
| 画布、评测与内部元数据 | 9 | 1 | 0 | 1 | 0 | 7 |

## 本轮直接闭环

| 模镜能力域 | 模镜自主节点名 | 内部 ID | 原名仅供参考 | 当前状态 |
|---|---|---|---|---|
| 触发与事件 | 定时启动 | scheduled_start | (Schedule Trigger) | 已实现 |
| 触发与事件 | 异常事件入口 | failure_event_entry | (Error Trigger) | 已实现 |
| 触发与事件 | 子流程入口 | workflow_call_entry | (Execute Workflow Trigger) | 已实现 |
| 触发与事件 | HTTP 事件入口 | http_event_entry | (Webhook) | 已实现 |
| 流程控制与编排 | 多路分派 | multi_route | (Switch) | 已实现 |
| 流程控制与编排 | 二路条件 | condition | (If) | 已实现 |
| 流程控制与编排 | 挂起等待 | suspend_wait | (Wait) | 已实现 |
| 流程控制与编排 | 批次循环 | iteration | (Split In Batches) | 部分实现 |
| 流程控制与编排 | 数据合流 | data_merge | (Merge) | 已实现 |
| 流程控制与编排 | 主动终止 | terminate_error | (Stop and Error) | 已实现 |
| 流程控制与编排 | 子流程调用 | invoke_workflow | (Execute Sub-workflow) | 已实现 |
| 数据变换与计算 | 安全文本加工（遗留函数场景） | code | (Function) | 部分实现 |
| 数据变换与计算 | 安全文本加工（逐项场景） | code | (Function Item) | 部分实现 |
| 数据变换与计算 | 列表拆分 | — | (Split Out) | 未实现 |
| 数据变换与计算 | 列表处理（遗留） | list_operation | (Item Lists) | 已实现 |
| 数据变换与计算 | 安全文本加工（模型编排场景） | code | (LangChain Code) | 部分实现 |
| 数据变换与计算 | 日期时间处理 | time_tool | (Date & Time) | 已实现 |
| 数据变换与计算 | 安全文本加工 | code | (Code) | 部分实现 |
| 数据变换与计算 | 数据汇总 | data_aggregate | (Summarize) | 已实现 |
| 数据变换与计算 | 数据集对比 | dataset_compare | (Compare Datasets) | 已实现 |
| 数据变换与计算 | 数据聚合 | data_aggregate | (Aggregate) | 已实现 |
| 数据变换与计算 | 数据排序 | list_operation | (Sort) | 已实现 |
| 数据变换与计算 | 数据去重 | list_operation | (Remove Duplicates) | 已实现 |
| 数据变换与计算 | 数据筛选 | list_operation | (Filter) | 已实现 |
| 数据变换与计算 | 数量限制 | list_operation | (Limit) | 已实现 |
| 数据变换与计算 | 字段编辑 | object_transform | (Set) | 已实现 |
| 数据变换与计算 | 字段重命名 | object_transform | (Rename Keys) | 已实现 |
| 数据变换与计算 | Convert to/from binary data 能力节点 | — | (Convert to/from binary data) | 未实现 |
| 文件与内容处理 | 内容转文件 | file_output | (Convert to File) | 已实现 |
| 文件与内容处理 | 网页内容处理 | document_extractor | (HTML) | 部分实现 |
| 文件与内容处理 | 网页内容提取（遗留） | document_extractor | (HTML Extract) | 部分实现 |
| 文件与内容处理 | 文件内容提取 | document_extractor | (Extract from File) | 已实现 |
| 文件与内容处理 | Markdown 转换 | document_extractor | (Markdown) | 部分实现 |
| 文件与内容处理 | XML 转换 | document_extractor | (XML) | 部分实现 |
| 网络与接口 | HTTP 调用 | http_request | (HTTP Request) | 已实现 |
| 网络与接口 | HTTP 事件回执 | http_event_reply | (Respond to Webhook) | 已实现 |
| 数据库与存储 | 内置数据表 | data_table_query / data_table_insert / data_table_update / data_table_delete | (Data table) | 部分实现 |
| 业务应用连接 | 智能体消息 | agent_task / agent_handoff | (Message an Agent) | 部分实现 |
| AI 模型与生成 | 文本分类 | question_classifier | (Text Classifier) | 已实现 |
| AI 模型与生成 | 信息提取 | parameter_extractor | (Information Extractor) | 已实现 |
| AI 模型与生成 | Clearbit 智能服务 | — | (Clearbit) | 未实现 |
| AI 模型与生成 | Cortex 智能服务 | — | (Cortex) | 未实现 |
| AI 模型与生成 | DeepL 智能服务 | — | (DeepL) | 未实现 |
| AI 模型与生成 | Dropcontact 智能服务 | — | (Dropcontact) | 未实现 |
| AI 模型与生成 | Humantic AI 智能服务 | — | (Humantic AI) | 未实现 |
| AI 模型与生成 | Hunter 智能服务 | — | (Hunter) | 未实现 |
| AI 模型与生成 | Jina AI 智能服务 | — | (Jina AI) | 未实现 |
| AI 模型与生成 | LingvaNex 智能服务 | — | (LingvaNex) | 未实现 |
| AI 模型与生成 | Mailcheck 智能服务 | — | (Mailcheck) | 未实现 |
| AI 模型与生成 | Mindee 智能服务 | — | (Mindee) | 未实现 |
| AI 模型与生成 | OpenThesaurus 智能服务 | — | (OpenThesaurus) | 未实现 |
| AI 模型与生成 | Peekalink 智能服务 | — | (Peekalink) | 未实现 |
| AI 模型与生成 | uProc 智能服务 | — | (uProc) | 未实现 |
| 智能体与任务协作 | 通用智能体 | workflow_agent | (Agent) | 部分实现 |
| 智能体与任务协作 | 智能体工具 | workflow_agent | (AI Agent Tool) | 部分实现 |
| 智能体工具与协议 | MCP 单工具连接 | mcp_tool | (MCP Client Tool) | 已实现 |
| 智能体工具与协议 | MCP 工具集连接 | mcp_tool | (MCP Client) | 部分实现 |
| 智能体工具与协议 | MCP 注册表连接（内部） | mcp_tool | (MCP Registry Client (internal)) | 部分实现 |
| 记忆与上下文 | 对话记忆管理 | workflow_agent | (Chat Memory Manager) | 部分实现 |
| 解析、评测与护栏 | 结构化结果解析 | parameter_extractor | (Structured Output Parser) | 已实现 |
| 解析、评测与护栏 | 列表结果解析 | parameter_extractor | (Item List Output Parser) | 已实现 |
| 解析、评测与护栏 | 内容护栏 | runtime_middleware | (Guardrails) | 已实现 |
| 解析、评测与护栏 | 自修复结果解析 | parameter_extractor | (Auto-fixing Output Parser) | 已实现 |
| 交互、人工与表单 | 表单回复 | — | (n8n Form) | 未实现 |
| 画布、评测与内部元数据 | 画布注释 | annotation | (Sticky Note) | 已实现 |

完整逐条对照见 [n8n-node-capability-matrix.csv](./n8n-node-capability-matrix.csv)。

## 门禁

- `/api/workflow/node-registry` 是新增节点的唯一权威目录；Registry 故障时本地目录全部只读。
- 前端 `WorkflowNodeKind`、后端 `NativeNodeKind`、NodeContract Registry 必须完全一致。
- Palette 必须是 NodeContract 合法子集；每个启用项必须有默认数据和配置入口。
- compatibility 合同不得超过 #213 冻结白名单；新节点必须直接提供完整合同。
- Planner 只接受完整合同、匹配 checksum 且显式启用的节点；R1–R2.3 增量节点均禁止 Planner 自动生成，Planner 可生成类型仍固定为 7 类。
