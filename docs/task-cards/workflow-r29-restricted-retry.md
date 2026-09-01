# 工作流 R2.9 收尾：持久受限重试

## 基线与目标

- 基线：`origin/main@c6162053`（PR #341 合并提交）。
- 分支：`codex/workflow-r29-restricted-retry`，独立 worktree。
- 单一目标：仅为 `http_request` V2、`data_table_query` 与 `knowledge_retrieval` V2 增加可恢复的瞬时故障重试。
- 不新增节点、Handle 或公开重试 API；不扩大 Planner，也不对写入、模型、Agent、MCP 或子流程调用提供自动重试。

## 范围与安全边界

- 增加 `retryMode=none|transient`、`maxAttempts=2|3`、NodeRetryPolicy 和配置感知的 `effective_can_wait`。
- 默认开关 `WORKFLOW_NODE_RETRIES_ENABLED=false`；关闭时可编辑和静态发布，但激活、私有 Xpert 发布及实际运行失败关闭。
- HTTP 仅允许 V2 GET 且无 Body；数据表只识别真实 SQLite `BUSY/LOCKED` 错误码；知识检索只允许本地 fulltext 或本地 hash embedding 且无远程 rerank。
- DNS、SSRF、TLS、凭据、权限、配置、Schema、解析、响应超限、取消和未知异常均不重试。
- HTTP/表单/RSS/邮件入口、可调用子流程目标、公共 App、Evaluation、Evolution 和 Planner 禁止重试配置。

## 持久化与恢复

- 重试只在失败调用没有产生正常输出后进入 `wait_kind=node_retry`。
- continuation 仅保存节点身份、attempt、固定安全错误码、resume_at 和可选目标指纹，不保存请求、响应、表记录、检索正文或凭据。
- continuation 顶层使用严格白名单，并与最近一次 `node_retry_scheduled` 安全事件核对；未知状态、attempt 回退或 wait 身份漂移均失败关闭。
- 工作流输入和变量声明可在恢复时重建；前序节点结果、触发正文、输出状态和运行时中间件状态不会跨等待保存。静态预检同时检查并行支路可能停在的每个队列切点。
- `list_due_waits()` 是 timer 与 node_retry 的唯一到期来源；`claim_due_wait()` 在一个 Store 锁内完成到期复核和 lease 领取。
- 领取、延期、输出和终态写入均使用 lease token fencing；真实只读调用及 Xpert 后处理开始前再次核对当前 owner，旧 worker 不得在被接管后启动新副作用。
- 可靠性边界仅为重试等待状态已原子持久化后的恢复；不宣称外部调用中途崩溃 exactly-once。

## 变更规模说明

- 本任务会超过默认 5 个文件：NodeContract、静态验证、部署激活、执行 Store、协调器/运行器、前端配置/观测、帮助中心和测试必须同步，否则会形成不一致的安全边界。
- 保持一次只实现上述单一产品目标；不做邻近重构、依赖升级或能力矩阵状态扩张。

## 验收

- 针对性覆盖合同、精确资格、2/3 次退避、429、耗尽与错误出口、Store 原子 claim、重启/重复协调器/取消竞态、并行等待切点、lease 接管和哨兵不泄漏。
- 回归 Scheduler V2、timer、审批、Handoff、失败处置、子流程、Xpert 与四类外部入口。
- 前端覆盖资格提示、等待倒计时、恢复事件与帮助中心搜索；完成构建和隔离预览器三轮反证验收。
- 最终运行后端全量、前端全量、`git diff --check`，并复核完整 Diff 与 Git 状态。

## 回退

先关闭 `WORKFLOW_NODE_RETRIES_ENABLED` 并停止新执行，完成或取消全部 `node_retry` 等待，再停用含重试配置的发布版本；旧运行器不得接管该 continuation。草稿、版本、执行记录和安全事件保留。
