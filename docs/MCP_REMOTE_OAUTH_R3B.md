# MCP Remote OAuth R3B

R3B 在 R3A 已发布 V3 契约上开放本地单主体 Runtime，不引入多租户
RBAC、自动刷新或写工具。当前主体仍固定为 `local/local`。

## 默认关闭的开关

```dotenv
MCP_REMOTE_OAUTH_RUNTIME_ENABLED=false
MCP_REMOTE_OAUTH_REMOTE_REVOCATION_ENABLED=false
```

Runtime 还要求 R3A 的 OAuth、Token storage、Review Factory、外部凭据主密钥和
本地契约签名全部就绪。不得将主密钥、Token 或 client secret 写入仓库、
Compose 或浏览器存储。

## 执行边界

- 只有 `HubReviewedContractV3`、当前 resource-bound Token、精确 Scope、Schema 和
  Registry 来源全部匹配时才可预检、激活和暴露工具。
- 每次工具调用仍标记为 sensitive，必须逐次审批，不自动重试。
- Token 距到期不足 60 秒时返回 `mcp_remote_oauth_refresh_required`，只能由运维者
  显式刷新。
- Token revision、Scope、重授权、撤销或 Schema 变化会立即使旧 Hub 会话
  失效。
- 401 不自动刷新；403 仅返回经边界检查的 Scope challenge，不自动
  扩权、重新授权或重试原操作。

## RFC 7009 撤销

删除 Token 会先断开当前候选会话，并在 SQLite 事务中封锁当前 revision；
无论远程结果如何，本地加密凭据都会撤销。远程开关开启且冻结 metadata
包含 HTTPS revocation endpoint 时：

1. 优先撤销 refresh token，否则撤销 access token。
2. 请求仅包含 token、`token_type_hint` 和 public `client_id`。
3. 派发后绝不自动重试。HTTP 200 记为完成；超时或断链记为
   `mcp_remote_oauth_revocation_unknown_outcome`。
4. 无论远程结果如何，本地凭据都保持已撤销。

## 回退

将两个 R3B 开关设为 `false` 并重启隔离环境，即可停止 OAuth Runtime 和新的
远程撤销请求。已撤销的远程 Token 不可恢复；V1/V2 契约、匿名 Hub、静态
Token 与 R3A 证据不受影响。
