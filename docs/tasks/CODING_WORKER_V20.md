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

PR C 最终自动证据：Coding Worker `463 passed, 5 skipped`；Agent Workspace、Coding Runtime 与 Project Host `441 passed, 9 skipped`；最终再次 rebase 到包含 PR #261、#262、#263 的主线后，后端独立全量 `4183 passed, 29 skipped`；前端独立全量 `104 files / 570 tests` 与 production build 通过。主 Worker 与 evaluation Compose 静态展开、V18 compile、Fake smoke、`git diff --check`、敏感信息和禁止产物扫描均通过，Fake smoke 仍为 8 条记录、四类别齐全，摘要 `472b88ae9de93f3816de84bc40d07e7c192ec82c4eca6cb67ef2f56dc60a1df3`。

在 #261 rebase 阶段首次把前后端全量并行运行时，既有 Chat/OCR 交互测试未展开面板，模型路由 p95 微基准以 `10.165ms` 略过 `10ms` 阈值。两者与 PR C Diff 无交集；停止并发后，OCR 单文件 `5/5` 通过，路由微基准连续 `3/3` 通过，随后前端和后端各自独立全量均通过。最终 #262/#263 基线上再次独立全量通过；未修改这两个模块、未放宽阈值，也未用单项绿测替代最终全量证据。

本阶段没有调用真实模型、没有运行另行授权的四次真实任务、校准或认证。因此当前只证明自动工程门禁与 evaluation conformance/准入边界完成；不能使用 V20 最终交付结论，也不形成任何能力提升、接近或等效表述。

## 10. 合并后证伪修复状态

严格反例审查发现，原自动门禁仍允许 pending request 在 turn 完成、interrupt 或 resume 时成为孤儿；失败事件还会提前消耗 Driver sequence，JSON-RPC 数字与字符串 ID 会发生相关性碰撞。修复后，一个 turn 同时只能存在一个未结算请求，退出前必须精确结算；sequence 和 pending 状态仅在生命周期核接受后提交，供应商 ID 同时绑定原始类型。ACP permission 现在接受规范 `_meta`，但只允许最多 16 个结构完整、ID 唯一的官方类型选项；回复必须选择原请求实际提供的 option，cancel 必须先结算全部 pending permission。

镜像级审查同时推翻了“health 即 Driver 可用”的假设：当前 evaluation sidecar 仅实现鉴权 health 与静态 adapter 装载，尚未承载 ACP/Codex 实时协议传输。sidecar 现会校验固定命令和镜像内实际 Schema 摘要，并明确返回 `available=false`、`reason=protocol_transport_unavailable`、`image_attestation=external_required`；Compose 健康检查据此 fail-closed。只有外部控制器完成真实镜像身份验证且实现协议传输后，evaluation profile 才能转为可用。

证伪修复最终重放到包含 #269 的主线 `bdd1dcda`，range-diff 与原四个逻辑提交一致。后端全量在紧邻主线 `e6a59117` 为 `4219 passed, 29 skipped`；主线继续合入 #267/#268 后，覆盖 Coding Worker、Coding Runtime、Agent Workspace 与 Project Host 的回归为 `962 passed, 14 skipped`，#269 仅修改前端竞态。最终基线专项为 `52 passed`，前端完整套件为 `104 files / 572 tests`，production build 通过。两套 evaluation 镜像重新构建并确认 UID/GID `65532:65532`，ACP 与 Codex Schema 摘要分别为 `998c6427fa78bf6cd39f442bf164c6172234ebdf1c04298af57c40fa716ce267` 和 `02a4c63a638fdae4a5f6c3ad32a41a377b642c66f3abc84f6fc47c7f3d6074df`。主/评测 Compose 静态展开、V18 compile 与 Fake smoke 通过，Fake smoke 摘要仍为 `472b88ae9de93f3816de84bc40d07e7c192ec82c4eca6cb67ef2f56dc60a1df3`。

结论仍为 Experimental：未调用真实模型、未运行四次真实任务、校准或认证；ACP/Codex evaluation profile 尚不可用，V20 最终交付结论仍不得使用。

## 11. R2 中立 Driver 边界收口

R2 不启用 ACP/Codex transport，也不调用真实模型。范围只覆盖生产 Harness 边界：

- 新增供应商中立的 open request、session、event、capability 与 checkpoint 私有载体；公共 API、SSE、数据库和 runtime protocol 不变。
- `LegacyHarnessDriver` 独占 Harness 与 Provider-v4 的双向转换，并在 V20 binding 存在时执行 session/turn/sequence 相关性校验；Service 不再导入 Provider-v4 或具体 translator。
- `HarnessSupervisor` 与 `HarnessDriver` 改为必需的独立注入，测试和生产组装不再依赖“同一对象碰巧实现两个端口”。
- 历史 checkpoint JSON 结构保持不变；V14–V19 任务继续通过兼容适配器恢复，不迁移、不重写。
- Adapter 与 `ProviderSidecarClientPool` 均以 `task_id + private session_id` 绑定会话，防止两个隔离槽使用相同私有 ID 时发生覆盖或串路由。

R2 的结论只允许表述为“中立 HarnessDriver 边界完成收口”。真实 OpenCode/Claude 任务、ACP/Codex 实时 transport、校准、认证和任何等效表述仍不属于本轮。

## 12. R3 真实烟测复盘与收口决定

R3 在自动门禁通过后尝试四项真实任务，但按停止条件未形成一次完整矩阵。后续不再通过增加付费重试或针对单次模型行为继续追加补丁。

### 12.1 冻结事实

| 指标 | 结果 |
| --- | ---: |
| Smoke Journal | 14 |
| 已创建任务 | 41 |
| 完成 | 2 |
| 失败 | 11 |
| budget_limited | 1 |
| fail-closed 后取消 | 27 |
| 计划但未创建 | 15 |
| 完整四项矩阵 | 0 |
| Task 4 实际创建 | 0 |

Controller 先并行创建 Task 1/3，再创建排队的 Task 2；只有前三项全部完成后才创建 Task 4。因此该矩阵适合作为最终门禁，不适合作为故障定位工具。任一前置失败都会取消其余任务并遮蔽 Task 4 覆盖。

### 12.2 十四次运行台账

planned 表示未创建，不消耗该任务额度。Schema v1 的 cleanup_success 只证明清理成功；它覆盖了主失败，不能作为运行成功或精确归因证据。

| Journal | Schema | Task 1 / 2 / 3 / 4 | 可证明主结论 |
| --- | --- | --- | --- |
| final-01 | v1 | cancelled / cancelled / failed / planned | 主失败不可恢复归因 |
| final-02 | v1 | failed / cancelled / cancelled / planned | 主失败不可恢复归因 |
| openrouter-01 | v1 | failed / planned / failed / planned | 主失败不可恢复归因 |
| openrouter-02 | v1 | failed / cancelled / failed / planned | 主失败不可恢复归因 |
| openrouter-03 | v1 | cancelled / cancelled / cancelled / planned | 主失败不可恢复归因 |
| openrouter-04 | v1 | cancelled / cancelled / cancelled / planned | 主失败不可恢复归因 |
| openrouter-05 | v1 | completed / failed / cancelled / planned | 仅 Task 1 单项完成 |
| openrouter-06 | v1 | cancelled / failed / failed / planned | 主失败不可恢复归因 |
| r3-01 | v2 | budget_limited / cancelled / completed / planned | OpenCode 流停滞；仅 Claude Python 完成 |
| streamfix-01 | v2 | cancelled / cancelled / cancelled / planned | unexpected_approval_intent |
| cleanupfix-01 | v2 | cancelled / cancelled / cancelled / planned | unexpected_approval_intent |
| racefix-01 | v2 | failed / cancelled / cancelled / planned | Driver transport failure 与越界审批并存 |
| cancelracefix-01 | v2 | failed / cancelled / cancelled / planned | 隔离栈短占位 Key，运行无效 |
| cancelracefix-02 | v2 | cancelled / cancelled / cancelled / planned | 无必要的依赖安装 Shell 意图 |

最后一次 Task 1 的冻结 python -m pytest -q 已实际运行并得到 1 failed, 2 passed，证明 pytest 与异步插件可用；随后请求 pip install pytest-asyncio 属于 Agent 策略偏离，不是夹具缺依赖。

### 12.3 归因与保留范围

保留的产品不变量修复：

- Provider stream 由精确 session/turn 所有，interrupt/close 后确定回收；迟到清理不能 fence 新 turn。
- stall、认证、协议、Broker 与 Executor 错误使用稳定中立分类，不再全部折叠为通用失败。
- 公共事件使用供应商中立调用 ID，不暴露供应商原始工具 ID。
- 取消先持久化终态，再中止 Provider；未决审批与剩余租约原子结算。
- 外层取消必须回收同 tick 完成的 Driver 异常，禁止 cancelled -> failed 和未观察异常。
- mutate 副作用结果无法证明时保持 operation_result_unknown，只允许精确 reconcile。

对应离线回归包括：

- test_v20_driver_replaces_supplier_tool_ids_before_public_events
- test_stale_stream_cleanup_cannot_interrupt_the_next_harness_turn
- test_harness_interrupt_deterministically_closes_nested_provider_stream
- test_v20_stalled_harness_stream_is_transport_failure_not_budget
- test_user_cancel_wins_over_concurrent_provider_abort_frame
- test_cancel_atomically_settles_pending_approval_and_revokes_lease
- test_outer_cancellation_retrieves_driver_exception_that_finished_same_tick
- test_mcp_rejects_empty_shell_arguments_before_rpc
- test_closing_session_quiesces_sidecar_stream_before_reusing_slot

不作为根因修复保留的内容：

- 不依靠提示词禁止 Shell 浏览、搜索或安装依赖。
- 不依靠包管理器字符串正则证明 Shell 策略完备。
- 不为某次采样中的新命令继续追加命令特例。

现有相对路径、宿主/private runtime 路径拒绝仍属于 Broker 安全边界。评测场景的冻结命令白名单必须在审批创建前由 EvaluationAdapter/Broker 共同执行；在该能力完成前，外置 Controller 的事后 fail-closed 只能算评测保护，不能算生产策略证明。

### 12.4 交付状态与重新进入条件

V20 当前保持 Experimental，不得使用本任务卡第 6 节的最终结论，不得据此进入 V21。当前只允许表述：

> Harness Protocol Kernel 的若干真实生命周期与副作用不变量已修复；标准 Driver 的完整生产闭环尚未通过。

零额度收口中，空参数 fail-closed、冻结命令匹配、Provider RPC/Runtime 专项和一次完整 Coding Worker 套件通过；完整套件此前仍曾在压力下复现取消后槽位复用失败。单次 `611 passed / 5 skipped` 不能覆盖该历史失败，因此不作为稳定性封板证据。

sidecar 现会在 session close 前精确回收该 session 的服务端 message handler；这修复了“Provider 要求流先静默”时的可证问题，但不宣称已解释或消除全部压力竞态。

重新进入真实验证前必须同时满足：

1. 用已捕获的真实失败帧完成无模型离线 replay，覆盖空工具参数、断流、迟到事件、取消竞态和越界命令。
2. 每个 Driver 先独立、顺序完成资格探针；最终四项矩阵只用于封板，不再承担调试。
3. 冻结命令策略在审批创建前生效，而不是只存在于提示词和外置 Controller。
4. 候选迁移到最新主线并重新执行受影响自动门禁。

R3 不新增公共 API、数据库或运行协议，不调用额外模型。回退方式仍为关闭 V20 开关并恢复上一镜像；已有任务、Workspace、Evidence、operation 和 checkpoint 不删除、不降级、不自动重放。

### 12.5 R7 脱敏回放与 replace 边界修正

授权冒烟在 Task 3 请求非冻结 Shell 时由外置 Controller 以 `unexpected_approval_intent` 停止；Task 4 未创建，完整矩阵仍为零。保留 Store 的无网络只读回放证明：冻结 pytest 与只读文件工具均形成持久 operation，但两次 `apply_changeset` 和一次兼容 `write_file` 失败均发生在权威 operation 创建之前。

可证根因位于 `replace` 适配边界：Claude Provider 明确不挂载 Workspace，MCP adapter 却按 Provider 私有 state cwd 读取目标文件并把 replace 展开为完整 write。因此任何 replace 都可能在到达 Tool Broker 前以文件不存在失败。修正后，MCP 只转发已绑定 preimage 的 replace 意图，由 `ChangesetEngine` 在权威 Workspace 内校验路径、文件摘要、UTF-8 与唯一片段，再以既有原子 changeset 事务发布；歧义片段保持全旧并持久化 `tool_input_invalid`。未扩大 Shell 白名单、未新增工具、公共 API、数据库或运行协议。

离线证据：真实失败形状的回放测试修正前稳定复现 Provider cwd 下 `FileNotFoundError`；修正后 replace 成功/歧义失败两项通过，Broker/Tool Broker/Shell/Session Controls 为 `81 passed`，OpenCode/Claude/Provider RPC/Service 为 `153 passed`，最终精确展开的完整 Coding Worker 套件为 `616 passed, 5 skipped`。兼容 `write_file` 的原始无效参数未被持久化，现有证据不能独立重建该次供应商输入；因此本修复只关闭已证明的 replace 结构性缺口，不将其外推为完整四项烟测通过。

该候选仍为 Experimental，未追加真实模型额度。进入下一次付费验证前仍需迁移到最新主线、重建候选镜像并重跑完整自动门禁；不得通过放宽冻结命令掩盖 Agent 偏离。

### 12.6 R11/R12 定向收口与提交前门禁

本次只收口已证明的 Project Source 并发阻塞、Claude 审批重启和既有生命周期/副作用边界，不恢复四项矩阵。原候选基于 `d4bd6b8d`；实现 Diff 以二进制 patch 固化，SHA-256 为 `3e78fd2341e51e9608894e22a0d01351215498764a9fc63cfbd3ce658596c06b`。该实现被拆为 11 个、每个不超过 5 个文件的逻辑提交后，先迁移到主线 `cc49136c`；提交前因主线继续前进，再重放到 `8c066b79`，重放时 14 个候选提交的 range-diff 全部为 `=`。最终提交随后只补入该基线上的验证计数与竞态收口说明。另以 2 文件测试提交加入脱敏 Claude 审批重启回放，未改变产品行为。

Project Source 的隔离资格探针连续为 `5.129s / 4.960s / 3.231s`；7 个来源均 `available`，四个 exact source 均正确绑定，未复现 `BrokenPipe`。实现仅将独立来源检查放入有界 4-worker executor，并保持清单顺序、精确 revision 和安全错误语义。

经明确授权只执行了一次付费 Claude 任务。任务在审批等待点重启 Provider 后唯一恢复并完成：Evidence `1/1`、operation `10`、未结算 operation `0`、重复副作用 `0`、孤立交互 `0`、公共泄漏 `0`。原始 journal 曾因控制器错误要求 `turn_resumed` 晚于 `capability_changed` 而给出 `post_restart_turn_resume_missing` 假阴性；脱敏回放现在以重启边界为权威，固定验证 `turn_resumed` 与 `capability_changed` 均发生在边界之后，并允许二者的合法投影顺序。该证据只关闭审批重启的已知判断缺口，不代表四项真实矩阵通过。

R12 最终自动门禁：

- PR 首轮 CI 在 `test_disabled_slot_never_runs_mixed_route_and_parks_bound_history` 暴露真实竞态：取消返回时 Driver 与 RPC client 已清除 session，但 Provider sidecar 尚未完成 close，调度器提前复用槽位，下一任务可能以 `harness_protocol_invalid` 失败。修复后普通取消会在 exact Harness close 结算后才释放 runner/槽位；Server shutdown 仍保留原有宽限与再次取消路径。修复前定向循环在第 6 次复现，修复后连续 `30/30` 通过，并新增 sidecar active task/session/message 全部清空的回归断言。
- Runtime/Service/Provider RPC 受影响套件：`131 passed`；Coding Worker 与 Project Source：`625 passed, 5 skipped`。
- Agent Workspace、Coding Runtime 与 Project Host：`439 passed, 9 skipped`。
- 后端首次全量因 RAG PDF safety 子进程在 10 秒阈值内未返回而为 `1 failed, 4798 passed, 29 skipped`；该用例在全新无网络容器中精确复跑通过，未修改 RAG 代码或阈值。槽位修复后的完整后端套件曾出现一次输出丢失、无法归因的 52% 单点失败，因此不记为绿；随后以 fail-fast 从零完整执行为 `4799 passed, 29 skipped`。52% 收集区间位于模型路由/多模态测试，两个时间阈值用例另做 10 轮定向复跑，共 `20/20` 通过，未修改该模块代码或阈值。重放到最终主线后再从零执行完整后端套件，结果为 `4806 passed, 29 skipped`。
- 最终主线前端：`119 files / 703 tests`，production build 通过；保留既有大 chunk 警告。
- 主 Worker 与 V20 evaluation Compose 静态展开通过；V18 compile 与 Fake smoke 通过，Fake smoke 仍为 8 条、四类别齐全，摘要 `472b88ae9de93f3816de84bc40d07e7c192ec82c4eca6cb67ef2f56dc60a1df3`。
- OpenCode、Claude、Project Source、ACP evaluation、Codex evaluation 五个镜像均以 UID/GID `65532:65532` 运行；无网络探针分别确认 OpenCode `1.18.9`、Claude Code `2.1.89`、ACP SDK `0.12.0` 与 Codex CLI `0.149.0`。
- `git diff --check`、秘密候选和禁止产物扫描通过；所有逻辑提交仍不超过 5 个文件。

生产 Server Dockerfile 仍显式使用 root，这是既有部署偏差，不在本次最小修复边界内；不得以 sidecar 非 root 结论掩盖。客户端锁文件未变化，`npm ci` 仍报告 5 个既有 audit 项，本次不升级依赖或运行 `audit fix`。

交付状态继续为 **Experimental**：完整 OpenCode/Claude 四项真实矩阵仍为零，没有运行 calibration、parity、certification 或 48/288 对照，不使用“现有 OpenCode/Claude 已通过该内核运行”、能力提升、接近或等效表述，也不据此进入 V21。回退仍为关闭 V20/evaluation 开关并恢复上一镜像；已有任务、Workspace、Evidence、operation 与 checkpoint 保留，不降级、不自动重放。
