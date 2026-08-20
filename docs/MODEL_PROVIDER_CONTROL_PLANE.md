# Model Provider Control Plane

状态：Round 0–2 建设批准，默认数据面切换未批准
Round 2 基线：`origin/main@ed27945e676986a5f7bb7d07619b10e96a03e534`
更新日期：2026-08-20

## 决策

ModelMirror 建设单租户 Model Provider Control Plane，统一管理动态 Provider
连接、凭据、策略、诊断和安全出口。首期主体固定为 `tenant_id=local`，但认证、
Service 与 Repository 接口继续保留租户边界。

newAPI 是独立、未修改的 OpenAI-compatible 数据面，不是 ModelMirror 前端模块。
ModelMirror 不嵌入、代理、复制或修改 newAPI 管理界面，只允许配置一个外部管理
链接。newAPI 使用独立 Compose 项目、数据目录和生命周期；核心栈只通过显式
URL/Key 契约连接。上游采用 AGPLv3 并包含附加条款，实际部署者仍需独立完成
许可证与使用场景审查。

Coding Runtime 继续使用自己的 Provider URL、Key、网络和审批边界，不读取
Provider Control Plane 的会话、SQLite 或主密钥。它只加入独立外部
`coding_provider` 网络，不加入控制面/newAPI 的 `provider` 网络。

## 授权与门禁

- 本决策解除 2026-07-28 对原生路由继续建设的功能冻结，仅批准控制面、安全与
  兼容建设。
- `native` 默认仍需至少 500 次真实请求、连续 14 天、无 P0/P1、故障演练和人工
  验收；数据不能被修改来伪造门槛。
- Round 0–1C 不改变 `/api/chat`、SSE、公开模型目录或默认路由协议。
- Round 2 统一普通文本 Chat 的内部调用契约，但不改变默认 Provider、公开请求协议、
  SSE 或路由回执，也不产生影子复制流量。
- newAPI 是否成为强制默认数据面由后续独立决策决定。
- 任一门禁失败时保留现有静态数据面和 SQLite，不自动迁移或删除数据。

## 部署边界

- 核心栈：`docker-compose.yml`，能够在 newAPI 未运行时达到基础健康状态。
- newAPI 栈：`deploy/newapi/compose.yml`，使用固定镜像 digest 并复用现有
  `new-api-data`。
- 互联：`deploy/newapi/modelmirror-overlay.yml`，只让 Server 加入显式共享网络并注入
  URL；Coding Runtime 不加入该网络。
- newAPI 栈必须先启动；核心 Compose 不使用跨项目 `depends_on`，也不接管其升级、
  停止或数据恢复。

## 管理会话与动态出口

- `POST/GET/DELETE /api/router/admin/session` 使用外部注入、至少 32 字符的配对密钥；
  服务端只保存 32-byte 随机会话令牌的哈希，会话绝对有效期 8 小时并随重启失效。
- Cookie 限定 `HttpOnly; SameSite=Strict; Path=/api/router`；非 HTTPS 仅允许 loopback，
  写操作还必须提供内存中的 `X-ModelMirror-CSRF`。
- 动态 Provider 的公网目标仅允许 HTTPS；内网 HTTP(S) 必须精确加入
  `MODEL_MIRROR_PROVIDER_INTERNAL_ALLOWLIST=host:port`。
- 每次请求重新解析全部 A/AAAA 结果；混合公共/私有解析整体拒绝。实际连接固定到已批准
  IP，同时保留原始 Host 与 TLS SNI，不跟随重定向且不读取代理环境变量。
- Metadata、link-local、multicast、unspecified 与保留地址永久禁止，白名单不能覆盖。

## 主密钥与显式迁移

密钥优先级固定为：测试构造参数、`MODEL_MIRROR_CREDENTIAL_MASTER_KEY`、弃用的
`MODEL_ROUTER_CREDENTIAL_MASTER_KEY`、开发模式本地 `credential-master.key`。启用
`MODEL_MIRROR_REQUIRE_EXTERNAL_CREDENTIAL_MASTER_KEY=true` 后只接受规范变量；缺失时
锁定动态控制面，不回退旧变量或本地文件。

变更密钥必须显式运行：

```powershell
python -m server.model_router.migrate_credentials --storage-dir <path>
```

命令先全量解密预检，再使用 SQLite Backup API 创建时间戳备份，在
`BEGIN IMMEDIATE` 中重加密并逐条回读验证，最后写入密钥指纹。失败会 Rollback；旧
数据库备份和本地旧密钥文件不会被删除。回退时恢复备份并重新启用旧密钥来源。

## 文本 Chat 契约与 newAPI 认证

- 内部契约版本为 `modelmirror-provider-chat-v1`。Static、OmniRoute sidecar 与 Native
  managed target 使用相同的 Base URL、`/models`、`/chat/completions` 解析规则。
- Managed target 在真实请求前继续执行 DNS 解析、地址审批与 IP pinning；Static 与
  sidecar 仍是可信部署输入，不被动态内网白名单误阻断。
- newAPI 认证只验证核心文本 Chat 兼容性。它不证明多模态、RAG、Workflow、Agent、
  Coding 或默认数据面资格，也不计入 Native Router 的 500 次/14 天门禁。
- `POST /api/router/connections/{id}/models/refresh` 刷新可认证模型；认证 POST 要求管理
  会话、CSRF、`Idempotency-Key` 和显式费用确认。
- 每次认证先刷新模型目录，再发送最多一个固定合成请求；付费 POST 不重试、不轮换 IP、
  不跟随重定向。SQLite v12 只保存脱敏检查、指标和配置指纹，不保存 Prompt、模型正文
  或凭据。
- `MODEL_MIRROR_PROVIDER_CHAT_CERTIFICATION_ENABLED=false` 可停止新的认证操作，不影响
  连接管理、现有认证记录或默认数据面。
- 连接的 Base URL、类型、scope 或凭据变化会让旧结果派生为 `stale`；名称或健康检查
  时间变化不会。Server 重启会把遗留 `running` 标为 `uncertain`，不会自动重放。

## 回退

关闭管理面或回退路由不得删除 Provider SQLite、newAPI 数据目录、旧主密钥或迁移
备份。部署回退通过恢复上一版本 Compose 与对应显式环境配置完成。
