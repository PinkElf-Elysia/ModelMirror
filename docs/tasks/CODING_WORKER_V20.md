# V20 Harness Protocol Kernel 与受控标准 Driver

## 1. 单一目标

- 将 V19 的 Provider-v4 兼容接口收敛为供应商中立、可恢复、可关联的 Harness Protocol Kernel。
- 现有 OpenCode 1.18.9 与 Claude Code 2.1.89 只可在证明 `broker_only` 后进入生产 V20 路由。
- ACP v1.19 与 Codex App Server 0.149.0 只进入独立 evaluation profile，不注册生产路由。
- 本轮不新增公共入口、Agent、语言、工具、数据库结构，不运行校准或认证，也不形成能力接近或等效结论。

## 2. 基线与范围

| 项目 | 状态 |
| --- | --- |
| V19 最终 PR #256 | 已合并 |
| V20 PR A 开工基线 | `dbe695ea`，随后无冲突纳入主线 PR #257、#258 |
| 主检出 | 保持不动；V20 使用独立 `C:\tmp\modelmirror-coding-v20-*` 工作树 |
| 公共 API、TaskSpec、SSE、数据库、runtime protocol、v13 写回 | 保持不变 |
| 历史 Provider-v4 checkpoint | 原样保留，不迁移、不重写 |

## 3. 所有权

| 领域 | 唯一所有者 |
| --- | --- |
| sidecar 健康、槽位、generation、协议/Schema 摘要、路由可用性和安全证明 | `HarnessSupervisor` |
| session、turn、steer、interrupt、checkpoint 与 close | `HarnessDriver` |
| 任务状态、审批、问题、Turn Transaction、Evidence 与 Acceptance | `TaskControlPlane` |
| 工具策略、operation ID、未知结果对账与副作用准入 | Tool Broker |
| Shell、服务、进程和 LSP 执行 | `ExecutionBackend` |
| Parity、Harbor、ACP/Codex conformance | `EvaluationAdapter` |

任何 `harness_native` 或 `unknown` 工具所有权都不得进入生产 route catalog。供应商原始请求只能被翻译为 Broker 意图，不能直接改变任务状态或执行副作用。

## 4. 分组交付

1. PR A：协议引用、事件信封、双向请求/响应、能力与持久性模型、Supervisor/Driver 端口分离、固定协议帧及生命周期拒绝门禁；不切换生产路径。
2. PR B：拆分 legacy Supervisor/Driver，迁移 OpenCode/Claude，增加默认关闭的 `CODING_WORKER_HARNESS_V20_ENABLED`，并对旧/新 translator 做单执行影子对照。
3. PR C：ACP/Codex evaluation-only adapter、生成 Schema、固定供应链资料、显式拒绝原生副作用接口和生产准入封板。

每个逻辑代码提交最多五个文件；生成 Schema、固定测试帧和许可证/SBOM 作为纯数据例外。

## 5. PR A 协议证据

- ACP 固定 `schema-v1.19.0@a213df5240048f96d2b23f644984bb20c188a234`；回放使用稳定的 `session/resume` 与 `session/close`，不以 `session/load` 冒充 resume。
- Codex 固定 npm `@openai/codex@0.149.0`，integrity 与该版本生成的稳定 JSON Schema bundle SHA-256 均写入固定帧夹具。
- Kernel 拒绝错误 session/turn、旧 generation、事件乱序、重复事件、重复 request reply、跨任务消息和不兼容 checkpoint。
- API、SDK、Control Plane、Projection 与 Tool Broker 不得导入 ACP、Codex 或具体 Driver；production profile 在评测模块不可导入时仍须启动。

## 6. 验收与回退

- 每组运行 Coding Worker、Agent Workspace、Coding Runtime、Project Host 专项和全量后端；前端 typecheck/test/build、Compose config、安全扫描与 `git diff --check` 全部通过后才可合并。
- PR B/C 额外证明两槽调度、第三任务排队、重启唯一性、checkpoint 兼容、公共 JSON/SSE 与写回结果不变。
- 回退不需要数据回滚：关闭 V20/evaluation 开关并恢复上一镜像。已有 V20 状态、Workspace、Evidence、operation 和 checkpoint 保留，不降级、不自动重放。
- 四次真实任务仅在自动门禁通过且另行授权额度后执行；平台缺陷造成的付费复跑不自动扩额。

V20 唯一允许的最终结论是：“Harness Protocol Kernel 与标准 Driver 准入边界完成，现有 OpenCode/Claude 已通过该内核运行。”

## 7. PR A 实施证据

- 协议、架构与 attestation 收口：`106 passed`。
- Coding Worker 全专项：`420 passed, 5 skipped`。
- Agent Workspace、Coding Runtime 与 Project Host：`439 passed, 9 skipped`。
- 最终候选后端全量：`4032 passed, 29 skipped`。
- 前端：typecheck 通过；`99 files / 534 tests` 通过；production build 通过。
- Compose config、V18 compile、Fake smoke、`git diff --check`、敏感信息和禁止产物扫描通过；Fake smoke 摘要保持 `472b88ae9de93f3816de84bc40d07e7c192ec82c4eca6cb67ef2f56dc60a1df3`。

证伪收口发现并修复了两项不能留给后续轮次的基础缺口：新增协议文件最初未进入 V18 在线 attestation 代码摘要；事件与请求最初按裸 ID 全局去重，两个独立任务使用相同供应商起始 ID 时会互相误判为重放。最终实现按 task、route、slot、binding、generation、session、turn、kind 与 ID 的完整私有引用隔离去重。

PR A 未切换生产路径，未调用真实模型，未运行 calibration/parity/certification。ACP/Codex 仍只存在于固定协议帧和后续 evaluation 设计中；OpenCode/Claude 生产迁移属于 PR B，因而 PR A 不能使用 V20 最终交付结论。
