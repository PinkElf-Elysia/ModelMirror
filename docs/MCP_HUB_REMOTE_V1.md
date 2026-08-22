# MCP Hub 第一轮受控接入

## 交付范围

第一轮只提供官方 MCP Registry 发现、本地候选管理，以及匿名、固定公网
HTTPS Streamable HTTP 端点的临时试连。Registry 收录是发现元数据，不是
ModelMirror 的安全认证或 `ready` 结论。

客户端只能提交 `server_name`、固定 `version` 和服务端快照产生的
`remote_id`。客户端不能提交 URL、命令、参数、Header、环境变量、凭据、
DSN、宿主路径或动态 MCP endpoint。stdio/package、旧 SSE、OAuth、Token、
Header/变量模板、私网和非工具能力只展示状态，不能试连。

候选状态为 `draft → verified → active`，异常或终止状态为
`drifted / tainted / blocked / disconnected`。Registry 版本或远程地址变化
不会原地替换已冻结候选；旧候选进入漂移状态，新记录需重新创建、预检和激活。
`eligible` 只表示端点可进入隔离预检，不表示工具只读或可执行。候选只有在
服务名、版本、精确 URL、完整 Schema digest 和逐工具 Schema digest 全部命中
ModelMirror 内置复核契约后才能激活；第一轮当前仅放行已完成真实验收的 Qt
Documentation 0.2.0。其他 Registry 条目可发现、可预检，但不会进入 Runtime。

## 功能开关

```dotenv
MCP_HUB_ENABLED=false
MCP_HUB_REMOTE_ENABLED=false
MCP_LEGACY_UNRESTRICTED_CONNECT_ENABLED=false
```

三个开关默认均为 `false`。只开启发现不会开启远程连接；远程试连必须同时
开启前两个开关。旧版接受任意命令的 `/api/mcp/connect` 与安装入口默认
返回 404，仅既有定向测试可显式开启兼容开关。

官方 Registry Host 编译固定为 `registry.modelcontextprotocol.io:443`。在
Docker Desktop 把公网 DNS 映射为 `198.18.0.0/15` 时，同步客户端只把该
网段视为 Docker 传输兼容地址，并继续使用固定 Host、TLS SNI 与系统证书
校验；这个兼容只适用于官方 Registry，同步得到的用户候选出口仍拒绝所有
合成 DNS。

同步单页限制 2 MiB，最多 500 页、50,000 条和 128 MiB 归一化快照；任一
总量门禁触发时保留上次有效快照，不发布部分结果。

## API

| 方法 | 路径 | 边界 |
| --- | --- | --- |
| GET | `/api/mcp/hub/status` | 返回固定 Registry 来源、快照和开关状态 |
| POST | `/api/mcp/hub/sync` | 显式启动官方 Registry 同步 |
| GET | `/api/mcp/hub/sync/{sync_id}` | 查询同步状态，不返回原始响应正文 |
| GET | `/api/mcp/hub/servers` | 搜索、分类、准入状态和游标分页 |
| GET | `/api/mcp/hub/servers/{name}/versions/{version}` | 查看固定版本与服务端归一化元数据 |
| POST | `/api/mcp/hub/candidates` | 只接受三个 Registry 标识字段 |
| GET | `/api/mcp/hub/candidates[/{candidate_id}]` | 当前 owner 的候选 |
| POST | `/api/mcp/hub/candidates/{candidate_id}/preflight` | initialize、能力和 tools/list 预检 |
| POST | `/api/mcp/hub/candidates/{candidate_id}/activate` | 必须回传当前 Schema digest |
| DELETE | `/api/mcp/hub/candidates/{candidate_id}/session` | 断开临时会话 |
| DELETE | `/api/mcp/hub/candidates/{candidate_id}` | 断开并删除本地候选 |

Hub 不提供通用工具调用 API。工具只以
长度不超过 64 的 `hub__<candidate-hash>__<tool-slug>_<tool-hash>` 进入 AI Runtime，固定标记为敏感、
非只读、不可并发、不可用于 Public App、不可重试，并要求 HITL 对每次调用审批。
远程描述不进入模型系统提示；审批显示精确 Origin、候选/工具 Schema digest
和不丢字段、不截断结构的完整脱敏参数。

## 隔离和出口

- `mcp-hub-remote` 使用 `network_mode:none`，只运行 MCP Python SDK 和固定
  UDS 网关，不安装 Registry package、不启动子命令。
- `mcp-hub-egress` 是唯一联网容器。后端以 root peer credential 创建
  256-bit 临时 capability；remote 进程只能凭 capability 建立 443 隧道。
- 授权时验证全部 A/AAAA 答案为公网并冻结集合；每次连接重新解析，集合
  变化即撤销 capability。连接只使用冻结 IP，TLS ClientHello 必须携带精确
  SNI，ECH 被拒绝。
- SDK 客户端禁用代理环境变量和重定向。initialize 只接受静态 tools 能力；
  prompts、resources、logging、completions、动态工具列表等能力均拒绝。
- 每次批准调用都在候选锁内重新 `tools/list` 并核对完整 Schema；之后才把
  execution ledger 写为 `started`。工具调用从不自动重试。
- 激活也在同一候选锁内重新读取 Registry 快照并核对版本状态、source digest
  和精确远程端点，避免预检后被下架或替换仍可激活。
- 调用发出后的超时或断链统一记为 `unknown_outcome`，候选被污染、临时
  会话和出口 capability 被销毁；旧审批再次提交只返回原未知结果，不会重发。
- 后端重启时，任何遗留 `started` ledger 会原子转为 `unknown`，对应候选转为
  `tainted`；不会把进程崩溃窗口误当成可安全重试。

默认上限：每个本地 owner 最多 2 个会话、每候选 1 个并发调用、50 个工具、
单 Schema 32 KiB、Schema 总量 256 KiB、参数 32 KiB、结果 256 KiB、工具
调用 20 秒、会话 50 次调用、空闲 5 分钟、最长 15 分钟。出口另限制并发
隧道、累计字节、隧道空闲和绝对时限。

## 存储、回退与已知边界

后续的批量复核、证据冻结、本机签名契约、仓库契约导出与撤销流程见
[`MCP_HUB_REVIEW_FACTORY_V1.md`](MCP_HUB_REVIEW_FACTORY_V1.md)。Review Factory
默认关闭，不改变本页第一轮连接边界。

已复核契约的普通用户入口、实时健康状态和自动复核停止点见
[`MCP_HUB_TRUSTED_CHANNEL_V1.md`](MCP_HUB_TRUSTED_CHANNEL_V1.md)。

Registry 快照、候选和 execution ledger 存在现有 MCP catalog storage 下的
`hub.sqlite3`，以部署时固定的本地 tenant/owner 隔离。当前仍不是可切换的
多租户主体模型，因此第一轮不开放共享 SaaS 部署或 OAuth/Token 服务。

回退时关闭两个 Hub 开关并断开 Hub 会话即可；Registry 缓存可保留，不包含
凭据。若真实官方 Registry 快照没有满足全部门禁的匿名远程条目，远程开关
保持关闭，不把目录记录制造成可执行或 `ready`。

Hub 镜像使用独立 `requirements.hub.in`，并由固定 pip-tools 生成包含全部
传递依赖 SHA256 的 `requirements.hub.lock`；构建必须以 `--require-hashes`
安装，不再复用其他 sidecar 的未哈希 requirements。

## 隔离验收证据

- 官方 Registry 真实同步得到 23,925 条 latest-only 记录；固定匿名远程条目
  `io.qt.qt-docs-mcp/qt-documentation@0.2.0` 通过 initialize、tools/list、
  Schema 冻结和批准后的 `qt_documentation_search` 代表调用。
- 固定 tools-only TLS fixture 的 `slow_read` 实际保持 25 秒；生产远程桥在
  20.191 秒返回 `unknown_outcome`，候选转为 `tainted`，相同审批重放被拒绝。
- 超时 fixture、出口、离线 remote 和 helper 全部位于随机命名的隔离资源；
  验收结束后容器、卷和网络的精确残留计数均为 0。

这些证据证明第一轮匿名只读远程桥接与 fail-closed 语义；Registry 收录本身
仍不构成安全认证，也不放宽 OAuth、Token、stdio/package 或非工具能力。
