# MCP Remote OAuth R2B：本机授权与加密 Token revision

R2B 在 R2A 已冻结的 Protected Resource Metadata、Authorization Server
Metadata 和 public client 登记之上，增加本地单主体的 Authorization Code +
PKCE S256 授权会话、固定回调、加密 Token revision、刷新与本地撤销。

R2B **不把 OAuth Token 交给 MCP Runtime**，也不执行 initialize、tools/list
或工具调用。认证型远程 MCP 仍在预检入口 fail closed，等待 R2C 的契约 V3、
真实只读资源验收与逐次审批接线。

## 开关与前置条件

全部条件必须同时满足：

```dotenv
MCP_REMOTE_AUTH_ENABLED=true
MCP_REMOTE_AUTH_LOCAL_SINGLE_OWNER_ACK=true
MCP_REMOTE_OAUTH_ENABLED=true
MCP_REMOTE_OAUTH_AUTHORIZATION_ENABLED=true
MCP_REMOTE_OAUTH_TOKEN_STORAGE_ENABLED=true
MODEL_MIRROR_REQUIRE_EXTERNAL_CREDENTIAL_MASTER_KEY=true
MODEL_MIRROR_CREDENTIAL_MASTER_KEY=<外部注入，不写入仓库>
MCP_REMOTE_OAUTH_REDIRECT_URI=http://127.0.0.1:8765/oauth/callback
```

`MCP_REMOTE_OAUTH_REDIRECT_URI` 必须与 public client 登记完全一致。Backend 提供
`/oauth/callback` 与 `/api/mcp/remote-auth/oauth/callback` 两个固定路由；部署者可选
其中一个作为固定回调，不支持客户端动态覆盖。

关闭任一 R2B 开关后，授权、换票、刷新和本地 Token 解析均 fail closed；R2A
发现与登记仍可按其独立开关使用。

## 安全边界

- 授权服务器、authorization endpoint、token endpoint、resource 与 client ID
  均来自当前有效的 R2A discovery/registration；写 API 不接受 URL、Header、
  client ID、授权码、PKCE verifier、tenant 或 owner。
- 只有授权服务器声明的 `scopes_supported` 子集可选；未声明 Scope 时禁止授权。
- state 只保存 SHA-256 摘要；PKCE verifier 临时存入外部主密钥加密槽，换票后
  立即撤销。
- access/refresh token 作为一个加密 bundle 保存；SQLite 只记录无秘密 revision、
  Scope 摘要、到期时间和凭据引用。
- Backend 在生成响应前清空 OAuth callback 的 ASGI query，避免 Uvicorn
  access log 记录 code/state；若前置反向代理会记录原始 URL，部署者仍必须对
  两个固定 callback 路径禁用 query 记录或做等价脱敏。
- token endpoint 请求只经 Backend → UDS → `network_mode:none` OAuth sidecar →
  固定 capability egress 发出。sidecar 不接受任意 Header、代理或目标。
- 授权码换票和 refresh 一旦发出绝不自动重试。refresh 在远程请求前通过 SQLite
  attempt 对精确 Token revision 原子占用，跨进程并发只能派发一次。超时、断链、
  malformed 200、凭据提交失败或进程在派发后重启时均进入 `unknown_outcome`，旧
  state/revision 永久封锁。
- Registry/source/discovery/registration 漂移会取消待授权会话、使 Token stale 并
  撤销对应加密凭据。
- 本地撤销不会声称已完成远程授权服务器撤销；远程 revocation 留待后续轮次。

## 操作流程

1. 在 Hub 候选中冻结 OAuth 元数据并登记 public client。
2. 选择授权服务器明确声明的最小 Scope 子集。
3. 创建一次性授权链接；链接仅在创建响应中返回，页面刷新后不再重建旧链接。
4. 在授权服务器页面完成授权，固定 callback 换票并把 Token 写入加密槽。
5. 返回 Hub 并点击“刷新授权状态”。R2B 只展示 Scope、revision、到期时间和是否
   可刷新，不展示 Token、credential ID、state 或 verifier。
6. 可显式刷新或本地撤销；刷新属于可能轮换 refresh token 的不可重放操作。

## 回退

关闭：

```dotenv
MCP_REMOTE_OAUTH_AUTHORIZATION_ENABLED=false
MCP_REMOTE_OAUTH_TOKEN_STORAGE_ENABLED=false
```

随后从 Hub 撤销本机 Token 和未完成授权会话。审计表保留无秘密状态证据，不执行
数据迁移或物理删除；R0/R1 静态 Token 与 R2A discovery/registration 不受影响。
