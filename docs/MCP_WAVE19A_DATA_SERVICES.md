# MCP 第 19A 批：只读数据服务

## 当前结论

本批已在现有 `mcp-database` sidecar 中实现 Prometheus、Qdrant 与 Elasticsearch
三个原生 REST 兼容层，并完成独立镜像、真实服务、原生只读凭据、429、超时、拒写、断开
和清理验收。用户验收后，三项已晋级 `ready` 并加入生产 Compose 的精确数据库 allowlist。

| 目录 ID | 审阅上游 | 许可证 | 固定兼容身份 |
|---|---|---|---|
| `pab1it0-prometheus-mcp-server` | v1.6.2 / `c41d06924ce436c2b63edd20671f77f5b7564bb0` | MIT | `1.6.2-compatible-native-read-only-v1` |
| `qdrant-mcp-server-qdrant` | v0.8.1 / `860ab93a96ca9f5e6cf6fe47e2f5b75d36eaac69` | Apache-2.0 | `0.8.1-compatible-native-read-only-v1` |
| `cr7258-elasticsearch-mcp-server` | v2.1.2 / `56ab520f863cd36fcd00e984ebc384f6cdf8279c` | Apache-2.0 | `2.1.2-compatible-native-read-only-v1` |

本批不运行三个上游 MCP 进程，也不把其包或源码复制进镜像。上游项目用于确认产品身份、
版本、许可证和工具意图；运行时由 ModelMirror 固定 facade 调用供应商原生 API。

## 固定工具与 Schema

- Prometheus：`execute_query`、`execute_range_query`、`list_metrics`、
  `get_metric_metadata`、`get_targets`；Schema SHA-256
  `44265e2144474e895d58010f2a80cb61efb381112978db81b180f2a960e46ff4`。
- Qdrant：`get_collection_info`、`scroll_points`、`query_points`；Schema SHA-256
  `45b1380288c0f842e4a1487b1470f3231cbdb7158bae5829d8b3aadacdf53e44`。
  上游 `qdrant-store` 不可发现、不可调用。
- Elasticsearch：`get_cluster_health`、`get_index`、`search_documents`、
  `get_document`；Schema SHA-256
  `d1ed0ec28c75c7faeb16ba461c07a508b5a4f7ecf0711b8f83ac2a4c29d09064`。
  写入、删除、管理和任意 API 工具均不存在。

所有工具均标记为只读、幂等、非破坏且非开放世界。客户端不能提交 URL、DSN、Header、
环境变量、命令、工作目录、任意 API path 或查询 DSL。

## 配置与权限边界

- 三项只接受结构化 `host`、`port` 和 `tls_mode=verify-full`；IP 字面量、私网/回环/
  metadata 地址、重定向和 DNS 漂移均拒绝。隔离验收专用的明文模式只有 sidecar 管理员设置
  `MCP_DATABASE_TEST_ALLOW_PLAINTEXT=true` 时才存在，生产 Compose 未设置。
- Prometheus 允许一个可选服务端加密 `bearer_token`；PromQL 最长 4096 字节、语法复杂度
  受限，范围查询最多 24 小时、每序列最多 1000 点，目标只返回 active 状态。
- Qdrant 强制一个 `collection` 和一个服务端加密 `api_key`；验收使用 Qdrant 原生
  read-only API Key。分页/查询最多 100 点，向量最多 4096 维且永不返回向量正文。
- Elasticsearch 强制一个 `index`、一个 `search_field`、一个 `username` 和服务端加密
  `password`；验收角色只有 cluster `monitor` 与索引 `read`、`view_index_metadata`。
  facade 只构造固定 `match` 查询，最多返回 100 条。
- 原始响应上限 256 KiB，供应商请求 12 秒超时，sidecar 15 秒硬截止；安全 read 才可由
  上层有界重试。子进程使用 Landlock、只读根、无 capability 和独立容器 PID cgroup；
  `RLIMIT_NPROC` 不用于共享 UID，因为该内核计数会跨容器耦合，生产 `pids_limit` 仍为 128。

## 隔离验收证据

- 最终 staged sidecar 镜像：`modelmirror-mcp-database:wave19a-staged`，manifest list
  `sha256:cfbbda661236320d1a28401c2ae270d04da717b093df457437b0b27e20c58691`，
  config `sha256:7a9a325f5847ccf55ab0a2c2cad520d74c05a87e48461d6dbe51990bab9de316`。
- 真实服务镜像：Prometheus `v3.12.0`（`sha256:69f52414…a8ac`）、Qdrant
  `v1.18.2`（`sha256:75eab8c4…e50c`）、Elasticsearch `8.17.2`
  （`sha256:9fb5d27b…7f7e54`）。服务未发布宿主端口。
- 每项均通过私有 UDS 完成 `initialize`、`tools/list`、冻结 Schema 和全部代表调用；
  任意 URL/Header 参数在网关层以 `-32602` 拒绝，写工具以 `-32601` 拒绝。
- Qdrant read-only Key 的删除尝试被供应商拒绝；Elasticsearch 原生只读用户的索引写入
  被 403 拒绝。
- 固定 fixture 真实返回 429 并延迟 20 秒；sidecar 分别验证错误脱敏、12 秒超时和同一会话
  后续恢复。fixture、故障 sidecar 和 socket 卷均已精确清理。
- 所有会话结束后 sidecar 仅剩 PID1，无 MCP 子进程；未启动或重建共享栈。

## 晋级、回退与剩余门槛

批次 19A 晋级完成时目录总数为 `67 ready / 34 planned / 99 blocked`。本次晋级仅包含：

1. 将三个精确 ID 从 staged 决策改为 ready；
2. 将其加入远程数据库 sidecar 的精确生产 allowlist；
3. 保留所有固定 Host、资源、原生只读凭据和查询上限。

回退不需要数据迁移：从 allowlist 移除精确 ID、恢复为 `planned` 并断开对应目录会话即可；
供应商数据、账号、索引和 collection 不由 ModelMirror 创建或删除。
