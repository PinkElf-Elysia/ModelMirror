# Xpert Client Tools

> 2026-07-19：Client Tool V1 现在同时承载 Chrome 当前标签页和 Office 当前文档宿主。两者使用同一配对、WebSocket、持久请求与恢复协议，但通过 `host_type`、能力 schema hash 和绑定状态严格隔离。Office 契约见 `docs/XPERT_OFFICE_AUTOMATION.md`。

最后更新日期：2026-07-18

## 目标

`client_tools` 让私有 Workflow、Xpert Chat、Goal 和 Handoff 可以暂停并把受限页面动作交给用户已配对的 Chrome。它只操作用户主动点击扩展按钮绑定的当前标签页，与隔离后台运行的 `browser_automation` 是两条不同的安全边界。

公开 Xpert App/API 不允许部署或运行 Client Tools。本轮不提供桌面宿主、本地命令、任意文件访问、密码/支付/验证码填写、秘密注入、任意 JavaScript、DevTools、CSS/XPath selector 或全站权限。

## 配对与认证

- `/runtime` 生成 8 位一次性配对码，5 分钟失效且只能消费一次。
- 首次 WebSocket 帧使用 `pair`，成功后返回一次性高熵 host token；服务端只持久化哈希和前缀。
- 后续连接首帧使用 `authenticate`。凭据不进入 URL、checkpoint、audit 或普通 API 响应。
- Chrome 扩展把 token 保存在 `chrome.storage.local`；管理端撤销 host 后旧 token 立即失效。
- 一个 host 同时只保留一个活动连接；新连接替换旧连接。扩展每 20 秒发送心跳，并使用 `chrome.alarms` 唤醒重连。

WebSocket 协议固定为 `modelmirror-client-tools-v1`。请求和结果必须同时匹配 `request_id`、`operation_id`、`tool_call_id` 和 `host_id`，跨 host 完成请求会被拒绝。

## 当前标签页授权

扩展采用 Manifest V3，最低 Chrome 116。权限仅包含：

- `activeTab`
- `scripting`
- `storage`
- `alarms`
- ModelMirror 本地后端的精确 host permission

扩展不申请 `<all_urls>`。用户必须点击扩展按钮绑定当前标签页；导航到新 origin、标签页关闭或用户主动解绑后，授权立即失效。`activeTab + scripting` 仅用于在已授权主 frame 注入仓库内固定函数；模型不能提交可执行脚本。

Snapshot 使用 role、accessible name 和短期 opaque ref。ref 绑定 host、tab、origin 和 snapshot revision；跨 tab、跨 origin 或过期 ref 拒绝执行。只遍历主 frame 与开放 Shadow DOM，跨域 iframe 和关闭 Shadow DOM 不支持。

## Runtime 工具

首批工具为：

- 读取：`host_page_snapshot`、`host_page_read`、`host_page_hover`、`host_page_scroll`、`host_page_wait_for`、`host_page_screenshot`。
- 修改：`host_page_click`、`host_page_fill`、`host_page_select`、`host_page_press`、`host_page_navigate`。

`host_page_navigate` 只能导航到当前 origin。Snapshot 最多 500 个元素，页面文本最多 24,000 字符，截图最多 5 MB，并通过独立认证 HTTP 上传登记为 Client Tool artifact。输入框当前值不会进入 snapshot；password、payment、OTP、captcha 和身份验证字段拒绝自动填写。

工具执行顺序固定为：

`ToolPermissionPolicy -> human_in_the_loop -> client dispatch -> audit finished/failed`

修改页面的工具必须由同一 `workflow_agent` 绑定的 HITL 覆盖。工具 schema hash、Agent allowlist、host capability、绑定标签页和 policy 都必须通过；LLM 工具选择器不能恢复被过滤或拒绝的工具。

## 等待、恢复与幂等

`WorkflowExecutionStore` 使用通用 `wait_kind + wait_id`：审批为 `approval`，客户端工具为 `client_tool`。旧 `approval_id` 数据继续兼容。

`ClientToolCoordinator` 在 host 在线时派发请求，在结果终态后领取 execution lease，并从已保存的 ReAct 消息和轮次继续执行。已完成节点和工具不得重跑。

请求状态为：

`pending -> dispatched -> running -> completed / failed / cancelled / expired / uncertain`

- 读取操作断线后可以回到 `pending` 安全重放。
- 可能修改页面的操作若在 `running` 时断线，进入 `uncertain`，绝不自动重放。
- Workflow/Xpert Chat 超时后保留显式 retry/cancel；GoalStep、AgentTask 和 Handoff 使用 `waiting_client`，最终超时进入 `needs_attention`，不计作模型或工具重试。
- API、事件和 checkpoint 只保存 host、工具、状态、耗时、结果长度与安全错误摘要，不保存页面正文、表单值、截图内容、token 或密钥。

## API

- `GET /api/runtime/client-tools/capabilities`
- `GET /api/runtime/client-tool-coordinator/status`
- `POST /api/runtime/client-hosts/pairings`
- `GET /api/runtime/client-hosts`
- `GET /api/runtime/client-hosts/{host_id}`
- `POST /api/runtime/client-hosts/{host_id}/revoke`
- `GET /api/runtime/client-hosts/extension.zip`
- `GET /api/runtime/client-tool-requests`
- `GET /api/runtime/client-tool-requests/{request_id}`
- `POST /api/runtime/client-tool-requests/{request_id}/retry`
- `POST /api/runtime/client-tool-requests/{request_id}/cancel`
- `POST /api/runtime/client-tool-requests/{request_id}/artifact`
- `GET /api/runtime/client-tool-artifacts/{artifact_id}`
- `GET /api/runtime/client-tool-artifacts/{artifact_id}/download`
- `WS /api/runtime/client-tools/connect`

扩展 ZIP 由源码动态生成，不提交二进制构建产物。`/api/runtime/client-tools/fixture` 是不含敏感数据的本地验收页。

## 验收护栏

修改 Client Tools 至少验证：配对码失效与单次消费、token 不落明文、WebSocket 重连、host 替换、schema hash、HITL 先于派发、跨 host/作用域拒绝、读操作重放、写操作 uncertain、断点续跑、Goal/Handoff 状态、App 部署阻断、扩展权限与前端生产构建。

Chrome API 采用官方 Manifest V3 行为：`activeTab` 是临时当前标签授权，配合 `scripting` 执行固定注入；`captureVisibleTab` 由 `activeTab` 授权。实现不复制 Xpert AGPL 源码。
