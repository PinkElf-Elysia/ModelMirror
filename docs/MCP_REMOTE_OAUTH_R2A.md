# MCP Remote OAuth R2A

R2A 为固定远程 MCP 建立 OAuth 发现与 public client 登记基础，但刻意停在用户授权
之前。它不会打开浏览器、接收回调、交换或刷新 Token，也不会改变 Hub Runtime、
激活资格或工具审批行为。

## 启用门禁

```dotenv
MCP_REMOTE_AUTH_ENABLED=true
MCP_REMOTE_AUTH_LOCAL_SINGLE_OWNER_ACK=true
MCP_REMOTE_OAUTH_ENABLED=true
MODEL_MIRROR_CREDENTIAL_MASTER_KEY=<由部署环境注入，至少 32 字符>
MODEL_MIRROR_REQUIRE_EXTERNAL_CREDENTIAL_MASTER_KEY=true
```

当前只支持 `local/local` 单主体。所有数据库身份仍包含 `tenant_id/owner_id`，客户端
不能提交主体、资源 URL、issuer、endpoint、Header、Scope 或凭据字段。

可选的 public client 登记配置：

```dotenv
# 仅当授权服务器 metadata 明确声明 client_id_metadata_document_supported=true
# 时可用；文档必须固定 client_id/client_name/redirect_uris/grant/response/auth method。
MCP_REMOTE_OAUTH_CLIENT_METADATA_URL=https://client.example/.well-known/oauth-client/modelmirror

# 兼容 DCR 所需的未来固定回调；R2A 不监听或使用该回调。
MCP_REMOTE_OAUTH_REDIRECT_URI=http://127.0.0.1:8765/oauth/callback
MCP_REMOTE_OAUTH_DYNAMIC_REGISTRATION_ENABLED=false
```

动态客户端登记是外部状态写入，默认关闭、无自动重试。发出后超时或断链固定记为
`mcp_remote_oauth_registration_unknown_outcome`；R2A 拒绝保存返回的
`client_secret` 或 `registration_access_token`。一次登记进入 `started` 后，无论上游
返回、解析或本机保存如何结束，同一发现 revision 均不得重放；若 Registry 来源或
发现快照在写入期间漂移，数据库 CAS 会拒绝把旧登记挂到新快照上。

## 发现链路

1. 从当前 Hub 候选读取固定公网 HTTPS/443 的 MCP resource URL 和 source digest。
2. 在 `network_mode:none` 的 OAuth sidecar 中进行有界 GET，读取可选的
   `WWW-Authenticate resource_metadata` 提示。
3. 逐个为确定性 well-known URL 申请临时精确出口 capability；每次请求完成即撤销。
4. 验证 Protected Resource Metadata 的 `resource` 与候选完全相同，并且只声明一个
   authorization server。
5. 按 OAuth metadata、OIDC issuer-path、OIDC append-path 的确定顺序发现授权服务器；
   验证 AS issuer、authorization/token endpoint、`authorization_code`、`code`
   response 和 PKCE `S256`，再冻结 canonical fingerprint。
6. Registry source、resource、issuer、endpoint 或 metadata digest 漂移时创建新发现
   revision，并将已有 client 登记标记为 stale。

所有 URL 禁止 userinfo、query、fragment、模板、IP 字面量、非 443 端口和重定向；
DNS 全答案、公网地址、重绑定和 SNI 继续由 Hub egress 门禁校验。

## R2A 明确不包含

- Authorization Code/PKCE 浏览器跳转、state/nonce、回调监听。
- access token、refresh token、Scope 同意、刷新、撤销或资源调用。
- confidential client、client secret、Basic、URL Token 或任意 Header。
- OAuth 候选激活、Runtime 工具暴露或自动复核通过。
- 多租户身份、RBAC、共享 SaaS 或跨主体授权。

关闭 `MCP_REMOTE_OAUTH_ENABLED` 即停止发现与登记；已有 R0/R1 静态 Token 和匿名
Hub 契约不受影响。SQLite 仅增加无 Token 的发现、client ID、登记证据、尝试账本和
审计事件，无需数据迁移；删除 Hub 候选会撤销对应的本机 client 登记并保留审计记录。
