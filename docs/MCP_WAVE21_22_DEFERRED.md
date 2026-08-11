# MCP 第 21—22 批：暂缓实现收口

## 结论

第 21、22 批只完成目录归组、阻断原因、恢复条件和回退边界，不新增运行时。
所有条目继续保持 `planned`，没有镜像、命令、端点、凭据槽、持久卷、工具策略或可绕过状态的功能开关。

恢复采用条件触发而非日历承诺：只有各自基础能力完成、通过独立安全审计并获得新的逐批实施授权后，
才进入版本、Schema 和代表调用核验。在此之前不安排共享栈验证或真实账号连接。

## 第 21 批：状态化资源

固定条目：

- `chopratejas-headroom`
- `samvallad33-vestige`
- `goldentrii-agentrecall`
- `juyterman1000-entroly`
- `patdolitse-piia-engram`
- `beever-ai-beever-atlas`
- `pv-bhat-vibe-check-mcp-server`

统一阻断原因：这些产品会创建或更新长期记忆、反思、关系或上下文资源，不能安全复用仅面向一次性文件产物的
临时工作区。当前还缺少项目级持久卷所有权、租户/项目隔离、容量与模型费用配额、保留/导出/删除生命周期、
写入审批、幂等/未知结果、崩溃恢复和可验证清理。

恢复门槛：

1. 每个持久资源绑定不可伪造的 tenant、owner、project 和固定 schema revision。
2. 创建、更新、压缩、合并和删除均有配额、一次性审批、幂等键与审计账本。
3. 用户可查看占用、导出和彻底删除；服务重启后保留策略与删除 tombstone 可恢复。
4. 外部模型调用具有显式供应商、费用预算、内容最小化和无自动重试策略。
5. 真实镜像完成跨项目越权、并发写、超时、崩溃恢复、配额和清理验收。

## 第 22 批：多租户与 OAuth

恢复组包含原第 10 批的 Gmail、Atlassian、Google Calendar、Google Drive、Microsoft 365、
OneDrive、Sentry、Azure、Box、Cloudflare、GitHub、Linear、Neon、Slack，以及第二阶段的：

- `r-huijts-strava-mcp`
- `tiberriver256-mcp-server-azure-devops`
- `tacticlaunch-mcp-linear`

统一阻断原因：当前部署没有贯穿每个请求、目录会话、凭据和审批的不可伪造用户主体；OAuth 2.1 的
PKCE/state、最小 Scope、刷新、撤销、解绑、账号/资源所有权证明和租户级审计也未形成完整闭环。

恢复门槛：

1. 认证主体由服务端验证并绑定 tenant/owner，不接受客户端声明身份。
2. OAuth 使用 PKCE、state、固定 redirect URI、加密 Token 存储、轮换、刷新、撤销和独立解绑。
3. 首版只复审固定账号与资源范围的只读工具；连接前完成真实账号最小 Scope 预检。
4. 消息发布、Issue/资源写入、基础设施管理、账号管理和批量操作即使 OAuth 完成也继续 blocked。
5. 真实供应商完成多账号串扰、撤销后访问、Scope 漂移、限流、超时和审计脱敏验收。

## 状态与回退

GoGraph 验收晋级后，目录为 `68 ready / 31 planned / 101 blocked`；第二阶段 100 项为
`23 ready / 17 planned / 60 blocked`。第 21、22 批不改变 ready 数，也不进入任何默认 allowlist。

本批没有数据迁移或外部状态。回退只需恢复归组文案；若未来开始实现，统一回退为移除精确 allowlist ID、
断开对应目录会话并恢复 `planned`，不得自动删除外部账号数据或假定服务商 Token 已撤销。
