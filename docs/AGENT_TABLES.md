# Native Agent Table

最后更新日期：2026-08-11  
状态：当前（资源、管理与私有工作流 CRUD 闭环）

## 当前结论

Native Agent Table 是可信本地工作空间中的轻量业务记录资源。私有 Workflow、
Xpert、Goal 与 Handoff 可以通过类型化节点查询和修改记录；它不是 Agent 可任意
调用的数据库工具，也不接受 SQL。

它与现有两条数据路径职责不同：

| 路径 | 当前职责 | 不承担 |
| --- | --- | --- |
| Agent Table | 本地类型化业务记录、Schema 版本、人工 CRUD | OLAP 指标、任意 SQL、外部数据库连接 |
| Data X | CSV/XLSX/Parquet 快照、DuckDB 语义模型和指标分析 | 可变业务记录和工作流事务写入 |
| Database MCP | 外部数据库的受控连接与工具调用 | ModelMirror 托管表和本地 Schema 生命周期 |

## 存储与版本

`AgentTableStore` 是 Backend-neutral 门面，首个实现为
`SQLiteAgentTableBackend`。默认存储目录按以下顺序解析：

1. 构造参数。
2. `AGENT_TABLE_STORAGE_DIR`。
3. `AGENT_TASK_STORAGE_DIR`。
4. `server/data_tables/storage`。

SQLite 开启 WAL、外键、busy timeout、进程内锁和显式写事务。数据库保存表定义、
不可变 SchemaVersion、唯一一份记录、幂等操作账本和安全审计摘要。

表状态为 `draft / published / archived`。发布生成递增且不可变的
`AgentTableSchemaVersion`；记录始终保存在同一逻辑表中，不复制“测试数据”和
“线上数据”。归档表可读但不可继续编辑 Schema 或写入记录。

## 字段契约

业务字段最多 50 个，字段名必须以 ASCII 字母开头，并仅包含字母、数字和下划线。
支持：

- `string`
- `integer`
- `number`
- `boolean`
- `datetime`（ISO 8601）
- `json`（JSON-safe typed value）

系统字段固定为 `record_id / created_at / updated_at / revision`，不会混入业务
`data`。单条记录的规范化 JSON 正文上限为 256 KiB。

Schema 发布后，只允许新增可选字段或带默认值的必填字段，以及修改字段标签和说明。
已发布字段不可删除、改名、改类型或改变约束/默认值。破坏性迁移和数据转换不在首版
范围内。

## 管理接口

```text
GET/POST /api/data-tables
GET/PATCH /api/data-tables/{table_id}
POST      /api/data-tables/{table_id}/validate
POST      /api/data-tables/{table_id}/publish
POST      /api/data-tables/{table_id}/archive
GET       /api/data-tables/{table_id}/schema-versions
GET/POST  /api/data-tables/{table_id}/records
PATCH     /api/data-tables/{table_id}/records/{record_id}
DELETE    /api/data-tables/{table_id}/records/{record_id}
```

Schema 草稿和记录修改都使用 revision 防止覆盖。记录写入可携带稳定
`operation_id`；同一 ID 与相同请求重放时返回原结果，与不同请求复用时返回冲突。
管理 API 不返回 SQLite 物理路径。

前端入口为 `/data-tables` 与 `/data-tables/:tableId`，支持建表、字段编辑、Schema
校验/发布、版本查看、记录浏览及人工增删改。`/studio` 的数据库主入口指向 Agent
Table，同时保留 Data X 分析和外部 Database MCP 入口。

经典工作流顶栏也提供“数据表”入口。四类 Agent Table 节点的配置侧栏直接加载已发布
数据表和不可变 SchemaVersion，可选择字段、递归 `and/or` 条件、排序、返回模式，或
把写入字段绑定到类型化固定值和工作流变量。节点侧栏提供当前表详情和新建/管理数据表
的跳转，设计者无需手写 Workflow JSON 才能形成可执行配置。

## 安全边界

- 首版不接受自定义 SQL，也不向 Agent 暴露自主数据库工具。
- 当前可信边界是单机、单工作空间；不宣称用户、组织、RLS 或多租户隔离。
- 审计只保存资源 ID、操作、Schema 版本、记录 ID、影响数量和时间，不保存完整记录。
- SQLite 文件、WAL、Runtime Store 和上传数据必须保持在 Git 之外。
- 公共 App 和只读 Evaluator 当前不能消费 Agent Table 节点。

## 工作流节点

四类经典工作流节点已经启用：

- `data_table_query`：字段白名单、递归 `and/or` 条件、排序、`list/first` 返回模式，
  默认最多 20 条、上限 200 条。
- `data_table_insert`：按字段绑定 literal 或 typed workflow variable，返回完整新记录。
- `data_table_update`：必须配置条件，单次最多影响 100 条，返回 matched/affected。
- `data_table_delete`：必须配置条件，单次最多影响 100 条，返回 matched/affected。

条件操作符固定为 `eq/ne/gt/gte/lt/lte/in/contains/is_null`。所有值都经过固定
SchemaVersion 的字段和类型校验；Schema、字段或类型漂移时 fail-closed。Classic
Workflow 可在运行时解析 `latest`，Xpert 发布则将其固定为具体 SchemaVersion。
写节点使用由 `task_id + node_id` 派生的稳定 operation ID，恢复或重放不会重复写入。

这些节点在 Registry 中保持 `planner_enabled=false`。原计划中的
`EVOAGENTX-META-PLANNER-DATA-04` 已从本闭环取消，后续需要以独立、可控的 Planner
数据编排闭环重新立项。

## 验证

```bash
python -m pytest server/tests/test_agent_tables.py -q
python -m pytest server/tests/test_workflow_data_table_nodes.py -q
python -m py_compile server/main.py server/data_tables/*.py
cd client && npm.cmd run build
```

容器验收还应覆盖 Schema 发布、记录 CRUD、条件查询、写入幂等、Xpert 固定版本、
归档只读和重启后 SQLite 数据恢复。
