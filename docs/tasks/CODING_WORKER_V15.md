# V15 专业级 Coding Worker 任务卡

## 目标与基线

- 实现起点：合并 PR #142 后的 `fbbcfa504cbc2606146f97c8e62bf809e3a5762b`；发布前已变基到最新 `origin/main`：`6040148de8e679e67efc4784dc0c88ea6ddf9881`。
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

## 当前进度（2026-08-11）

PR A 已作为 Draft PR #151 发布；PR B 的实现与自动门禁已完成，尚未进行真实 Provider/人工验收，也未开始 PR C：

- 契约与文件工具：`d0e955b`、`50d933e`、`29c135b`。
- Shell 执行、审批、changeset 与查询：`ad831ac`、`0a56605`、`45df707`。
- Python/TypeScript 代码智能与固定依赖：`30def7c`、`72ad76d`、`7816b9a`、`5c3ba39`。
- 默认关闭开关与 TypeScript Language Server 稳定性修正：`c7615d7`、`66ef2e2`、`bb556c2`、`2dc78e8`、`00c9637`、`ef4eac4`。
- 以上 16 个实现提交均不超过五个文件；发布目标基线为 `6040148de8e679e67efc4784dc0c88ea6ddf9881`。

PR A 实际自动验证：

- 变基前分层复跑：Worker 125 passed、1 skipped；Agent Workspace 44 passed；Coding 748 passed、10 skipped。
- 后端全量（最新主线）：2600 passed，24 skipped；5 项 Agency Worker bridge 因测试镜像缺少其编译产物而失败，已在纯 `origin/main` 基线精确复现同样 5 项失败；6 条既有框架/依赖告警。
- 前端（最新主线）：production build 成功；最终全量为 35 个测试文件、169 项测试全部通过。一次中间重复运行出现 1 项 `ChatVisualAnalysisPanel` 时序波动，单文件复跑和随后全量复跑均通过。
- 变更 Python 文件 `py_compile` 通过；Worker Compose `config --quiet` 通过。
- `git diff --check` 通过；提交文件数门禁通过。

上述结果只证明 PR A 的自动化边界，不等同于 OpenCode/Claude 双引擎真实任务、逐组件重启或 v13 Host 写回人工验收。PR A 发布后仍须按顺序完成 PR B、PR C，并在人工验收通过前保持 V15 非 Ready。

PR B 实现提交：

- Provider 私有契约 v2 与 route 固定：`59b4bf9`、`ae337ee`。
- Claude Code Provider 与 Sidecar：`01512d0`、`6544878`。
- Claude secret、网络和部署隔离：`f6bf8a5`、`23b9fc1`。
- 完成回执对账与 Fake/OpenCode/Claude conformance：`b70d0e4`、`c040d60`。
- 以上 8 个实现提交均不超过五个文件；PR B 以 PR A 的 `cdad6da` 为基线。

PR B 实际自动验证：

- Linux 无网络只读源码环境全部 Worker：142 passed、5 skipped、1 条框架告警。
- Windows 全部 Worker：132 passed、12 skipped、3 项 V14 snapshot reader 的 `mtime_ns` 漂移失败；同样 3 项已在 PR A 工作树精确复现。
- Agent Workspace：44 passed；Coding：765 passed、14 skipped。
- 后端全量：2615 passed、27 skipped、6 failed、6 warnings。5 项为 PR A 已记录的 Agency Worker 测试镜像缺少构建产物；另 1 项为当前 `modelmirror-server` 镜像 Node 20 无法直接导入 `.ts`，已在 PR A 基线用相同镜像精确复现。该结果不是全绿，两个基线问题均未在 PR B 跨范围修复。
- 前端 TypeScript 检查通过；35 个测试文件、169 项测试全部通过；production build 成功，仅有既有大 chunk 告警。
- 变更 Python 文件 `py_compile` 通过；合并 V14/V15 overlay 的 Compose `config --quiet` 通过。
- Claude 镜像使用固定包 `@anthropic-ai/claude-code@2.1.89` 及 SHA512 完整性门禁；最终镜像无网络只读探针返回 `2.1.89 (Claude Code)`，运行用户为 `65532`。
- 有效 Compose 配置确认 Claude Provider 不继承 route key、gateway base URL 或 Workspace 挂载；Provider 只接内部网络和受控 socket/secret，独立代理只允许 `api.anthropic.com:443`。

上述 PR B 证据证明契约、隔离、恢复对账和镜像构造，不等同于使用真实 Anthropic 凭据完成任务。真实 OpenCode/Claude 双任务、逐组件重启和 v13 Host 写回仍属于 PR C 后的人工验收，完成前 V15 保持非 Ready。

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
