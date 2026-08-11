# V15 专业级 Coding Worker 任务卡

## 目标与基线

- 基线：合并 PR #142 后的 `fbbcfa504cbc2606146f97c8e62bf809e3a5762b`。
- 目标：在不改变 `/api/coding-worker/v1` 的供应商中立边界下，补齐专业文件工具、原子 Shell changeset、Python/TypeScript 代码智能和第二个真实 Provider。
- 完成标准：两个隔离任务分别完成 Python 与 React 的“诊断、复现失败、修改、复测、Evidence”，第三个任务排队；逐个重启 Server、Provider 与 Executor 后结果唯一且可显式恢复。

## 不可突破的边界

- 浏览器和模块只提交通用 route、目标、来源、上下文与验收；不得提交供应商名、物理路径、环境变量、remote URL、凭据或执行端点。
- Provider 不挂载 Workspace；Executor 不接收模型或模块凭据。所有写入、Shell、网络和后台服务只能经过 Tool Broker。
- Shell 审批仅对单个 operation 生效，并精确绑定脚本摘要、相对 cwd、执行模式、超时和网络范围摘要；不提供任务级 Shell 批准。
- `inspect` Shell 的文件变化全部丢弃；`mutate` 仅在 exit code 为 0 且 Workspace tree CAS 未变化时原子发布。崩溃、超限、策略外产物或 CAS 冲突不得留下部分修改。
- 网络默认关闭，Git remote/fetch/push、Docker socket、SSH、宿主路径、用户目录、任意 MCP/Skill/LSP 注入始终拒绝。
- 公共 API、SSE、日志和 Artifact 不暴露 Provider 名称、端口、原始帧、session ID、凭据或隐藏思维链。

## 顺序交付

### PR A：Tooling Foundation

1. 冻结 Shell、changeset、operation output、diagnostics 与 capability 契约。
2. 增加范围读取、glob、正则搜索、原子 unified patch、移动和批量 changeset；每个变更绑定 preimage hash。
3. 增加沙箱 Shell、按序输出、完整输出 Artifact、取消和 unknown-result 对账。
4. 固定 Pyright 与 TypeScript Language Server，提供 symbols、definition、references、hover、diagnostics，并绑定 Workspace tree hash。

### PR B：Provider Portability

1. Provider 私有契约 v2 与 Fake、OpenCode、Claude 共用 conformance suite。
2. Claude Code 固定 `2.1.89`，仅使用托管设置和 ModelMirror MCP Broker；内建文件、Shell、Web、Skill、插件、hooks、遥测和更新检查关闭。
3. route catalog 与 secret 隔离；缺少 Claude secret 只影响对应内部 route。
4. checkpoint 只允许原 Provider、兼容版本和相同 Workspace tree 恢复，不跨引擎迁移。

### PR C：Console、SDK 与加固

1. 共享 Console 展示中立 capability、计划、实时终端、changeset、diagnostics、Evidence 与精确审批。
2. 模块 SDK 只允许登记 source、context、acceptance 和通用 route allowlist。
3. 完成部署、保留期、恢复、回退、安全和真实双引擎验收文档。

每个提交不超过五个文件；每个 PR 的上一轮门禁通过后才能开始下一轮。

## 开关与回退

以下开关默认均为 `false`：

- `CODING_WORKER_V15_ENABLED`
- `CODING_WORKER_SHELL_ENABLED`
- `CODING_WORKER_CODE_INTELLIGENCE_ENABLED`
- `CODING_WORKER_CLAUDE_ENABLED`

回退只关闭对应开关并停止接收新的 V15 工作；不得删除 V14 Store、Workspace、Evidence、Agent Workspace 数据或 v13 Recovery。已运行任务必须进入明确的 `interrupted`/终态，不得自动重放副作用。

## 自动门禁

- 契约：物理路径、环境、remote、Provider 字段均被拒绝；Shell task-scope approval 被拒绝。
- 文件：路径穿越、symlink/hardlink、跨任务读取、preimage 冲突、批量失败全旧、tree CAS、二进制/超限拒绝。
- Shell：脚本/审批篡改、审批重放、环境泄漏、remote、Docker socket、网络绕过、超时、输出超限、崩溃和 unknown 对账。
- LSP：Python/TypeScript definition、references、hover、diagnostics、tree 变化失效、重启重建和跨任务隔离。
- Provider：Fake、OpenCode、Claude conformance；供应商帧不进入公共事件；Claude 内建工具零调用且凭据不出 Provider。
- 并发与恢复：两个任务并行、第三个排队；取消隔离；queue/running/approval/output/checkpoint/SSE 断线补发；逐组件重启不重复副作用。
- 回归：全部 Worker、Agent Workspace、Coding、后端、前端测试/build、Compose config、镜像安全、敏感信息和禁止产物扫描。

## 人工验收

- OpenCode 与 Claude 各完成一个真实 Python/React 失败驱动修复，必需检查通过并生成 Diff、changeset、诊断、终端与 Evidence。
- Shell formatter/codegen 仅以原子 changeset 发布；React 依赖安装和安全预览仅在精确审批后可用。
- Server、两个 Provider、两个 Executor 逐个重启后任务可显式继续且结果唯一。
- Host Snapshot 任务完成后继续使用 v13 的 apply、commit、undo 链路；V15 不在宿主仓库原地执行。

人工验收通过前不得将 V15 标记 Ready，不得合并最终发布 PR。
