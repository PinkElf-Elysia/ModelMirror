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

Managed Agent 的客户端恢复调用以服务端固定的 Client 等待 ID 区分发生次数：
同一个等待恢复不能重新派发已有模型调用，下一次真实等待才可建立新的阶段。
开始调用前复核 lease、等待节点、工具和终态请求归属；页面内容、模型输出和客户端
自报身份不能创建这一阶段。初始调用与审批恢复身份保持原约定。
升级前旧恢复身份已有任何账本记录时保守阻断，不通过新 ID 绕过未确认结果；旧身份
检查与新阶段登记处于同一 SQLite 事务。模型调用结果不确定仍不得自动重发，人工
需先核对回执，不能把本机制理解为安全重试或外部副作用回滚。
回退应关闭新增 Managed Client 恢复派发并保留新旧账本的识别与拒绝逻辑；不能恢复
为只查询旧阶段的实现，否则可能漏查新阶段已有的执行证据。不得清理账本来重试。

请求状态为：

`pending -> dispatched -> running -> completed / failed / cancelled / expired / uncertain`

- 读取操作断线后可以回到 `pending` 安全重放。
- 发送前必须先持久化 `dispatched`，并核对请求 revision、有效期及当前连接；登记失败不发送，过期或重复请求不再发送。
- 每次派发同时保存连接身份与递增的 `dispatch_count`；WebSocket 的 accepted/result/error 必须匹配服务端当前连接及本次代次。旧连接、已撤销连接、前次重派结果均不能结算新请求。连接身份不接受客户端消息自报。
- Chrome/Office 回传原请求的代次，只读缓存按 request/dispatch 隔离；重派不能复用旧读取结果。修改操作仍保留 operation 缓存，不能借代次升级获得修改重派许可。旧客户端未回传代次时，仅兼容当前连接首次派发；再次派发需要更新客户端，否则拒绝结算并保留保守状态。
- `running/completed/failed` 回执只有在保存成功后才对恢复消费可见；保存失败保留原状态，不能仅凭内存中的未提交结果继续工作流。已提交终态在重启后复用原回执，不重新派发；未派发请求不能提交失败回执。
- 人工重试与取消同样先保存后生效，保存失败保留原状态。只读请求及尚未派发的修改请求可显式重派；已派发或结果不确定的修改请求禁止沿用原 request/operation 身份重派，必须先人工核对实际结果。本轮不提供新的修改重执行协议。
- 重试和新派发还必须核对父执行：仅允许仍停在同一 `task_id/run_id/client_tool/wait_id` 的等待点。父执行缺失、结束、已进入恢复、等待其他事项或状态不可验证时拒绝；协调器不可用时重试关闭。请求自身可重试不等于父执行允许继续，列表、详情及操作响应统一返回有效许可。
- 父执行锁先于 Client Store 锁，保护重试、派发登记与回执唤醒的同步决策；旧失败回执不能把刚重试的请求唤醒。派发登记后发生的取消不能证明网络动作撤销，网络发送不持 Store 锁，本机制不是跨 Store 事务或外部动作回滚。
- 截图上传使用同一父等待点和派发连接校验，扩展通过 `X-ModelMirror-Dispatch-Count` 回传原请求代次；缺省仅兼容首次有效派发。读取上传内容后再次校验，取消、断线、重派、过期或结束后的上传不得登记。上传只在 `dispatched/running` 接受，不把产物登记当作工具完成回执。
- 截图完整文件先原子改名，再提交 Store 登记；登记成功前不公开产物。文件或登记失败恢复内存并清理本次文件；进程崩溃遗留的未登记文件不可查询，不自动扫描删除历史文件，不承诺跨文件断电事务。同一有效代次重复上传相同内容复用产物，不同内容拒绝；新代次不复用旧截图，已有历史产物继续可读。
- 取消已派发的修改请求只停止等待，保留 `uncertain`，不表示客户端动作被撤销。已完成回执不会被取消覆盖。界面使用服务端 `retryable/retry_blocked_reason` 展示许可与原因，缺失许可时不开放重派。
- 可能修改页面的操作从 `dispatched` 起断线即进入 `uncertain`，即使未收到 `running` 确认也绝不自动重放。标记已保存但实际是否发出无法确认时同样保守阻断，不能据此声称页面操作已经发生。
- Workflow/Xpert Chat 超时后保留受上述边界约束的显式 retry/cancel；GoalStep、AgentTask 和 Handoff 使用 `waiting_client`，最终超时进入 `needs_attention`，不计作模型或工具重试。
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
