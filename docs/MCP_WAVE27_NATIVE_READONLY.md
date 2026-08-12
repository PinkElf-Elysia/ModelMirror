# MCP Wave 27：原生只读数据服务

## 当前结论

Wave 27 首个验收单元仅推进 `greptimeteam-greptimedb-mcp-server`。它当前仍为
`planned`，私有 sidecar 契约已编译但默认关闭，未加入生产
`MCP_DATABASE_ALLOWED_ADAPTERS`。只有真实 GreptimeDB 原生只读账号、代表调用、
限流/超时、断开、重启和清理全部通过，并经人工验收后，才能晋级 `ready`。

VictoriaMetrics 与 Chroma 本轮不用于补足数量。VictoriaMetrics 的 bearer token
出口与动态目标边界尚未形成可证明的最小契约；Chroma 尚未证明原生服务端只读主体。
Snowflake、Confluent、dbt、Superset、OpenTelemetry 和 PlanetScale 等候选继续保持
`planned`，等待真实只读账号、固定资源范围或更窄的产品身份。

## 冻结的上游身份

- 项目：`GreptimeTeam/greptimedb-mcp-server`
- 版本：`v0.5.1`
- 提交：`ba3b732fe2113378f41c391da880b9ab75f2d862`
- 许可证：MIT
- 工具 Schema SHA-256：
  `86c8dbbfda387925e345fde14bdfdb3681c2b02e5072e5b84bfb7000e1aef65c`

ModelMirror 不安装或运行上游 MCP 包，只实现经审阅的同产品只读子集。上游身份用于
锁定产品语义、版本、许可和副作用边界；运行时通过 GreptimeDB 的固定 HTTP SQL
接口访问项目绑定资源。

## 暴露的工具

- `describe_table()`：仅描述配置中绑定的一张表；
- `query_range(start, end, limit=200)`：仅读取绑定的时间列和值列，时间范围最多
  24 小时，返回最多 200 行；
- `health_check()`：执行固定的 `SELECT 1` 只读预检。

客户端不能提交 SQL、TQL、查询表达式、数据库名、表名、列名、Host、URL、DSN、
Header、环境变量或命令。所有 SQL 均由 sidecar 从已验证配置生成；标识符只允许
受限字符集，时间会规范化为 UTC RFC3339。

## 配置与凭据边界

配置字段固定为 `host`、`port`、`database`、`table`、`time_column`、
`value_column`、`tls_mode` 和 `username`；凭据槽固定为 `password`。生产只允许
`verify-full`，明文模式仅在隔离测试同时设置专用测试开关时有效。Host 必须通过
数据库 sidecar 的 DNS 全答案与管理员私网 allowlist 检查。

默认 allowlist 排除该 ID，因此目录虽能展示 `planned` 条目，但不能准备、连接或
执行。测试显式 allowlist 只作用于随机命名的隔离容器，不改变共享栈。

## 验收门槛与回退

隔离验收已使用官方 `greptime/greptimedb:v1.1.4`（manifest
`sha256:9726587eac95d0360755254cd59a528dbf48abfdf268478aea6a644f62afe44c`）完成：
原生 `ro` 账号读取返回 `HTTP 200 / code 0`，建表写入由服务端拒绝为
`HTTP 403 / code 7006`；两次独立 UDS 会话均通过 initialize、tools/list、Schema、
三项代表调用和负向工具门禁；确定性延迟 fixture 证明 provider timeout 在
`10.5–16.0s` 观测窗口内先于 gateway 硬期限返回。会话后 sidecar 仅剩 PID1，随机
命名容器、卷和网络清理为零残留。

429、503 与 403 的固定错误分类由定向自动化测试覆盖；真实 GreptimeDB 没有被人为
配置为产生 429。日志和公开 smoke 结果不含密码、Authorization、SQL 或行值。目录
计数仍保持 `76 ready / 68 planned / 156 blocked`，等待用户验收后再决定晋级。

回退无需数据迁移：移除私有契约与测试，或在晋级后从精确 allowlist 删除该 ID，
恢复 `planned` 清单并断开相关目录会话即可。
