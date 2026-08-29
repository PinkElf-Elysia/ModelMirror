# R4A 认证型远程 MCP 统一复核验收记录

## 基线与隔离环境

- 验证日期：`2026-08-26`。
- 最终提交基线：`origin/main@f9e3cfe25362dc569b3ca50d1949123533cfbb36`。
- 验收期间主线新增的 4 个提交只触及 RAG、模型目录和图片路径，与本轮文件零交叉；获得 Commit 授权后已无冲突 rebase 到该基线。
- PR 工作树：`C:\tmp\modelmirror-mcp-remote-unification-r4a-review`。
- 前端隔离预览：`http://127.0.0.1:15285`；后端隔离端口：`18286`。
- 仅使用本轮专用容器、临时 SQLite 和独立 Docker 卷；未重建共享栈。
- 本轮身份边界为 `local/local` 单主体；没有验证多租户身份、RBAC 或对象隔离。

## 真实双路径门禁

### Catalog static-token：GitHub

- 固定项目：`github-mcp-server`；固定 Origin：`https://api.githubcopilot.com`。
- 未认证请求返回 401；认证后完成 tools/list 与 Schema 冻结。
- 精确 GitHub manifest 只允许空 `completions` 作为惰性、不可调用声明；全局 tools-only 策略未放宽。
- 系统生成代表调用 `search_code`，固定参数为 `{"query":"modelmirror-review"}`；只派发一次，远端未报告错误。
- 发布契约只包含 `search_code`，指纹为 `34bc8bda4d0339fc3ab5b1a40e97dc99b38698d383b142a36fbb99f184eec852`。

### Catalog OAuth：Tako

- 固定项目：`tako-mcp`；固定 Origin/resource 为 `https://mcp.tako.com` / `https://mcp.tako.com/mcp`。
- 使用 Catalog 目标绑定的新 OAuth 授权；Hub candidate Token 未被复用。
- Token 状态为 active、resource-bound，Scope 为 `mcp offline_access`；不存在 write/admin/delete/publish/trade/device 语义。
- 系统生成代表调用 `tako_available_data`，固定参数为 `{"q":"modelmirror-review"}`；只派发一次。
- 结果为 MCP content，大小 `2479` 字节，digest 为 `5d171a8b96eb04ff297f8d473d0ef6a5d09bb9842884d764936ee79eb2ab5237`；远端未报告错误。
- 临时会话关闭和 capability 撤销均为 true。
- 发布契约只包含 `tako_available_data`，指纹为 `4096467fa1e441269ee21179fbfc8a6e5f8c7b7b83ad2e2126dce4105a1f53d5`。
- 契约导出由仓库 `CatalogReviewedRemoteContractV1` loader 重载后字节级无差异。

两条 Catalog 契约发布后均为 reviewed、`activation_eligible=false`、Runtime 工具数 `0`，符合 R4A 不开放新 Runtime 工具的边界。

## 真实用户路径与截图

预览器从 `/mcps` 搜索 Tako，打开“认证与复核”，核对固定 Origin、OAuth 2.1 + PKCE、`2025-11-25`、已复核状态和 Runtime 0。授权成功后旧的一次性授权链接会立即清空，不再留在 UI/DOM；页面只展示无秘密的 Token revision 和 Scope。

- 截图：`client/public/help-center/f9e3cfe2/catalog-tako-reviewed.png`（资产已清理，保留哈希供审计）
- SHA256：`A20E4275B0E8C891B7F0D12815EB04C64B2A3922123264F6856DB14BE61AB319`
- 截图不含地址栏、OAuth callback、授权码、Token、client ID、用户信息或本机密钥。

## 安全与外部影响

- 临时 GitHub/Tako DNS 映射均只用于一次性隔离验收；当前活动 egress 的 `ExtraHosts=null`，临时映射容器已停止。
- 活动 egress 保持 UID `65532:65532`、只读根、`cap-drop=ALL` 与 `no-new-privileges:true`。
- 没有读取、输出或记录用户凭据；Token 只存在于本地加密槽和短会话内存。
- 代表工具调用不自动重试；调用派发后的歧义结果仍按 `unknown_outcome` 处理。
- 没有执行写入、发布、管理、交易、设备控制或高风险 Scope 操作。
- 仅创建获授权的本地提交；没有推送、PR、共享栈重建或部署。

## 当前验证状态

- GitHub 与 Tako 真实双路径门禁：通过。
- Catalog 契约导出/重载：通过。
- OAuth/Hub/Catalog/Review/Runtime 定向后端：`263 passed`。
- Workflow contract：`6 passed`。
- 完整后端（排除单独运行的 Workflow contract）：`4870 passed, 29 skipped`。
- 前端 typecheck 与生产 build：通过（保留现有大 chunk 警告）；本轮 MCP/帮助中心精确定向前端为 `5 files / 39 tests` 通过。
- 完整前端为 `120 files / 728 tests` 通过，另有 `ModelCard.test.ts` 与 `tokenPricing.test.ts` 各 1 个 UTC 定价断言失败。两项实现、测试和模型数据依赖相对本轮 diff 均为零差异，并已在一次性纯 `origin/main@f9e3cfe2` 工作树独立复现同样的 `2 failed / 12 passed`，因此记录为同步主线的既有基线失败，不归因于 R4A。
- orchestration worker：build 通过；本地 `75` 项及 upstream 套件全部通过。
- `docker compose config --quiet` 与 `git diff --check`：通过。
- Secret 模式扫描覆盖 36 个变更/新增文本文件：0 命中。
- UDS 健康收口：remote `active_sessions=0`、egress `active_grants=0`、OAuth `token_storage_enabled=false`；socket 均为 `0660`、UID/GID `65532:65532`。
- 已同步到最新 `origin/main@f9e3cfe2`；下列最终门禁均在同步主线后复跑。创建 PR 前仍需单独获得 Push 与 PR 授权。

## 回退

关闭 `MCP_REMOTE_REVIEW_UNIFICATION_ENABLED` 与 `MCP_REMOTE_CATALOG_OAUTH_ENABLED` 即可停止统一复核和 Catalog OAuth 入口。现有 Hub V1–V3、Catalog 静态适配器、无秘密审计证据和未激活契约保留，不执行破坏性迁移或物理删除。
