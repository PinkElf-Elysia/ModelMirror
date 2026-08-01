# Xpert App 与兼容 API

> 安全补充（2026-07-19）：公开 Xpert App 与 OpenAI 兼容 API禁止部署 `office_automation`。Office 当前文档操作只允许可信私有 Workflow、Xpert Chat、Goal、Handoff 与 Automation 使用，不提供 App policy 放行开关。

> 安全补充（2026-07-18）：公开 Xpert App 与 OpenAI 兼容 API 禁止部署 `client_tools`。客户端当前标签页只能用于可信私有 Workflow、Xpert Chat、Goal 与 Handoff，App policy 不提供放行开关。

> 安全补充（2026-07-16）：公开 Xpert App 与 OpenAI 兼容 API 禁止部署 `browser_automation`。服务端 Browser 只面向私有 Workflow、Xpert Chat、Goal 和 Handoff，App policy 不提供放行开关。

最后更新日期：2026-07-18

## 能力边界

已发布 Xpert 可以创建一个未列出 App，并显式部署任意不可变 `XpertVersion`。App 固定运行部署版本；编辑草稿或发布新版本不会改变线上行为，重新部署历史版本即完成回滚。

公开 App 不允许客户端替换 workflow、模型或版本，也不开放附件、Goal 和记忆候选写入。工具、Handoff、Xpert 记忆和动态知识读取默认关闭，必须由管理端显式开启。工具调用还要求工作流包含并先执行 `tool_policy`；策略未加载时默认拒绝。

公开 App 和 OpenAI 兼容 API 不支持交互式暂停。部署预检会拒绝包含 `human_intervention` 节点，或通过 middleware binding 绑定 `human_in_the_loop` 的版本。该限制仅作用于公开部署；私有 Xpert Chat、Workflow、Goal 和 Handoff 可以使用持久化审批与恢复。

公开 App/API 同样禁止部署 `sandbox_files`、`sandbox_shell` 与 `skills_runtime`。这些能力可执行文件和代码，只允许在私有 Workflow、Xpert Chat、Goal 与 Handoff 中通过隔离 sidecar 和 HITL 使用；不能通过 App policy 开关放行。

公开 App/API 也禁止 `scheduler`、`ralph_loop`、`plugin_hooks` 和 `knowledge_writer`。公共调用不能创建持久计划、启动跨请求循环、执行 Skill Hook 或提出知识写入；这些能力只面向可信私有运行入口。

动态知识工具受独立 `allow_knowledge_read` 策略控制。显式开启后，App 只能读取已发布工作流中 `knowledgeBaseIds` 固定的活动知识版本；不能扩展到其他知识库。任何启用了 `knowledgeWriteEnabled` 的 Xpert 都不能部署为 App，公开运行时也永远不注册 `knowledge_propose_write`。固定的 `knowledge_retrieval` / `knowledge_citation` 节点保持兼容，并继续随部署快照运行。

Data X 工具受独立 `allow_datax_read` 策略控制。部署预检会验证中间件固定的项目和语义模型真实存在且 scope 一致，并要求工作流包含 `tool_policy`。启用后 App 只获得已发布指标的只读查询和受控结果展示；指标提案、草稿修改、发布、原始明细导出和任意 SQL 均被禁止。

## 访问凭据

- 分享链接格式：`/apps/{slug}#access={share_token}`。Fragment 不进入 HTTP 请求日志，前端读取后立即从地址栏移除，并通过 `X-ModelMirror-App-Token` 发送。
- 机器调用使用 `Authorization: Bearer <api_key>`。
- 分享 token 与 API key 仅在创建或轮换时显示一次。服务端只持久化 SHA-256 哈希、前缀和状态，比较使用恒定时间函数。
- API key 可以命名、设置过期时间和撤销。分享 token 轮换后旧链接立即失效。

默认限额为 30 RPM、每日 1000 次、最大并发 2。每日计数持久化；RPM 与并发计数属于单进程内存状态。超限返回 `429`、OpenAI 风格 error 对象和 `Retry-After`。

## 管理接口

```text
POST   /api/xperts/{xpert_id}/app
GET    /api/xperts/{xpert_id}/app
PATCH  /api/xpert-apps/{app_id}
POST   /api/xpert-apps/{app_id}/deploy
GET    /api/xpert-apps/{app_id}/deployments
POST   /api/xpert-apps/{app_id}/disable
POST   /api/xpert-apps/{app_id}/share-token/rotate
GET    /api/xpert-apps/{app_id}/keys
POST   /api/xpert-apps/{app_id}/keys
DELETE /api/xpert-apps/{app_id}/keys/{key_id}
```

管理接口属于当前可信本地管理面。外网部署必须由反向代理保护 `/api/xperts` 与 `/api/xpert-apps`。

## 公开接口

```text
GET  /api/apps/{app_slug}/manifest
POST /api/v1/xpert-apps/{app_slug}/chat/completions
```

`chat/completions` 接受最多 20 条 `system/user/assistant` 消息。最后一条必须是 `user`；历史正文最多 40,000 字符，当前输入最多 20,000 字符。客户端 `system` 内容只作为不可信历史上下文，不能覆盖已发布 Xpert 的角色提示词。

非流式响应使用 `choices[0].message.content`。流式响应使用 `choices[0].delta.content`、结束 chunk 与 `[DONE]`。公开响应只转发最终输出，不暴露内部变量、工具结果、节点 trace 或完整 checkpoint。

## 运行观测

每次调用创建 `run_type=xpert_app` 的 RunRegistry 记录，metadata 仅保存 App ID、slug、固定版本、deployment revision、访问类型和凭据前缀。节点子 run 与安全 checkpoint 保持可观测，但不得保存完整 prompt、工具输出、密钥、本地路径或 embedding。

## 回退

1. 在 Studio 中重新部署历史版本。
2. 如需立即停止访问，停用 App 或轮换分享 token、撤销 API key。
3. 代码回退后重新构建容器，确认 Xpert Store 与 App Store 挂载仍存在。
