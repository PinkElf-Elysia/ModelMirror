# V14 通用 Coding Worker 基座

## 目标与边界

V14 将 Agent Workspace 的持久任务语义与 Coding Runtime 的隔离工作区、Diff、验证、恢复及 v13 写回收敛到供应商中立的 `coding_worker` 内核。下游模块只提交不透明来源、目标、上下文和冻结验收合同；不得提交宿主路径、环境变量、供应商名、remote URL、凭据或原始执行端点。

V14 不在宿主仓库原地执行，不开放 Git remote/push、自动 PR、任意 Skill/MCP 注入、长期凭据或领域专属逻辑。宿主写回继续只走 v13 Project Host 确认链。

## 威胁模型

- 任务工作区、进程、事件、审批和 Artifact 必须按 `task_id` 隔离；跨任务 ID、路径穿越、符号链接和硬链接失败关闭。
- Worker 以固定非 root 身份运行，不挂载 Docker socket、用户目录、SSH、Server 密钥或正式数据根。
- 网络默认关闭。网络、依赖安装、后台服务和非冻结命令必须持有绑定任务、用途、范围和 TTL 的能力租约。
- 每个副作用具有稳定 operation ID。未知结果只允许 reconcile，禁止以新 ID 或盲重放继续。
- Server 只持有不透明来源和 Artifact ID。物理宿主路径只存在于 v13 Windows Helper。
- 不保存隐藏思维链，只保存公开计划、消息、工具摘要、checkpoint、审批和验收证据；敏感结构字段使用 Coding Recovery 同级 Fernet 持久化。

## 交付门禁

1. `CODING_WORKER_V14_ENABLED` 默认 `false`。
2. 两个任务可并行，第三个持久排队；取消一个不得终止另一个的进程。
3. 重启把不确定的执行中任务标记为 `interrupted`，不得恢复旧进程或自动重放。
4. Agent 停止不代表完成；只有冻结验收合同的全部必需检查及 Artifact 都有绑定当前 Workspace tree hash 的通过证据，状态才可进入 `completed`。
5. 数据默认保留 604800 秒；pin 后不自动过期，用户可立即删除。

## 回退

关闭 `CODING_WORKER_V14_ENABLED` 后，新任务继续走 legacy；不得删除 Worker Store、Workspace、Coding Recovery 或 Agent Workspace 数据。已有活动 legacy 会话不迁移。

## PR A 当前接口

`/api/coding-worker/v1` 默认关闭。Kernel 阶段提供任务幂等创建、状态、SSE 事件补发、消息、暂停/继续/取消/pin，以及用 opaque `entry_id` 读取的 Workspace tree、文本预览与 Diff。`origin` 始终由 Server 写入；模型路由必须命中 `CODING_WORKER_MODEL_ROUTES`。

PR A 使用 Fake Provider 验证状态机，模型停止后只进入 `testing` 并以 `acceptance_runner_pending` 阻断，绝不伪造完成。OpenCode、Tool Broker、Harness Runner 和 legacy 转发在后续顺序 PR 接入；未配置真实 Provider 时即使误开开关，API 也返回 503，而不会回退到未隔离执行。
