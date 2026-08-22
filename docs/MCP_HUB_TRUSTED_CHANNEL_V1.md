# MCP Hub Trusted Channel V1

Trusted Channel 把已复核执行契约变成面向普通用户的“可信可用”入口。它不把
Registry 收录、一次预检或远程 annotations 当成信任；Runtime 只接受仓库契约，
以及由当前本地签名密钥验证通过的本机不可变契约。

本轮仍只支持匿名、固定公网 HTTPS、Streamable HTTP、tools-only 服务。Token、
OAuth、自定义 URL/Header、stdio/package、动态 endpoint 和非工具能力不在边界内。

## 开关与依赖

```dotenv
MCP_HUB_ENABLED=false
MCP_HUB_REMOTE_ENABLED=false
MCP_HUB_REVIEW_FACTORY_ENABLED=false
MCP_HUB_LOCAL_CONTRACT_PUBLISH_ENABLED=false
MCP_HUB_TRUSTED_CHANNEL_ENABLED=false
MCP_HUB_AUTO_REVIEW_ENABLED=false
MCP_HUB_CONTRACT_SIGNING_KEY=
```

- 六项开关默认关闭；Trusted Channel 不隐式开启 Hub、远程桥或 Review Factory。
- `MCP_HUB_TRUSTED_CHANNEL_ENABLED` 开启可信列表、实时复核门禁和本地产品证据。
- `MCP_HUB_AUTO_REVIEW_ENABLED` 还要求 Review Factory 已开启。它只执行安全阶段，
  最多选择 20 项、并发 2 项，并停在代表调用审批之前。
- 自动复核绝不批准代表调用、发布契约、激活候选或执行 Runtime 工具。
- 本机契约仍要求外部签名密钥；密钥缺失或不匹配时 fail closed。仓库契约不受
  本机签名密钥影响。

## 可信状态

| 状态 | 含义 | 用户动作 |
|---|---|---|
| `ready` | 契约和当前 Registry 身份一致，24 小时内完成真实隔离检查 | 可连接 |
| `stale` | 尚未检查、检查已过期，或本机尚无 Registry 快照 | 连接时先实时复核 |
| `degraded` | 远程超时、限流或暂时不可达 | 稍后复检 |
| `environment_blocked` | 本机 DNS 或隔离出口拒绝 | 修正环境后复检；不等同远程不安全 |
| `drifted` | Registry 来源、远程身份或工具 Schema 改变 | 重新进入 Review Factory |
| `revoked` | 本地运维者撤销契约 | 不可连接 |
| `collision` | 相同身份出现不同契约指纹 | 全部关闭，人工处理冲突 |

只有 `ready` 且健康检查未超过 24 小时的契约可以进入 Runtime。状态漂移或撤销会
立即断开匹配候选；健康检查失败不会使用旧结果继续放行。手动复检最短间隔为
10 分钟，连接操作仍会执行自己的实时复核。并发连接按契约串行化，同一
tenant/owner/Registry 身份只保留一个候选。

## API 边界

```text
GET  /api/mcp/hub/trusted/status
GET  /api/mcp/hub/trusted/servers
GET  /api/mcp/hub/trusted/servers/{contract_id}
POST /api/mcp/hub/trusted/servers/{contract_id}/revalidate
POST /api/mcp/hub/trusted/servers/{contract_id}/activate
GET  /api/mcp/hub/trusted/metrics?window=7d|30d|90d
```

两个写接口只接受当前 `expected_contract_fingerprint`。客户端不能提交 URL、远程
ID、Header、环境变量、工具参数或连接配置。激活会用服务器端契约身份创建一次性
隔离检查会话，校验完整工具集合和 Schema 后关闭 capability，再复用现有 Hub
候选与逐次审批 Runtime。

## 自动复核与恢复

Registry 成功同步后触发一次维护；后台每 15 分钟检查到期契约。无有效 Registry
快照时不访问网络，也不会把仓库契约误报为漂移。自动候选按现有确定性 Registry
排序选择，每次最多 20 项；同一 owner 仍只允许一个活动复核批次。

自动批次沿用 Review Factory 的安全恢复规则。Registry/静态/initialize/tools-list
阶段可恢复；代表调用一旦写入 `started` 后中断即 `unknown_outcome`，不会重发。
结构性拒绝在当前 source digest/SOP 下长期抑制；临时失败按 24 小时、72 小时、
7 天退避。source digest 或 SOP 版本变化会重新进入复核资格。

## 本地证据与隐私

`hub.sqlite3` 只新增健康状态、自动复核调度和产品事件表。产品事件是固定枚举，
最多保留每 owner 50,000 条和 90 天，只记录 contract/candidate 标识、工具名摘要、
固定结果码和时间。不记录参数、结果正文、URL、Header、Secret、账号或物理路径。

`metrics` 仅返回 7/30/90 天聚合计数，用于查看可信频道加载、复核、连接、逐次审批、
调用结果、漂移和撤销漏斗。它是本地运维证据，不是跨用户分析或计费账本。

## 验收与回退

自动验收至少覆盖：契约 HMAC/碰撞/撤销、Registry 和 Schema 漂移、无快照降级、
环境阻断分类、复检限流、并发连接、Owner 隔离、自动批次停止点、Runtime 审批重放、
`unknown_outcome`、会话清理、UI 错误恢复及 Secret 不落库。

真实验收必须使用隔离 Docker 资源完成 Registry 同步、至少 5 个真实 preflight，
并以 Qt 和至少 3 个不同发布者/至少 2 类用途的新契约证明连接、逐次审批调用、撤销
和立即断开。无法通过真实门禁的项目不得用 fixture 替代或宣称可信可用。

回退时关闭 `MCP_HUB_TRUSTED_CHANNEL_ENABLED` 和 `MCP_HUB_AUTO_REVIEW_ENABLED`，
断开 Hub 会话即可。新增 SQLite 表和聚合证据可保留，不修改或迁移既有 Registry
快照、候选、复核证据、执行 ledger 和仓库契约。
