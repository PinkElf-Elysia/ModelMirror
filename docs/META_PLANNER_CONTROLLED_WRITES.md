# Meta Planner 受控 Agent Table 写入

状态：V3 第 7/10 轮实施契约；自动化与人工验收状态以任务记录为准。

## 权威边界

Capability V10 仅增加 `data_table_insert/update/delete`，总数 22；Graph IR 保持 V3。
三个 Adapter 使用 V2、`task_binding=forbidden`，不能覆盖计划任务或作为最终交付来源。
查询权限和写权限分别授权，缺省都不扩大。写授权按表固定：

```json
{"table_id":"table-demo","operations":["update"],"writable_fields":["status"],"max_affected_rows":1}
```

每张表最多授权 50 个业务字段，更新/删除默认最多 1 行，可显式提高到 100 行；Insert
始终只写一条。模型不能提交授权、原生变量、SchemaVersion、Handle、执行环境或 operation ID。
服务端固定 SchemaVersion/checksum。首次写入时该 Schema 必须仍为活动版本，归档、缺失
或校验和漂移均拒绝。业务字段在写入事务内再次进行类型、必填与默认值校验。

本轮沿用可信本地管理端边界：设计者可配置私有 Native Workflow，也已有直接 CRUD 权限。
`writeGrant` 约束 Planner、模型和候选编辑，不能被描述为用户、组织或 RLS 权限系统。
模型和 Graph Patch 不能自行签发或扩大授权；公共 App 仍禁止全部 Agent Table 节点。

## 数据来源

- Update/Delete 的记录输入只能直接来自本次工作流实际执行的同表 Query 或 V2 Insert。
  运行收据绑定输出内容、表、Schema 和 revision。Agent 文本、JSON 伪造 ID、跨表或被替换
  的输出不能充当身份。
- 业务值是一份固定对象或完整类型化对象，不增加属性路径、映射语言或系统字段写入。
  当前输入校验证明由直接相连的 JSON Deserialize V2 提供，必须显式列出可写业务字段；
  Agent 生成的数据也必须经过该节点及事务内字段校验。
- 条件树最多 3 层、20 个谓词，只能缩小可信记录集合。空条件不等于全表授权。
- 同一可达路径上的同表冲突读写必须显式排序。更新后再次修改同一记录，须重新 Query
  取得新 revision；旧 Query/Insert 收据不会被自动刷新。

### 资源连线类型

首次生成与 Patch 共用 Graph IR 的资源类型解析。确认授权、固定 Schema、端口、变量身份及
控制可达性后，直接资源连线使用 Adapter 的真实输出类型；模型声明只能约束它，不能改变它。
宽泛声明可由已验证来源细化，但显式错误形状、字段类型或缺失 nullable 必须拒绝。真实
对象不能因此直连只接受字符串的 Agent，也不能绕过同表可信记录来源要求。

生成用 Schema 继续引导具体声明，不把裸 `any` 当作合法记录形状；公开 IR 中较宽的描述
不构成执行类型权威。Patch 不再静默覆盖资源输入的显式冲突，须显式修改来源/配置或重连。
编译候选的 V3 与兼容端口元数据均记录已验证类型，避免同一资源连线产生不同 checksum。

Query 的 `first/list` 和 Insert 的基本形状在配置确定后即可推导，完整字段仍须授权资源
快照。模型短提示由同一 Adapter 生成，端口 Schema 只省略可还原默认值，并明确通用
union 不等于裸 any；模式相关形状紧邻显示，不删除原有端口契约。
任务计划的 `input_contract` 是字符串数组，`output_contract` 是字符串；非法输入不自动转换。

## 事务与恢复

每个写节点独立事务，所有记录前置条件、影响上限、实际修改、幂等账本原子提交。
任一 revision 冲突整节点回滚；后续节点失败不会撤销先前成功写入。
`retryMode=none` 只表示关闭重试，不赋予等待或重发权限。

Runtime 在首次派发前将固定请求保存到私有执行日志。operation ID 由稳定 task/node
身份生成，恢复先核对日志与账本 checksum：已提交返回原收据，确认未提交才执行原请求；
缺失、损坏或无法证明结果时失败停止。不同请求不能复用同一 operation ID。
已提交操作的幂等回放不因为后来 Schema 发布而变成第二次写入。

## Headless 与审批

Preview 只解析元数据、Schema、授权、来源和控制关系，不查询待修改记录，也不写表。
画布通过受限 `plannerWriteIntentV2` 传递语义配置与命名端口，服务端经 Adapter 重新编译，
该编辑请求不进入持久化 Workflow。原生授权、变量、版本与 Handle 仍不可编辑。
无效 JSON 不会静默使用上一次合法对象。Apply 重算预览并绑定 revision/checksum，只修改
pending Proposal 一次；批准仍只创建或更新 Xpert 草稿，不执行业务写入。

## 隔离评测

Dataset Case 的 `table_initializations` 只接受 `manual/synthetic`、固定 SchemaVersion
和局部记录 ref。每表最多 200 条，发布版本时规范化、固定 Schema 和 checksum；冻结夹具
总量不超过 16 MiB。没有业务记录复制、整表导入或自动生成初始化夹具入口。

每个目标、用例、重复序号使用单独私有 Backend。所有 Query 与写节点共享本项隔离状态，
写后 Query 读取修改后的记录，不使用旧只读夹具替代，也绝不回退活表。基线与候选从相同
初始化内容开始；随机记录 ID、系统时间不参与业务值相等判断。隔离记录与账本逻辑总量
不超过 16 MiB，事务提交前检查。

已完成项不复跑；未完成项恢复已有实例及账本。取消停止后续派发，不删除已完成效果证据。
初始化文件或实例损坏时拒绝重新从业务表取得数据。客户端不能传入隔离 Backend 或测试模式。
旧只读评测保留原路径；依赖 Agent 输出的 Query、嵌套写入及 Optimizer 写入入口不开放。

## 效果证据

写入评测必须为写节点配置 `effects`，由 `workflow_effect_match` 核对真实 Backend 收据、
逐节点前后状态和未选中记录 checksum。断言包含表、操作、可选固定 Schema/契约、状态、
影响行数及业务字段期望。支持 `applied/noop/conflict/not_executed`；预期冲突须声明安全
错误码，不能同时用文本答案掩盖执行错误。

跳过写入却回答成功、伪造影响行数、额外修改记录、重复回放计分均不构成有效证据。
普通报告只保存字段名、计数、状态、checksum 和部分完成摘要；私有记录、初始化正文与
逐节点前后值不进入 API 报告、checkpoint 或审计。含隔离写入的最终模型文本也不进入
普通报告，以免泄漏初始化业务值；结构校验失败仅返回安全错误类型，不回显未知 JSON 键名。

## 不开放与回退

公共 App、Structure Evolution、SQL、外部工具写入、循环、等待、HITL、通用 Goal/Handoff
授权、跨节点事务及自动补偿不在本轮范围。不修改 RAG、Data X 或其数据。
回退先关闭三个 Planner Adapter 和隔离写入评测入口，保留 V2 读取执行兼容；不自动撤销
已完成业务写入，不进入第 8 轮。

测试与交付记录：[任务卡](./tasks/META_PLANNER_CONTROLLED_WRITES_10.md)。
