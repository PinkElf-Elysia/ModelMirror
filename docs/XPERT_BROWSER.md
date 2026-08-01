# Xpert Browser Runtime

最后更新日期：2026-07-18

## 目标

`browser_automation` 为私有 Workflow、Xpert Chat、Goal 和 Handoff 提供隔离的服务端浏览器。实际 Chromium 运行在独立 Playwright sidecar 中，主服务仅通过 Unix Domain Socket 发送受限动作。该能力不向公开 Xpert App/API 开放，也不提供任意 JavaScript、DevTools、CSS 或 XPath 执行入口。

## 隔离模型

- Browser sidecar 只加入专用 egress network，不加入应用默认网络，不暴露宿主机端口。
- 容器以非特权用户运行，根文件系统只读，移除 Linux capabilities，并启用 `no-new-privileges`、PID、CPU、内存和临时目录限制。
- sidecar 不挂载仓库、`.env`、Runtime Store、Docker Socket 或主服务密钥。
- 浏览器私有状态写入独立 Docker volume；主服务只读该卷中已登记的截图和下载产物。
- Sandbox 工作区以只读方式挂载，只允许同一作用域的 `inputs/` 和已发布 `artifacts/` 进入文件上传。

## 网络策略

固定策略为 `public_with_domain_approval`：

1. 顶层 URL 只允许 HTTP/HTTPS，拒绝 URL credentials、`file:`、`data:`、`chrome:` 和其他本地协议。
2. 首次访问新顶层域名创建 `browser_domain` 审批；批准仅对当前 Browser session 生效，授权可撤销。
3. 内置 egress guard 在 DNS、HTTP、CONNECT 和重定向路径上拒绝 loopback、private、link-local、reserved、multicast、云元数据地址、Docker service name、`.local` 和混合公网/私网解析。
4. Playwright `BrowserContext.route` 进行第二次 URL 与授权检查；任一策略组件异常时 fail-closed。
5. Service Worker、QUIC 和 WebRTC 非代理 UDP 被关闭。页面 WebSocket 仍受 CONNECT、DNS 和 route 校验，不存在隐式 localhost bypass。

Windows Docker Desktop 或本机代理使用 `198.18.0.0/15` Fake-IP DNS 时，可通过 `BROWSER_ALLOW_SYNTHETIC_DNS=true` 显式兼容。该例外只接受合法公网域名的解析结果；IP 字面量、私网混合解析、单标签服务名和云元数据地址仍然 fail-closed。

`allowedDomains` 用于收窄可访问域，`blockedDomains` 始终优先。页面链接跨域时不会隐式放行；Agent 必须显式调用 `browser_navigate` 触发新域名审批。

## 会话与幂等

`BrowserSessionStore` 持久化 session、域名授权、操作和产物的安全元数据。作用域为：

- Xpert Chat：conversation。
- Goal：goal/step。
- Handoff：handoff。
- Workflow：task/node，默认保留 24 小时。

每个作用域默认复用一个 BrowserContext。空闲 30 分钟后关闭页面进程并保存 storage state；下次运行在域名授权仍有效时恢复最近安全 URL。Cookie、localStorage 和 storage state 不通过管理 API 返回。

所有副作用操作使用稳定 operation ID。已完成的点击、填写、选择、按键、上传、下载和关闭动作在审批恢复、请求重放或容器重启后返回原结果，不重复执行。

## Runtime 工具

`browser_automation` 注册以下工具：

- 导航与读取：`browser_navigate`、`browser_snapshot`、`browser_read`。
- 交互：`browser_click`、`browser_fill`、`browser_select`、`browser_press`、`browser_hover`、`browser_scroll`、`browser_wait`。
- 文件与页面：`browser_screenshot`、`browser_upload_file`、`browser_download`、`browser_close_page`。

Snapshot 仅返回 ARIA/role/name 摘要与短期 opaque ref。页面正文被标记为不可信外部内容并限制长度，不能覆盖系统提示或 Runtime policy。密码、支付卡、验证码和浏览器权限提示禁止自动填写。

工具顺序固定为：

`ToolPermissionPolicy -> 域名/HITL 审批 -> audit started -> Browser sidecar -> audit finished/failed`

`click/fill/select/press/upload/download` 必须由同一 `workflow_agent` 绑定的 `human_in_the_loop` 覆盖，静态校验和运行时都会再次检查。审批参数修改后重新执行工具 schema、域名、作用域和 policy 校验。

## API

- `GET /api/runtime/browser/capabilities`
- `GET /api/runtime/browser-sessions?scope_type=&scope_id=&status=`
- `GET /api/runtime/browser-sessions/{session_id}`
- `GET /api/runtime/browser-sessions/{session_id}/operations`
- `GET /api/runtime/browser-sessions/{session_id}/screenshot`
- `DELETE /api/runtime/browser-sessions/{session_id}/grants/{domain}`
- `POST /api/runtime/browser-sessions/{session_id}/close`
- `GET /api/runtime/browser-artifacts/{artifact_id}`
- `GET /api/runtime/browser-artifacts/{artifact_id}/download`

兼容 SSE 摘要事件为 `browser_session_started`、`browser_operation_started`、`browser_operation_finished` 和 `browser_artifact_published`。事件、audit 与 checkpoint 只保存域名、动作、状态、耗时、页面标题和字节数，不保存页面正文、表单值、Cookie、截图、下载正文、prompt 或密钥。

## 发布与回退

- Xpert 发布预检校验 Browser 配置和 HITL 覆盖。
- Xpert App 部署预检拒绝 `browser_automation`，公开 App/API 运行时也不会注册 Browser 工具。
- 删除 Browser binding 即可回退为原 `workflow_agent` 执行路径；既有 workflow、SSE 和 Toolset 协议不变。
- 关闭或归档 session 不删除已登记的 Runtime artifact。

## 验收护栏

修改 Browser 路径至少验证：网络地址分类、DNS 混合解析、域名审批、操作幂等、作用域隔离、文件上传下载、App 门禁、前端构建、后端全量测试，以及 Docker 中 localhost/private/cloud metadata 阻断 smoke。

客户端当前标签页桥已由 `XPERT-MIDDLEWARE-CLIENT-05` 独立交付，契约见 `docs/XPERT_CLIENT_TOOLS.md`。Browser sidecar 仍负责隔离后台浏览器，Client Tools 负责用户明确绑定且可能已登录的当前标签页，两者不共享 Cookie、session、工具 provider 或网络策略。后续进入 Automation 与 Consolidation。
