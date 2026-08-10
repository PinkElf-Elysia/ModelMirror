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

## 顺序 PR 状态

| PR | 范围 | 当前状态 |
| --- | --- | --- |
| PR A `#122` | 契约、加密任务 Store、事件补发、Workspace Broker、双槽调度与 Fake Provider | 已完成并作为后续堆叠基线 |
| PR B `#125` | OpenCode Provider、Tool Broker、审批/网络/服务租约、Evidence Ledger、checkpoint 恢复 | 已完成并作为 PR C 基线 |
| PR C `#130` | 模块 SDK、共享 Console、三类来源、冻结验收、v13 写回桥、部署与文档 | Draft；自动门禁尚未全部完成 |

每个实现提交继续限制在不超过五个文件。PR C 不迁移已有活动 legacy 会话；只有新会话在开关开启且 Worker 可用时进入 V14。

## 当前公共能力

`/api/coding-worker/v1` 默认关闭，提供：

- 幂等创建与查询任务、SSE 事件补发、消息 steering、审批、暂停、继续、取消、pin 和删除；
- 使用 opaque `entry_id` 的 Workspace tree、文本预览、Diff、Evidence、Artifact 与安全预览；
- `builtin`、`manifest`、`host_snapshot` 三种受控来源；来源适配器和冻结检查只能由 Server 模块注册；
- 两个固定任务槽；第三个任务持久排队，重启后的运行中任务进入 `interrupted`；
- 供应商中立的 Provider 契约。首个真实实现固定为 OpenCode 1.18.9，ACP 保留回退，公共 API 不暴露两者；
- 通过 Tool Broker 执行文件、命令、服务与网络动作。Executor 默认只在内部工具网络，只有带批准租约且启用专用 profile 时才经 egress proxy 访问允许域名；
- 后端冻结的 `python-compile`、`python-pytest`、`react-test`、`react-build` 检查。调用方只能选择公开 ID，不能改写 argv 或降低验收；
- `/coding` 与 `/agents/workbench` 共用 Worker Console；Coding 场景中的已完成 `host_snapshot` 任务可显式转入 v13 apply/commit/undo/publish 确认链。

## 当前自动验证证据

- V14 Worker 17 个专项文件：`96 passed`；
- Project Host API：`19 passed`；
- 全部 Coding + Agent Workspace：`762 passed, 9 skipped`；
- 后端全量：`2131 passed, 21 skipped, 1 failed`。唯一失败是现有 `modelmirror-server`
  测试镜像内 Node `20.20.2` 无法直接导入 TypeScript；该节点及数据未被 PR C 修改，使用本机
  Node `24.18.0` 单独复跑为 `1 passed`，因此记录为测试镜像环境门禁而非绿色全量；
- 前端：`29` 个文件、`134 passed`；
- 前端 production build 通过，`CodingPage` gzip `37.60 kB`；
- V14 Compose 组合 `config --quiet` 与部署静态测试通过；
- 真实 Worker 镜像中的 OpenCode `1.18.9` 已在非 root、只读根、无网络、无宿主端口的
  临时容器中完成 Basic Auth `serve` 与 `/global/health` 冒烟；这不等同于带真实模型的修复循环；
- 分支已推送至 Draft PR `#130`。

这些结果不等同于完整发布验收。后端全量仍需在支持 TypeScript strip 的正式 Node 镜像复跑；真实 OpenCode 模型任务循环、真实 Windows Helper、共享栈重建和用户项目写回仍须分别完成并记录。

## 发布与人工验收门禁

1. 运行新增专项、全部 Agent Workspace、全部 Coding、后端全量、前端测试/build、Compose config、敏感信息与禁止产物扫描。
2. 在独立临时环境验证真实 OpenCode 1.18.9、两个任务并行、第三个排队、失败后自动修复复测、SSE 补发与逐个重启。
3. 取得用户确认的共享栈独占窗口后，重新 fetch 最新主线、核对堆叠 PR 和环境变量，再重建；不得停止仍在运行的独立预览器。
4. 使用用户明确选择的测试项目验证 Host Snapshot → Worker → v13 apply/commit/undo；真实写入前再次确认项目、分支与 Diff。
5. 只有以上证据齐全且人工验收通过，PR C 才可由 Draft 转为 Ready。
