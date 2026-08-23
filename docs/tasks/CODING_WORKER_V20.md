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

## 8. PR B 实施边界

- `HarnessSupervisor` 与 `HarnessDriver` 已使用不同生产对象注入；Service 的健康、generation、capability 与 descriptor 查询只经过 Supervisor，会话、turn、interrupt、checkpoint 与 close 只经过 Driver。
- OpenCode 1.18.9 与 Claude Code 2.1.89 显式报告固定实现版本、无密钥配置摘要、`broker_only` 和真实的 `session_resume`；安全 descriptor 由 sidecar generation 绑定。
- `CODING_WORKER_HARNESS_V20_ENABLED=false` 默认关闭。只有新任务在创建时冻结完整 descriptor，且调度时再次核对 route、Schema、sidecar/controller generation 与 binding 后，才进入 V20 事件内核。
- Provider-v4 事件通过相关性内核验证 session、turn 与单调 sequence 后，仍写入完全相同的持久投影；影子测试只消费同一记录帧，不执行第二次工具副作用。
- 关闭开关会将非终态 V20 任务置为 `interrupted` 并禁止恢复；历史任务不带 V20 标记，沿用原 Provider-v4 checkpoint，既不迁移也不降级。

PR B 自动门禁通过前不能进入 PR C；本阶段仍未调用真实模型，不能使用 V20 最终交付结论。

PR B 最终候选自动证据：Coding Worker `430 passed, 5 skipped`；后端全量 `4042 passed, 29 skipped`；前端 `99 files / 534 tests` 与 production build 通过；Compose 静态展开、V18 compile、Fake smoke、`git diff --check` 通过，Fake smoke 摘要仍为 `472b88ae9de93f3816de84bc40d07e7c192ec82c4eca6cb67ef2f56dc60a1df3`。首次并行运行前端时有一项 Skill Creator 时序测试失败；该单文件随即 `12/12` 通过，并在后端结束后的独立完整复跑中 `534/534` 通过，未修改该模块源码。后端首次全量因新工作树尚未生成 orchestration worker 产物而在早期失败；按锁文件构建运行时后，从零全量复跑通过。

收口证伪另发现并修复：只重核选中槽会遗漏同 route 其他冻结槽的 generation 漂移；Server 重启后的 controller generation 变化会让 V20 任务永久无法恢复；协议违例会落入通用 `worker_failed`。最终行为改为全 route descriptor/capability 对账，仅在用户显式 resume 且 descriptor 语义完全兼容时用现有加密 capability row 做 CAS 换代，并将协议失败归因为 `harness_protocol_invalid`。本阶段未运行四次真实任务；该额度仍需另行授权。

## 9. PR C 实施边界与阶段证据

- ACP v1.19 与 Codex App Server 0.149.0 adapter 只消费官方 Schema 合法帧并归一到同一生命周期核；原始 reasoning/供应商帧不进入公共事件。
- ACP 只接受部署固定的 loopback Broker MCP；Codex 原生命令、文件审批、进程、Web、Skill、插件、认证、配置与任意 MCP 接口在创建 operation 前拒绝。Codex 工具所有权保持 `unknown`，生产能力不可用。
- evaluation loader 仅在对应显式开关开启时动态导入单一供应商模块。Server 在 loader、sidecar、Schema 与两个 adapter 均不可导入时仍可启动。
- 两个独立镜像固定基础镜像 digest、ACP wheel、Codex 包装包和 Linux x64 原生包完整性；只复制协议最小文件闭包，以 UID/GID 65532、只读文件系统和内部网络运行，不包含 Store、Service、Workspace、另一供应商 adapter、Docker socket 或模型凭据。
- 许可证、复用说明与 CycloneDX 清单已纳入仓库；`codex-acp` 仅记录为未打包、未执行的共同子集映射 oracle。

阶段专项证据：最终供应链、标准 Driver、隔离与架构组合门禁 `39 passed`；在线 attestation 完整闭包 `93 passed`；两套镜像构建、包/Schema/运行时版本校验及无网络只读准入烟测通过。镜像实测发现并修复了源码态 `server.coding_worker` 包名无法在容器中加载、根包隐式拉入 Store/crypto、Pydantic 传递依赖漂移、Codex 拒绝面二进制残留和 health 未核验完整 manifest 等问题。

PR C 最终自动证据：Coding Worker `463 passed, 5 skipped`；Agent Workspace、Coding Runtime 与 Project Host `441 passed, 9 skipped`；rebase 到包含 PR #261 的最新主线后，后端独立全量 `4101 passed, 29 skipped`；前端独立全量 `100 files / 551 tests` 与 production build 通过。主 Worker 与 evaluation Compose 静态展开、V18 compile、Fake smoke、`git diff --check`、敏感信息和禁止产物扫描均通过，Fake smoke 仍为 8 条记录、四类别齐全，摘要 `472b88ae9de93f3816de84bc40d07e7c192ec82c4eca6cb67ef2f56dc60a1df3`。

rebase 后首次把前后端全量并行运行时，既有 Chat/OCR 交互测试未展开面板，模型路由 p95 微基准以 `10.165ms` 略过 `10ms` 阈值。两者与 PR C Diff 无交集；停止并发后，OCR 单文件 `5/5` 通过，路由微基准连续 `3/3` 通过，随后前端和后端各自独立全量均通过。未修改这两个模块、未放宽阈值，也未用单项绿测替代最终全量证据。

本阶段没有调用真实模型、没有运行另行授权的四次真实任务、校准或认证。因此当前只证明自动工程门禁与 evaluation conformance/准入边界完成；不能使用 V20 最终交付结论，也不形成任何能力提升、接近或等效表述。
