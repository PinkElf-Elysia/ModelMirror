# Xpert Data X 语义指标闭环

最后更新日期：2026-07-22

## 目标与边界

Data X 把本地文件数据转换为 Agent 可以安全消费的业务指标：

`文件快照 -> DuckDB -> 语义模型 -> 指标草稿 -> 不可变发布版本 -> 受限查询 -> 提案审批`

第一版支持 CSV、XLSX 和 Parquet，不连接外部数据库，不接受任意 SQL，不写回源数据，也不提供通用 Dashboard 脚本执行器。Xpert Data X 文档仅作为领域参考；实现独立完成。

## 存储与导入

- `DataXStore` 原子保存项目、来源、导入任务、语义模型、指标、版本、提案和结果产物元数据。
- 每个项目使用独立 DuckDB 文件；上传文件按 SHA-256 保存为不可变快照。
- 单文件最多 50MB、最多 100 万行。临时表构建成功后来源才进入 `ready`。
- 导入任务具有 lease、尝试次数和重启恢复；API 不返回物理路径。
- 字段 profile 只包含类型、空值、唯一值、数值范围和日期范围等安全统计。

## 语义模型

一个模型包含 1–5 个实体。实体显式引用来源快照；连接只允许 `inner` 或 `left` 等值连接。字段角色为：

- `dimension`：分组与过滤。
- `time`：时间范围。
- `measure`：聚合值。
- `hidden`：不向 Agent 暴露。

预览和查询都从模型编译，模型名称、字段和 join identifier 必须通过白名单校验。

## 指标生命周期

基础指标支持 `sum`、`count`、`count_distinct`、`avg`、`min`、`max`。派生指标只允许引用已发布指标 code，并使用数字、括号和 `+ - * /`。

指标状态为 `draft / published / archived`。每次发布生成不可变递增 `IndicatorVersion`。后续编辑只改变草稿，在线查询继续读取当前已发布快照，直到用户再次显式发布。

已发布指标进入词法和本地确定性向量目录。向量目录不可用时，发布保持成功并返回降级 warning。

## 查询 DSL

`POST /api/datax/query` 接受项目、模型、指标 code、维度、参数化过滤、时间范围、排序、limit 和固定视图。服务端将其编译为参数化 DuckDB SQL。

约束：

- 默认最多 100 行，绝对上限 500 行。
- 只允许模型中可见字段和已发布指标。
- 过滤值始终参数化；字段、排序和聚合只能来自白名单。
- 结果只生成 `kpi / table / line / bar` 四类 `DataXResultArtifact`。
- API、audit 和 checkpoint 不保存上传数据、完整结果、展开 SQL、路径或密钥。

## Agent 与审批

`datax_indicators` 只能绑定 `workflow_agent`，要求 Runtime 工具模式，并显式配置项目/模型 scope。它提供作用域、模型上下文、维度成员、指标列表/检索/读取/查询/展示以及指标提案工具。

模型不能通过工具参数访问 scope 外的项目、模型、指标或结果。提案按来源 run 去重，并记录 Xpert、Goal 和 Handoff 来源。Inbox 可 revision-safe 编辑、批准、拒绝或取消；批准只创建/更新草稿，发布仍由用户显式执行。

## App 策略

公开 Xpert App 默认 `allow_datax_read=false`。显式开启后，部署仍需满足：

1. 中间件项目和模型真实存在且 scope 一致。
2. 工作流存在 `tool_policy`。
3. `allowProposals=false`。

公开 App 不允许提案、草稿变更、指标发布、原始明细导出或任意 SQL。普通 Workflow、私有 Xpert Chat、Goal、Handoff 和 Automation 不受该公开策略影响。

## API

- `/api/datax/projects`：项目与来源。
- `/api/datax/projects/{project_id}/models`：语义模型。
- `/api/datax/projects/{project_id}/indicators`：指标草稿与列表。
- `/api/datax/indicators/{indicator_id}/validate|publish|archive`：生命周期。
- `/api/datax/query`：安全分析。
- `/api/datax/indicators/search`：发布指标检索。
- `/api/datax/indicator-proposals`：提案 Inbox。
- `/api/datax/capabilities`：安全能力摘要。

## 回退与恢复

- 指标错误：保留当前线上版本，修正草稿后重新发布。
- 导入失败：重试原任务；未 ready 的来源不会进入模型查询。
- App 风险：关闭 `allow_datax_read` 或重新部署不含 Data X 中间件的历史 XpertVersion。
- 代码回退：保留 `server/datax/storage` 挂载；旧版本不会读取该目录，也不会影响 Workflow、RAG 或 Xpert Store。
