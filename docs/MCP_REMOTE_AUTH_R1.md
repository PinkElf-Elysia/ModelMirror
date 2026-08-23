# MCP Remote Auth R1

R1 在 R0 的共享 Broker 上增加固定 Token 只读远程链路。它仍然是
`local/local` 单主体能力，不提供多租户身份、RBAC、OAuth 或任意远程连接。

## 启用门禁

以下条件必须同时满足，否则绑定、解析、预检和激活均失败关闭：

```dotenv
MCP_REMOTE_AUTH_ENABLED=true
MCP_REMOTE_STATIC_TOKEN_ENABLED=true
MCP_REMOTE_AUTH_LOCAL_SINGLE_OWNER_ACK=true
MODEL_MIRROR_CREDENTIAL_MASTER_KEY=<由部署环境注入，至少 32 字符>
MODEL_MIRROR_REQUIRE_EXTERNAL_CREDENTIAL_MASTER_KEY=true
```

不要把真实 Secret 写入 `.env`、Compose、命令行、镜像或仓库。页面保存后只显示
掩码与 revision；策略、契约、证据、日志和导出文件均不得包含 Secret、密文、
credential ID 或客户端提交的目标范围。

## 固定边界

- Hub 只接受官方 Registry 中固定公网 HTTPS/443、Streamable HTTP 的记录。
- 认证声明必须恰好包含一个 required+secret Header，且不能含 URL 变量。
- `Authorization` 固定映射为 Bearer；其他 Header 必须通过安全名称和禁止表。
- 客户端只能提交固定 slot、显示名称和 Secret，不能提交 tenant、owner、Origin、
  Header 名、credential ID、URL、环境变量或任意参数。
- Token 只在 Backend 解析后经 UDS 进入 `network_mode:none` 的 remote sidecar；
  egress 只传输原始 TLS，不能读取 Header。
- initialize 和 tools/list 只对瞬时网络失败安全重试一次；401/403 不重试；工具
  调用始终逐次审批且 `retry_on_failure=false`。
- 调用发出后断链或超时返回 `unknown_outcome`，污染候选并销毁会话，旧审批不得
  重放。

## Hub 与 Catalog

Hub 的静态 Token 候选必须先完成绑定，再进入 `static_token_https_tools_v1`
复核。发布的 `HubReviewedContractV2` 冻结认证策略指纹，但不包含绑定或凭据身份；
策略、来源、Schema 或绑定状态漂移会取消激活资格。

Catalog 仅为已声明固定 Origin、单一 credential slot 和固定认证 Header 的适配器
接入 Broker。R1 首个兼容目标为 `tavily-mcp`；关闭 Remote Auth 后，既有目录 Token
适配器继续走原兼容路径。

## 验收与回退

自动与隔离 fixture 可以证明策略、加密槽、UDS envelope、审批、401/403、撤销、
断开和零残留，但不能替代真实供应商门禁。R1 完成仍要求用户分别提供 Tavily Key
和一个非 Tavily Registry 候选的只读凭据，并完成各一次真实代表调用。

回退时关闭三个 `MCP_REMOTE_*` 开关并撤销相关 binding；匿名 Hub V1 契约和原有
Catalog 兼容适配器保持可用。SQLite 只保留无秘密 binding revision 与审计事件，
不执行数据迁移或物理删除。
