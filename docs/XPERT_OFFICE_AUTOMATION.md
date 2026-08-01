# Xpert Office Automation

最后更新：2026-07-19

## 能力边界

`office_automation` 是私有 `workflow_agent` 中间件。它复用 Client Tool V1 的一次性配对、持久等待、HITL、Tool Policy、Audit 和断点恢复，将操作交给用户主动绑定的 Word、Excel 或 PowerPoint 当前文档。它不是离线 DOCX/XLSX/PPTX 生成器，也不使用 Microsoft Graph。

公开 Xpert App 与 OpenAI 兼容 API禁止部署或运行该中间件。Office Host 不持有模型密钥、Runtime Store、文件路径或其他文档内容；服务端 API 只保存脱敏标题、随机文档绑定 ID、宿主类型、Requirement Set 和操作状态。

## 安装与配对

1. 运行 `scripts/setup-office-dev-cert.ps1` 生成并信任 localhost 开发证书。证书和私钥写入 ignored 的 `server/office_host/certs/`。
2. 使用 `docker compose --profile office -p modelmirror up -d --build --force-recreate` 启动 HTTPS Task Pane。
3. 从 `/runtime` 下载 `GET /api/runtime/office-host/manifest.xml`，在 Word、Excel 或 PowerPoint sideload 同一 add-in-only XML manifest。
4. 在 `/runtime` 创建 Office 配对码，在 Task Pane 中完成配对。
5. 用户必须点击“绑定当前文档”。切换文档或宿主后必须重新绑定。

Task Pane 通过 `https://localhost:8443` 和 `WSS /api/runtime/client-tools/connect` 工作。原始 host token 仅保存在 Office 本地存储；服务端只保留哈希和前缀。

## 中间件配置

- `clientHostId`：已配对 Office Host。
- `host`：`word / excel / powerpoint / all`。
- `allowDeletes`：是否允许删除幻灯片、形状或工作表。
- `allowImageInsert`：是否允许 PowerPoint 从同作用域 Runtime artifact 插入图片。
- `timeoutSeconds`：30 秒至 24 小时。
- `requireBoundDocument`：默认启用。

中间件要求 Agent 使用 Runtime 工具模式。所有文档修改工具必须被同一 Agent 的 `human_in_the_loop` 覆盖；删除还要求工具参数 `confirm=true`。执行顺序固定为：

`Tool Policy -> HITL -> Client Tool dispatch -> Office.js -> Audit`

读取操作断线后可以安全重新派发。修改操作在执行中断线时进入 `uncertain`，不能自动重放，只能由可信管理端处理。

## 22 个工具

PowerPoint（9）：snapshot、选择/新增/删除幻灯片、添加文本框/形状、更新/删除形状、插入 artifact 图片。

Word（6）：snapshot、插入文本、替换选区、插入标题、插入表格、搜索文本。

Excel（7）：snapshot、读取/写入区域、新增/删除工作表、自动调整区域、新增表格。

工具 schema 限制正文、区域、矩阵、形状和文件大小。Host 只声明当前 Office 应用及 Requirement Set 实际支持的工具，schema hash 不匹配时服务端不暴露该工具。

## 恢复与观测

运行等待继续使用 `wait_kind=client_tool`，Goal、Handoff 和 Automation 使用既有 `waiting_client` 语义。页面或容器重启后，读取请求可恢复，已完成修改通过稳定 operation receipt 返回原结果，不重复执行。

安全事件包括 `client_tool_waiting`、`office_operation_started`、`office_operation_finished` 和 `office_operation_uncertain`。事件、Audit 与 checkpoint 只记录 host、Office 应用、工具、状态、耗时和结果长度；不保存正文、表格值、图片 Base64、token、路径或密钥。

## 验收护栏

- 校验 22 个工具、宿主类型、schema hash 和 Requirement Set 过滤。
- 校验所有修改工具的 HITL 覆盖，删除权限与 `confirm=true` 双门禁。
- 校验同作用域 artifact、文档绑定、断线 `uncertain` 和 operation 幂等。
- 校验旧 Chrome Host 数据默认迁移为 `host_type=chrome`。
- 校验 App 部署 fail-closed，manifest 为 add-in-only XML，Task Pane 不包含任意脚本执行入口。
