## 完成结果

你会在 MCP 工具货架中识别需要静态 Token 或 OAuth 的远程服务，完成受控认证与复核，并知道哪些安全提示意味着必须停止，而不是反复重连或绕过门禁。

## 适用对象

适合在本地单主体部署中负责配置远程 MCP、保管供应商凭据和审核只读工具的运维者。

## 开始前

- 确认页面显示的是固定 Origin，并且服务已标记为 `remote-mcp`；不要自行输入 URL、Header、Scope 或环境变量。
- 准备供应商要求的 HTTPS 访问令牌，或可以完成 OAuth 登录的账号。SSH Key 和 GPG Key 不能代替 API Token。
- 确认本地部署已启用远程认证、外部主密钥和 Review Factory。开关未开启时不要尝试绕过禁用按钮。
- 认证、代表调用和供应商 API 可能受账号权限、限流或费用影响。使用真实账号前先核对供应商规则。

## 真实范例

GitHub 固定远程项目要求静态 Bearer Token。运维者在“认证与复核”中核对固定 Origin，只保存供应商签发的 HTTPS Token，再让 Review Factory 读取工具列表并提出一次固定的代表调用。GitHub 的精确 manifest 只容忍服务端返回的空 `completions` capability，并把它当作不可调用的惰性声明；这不是全局放宽，其他非工具 capability 仍会阻断。

Tako Catalog 项目则要求目标绑定的全新 OAuth 授权。Hub 中已有的 Tako Token 不会复用到 Catalog；授权、代表读取和契约发布成功后，页面仍明确显示“R4A 不激活，Runtime 工具数为 0”。

![Tako MCP 已完成目标绑定 OAuth 与统一复核，R4A 仍保持 Runtime 工具数为 0](/help-center/f9e3cfe2/catalog-tako-reviewed.png)

## 操作步骤

1. 打开 [MCP 工具货架](/mcps)，找到标记为远程连接且需要认证的项目。你应该能看到“认证与复核”入口，而不是任意连接配置框。
2. 打开“认证与复核”，核对固定 Origin、认证类型和“本地单主体”提示。Origin 与预期不一致时立即停止。
3. 按页面显示的唯一认证方式继续：静态 Token 只填写供应商访问令牌；OAuth 先重新发现元数据，再检查 Issuer、资源和推荐 Scope。页面不会接受自定义 Header 或 Scope。
4. 保存 Token 或完成 OAuth 回调后刷新状态。你应该只看到掩码、revision 或“Token 已加密保存”，不应再次看到明文。
5. 创建受控复核批次，等待自动阶段完成。系统会冻结认证后的工具列表和 Schema，并在代表调用前暂停。
6. 核对精确 Origin、工具、参数和风险提示后，逐次批准一次代表调用。调用不会自动重试；断链或超时时应按“结果未知”处理。
7. 只选择已实测并人工确认为只读的工具，生成并发布本机不可变契约。契约发布不会自动激活服务，也不会直接把工具加入 Runtime。

## 常见问题

### 为什么凭据已经保存，复核仍然被阻断？

凭据正确只证明可以认证。非工具 capability、高风险 Scope、Schema 漂移、私网或合成 DNS、Origin 变化都会让系统继续阻断。请按固定错误码处理，不要更换任意 URL 或放宽 Header。

### GitHub 页面中的 SSH Key 或 GPG Key 可以填写吗？

不可以。远程 Bearer 认证需要供应商签发的 HTTPS API Token。SSH Key 用于 Git/SSH 连接，GPG Key 用于签名，它们不是 MCP API 凭据。

### Registry 已收录，为什么还要 Review Factory？

Registry 只提供身份与发现元数据，不等于安全认证。ModelMirror 仍要冻结 Origin、认证策略、工具 Schema、代表调用证据和人工确认的工具子集。

### Hub 里已经授权过同一服务，Catalog 还需要再授权吗？

需要。凭据按主体、目标类型、目标 ID、resource、issuer 和策略指纹绑定。Hub candidate 的 Token 不能用于 Catalog project；目标变化必须重新授权，避免跨目标复用 Bearer。

### 服务未先返回 Bearer challenge，为什么仍可能发现 OAuth？

只有固定 Catalog manifest 已冻结同源 Origin、resource、issuer 和 OAuth 策略时，系统才可读取同源 Protected Resource Metadata。部分服务允许匿名读取少量工具，同时为授权后的工具提供 OAuth；这条兼容路径不接受客户端 URL、Header 或 Scope，也不会改变 Hub 的严格门禁。

### 契约发布后为什么仍不能调用？

发布只让目标具备后续激活资格。Runtime 开关、当前 Token revision、Scope、Schema 和逐次审批仍必须全部满足；R4A 复核阶段不会新增 Runtime 工具。

## 限制

- 当前只验证本地 `local/local` 单主体模式，不代表多租户权限和对象隔离已经完成。
- 只支持 manifest 或 Registry 冻结的 HTTPS Streamable HTTP、静态 Token 或标准 OAuth；不支持任意 URL、Header、stdio 或 package 安装。
- 明确包含写入、管理、删除、发布、交易或设备控制含义的 Scope 保持阻断。
- 服务声明 prompts、resources、completions 等非工具 capability 时默认停止。唯一例外是精确 manifest 明确允许且服务端返回为空的惰性声明；它不会成为工具，也不能由 Runtime 调用。
- 本指南验证了认证、代表读取、V3 契约发布与导出；没有验证 Catalog Runtime 激活或写工具。

## 下一步

阅读[操作前检查可用性、费用与数据影响](/help/check-availability-cost-data)，再决定是否在后续 Runtime 阶段激活已发布契约。
