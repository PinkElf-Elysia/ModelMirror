# MCP Notion 收口验收证据

- 验证基线：`main@821067a7`，日期 `2026-08-27`。
- 独立预览：`127.0.0.1:15287`，使用独立容器、卷和测试端口；共享栈未重建。
- 真实目标：官方 Registry `com.notion/mcp@1.0.1`，固定 Origin `https://mcp.notion.com`。
- 真实认证：标准 OAuth 完成，页面仅显示“Token 已加密保存”、Scope `default` 和 revision，不显示 Token、授权码或回调地址。
- 真实复核：隔离 `initialize`、`tools/list` 和 Schema freeze 通过；人工批准 `notion-get-teams {}` 代表读取并只执行一次，远程返回成功，临时会话与 capability 已清理。
- 契约更新：上游聚合 Schema digest 变化但 28 个逐工具 Schema、工具名、Origin、source、OAuth policy 与 Scope 均未变化；本地契约通过不可变线性 revision 从旧指纹定向 supersede 到新指纹，仓库契约仍不可被本地覆盖，分叉和错误前驱继续 fail closed。
- Runtime 证明：新的逐次审批只产生一条 `completed` execution-ledger 记录，`retry_on_failure=false`，错误码为空，结果 digest 为 `7ad480d768704c3284a4adc6e1756279c119a9a56fffb488a83d826ab4af6f87`；调用后候选保持 `active`、`connected=false`、无污染，证明成功路径也销毁一次性会话。
- 发布边界：契约只允许 `notion-get-teams`，其他 27 个远程工具不进入 Runtime；候选已显式激活。
- 页面重放：Notion 显示 `active`、已复核和加密 Token；旧授权链接不存在；控制台无错误。
- OAuth 过期恢复：自动测试覆盖服务端 `expired`、客户端截止时间、晚点击和跨标签页新会话。过期链接被隐藏，用户只能创建新会话；旧授权码不重试。
- 帮助重放：`/help/review-remote-mcp-auth` 可见“授权链接已过期”恢复说明，操作步骤仍保持 8 步上限。

## 延期项

- New Relic 保持 staged：本机到供应商登录与控制台域名不可达，未保存或伪造凭据。
- Atlassian 保持 blocked：当前 Scope 含写入能力，不以只读名义放行。

## 回退

- 删除本轮前端状态判断和帮助补充即可恢复旧界面；不涉及数据库迁移。
- 关闭远程契约 Runtime 开关会停止认证型远程工具；Notion 契约、无秘密审计证据和加密 Token 保留。
