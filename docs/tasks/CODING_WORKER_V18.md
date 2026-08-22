# V18 首轮：真实任务准入与可执行 Harness v3

## 交付边界

本轮只建立两项基础能力：12 项真实任务校准集，以及新任务持久化前的 Workspace Source Admission。它不新增 Provider、工具或 UI，不运行 48 次真实校准，更不启动 288 次认证。交付状态只能写为“真实任务校准基座可用”或“Experimental/阻断”，不得使用任何 OpenCode 等效表述。

Harbor 固定为 `0.21.0`，只存在于独立评测环境，不进入生产 Server 依赖。原生对照固定 OpenCode `1.18.9`；发布窗口另行审计当时最新版差距。本轮借用 Harbor Task 1.4、Oracle/Nop、独立 verifier、ATIF 与 `BaseAgent` 扩展点，以及 Terminal-Bench/SWE-bench 的任务有效性和回归分层语义；产品执行边界仍只有 ModelMirror Workspace、Tool Broker、审批、Evidence 与 v13 写回。

2026-08-21 发布窗口复核时，OpenCode 官方最新稳定版为 [`1.18.19`](https://github.com/anomalyco/opencode/releases/tag/v1.18.19)。该版新增 Cloudflare AI Gateway 的 OpenAI/Anthropic 原生透传并修正若干认证、计费、WebSocket 与 Provider 兼容问题；发布说明未提供 Harbor 非交互 runner 所缺的 scenario steering、question 回答和组件故障编排层。因此固定 `1.18.9` 的可比 runner 暂不升级，但这些网关与兼容差异进入后续差距清单；不能据此解除 Session 校准阻断。

评测 Server 通过 `docker-compose.coding-worker-v18-harness.yml` 只读挂载公开 `fixture-bundle.json`；该 overlay 不挂载 solution、verifier 或密封 checker，也不得加入共享产品栈。

## 任务集与隔离

`benchmarks/coding-worker-v18/` 包含 Python、TypeScript/React、Repository、Session 四类各三项任务。每项任务将 H0、Oracle solution 与 verifier 分开；Agent 只获得 `environment/project` 和公开目标。公开仓库的 `tests/` 只保留 verifier 启动包装和 Workspace policy，隐藏检查正文只存在于仓库外的只读密封目录。CLI 核验逐任务哈希与整体 bundle 哈希后，才把正文注入一次性 Harbor task 副本。Verifier 在独立无网络容器中取得终态 Workspace Artifact，Provider、Executor、Workspace API 与 Worker Agent 均不能读取 `solution/`、公开 verifier 包装或密封 checker。

任务编译器拒绝软链接、reparse、硬链接、异常文件、错误镜像 digest、环境引用 verifier/solution、缺少二进制 canary、少于两文件的有效修复及陈旧 source binding。普通任务至少五个源码/测试/配置文件；长上下文任务至少三十个文件。

确定性门禁要求每项 Oracle `5/5` 通过，Nop 与 near-miss 各 `5/5` 失败。Docker Desktop 上默认只使用仓库的静态 `network_mode:none` 环境做这些任务有效性门禁；评测专用 `DockerDesktopAllowlistProbeEnvironment` 只允许少量明确选择的真实场景经 exact-host sidecar 探测，并被 `run-round` 硬拒绝，不能形成校准证据。48 次真实校准仍只能在 Linux 上使用 Harbor 标准 Docker 环境，并先通过动态出站控制预检。

## Harness v3

主要命令：

```powershell
python scripts/coding_worker_harness.py compile --write
python scripts/coding_worker_harness.py validate --sealed-checker-root C:\absolute\sealed-checkers
python scripts/coding_worker_harness.py smoke
python scripts/coding_worker_harness.py task-gate --sealed-checker-root C:\absolute\sealed-checkers --harbor C:\path\to\harbor.exe --repetitions 5
```

真实发布窗口才可运行 `run-round`。它固定产生 `12 tasks × 2 engines × 2 attempts = 48` 个记录，并要求用 `--allow-agent-host` 指定冻结模型网关的精确主机名；不接受通配符、IP 字面量或 localhost。两侧的任务级 Agent 超时均为 900 秒；Worker API 同时使用同一 900 秒任务预算。原生 OpenCode 权限默认拒绝全部工具和 Bash，只重新开放工作区内文件/Todo 工具与任务冻结的精确命令；可见检查和只读命令可重复，只有 scenario 明确标为 `mutate` 的命令重复执行才计为副作用重放。固定 1.18.9 的原生 LSP 因继承 Server 凭据且可加载仓库插件而失败关闭；Worker 隔离 LSP 不变，完整原生工具配置进入 route binding，这项差异只能作为校准限制，不能被结果隐藏。外部 `OPENCODE_*` 配置不会继承，任务 H0 也不能包含或创建 OpenCode 配置覆盖。工具调用、turn、公开输出和规范化 token 上限连同原生模型名、Worker 中立 route、allowlist 一起进入 route binding，超限记录按事实标为 `budget` 失败。

Harbor 内置阻塞式 OpenCode runner 仍不能公平驱动三项 Session 场景，因此 `run-round` 改用评测专用 `NativeOpenCodeHarnessAgent`：它复用 Harbor 的固定 OpenCode 1.18.9 安装，但通过随机认证、仅回环的 Session API 精确执行 question 回答、running steering、显式 compaction 与故障后的原 operation reconcile。OpenCode Server 的认证和模型 route 环境只保留在 root 进程及 root-only 私有目录；全部冻结 Shell 由平台 wrapper 以 `env -i` 最小环境、uid/gid 65534 和独立 HOME 执行，Workspace 只向该 UID 开放写入。冻结的 restart 场景由同一 root 监督 wrapper 在降权子命令副作用成功后原子写入结果标记并阻塞回执，随后终止完整 OpenCode 进程组；只有同一 call ID、完整工具参数的 intent hash、成功 result hash、故障与 resume 台账闭合，原 operation 才能结算，重复执行仍产生第二个副作用 operation。控制器在供应商帧写盘前删除 reasoning 和凭据；accepted 结果必须携带与任务、模型、session、environment 绑定的 `native_ledger`，并由实际 question/compaction 事件、公开消息哈希及 unknown/reconcile 一一对应关系重建事实。三项 Session 已完成一次授权的定向真实探测，其中两项 accepted、一项按事实拒绝；这不是 48 次校准，也不能形成能力等效结论。

每条可执行 run 还记录 CLI 从外置 checker 正文与完整 fixture bundle 实际派生的密封摘要、当前干净 worktree 的候选 SHA，以及 controller 从自身 container ID 和 Docker image ID 运行时对账所得的 runner image；CLI 不再接受调用方填写 digest。候选与实际 HEAD 不同、worktree 不干净、宿主直接运行或 image 对账失败时拒绝启动。启动前和整轮结束后，Controller 还必须通过精确回环 Worker API 取得在线 attestation：Server 与两个 Provider 的完整 `coding_worker` Python 包必须和候选 checkout 同哈希，两个 Provider 必须同时证明固定 OpenCode 版本、通用 route 与相同模型身份哈希；任一组件、generation、route 或模型漂移即拒绝形成报告。Controller token 不得发送到非回环 URL。Worker Agent 从真实 `provider_event.payload` 的持久 usage 事件提取规范化 token 计数，任何 checker 通过但 token usage 缺失或为零的记录都无效。`report` 从完整矩阵派生 route、checker、candidate 与 runner image 绑定，均不接受调用方另填。校准报告固定为 `calibration`，`certify` 必须拒绝它。

一次性任务副本不是可信输入。CLI 在注入密封 checker 前重新编译复制后的公开任务并要求与冻结 fixture 完全一致；注入后使用 Harbor `0.21.0` 自身同时计算 publishable-content digest 与 `result.json` 使用的 task checksum。真实 run 在每次 trial 后复核，确定性 task gate 在每个 agent 批次后复核；两者都以运行前的冻结摘要为基准。Worker run 的 fixture task、Worker task、source/revision、ATIF session、终态 Workspace Artifact 及实际 tree hash 必须形成同一条 ledger binding；任一跨任务拼接或 Artifact/tree 不一致都会使记录无效。

Session 故障场景使用仅 Harness profile 可用的受控注入端点。Controller token 至少 32 字节且只经环境传入；端点只接受当前任务唯一待批准的 `run_shell` mutate operation。副作用已原子发布但回执尚未写入时，Server 关闭该任务的 Executor binding、把原 operation 标为 unknown，并要求按原 ID reconcile。它验证的是精确结果未知与绑定重建，不宣称发生了宿主容器或操作系统级重启。

协调指标只能由 Harbor ATIF 与 Worker 公开 ledger 重建：`platform_coordination_failures`、`duplicate_side_effects`、`unsettled_operations`、`orphaned_interactions`。每项计数必须携带脱敏 evidence ID；缺轨迹、缺 ledger、非 ATIF-v1.7、摘要不符或异常但无协调事实时，记录无效或明确失败，不能用调用方填写的零值通过。

## Workspace Source Admission

创建顺序固定为：

1. 先按服务端 origin 与 `client_task_id` 查询旧任务；精确幂等重试直接返回原任务，不依赖当前 Provider、Helper 或来源在线。
2. 对新任务执行适配器 `admit()`：校验 source 已注册、revision 精确、当前可用且适配器受支持。Manifest 只调用 Project Source `check`；Host Snapshot 只检查 Helper catalog 的 exact HEAD，不提前传输快照或占槽。
3. 在同一事务中写任务、加密 admission receipt/binding hash/observed time 和 `source_admitted` 事件。
4. Scheduler acquire 时再次检查 exact revision；receipt 不是使用时授权。

公共失败统一为 HTTP 409 `workspace_source_unavailable`，reason 只能是 `not_registered`、`revision_changed`、`temporarily_unavailable`、`unsafe` 或 `limit_exceeded`，不得返回路径或宿主信息。历史任务没有 receipt 时保持旧恢复路径；服务端新建任务必须持有有效 receipt。

来源大小采用分层策略而不是修改大型索引：部署控制、精确 revision 绑定的 `builtin` 单文件硬上限为 `16 MiB`；manifest Project Source 与 Host Snapshot 仍为 `8 MiB`。所有来源继续共享 `192 MiB` 总量和 20,000 文件上限。`builtin` 在任务持久化前枚举 tree 元数据并把 `builtin-16m-v1`、文件数和总字节数绑定进 admission receipt，Scheduler acquire 时再次复核；外部来源不会因内部 `SourceFile` 上限提高而获得额外权限。

## 历史审计记录与最终收口

2026-08-21 的中间候选曾处于 `Experimental/阻断`：Source Admission 与确定性任务有效性基座可独立验收，但 12 项离线 runtime 绑定和修后完整任务门禁尚未闭合。三项 Session 的授权定向真实探测为 `2 accepted / 1 rejected`，48 次真实校准未运行。

以下是 2026-08-21 中间候选的审计记录，不作为最终候选身份：

- 当前公开 fixture bundle 规范摘要为 `09d447f1324f8382cc9cda5534595933167220ce5ce4067aca30160e5d9880d1`；密封摘要同时绑定完整 fixture bundle，以仓库外 checker 只读重跑 `validate` 已通过，当前配对摘要为 `00e147da45c2a557043d9bd8ee1913702a8b6b0a0c86457d411634e54a084fc6`。当前 bundle 的 Fake smoke 为 8 条记录、四类别齐全，记录摘要 `685a1c711cdad8559a3ec1b8fd8da7b21b87c984197cd4d0bdefab4c506e00ec`。
- 12 项任务的 Oracle `60/60`、Nop `60/60`、near-miss `60/60`；共 180 个有效 trial，三阶段异常数均为零，reward 分别严格为 `1.000 / 0.000 / 0.000`。
- 反证审计后的最小纠正专项为 `80 passed`，Workspace/Service 回归为 `48 passed`；该阶段修后 Nop/near-miss 长批次曾因桌面会话异常中断，因此当时没有把历史结果冒充最终重跑。
- Harness v3 单文件 `48 passed`；产品专项七文件 `154 passed`。Agent Workspace 与 Coding 合集首跑为 `1003 passed, 14 skipped, 11 failed`，11 项均因临时副本漏根目录文件；补齐后对应九个测试文件 `59 passed`，最后一项 `.dockerignore` 用例 `1 passed`，因此没有产品失败。
- 后端全量 `3507 passed, 29 skipped, 21 failed`；21 项均是未构建 Agency Worker dist、既有 Skill Node matcher/索引依赖和模型路由 p95 基线，V18 新增路径无失败。前端未改动，既有本轮证据为 83 个测试文件、407 项通过及 production build 通过。
- 102 个变更 Python 文件 `py_compile` 通过；V18 Compose 叠加拓扑 `config --quiet` 通过；Diff、未跟踪文本空白、敏感信息和禁止产物扫描通过。
- 原生 Session 控制器、Shell 凭据/UID 隔离、精确副作用后/回执前 fault gate、runtime runner image attestation、bundle-bound checker 摘要及反证用例已实现；固定 Harbor 0.21.0 controller 内只读导入检查通过。实际 Python 与 Node 任务基础镜像均证明子命令环境不含 Server/route secret、uid 为 65534、无法读取 root-only 密码文件且仍能写 Workspace。最终候选已把全部 12 项 fixture 统一绑定到 daemon-attested 离线 runtime，缺少任一绑定时继续失败关闭。
- 另经明确额度授权执行了两项共享栈 Worker 针对性真实任务（Python/default 与 TypeScript/default），两者都在第二个顺序审批恢复时复现 `approval_operation_conflict`，且都在副作用重放前安全阻断。修复后同一 turn 的双审批回归与 Service/Shell 合集分别为 `1 passed`、`50 passed`。一次 `coding/quality` 探测因余额不足且 usage/cost 均为零，不计为模型运行。
- 补充额度后的共享栈修后复测使用了两个全新任务。Python `task_1f140dfe658148cfbf54d2b14aa53635` 在同一 turn 依次结算 `pytest_init_3884` 与 `verify_allocator_fix`，两个 operation 各产生一次 `operation_reconciled`，无 `approval_operation_conflict`、重复终态或未结算 operation；终态为 `completed`，`python-pytest` 与 `python-compile` 均在最终 tree `3de209c08057dd6d92d0f8b76ba7de90de480387e4a0fb62f207c26708f63f6a` 通过。TypeScript `task_c4834e04dd6e42c19bcb209ae8bf4cd1` 的单次审批恢复、`react-test` 与 `react-build` 也通过，且无协调异常，但它暴露了独立的目标/夹具失配：输入目标要求焦点、Tab、Escape 和焦点恢复，来源项目与冻结检查只表达静态 ARIA 标记，最终 Diff 也只实现静态标记。因此该任务只证明审批传输与检查执行成功，不能计为语义任务成功；没有追加第三项真实任务。
- 本轮反证修复新增真实 SSE usage 解析、完整事实源失败关闭、instruction/scenario/acceptance 绑定、场景结算，以及在线代码、实际 CLI 版本、route、模型和进程 generation attestation，并修复 question option 请求形状及 `TaskChildrenResponse` 字段回归。受影响集合（Harness、API、Provider RPC、OpenCode、Claude、Source Admission、Service、Deployment）上一轮为 `186 passed`；本次又修正了 OpenCode 启动期健康探测阻塞、Docker Desktop internal 网络阻断、结构化提问指令、Docker-host-visible jobs 路径要求，以及 reconcile 必须绑定完整工具参数（含 `workdir`）的 intent hash。
- 明确授权的定向真实探测使用 `openrouter/deepseek/deepseek-v4-flash`，不是校准：`session-clarify-before-edit` accepted（sealed reward `1.0`，input/output tokens `43643/838`），`session-steering-compaction` accepted（`1.0`，`44513/1218`）；`session-restart-command-reconcile` rejected（reward `0`，`151518/2275`）。第三项在修复完整参数 hash 后四项协调诊断均为零，但模型仍尝试未批准 Shell（安全拒绝），且生成索引为 beta→alpha 而非稳定 alpha→beta，故归因为真实 `policy/agent outcome` 失败，不能人工改写为通过。

截至 2026-08-22，最终候选状态为 **真实任务校准基座可用**：

- 公开 fixture bundle 摘要为 `866c29944c84efdf1371ea90fc085a8949b3d5d53aaa9d558e6813aec536c11d`；外置只读 checker 与完整 bundle 的配对摘要为 `a019e421dcfd95ac6619e973f233bbcaf38343115b21c64c13fd73fedd5acc80`。`validate` 返回 12 项、Harbor `0.21.0`、OpenCode `1.18.9` 和 `status=valid`。
- Fake smoke 为 8 条记录、四类别齐全，记录摘要为 `472b88ae9de93f3816de84bc40d07e7c192ec82c4eca6cb67ef2f56dc60a1df3`。
- 全部 12 项 fixture 均继承 daemon-attested runtime `modelmirror-coding-worker-v14:local@sha256:27302ee0527aff43e651d82148beb1c2562ffabc22606974499c13f831f417ed`。Session fixture 显式安装冻结 near-miss 所需的 `patch`。
- 最终 bundle 从零重跑 Oracle `60/60`、Nop `60/60`、near-miss `60/60`；共 180 个有效 trial，异常数均为零，reward 分别严格为 `1.000 / 0.000 / 0.000`。该结果是确定性任务有效性证据，不是 48 次真实模型校准。
- PR 发布预检还统一移除了任务文本末尾的多余空行，并同步修正两份依赖尾部上下文的 near-miss patch hunk；完整 PR Diff 的 `git diff --check` 因此通过。哈希变化后没有复用旧证据，而是重新执行了上述 180 次门禁。
- Harbor 0.21.0 的静态 Docker Desktop 门禁清理不再并发执行 `--rmi local`；仍删除 trial 容器、卷和 orphan，避免共享 runtime 派生镜像删除竞争。
- 修后专项为 `128 passed`；重放至最新 `origin/main` 后，全部 Coding Worker 为 `398 passed, 5 skipped`；Agent Workspace、Coding Runtime、Recovery 与 Publisher 兼容集为 `265 passed`。最终前端为 96 个文件、`506 passed`，production build 通过。
- 主线重放前的后端空载全量为 `3753 passed, 29 skipped`；重放时产品代码路径无交叉，只有两份文档自动合并，并在重放后重新运行了全部 Coding Worker、前端测试和 build。此前一次仅有的 PDF 资源顺序失败已单例复跑通过，后续全量未再出现。
- V14、V15、V17 与 V18 Compose 组合均通过 `config --quiet`。共享栈 `/api/health`、`/coding` 与 `/api/coding-worker/v1` 均返回 HTTP 200，Server、双 Provider、双 Executor 及 v13 写回相关容器健康。

本轮没有运行 48 次真实校准或 288 次认证，也不宣称接近或等效 OpenCode。后续若决定启动 48 次校准，仍须在固定 Linux controller 中冻结干净候选、完整 runtime、route 和 checker；不得从本轮确定性 180 次结果推导真实模型成功率。

回退时停止 Harbor profile并恢复旧创建入口；nullable admission 表、已有任务、Workspace、Evidence、Recovery 与 v13 写回数据全部保留。旧任务不做破坏性迁移。
