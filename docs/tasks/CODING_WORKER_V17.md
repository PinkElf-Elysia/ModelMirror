# V17 真实交互内核与可执行等效认证任务卡

## 交付边界

V17 只收口两项：持久 Turn Transaction 与真实可执行 parity v2。它不升级 Worker 内 OpenCode，不增加 Agent 深度、语言/LSP、Skill/MCP/plugin 注入、remote/push、自动 Provider 切换或分布式调度。

唯一可能使用的认证表述是：

> 在已验收的受控 Python/TypeScript 仓库开发任务上，ModelMirror Coding Worker 的任务成功率与恢复能力接近 OpenCode 1.18.9。

在两轮 288 次真实运行和 Console 人工门禁全部通过前，该表述被禁止。代码、确定性测试与 Compose profile 可以作为 Experimental、默认关闭能力合并，但不能标记 Ready。

## 顺序 PR

| PR | 范围 | 分支/状态 |
| --- | --- | --- |
| A | capability 真实性、parity v2 契约、24 项公开 fixture、CLI 基座 | `codex/coding-worker-v17-harness`，Draft PR #199，HEAD `05671d7c` |
| B | `runtime_protocol=v17`、Turn Transaction、平台 Plan/Todo/Input/Compaction、重启矩阵 | `codex/coding-worker-v17-turn-transactions`，Draft PR #200，HEAD `9342e5f7` |
| C | 隔离 Native/Worker/checker/controller、终态导出、Console 权威状态、部署与认证封板 | `codex/coding-worker-v17-certification`，本 Draft PR |

每个逻辑提交不超过五个文件；三个 PR 顺序基于前一 PR head，不复用或修改脏主工作树及 V14–V16 工作树。

## PR C 自动证据

- parity runner 与 checker 使用独立 argv/进程，runner request 不含 hidden bundle locator/hash；checker request 不含目标、模型或 Provider。
- Worker 终态导出是 path-free、确定性 tar，绑定 task、tree、Artifact SHA/size 与预算摘要。
- parity profile 中 checker/controller 无网络；checker 唯一挂载密封 bundle；controller 不挂载 bundle、route key、Docker socket 或 Workspace。
- Native 与 Worker runner 都从公开 fixture 构造全新 Workspace；checker 安全解包普通文件并在隐藏检查前复核 run/task/tree/artifact/check 绑定。
- round ID 进入 seed 与 run ID；不同 round 不会命中同一个幂等 Worker task。
- Console 从任务 API读取 capability、Plan、Todo、Question、Turn 与 Evidence；Provider 原生 Plan/Todo 不再驱动业务状态。

这些项目必须以实际命令结果填写，不能用代码审查代替：

| 门禁 | 状态 |
| --- | --- |
| parity、source adapter、API、sidecar 专项 | PR C 完成前重跑并记录 |
| 全部 Coding Worker 测试 | 待最终门禁 |
| Agent Workspace、Coding 与后端全量 | 待最终门禁 |
| 前端测试、typecheck/build | Console 定向 10/10 与 production build 已通过；全量待最终门禁 |
| Compose `config --quiet` | V14 + V17 parity 临时配置已通过；最终组合待重跑 |
| 敏感信息、禁止产物与每提交 ≤5 文件 | 待最终门禁 |

## 真实认证与人工门禁

以下项目在 Draft PR 创建时仍明确未执行：

1. 同一候选、manifest、route、bundle 与 runner image 的两轮完整 144 格，共 288 次真实模型运行。
2. Worker accepted-run ≥85%、落后原版 ≤5 个百分点、每类 ≥80%，以及原版 2/3 而 Worker 0/3 禁止项。
3. 安全、原子性、跨任务隔离、重启唯一性 100%，`platform_coordination_failures`、重复副作用、未结算 operation 与孤立 approval/question 全为零。
4. token 与活动时长中位数不超过原版 1.5 倍；等待批准时间不计入活动时长。
5. OpenCode 与 Claude 各六项 plan/todo、审批恢复、用户问题、受控压缩、subtask 合并和 Provider/Server 重启真实交互门禁。
6. Console 脚本化人工验收无 P0/P1；最新 OpenCode 能力差距审计已记录且没有用固定 1.18.9 隐藏新增差距。

任何一项失败、未运行或环境不确定时，V17 继续保持 Experimental，`CODING_WORKER_V17_ENABLED=false` 与 `CODING_WORKER_PARITY_ENABLED=false`，不得宣布等效。

## 回退

关闭 V17 后，新任务回到 V16。已有 V17 任务进入 `interrupted` 并保留 Store、Workspace、Turn、Evidence、Artifact、fork 与 v13 Recovery；不得降级私有 checkpoint、自动重放工具副作用或删除用户数据。capability 的失败关闭是事实性修复，不因回退恢复虚假可用状态。
