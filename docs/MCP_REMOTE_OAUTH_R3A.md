# MCP Remote OAuth R3A 运维边界

R3A 只面向可信本地运维者，把 resource-bound OAuth Token 接入 MCP Hub
Review Factory 并发布 V3 契约。它不会激活 OAuth 候选，也不会向 AI Runtime
暴露 OAuth 工具。

实现固定使用 `mcp==1.27.2` 与 MCP `2025-11-25` 授权/传输语义；规范依据为
[MCP Authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)。
`2026-07-28` 传输握手迁移不在本轮范围内。

## 启用条件

以下开关默认均为 `false`，且必须继续使用外部凭据主密钥：

```dotenv
MCP_REMOTE_AUTH_ENABLED=true
MCP_REMOTE_AUTH_LOCAL_SINGLE_OWNER_ACK=true
MCP_REMOTE_OAUTH_ENABLED=true
MCP_REMOTE_OAUTH_AUTHORIZATION_ENABLED=true
MCP_REMOTE_OAUTH_TOKEN_STORAGE_ENABLED=true
MCP_REMOTE_OAUTH_REVIEW_ENABLED=true
MCP_HUB_REVIEW_FACTORY_ENABLED=true
MCP_HUB_LOCAL_CONTRACT_PUBLISH_ENABLED=true
MODEL_MIRROR_CREDENTIAL_MASTER_KEY=<external secret>
MCP_HUB_CONTRACT_SIGNING_KEY=<external secret>
```

不得将以上密钥写入仓库、Compose 文件或浏览器。当前主体固定为
`local/local`，不是多租户 RBAC。

## 固定流程

1. 重新发现并冻结 resource、Issuer、授权端点和推荐 Scope。
2. 登记 public client。DCR 固定声明 `application_type=native`。
3. 以 discovery、registration 和 Scope 摘要创建授权；客户端不能提交 Scope。
4. 回调校验 state，并在返回 `iss` 时与冻结 Issuer 完全比较；换票携带 resource。
5. 在 Review Factory 执行隔离预检、逐次批准代表调用、人工确认 read 工具并发布 V3 契约。
6. V3 发布结果固定 `activation_eligible=false`，原因是
   `mcp_remote_oauth_runtime_disabled`。

旧 R2B Token 没有 resource 绑定，只保留加密记录并显示为 `legacy_unbound`；
它不能刷新、复核或执行，必须重新授权。只有授权服务器明确声明
`offline_access` 时，界面才允许运维者额外请求 refresh token。R3A 不自动或
后台刷新。

## 回退

设置 `MCP_REMOTE_OAUTH_REVIEW_ENABLED=false` 并重启隔离环境即可停止新的
OAuth 复核。已发布 V1/V2 契约、匿名 Hub 和静态 Token 路径不受影响；V3
证据和旧 Token 记录保留用于审计，不执行物理迁移或删除。
