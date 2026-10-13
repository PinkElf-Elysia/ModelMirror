# R8-A：持久执行内核收口

日期：2026-10-07。当前批次：A1，所有权与终态的核心 Store / Coordinator 收紧及兼容迁移。**A1 尚未完成全部调用者迁移，不具备提交门禁。**

## 开工契约

- 基线：`origin/main@e3ac455553c2d3065bcd8768d78b849c8477392a`。
- 分支：`codex/meta-planner-r8-a-durable-kernel`；独立工作树：`C:\tmp\modelmirror-meta-planner-r8-a-durable-kernel`。创建后 Git status 干净；不修改原主工作区、R7 工作树或预览。
- R7 功能 PR #403 已合并，merge 为 `c568ccf529e7e4c4b8a30077afe07440503f8667`。本基线随后合入 #404；其后端差异仅涉及 OpenRouter Decision 请求模型，不涉及本轮三个被测 Runtime 模块或恢复回调。
- A0 允许路径只有本任务卡、两个 `test_workflow_r8a_*.py` 和一个自有合成 JSON 夹具，共四个文件。生产实现、依赖、CI、前端、运行目录和持久数据均不改。
- 本批无公共接口变化、无数据迁移、无新依赖。风险为对高风险恢复路径建立离线验收，不是已上线修复。
- 单实例跨重启是后续目标，不承诺多后端进程共享运行目录。Capability 保持 V10 / 22 类；不开放 Planner 节点，不改变单模型及最多三次生成调用边界。
- 不实施 R8-B 至 E，不激活 R8-F，不提前进入 V4。外部模型、业务写入、共享栈、Proposal 批准、发布、Commit、Push 和 PR 均未执行。

## R7 与主线门禁分别记录

2026-10-07 本批读取 GitHub 检查结果：

| 检查 | 已证实结果 | 处理边界 |
| --- | --- | --- |
| #403 Backend quality | 通过，job `112792852473` | 不是 R8-A 修复或全量通过证明 |
| #403 Frontend quality | 失败，job `112792852327` | 前次离线审计在 R7 前后目录均复现时钟相关三项断言；不重写历史 CI 状态 |
| #404 Frontend quality | 通过，job `112800787454` | 最新上游前端 CI 证据，不冒充本地重跑 |
| #404 Backend quality | 失败，job `112800787381` | `check_provider_coverage.py` 报 `models.ts` module review 未复核、stale site、source module drift；其后后端测试未据此获得通过证明 |
| #403 / #404 AI Research 范围检查 | 失败 | #403 前次使用 base-owned reviewer 复现 protected-path 拒绝；#404 本批只核验失败状态，不猜测详细根因 |

本地执行 `python -B scripts/check_provider_coverage.py` 同样失败，输出与 #404 对应。A0 未修改该脚本或目录。该上游门禁必须独立收口或取得明确处理结论，不能将 #403 后端通过冒充最新主线全绿；本批可记录反例，但不以此授权带病提交或部署。

检查链接：[#403](https://github.com/PinkElf-Elysia/ModelMirror/pull/403)、[#404](https://github.com/PinkElf-Elysia/ModelMirror/pull/404)、[#404 后端检查](https://github.com/PinkElf-Elysia/ModelMirror/actions/runs/37623930537/job/112800787381)。

## A0 反例与控制组

`server/tests/test_workflow_r8a_execution_fencing.py`：

- 五个变更入口：`fail / complete / suspend / append_event / update_run_id`，分别测试旧 token、过期 token、接管后缺 token，以及当前合法 owner。
- 明确拒绝时同时核对内存和快照字节未变。仅拒绝异常不等于证明整条外部调用链没有副作用。
- 保留首次执行无 token 的历史控制组，以及 completed 后来源失效标记；不将后者误报为 completed 状态回退。
- 取消后迟到完成不得复活任务；等待 continuation 不得被无 owner 的完成清除。
- 使用真实 WorkflowHandoffCoordinator 与两个 Store，通过确定性时钟触发接管后的旧回调异常，检查新 owner 和快照不被覆盖。无 sleep、无真实模型。
- AgentTaskStore 使用公开取消入口后接收迟到完成，同时检查内存与重新打开的文件结果。
- 自有合成 v1 快照验证 `approval_id` 旧等待字段、typed continuation、已完成结果与旧 running 租约清理。没有真实业务数据；该样本不证明恢复请求可以安全自动重发。

`server/tests/test_workflow_r8a_resume_projection.py`：

- Approval / Client Tool 两入口，乘 approval、client、timer、retry、handoff 五种等待、cancelled、正常成功，共 14 项。
- 从当前 main 原样提取三个函数和错误类 AST，执行真实 SSE 消费器、恢复回调、ExecutionStore 和 AgentTaskStore。没有改写被测函数体。
- 运行器与 Payload 构造替换为确定性边界；RunRegistry 查询为空，未绑定 Automation。不是完整应用、真实 Provider、RunRegistry 或 Automation 集成验收，A2 必须补齐这些边界。
- 当前等待控制组仅证明没有错误完成父任务，不证明等待类型、超时和跨 Store 状态已经完全正确。
- 测试保留红测，不添加 skip/xfail，不反转断言去迎合旧缺陷。

首跑冻结结果：**40 项，23 通过 / 17 失败**。失败归并如下，不能当作 17 个独立根因：

| 边界 | 通过 | 失败 | 已证实行为 |
| --- | ---: | ---: | --- |
| 旧 token / 过期 token × 五种变更 | 10 | 0 | 显式旧身份可以被拒绝 |
| 接管后省略 token × 五种变更 | 0 | 5 | 无 token 绕过身份校验 |
| 当前 owner × 五种变更 | 5 | 0 | 合法 owner 控制组可执行 |
| 首次执行、来源失效、取消、旧快照 | 3 | 0 | 已有兼容与部分终态保护有效 |
| 等待误完成、协调器迟到异常、AgentTask 取消复活 | 0 | 3 | 三项目标不变量失败 |
| 恢复结果与父状态投影矩阵 | 5 | 9 | 非本入口等待及取消被投影为完成 |

## A1 调用者迁移清单

以下是基于上述基线的直接调用与委托调用初始清单，不是已完成迁移或全局副作用安全证明。正式修改前须再次核对各分支及直接对象引用；尤其不能只对调用文本批量补 token。

| 类别 | 入口与位置 | 当前事实 | A1 要求 |
| --- | --- | --- | --- |
| 初次执行 / 已认领恢复 | `main.py:_run_workflow_response`，11020 起 | helper 只在有 resume token 时传所有权；首次执行 create 不先 claim | 明确两类身份；恢复 token 缺失不能降级为首次执行 |
| Runner 事件与终态 | `main.py:17168-27069` | 多处带 helper；26096、26148、26499 及多种异常后的 append_event 无 token | 按写事件发生在终态前/后、是否已失去 owner 分类；不得仅补 token 使正常终态事件丢失 |
| Timer / Retry | `main.py:resume_runtime_due_execution`，27491 起 | claim_due_wait、refresh_lease 委托调用、fail 显式 token | 保留 due wait 身份与取消/到期竞争；不开放新 Planner 等待 |
| Approval Coordinator | `approval_coordinator.py:115-154` | claim、fail、release_ready 带当前 token；defer 允许相同 token 到期后释放 | 保留已存在的 fencing；旧 token 不能释放新 owner |
| Handoff Coordinator | `workflow_handoff_coordinator.py:86,128,142` | 缺失回执时 fail 未认领；回调异常 fail 丢失 claimed token | 区分管理侧坏回执处置和 worker 失败；后者必须按原 claim 处理失权 |
| Client Tool Coordinator | `client_tool_coordinator.py:216,230` | claim 后异常 fail 无 token | A1 补独立竞态测试，不把 Handoff 反例当作此路径已实测 |
| Handoff 恢复回调 | `main.py:resume_workflow_handoff_execution`，29053 | 异常 fail 未携带 owner，随后可更新 deployment | 必须先确认身份/结局，再投影其他 Store |
| Approval / Client 恢复投影 | `main.py:28541,28662` 附近 | 按少数 event 特判，剩余默认完成父任务 | A2 统一结果分类，A1 不逐事件堆补丁 |
| 管理取消 / 审批决定 | `main.py:6741,27146,29713,30122`；`approval_api.py:149,153,211,261` | cancel、mark_ready 和审批审计无 worker token | 明确管理操作权限与 wait/revision 绑定，不伪装成 worker，也不能让 worker 借此绕过 |
| 到期审批 / Office 事件 | `main.py:28667,28867` | 存在无 token 事件追加 | 区分管理审计和 owner 内执行回执 |
| Deployment 终态对账 | `main.py:reconcile_runtime_deployment_execution`，27779 起 | 调用独立 mark_deployment_projection_reconciled | 保留幂等对账，不能借对账恢复原终态 |
| Agency 兼容执行 | `expert_team_agency_runtime.py:812,988-1016,1333-1715` | 首次执行及恢复采用独立协调路径，包含多处无 token Store 写入 | 单独确认其所有权协议；不可批量禁用造成另一产品链路回归 |
| Skill 版本绑定 | `main.py:12070,13695,14731,15348` | bind_skill_versions 通过委托调用；同 Skill 已固定版本不可替换，但 Store 方法不校验 owner | 保留不可变版本事实，核验写入时机与失权边界 |
| R7 私有写入日志 | `workflow_native/controlled_writes.py:211` | freeze_private_write_entry 无 token，已有同 key 请求一致性与容量限制 | 不能改变既有 operation ID 或恢复账本语义；长运行写入组合留到 R8-E |
| 读者和维护工具 | `skills/creator_runtime.py`、`skills/experience.py`、`skills/creator_evidence.py`、`xperts/app_api.py`、`execution_store_recovery.py` | 有 Store 注入、读取、安全投影及显式恢复工具 | 不把序列化/离线维护视为 worker 权限；A1 保留读取兼容 |

Store 方法需要按授权类别审查：`create`、`freeze_private_write_entry`、`mark_deployment_projection_reconciled`、`suspend`、`mark_ready`、`claim_due_wait`、`claim`、`refresh_lease`、`release_ready`、`append_event`、`update_run_id`、`bind_skill_versions`、`complete`、`fail`、`cancel`、`reject`。`get/require/list_items` 的可变对象引用也不能被当成天然只读快照。

不确定项：跨 Store 对账原子性、完整取消传播、根预算跨重启、请求派发不确定恢复、目录第二进程排斥尚未在 A0 验证。已有预算 continuation 恢复不等于根预算贯通已证明；不能声称当前完全没有预算恢复。

## 后续批次顺序

1. **A0：已冻结。** 反例、控制组、旧格式样本、调用者清单及上游 CI 归因。
2. **A1：所有权与终态。** 区分首次执行、恢复 worker、管理操作；封住失权变更与迟到投影，保留来源失效语义。先解决当前八项 Store/Coordinator 反例，再扩大调用者回归。
3. **A2：统一恢复结果。** 成功、失败、取消、等待、推迟、不确定统一分类，未知/矛盾 fail-closed；覆盖真实 RunRegistry/Automation 和父子 Store，替换当前入口各自枚举等待的逻辑。
4. **A3：发生标识与恢复日志。** 恢复复用原发生，新子项才新发生；在派发前、结果确定、不确定三个边界持久记录，不重发无法证明未派发的请求，不复制持久层。
5. **A4：根预算、取消、超时。** 复用现有计量，保持父子根预算，取消阻止后续派发；建立单实例运行目录边界，不承诺强杀在途外部请求或无费用取消。
6. **A5：集成交付。** 故障注入、跨 Store、全量后端、前端、语法、敏感扫描和独立预览，分开报告编译、恢复和真实模型证据。人工验收及另行提交授权后才 PR；不进入 R8-B。

## 验证与回退

解释器：本机已有 `C:\tmp\modelmirror-mcp-test-venv\Scripts\python.exe`，未新增依赖。命令在本工作树执行；`<temp>` 必须是新的仓库外合成目录。

```powershell
python -B -m pytest server/tests/test_workflow_r8a_execution_fencing.py server/tests/test_workflow_r8a_resume_projection.py -q -p no:cacheprovider --basetemp=<temp> --tb=short
python -B -m pytest server/tests/test_workflow_handoff_coordinator.py server/tests/test_xpert_execution_budget.py -q -p no:cacheprovider --basetemp=<temp> --tb=short
python -B -m pytest server/tests/test_workflow_retry_store.py -q -p no:cacheprovider --basetemp=<temp> --tb=short
python -B scripts/check_provider_coverage.py
```

- 首条：失败，23 passed / 17 failed，11.41 秒，失败项与上表一致，未出现导入/夹具异常。
- 使用第二个全新临时目录重复首条：失败，23 passed / 同样 17 failed，8.14 秒；未复用第一次的 Store。
- Handoff / Budget 控制组：通过，5 passed，6.36 秒。
- Retry Store 控制组：通过，4 passed，6.24 秒。
- Provider 清单：失败，上游 models.ts 未复核；未修改规则或跳过门禁。
- 两个测试文件通过内存 `compile(..., 'exec')` 语法检查，三个合成快照条目通过 JSON 解析及 Store 重载测试；未产生 pyc。
- 四个新增文本文件的尾随空白和限定凭据特征扫描通过。Git 核对只有这四个未跟踪文件，无已跟踪生产改动、暂存内容、SQLite、运行数据或构建产物。这是定向静态检查，不是全仓库秘密审计。
- 全量后端、前端本地构建、Docker、真实预览与外部模型：未运行，不以以上局部结果替代。

历史结论：A0 反例固化与迁移清单已建立；上游 Provider 清单和 AI Research 范围门禁未关闭。随后用户明确要求“忽略，开始A1”，允许这两项不阻塞 A1 本地实施；不代表失败变成通过，也不授权带病提交或部署。未顺带修改目录或 CI 规则。

A0 不修改行为或持久格式，回退只需撤下本批新增测试/文档，不操作任何用户运行目录。后续内核回退必须保留新格式读取和保守恢复，不能恢复允许旧 worker 写入的入口，不自动撤销已提交业务写入，不宣称支持自动补偿。

## A1 首批实施与证据

### 变更边界

- 核心批修改 `execution_store.py`、`agent_tasks.py`、两个恢复 Coordinator 和 A0 fencing 测试：已认领执行的五个写入口拒绝省略/空 token；等待不能直接完成；取消任务不接受迟到更新；异常处置携带原 claim 的 token。
- 兼容迁移涉及 `expert_team_agency_runtime.py` 和四个既有测试文件（Handoff、Client Tools、Approvals、Retry Store regressions）。整体超过五个文件是同一 Store 契约及实际调用者的不可分割迁移，不扩展节点、业务授权或恢复结果分类。两个批次分别验证后合并回归；此处并非两个独立可部署版本。
- Agency HITL 恢复原来已 claim 却不传 token。现按原 claim 贯通事件、挂起与终态；失权后的完成/失败不投影 RunRegistry，取消保留 CancelledError，不允许旧执行者取消新 owner。
- 启动故障对账和 Handoff 缺失回执处置使用内部 `snapshot / fail_if_current`。完整快照在 Store 锁内取得并比较，因为事件追加及 lease renewal 不增加 revision，单独 revision CAS 不够。没有新增 HTTP 管理入口；worker 仍必须用租约。
- 保留首次无租约执行、管理取消/拒绝、completed 的来源失效信号，以及无活动 lease 时 run ID 重绑定的既有兼容行为。新增“所有状态只能 running 重绑定”的限制曾导致四项取消定位回归，已撤回；未删除或降低这些原有断言。
- 原有六项“已 claim 后无 token 仍可写”的测试与本轮安全目标冲突，改为先验证无 token 拒绝且内存/磁盘不变，再验证原 claim token 可以正常挂起/完成/失败；不是跳过兼容测试。
- 无持久格式迁移、新生产依赖、Planner 入口变化、SSE 变化或真实外部调用。独立 worker 按已有 lockfile 安装 11 个包并构建，只用于模拟网关下的真实本地 Node worker 回归；锁文件未修改。

### 新增证伪与回归

- 省略、空、过期、被接管 token 均覆盖五个修改入口；检查内存及快照字节。
- Client Tool 和 Handoff 两条迟到异常路径独立验证，不互相替代。
- 管理对账在事件追加、续租、接管、取消和完成后拒绝旧快照；当前快照也不能改变终态。有效对账只增加一次 revision，并可重载同一事件及失败状态。
- Agency 覆盖完成、异常、取消三类迟到结果，分别测试新 owner 运行中和已完成，共六项；核对 ExecutionStore 不变及 RunRegistry 不被旧结果投影。
- Handoff 缺失回执查询期间由新 owner 接管或完成，共两项；修复前均失败，修复后纳入总回归。
- 第一版管理 revision CAS 被事件追加反例证伪，改为完整快照比较；第一版 Agency 取消传播被实际反例证伪，修复后重跑。同一失败检查未被换成其他绿色检查。

解释器同 A0。工作树根目录执行：

```powershell
python -B -m pytest server/tests/test_workflow_r8a_execution_fencing.py server/tests/test_workflow_retry_store.py server/tests/test_workflow_handoff_coordinator.py server/tests/test_xpert_runtime_client_tools.py server/tests/test_expert_team_agency_execution.py server/tests/test_xpert_runtime_agent_tasks.py server/tests/test_xpert_runtime_run_registry.py server/tests/test_xpert_runtime_foundation.py server/tests/test_xpert_runtime_approvals.py server/tests/test_xpert_handoff_executor.py server/tests/test_workflow_retry_store_regressions.py server/tests/test_workflow_retry_security_regressions.py server/tests/test_workflow_retry_reconciliation.py server/tests/test_workflow_retry_runtime.py server/tests/test_workflow_retry_xpert_history_runtime.py server/tests/test_workflow_retry_xpert_cancel.py server/tests/test_controlled_write_execution_integration.py -q -p no:cacheprovider --basetemp=<temp> --tb=short
python -B -m pytest server/tests/test_workflow_r8a_resume_projection.py -q -p no:cacheprovider --basetemp=<another-temp> --tb=short
```

- 第一条：333 passed / 1 skipped，42.32 秒，4 条现有 FastAPI lifespan 弃用警告。Windows symlink 权限不足触发既有 skip；该边界未验证，不描述为 334 项全部通过。
- 第二条：5 passed / 9 failed，8.15 秒。A2 跨等待/取消投影反例与 A0 同样失败；未增加 skip/xfail，也未提前按事件堆补丁。
- 本地 worker `npm.cmd run build` 通过。它不是前端生产构建，也不是真实 Provider 验收。
- 最后核对：13 个变更文件中 11 个 Python 文件通过 AST 语法检查，1 个 JSON 夹具通过解析；`git diff --check`、尾随空白和限定凭据特征扫描通过。未跟踪清单没有 SQLite、日志、storage 或构建产物；暂存为空。这不是全仓库秘密审计。

### A1 剩余项与停止边界

首批没有修改 `server/main.py`，不能将 Store 拒绝变更等同于全调用链无副作用。下列事项继续属于 A1；第二小批进展见后文：

1. 迁移主运行器和 Handoff 恢复异常、Office 回执等剩余所有权传递；恢复身份缺失不得回落成初次执行。
2. 区分合法终态后审计与迟到 worker 回调，验证跨 Store 投影时的所有权；不能在终态清 lease 后简单再次 assert_lease。
3. 审查 Skill 固定版本、私有写日志及可变 Store 对象引用。不得变更 R7 operation ID、账本或授权；长运行写入组合仍留 R8-E。
4. AgentTask 当前只收紧 cancelled；completed/failed、Handoff 显式 retry 与 RunRegistry 的终态约定需要独立验证，不能宣称全状态单调已经建立。

A2 恢复结果分类、A3 发生标识、A4 根预算及单实例目录排他、A5 全链路门禁均未实施。全量后端、前端生产构建、Docker、预览器、真实模型和帮助中心交付门禁未运行。没有 Commit、Push、PR 或部署；上游两项失败继续单列。

当前回退单位为 Store 护栏与配套调用者迁移整体，不能只撤销调用者 token 而保留严格 Store。工作树未部署，回退不涉及业务数据。即使回退代码，也不得声称已解决旧 worker 越权问题或允许带未关闭风险发布。

## A1 第二小批：恢复入口身份与回执写入

2026-10-07，用户要求继续 A1。基线仍为 `e3ac455553c2d3065bcd8768d78b849c8477392a`；保留首批全部未提交改动。仅新增本小批三个路径的修改：`server/main.py`、`server/tests/test_workflow_r8a_resume_ownership.py` 和本任务卡；未修改运行结果分类、Store 格式、授权、计费或 SSE 类型。

### 已定位与实施

- 主运行器原来从恢复对象提取空 token 后转为 None，使用首次执行的无租约路径；且早期预检和 RunRegistry 工作发生在租约检查之前。现先捕获恢复 token 并验证，缺失/空/过期/失权/终态均拒绝，首次执行不受该前提约束。
- Approval、Client Tool、Handoff 三个实际回调入口在自身工作前验证 claim。Office 安全回执显式传递捕获的 token；Handoff 异常失败也携带捕获 token，只有成功写入自身失败后才继续既有部署失败投影。
- 在 RunRegistry 异步读取后、创建或更新运行记录前再次验证，防止读取等待期间被接管后重开旧运行。
- 复用原 `assert_resume_lease`，将其提前定义，没有新增第二套所有权规则或恢复调度器。

### 反例与控制组

新增测试直接调用生产入口并使用独立 ExecutionStore/RunRegistry。对回调测试替换 Runner 边界，对部署侧使用调用监视器；不将该部分称为真实部署或跨 Store 原子性验证。另三项调用真实纯计算 Runner，不使用模型、工具或外网。

- 六种无效所有权（缺失、空、过期、接管、取消、完成）乘五个入口（Runner、Approval、Client、Office、Handoff），检查前置工作未发生、Store 内存/文件不变、无 RunRegistry/部署失败写入。
- Office 合法恢复能写一次回执并再次等待；Handoff 合法异常仍能记录失败和调用既有部署失败投影。
- Handoff 迟到异常分别遇到新 owner 运行中、已完成、已取消、再次等待，均不能修改新状态或投影部署失败。
- 真实纯计算工作流的初次执行与合法恢复均到达 workflow_end，ExecutionStore 和 RunRegistry 完成；异步读取运行记录时被接管则保留等待状态，不重开运行。

验证命令使用首批解释器及仓库外新 `--basetemp`：

```powershell
python -B -m pytest server/tests/test_workflow_r8a_resume_ownership.py -q -p no:cacheprovider --basetemp=<temp> --tb=short
python -B -m pytest server/tests/test_workflow_r8a_resume_ownership.py server/tests/test_workflow_retry_security_regressions.py server/tests/test_workflow_retry_runtime.py server/tests/test_workflow_retry_reconciliation.py server/tests/test_workflow_retry_xpert_history_runtime.py server/tests/test_workflow_retry_xpert_cancel.py server/tests/test_workflow_agent_task_node.py server/tests/test_workflow_managed_runtime.py server/tests/test_workflow_run_contract.py -q -p no:cacheprovider --basetemp=<another-temp> --tb=short
```

- 修改前第一组：31 failed / 5 passed，47.39 秒。未改断言掩盖失败；其中合法 Office/Handoff 漏传 token 是首批严格 Store 带来的待迁移路径。
- 修复后原 36 项：36 passed，13.39 秒。随后增加三个真实 Runner 控制组/竞态。
- 第二条：215 passed，35.32 秒，4 条现有 lifespan 弃用警告。未调用真实模型。
- 合并首批 17 文件测试组与 `test_workflow_r8a_resume_ownership.py`、`test_workflow_agent_task_node.py`、`test_workflow_managed_runtime.py`、`test_workflow_run_contract.py` 后：459 passed / 1 skipped，53.77 秒。仅既有 Windows symlink 权限不足项跳过，4 条同样的 lifespan 警告；临时目录尾缀为 `pytest-a1-entry-combined`。
- `test_workflow_r8a_resume_projection.py` 再次单独运行：5 passed / 9 failed，5.22 秒，仍是原跨等待/取消投影失败，非夹具或入口核验错误。临时目录尾缀为 `pytest-a1-entry-a2-boundary`。
- 当前整体 15 个变更文件中 13 个 Python 文件通过 AST 语法检查，JSON 解析、限定凭据特征及尾随空白扫描通过；未发现待提交 SQLite、日志、storage 或构建产物。`git diff --check` 通过，暂存为空。全量后端和前端构建未运行，不以局部回归替代。

### 未证明边界

本批不声称跨 Store 原子性。RunRegistry 自身写入的等待窗口、终态清 lease 后的安全审计归属、Approval/Client/Handoff 正常结束的迟到投影，以及 Skill/私有写日志所有权仍待后续 A1 小批审查。不能以入口核验替代这些门禁，也不能用重新检查已清除的 lease 粗暴阻断合法终态回执。

回退本批需成组处理恢复入口检查与 Office/Handoff token 传递；不能仅去掉 token 后保留首批严格 Store。无持久数据或部署变更，无外部调用、提交或 PR。

## 收尾定位：恢复结果与父状态投影

2026-10-07，用户要求将原失败从单列转为主动定位。本节针对原 9 项跨等待/取消失败，不替代上游 Provider coverage / AI Research 门禁结论；此前允许忽略上游失败仅用于继续 A1 本地实施。基线与前述工作树不变。本次未修改生产代码或原失败断言，仅更新任务卡并在仓库外执行合成诊断。

### 已证实的因果链

1. `main.py:consume_workflow_stream` 已识别五种等待，不是遗漏了 SSE 分片或等待事件。它将 `workflow_cancelled` 与 `workflow_end` 都作为普通字典返回，调用者需自行判别。
2. `resume_runtime_approval_execution` 只排除再次审批；`resume_runtime_client_tool_execution` 只排除审批和客户端等待。其他已识别结果直接落入“读取 final_output，否则空字符串，然后完成父任务”的分支。
3. 上述分支调用真实 AgentTask/Handoff、RunRegistry 和 Automation 完成入口。这些入口没有在同一判定中验证对应子执行的结算事实。Automation 完成还会清除等待标识。入口 lease 检查只能证明开始时有权恢复，不能证明结束时已经成功。
4. `resume_workflow_handoff_execution` 存在同源漂移：等待枚举遗漏 `node_retry_scheduled`，取消也落入部署完成分支。不能只修 Approval/Client 两处。

原红测再次执行：`test_workflow_r8a_resume_projection.py` 为 **9 failed / 5 passed，9.09 秒**。没有改变断言、增加 xfail 或跳过。其 5 项通过仍仅为正常成功及“不错误完成”的控制组，不代表所有等待语义正确。

### 跨 Store 扩展取证

诊断使用原样提取的生产回调和流消费器、真实临时 ExecutionStore、AgentTaskStore、RunRegistry、AutomationStore，以及未启动的 AutomationCoordinator。目标执行入口设为直接报错；Runner 与 Payload 构造仍是合成边界。所有数据均在新建临时目录；不启动服务器、调度器或外部请求。

| 恢复入口 | 子执行的新结果 | 已证实的错误投影 |
| --- | --- | --- |
| Approval | client / timer / node_retry / agent_handoff 等待、cancelled | 5/5：AgentTask、Handoff、Automation 和三条相关 RunRegistry 均 completed |
| Client Tool | timer / node_retry / agent_handoff 等待、cancelled | 4/4：同上 |
| Handoff | node_retry 等待、cancelled | 2/2：WorkflowDeploymentExecution completed，等待信息清空 |

- 前两行共 9 项全部重载 AgentTaskStore 和 AutomationStore 后仍为错误完成；RunRegistry 仅验证本进程内状态，不声称其跨进程持久化。
- Handoff 使用真实临时 WorkflowDeploymentStore 建立合成发布/执行记录，未启动任何真实触发入口。重载后两个错误仍存在；timer 等待与 workflow_end 两个控制组符合预期。
- Client Tool 转审批的控制组还暴露等待类型不一致：Automation 为 `waiting_approval`，父 AgentTask/Handoff 仍为 `waiting_client`。这没有被原“不得完成”断言覆盖。
- 这些反例不需要模型或业务写入即可复现，不能归因于 Planner 生成质量。对后续调度、预算或实际副作用的影响仍需专门故障注入，不由错误状态直接推定已经发生。

本地可复核命令（解释器为 `C:\tmp\modelmirror-mcp-test-venv\Scripts\python.exe`，工作目录为本工作树）：

```powershell
python -B -m pytest server/tests/test_workflow_r8a_resume_projection.py -q -p no:cacheprovider --basetemp=C:\tmp\modelmirror-r8-a-audit-20261007\pytest-projection-localize-01 --tb=line
python -B -c "import runpy; runpy.run_path(r'C:\tmp\modelmirror-r8-a-audit-20261007\projection_audit.py')"
python -B -c "import runpy; runpy.run_path(r'C:\tmp\modelmirror-r8-a-audit-20261007\handoff_projection_audit.py')"
```

后两条退出码均为 0，表示诊断脚本正常结束，**不表示被测行为通过**。脚本当前是仓库外取证工具，不是已交付的跨 Store 集成测试；修复时须把正确行为断言纳入版本化回归。

### 分类器本身的反例与影响面

同一生产流消费器的合成事件测试已证实：

- 空流、仅未知事件均抛 RuntimeError；不能误报为“所有未知事件都会成功”。
- timer_waiting 与 workflow_end 同时存在时，无论顺序均返回等待，不拒绝矛盾。
- workflow_cancelled 与 workflow_end 同时存在时，以最后一个事件为准，不拒绝矛盾。
- error 与 workflow_end 同时存在时抛 WorkflowStreamFailure，保留既有失败优先行为。

AST 调用清单共 18 个消费入口，包括部署、到期恢复、Skill、External Xpert、Handoff、Automation、Goal 与 Evaluator。此数量是影响面清单，不等于已证明 18 个入口全部有同样缺陷。到期恢复已有较完整的五等待/取消分支，不能将其等同于本次 11 个错误投影反例。

### 修复边界建议，尚未实施

- 先完成 A1 的合法终态结算归属：运行成功/失败后 lease 已清空，不能以再次检查旧 lease 的方式拒绝正常回执；也不能只凭入口时的 claim 允许后续所有投影。
- A2 复用同一个内部结果分类契约，显式区分成功、失败、取消、等待、推迟与不确定。仅经校验的成功可进入完成分支；未知、冲突、身份或持久状态不一致不能默认成功。保持既有 SSE 类型和外部协议。
- 分类结果需要关联当前逻辑执行身份及权威持久结局，再由父 Store 做明确投影。不能只把三个入口的事件集合补齐，也不能把未匹配情况全部伪装成失败、取消或完成。
- AgentTask/Handoff/Automation 当前不能完整表达 timer/retry/handoff 等待。修复前需明确兼容的等待状态及元数据映射，不能随意新增公共状态值或把所有等待统一改为 `waiting_approval`。
- 父状态写入须验证预期来源，且可重复对账；跨 Store 依次写入不是原子事务。补充投影中途崩溃、重复投影、旧 owner 迟到、取消竞争及新等待替换旧等待的故障注入。
- 先固定真实 Store 的完整矩阵和冲突事件反例，再迁移 Approval/Client/Handoff；随后审查其余消费入口的语义。不要把诊断范围扩成一次性改写全部 18 个调用者。

结论：原失败已从“已知红测单列”转为有入口、持久影响和相邻反例的根因定位。A1 未关闭项与 A2 修复仍然阻断 R8-A 提交，尚未进入 R8-B，也不据本次诊断声称恢复内核稳定。

### 本次收尾复核

- 原 A1 合并 21 文件测试组原命令重跑，临时目录 `C:\tmp\modelmirror-r8-a-audit-20261007\pytest-a1-closeout-01`：**459 passed / 1 skipped，78.86 秒**，4 条既有 FastAPI lifespan 弃用警告。跳过项仍为 Windows symlink 权限边界，不是新增豁免。
- 该回归没有包含上述主动保留的 9 项 A2 红测；二者分别报告，不能相加后宣称全部通过。
- `git diff --check` 通过，暂存为空；本次仓库修改仅为本任务卡，没有变更生产实现、既有测试断言或依赖。
- 全量 `server/tests/`、前端生产构建、独立预览与真实模型验收未运行。没有 Commit、Push、PR、部署或业务写入。

## A1 第三小批：终态与审计原子提交

用户授权“开始补齐”。本小批保持既有基线与脏工作树中的已授权改动，限定五个路径：`execution_store.py`、`main.py`、`test_workflow_r8a_terminal_commit.py`、`test_workflow_r8a_resume_ownership.py` 和本任务卡。目标是终态提交与其安全审计事件不分离，并将对应运行记录的终态投影放在持久提交之后。不修改 A2 等待分类、Planner、授权、计费、SSE 类型、依赖或持久快照格式，不触碰用户运行目录。

新增三个真实纯计算 Runner 反例证明：首次/恢复执行发出 workflow_end 时，磁盘中尚无该终态事件；RunRegistry 完成投影也早于持久结算。另 22 项 Store 测试定义新原子入口的合法 owner、身份错配、失权、磁盘失败和不可变返回值契约。修改前为 25 failed / 39 passed（11.17 秒）；其中 22 项失败是新增参数尚未实现，并非 22 个独立既有缺陷，三个 Runner 反例才是原执行顺序的直接证据。

实施方向：`complete/fail` 接受内部可选 terminal_event，先核对 task/run/结果类型及 lease，再在同一 Store 锁和一次快照替换中记录终态与安全事件。新入口拒绝往已结束执行附加另一终态；不携带新参数的旧调用保持兼容。返回深拷贝，不能通过返回对象修改持久 Store。成功及六类主异常分支迁移为先提交、后投影；持久化失败不冒充已结算结果。

风险与回退：高风险 Runner/持久化顺序调整，须通过针对性故障注入及既有运行回归；不能仅撤销 Store 参数支持而保留调用者。无数据迁移，回退为本小批成组恢复，不能回滚用户业务数据。跨 Store 的写入仍非原子，终态后 Skill Handoff、父回调投影、管理取消及其他 A1 未迁移边界不能因此宣称完成。完整验证结果在执行后追加。

兼容测试迁移：首轮 29 文件回归为 609 passed / 2 failed / 1 skipped（79.82 秒）。两项需要随本次内部契约同步，单独触及 `test_workflow_retry_security_regressions.py` 与 `test_skill_creator_middleware_handoff.py`，因此本小批连同迁移共七个路径，不扩展生产范围。前者原来在 completed checkpoint 注入接管，该点现已是持久终态；改在 output node_end、仍在 enrichment 前注入接管，保留“旧 worker 不继续 enrichment、会话、记忆和终态”的全部断言，并新增“完成 checkpoint 时不能接管”的控制组。后者仅同步取消竞态替身的 Store.complete 参数签名，保留不发 workflow_end、不创建 Skill Session 等全部断言。不是放宽为允许失权执行或删除失败检查。

进一步证伪：用真实 Store 替代取消返回值模拟，分别在首次执行和恢复执行的 complete 调用前触发管理取消。原子入口拒绝写入，但第一版 Runner 返回冲突或空流，2 项失败（10.99 秒）。现按权威取消状态沿用 workflow_cancelled 响应，不放松 Store 的拒绝规则；测试验证不投影完成、不改写取消后的快照。成功/失败原子提交不等于新增管理取消协议。

专项命令（同一解释器与独立工作树）：

```powershell
python -B -m pytest server/tests/test_workflow_r8a_terminal_commit.py server/tests/test_workflow_r8a_resume_ownership.py server/tests/test_workflow_retry_security_regressions.py server/tests/test_skill_creator_middleware_handoff.py -q -p no:cacheprovider --basetemp=C:\tmp\modelmirror-r8-a-audit-20261007\pytest-terminal-migration-02 --tb=short
```

结果：**119 passed，11.98 秒**，4 条既有 lifespan 弃用警告。新增故障矩阵覆盖六类异常（Termination、Knowledge、Vision、Document、Content Policy、普通异常）乘正常提交、提交前接管、提交前取消、磁盘写失败。使用生产 Runner 并在 SSE 编码边界注入异常，不是六类节点各自的真实外部服务验收。

29 文件组合组由此前 21 文件 A1 组，加 terminal_commit、control_data_nodes、knowledge_retrieval_v2、vision_understanding、vision_v2、content_policy_runtime、typed_values、skill_creator_middleware_handoff 八个测试文件构成。修复后须使用同一组重跑，不以 119 项替代。

最终复核：

- 同一 29 文件组，`--basetemp=C:\tmp\modelmirror-r8-a-audit-20261007\pytest-terminal-combined-02`：**614 passed / 1 skipped，94.50 秒**，4 条既有 lifespan 警告。Windows symlink 权限 skip 保持单列。
- 原 A2 projection 文件单独重跑，`--basetemp=C:\tmp\modelmirror-r8-a-audit-20261007\pytest-terminal-a2-boundary`：**9 failed / 5 passed，5.38 秒**。本小批没有修改等待分类或该文件断言，不用组合绿色结果覆盖此阻断项。
- 当前累计 18 个变更文件中 16 个 Python 文件 AST 语法通过，1 个 JSON 解析通过。限定凭据特征、尾随空白及待提交运行产物路径扫描通过；`git diff --check` 通过，暂存为空。此扫描不是全仓库秘密审计。
- 本小批完成终态与审计的受控原子提交及主成功/异常投影顺序；尚未解决所有跨 Store 一致性、终态后派生操作归属、父状态投影和管理审计。Skill 版本、私有写入日志及可变引用审查仍属 A1 未关闭边界。
- 全量后端、前端生产构建、独立预览、容器恢复和真实 Provider 验收未运行；无提交、推送、PR、部署或外部模型调用。不具备 R8-A 提交门禁。

## A1 第四小批：固定元数据写入归属

用户再次授权“开始补齐”。范围限定 Skill 版本绑定及受控写入私有日志的执行所有权，复用既有 lease fencing，调用方传递进入 Runner 时捕获的令牌，禁止临时读取当前 owner 冒充旧 worker。保持业务授权、operation ID、Backend 事务、SSE、快照格式和 A2 等待分类不变。

限定路径为 `execution_store.py`、`main.py`、`workflow_native/controlled_writes.py`、新 `test_workflow_r8a_metadata_fencing.py`、本任务卡；兼容迁移另需 `test_controlled_write_store_trust.py`，共六个文件。第六个文件原夹具先 complete 再新增日志，不符合终态不可变；仅迁移合法写入阶段和恢复时认领，必须保留格式、备份、磁盘失败、泄露与重复写入全部断言。

验收先执行新增反例，再运行上述新增测试及 controlled_write_runtime/store_trust、Skill lifecycle、纯本地 Runner 集成，最后重跑 A1 组合与原 A2 红测。所有记录均为临时目录合成数据，无 Provider 或活表调用。恢复后 ready 状态仅允许无令牌的既有相同值读取，新增值必须先认领；已认领的同值调用也须校验令牌，终态和等待状态拒绝写接口。返回值不应提供新的可变引用。

高风险边界：本批只关闭这两个持久元数据入口，不宣称解决 Backend 派发与 Store 提交之间的跨 Store 竞态，也不把日志 fencing 当作 R8-E 长运行写入组合已开放。回退须同时恢复 Store 参数及调用者，数据无需迁移；不撤销用户数据或已提交业务写入。结果待验证追加。

### 实施与反例结果

- 两个 Store 方法新增内部可选 `expected_lease_token`，在同值返回前也检查所有权；新增内容只允许 running。ready 的既有同值调用仅返回副本，不持久化；waiting 和终态拒绝这两个写接口。Skill 返回深拷贝，原不可变版本约束不变。
- 四个 Runner Skill 绑定点和 ControlledWriteRuntime 统一传递恢复入口捕获的令牌。不会读取新 worker 的令牌来重新授权旧回调。首次无租约执行兼容，不改变 operation ID 或 Backend。
- 新测试首跑 **39 failed / 1 passed，5.78 秒**。含新增令牌参数未实现的 TypeError，不代表 39 个独立既有问题。直接反例包含已认领执行省略令牌、等待/终态仍写入、新值返回对象修改 Store 内存、未认领 ready 新增元数据。
- 六文件针对组（metadata_fencing、controlled_write_runtime、controlled_write_store_trust、skill_lifecycle_enforcement、controlled_write_execution_integration、evaluation_write_isolation），`pytest-metadata-focused-01`：**131 passed，22.42 秒**。
- 后增生产恢复 Runner 用例首跑为空 continuation，实际没有执行节点，新增“六笔日志必须存在”断言失败，**44 passed / 1 failed，11.37 秒**。修正测试为显式合成 continuation 队列，不改生产调度；此空 continuation 行为须纳入后续恢复完整性审查，不能用 workflow_end 作为执行证明。
- `python -B -m pytest server/tests/test_workflow_r8a_metadata_fencing.py -q -p no:cacheprovider --basetemp=C:\tmp\modelmirror-r8-a-audit-20261007\pytest-metadata-runner-02 --tb=short`：**45 passed，9.67 秒**。含真实本地恢复 Runner、隔离 Backend 的 insert/query/update/query/delete、六笔私有日志固定令牌和业务哨兵不变；不是模型实测，也不是等待加写入的产品能力验收。

### 扩展回归与新增失败定位

沿用上一批同一 29 文件组，增加 metadata_fencing、controlled_write_runtime、controlled_write_store_trust、skill_lifecycle_enforcement、evaluation_write_isolation、skill_application_receipts 六个文件，共 35 文件；参数仍为 `-q -p no:cacheprovider --tb=short`，临时目录 `pytest-metadata-combined-01`。结果 **778 passed / 1 failed / 1 skipped，122.56 秒**；skip 为既有 Windows symlink 权限，4 条既有 lifespan 警告。不得将该组写为通过。

失败为 `test_skill_application_receipts.py::test_skill_tools_emit_application_evidence_for_installed_package` 的路径顺序断言，单独以 `pytest-metadata-skill-order-repro` 重跑仍 **1 failed，3.80 秒**。定位：`sandbox_toolset.py` 的 `_skill_stage` 用 `sorted(root.rglob("*"))` 排列 Path，实际 Windows 顺序为 assets、references、SKILL.md；测试期待 SKILL.md、assets、references（POSIX 排序）。纯 Windows/POSIX 路径比较产生同样差异。该 provider、测试文件及 application_receipts/package_validation/sandbox_store/toolset 共六文件与 HEAD 的规范化文本逐字一致；此证据证明相关实现不是本批引入，但不冒充完整干净基线测试已运行。

处置：不修改测试断言、不移除该测试。后续独立明确跨平台的 staging 顺序契约；不能只排序报告掩盖派发次序差异，因为同一遍历顺序同时决定 `operation_base:len(files)`。本批没有扩展 Sandbox 范围。

### 剩余门禁

- 原 A2 `test_workflow_r8a_resume_projection.py` 单独重跑，`pytest-metadata-a2-boundary`：**9 failed / 5 passed，5.15 秒**。等待/取消被父回调误判成功的问题未修复，测试断言未改。
- 累计 21 个变更文件：19 个 Python AST、1 个 JSON 解析通过；限定凭据特征、产物路径、尾随空白扫描通过。`git diff --check` 通过，暂存为空。
- 本批没有关闭可变 get/require 引用、终态后派生操作归属、管理审计或跨 Store 派发竞态。已有请求缓存可绕过新增日志入口，不能据此宣称所有业务请求均已 fencing。
- 尚未执行全量后端、前端构建、独立预览、容器重启或真实 Provider 验收。没有 Commit、Push、PR、部署及真实业务写入。A1 与 R8-A 仍未达到提交门禁，不进入 R8-B。

## 逐项收口：A1 引用边界

用户授权“开始逐项收口后验收”。首先限定 execution_store.py、metadata_fencing 测试、本任务卡，以及必要的旧测试故障注入迁移。所有公共对象返回必须为锁内快照，嵌套输入必须脱离调用方引用；内部持久写入仅使用私有 `_require_unlocked`。不改变快照格式、状态值、权限及业务流程。不把私有测试注入入口提供给生产调用方。

先运行新反例，再回归 execution Store、恢复、Agency、Skill 和受控写入测试；若旧测试依赖修改公共返回值制造故障，则迁移为明确私有故障注入，保留原拒绝/恢复断言。风险为调用方以前隐式修改 Store，须检查真实调用路径和扩大受影响回归；回退为成组恢复 Store 引用边界及测试，无持久数据迁移。后续派发和 A2 分类另批处理。

新增引用矩阵首跑 19 failed / 3 passed（5.71 秒）。修复后五文件组暴露旧注入依赖，迁移 retry_store_regressions 和 controlled_write_store_trust 两文件后为 221 passed / 1 skipped（16.27 秒）。35 文件组为 793 passed / 8 failed / 1 skipped（144.76 秒），除既有排序失败外，六项是 retry_runtime 的 continuation 篡改只作用于副本，一项是 Agency 种子帮助函数返回创建时快照。同步迁移这两个测试文件，保留所有错误码、调用次数和历史不变断言。因此本批共七文件，超过五文件限额原因仅为不可拆开的引用契约测试迁移，未扩展生产实现范围。

迁移后的 metadata_fencing/Agency/retry_runtime 三文件原组重跑：159 passed（18.27 秒）。列表查询在同一锁内筛选、排序和截断后才复制，避免无条件深拷贝全部私有日志；锁内写入改用私有引用，不经公共副本。

## 逐项收口：本地派发所有权

下一小批限定 execution_store.py、controlled_writes.py、专用派发测试、controlled_write_runtime 兼容测试、本任务卡。关闭已有 request/source 缓存绕过日志写入口的 owner 校验，并在单进程范围序列化本地 Backend 派发与接管/取消。采用执行 Store 锁先于 Backend 锁的受限同步区段；不在锁内等待外部 HTTP、模型或异步回调，不提供跨进程保证。初始无租约执行保留，恢复必须认领；已提交操作继续从 Backend 账本重放，不重新提交。

验收覆盖缓存请求失权、取消、恢复前未认领、接管与提交的双线程竞态、提交后私有来源日志失败及幂等恢复。租约在同步事务中途到期时，不假装能撤销已提交写入：后续日志提交仍须验证租约，恢复依靠原 operation ID 账本。回退须成组恢复 Runtime 调用与 Store 区段，不能撤销业务提交。不宣称跨 Store 原子事务或 R8-E 能力开放。

四项新反例均失败（4.58 秒）：缓存请求在过期/接管/取消后真的新增记录，虽随后保存来源日志被拒绝，拒绝已太迟；另一项确认 Backend 提交前可被新 owner 接管。修复后首组 144 passed / 2 failed（17.31 秒），两项为 Evaluation 测试帮助函数恢复时没有认领执行。迁移该帮助函数使其显式认领并传入令牌，保留账本重放、取消不再派发及计分去重断言，本批由五路径增至六路径，仅测试兼容迁移。

同一六文件组重跑 `pytest-dispatch-after-02`：146 passed（17.68 秒），4 条 lifespan 警告。没有 Provider、活表或共享服务调用；尚未代替全量回归。

## 逐项收口：终态本地后续操作

本小批限定 execution_store.py、main.py、Skill Creator handoff 测试、terminal_commit 测试及任务卡。完成时 lease 清除，不能伪造新 lease 继续操作；以提交返回的完成快照作为前置条件，在同一线程持有执行 Store 锁期间创建幂等 Skill 会话并记录安全事件。仅适用于既有纯本地后续操作，不跨 await、不调用模型，不宣称跨 Store 原子事务。

先验证完成后源失效或 run_id 改变时不得派发，再验证正常成功、会话幂等及事件持久失败不回退终态。回退需同时恢复调用者及 Store 区段，不删除已创建会话。父任务投影与其他管理审计另批处理。

首跑两个失败中一项是测试误用 update_run_id 的位置参数；改正为关键字后重跑 `pytest-terminal-followup-before-02`，两项均确认已漂移来源仍进入 create_or_get（2 failed，9.53 秒）。修复后四文件组 107 passed（11.94 秒），补全非 completed 状态拒绝及双线程来源失效阻塞反例后，同组 `pytest-terminal-followup-after-02` 为 111 passed（13.72 秒）。会话创建成功而事件存储失败时仍保留完成状态和幂等会话，不声称跨 Store 回滚。

原 35 文件组增加 local_dispatch 后，`pytest-closeout-combined-01`：810 passed / 1 failed / 1 skipped（162.44 秒）。唯一失败仍为原 Skill 路径顺序断言，未跳过或放宽；skip 仍为 Windows symlink 权限。

## 独立小批：Skill 暂存跨平台顺序

限定 sandbox_toolset.py、skill_application_receipts 测试及任务卡。使用相对路径分段的大小写敏感字符串排序，保持原 POSIX 路径比较语义，也让 Windows 实际派发、编号和回执遵守同一顺序；不只排序报告，不改账本或操作 ID 算法。

兼容边界：旧 Windows 的未完成暂存可能已给序号绑定不同路径。现有 Sidecar 账本校验完整请求指纹，新顺序遇到旧操作返回 operation_conflict；禁止静默覆盖、清理账本或自动分配新 ID 重试。仅由人工确认后发起独立新操作，不宣称旧部分操作可自动续跑。Linux 原路径顺序不变；回退同样不得清理既有账本。

验收保留原顺序断言，增加真实本地 Sidecar 引擎的派发顺序、同请求重放和旧 Windows 部分账本冲突反例；不启动容器、网络或 Shell 工具。完成重点回归后重跑同一 36 文件组。

首个补丁误匹配同文件中 Skill hook 的相似遍历，三文件回归仍 52 passed / 1 failed / 1 skipped（13.13 秒）。已撤回该无关修改，以 `_skill_stage` 的 operation_base 和 package_files 上下文定位正确循环；原样重跑 `pytest-stage-order-after-02` 为 53 passed / 1 skipped（12.73 秒）。新增反例仅使用本地 Sidecar 的文件派发与账本，不调用 Shell；三文件扩展组包含既有 Sandbox 测试，未启动共享服务。

原 A2 反例 `pytest-closeout-a2-boundary` 仍为 9 failed / 5 passed（5.10 秒）。根因和断言保持原样：审批回调只识别审批等待、Client 回调只识别两种等待，其他等待及取消落入成功投影。需独立统一恢复分类并核对持久执行身份及父 Store 状态，不能靠再加事件白名单完成收口。

当前累计 28 个变更路径，26 个 Python AST 和 1 个 JSON 解析通过；限定凭据特征、产物路径及尾随空白扫描通过，`git diff --check` 通过，暂存为空。这不是全仓库秘密扫描或全量后端验证。全量、前端构建、独立预览和恢复验收仍待剩余状态投影/管理审计收口，不能据本批绿色测试宣布 R8-A 完成。

同一 36 文件组合最终重跑 `pytest-closeout-combined-02`：811 passed / 1 skipped（177.69 秒），4 条既有 lifespan 警告。原排序失败已在原组转绿，未删除失败测试或降低校验。跳过项仍为 Windows symlink 权限，不计作验收通过。

本次已验证四个局部目标：公共引用隔离、缓存请求与本地派发所有权、完成快照约束下的 Skill 本地后续操作、Skill 暂存跨平台顺序。尚未关闭：A2 统一恢复结果及父子投影（9 项红测）、管理取消审计与剩余跨 Store 一致性、空 continuation 完整性、全量后端/前端构建、独立预览与跨重启故障注入验收。下一小批优先 A2，不增加 Planner 节点，不把只读/模拟证据当作真实 Provider 验收。

未 Commit、Push、创建 PR、部署或调用外部模型；没有活表业务写入或共享栈操作。修复仍留在独立 R8-A 工作树，现有主工作区未改动。

## A2 第一小批：统一结果判定与成功准入

用户授权“开始处理剩余批”。本小批限定新 workflow_outcomes.py、main.py、resume_projection/outcomes 两个测试文件及本任务卡，共五文件。仍在 codex/meta-planner-r8-a-durable-kernel，不改主工作区及既有未提交工作。

统一等待、取消、成功的结果判定；互相矛盾的流结果不得由出现顺序决定结论。恢复入口必须用持久快照核验 task/run、状态、等待类型与 ID，只有明确完成才进入原成功投影。保留 SSE 字段及错误诊断合同、无数据迁移，不增加 Planner 节点或外部调用。

先运行冲突事件组合和原父投影反例，再验证身份/等待登记篡改与受影响 Runtime。风险为以前被静默接受的冲突帧将明确失败；回退同时恢复消费者与入口判定。此批只关闭成功误判入口；父 Store 原子投影、取消传播、Automation 不支持的等待类型及跨 Store 竞态另批处理，不将 9 项转绿等同完整 A2 收口。

反例首跑 `pytest-outcome-before`：52 failed / 6 passed（12.24 秒）。实现后首个五文件回归 236 passed / 1 failed（25.58 秒）；失败是 resume_ownership 的 Office 模拟等待缺少 task/run 身份且 continuation 为空。仅补全该夹具以符合生产等待合同，不修改 lease、调用次数和持久状态断言，因此本批因不可分的夹具迁移增为六文件。追加 Handoff 恢复的七类结果投影用例，其中 Deployment 投影边界为 Mock，不能冒充跨 Store 集成验收。

同一五文件组 `pytest-outcome-after-02`：244 passed（24.76 秒）。原 9 项错误完成反例已转绿，但不代表取消已正确传播或所有等待类型已可在父 Store 表达。AgentTask/Handoff 与 Automation 仅有 approval/client 专用等待状态；不得把 timer/retry/handoff 冒充其中一种，不在此批新增公开状态。

## 管理审计小批：取消决定与事件一次提交

限定 execution_store.py、main.py、terminal_commit 测试及本任务卡。Store 管理取消在同一锁与同一次持久化中生成当前 task/run 的安全 workflow_cancelled 事件，接口不再在异步子任务处理后另行追加。既有终态保持幂等，完成先赢时不得补写取消事件。无新事件类型、数据迁移、外部请求或重试。

验收覆盖 running/claimed/waiting/ready、重复取消、完成赢得竞态、磁盘写入失败与重启读取，再回归两个取消入口。回退须同时恢复 Store 和接口追加路径，避免漏记或重复事件；此批不声称父子取消传播原子化。

反例前两次发现测试夹具漏传 timer resume_at 和 mark_ready 等待身份；依真实 Store 合同补全后，`pytest-cancel-audit-before-03` 四种状态均在“取消已提交但无事件”断言失败（4 failed，4.98 秒），不将夹具错误计作生产缺陷。取消接口不再另行 append_event，Store cancel 自行构造当前 task/run 的安全事件并一次提交。

八文件回归 `pytest-cancel-audit-after`（terminal_commit、execution_fencing、retry_xpert_cancel、resume_ownership、resume_projection、handoff_coordinator、runtime_approvals、runtime_client_tools）：203 passed（15.44 秒），4 条既有 lifespan 警告。终态保持、磁盘失败、重启读取及两个取消入口原断言未放宽。

累计 30 个变更路径，28 个 Python AST、1 个 JSON 解析通过；限定凭据特征、产物路径及空白扫描通过，暂存为空。没有外部模型、活表、共享服务、部署或提交。本批仍是离线验证，不替代独立预览或整轮全量测试。

### 本次组合验收与剩余边界

原 36 文件组增加 outcomes、resume_projection、workflow_deployment_api、runtime_automations、skill_evaluation_runtime、workflow_r18_file_data、workflow_r21_code_v2，共 43 文件。以相同 Python 和 `-B -m pytest ... -q -p no:cacheprovider --tb=short --basetemp=C:\tmp\modelmirror-r8-a-audit-20261007\pytest-outcome-cancel-combined-01` 执行：1020 passed / 1 skipped（227.60 秒），4 条既有 lifespan 警告。skip 为 Windows symlink 权限，未放宽或移除测试。

已关闭本批目标：冲突结束/等待事件不得覆盖彼此；三个恢复入口核验持久身份与结果；审批/Client 的非成功结果不再落入完成分支；Handoff 的 retry 等待和取消不再被 Deployment 当作完成；管理取消及安全审计一次持久提交。

仍不得宣称 A2/R8-A 完成：审批/Client 对非成功结果目前采取安全停止投影，父任务可能保留此前等待标签；跨等待转换、取消向父任务及 Automation 的传播、父 Store 的原子比较更新与跨 Store 对账需下一独立小批。成功核验与后续异步父投影之间的竞态尚未关闭。空 continuation 恢复完整性、逻辑发生标识、父子预算与不确定请求安全回执、全量后端/前端构建、独立预览及跨重启验收仍按原门禁执行。

工作树 HEAD 仍为 e3ac455553c2d3065bcd8768d78b849c8477392a，分支 codex/meta-planner-r8-a-durable-kernel。本次没有 Commit、Push、PR、部署或真实调用，不进入 R8-B。

## A2 父执行小批：Automation 终态与 worker 所有权

本次用户授权最后一批收尾；核对发现 Automation Store 的等待、完成、失败提交没有终态或 lease 检查，取消后迟到回调可复活任务。先独立关闭这一反例，不承诺据此完成整轮。限定 automation_store.py、automation_coordinator.py、test_xpert_runtime_automations.py、新 test_workflow_r8a_automation_fencing.py 和本任务卡，共五文件。

目标：终态不能被普通回调覆盖；运行态提交必须携带派发时捕获的有效 lease；公开执行对象不得泄漏可变引用；Coordinator 派发前及结果/异常提交检查所有权。等待恢复仍走原接口，但校验已绑定的 task/run，显式 retry 保留。无新增状态、公开路由、数据迁移、自动重试或外部调用，不操作真实 Automation。

先通过真实临时 Store 和可控暂停的 Coordinator 复现取消/接管后的迟到回调，再实现并回归 Automation、原 A2 与取消路径。最小命令为 Python -B -m pytest server/tests/test_workflow_r8a_automation_fencing.py server/tests/test_xpert_runtime_automations.py -q -p no:cacheprovider；之后重跑受影响组合。风险为历史未携带 lease 的运行态内部调用被明确拒绝；回退必须成对恢复 Store 与 Coordinator，禁止恢复中清除账本。跨等待类型、父子跨 Store 对账和 RunRegistry 投影竞态不由本批冒充关闭。

反例首跑 `pytest-automation-fence-before`：16 failed（4.69 秒），分别定位取消后复活、旧 worker 的成功/等待/异常/超时越权提交及可变 claim 引用随新 worker 改变。实现后 `pytest-automation-fence-after-01` 为 20 passed / 4 failed（5.12 秒）；四项均定位 `_load` 把过期 running 改成 pending 并清 lease，重启读取不再是磁盘快照。移除此隐式改写，claimable 的既有过期识别不变，不把它等同于不确定请求已安全恢复。

五文件回归 `pytest-automation-fence-after-02`（Automation 两文件、resume_projection、resume_ownership、retry_xpert_cancel）：137 passed（15.05 秒），4 条既有 lifespan 警告。追加缺失/错误/过期 lease、完成后迟到提交、派发前取消、恢复来源错配、接管后新 owner 正向提交、取消等待后禁止成功审计等断言；原测试仅补充真实 claim token，不放宽状态及次数断言。

本批检查累计 34 路径、32 Python AST 和 1 JSON 解析通过；限定凭据特征/Runtime 产物路径扫描无命中、`git diff --check` 通过、暂存为空。前端 node_modules 不存在，未安装依赖或运行构建。后端全量、预览与真实恢复验收仍未运行。

兼容复核发现 Automation 默认 lease=120 秒而执行预算默认 1800 秒，旧代码没有续租；单加 fencing 会拒绝合法长任务。因此同批在 Store 增加当前 owner 专用续租，在 Coordinator 的原预算内维持 lease；丢失所有权时取消本地目标并等待退出，正常结果/异常/取消退出时不遗留续租任务。不增加派发次数、重试或模型调用，也不声称本地取消能撤回已发送的远端请求。

加入续租前的 44 文件组 `pytest-automation-combined-01` 为 1055 passed / 1 skipped（268.74 秒）；加入续租后五文件组 `pytest-automation-renewal-01` 为 148 passed（24.81 秒），覆盖长任务跨原到期时刻仍只派发一次、过期/取消/接管/完成后不可续租，以及取消/接管导致本地目标停止。两组均为离线测试，4 条既有 lifespan 警告；skip 仍是 Windows symlink 权限，不计为通过。最终代码的同一 44 文件组需再次记录，不能借用续租前结果。

剩余门禁须保持阻塞：过期执行的原有可重派发路径必须先接入不确定请求回执，不能拿本批 lease 正确性替代防重复扣费证据。跨等待状态转换、父 Store 原子投影/取消传播及 RunRegistry 对账、空 continuation 完整性、逻辑发生标识和父子预算同样未关闭。不能称本批为 R8-A 最后一批，也不能进入 R8-B 或 PR。

最终同一 44 文件组 `pytest-automation-combined-final` 已退出 0：1062 passed / 1 skipped / 4 warnings。pytest 报告累计耗时 48631.03 秒（13:30:31），运行跨越会话日期切换；不将该墙钟累计当作性能基准。没有删除失败用例或放宽原断言。此结果仅关闭本批 Automation 所有权/终态/续租回归，不替代上述剩余 R8-A 门禁。未 Commit、Push、创建 PR、部署、调用外部模型或操作共享栈。

## 验收门禁小批：恢复队列完整性

用户授权“开始关闭验收门禁”。仍在原独立工作树，现有 34 路径变更保留。范围为 main.py、新 workflow_continuation.py、新 test_workflow_r8a_continuation.py、resume_ownership 测试及本任务卡，共五文件。先复现缺失/空队列导致未执行节点仍进入成功结算，再在恢复入口增加纯校验；不推断或补造队列，不触发重新执行。允许旧 V1 缺少 scheduler/queued，但已提供的集合须为合法节点 ID 列表，队列必须非空且无重复/已执行节点。Scheduler V2 的后续完整校验不替代、不放宽。

无新路由、SSE、数据迁移、依赖或 Provider 调用。损坏快照在运行观测写入和节点执行前以稳定错误码拒绝，保留原持久快照用于诊断。恢复正向夹具补齐显式待执行队列，不能继续用空 continuation 当作成功样例。最小验收为新 continuation 测试；之后运行 Scheduler/typed values/approval/client/retry/ownership 回归和受影响组合。回退恢复入口与校验模块须成对进行，不修改用户快照。此批不代替跨 Store 投影、父子预算和不确定请求门禁。

### 本批验收记录（2026-10-08）

- 反例首次运行的 12 个 setup error 是 basetemp 父目录不存在，不计作产品缺陷。
  换用已存在的独立审计目录后，原新增组为 10 failed / 2 passed，24.06 秒：
  空/缺失队列静默成功，其他非法队列未在观测写入前拒绝。
- 入口新增 validate_resume_queue，放在 RunRegistry 读取/写入前；不自动补造队列。
  对当前可执行节点、列表类型、重复项及 queue/executed 冲突做确定性校验。
  旧 V1 允许缺失 scheduler/queued，V2 原完整校验不放宽。
- resume_ownership 的正向夹具改为实际 suspend/mark_ready 后恢复，保留原所有权断言。
- continuation/resume_ownership/retry_runtime/typed_values/runtime_approvals/runtime_client_tools
  六文件组：178 passed，93.23 秒，4 条既有 lifespan 警告。
- 34 个 Python AST、1 个 JSON 解析通过；36 路径限定凭据/运行产物扫描零命中，
  git diff --check 通过。暂存为空。
- client 通过 npm ci --offline --ignore-scripts --no-audit --no-fund 安装缓存依赖，
  未变更 package.json/package-lock.json。npm run build 退出 0，3196 模块，
  Vite 阶段 29.32 秒；保留既有大 chunk 警告，未更改阈值。
- 全量 server/tests/ 已运行至输出 46% 后主动中止，退出 1；已出现多项失败和 setup error，
  未产生最终 pytest 汇总，不能填写精确总失败数或称为完成全量。
  使用 --basetemp=C:\tmp\modelmirror-r8-a-audit-20261007\full-continuation-20261008。
  本次全量门禁为失败/未完整执行；其他失败尚未逐项归因，完整重跑仍是剩余门禁。

用户另行批准 Coding Windows 独立小批。干净同基线工作树已复现 os.fchmod 缺失，
并在 codex/coding-windows-fchmod-20261008 独立修复；未并入本 R8 树，
不影响本次全量代码快照。该小批任务卡为 CODING_WINDOWS_FCHMOD_20261008.md。
Unix socket 平台失败仍单独记录，不以 fchmod 修复概括所有全量失败。

本批没有完成跨等待父状态转换、父 Store 原子投影及取消传播、RunRegistry 对账、
逻辑发生标识、父子预算、不确定外部请求恢复及独立预览/跨重启验收。
前端构建通过不代表这些门禁关闭。未提交、推送、PR、部署、真实模型调用或活表写入。

## 未完成验证重跑（2026-10-08）

用户要求重新完成未完成的验证。本次只运行验证、定位失败并追加结果，
没有修改生产代码、测试断言或依赖清单；不撤销上述 Windows 全量中断记录。

### 快照与隔离

- R8 基线仍为 e3ac455553c2d3065bcd8768d78b849c8477392a，加当前 36 路径覆盖。
  冻结归档 SHA-256：eeff7a56cdd6fb6da292a3c5f0d26740906aa402592860c4b7c2334d11289e6a。
  运行期间逐项哈希与工作树一致；本节仅在测试后补记。
- 复用已有本地 Linux 验证镜像
  sha256:8cd2dcf996cfd7c0d2bdf261d5d9358df528343ed9d625f0d101e768e202e8a7。
  Python 3.12.14，Node 24.18.0；已解析的固定 Python 依赖逐项匹配，无不匹配。
  Agency/RPG 缓存只在 package-lock 字节相同时复用，Agency worker 从冻结源码重新构建通过。
- 临时容器无网络、非 root、只读根目录，不挂载凭据或共享服务目录，不开放端口。
  证据写到仓库外 `C:\tmp\modelmirror-r8-verification-20261008`；无新生产依赖或镜像拉取。

### 后端全量与失败对照

完整运行 `python -B -m pytest server/tests/ -q -ra -p no:cacheprovider --tb=short`
并输出 JUnit：**8939 passed / 2 failed / 32 skipped / 34 subtests passed / 7 warnings**，
1787.19 秒，退出 1。运行到 100%，没有主动中止、排除失败测试或在运行中修改代码。
JUnit 包含子测试共 9007 项，failures=2、errors=0；日志、JUnit 和环境回执为
`r8-evidence/r8-linux-full-*`。

两项失败在冻结 R8 源上窄测再次复现（2 failed，7.87 秒）；
相同环境、相同测试在纯 Git 基线 e3ac4555 上为 2 passed（7.02 秒）。
因此不能归为既有基线失败或仅由全量顺序导致。

1. `test_workflow_r20_runtime.py::test_legacy_continuation_stays_on_scheduler_v1_after_waiting_again`：
   期望 V1，实际仍为 V2。测试通过 `require()` 返回值直接 `pop("scheduler")` 构造旧快照；
   本轮 Store 已返回深拷贝，该修改不再改变权威快照，因此未真正建立 V1 恢复前提。
   需用真实持久化的旧格式夹具重建前提，并保留原 V1 断言；本次未改测试，门禁未关闭。
2. `test_workflow_retry_xpert_cancel.py::test_new_recovery_run_is_cancelled_when_rebind_loses_lease`：
   辅助夹具只保存 retry_state、缺少 queue，新增入口完整性校验在 RunRegistry 写入前拒绝，
   因而尚未触发原 lease rebind 竞态。需补齐真实待执行队列后重跑原取消/lease 断言；
   不能删除队列校验或将预期异常改成通过来替代该验收。本次未改测试。

窄测日志为 `r8-evidence/r8-failure-focused-*`，纯基线对照为
`r8-evidence/r8-baseline-focused-02-*`。首次基线归档因验证脚本对空覆盖执行 tar append
导致不可读取，未进入 pytest；修正仓库外脚本后以新归档完成对照，不计作产品失败。

32 个 skip 含 Windows 专用路径、root/sidecar、专用语言服务器/Office/renderer 依赖及部署态 worker。
这些环境门禁没有被本次 Linux 全量证明；不将 skip 计为通过，也未修改条件。
先前 Windows 全量的所有失败未逐项重跑，Linux 完整结果不能宣称全平台全绿。

### 前端与独立 Coding

- 首次完整 typecheck 和测试暴露嵌入式 RPG card-replica 缺失 React 依赖，
  与当前 CI 要求安装该子包相符。仅用既有锁文件执行
  `npm ci --offline --ignore-scripts --no-audit --no-fund --prefix experiments/ai-rpg-engine/card-replica`，
  无网络、无脚本、无清单或锁文件变更。
- `npm run typecheck` 原命令重跑退出 0。
- `npm run test:run` 原命令完整重跑：153 文件通过、1 文件失败；
  **1194 passed / 2 failed**，405.47 秒。两项均在 `models.refresh.test.ts`。
  该 npm 脚本因 Vitest 失败不会执行后半段，因此另行执行原 Node 命令
  `node --test server-headers.node.mjs scripts/build-typecheck.node.mjs`：**17 passed**。
- 目录文件窄测 72 passed / 2 failed。models.ts 按真实当前时间处理
  `baidu/ernie-4.5-vl-424b-a47b` 的 expiration_date=1791417600
  （2026-10-08 00:00 UTC），live 从 574 变为 573、expired 从 12 变为 13；
  测试仍写死旧总量与名单。相关源码/测试相对 HEAD 未改动，未执行目录外部刷新或顺带修复。
  未在另一干净前端工作树重跑，不把文件一致性说成独立全量基线通过。
- `npm run build` 退出 0，3196 模块，Vite 14.71 秒，保留大 chunk 警告。
  `npm run verify:help-images` 通过，23 篇注册文章；这不是浏览器可见性或人工验收证据。
- R8 34 个变更 Python 文件与独立 Coding 两个 Python 文件实际 `py_compile` 通过，
  缓存输出到仓库外；两个工作树 `git diff --check` 通过，限定凭据特征扫描零命中，暂存为空。
- Coding 独立树五文件 Linux 回归 39 passed / 7 Windows-only skipped，0.98 秒；
  Unix socket 与 Linux 符号链接缺口已补齐。该代码没有集成到本 R8 快照，
  Windows 无符号链接权限的跳过项仍未关闭。详见该树独立任务卡。

### 当前结论

本次完成了此前未跑完的 Linux 后端全量、前端完整检查及 Coding Linux 回归，
不是“门禁全部通过”：后端两项本轮契约迁移相关失败、前端两项日期断言仍阻塞。
本地 origin/main 已到 114461d3a63ef5c5e0dc8053e66a3ebf1356b0f3，比测试基线前进六个提交；
本次未 fetch 或集成，不能声称完成最新上游验收。
跨等待父投影、取消传播/跨 Store 对账、逻辑发生标识、父子预算、
不确定请求恢复及独立预览/跨进程恢复验收仍按原门禁保留。
未 Commit、Push、PR、部署、调用外部模型或进入 R8-B。

## 全量失败夹具修复小批（2026-10-08）

用户授权“开始修复”。本批限两个后端失败测试文件、models.refresh.test.ts、
新增 models.expiration.test.ts 及本任务卡，共五个文件；保留原工作树其他变更。
不修改生产代码、Store 安全边界、模型目录数据、依赖、共享栈或持久业务数据。
基线仍为 e3ac4555；前一节全量失败与纯基线对照为修复前证据。

旧调度测试改为修改合成临时目录中的磁盘快照并重新加载 Store，
覆盖缺失 scheduler 和显式 V1 两种旧格式，保留恢复后再次等待仍为 V1 的原断言。
lease 竞争夹具补齐待执行 queue、历史 executed、变量及 V1 标记，
增加竞争钩子恰好触发一次和权威任务已取消断言，不改变预期 lease 异常。
目录静态快照在模块导入前固定到 2026-10-07；新增独立的到期前、等于到期时刻、
到期后测试，分别重新加载真实目录模块，保持生产按当前时间判定的行为。

验收先跑原失败文件与日期边界，再跑恢复/调度/取消/所有权受影响组合、
前端全量、类型检查及生产构建。语法、Diff、覆盖路径与限定敏感特征检查随后执行。
风险仅为测试前提与时间隔离，不涉及公开接口或数据迁移；回退只撤回本批测试改动，
不能回退 Store 副本或队列完整性护栏。验证结果后续追加，不预先标记通过。

### 本批验证结果

- Windows 原失败所在两完整文件：15 passed，17.69 秒，4 条既有 lifespan 警告。
  两个旧格式调度场景均从磁盘加载后再次等待并保持 V1；lease 测试确认竞争钩子触发一次。
- 十文件组合（r20_runtime、retry_xpert_cancel、r8a_continuation、r8a_execution_fencing、
  r8a_resume_ownership、r8a_resume_projection、retry_runtime、typed_values、
  xpert_runtime_approvals、xpert_runtime_client_tools）：261 passed，58.51 秒，4 条既有警告。
  完整日志/JUnit：`r8-evidence/repair-backend.log`、`repair-backend-junit.xml`。
- 相同 Linux 隔离镜像、同固定依赖再次运行两个完整后端文件：15 passed，7.93 秒。
  归档 SHA-256：28e9614a43388462bb07a7fbdd49f727743eb3356c9bf0f6935f82119d0e80c6；
  环境、日志与 JUnit 为 `r8-evidence/r8-repaired-linux-*`。
- 目录两文件组最终 77 passed，4.46 秒。首跑新增日期测试误断言公开 Model 不包含的
  原始 expiration_date 字段，导致新增三项失败；按真实安全投影删除该不存在字段的断言，
  保留到期前/瞬间/后的 catalog_status、active 和 catalog_counted 检查，再运行原组通过。
  原目录数量、名单断言未放宽，生产目录数据与到期规则未修改。
- `npm run test:run` 完整退出 0：155 文件、1199 测试通过，395.64 秒；
  脚本后续 17 项 Node 测试通过，1.91 秒。证据：`r8-evidence/repair-client-tests.log`。
- `npm run typecheck` 退出 0；`npm run build` 退出 0，3196 模块，Vite 14.05 秒，
  保留原大 chunk 警告。证据：`repair-typecheck.log`、`repair-client-build.log`。
- 两个后端测试实际 py_compile 通过，缓存写到仓库外。
  五文件限定凭据特征扫描零命中，git diff --check 通过，无暂存。
  与上一轮完整后端快照对比，12 个生产变更文件哈希全部一致，本批只改测试与任务卡。

本批关闭四项已知失败的修复验证，不将重点回归与上一轮全量数字拼成“全量通过”。
修复后近九千项后端全量未再次运行；前次 8939 passed / 2 failed / 32 skipped 仍是历史完整结果。
跨平台 skip、最新上游集成以及 R8-A 原剩余实现/预览门禁保持未关闭。
未修改 Coding 独立修复树，未 Commit、Push、PR、部署或真实 Provider 调用。

### 修复后完整后端重跑

用户明确要求“重跑”，随后继续等待。本次完整运行原命令，不筛选测试、不中断、
不在运行中修改代码。冻结基线 e3ac455553c2d3065bcd8768d78b849c8477392a 加 40 路径覆盖；
归档 SHA-256 为 98ece9130833508ab165efed87bdf8cb3499043b375527b1ad8bb47f01766106。
运行结束后逐项核对 40 文件哈希全部一致；本节是在核对之后追加的记录。

- 沿用上述本地 Linux 镜像、固定依赖、无网络、非 root、只读根文件系统，
  Agency worker 从冻结源码构建退出 0；独立测试容器正常退出并自动清理。
- 完整执行 `python -B -m pytest server/tests/ -q -ra -p no:cacheprovider --tb=short`
  并输出 JUnit：**8942 passed / 32 skipped / 34 subtests passed / 7 warnings**，
  1683.10 秒（28:03），退出 0。
- JUnit 共 9008 项（含子测试），failures=0、errors=0、skipped=32。
  原 lease rebind 失败用例、缺省 scheduler 和显式 V1 的再次等待用例均通过且未跳过。
  相对修复前全量，通过数增加 3：原两个失败转绿，另增加一个旧格式参数化场景。
- 完整日志、JUnit、环境和退出回执位于
  `C:\tmp\modelmirror-r8-verification-20261008\r8-evidence\r8-linux-full-rerun-*`；
  冻结源和覆盖哈希清单位于 `full-rerun-source`。
- 32 个跳过项仍为平台、专用 worker/sidecar、root、语言服务器及 Office/renderer 依赖条件，
  没有改动跳过逻辑，不能记为通过。7 条警告仍单列，不声称零警告。

至此关闭当前冻结代码的 Linux 后端全量重跑门禁；上一节“尚未重跑”保留为历史状态。
前端完整测试与构建使用上一批已核验结果，本次未重复运行，也未修改对应代码。
最新上游集成、跨平台未覆盖项、跨 Store/等待投影、父子预算、逻辑发生标识、
不确定请求恢复及独立预览验收仍不能由全量绿色替代，R8-A 不据此宣布整体完成。
本次只追加验证记录，无生产代码变更、提交、推送、PR、部署或外部模型调用。

## AgentTask 终态与显式重排小批

用户授权继续关闭剩余项。本批限定 agent_tasks.py、handoff_executor.py、
新增 test_workflow_r8a_agent_task_terminal.py 及本任务卡，共四文件。
基线与既有未提交变更保持不动；不集成上游、不部署、不调用真实模型。
核对恢复父投影时发现前置缺口：AgentTask 仅保护 cancelled，completed/failed 可被普通更新覆盖；
读出对象及嵌套 metadata 保留可变引用；显式 Handoff 重排分两次持久化父任务和 Handoff。

先用真实临时 Store 复现，再收紧终态、隔离 Task 引用、取消幂等和显式重排的单 Store 提交。
失败任务只可经既有显式 requeue 重新开始，completed/cancelled 不得重排。
无新增状态、路由、数据格式或依赖；风险为收紧内部调用契约及重排持久化边界。
最小验收为新增反例组，之后回归 AgentTask、Handoff、Goal、恢复入口及 Runtime。
回退须一起恢复 Store 与 Executor 重排调用，不删除或修改用户任务快照。
本批不证明跨 Store 投影、worker 派发/取消、重排后的旧 worker fencing 或外部审计恰好一次，
这些边界继续独立验收；不会因此开放 R8-B 或新增长运行能力。

### 本批结果（2026-10-08）

- 修改前新增 40 条反例：33 failed / 7 passed；失败包括 completed/failed 被覆盖、
  返回对象与嵌套元数据引用泄漏、重复取消误报审计、父任务与 Handoff 分步重排、
  持久化失败后内存状态提前改变。7 条通过仅对应此前已有的 cancelled 更新保护。
- AgentTask 的创建、查询、列表、更新和 occurrence 返回独立快照，传入嵌套元数据也复制。
  三种终态的普通更新均为无副作用 no-op；取消只为实际赢得状态转换的一方记录审计。
- 显式 requeue 在同一锁和同一次 JSON 快照提交中更新 Task 与 Handoff；
  completed/cancelled 或绑定其他 Handoff 的父任务被拒绝。失败 Task 仍可经该显式入口重排，
  Executor 不再补做第二次 Task 持久化，保留既有重排与 Task 更新事件。
- 更新、取消和重排使用副本提交；写入前或临时文件 replace 失败时恢复内存旧状态，
  不产生成功事件。此结论不覆盖文件系统未知提交结果或外部事件 Store 恰好一次。
- 最终新增反例为 52 条，增加终态读引用、事件 await 窗口竞争、重复重排、
  绑定错配及 replace 故障；测试不调用模型，不写业务表。

冻结源仍为 e3ac455553c2d3065bcd8768d78b849c8477392a 加 42 路径覆盖，
归档 SHA-256 为 05b13b22cb9cd6659dd530f37b90dc94ad8bac9840f5ad83fa2618cc5735b969，
路径为 `C:\tmp\modelmirror-r8-verification-20261008\agent-task-terminal-source`。
验证后 42 项文件哈希与工作树全部一致；本节在核对之后追加。

最终回归命令为 `python -B -m pytest <以下十五个完整文件> -q -ra -p no:cacheprovider --tb=short`
（Windows 使用独立 basetemp；两平台均输出 JUnit）：

```text
server/tests/test_workflow_r8a_agent_task_terminal.py
server/tests/test_xpert_runtime_agent_tasks.py
server/tests/test_xpert_handoff_executor.py
server/tests/test_workflow_agent_task_node.py
server/tests/test_workflow_handoff_coordinator.py
server/tests/test_workflow_retry_xpert_cancel.py
server/tests/test_xpert_runtime_approvals.py
server/tests/test_xpert_runtime_client_tools.py
server/tests/test_xpert_conversation_goals.py
server/tests/test_xpert_runtime_run_registry.py
server/tests/test_skill_creator_middleware_handoff.py
server/tests/test_workflow_r8a_execution_fencing.py
server/tests/test_workflow_r8a_resume_projection.py
server/tests/test_workflow_r8a_resume_ownership.py
server/tests/test_xpert_publish.py
```

- Windows：379 passed，232.63 秒；Linux：379 passed，22.59 秒。
  两者均 0 failed / 0 skipped，保留 4 条既有 lifespan 弃用警告。
  Linux 沿用固定镜像及匹配依赖，断网、非 root、只读根目录，临时容器已退出并自动删除。
- 命令清单曾误写不存在的 test_xpert_store.py，导致两平台各一次未收集测试；
  核对后改用实际含 XpertStore 测试的 test_xpert_publish.py，再完整运行上述十五文件。
  未删除、跳过或放宽任何测试断言；失败尝试与最终结果分开保留。
- 证据目录为 `C:\tmp\modelmirror-r8-verification-20261008\r8-evidence`，
  Windows：`r8-agent-task-terminal-windows-v2-junit.xml`；
  Linux：`r8-agent-task-terminal-linux-v2-{pytest.log,junit.xml,environment.json,result.json}`。
- 三个 Python 文件 `py_compile` 通过（缓存输出仓库外）；
  `npm.cmd run build` 退出 0，3196 模块、Vite 26.21 秒，原大 chunk 警告仍在。
  `git diff --check` 通过，本批四文件限定凭据特征扫描零命中，暂存为空。

### 剩余门禁，不得据本批通过跳过

1. 跨等待类型的父状态投影及取消传播、跨 Store 对账；当前修改只保护 AgentTask 权威终态，
   不保证 Handoff/RunRegistry 已同步，也不保证旧 worker 停止派发。
2. 显式重排后的执行发生标识与旧执行者隔离，父子预算和不确定外部请求恢复。
3. 单实例运行目录占用保护、独立预览和跨进程崩溃/重启验收。
4. 最新上游集成、独立 Coding 修复边界及跳过项核对；合并后的完整后端回归。
   前述 8942 passed 属于本批修改前冻结版本，本批 379 项不能替代修改后的全量。
5. 最终面向用户的恢复/重排说明及同基线人工验收，随后才评估 R8-A 提交条件。

本批四文件目标已通过针对性验收，R8-A 整体门禁仍未关闭。
未 Commit、Push、PR、部署、操作共享栈、调用外部模型或进入 R8-B；
独立 Coding 修复树与主工作区保持不动。

## 审批与 Client Tool 父状态投影小批

用户授权继续。基线、42 路径未提交改动及独立 Coding 树保持不动。
本批限 main.py、agent_tasks.py、execution_store.py、test_workflow_r8a_resume_projection.py
及本任务卡，共五文件。先加强真实恢复入口反例，不调用外部模型、不部署或集成上游。

已定位：两个恢复回调对非成功直接返回，避免了错误完成，却未把跨 approval/client_tool
的等待类型及子取消同步到父 Task/Handoff。成功回调还分步写父任务和 Handoff。
本批统一这三类结果的父对投影，在 Task 锁内取得 Execution 快照护栏后同步提交，
护栏内不 await；校验来源快照、父子绑定、终态与显式重排时间，持久化失败回滚内存。
已完成仍需 existing RunRegistry/Automation 路径，完整跨 Store 对账并非本批完成声明。
timer/node_retry/agent_handoff 等其他等待类型保留既有非成功处理，不伪造新的公开状态。

验收：两入口交叉等待、取消和完成；错配、迟到、终态保护、重复投影、replace 失败及
重新加载；随后回归审批、Client Tools、Handoff、Goal、执行快照护栏与前端构建。
不改数据格式、SSE 或 Planner 能力；只新增私有安全投影摘要。
回退一起撤回两恢复入口及 Store 投影/护栏，不删除用户快照。

### 本批实现与验收结果

此前生产编辑的自动审批两次超时，用户确认允许后恢复写入；未绕过授权或改动范围。
加强原两入口反例后为 6 failed / 15 passed：两次跨等待类型、两次等待回执缺失、
两次子取消未投影。原错误成功保护依旧成立，不能据此误称父状态已同步。

- 新增 Execution 快照投影护栏：取得父 Store 的异步锁后重新核对整个来源快照，
  同线程执行同步提交段，期间不 await；来源变为 ready、新 lease、取消或再次等待均拒绝旧投影。
- AgentTaskStore 统一处理 approval/client_tool 等待、completed 和 cancelled：
  父 Task/Handoff 同次持久化，并保存 task/run/revision/wait 的受限回执。
  子取消映射为 Task cancelled、Handoff dead_letter 与 WORKFLOW_CANCELLED，
  不新增公开状态；父已终止、绑定错配或检测到显式重排旧来源时不覆盖。
- 成功结果读取持久执行的 result，不信任 SSE 片段中的替代正文。
  保留已有完成元数据、原结果长度及事件类型；事件正文不包含结果全文。
- 重复投影不再次写盘或重复记录事件；replace 失败恢复整对内存状态，
  提交后在审计前中断，重新加载后可幂等重放。此处没有承诺审计事件恰好一次。
- 两个 main 恢复入口调用同一投影方法，移除原来分步修改父任务与 Handoff 的代码。
  没有修改其他恢复入口或擅自把 timer/node_retry/agent_handoff 映射成某种审批等待。

新增 34 个参数化反例，加上原文件 21 项，共 55 项；与上一批 52 项合计 107 passed。
随后冻结源码运行完整受影响组，不在测试期间修改生产或测试文件。
基线仍为 e3ac455553c2d3065bcd8768d78b849c8477392a，42 路径覆盖归档为
`C:\tmp\modelmirror-r8-verification-20261008\parent-projection-source`，SHA-256：
76d34bc34c56da0faeb920598e3427ce47273f0b6c1f31495159b214f3c86276。
与上一批归档比较，恰好只有本批约定的五文件变化；本节追加前全部 42 项哈希一致。

回归沿用上一节十五个完整文件，另加入 xpert_runtime_automations、r8a_outcomes、
r8a_terminal_commit、workflow_retry_runtime、workflow_retry_security_regressions，
均位于 server/tests/ 且带 test_ 前缀与 .py 后缀。命令仍为
`python -B -m pytest <二十文件> -q -ra -p no:cacheprovider --tb=short`，输出 JUnit。

- Windows：608 passed，156.32 秒；Linux：608 passed，17.63 秒。
  两者均 0 failed / 0 skipped，保留 4 条既有 lifespan 弃用警告。
  Linux 为既有固定镜像、匹配依赖、断网、非 root、只读根文件系统，测试容器正常退出。
- 证据位于 `C:\tmp\modelmirror-r8-verification-20261008\r8-evidence`：
  `r8-parent-projection-windows-junit.xml`，以及
  `r8-parent-projection-linux-{pytest.log,junit.xml,environment.json,result.json}`。
- 四个变更 Python 文件 py_compile 通过（缓存在仓库外）；
  npm.cmd run build 退出 0，3196 模块、Vite 12.81 秒，保留大 chunk 警告。
  git diff --check 通过；限定敏感特征扫描的一条命中是历史 task-terminal 文件名，
  核对为非凭据，不以该误报改动证据文件。

本批关闭两个恢复入口的 Task/Handoff 状态对投影及子取消向父对传播，
并非关闭全部“跨 Store 状态投影与取消传播”：RunRegistry/Automation 对账、
父取消向子执行传播、其他等待类型、通用发生代次 fencing、父子预算及不确定请求仍未完成。
显式重排时间/运行绑定检查不能替代后续发生代次契约，未宣称旧 worker 全路径隔离。
未运行本批修改后的完整 server/tests/；最新上游集成、独立预览、真实崩溃恢复及帮助验收
仍按原路线保留。未 Commit、Push、PR、部署、触碰共享栈、调用外部模型或进入 R8-B。

## Task 取消与 Handoff 派发边界小批

用户授权开始处理；仍在 e3ac4555 的既有独立工作树，保留此前 42 路径改动。
范围限 main.py、agent_tasks.py、handoff_executor.py、新增
test_workflow_r8a_parent_cancel.py 及本任务卡，共五文件。
目标：取消 API 不伪造终态；Task 与其未终止 Handoff 同次持久化取消决定；
取消后的旧队列不可领取，派发前和返回边界复核取消，不将迟到结果重新提交为成功或重试。
不修改公开状态、Store 格式、SSE、Planner 或 Automation；运行中子工作流的取消传播、
完整 lease fencing、跨 Store 对账及不确定请求回执另批收口，不能由本批代替。
风险：本地持久化和运行调度，无外部请求或真实凭据。先跑确定性反例，再跑上一批
二十文件及新增测试、语法与前端构建；全量及预览仍为独立剩余门禁。
回退应同时撤回三处取消边界与对应测试，不删除或恢复任何用户运行数据。

### 取消边界小批结果

反例先行：默认 pytest 临时目录拒绝访问，属于环境阻塞；改用仓库外新目录后运行。
首个夹具遗漏 HandoffExecutionResult 必填身份字段，修正后重新取证为 17 failed / 2 passed；
生产修复前的有效证据为 r8-parent-cancel-red-02.xml，不把夹具异常当作生产反例。

- cancel_task 在同一个 Store 锁和一次持久化中提交 Task cancelled 与未终止 Handoff
  dead_letter/HANDOFF_CANCELLED，撤销派发资格和 lease；保留终态 Handoff 与其他任务。
  持久化失败回滚整个内存变更；已提交但审计中断，重载后重复取消不再写盘。
- 自动列表返回副本并排除终态或缺失父任务，实际领取再次核对父状态。
  已取消任务不可新增 Handoff，迟到 metadata 不得重新开启派发。
  失败任务仍保留既有人工修复/显式重排行为，没有扩大本批为禁止全部终态创建交接。
- Executor 在目标调用前、返回后以及失败处理入口复核取消，已取消时不提交迟到结果、
  等待或自动重试，并同步本次已知的 Task/Handoff RunRegistry 记录。
- 取消 API 只投影 Store 的真实取消结果；完成或失败胜出的任务不被改成 cancelled。
  重复取消使用原取消原因；仅同步由本次取消标记的 Handoff，不重写历史终态。
  更新 RunRegistry 时先按 source_id 过滤，避免其他任务占满 200 条窗口导致漏更新。

新增文件共 24 项，含 5 个补充反例：终态兄弟与无关任务保留、审计中断重载、旧队列
快照、200 条分页边界、迟到 metadata。与此前两批重点组合为 131 passed。
第一次广回归 Windows 632 passed，Linux 631 passed / 1 failed：定位为本批 source_id
过滤误加到相邻只读函数；Windows 创建时间相同使分页夹具偶然通过。
已撤回相邻函数改动、修正实际更新函数，并固定测试旧记录时间、先断言其确在全局
200 条之外。没有降低断言或跳过失败项，两端均重跑相同二十一文件。

最终源码冻结：`C:\tmp\modelmirror-r8-verification-20261008\parent-cancel-final-source`，
基线 e3ac455553c2d3065bcd8768d78b849c8477392a，43 个覆盖路径，归档 SHA-256：
6476cd6245844aab2083c09c5425c31d74d3c32963dbcbb1cca4e046264f4236。
与上一批归档相比仅本批约定五文件变化；本节补记前 43 路径哈希全部一致。

- 二十一文件 pytest：上一批二十文件加 test_workflow_r8a_parent_cancel.py，仍使用
  `python -B -m pytest <文件列表> -q -ra -p no:cacheprovider --tb=short`，独立 basetemp 和 JUnit。
  Windows 632 passed，167.81 秒；Linux 632 passed，18.62 秒；均无失败/跳过，4 条既有
  lifespan 弃用警告。Linux 固定镜像、断网、只读根、非 root、依赖无不匹配。
- 最终证据在同一 r8-evidence 目录：r8-parent-cancel-windows-final-junit.xml 及
  r8-parent-cancel-linux-final-{pytest.log,junit.xml,environment.json,result.json}；首次失败证据保留。
- 四个变更 Python 文件 py_compile 通过，缓存位于仓库外。npm.cmd run build 通过，
  3196 模块、Vite 13.83 秒，仅既有大 chunk 警告。git diff --check 通过，暂存区为空，
  本批五文件受限凭据特征扫描无命中。无新增依赖、Store 数据、构建产物或密钥进入 Diff。

此处取消回执不表示外部请求已撤回，不是完整 lease/发生代次 fencing：进入目标函数后
其内部仍可能有 await 和外部操作；结果复核与后续跨 Store 提交之间也不是原子事务。
运行中 WorkflowExecution 的父取消传播、等待恢复队列对账、全部迟到 worker 路径、
Automation/RunRegistry 全面对账、预算及不确定请求、最新上游集成、最终全量和独立
预览仍未关闭。本批未运行修改后全量 server/tests/，不能沿用历史全量作当前通过证明。
未 Commit、Push、PR、部署、操作共享栈、调用模型或进入 R8-B。

## 父 Task 取消向持久子执行传播小批

用户授权继续收口；保留 e3ac4555 独立工作树的 43 路径既有改动。
限 main.py、execution_store.py、test_workflow_r8a_parent_cancel.py、新增
test_workflow_r8a_child_cancel.py 及本任务卡，五文件。运行中子执行创建时已保存
agent_task_id/handoff_id，不能仅依赖暂停后才出现的 Handoff.workflow_task_id。
目标：按真实父对绑定查找全部子执行，父取消后取消未终止子执行并通知本地事件及
当前/历史 RunRegistry；新建和恢复入口复核父取消，覆盖取消先于子登记的窗口。
已完成子结果保留；跨 Store 提交中断可重放，不承诺跨 Store 原子事务或撤回外部请求。
只接入现有 Task 取消、经典 Handoff 取消及超时调用点，不改 Goal/Automation 授权。
先补确定性反例，再跑前批二十一文件与本批新增文件、语法和前端构建；后续最终全量、
上游集成及独立预览仍单列。风险为持久化与取消调度，不读取密钥、不外发或部署。
回退同时撤回取消传播查询和入口复核；不删除用户快照、不重开已取消任务。

### 父子取消传播结果

先补真实运行入口与临时 Store 反例。首次夹具的 timer/node_retry 未带 resume_at、
重载引用了不存在的 storage_path；修正夹具后重新取证为 14 failed / 3 passed，
有效红测证据为 r8-child-cancel-red-02.xml。测试没有 Provider 或业务数据调用。

- 执行 Store 按 agent_task_id 与当前父 Task 拥有的 handoff_id 双重匹配子执行，
  不通过最多 1000 条的通用分页列表猜测关联。返回副本；取消时在同一个执行 Store 锁
  内匹配和调用既有终态安全 cancel，不改变公开状态或快照格式。
- Task 取消 API、经典等待 Handoff 的取消分支及 Handoff 超时调用统一传播函数。
  先提交匹配子执行的取消，设置本地 cancel_requested/provider_cancel_event/pause_event，
  再投影当前及 previous_run_ids；缺失的内存 Registry 不妨碍持久取消。
- 新建执行在预检前核对父取消，登记后再核对一次。后一次覆盖父取消扫描时子执行
  尚未登记的窗口。恢复使用持久 execution 的 metadata，不信任替代运行元数据；
  父取消已落盘但子取消中断，显式重复取消或进入恢复时可继续对账并失效旧 lease。
- 已完成、失败、拒绝的子执行不改写；跨父任务、错误 Handoff 绑定不取消其他执行。
  迟到外部请求并未被宣称撤回，已完成结果也不被抹除。

补充故障注入曾复现 1 failed / 19 passed：Registry 阻塞时第二个子执行尚未取消。
已调整为同一同步段完成持久取消，随后设置全部本地信号，最后才 await Registry。
复核超时旧逻辑又复现 2 failed：仅凭 xpert_run_id 把已完成/失败 Run 改为 cancelled。
已移除该直接覆盖，统一依据持久子执行决定投影。仅有旧 Run ID、无持久执行证据时
不伪造已取消；这类历史展示对账保留为后续门禁，不能靠改状态消除不确定性。

新增文件最终 22 项，涵盖七种运行/等待状态、旧 lease、历史 Run ID、终态保留、
错绑、提交中断重放、启动登记竞态、重载恢复、1000 条分页、Registry 阻塞、经典取消
及超时入口。原三文件重点组合为 113 passed；补充超时与 Coordinator 检查为 25 passed；
最终完整受影响回归为前批二十一文件加 test_workflow_r8a_child_cancel.py，共二十二文件。

最终冻结目录：`C:\tmp\modelmirror-r8-verification-20261008\child-cancel-final-source`。
基线 e3ac455553c2d3065bcd8768d78b849c8477392a，44 个覆盖路径，SHA-256：
d49ad8da07fca15a29ad699d6e36397fbafc196446aafafe730aa524d8119f8e。
与上一批归档相比仅本批约定五文件变化；本节补记前 44 路径哈希全部一致。

- `python -B -m pytest <二十二文件> -q -ra -p no:cacheprovider --tb=short`，使用仓库外
  独立 basetemp、JUnit。Windows 654 passed，180.85 秒；Linux 654 passed，18.66 秒。
  均无失败/跳过，4 条既有 lifespan 警告。Linux 使用既有固定镜像，断网、非 root、
  只读根文件系统，依赖匹配，容器正常退出。
- 证据在 r8-evidence：r8-child-cancel-windows-final-junit.xml 与
  r8-child-cancel-linux-final-{pytest.log,junit.xml,environment.json,result.json}。
- 四个变更 Python 文件 py_compile 通过，缓存在仓库外；前端 npm.cmd run build 通过，
  3196 模块、Vite 13.23 秒，仅既有大 chunk 警告。git diff --check 通过，暂存区为空；
  本批五文件受限凭据特征扫描无命中，无运行数据、构建产物或凭据纳入 Git。

限制：这是父 Task 到直接持久子执行的取消链路，不是完整跨 Store 原子事务。
每个子执行分别持久化，后续写盘失败时需恢复/重放；未新增后台全树清扫器。
经典父工作流若在自身取消后、子 Task 取消前崩溃，恢复取消树所需关系仍须后续对账；
不能用本批重放测试冒充这一路径已闭环。Goal/Automation 取消树、嵌套委托、完整发生代次
和旧 worker fencing、预算、不确定请求、最新上游集成、最终全量、独立预览及帮助验收
仍未关闭。未运行本次修改后的全量 server/tests/，不沿用旧全量结果。
未 Commit、Push、PR、部署、触碰共享栈、调用模型或进入 R8-B。

## 经典父取消中断对账小批

继续使用 e3ac4555 / codex/meta-planner-r8-a-durable-kernel，保留既有 44 路径改动。
仅修改 main.py、execution_store.py、workflow_handoff_coordinator.py、新增
test_workflow_r8a_cancel_reconciliation.py 及本任务卡。父执行取消清除 wait_id，
导致中断后失去子 Task 关联；Handoff 的 workflow_task_id 在子流程等待时会改写，
不能用它恢复父关联。本批在父取消的同一次持久化中保存私有取消待办，复用既有
Task/子执行取消入口，成功后确认待办；现有 Coordinator 重启后幂等对账。
不新增状态、公开路由、SSE、Planner 能力、依赖或 Store 格式版本；旧快照无待办时
不猜测关系。仅经典 agent_handoff 取消，Goal/Automation、嵌套授权与预算不在本批。
先复现父终态/子任务及子执行未取消两类断点，再覆盖重复、终态、错绑、分页和写盘故障。
执行新文件 pytest、前批二十二文件组合、后端语法及前端构建；使用仓库外隔离证据目录。
高风险点为持久状态投影；取消待办只源自 Store 当前等待状态，不能由输入 metadata 注入。
回退需同时关闭对账入口，保留待办读取，不删除快照或恢复已取消任务；不宣称跨 Store
原子提交或已撤回外部请求。最新上游、最终全量及独立预览仍需单独完成。

### 经典取消中断对账结果

初始两条真实入口反例均失败（r8-cancel-reconcile-red-01.xml）：父 Workflow 已取消，
重载后重复取消仍留下 pending Task 或 waiting 子执行。没有用新接口不存在作为红测。

- 在执行 Store 的既有终态持久化内保存 pending_handoff_cancellation_id，再清理
  wait/continuation；取消终态与关联同次提交。新建执行时剔除传入的同名 metadata。
  私有待办不出现在 serialize_public、SSE 或新业务路由中。
- 经典取消入口和现有 Handoff Coordinator 共用同一个对账函数，按持久 wait ID 找到
  Handoff/Task，复用 Task 取消及子执行传播；完成后单独确认待办。不用可被子暂停改写的
  Handoff.workflow_task_id，也不猜测旧 Run ID。重复取消不再因父已 cancelled 而跳过待办。
- 对账重新读取 Store，不信任传入的旧快照。取消队列不受全局 1000 条分页截断影响；
  关联缺失或取消中断时保留待办，不确认成功，单项异常不阻断其他待办。已经完成/失败的
  子 Task 与其终态结果不被取消覆盖。这里仍是幂等跨 Store 对账，而非跨 Store 事务。
- 补充 Registry 阻塞反例曾失败（r8-cancel-reconcile-registry-red.xml）。调整为先发
  本地停止信号、提交 Task/子执行取消，再投影 Registry，防止展示层延迟阻塞持久取消。
  确认待办时写盘失败，Store 保持 fail-closed；重载后读取尚未确认的待办继续对账。

新增文件最终 19 项，覆盖三个中断阶段、确认写盘失败、取消提交失败、缺失关联、重复
回调、终态竞争、无关任务、分页、注入、其他等待类型、生产 Coordinator 接线与旧快照。
本批加前批子取消/Coordinator 三文件重点 44 passed；完整受影响回归为前批二十二文件
加 test_workflow_r8a_cancel_reconciliation.py，共二十三文件。

最终源码冻结在 `C:\tmp\modelmirror-r8-verification-20261008\cancel-reconcile-final-source`，
HEAD e3ac455553c2d3065bcd8768d78b849c8477392a，45 个覆盖路径，归档 SHA-256：
e708644405e817be1d32fdae7f4b7e07a1e74054804dc5092a06311bfdb875a5。
与前批冻结相比仅约定五文件有变化，本节补记前全部 45 路径哈希一致。

- `python -B -m pytest <二十三文件> -q -ra -p no:cacheprovider --tb=short`，独立
  basetemp/JUnit：Windows 673 passed，185.19 秒；Linux 673 passed，18.28 秒。
  均无失败/跳过，4 条既有 lifespan 警告。Linux 固定本地镜像、断网、非 root、只读根，
  依赖匹配且退出码 0。完整文件清单保存在 Linux pytest.log 的 COMMAND 中。
- 证据目录仍为 r8-evidence，文件为 r8-cancel-reconcile-windows-final-junit.xml 与
  r8-cancel-reconcile-linux-final-{pytest.log,junit.xml,environment.json,result.json}。
- 本批四个 Python 文件 py_compile 通过，缓存位于仓库外；npm.cmd run build 通过，
  3196 模块、Vite 14.27 秒，仅既有大 chunk 警告。git diff --check 通过；暂存区为空，
  本批五文件受限凭据特征扫描无命中。

此证据包含真实 Store 重载与故障注入，但不是独立预览进程的真实断电/崩溃验收。
旧版本已清除关联的 cancelled 快照不自动补关系，仍保留人工核对缺口；待办关联缺失时
不宣称子任务已取消。Goal/Automation 取消树、发生代次与完整旧 worker 隔离、预算及
不确定外部请求、最新上游集成、修改后的全量 server/tests/、独立预览和帮助验收仍未关闭。
本批未 Commit、Push、PR、部署、触碰共享栈、外部模型调用，也未进入 R8-B。

## Goal 取消传播前置：终态与快照隔离

继续核对取消链，确认 goals.py 的 get/list 返回内部可变对象，replace_plan、update_goal、
update_step、mutate_steps 均未保护 completed/cancelled。Coordinator 的取消状态检查在锁外，
规划及步骤回调可跨 await 到达迟到提交。本批先稳定 Goal 决策源，不同时扩大子任务传播。
保留 e3ac4555 工作树既有 45 路径；仅修改 goals.py、goal_coordinator.py、新增
test_workflow_r8a_goal_terminal.py 及本卡，共四文件。目标是返回隔离快照、在锁内保护
Goal 终态、取消原子更新本 Store 的 Goal/步骤，并使迟到规划/步骤结果不能复活 Goal。
失败回调及写盘失败不能污染当前内存；现有显式 retry/reassign/skip 在非终态仍可用。
不新增公开状态/路由/Store 格式，不新增 Planner 能力，不更改 Goal 附件或资源授权。
本批不宣称取消已传播到 Task/Handoff/Workflow，也不闭合所有分派或 Registry 竞态。
先复现迟到规划成功/失败与终态写入，再验证取消/完成竞争、快照别名、回调/持久化失败、
重复取消和旧计划恢复。运行新文件、Goal/API 测试、前批二十三文件及语法/前端构建。
风险为状态语义和持久写入；不涉及网络或凭据。回退只撤回本批调用及 Store 保护，
不回退已取消状态或删除用户快照；最终全量、上游集成、预览和帮助门禁仍单列。

### Goal 终态与快照隔离结果

初始 12 项反例全部失败，证据 r8-goal-terminal-red-01.xml：迟到规划成功把取消目标
改为 awaiting_review，迟到失败改为 needs_attention；四种写入口可改写终态，读取别名
和中途抛错的 mutation 可污染内存。修复后的最小 Goal/API 组合先为 18 passed。

- GoalStore 对 create/get/require/list 和写入结果返回深副本；步骤 patch 及 mutation
  保留的对象也与 Store 隔离。回调在副本上工作，失败不暴露半完成状态；提交前复制，
  持久化异常恢复此前内存记录。未替换 JSON 存储协议或宣称解决断电 durability。
- replace_plan/update_step/mutate_steps 在锁内拒绝 completed/cancelled；update_goal
  不允许终态业务字段改写，仅保留重启后 Registry run_id 重新绑定。相同终态空更新
  不改变时间。取消由 Store 在同一锁、一次持久化内更新 Goal/未完成步骤，保留完成和
  跳过步骤、task_id/handoff_id。完成抢先提交时取消返回原结果，不反向覆盖。
- Coordinator 在规划和步骤处理遭遇终态竞争时读取并返回当前决定；迟到成功、失败
  都不能复活 Goal。原暂停、人工 retry/reassign/skip 在非终态保持可用。
- 复制契约检查另发现本批引入的返回值回归：首次绑定 Registry 后七个管理入口仍返回
  空 run_id 的旧快照（r8-goal-terminal-response-red.xml，7 failed）。已统一为操作后
  返回当前 Store 快照，没有恢复可变别名；重新冻结源码并重跑全部受影响回归。

新增文件最终 37 项；与原 Goal/API 六项组合为 43 passed。前批二十三文件加新文件，
完整二十四文件的最终结果为 Windows 710 passed / Linux 710 passed，均无失败或跳过，
4 条既有 lifespan 警告。分别耗时 193.28 秒、18.43 秒。
命令为 `python -B -m pytest <二十四文件> -q -ra -p no:cacheprovider --tb=short`，
独立 basetemp/JUnit，文件清单在 Linux pytest.log 的 COMMAND；不是全量 server/tests/。

最终冻结：`C:\tmp\modelmirror-r8-verification-20261008\goal-terminal-final-02-source`，
HEAD e3ac455553c2d3065bcd8768d78b849c8477392a，48 个覆盖路径，SHA-256：
99ec09d0d1cd60b4a641897d47b660e36a7e03d019ef28752399ecdabcab6861。
相比上一批仅约定四文件变化；本节补记前 48 路径哈希全部一致。此前 703 passed 的
中间归档保留，但不作为最终源码证据。最终 JUnit 为
r8-goal-terminal-windows-final-02-junit.xml 及 r8-goal-terminal-linux-final-02-junit.xml；
Linux 同名前缀附 pytest.log、environment.json、result.json。均位于既有 r8-evidence。

引用检索补跑 test_xpert_context.py 全文件，验证显式 Goal 附件传递：Linux 13 passed，
6.56 秒。Windows 长 basetemp 初次 6 failed / 7 passed，文件操作报 FileNotFoundError；
样例路径 285 字符且父目录存在。保持源码/测试不变，仅改为未使用的短 basetemp
`C:\tmp\r8gc-1008-final` 后，13 passed，10.90 秒。归因于本机深临时目录的路径长度
敏感性，不修改无关文件模块、不跳过测试。长路径能力未因此被证明；首次失败保留在
r8-goal-context-windows-final-junit.xml，短目录通过证据为
r8-goal-context-windows-short-final-junit.xml，Linux 为 r8-goal-context-linux-final-*。

三个 Python 文件 py_compile 通过；最终 npm.cmd run build 通过，3196 模块、Vite
13.51 秒，仅既有大 chunk 警告。git diff --check 通过，暂存区为空，本批四文件受限
凭据特征扫描无命中。Linux 使用既有固定本地镜像、断网、非 root、只读根，无依赖漂移。

限制：本批仅保护 Goal 决策源，不是 Goal 取消树闭环。取消期间尚未登记/正在派发的
Task/Handoff、跨 Store 传播及后台对账、全部 Registry 迟到投影仍需下一批处理。
Automation 取消、完整旧执行者 fencing、父子预算及不确定外部请求、最新上游、最终
全量、独立预览及帮助验收仍未关闭。不扩大附件授权或 Planner 能力，不进入 R8-B。
未 Commit、Push、PR、部署、修改共享栈或调用真实模型。

## Goal 取消树：登记竞态与重启对账

基于 e3ac4555、已有 48 个变更路径继续；本批仅 agent_tasks.py、goal_coordinator.py、
main.py、新增 test_workflow_r8a_goal_cancel.py 和本卡共五文件。已核对 Goal 取消不传递到
Task，且分派先设置 Handoff ready、后写 Goal 步骤关联。目标为取消持久关联的子任务，
并复用已有 Task/Handoff 到 durable Workflow 的取消链。登记中断不能靠列表分页或
易失内存判断完成；取消后的 Task 关联用于后台重放，终态子任务不撤销。

先用临时 Store 复现取消未传播、登记前后竞态和重启遗漏；再覆盖跨 Goal 关系拒绝、
关联尚未回填、子 Store 故障、重复取消、发布资格与取消排序及迟到 Registry 投影。
运行新测试、前批二十四文件及 Goal 附件回归，语法、前端构建、Diff/受限敏感扫描。
高风险为跨 Store 状态与异步分派；不新增状态、业务路由、依赖、Planner 能力或文件
授权，不撤回已提交副作用、不声称撤回外部请求。回退撤去本批协调接线及查询方法，
保留所有持久取消决定；全量、上游、预算/发生身份、独立预览仍是后续门禁。

### Goal 取消树结果与证据

本批最终五路径：agent_tasks.py、goal_coordinator.py、main.py、
test_workflow_r8a_goal_cancel.py、本任务卡。未修改 goals.py 存储协议或权限边界。

- 使用既有 Task source_agent + goal_id + 合法 goal_step_id 关联查找取消目标，
  不依赖 Goal 步骤回填是否完成，不受管理列表 50/200 条分页限制；返回隔离快照。
  缺失、错误及非字符串步骤关联不猜测、不取消其他任务，也不阻断合法兄弟任务。
- Goal 持久取消后先提交所有可取消 Task 的本地决定，再复用主入口的 Task/Handoff
  到 durable Workflow 取消链。单项失败继续处理兄弟项，最终仍抛错，不假称全成功。
  已完成/失败 Task 保留；重复取消及后台扫描能再次补齐未完成的跨 Store 投影。
- Coordinator 用同一进程内锁串行化 Goal 取消决定和执行资格发布。先持久化步骤关联，
  再设置 Handoff ready；登记期间发生取消时，finally 对账能找到尚未回填的 Task。
  曾考虑保留旧的 ready 在前顺序，安全复核拒绝后未应用，不绕过该约束。
- 对“关联已经保存、ready 尚未提交”的新增中断窗口，重载只补齐同 Goal/步骤、同
  Task/Handoff、同目标固定版本、pending、明确 ready=false 且零尝试、无租约及运行
  标识的登记。检查和发布在 Task Store 锁内；写盘异常回滚内存。既有尝试、运行 ID、
  类型错误、绑定漂移、暂停或取消不自动重发。这不是外部请求重试或完整发生身份方案。
- 原 main.get_goal_coordinator 显式接入已有 cancel_agent_task；测试覆盖真实内部函数和
  临时 Goal/Task/Workflow Store，运行、审批、客户端、定时与 Handoff 等待都被取消，
  子执行 Registry 与本地取消信号一致。重复对账不增加已取消子执行的 revision。

失败证据全部保留：初始夹具误用单步骤，被合法性检查拒绝（r8-goal-cancel-red.xml）；
修正为合法两步骤后六项业务反例全失败（red-02.xml）。readiness-red.xml 复现登记
中断滞留；slow-red.xml 复现慢子投影拖延兄弟 Task 取消。中间扩大回归两平台均为
765 passed / 2 failed，原因是新增测试插入位置错误导致 step 未定义；已把断言恢复到
对应测试，未删断言或放松产品规则。中间 final-source 归档不作为最终通过证据。

最终新增 45 项，与 Goal 终态/API 组合为 88 passed。最终扩大回归覆盖前批二十四文件、
新取消文件和 test_xpert_context.py，共二十六文件：Windows 768 passed，208.31 秒；
Linux 768 passed，19.90 秒。均无失败/跳过，4 条既有 lifespan 警告。
命令为 `python -B -m pytest <二十六文件> -q -ra -p no:cacheprovider --tb=short`，
完整列表见 Linux pytest.log 的 COMMAND，Windows 使用新的短目录 r8gcan-final2。
本结论不等同于全量 server/tests/ 或真实 Provider 验收。

最终源码归档：C:\tmp\modelmirror-r8-verification-20261008\goal-cancel-final-02-source。
HEAD e3ac455553c2d3065bcd8768d78b849c8477392a，49 个覆盖路径，SHA-256：
4ec2223dd28e87988d687fa744ceda11de8ae2e4590d288ca77f27de947ebd3a。
与上一批最终归档仅五个约定路径不同；此结果补记前全部 49 个工作树哈希一致。
证据在既有 r8-evidence：r8-goal-cancel-windows-final-02-junit.xml、
r8-goal-cancel-linux-final-02-{pytest.log,junit.xml,environment.json,result.json}。
Linux 使用既有固定本地镜像，断网、非 root、只读根，无依赖版本差异。

四个 Python 文件最终 py_compile 通过，缓存位于仓库外；npm.cmd run build 通过，
3196 模块、Vite 11.95 秒，仅既有大 chunk 警告。Diff 检查通过、暂存区为空；本批
五文件受限凭据特征扫描无命中。未纳入运行数据、构建产物或上传文件。

限制：本批支持单实例内的取消排序和持久事实重载，不支持多后端共享目录；Store
重载/故障注入不冒充真实独立进程崩溃验收。正常活动 Goal 调度的既有分页上限未改。
关联缺失的历史 Task 不猜测归属；全部迟到 Registry 投影、完整逻辑发生身份和旧
执行者隔离仍未宣布关闭。Automation 取消、父子预算/不确定请求、最新上游集成、
最终全量、独立预览及帮助中心门禁仍待后续批次。回退不删除既有取消事实，不撤销已
提交业务副作用。未 Commit、Push、PR、部署、修改共享栈或调用真实模型，未进入 R8-B。

## Automation 取消入口与本地状态投影

基于 e3ac4555、前批 49 路径继续，本批仅 automation_store.py、automation_coordinator.py、
automation_api.py、新增 test_workflow_r8a_automation_cancel.py、本卡共五文件。API 当前
只写 Store；本地目标要等续租才观察取消，Registry 可继续 running；取消持久化异常
会先污染内存。先稳定这一直接链，持久 Workflow 子执行的身份关联/取消另作紧邻批次。
不把本地 Task.cancel 当作撤回外部调用，也不声称关闭跨 Store 子执行链。

验收先复现取消落盘失败、API 取消无即时停止/状态投影、重复及重启对账，再覆盖完成
竞争、迟到成功/失败、取消与人工重试排序、单项投影失败、分页遗漏和 Coordinator 停止。
新增私有协调方法，不新增业务路由、公开状态、Store 格式、Planner 能力、权限或依赖。
先运行本批测试与既有 Automation fencing，再跑受影响 Runtime/Workflow/App/Goal
回归、语法和前端生产构建。风险为持久决定及异步取消；全部使用临时 Store 和合成
回调，无真实 Provider/业务写入。回退只撤回协调接线，不撤销已持久化的取消状态。

定向证伪：取消后的迟到失败仍写 retry_scheduled、人工重试落盘失败污染 pending 内存、
旧取消投影未完成即允许人工重试，均有原失败记录。取消与重试改用副本提交并在持久化
异常时恢复内存；协调器串行核验持久状态、取消本地目标并对账所有关联 Registry。
投影单项失败继续处理其余关联项，保留错误供下轮对账，不伪装为全部成功。人工重试
需本地旧目标退出且旧取消投影完成。取消后的迟到完成、等待、失败不可重新提交。
21 项本批测试通过；测试编写中的异步断言及缺失 wait ID 已修正并分别保留失败证据，
不归为产品缺陷。影响面冻结回归尚待本批末尾记录。关闭 Coordinator 仍保留恢复事实，
本批不据此承诺不确定外部派发可安全重发。

### 本批冻结验收结果

冻结目录 C:\tmp\modelmirror-r8-verification-20261008\automation-cancel-final-01-source，
HEAD e3ac455553c2d3065bcd8768d78b849c8477392a，51 个覆盖路径，归档 SHA-256：
96352d520c129641170974c8b7e2f69640095a7d204df9efcbe36e31fe86b2d5。
相较前批 goal-cancel-final-02-source 仅本批约定五文件不同；结果补记前全部工作树覆盖
文件哈希与冻结一致。没有暂存、提交、Push、PR、部署、共享栈修改或真实模型调用。

- 32 文件影响面回归：Windows 978 passed（235.48 秒）；Linux 978 passed（23.10 秒）。
- 补充 Automation 专属 fencing：Windows 42 passed（12.97 秒）；Linux 42 passed（13.09 秒）。
- 两平台各合计 1020 passed，无失败/跳过；主回归各 4 条既有 lifespan 弃用警告。
- 本批 21 项新增测试包含在上述主回归内，不重复计数；独立定向结果为 21 passed。
- 四个 Python 文件 py_compile、Diff 检查通过，前端生产构建通过：3196 模块、14.60 秒，
  仅既有大 chunk 警告。本批五文件受限凭据特征扫描无命中，未加入运行数据或构建产物。

证据目录为既有 r8-evidence：r8-automation-cancel-{windows,linux}-final-01-junit.xml、
r8-automation-cancel-fencing-{windows,linux}-01-junit.xml，Linux 同前缀附环境、日志与
result JSON。Linux 使用固定本地镜像、断网、只读根、非 root，依赖版本无差异；没有
把测试回调冒充真实 Provider，也没有把 Store 重载冒充独立进程崩溃恢复。

本批仅关闭 Automation 取消 API、本地目标停止、取消投影对账与人工重试排序的定向
门禁。紧邻下一批需处理 Automation 到持久 Workflow 子执行的固定身份核验、取消
传播与等待分类；父子预算、不确定外部请求、完整发生身份、最新上游集成、最终全量
及独立预览验收仍未关闭。正常成功/失败投影的全面恢复及性能规模验收亦不由本批结果
代替。回退不删除已提交取消事实，不撤销已发生业务副作用；未进入 R8-B。

## Automation 持久子执行取消批次

从前批 51 路径继续，本批限定 main.py、execution_store.py、automation_coordinator.py、
新增 test_workflow_r8a_automation_children.py 和本卡。只连接父取消到持久子执行，不
同时改造等待结果向父状态的反向分类，不新增 Planner、路由、授权、依赖或 Store。
匹配服务端持久的 automation_id/execution_id/occurrence_key 三项，取消不依赖尚未
返回的目标 task ID；新建与恢复入口在注册前后检查父取消，封住准备期间的取消窗口。
跨 Store 异常保留父取消事实，子执行及 Registry 可重放对账；终态子执行保持不变。
验收先复现运行/五类等待/ready 子执行漏取消，再覆盖错配、重载、迟到回调、注册竞态、
单项异常和分页遗漏。全部合成 Stores 与本地回调，不调用 Provider、不操作共享栈。
回退不删除持久取消、不撤销已提交副作用。完整发生身份/预算/不确定外部请求仍另批。

原失败证据为 r8-automation-children-red-01.xml：7 类子状态未取消，以及已取消父执行
仍能注册新子执行，共 8 个反例。实现后新增 25 项测试，连同前批取消、Automation
fencing 和 Handoff 子取消测试共 110 passed（19.71 秒）。双 Store 重载、错配身份、
单项 Store/Registry 失败后其余子项仍取消、重试前必须完成旧子取消、旧 lease 回调、
恢复 run aliases、注册窗口与分页遗漏均有合成证据。测试中 storage_path 属性名误写
已改为真实 storage_dir，并保留原失败记录；未削弱校验以通过。

### 持久子执行取消验收结果

冻结目录 C:\tmp\modelmirror-r8-verification-20261008\automation-children-final-01-source，
HEAD e3ac455553c2d3065bcd8768d78b849c8477392a，52 个覆盖路径，归档 SHA-256：
628cc70b3e30ebf90f240941eb42ac67f7f24d279adbb783be41d9b86562cf8c。
相较 automation-cancel-final-01-source 仅本批五文件不同；补记前全部覆盖文件工作树
哈希与归档一致。测试未访问真实 Provider、共享栈或业务数据，未提交或发布。

- 34 文件回归：Windows 1045 passed（248.81 秒），Linux 1045 passed（31.61 秒）。
- 旧审批/等待恢复调用方补测：Windows 7 passed（8.78 秒），Linux 7 passed（7.98 秒）。
- 两平台各 35 文件、1052 passed，无失败/跳过；各次命令仅 4 条既有 lifespan 弃用警告。
- 25 项新增测试已包含在上述主回归内，不重复计数。四个 Python 文件 py_compile、
  Diff 检查及受限凭据特征扫描通过；前端生产构建通过（3196 模块、13.94 秒），仅既有
  大 chunk 警告。暂存区为空，没有纳入 Runtime Store、上传文件或构建产物。

证据在既有 r8-evidence：r8-automation-children-{windows,linux}-final-01-junit.xml、
r8-automation-children-legacy-{windows,linux}-01-junit.xml；Linux 同前缀附环境、日志
和 result JSON，固定本地镜像、断网、非 root、只读根，依赖无版本差异。

结论限定直接子 Workflow 的取消、身份匹配和持久重载对账，不宣称外部请求撤回或真实
独立进程崩溃验收。嵌套 Handoff 后代的传递取消、非取消父终态的一致性、旧执行者
完整发生身份及预算均不由本批代替。正常分派的其他父状态判断未改变。下一小批须统一
Automation 初次结果与审批/Client 恢复后的等待分类、父状态投影；特别是初次消费尚未
统一持久结果分类、审批恢复后跨等待类型时父状态可能滞留，不能用本批通过掩盖。
最新上游集成、最终全量后端和独立预览仍未关闭。无 Commit、Push、PR、部署或 R8-B。

## Automation 权威结果与既有等待投影批次

本批从前批 52 覆盖路径继续，仅 main.py、automation_store.py、automation_coordinator.py、
新增 test_workflow_r8a_automation_outcomes.py 与本卡五文件。不新增公开状态、节点、
权限、依赖或业务路由；仅修正初次结果和既有审批/Client 等待的恢复投影。

初始证伪中 5 项真实失败：timer/node_retry/agent_handoff 被错误当作成功、取消被当作
成功、结果取自事件文本而非持久快照。另 14 项恢复测试因空图夹具非法失败，不冒充
产品反例；修正夹具后覆盖审批/Client 交叉等待及 run ID 更新。现改为复用
resolve_workflow_outcome 与 projection_local_operation，在双 Store 锁内核对当前
子快照、三项父身份、绑定 task 与 run 历史后提交。投影收据只含 ID、revision、状态
和 wait 类型/ID，不包含输入、Prompt 或工具正文；重复收据不重复提交。

当前 Automation 仅承接审批和 Client 等待。其他等待明确进入 dead_letter，错误码
AUTOMATION_WAIT_UNSUPPORTED，禁止自动重试；终态父执行阻止子 Workflow 继续恢复。
不把未知等待映射成已有等待，也不提前开放 R8-C/D。等待子快照保留供诊断，不自动
删除或补偿；不声称嵌套委托后代已停止。既有不带权威结果的内部 TargetResult 调用
保持兼容，生产初次入口携带事件并必须由 Coordinator 绑定的 Workflow Store 核验。

Registry 投影从持久收据重新对账，不受管理分页限制；重试后的 pending/running 不用
旧收据覆盖。验收包括落盘回滚、重复、取消后重放、旧快照、伪造初次身份、跨 run
绑定、双 Store/Registry 分离恢复；全部合成回调。真实独立进程崩溃、模型表现、旧
worker 全链身份、父子预算、超时/失败投影的全面收口仍未由本批代替。
回退时恢复旧入口但保留收据可读，不能自动重发已派发或死信目标。

补充根因对照：从 automation-children-final-01-source 的 main.py 提取旧审批/Client
回调，配合修正后的相同夹具和当前 Store，三项均观察到预期旧缺陷（3 passed，21.11 秒）：
审批到 Client 未投影、审批到审批换 run 后冲突、Client 到完成换 run 后冲突。该测试为
仓库外 test_automation_outcomes_old_callbacks.py，证据 r8-automation-outcomes-old-callbacks-01.xml；
不是干净 main 或全旧版本验收，不计入新代码通过数量。新代码对应 21 项交叉结果已通过。
新增故障/身份/重复/对账后本文件总计 31 passed（9.68 秒）；联动定向回归曾为 131 passed，
最终以同源冻结门禁为准，不累计不同轮次测试数。

### 权威结果投影冻结验收

冻结目录 C:\tmp\modelmirror-r8-verification-20261008\automation-outcomes-final-01-source，
HEAD e3ac455553c2d3065bcd8768d78b849c8477392a，53 个覆盖路径，SHA-256：
b3f9b2a10d6cccff7efc274ac936c91555f429c4a8c48310cec32b48b357d1d5。
与上批 automation-children-final-01-source 仅约定五文件不同；补记前源码覆盖哈希
全部一致。最终结果补记只修改本卡，不修改已测代码。

- 36 文件影响面回归：Windows 1083 passed（335.90 秒）；Linux 1083 passed（42.87 秒）。
- 两平台均无失败、无跳过，各 4 条既有 lifespan 弃用警告。31 项新增测试包含在主回归内。
- 四 Python 文件语法通过；前端生产构建通过（3196 模块、20.82 秒），仅既有大 chunk
  警告。Diff 检查与五文件受限凭据特征扫描通过，暂存区为空。
- 证据：r8-evidence/r8-automation-outcomes-{windows,linux}-final-01-junit.xml；Linux
  同前缀附环境、日志及 result JSON。使用既有固定本地镜像，断网、非 root、只读根，
  依赖版本一致；没有下载新镜像或操作共享栈。

本批关闭初次持久结果分类、审批/Client 跨等待与 run 变更投影、收据重放及 Registry
对账门禁；其他等待仍不开放，不支持时死信并禁止子执行继续恢复。外部请求的结果
未知问题、超时/失败回调的全面投影、完整旧执行者身份、父子预算、最新上游集成、
最终全量后端及独立预览仍未关闭。未 Commit、Push、PR、部署、调用真实模型或进入 R8-B。

## Automation 等待超时身份与恢复批次

目标限定审批/Client 等待超时，不包含普通执行异常、执行预算超时或新等待类型。
沿用 e3ac455553c2d3065bcd8768d78b849c8477392a 独立工作树及已有 53 路径覆盖，
本批只修改 main.py、automation_store.py、automation_coordinator.py、新增
test_workflow_r8a_automation_expiry.py 和本卡。无公开 API、依赖或数据迁移。

初始离线反例 11 项失败：两项新错误码契约未实现；九项行为失败分别为旧等待误伤新
等待、伪造过期对象可终止父执行、过期请求落盘后未投影不可恢复（各两项）、通用失败
绕过等待身份（两项）及失败落盘异常留下内存变更（一项）。不能将错误码差异冒充
独立的运行失败。原始证据 r8-automation-expiry-red-01.xml 保留。

实现复用既有 Workflow 快照 fence 及 Automation 安全投影收据：请求 Store 必须确认
同 task/run 的 expired，双执行 Store 锁内再核对完整快照及父绑定 wait kind/ID。
迟到、伪造或终态冲突不修改父执行；真实过期进入 dead_letter，错误码
AUTOMATION_WAIT_EXPIRED，不自动重试。超时父状态与定义更新时间在同一次文件持久化
提交，失败恢复内存；通用 fail_execution 仅接受持有效 lease 的 running 执行。

Coordinator 无分页扫描既有等待，在请求过期已落盘但回调丢失时重新核验；父超时
收据落盘后 Registry 失败则仅重放本地投影，不重派模型或 Client Tool。子 Workflow
等待快照保留诊断，父终态阻止恢复，不宣称撤回客户端已经发生的副作用，也不把
过期等同于已执行或未执行。非 Automation 超时路径保持原逻辑。

验收：先新测试，再 Automation/审批/Client/Workflow/Goal/Handoff/Registry/App
影响面回归及双平台冻结验证、语法、前端构建、Diff/敏感扫描。普通执行异常、预算
超时与不确定外发、完整 attempt 身份、最新上游、全量和独立预览仍另批收口。
回退只能恢复旧入口且保留新收据可读，不得自动重发已过期或死信执行。

故障注入续查：第一次四项失败为测试误计了 RunRegistry 自动生成的 run.failed，
不计作产品缺陷。改为只统计 automation.outcome_projected 后，复现两项真实缺口：
update_run 成功、record_checkpoint 失败时，旧收据短路导致缺失证据不能补齐。
现分别核对状态与专属 checkpoint，缺项只补本地投影，不重新派发；保留
r8-automation-expiry-fault-01/02.xml，避免混淆测试问题与实现问题。

必要范围调整为七文件：来源 Store 的 expire_due 保存失败会残留内存 expired，
新增对账可能将未提交事实当成过期证据。两项集成反例已复现（source-red-01.xml）。
因此同时限定修复 approval_store.py 与 client_tool_store.py 的过期批提交：先复制、
落盘失败恢复全部原对象、成功才返回分离副本。不改变决定、派发、取消及工具执行
协议。不能将来源原子性留到后批而宣称本批恢复安全；本项是同一超时投影目标的
必要组成，不扩展为整套请求 Store 重写。其余失败路径仍明确待审计。

### 等待超时冻结验收

冻结目录 C:\tmp\modelmirror-r8-verification-20261008\automation-expiry-final-01-source，
HEAD e3ac455553c2d3065bcd8768d78b849c8477392a；56 个覆盖路径，归档 SHA-256：
c65d69da456f3cfc0f930710711fd00562d73f3d43d16cf30e83791fd9ab4b1b。
与上批冻结归档仅本批七文件不同；验证中工作树覆盖哈希无漂移，结果补记只改本卡。

- 新超时矩阵 35 passed（10.14 秒），包含来源/父 Store 落盘失败、Registry 两步
  投影中断、持久重载、迟到与重复、取消、换 run、伪造、人工重试后旧回调拒绝。
- 37 文件影响面回归：Windows 1118 passed（361.72 秒）；Linux 1118 passed
  （54.26 秒）。两平台无失败、无跳过，各四条既有 lifespan 弃用警告。
  35 项新测试已包含在 1118 中，不能累加不同轮次结果。
- 六 Python 文件 py_compile 通过；前端 npm.cmd run build 通过（3196 模块，
  Vite 20.05 秒），仅既有大 chunk 警告；Diff、七文件受限敏感特征扫描通过，
  暂存区为空，无 Runtime Store、凭据或构建产物进入改动范围。
- 证据位于既有 r8-evidence：r8-automation-expiry-{windows,linux}-final-01-junit.xml；
  Linux 同前缀附环境、日志、result JSON。固定既有本地镜像、断网、非 root、
  只读根，依赖版本无差异；未操作共享栈。

本批验收仅关闭 Automation 既有审批/Client 等待超时身份及本地投影恢复门禁。
单独请求 Store 的其他决定/派发/取消路径、普通执行失败与运行预算超时、不确定
外发请求、完整 attempt 身份、父子预算、最新上游集成、最终全量及独立预览仍未
关闭；本批离线重载不能冒充真实进程崩溃或 Provider 验收。无 Commit、Push、PR、
部署、真实模型调用或 R8-B。下一批优先审查执行异常/运行预算超时与重发边界。

## Automation 目标派发与不确定重发批次

目标：关闭已进入目标函数后的异常、预算超时及停机导致相同执行自动重派的路径。
本批限定 automation_store.py、automation_coordinator.py、已有 automation_fencing
测试、新增 test_workflow_r8a_automation_dispatch.py 和本卡五文件。基线沿用上批
冻结源码；无新路由、SSE、节点、依赖、模型调用或部署，不进入 R8-B。

首跑 4 failed / 1 passed：普通异常与 TimeoutError 被排入 pending、停机后 lease
过期直接再次调用目标、已派发执行可被重新领取。另一个普通 retry 拒绝是因为旧
状态仍为 pending，不视为未知结果安全门禁已成立。原始证据 dispatch-red-01 保留。

新增内部 target_dispatch V1 安全回执，只有 version/attempt/phase/Registry run ID。
claim 原子登记 prepared；进入目标函数前持久化 started，落盘失败禁止调用目标。
已派发异常或结果不明落为 dead_letter / AUTOMATION_DISPATCH_UNCERTAIN；过期 running
或历史重试项缺少可信 prepared 时同样阻断，不能推断历史请求从未派发。prepared
明确表示本次目标未进入，保留既有有界自动重试。完成/等待结果与 settled 在同次
父 Store 写入；不确定结果不能通过普通 retry 或 reset_attempts 清除。现有已确定
等待/完成快照保持兼容，无外部副作用补偿。

派发标记是保守的目标调用边界，不是 Provider 实际收到请求的证明；标记落盘后、
调用前崩溃也会阻断，不猜测、自动查询 Provider 或重发。子 Workflow 内部逐模型/
工具派发、等待恢复、根预算和完整发生代次仍是后续门禁，不能以本批代替。
已有 fencing 测试保留旧执行者禁止迟到提交断言；派发后的 takeover 现在必须拒绝，
未派发 prepared 的 lease 接管仍保留正向用例。

验收先新反例与 Automation 回归，再同源双平台影响面、语法、前端构建和敏感扫描。
回退不得删除回执或将 uncertain 死信改为 pending；旧版本不能直接接管这些执行。

范围说明：增加第六文件 test_workflow_r8a_automation_cancel.py，仅修正一个旧取消竞态
夹具的失败位置。旧夹具通过目标函数异常制造 pending，但该路径现在必须按结果未知
阻断；改为 automation.started 记录失败（目标从未调用），保留取消后不得发布重试
事件的全部断言。不是放宽取消门禁，也不改取消实现。

故障注入已验证派发标记落盘失败不调用目标，claim/dispatch/完成/等待/不确定隔离的
落盘异常恢复内存与磁盘原值。另三项投影重复反例由 failure_projection 安全收据及
专属 checkpoint 独立对账收口，不反复写 run.failed，也不因报告失败重派目标。

专项补充 23 passed（3.64 秒）：实际 asyncio 本地 deadline、目标抛出 TimeoutError、
停机重载、标记后调用前崩溃、未知/损坏/历史回执拒绝、旧 lease 拒绝、普通重试禁止
清除未知回执、已证明 prepared 的接管/有界重试与落盘故障全部覆盖。未验证 Provider
是否收款或执行；测试目标都是合成协程，模拟超时不是实际模型超时验收。

### 目标派发冻结验收（2026-10-09）

冻结目录 C:\tmp\modelmirror-r8-verification-20261008\automation-dispatch-final-01-source，
HEAD e3ac455553c2d3065bcd8768d78b849c8477392a；57 个覆盖路径，归档 SHA-256：
f3ed3eccb6c73636c06b2069fb88877712c18826a93e58b9191e26fdc2369905。
与等待超时批次冻结归档相比，仅上述六文件变化；测试及构建后覆盖文件哈希无漂移。
本节为测试结束后的文档补记，不纳入此前冻结源码的执行证据。

- 首次 Windows final-01 会话恢复时进程已退出，未取得 JUnit 或退出结果，不算通过。
  核对无遗留测试进程后，使用全新 basetemp 重跑 final-02，未覆盖原证据。
- 38 文件影响面回归：Windows 1141 passed（357.96 秒），Linux 1141 passed
  （38.14 秒）；均零失败、零跳过，各四条既有 lifespan 弃用警告。新增 23 项已包含
  在 1141 中，不能与专项或前批结果累加。两平台使用相同冻结源码与测试文件集。
- Windows 命令为既定 38 路径的 python -B -m pytest -q -ra -p no:cacheprovider
  --tb=short，独立 basetemp 为 C:\tmp\r8ad-win2；JUnit 为既有 r8-evidence 下的
  r8-automation-dispatch-windows-final-02-junit.xml。
- Linux 使用既有固定本地镜像、断网、非 root、只读根和只读冻结源码挂载，运行
  verify_linux.py r8-automation-dispatch-linux-final-01 automation-dispatch；
  依赖版本无差异。完整命令、日志、JUnit、环境及 result JSON 均以此标签归档。
  脚本 result 的内部 name 沿用 goal-terminal；实际执行范围以日志中的 38 路径及
  JUnit 为准，不将该汇总名称误报为仅验证 Goal。
- 五个本批 Python 文件 py_compile 通过；npm.cmd run build 通过（3196 模块，
  Vite 19.31 秒），仅既有大 chunk 警告。Diff 空白检查与六文件受限敏感特征扫描
  通过；暂存区为空，未新增运行数据、凭据、依赖或提交产物。

本批仅关闭 Automation 初始目标派发边界的自动重派、普通重试清除不确定证据、
父 Store 写入故障及失败 Registry 投影重复问题。普通 retry 遇到未知派发仍拒绝，
本批没有新增人工消除未知状态的入口，不得手工删回执恢复 pending。

下一批优先核验子 Workflow 逐请求派发与等待恢复中的不确定结果，及其逻辑发生
身份；父子根预算、单实例目录所有权、最新上游集成、Coding Windows 独立修复集成、
最终全量后端及独立预览/进程崩溃验收仍未关闭。离线 Store 重载和合成协程不冒充
真实 Provider、计费、浏览器或跨进程恢复证据。无 Commit、Push、PR、部署、真实模型
调用或 R8-B 能力开放；回退继续保留不确定回执，禁止自动重派。

## Client Tool 派发前持久化与断线不确定性批次

日期：2026-10-09。沿用独立分支及上批冻结源码，不集成上游或改动其他脏文件。
范围限 client_tool_store.py、client_tool_coordinator.py、新增
test_workflow_r8a_client_dispatch.py 及本卡四文件。

源码核对发现：连接管理器先 send_json 再 mark_dispatched；断线时只有 running
修改请求进入 uncertain，dispatched 修改请求却回到 pending。待用合成连接证伪
发送后丢失回执、快速响应、重复派发及取消/过期快照继续发送，不能先声称已复现。

目标：发送前原子登记派发事实，失败不发送；以权威请求、revision 和连接身份复核
待发列表；派发后的修改请求不因缺少 running 确认回到自动待发队列。派发和断线
持久化失败须保留原内存/磁盘事实。只读请求保留既有断线重放语义，不新建 Runtime、
Store、协议或状态。回退不得把 uncertain 修改请求降级为 pending。

先运行新反例，再验证 Client/Office、Workflow 恢复、取消、审批、Automation、App
及既有 R8-A 影响面，最后语法、构建、Diff 和受限敏感扫描。外部连接均为本地合成
对象，不操作用户浏览器或文档。逐模型回执、人工重试的重新执行语义、连接替换后的
完整回调 fencing、父子预算及独立进程恢复仍另批处理，不据本批宣称全链路已稳定。

首跑 r8-client-dispatch-red-01：7 failed / 1 passed（4.52 秒），证实先发后落盘导致
快速 running 回执被拒、发送后异常回 pending、取消缓存仍发送、并发双发、落盘失败
仍发送及断线内存状态未回滚；只读重放控制组通过。没有用假接口缺失冒充既有缺陷。

改动复用原 dispatched/uncertain 状态，mark_dispatched 在锁内核对 pending、有效期、
revision 与当前在线连接，复制并落盘后才返回权威请求副本供 socket 使用。重复或
过期缓存不再发送；传输异常只记录安全类型，取消保留 CancelledError。断线将全部
已派发修改请求保守标为 uncertain，Host 和关联请求同次提交，保存失败恢复原值。
真实是否收到请求仍不可判定；标记后发送前中断也不允许猜测为未派发。

focus-01 Client/Office 24 passed（4.07 秒）；扩大故障矩阵及所有权/Automation 超时
focus-02 为 118 passed（15.85 秒）。包含真实 os.replace 故障、连接替换/撤销/错绑、
过期和 revision 漂移、缓存参数伪造、取消时对账失败及重载。合成 socket 配合真实
ClientToolset Provider、ClientToolCoordinator 与两 Store 验证 uncertain 返回错误，
同一请求不重派；测试恢复回调主动 fail，仅证明错误交付边界，不冒充 main Runner
全部异常策略验证。原人工 retry 行为未修改，其明确重新执行语义仍待后批审计。

文档核对后增加第五文件 docs/XPERT_CLIENT_TOOLS.md，仅同步派发前持久化与
dispatched 阶段断线的不确定语义，避免仍只描述 running。该说明及最终结果为冻结
测试源码后的文档修改，运行代码不变。公开帮助与真实扩展预览仍属最终交付门禁。

### Client 派发冻结验收

冻结目录 C:\tmp\modelmirror-r8-verification-20261008\client-dispatch-final-01-source，
HEAD e3ac455553c2d3065bcd8768d78b849c8477392a；58 个覆盖路径，归档 SHA-256：
00ca17b73ad44b0ed0ce98a1505f453bcbc82459a867e2d7192dec40b50a0445。
执行期覆盖哈希无漂移；冻结后仅本卡及 Client Tools 说明文档更新，未修改运行代码。

- 40 文件影响面回归：Windows 1168 passed（366.12 秒），Linux 1168 passed
  （38.73 秒），零失败、零跳过，各四条既有 lifespan 弃用警告。已逐项核对两平台
  1168 个 testcase ID 完全一致；20 项新派发测试包含其中，不能与专项结果累加。
- Windows 使用 python -B -m pytest、既定 40 路径、-q -ra -p no:cacheprovider
  --tb=short，独立 basetemp C:\tmp\r8cd-win1。Linux 使用既有固定镜像、断网、非 root、
  只读根与冻结归档，verify_linux.py 的 client-dispatch 模式；依赖版本无差异。
  r8-evidence 下 r8-client-dispatch-{windows,linux}-final-01-junit.xml 保存结果；
  Linux 同标签保存完整命令、日志、环境及 result。脚本内部 check 名仍为
  goal-terminal，范围以实际 40 路径及 JUnit 为准。
- 三个本批 Python 文件 py_compile 通过；前端 npm.cmd run build 通过，3196 模块，
  Vite 18.94 秒，只有既有大 chunk 警告。扩展 manifest V3、四项权限与两个精确本地
  host permission 检查通过；service-worker.js 和 popup.js 的 node --check 通过。
- Diff 与限定敏感特征扫描通过，暂存区为空。没有生产依赖、运行数据、凭据或产物
  进入提交范围。与上批相比仅两实现文件、新测试、本卡及 Client Tools 文档变化。

本批为本地离线证据，不是真实 Chrome/Office 操作、Provider 或进程崩溃验收。
下一批优先审查 Client 完成/失败回执的持久化与恢复消费一致性，随后收口人工重试
与原请求恢复边界；模型逐请求回执、父子预算、目录单实例、最新上游、最终全量及
独立预览继续待办。未提交、推送、创建 PR、部署或外部调用；未进入 R8-B。回退必须
保留 dispatched 修改请求的保守恢复，不得恢复为自动 pending 重派。

## Client 回执落盘与恢复消费一致性批次

日期：2026-10-09。沿用当前独立工作树，保留前批所有改动。限定
client_tool_store.py、新 test_workflow_r8a_client_receipts.py、本卡及
docs/XPERT_CLIENT_TOOLS.md 四文件，不修改 main、Planner、WebSocket 协议或依赖。

目标为 mark_running / complete_request / fail_request 三个客户端回执入口：先以
临时 Store 复现保存失败残留内存状态，再用真实 ClientToolCoordinator/Provider
验证未提交终态不能触发恢复。合法已派发请求的回执应复制、提交成功后才暴露；
返回值及回执元数据不能反向修改权威结果；重复回执维持既有终态幂等语义。失败
回执不得把尚未派发的 pending 请求提前结算。此处不改人工 retry/cancel、所有读取
方法或整套 Request Store，也不承诺过期/连接代次检查已完整收口。

验收先新增反例，再覆盖 Client/Office、审批/Workflow/Handoff/Goal、Automation、
Registry/App 的同源 Windows/Linux 影响面，语法、前端构建、Diff 与敏感扫描。
所有连接、回执及数据均为本地合成，无真实 Provider/浏览器/Office 调用。回退保留
已提交回执与原先不确定派发保护，不得将未知结果自动重发；无提交、部署或 R8-B。

### Client 回执反例与修复

原实现三入口直接修改 Store 活对象再保存。red-01 为 16 failed（3.30 秒），
覆盖保存/真实 os.replace 失败、返回值及嵌套元数据别名、未派发失败回执；其中
两项经真实 ClientToolCoordinator/Provider 证明未落盘 completed/failed 仍触发恢复。

修复仅在三入口复制请求、锁内保存并在失败时恢复原引用，返回独立副本，完成
元数据同样深拷贝；pending 失败回执改为拒绝。终态重复返回维持旧语义，重复
running 仍更新时间与 revision，不宣称其幂等。未重写读取 API、retry/cancel 或
连接代次协议；未知派发仍禁止自动重放。无数据格式、SSE 或 Planner 能力变化。

focus-01 Client/Office 52 passed（4.78 秒）；补齐身份、终态、重载消费后 focus-02
为 73 passed（6.23 秒），包括 37 项新回执测试。完成/失败身份必须在终态 no-op
前核对；迟到回执不能复活 failed/cancelled/expired/uncertain；重复终态不覆盖
首个结果且不再次保存。两 Store 重载及新协调器验证保存后、唤醒前的中断可复用
原回执且不会派发工具。恢复回调为合成代码主动结算 Workflow，不能当作 main
Runner、真实 Chrome/Office 或进程崩溃验收。证据在既有 r8-evidence 的
r8-client-receipts-{red-01,focus-01,focus-02}.xml。

### Client 回执冻结验收

冻结目录 C:\tmp\modelmirror-r8-verification-20261008\client-receipts-final-01-source，
HEAD e3ac455553c2d3065bcd8768d78b849c8477392a，60 个覆盖路径，归档 SHA-256：
e0e3d783b9f267115c64ad10bad695887a411c3b3495675d82b9cfeb54e21e11。
与 Client 派发冻结源相比仅本批约定四文件变化；测试结束时全部覆盖哈希一致。
本节为测试后的证据追加，运行代码及测试不变。

- 41 文件影响面回归：Windows 1205 passed（372.26 秒），Linux 1205 passed
  （35.32 秒），零失败、零跳过，各四条既有 lifespan 弃用警告。两平台 1205 个
  testcase ID 完全相同；其中包括本批 37 项，不与专项结果累加为不同用例。
- Windows 使用 python -B -m pytest、既定 41 路径、-q -ra -p no:cacheprovider
  --tb=short，独立 basetemp C:\tmp\r8cr-win1。Linux 使用既有固定镜像、断网、
  非 root、只读根和冻结归档，以 verify_linux.py client-receipts 模式运行相同
  路径，依赖版本无差异。r8-evidence 下
  r8-client-receipts-{windows,linux}-final-01-junit.xml 为结果；Linux 同标签保留
  日志、环境和 result。脚本内部 check 名仍为 goal-terminal，实际范围以记录的
  41 路径和 JUnit 为准，并非全量 server/tests/。
- 两个本批 Python 文件 py_compile 通过；npm.cmd run build 通过，3196 模块，
  Vite 16.88 秒，保留既有大 chunk 警告。扩展 manifest V3、四项权限、两个精确
  本地 host permission 核对及 service-worker.js/popup.js 的 node --check 通过。
- git diff --check、限定敏感特征扫描通过，暂存区为空；未引入依赖、迁移、
  Store 数据或构建产物。没有提交、推送、PR、部署、真实模型或客户端调用。

本批离线门禁通过，不代表 R8-A 完成：人工 retry/cancel 的提交与恢复边界、连接
代次、模型逐请求回执、父子预算、运行目录单实例、最新上游集成、最终全量及独立
预览/真实 Chrome/Office 与 office profile smoke 仍待验证。公开帮助与同基线
预览仍是 PR 前门禁。下一批先审查人工重试/取消与原请求恢复的区别，不进入 R8-B。
回退应关闭受影响恢复入口而保留已提交回执，不得通过恢复未知请求自动派发来
规避错误；本批没有真实业务写入可撤销。

## Client 人工重试与取消边界批次

日期：2026-10-09。沿用本工作树，先复现再修复。范围为 ClientToolStore 的
retry_request/cancel_request 与安全序列化、独立操作测试、ClientToolPanel 及其
投影测试、本卡和 Client Tools 文档；另调整前批一项 cancelled 测试的构造，共七文件。
该用例改为取消未派发请求，保留“终态不可复活”断言；已派发修改请求取消现在明确
为 uncertain，旧 cancelled 快照另有独立兼容测试。超过默认五文件的理由是同一安全
决策必须贯通服务端、界面禁用与独立回归，不能只隐藏按钮或复制前端权限规则。
不修改 WebSocket 协议、Planner、main、依赖、持久化格式或其他批次文件。

目标：重试/取消保存失败不能暴露未提交状态；已派发修改请求不能使用旧 operation
ID 重新执行；取消已派发修改请求只表示停止等待，不能证明外部动作已撤销，保留
uncertain。只读请求或确实未派发的修改请求保留既有显式重派路径。接口仅追加
retryable/retry_blocked_reason 安全投影，界面不自行推导授权，缺失投影时关闭重派。
不在此批实现新的修改重执行协议、连接代次、父任务恢复许可或完整 occurrence。

先覆盖保存故障、终态/旧快照、重复操作、迟到回执和协调器不可误派发；再运行
Client/Office 与现有影响面双平台回归、前端投影测试/生产构建、语法与扫描。均为
合成离线数据，不调用真实客户端/模型。UI 实操、帮助中心和最终全量仍为后续门禁。
回退不得重新允许未知修改请求按原身份重派；必要时关闭重试入口，保留已存回执。

### Client 人工操作反例与修复

red-01 为 17 failed / 2 passed（4.25 秒）：13 项既有行为反例证实保存失败内存
残留、返回别名、已派发修改请求原 ID 重派、取消误报确定终态，以及未提交 retry
仍经真实 ConnectionManager 发送。另四项仅是新增安全投影缺失，不能计为既有
行为缺陷。保留原始 JUnit，不将全部红测混称产品缺陷。

修复在 Store 内复制、保存、失败恢复，retry/cancel 返回副本；以既有派发次数、
时间和状态保守判断修改是否可能执行，统一生成重派拒绝原因供执行和 UI 使用。
pending 的取消/到期重派及只读显式重派保持兼容；已派发修改取消为 uncertain，
完成后取消为不写盘 no-op。终态和 Schema 无迁移，不增加 SSE 或新重执行协议。

focus-01 为 1 failed / 91 passed（5.98 秒），唯一失败是前批测试假定派发后修改
取消为 cancelled；明确调整测试构造为未派发取消，保留终态不可复活断言。追加
旧快照、API 不唤醒及取消/完成顺序后 focus-02 为 103 passed（7.30 秒）；补充
跨 Store 轮询反例后 focus-03 为 104 passed（7.80 秒），其中 31 项本批新测试。
ClientToolPanel 组件测试 5 passed（2.93 秒），覆盖缺省、false、伪 truthy 许可、
拒绝原因及正常只读重派。该组件测试使用合成 fetch，不是真实预览验收。

本批仍不证明只读重派的新旧回执代次隔离、父任务状态与重派的跨 Store 许可，
不自动重新审批、不创建新的修改 operation，也不承诺取消撤销外部副作用。

### Client 人工操作冻结验收

冻结目录 C:\tmp\modelmirror-r8-verification-20261008\client-actions-final-01-source，
HEAD e3ac455553c2d3065bcd8768d78b849c8477392a，63 个覆盖路径，归档 SHA-256：
09e2eb336558ad2e04eb13de8d339be53b106257d815d380cde6ec222e2ca53b。
与回执批冻结源相比仅约定七文件变化；两平台完成后覆盖哈希无漂移。本节为之后的
文档追加，不修改测试或运行代码。

- 42 文件影响面：Windows 1236 passed（360.70 秒），Linux 1236 passed
  （38.05 秒），零失败、零跳过，各四条既有 lifespan 弃用警告。逐项核对两个
  JUnit 的 1236 个 testcase ID 完全一致；31 项新操作测试包含其中，不重复计数。
- Windows：python -B -m pytest、既定 42 路径、-q -ra -p no:cacheprovider
  --tb=short，独立 basetemp C:\tmp\r8ca-win1。Linux：既有固定镜像、断网、
  非 root、只读根、冻结归档，verify_linux.py client-actions 模式，依赖版本
  无差异。r8-evidence 下 r8-client-actions-{windows,linux}-final-01-junit.xml
  为结果；Linux 同标签保留日志、环境和 result。内部 check 名仍为 goal-terminal，
  范围以 42 路径及 JUnit 为准，不是全量 server/tests/。
- 前端 npm.cmd run test -- run src/components/runtime/ClientToolPanel.test.tsx
  --maxWorkers=1 --fileParallelism=false：5 passed。npm.cmd run build 通过，
  3196 模块，Vite 15.32 秒，只有既有大 chunk 警告。三个本批 Python 文件
  py_compile、Diff 与限定敏感特征扫描通过，暂存区为空。

仅为本地离线证据；未进行浏览器 UI/真实扩展/Office 操作或 Provider 调用，帮助
中心及同基线独立预览仍是 PR 前门禁。只读重派回执代次及父执行许可优先进入下一
审计批，父子预算、不确定模型请求、目录单实例、最新上游集成及最终全量继续待办。
未提交、推送、PR、部署；未进入 R8-B。本批不构成 R8-A 全部验收通过。

### 组件测试覆盖复核更正

最终 Diff 核对发现 ClientToolPanel.test.tsx 是已有文件，新增五项时曾覆盖原有
三项终态通知测试。已恢复三项原测试，逐字比对其完整 describe 区块与 HEAD 一致，
保留全部断言。此问题发生于本批操作，不能将最初五项结果当作完整原有覆盖。
重新执行同一组件命令：8 passed（2.28 秒）；随后前端生产构建再次通过，3196
模块，Vite 18.23 秒，仍只有既有大 chunk 警告。

冻结源 final-01 之后仅该组件测试与本卡变化；已逐路径核对运行代码及全部 Python
测试哈希不变，因此双平台 1236 项证据继续只绑定 final-01 对应的后端范围，不
宣称后端回归覆盖了后来恢复的前端测试。当前组件测试证据为 8 项，不再使用 5 项
作为最终组件门禁。公开帮助、浏览器实操和最终全量仍未完成。

### Client 回执代次隔离：开工契约（2026-10-11）

工作树与 HEAD 延续上述基线，保留现有 63 路径 overlay，不操作主工作区。
事实：WebSocket 回执只校验 host/operation/call；派发连接没有随请求持久化；
Chrome 与 Office 的只读缓存按 operation 复用，可能将前次结果冒充新派发结果。
本批目标仅为 accepted/result/error 结算的连接与代次隔离。父执行结束后的重派
许可、附件上传回执及跨 Store 授权另批处理，不以本批测试声称其已经完成。

允许路径：client_tool_store/coordinator/api.py、两端 service-worker/taskpane.js、
新增 test_workflow_r8a_client_generation.py、既有 client_dispatch 快速回执夹具、
XPERT_CLIENT_TOOLS.md 与本卡，共九文件。超过五文件原因是服务端、两端缓存及
回执必须同步使用同一派发契约，不能只在服务端拒绝而遗留两端错误重放。
新增可选持久字段 dispatched_connection_id；复用 dispatch_count 为派发代次。
生产 WebSocket 以服务端连接身份验证，不信任消息自报连接；旧客户端缺省代次
仅允许当前连接上的首次派发，后续必须显式回传。内部未绑定连接的旧调用仍兼容，
但不能作为外部回执入口。只读缓存按请求与代次隔离，修改缓存仍保留 operation
语义，不增加修改重派能力。风险为连接/重试兼容，未知结果仍保持保守状态。

验收：先保留三类迟到回执反例，再运行本批测试及 Client/Office/前批测试；
同源 Windows/Linux 影响面回归、客户端脚本语法、前端构建、Diff/敏感信息扫描。
Node VM 仅模拟客户端宿主，不触发浏览器、Office 或模型。失败时停止派发，不
回滚真实请求状态；回退不得放开旧代次结算。无提交、部署、真实调用或 R8-B。

### Client 代次定位与定向结果

首轮 red-01 26 failed 包含新接口尚未存在和测试宿主缺项，不全归因为产品问题。
补齐 synthetic connect 的 schema_hashes 与 Chrome tabs 事件后，red-02 的 17 failed
（5.30 秒）为真实入口反例：15 项 WebSocket 迟到/伪造回执被接受，2 项实际客户端
脚本不回传代次。没有外部连接或宿主执行。初修后 focus-01 130 passed（10.88 秒）。

补充撤销、重启、存储失败、正向 WebSocket、迟到 JS callback 和修改缓存保护后，
focus-02 为 3 failed / 135 passed；三项是测试把 revoke 与 disconnect 的状态变化
混为一谈。现有 revoke 只撤销 host，request 保持 dispatched，之后由 detach/expiry
处理。已将断言明确为该既有语义，仍严格断言回执被拒绝且磁盘状态零变化，不扩展
本批为撤销协调器重写。focus-03：138 passed（10.50 秒），34 项为本批新增测试。
证据存于 r8-evidence/r8-client-generation-{red-01,red-02,focus-01,focus-02,focus-03}.xml。
两端完整 JS 在 Node VM 内执行真实 handler，仅替换宿主 IO；不是 Chrome/Office
真实操作验收。接下来冻结同源运行 43 文件双平台影响面回归。

### Client 代次冻结验收

冻结目录 C:\tmp\modelmirror-r8-verification-20261008\client-generation-final-01-source；
HEAD e3ac455553c2d3065bcd8768d78b849c8477392a，67 个覆盖路径，146042880 字节，
归档 SHA-256 为 45f7d5bb991cd734d14d43e545256ce7e414eb7ddee0a44f3c5a8383e8505469。
与前批 final-01 相比，本批九文件及前批已记录恢复的组件测试不同；没有其他范围
变化。双平台完成后逐路径核对 67 个覆盖哈希无漂移；本节仅为之后的记录追加。

- Windows 43 文件：1270 passed（367.35 秒）；Linux 同源 43 文件：1270 passed
  （42.61 秒）。零失败、零跳过，各四条既有 lifespan 弃用警告。逐项比较 JUnit
  的 classname/name 完全相同。34 项本批新增测试包含其中，不重复累计。
- Windows 使用 python -B -m pytest、43 路径、-q -ra -p no:cacheprovider
  --tb=short，basetemp=C:\tmp\r8cg-win1。Linux 使用既有固定镜像、断网、非 root、
  只读根和冻结归档，verify_linux.py client-generation；依赖版本匹配。内部
  check 名仍为 goal-terminal，实际范围以命令与 JUnit 为准，不是全量 server/tests/。
- r8-evidence/r8-client-generation-{windows,linux}-final-01-junit.xml 为结果；
  Linux 同标签保留环境、日志和 result。前端组件原有八项通过（3.52 秒）；
  npm.cmd run build 通过（3196 模块、18.14 秒），只有既有大 chunk 警告。
- 本批五个 Python 文件 py_compile、两端 node --check、Git diff --check 与限定
  敏感特征扫描通过；Chrome/Office manifest、权限及 Schema 检查包含在上述回归。
  暂存区为空，没有 Commit/Push/PR/Deploy，没有实际客户端操作或模型调用。

本批只关闭 accepted/result/error 的派发连接与代次隔离。父执行结束后的重派许可
及跨 Store TOCTOU、附件上传代次与持久化仍未关闭，不能因回执测试通过宣称无效
父任务不会再次派发。下一小批优先处理前者；真实 Chrome/Office、独立预览、帮助
中心、父子预算、不确定模型请求、单实例目录保护、最新上游及最终全量仍按 R8-A
门禁继续。不得回退成接受旧代次回执；失败时停止派发并保留未知结果。

### Client 父执行派发许可：开工契约（2026-10-11）

沿用 e3ac4555 独立工作树及前批 67 路径修改，不进入 R8-B。本批只处理父执行
与 Client retry/dispatch/mark_ready 的本地判定竞态，不扩展附件、预算或真实 IO。
已证实：人工 retry 仅检查 Client 状态；轮询派发所有 pending；终态回执读取与
mark_ready 分离，可能把刚重试的请求对应父任务错误唤醒。

允许七文件：client_tool_coordinator/api/store.py、新增
test_workflow_r8a_client_parent_permission.py、既有 client_actions API 夹具、
XPERT_CLIENT_TOOLS.md 与本卡。超五文件原因是入口策略、同步锁边界、回归及文档
必须绑定同一判定，不拆成显示拒绝但实际允许的临时状态。复用 Execution Store
projection_local_operation；按父执行锁到 Client 锁顺序保护本地决策，每条路径
只写一个 Store，不增加跨 Store 事务。网络 await 不持锁。

许可限定为有效且仍 waiting 的精确 task/run/client_tool/wait_id；缺失、终态、
其他等待、ready/running 或快照变化均拒绝 retry 和新派发。公开 retryable 使用
相同许可；未配置协调器不允许重试。已登记派发属于在途请求，之后取消不能证明
外部动作撤销，不将本地线性化门禁表述成网络原子取消。可信内部 Store 操作和
独立传输夹具保留兼容，生产入口必须走协调器父许可，不修改 Planner/SSE。

验收先红测，再定向与同源双平台 44 文件影响面、前端构建/组件、语法、Diff 和
敏感检查。覆盖终态、旧 run、错等待、读取后取消、重试与唤醒竞态、保存失败、
重启及锁边界。真实客户端/模型、共享栈、提交或发布均不在授权范围；失败保持
阻断和原记录，不通过回退校验重新派发，公开帮助与真实预览仍为整轮门禁。

### Client 父许可反例与定向证据

red-01：26 failed / 1 passed（6.82 秒），直接复现终态/错等待等仍可 retry/send、
无协调器仍可 retry，以及旧失败回执将刚重试请求的父任务变为 ready。均使用实际
API/Coordinator/Store 与合成 Socket；没有真实网络调用。初修 focus-01 为
1 failed / 164 passed（11.08 秒）：旧 API 测试仅提供 wake 的假协调器，缺少新
权威入口。已替换成真实协调器和有效等待父任务；故障测试新增 attempted 断言，
防止因提前拒绝而未执行持久化故障注入。

focus-02：172 passed（10.98 秒）；focus-03：175 passed（12.92 秒），含本批
37 项。补充范围包括父锁覆盖同步 Client 保存而不跨网络 await、重启后的有效
等待可派发、重试后取消跨重启不再派发、双击只递增一次 revision、失效 Store
不泄露路径以及不同父任务不能消费同一回执。精确 task/run/wait 判定也复用于
ready/expiry 筛选，避免旁路，但不宣称重写了整个恢复调度器。
结果位于 r8-evidence/r8-client-parent-{red-01,focus-01,focus-02,focus-03}.xml。
下一步冻结源码，运行同源 44 文件双平台影响面回归；这不是最终全量门禁。

### Client 父许可冻结验收

冻结目录 C:\tmp\modelmirror-r8-verification-20261008\client-parent-final-01-source；
HEAD e3ac455553c2d3065bcd8768d78b849c8477392a，68 个覆盖路径，146073600 字节，
归档 SHA-256 为 dc36ad71935abd696bdefe22079d8a28a711ae83235aaa9237bababde6486840。
与前批冻结相比仅开工契约中的七文件变化。回归结束后逐路径核对全部 68 个哈希
无漂移；本节为之后的验收记录追加，不修改已测运行或测试代码。

- Windows 44 文件：1307 passed（389.53 秒）；Linux 同源 44 文件：1307 passed
  （37.74 秒）。两边均零失败、零跳过，各四条既有 lifespan 弃用警告；逐项比较
  JUnit classname/name，集合完全一致。37 项新增测试包含其中，不重复累计。
- Windows 使用 python -B -m pytest、44 路径、-q -ra -p no:cacheprovider
  --tb=short，basetemp=C:\tmp\r8cp-win1。Linux 使用既有固定镜像、断网、非 root、
  只读根及冻结归档，verify_linux.py client-parent，依赖版本匹配。内部 check
  名仍为 goal-terminal，实际范围以命令及 JUnit 为准，不是全量 server/tests/。
- 结果为 r8-evidence/r8-client-parent-{windows,linux}-final-01-junit.xml；Linux
  同标签另有环境、日志和 result。ClientToolPanel 八项通过（2.69 秒）；
  npm.cmd run build 通过（3196 模块、20.07 秒），只有既有大 chunk 警告。
- 本批五个 Python 文件 py_compile、Git diff --check 与限定敏感特征扫描通过。
  暂存区为空，没有 Commit/Push/PR/Deploy，没有真实客户端或模型调用。

本批关闭精确父等待点的 retry/dispatch 许可及本地跨 Store 判定竞态。已经登记
派发的请求仍为在途操作，父任务随后取消不等于外部动作已撤销；不提供跨 Store
事务或网络原子取消保证。回退不得重新允许失效父任务派发，失败保持阻断及原记录。

下一小批为 Client 附件上传代次与原子持久化边界。整轮还须完成发生标识、父子
预算与超时、不确定请求恢复、单实例目录保护、跨 Store 综合故障注入、最新上游
集成及最终全量、独立预览/真实客户端验收、帮助中心同基线证据。不进入 R8-B，
不以本批离线回归代替 R8-A 的完整 PR 提交门禁。

### Client 附件上传：开工契约（2026-10-11）

沿用当前 e3ac4555 独立工作树及 68 路径修改。本批限定截图上传：旧派发代次、
失效父等待点、上传期间取消/重派/断线必须拒绝；登记失败不得暴露未提交产物。
允许七文件：client_tool_api/coordinator/store.py、Chrome service-worker.js、
新增 test_workflow_r8a_client_artifacts.py、XPERT_CLIENT_TOOLS.md 与本卡。超过
五文件原因是上传端协议、权威校验、文件与登记一致性、测试及说明必须同批闭合。
不修改 Office 输入下载、普通文件存储、预算或 Planner，不扩大外部操作授权。

复用 dispatch_count 与当前连接判定，上传新增可选代次请求头；缺省仅允许首次
有效派发。父执行锁先于 Client 锁，不跨文件读取 await。完整文件先原子改名，
Store 登记随后提交；登记失败恢复内存并清理本次文件，进程崩溃遗留未登记文件
不可查询，不自动扫描删除历史文件。不承诺跨文件断电事务或回滚客户端动作。
同一代次重复上传相同内容复用登记，不同内容拒绝。保留旧产物读取，无数据迁移。

验收先保留实际 API 红测，再运行 Client/Office/既有 R8-A 回归、两平台冻结测试、
脚本语法、前端构建、Diff 与敏感扫描。仅合成字节与模拟 socket，无真实客户端、
模型、共享栈、提交或部署。回退须保留失效代次阻断，不能恢复为无校验上传。

### Client 附件定位与定向证据

red-01 为 16 failed / 1 passed（4.71 秒）；其中通过项的断线注入调用签名有误，
不能作为拒绝证据。修正为 connection_id 关键字后 red-02：17 failed（4.03 秒），
复现失效上传仍登记、保存失败内存污染及重复上传多产物。没有真实客户端 IO。
初修 focus-01：192 passed（11.48 秒）。补齐文件写入/改名/快照改名失败、锁、
旧产物保留、孤立文件及真实扩展函数的 Node VM 测试后，focus-02 为 1 failed /
200 passed（12.23 秒）；失败为 VM 将扩展启动 capabilities 读取误计为截图上传。
明确分离两类模拟请求后 focus-03：201 passed（11.96 秒），不改变生产逻辑。

随后独立 red-03（1 failed / 26 deselected，4.27 秒）发现过严快照会拒绝上传读取
期间同代次 accepted 回执。改为固定派发身份、锁内核对最新快照，取消/重派/连接
替换仍拒绝；focus-04：202 passed（12.96 秒），包含本批 27 项。证据位于
r8-evidence/r8-client-artifacts-{red-01,red-02,red-03,focus-01,focus-02,focus-03,focus-04}.xml。
下一步冻结同源 45 文件 Windows/Linux 影响面回归；不代替最终全量或真实宿主验收。

### Client 附件自动重派补查

final-01 同源回归 Windows 1334 passed（358.64 秒）、Linux 1334 passed（40.43 秒），
69 个文件哈希无漂移，但未覆盖上传后断线、工具结果前自动重派的旧 artifact 引用。
不因这次通过关闭门禁。独立 Store 反例复现新图片被误判为重复；正式 red-04：
2 failed / 1 passed（4.53 秒），分别为同内容错误复用旧 ID、不同内容错误拒绝，
并保留派发保存失败回滚引用的正向覆盖。

最小修改只在截图新派发登记时解除当前请求的旧 artifact 引用，历史产物不删除，
登记失败仍恢复原快照；不增加版本字段或扫描清理。focus-05：205 passed（13.56
秒），含本批 30 项。重新冻结并重跑双平台 45 文件，旧 final-01 不代表最终代码。

### Client 附件最终冻结验收

冻结目录 C:\tmp\modelmirror-r8-verification-20261008\client-artifacts-final-02-source；
HEAD e3ac455553c2d3065bcd8768d78b849c8477392a，69 个覆盖路径，146094080 字节，
归档 SHA-256 为 2802d2831b8a8e098d7819f0aba9d57d30a5748a68e1fa06aaa23115afaf02c0。
与前批父许可冻结相比仅本批七文件变化；验证结束后核对全部 69 个哈希无漂移。
本节仅为之后的验收记录追加，没有修改已测代码。

- Windows 45 文件：1337 passed（363.81 秒）；Linux 同源 45 文件：1337 passed
  （41.25 秒）。零失败、零跳过，各四条既有 lifespan 弃用警告；逐项比较 JUnit
  classname/name 完全一致。本批 30 项包含其中，不重复累计各轮测试数量。
- Windows final-02 命令误重复一个测试文件，已精确停止本次 pytest 及其子进程，
  不作为通过证据；final-02b 从既有 JUnit 模块生成并校验 45 个唯一文件后重跑。
  命令为 python -B -m pytest、45 路径、-q -ra -p no:cacheprovider --tb=short，
  basetemp=C:\tmp\r8ca-win2b。结果为 r8-client-artifacts-windows-final-02b-junit.xml。
- Linux 使用固定本地镜像、断网、非 root、只读根和 final-02 归档，
  verify_linux.py client-artifacts；依赖版本匹配。结果为
  r8-client-artifacts-linux-final-02-junit.xml，同标签另有环境、日志和 result。
  所有结果位于 r8-evidence。内部 check 名仍为 goal-terminal，范围以实际命令
  为准，以上不是全量 server/tests/。
- ClientToolPanel 八项通过（2.51 秒）；npm.cmd run build 通过（3196 模块、
  15.48 秒），只有既有大 chunk 警告。四个最终 Python 文件 py_compile、Chrome
  node --check、Diff 与限定敏感特征扫描通过。暂存为空，无提交、PR 或部署。

本批关闭截图上传代次、精确父等待许可及文件/登记失败可见性；保留首次缺省代次
兼容、同代次重复上传复用和旧产物读取。上传没有真实宿主执行证据，Node VM 不
替代 Chrome 验收；崩溃孤立文件不可查询不等于断电跨文件事务，不自动删除历史
或撤销客户端动作。不得回退为无代次/父许可上传。

后续仍限 R8-A：逻辑发生标识、不确定请求恢复、父子预算及超时、单实例运行目录
保护、跨 Store 综合故障注入、最新上游集成及最终全量、独立预览/真实宿主与帮助
中心同基线证据。未运行真实客户端、模型或共享栈；本批通过不代表整轮可提交 PR。

### Managed Client 恢复发生身份：开工契约（2026-10-11）

沿用 e3ac4555 及既有 69 路径；本批只处理同一 Agent 连续 Client 等待的模型阶段
身份，不开放 Planner、迭代、父子预算或新的重试。先用真实 runner、Client Store
与 SQLite Provider 账本复现，模型和客户端结果使用合成替身，不访问外网。
允许 main.py、model_router/workflow_gateway.py、workload_control.py、repository.py、
新增 test_workflow_r8a_managed_occurrence.py、XPERT_CLIENT_TOOLS.md 与本任务卡。
七文件为单一目标所需的 runner 身份、Gateway、原子账本兼容保护、测试及文档边界。

新 Client 恢复身份须来自服务端固定等待点，并校验当前请求与节点归属；同等待
重放仍拒绝，不同等待才是下一次发生。旧身份已经存在任何执行证据时保守阻断，
不得换命名空间绕过未知请求。复用现有账本，不新增数据表、公开接口或迁移。
先红测，再定向与 Managed/Client/R8-A 回归，检查语法、Diff、敏感特征；整轮全量、
双平台冻结及真实预览各自报告。回退保留防重放记录，不清账、不自动重试。

定位记录：red-01/02/03 分别暴露夹具混用 MCP 白名单、模拟调用未写真实 Provider
回执和 Client complete_request 签名错误，不作为产品根因证据。改用真实 Gateway
及 MockTransport 后 red-04（1 failed，11.09 秒）复现第二次 Client 等待恢复被
provider_workload_logical_run_replay_blocked 误拦。首次修复与 Managed 回归 45 passed
（18.62 秒）；补充归属、重启、并发和事务失败后 59 passed（30.00 秒）。
证据在 r8-evidence/r8-managed-occurrence-{red-01..04,focus-01..02}.xml。
继续补充实际经过模拟 HTTP 派发但响应丢失的账本恢复反例，不能把准备阶段拒绝
当作派发后防重放证据；本批仍无真实 Provider 或客户端调用。

### 取消契约测试独立对齐小批

Managed 身份 final-01 冻结（73 路径）扩大至 51 文件后，Windows/Linux 均为
1 failed / 1462 passed（458.01 / 51.28 秒）。唯一失败为旧 run_contract 测试用
FakeRunRegistry 调用次数等于 1 代替取消效果幂等。上一批 2802d283 归档同测失败，
干净 HEAD fe07d8d5 归档同测通过；定位为此前“经典取消中断对账”允许重复恢复投影
后的测试遗漏，不归因于本批 Managed 变更，也不计作无关主线失败。

独立限定 test_workflow_run_contract.py 与本卡，不改生产代码。改用真实 Registry
检查重复请求不改变终态/时间、不增加取消 checkpoint；增加首次 Registry 投影失败
后再次请求完成对账的反例。迟到 complete 遵循 R8-A 拒绝终态回退，并核对落盘不变。
禁止通过恢复 cancelled 早退绕过待办对账，禁止仅放宽调用计数。验收先该文件及取消
对账测试，再重新冻结并执行同一 51 文件矩阵。无运行数据迁移或公开协议变化。

独立核对中首次测试误将“无 lease 的旧终态幂等返回”也要求抛错（2 failed / 25
passed），不属于生产缺陷。对照 _finish 实现后恢复原断言，并另加旧 lease 抛错
及磁盘字节不变断言；取消契约与中断对账共 27 passed（8.05 秒）。正常重复请求
与首次投影失败两条路径均只有一个 run.cancelled checkpoint，终态及时间不回退。
Managed 的 23 条新测试和此前影响面重点共 110 passed（36.04 秒），其中实际模拟
HTTP 已派发成功/超时后重启的四个反例均无第二次请求。

回退限制补充：不能直接恢复只看旧逻辑 phase 的代码；应停止新增 Managed Client
恢复派发，同时保留新旧账本读取和拒绝，避免漏查新命名空间记录而重复扣费。

### Managed 发生身份最终证据与未关闭项

最终归档 managed-occurrence-final-02-source，HEAD e3ac455553c2d3065bcd8768d78b849c8477392a，
74 个覆盖路径、146882560 字节、SHA-256：
94796d00c126f6edc9111bfb708e48b50c1f2405c30227188d111ca2d3c1a2c7。
相对前批共八文件：身份修复原定七文件，以及独立取消契约测试一文件。本节追加前
全部 74 路径哈希一致，运行代码与测试没有在冻结后修改。

- Windows final-02：1464 passed（471.85 秒）。Linux final-02-repeat：1464 passed
  （51.51 秒）。两次均零失败、错误、跳过；JUnit classname/name 逐项完全一致。
  使用 python -B -m pytest、51 唯一文件、-q -ra -p no:cacheprovider --tb=short。
  Linux 沿用固定本地镜像、断网、非 root、只读根及归档校验，依赖版本一致。
- 不能只报告通过结果：Linux final-02 首跑为 1 failed / 1463 passed（52.16 秒），
  失败在 test_waiting_completion_rejects_different_child[run] 的初次 claim，尚未
  进入子任务身份断言；错误为 pending 执行尚未到 available_at。原失败完整保留，
  未改 Automation 生产代码或该测试；同一归档随后完整复跑通过。
- 独立临时 Store 确定性探针将 available_at 固定 1000、领取时钟改为 999.999，
  重现相同拒绝且磁盘字节不变；恢复到 1000 可领取。该探针证明拒绝条件，不证明
  原失败时宿主时钟确实回退，原现场未记录实际时值。因此时间边界不稳定仍待查，
  不能以重跑通过关闭稳定性门禁。
- 六个本批 Python 文件内存 compile 通过；npm.cmd run build 通过（3196 模块、
  15.23 秒，仅既有大 chunk 提醒）；Diff 与限定密钥特征扫描通过，暂存区为空。
  本批未修改前端源文件，未重复计入上一批组件测试；以上不是全量 server/tests/。
- 证据统一位于 C:\tmp\modelmirror-r8-verification-20261008\r8-evidence，前缀
  r8-managed-occurrence，保留 red、focus、取消对照、final-01、final-02 及 repeat。
  原取消失败已通过真实 Registry 效果断言及投影失败补偿关闭，不能把它误标成主线问题。

下一独立小批先固定时间边界反例与必要现场时值，核对 Automation/lease 的时钟
假设，再决定测试时钟控制或运行逻辑是否需要修改，不降低到期校验。整轮仍有
父子预算及超时、其他不确定操作恢复、单实例目录保护、跨 Store 综合故障注入、
最新上游集成和最终全量、独立预览/真实宿主及帮助中心证据。当前只关闭 Client
Managed 阶段的局部身份缺口；无真实 Provider/客户端调用、提交、Push、PR 或部署，
不进入 R8-B。回退必须保留新旧 Provider 账本防重放，不删除数据或自动重发。

### Automation 时间边界：开工契约（2026-10-11）

沿用 e3ac4555 与 74 个既有覆盖路径。本批限 automation_store.py、
test_workflow_r8a_automation_fencing.py 和本卡三文件：将身份/终态测试与真实墙钟
解耦，补充到期前、恰好到期、到期后、重载以及扫描后时钟回退的确定性测试，
并让未到期领取与状态冲突的错误可区分，保留本次读取的时值。
不改调度、lease 到期判定、重试、安全权限、公开 Schema 或持久数据格式。
不使用 sleep、自动重跑或放宽 available_at 来掩盖失败；不假设原 Linux 现场
确有时钟回退。既有时钟替换均为模块级 monkeypatch，当前检查未发现泄漏证据。

先运行新边界测试保留诊断红测，再运行 Automation 全部影响面和冻结源码双平台
51 文件矩阵。拒绝路径须保持内存、磁盘及 attempt 不变，Coordinator 不能提前
派发或错误结束任务，时钟追平后才允许领取。风险为低：只修改错误诊断和测试；
回退三文件本批差异即可，无数据迁移。本批不调用外部模型或客户端、不提交部署。

### Automation 时间边界：验证与结论

- 红测 r8-automation-time-red01：4 failed / 10 passed（3.81 秒）。四项失败均为
  既有异常缺少未到期原因和时值；提前领取拒绝、磁盘不变与 Coordinator 安全延迟
  已通过。不得将这四项诊断红测说成“已经复现原现场时钟回退”。
- 生产差异只有 claim_execution 拆分状态拒绝与未到期拒绝，后者返回
  AUTOMATION_NOT_DUE、status、available_at 和实际读取的 observed_at。保持
  available_at > current、lease 到期判定、异常类型和无副作用拒绝不变。
- fencing 文件采用函数作用域 Store 时钟，不改变全局 time 或 asyncio 的时钟。
  新增 20 个确定性实例，覆盖隐式/显式 now、重载、到期前/恰好/之后、lease 交接，
  以及扫描后回退、无派发、无 Registry 运行、追平后唯一派发。原身份、迟到回调、
  取消和续租断言保留；其他文件不自动继承此测试时钟。
- 定向三文件 pytest：93 passed（14.85 秒）。冻结后同一 51 文件矩阵：Windows
  1484 passed（490.74 秒），Linux 1484 passed（52.08 秒）；逐项核对 1484 个
  classname/name 完全一致，均零失败、错误、跳过，仅四条既有弃用警告。
  Linux 为固定本地镜像、断网、非 root、只读根，依赖版本无差异。
- 冻结 automation-time-final01-source：HEAD e3ac455553c2d3065bcd8768d78b849c8477392a，
  74 路径、146892800 字节，SHA-256：
  42fc941c985871f702231810bfe561edb0f14ca7c11cbd4088922b229cc29feb。
  验证完成后、追加本节之前，74 路径哈希仍一致；相对前批恰为本批三个文件。
- 两个 Python 文件内存语法检查通过。首次检查因 PowerShell/Python stdin 编码
  不一致报 UnicodeEncodeError，明确 UTF-8 后原检查通过，非源文件语法失败。
  Diff 格式及限定密钥特征扫描通过，暂存为空。前端未修改，本批未重跑前端构建。
  证据在 r8-evidence/r8-automation-time-*，命令为 python -B -m pytest、
  -q -ra -p no:cacheprovider --tb=short；Linux 使用原 managed-occurrence 矩阵模式。

本批关闭“身份测试夹具依赖墙钟”和“领取错误缺少时间原因”两项；原 Linux
final-02 失败保留，现场具体时钟来源无法追溯。不能将本批通过描述为宿主时钟
问题已修复或整轮稳定性已验收。真实环境时间变化、最终全量和预览证据仍需各自
验证；本批未迁移墙钟设计，也未将时间追平视为外部未知请求可重发的证据。
下一批仍限 R8-A 的父子预算/超时及其他逻辑发生与不确定请求边界，之后再收口
单实例运行目录、跨 Store 综合故障注入、上游集成、全量及独立预览/帮助中心。
无外部模型或真实宿主调用，无 Commit、Push、PR、合并或部署，不进入 R8-B。

### 父子预算准入原子性：开工契约（2026-10-11）

沿用 e3ac4555 和 74 个既有覆盖路径，下一批只收口单事件循环内的预算准入。
代码定位：execution_budget.py 按 active budget 顺序扣减，子预算拒绝时父预算
已增加；execution_operation 在等待并发槽位之前计数，排队取消可能虚计调用。
这不是父子预算完全缺失，也不证明根预算跨进程恢复已经实现。

允许五文件：server/xpert_runtime/execution_budget.py、
server/tests/test_xpert_execution_budget.py、server/tests/test_workflow_vision_v2.py、
docs/XPERT_RUNTIME.md 与本卡。视觉测试复用真实服务及模拟 Provider，补充调用者证据。
先用真实预算对象与确定性事件建立红测，再实现所有活动预算的原子检查/扣减，
并在获得并发槽位后重新检查预算；排队取消须释放已领取槽位且不扣调用。
一旦进入操作体，失败或取消仍保留消耗，不能推断外部请求未发生并返还预算。
重复绑定同一预算实例不能重复计数或自锁；不同实例即使限额相同仍分别约束。

不改 main.py、Provider 账本、任务 Store、公开协议、持久格式或计费回执；
不新增生产依赖，不实施跨重启根账本、等待时限或新 Planner 能力。
风险为共享准入边界：先预算红测，再 Vision/Managed/Workflow 影响面及断网 Linux
矩阵；测试必须区分准入计数与真实账单。失败回退仅本批五文件差异，无数据迁移。
后续父子根预算持久化、超时传播及不确定操作恢复仍必须独立验证。

### 父子预算准入原子性：结果与证据

- red01：8 failed / 5 passed（3.54 秒）。六项为子层总步数/模型/工具额度拒绝后
  父层仍被扣减，另两项为排队取消计数与同一预算重复绑定。不是八个独立根因，
  也不涉及真实 Provider 扣费。
- 首轮 focus01：41 passed（11.44 秒）。反向测试 focus02 发现本批引入的可用性
  回归：已耗尽预算仍等待忙槽位，1 failed / 15 passed（6.27 秒）。在准入前恢复
  无副作用余额预检，并保留取得全部槽位后的原子复核；原失败检查重新通过。
  不删除失败报告，不把此项归为上游问题。
- 最终算法复用已有 counter lock，按实例身份稳定顺序取得全部锁，然后先检查
  全部活动预算，再无 await 地统一扣减。相同预算实例只入栈一次；排队或等待锁
  被取消时释放已取得资源。操作体一旦进入，错误/取消均不返还计数，不新增重试。
- 新增 15 个测试实例：预算文件 14 个、真实视觉服务加模拟 Managed Provider 1 个。
  包括子层拒绝、排队取消、排队期间余额耗尽后二次校验、锁获取中断、兄弟竞争、
  相同实例重绑、进入操作后的异常/取消，以及视觉页数预算拒绝不虚计父调用。
  既有并发和递归预算测试保留；Runtime 模拟结果不能替代真实模型或账单验收。
- focus03：58 passed（72.51 秒）。冻结源码的 55 文件影响面矩阵：Windows
  1529 passed（575.00 秒），Linux 1529 passed（74.07 秒）。两平台 JUnit 的
  1529 个 classname/name 完全一致，均零失败、错误、跳过，五条既有弃用警告。
  使用 python -B -m pytest、-q -ra -p no:cacheprovider --tb=short；Linux 为
  原 managed-occurrence 51 文件加预算/视觉四文件，验证器模式 budget-admission。
- 冻结 budget-admission-final01-source：HEAD e3ac455553c2d3065bcd8768d78b849c8477392a，
  78 个覆盖路径，146974720 字节，SHA-256：
  6c98498f39c62d0a81fb88e436133166594f76b708c4eb9b152629172577f55a。
  本节追加前全部 78 个路径哈希一致，较前批只变化开工契约的五文件。仓库外
  verify_linux.py 仅扩充四个测试文件，不修改测试源或放宽断言；依赖版本无差异。
- 三个 Python 文件内存语法、Diff 格式和限定密钥特征扫描通过；暂存区为空。
  本批未改前端、未重跑前端构建、未运行全量 server/tests/。证据统一保存在
  C:\tmp\modelmirror-r8-verification-20261008\r8-evidence，前缀 r8-budget-admission。

本批仅关闭同一进程、同一事件循环内父子预算准入的三个缺口。不同后台执行间的
根预算绑定、持久化恢复、超时/取消传播尚未据此完成；必须继续独立处理。恢复
涉及的真实用量和不确定请求仍以原回执为准，本批没有重置任何运行或计费账本。
实际模型/客户端调用、共享栈、Commit、Push、PR、合并与部署均未执行。
回退只恢复本批五文件差异，无数据迁移；将重新暴露准入虚计问题，不能以回退
返还已进入操作体的消耗。下一批仍在 R8-A，不进入 R8-B 或新增 Planner 权限。

### continuation 预算恢复：开工契约

本批沿用 e3ac455553c2d3065bcd8768d78b849c8477392a 与既有 78 路径工作区差异，
不清理、不提交。代码核对发现普通 Workflow 视觉 V2 恢复分支新建零计数预算，
Xpert 分支使用 int/or 转换恢复计数，严格快照验证仅覆盖 node_retry。
先用真实 ExecutionStore 重载和 Runner 建立反例，再统一恢复校验与初始化。

允许五文件：server/xpert_runtime/execution_budget.py、server/main.py、
server/tests/test_workflow_r8a_budget_restore.py、本任务卡、docs/XPERT_RUNTIME.md。
范围仅 continuation 中已保存的 usage 恢复；不新建 Store，不修改租约、Provider
账本、公开协议或持久格式。旧缺失/null 快照保持兼容，不把未知历史消耗说成零。
非法类型、负数、未知/缺失计数字段及计数自相矛盾必须拒绝，错误不回显正文。
node_retry 保留既有 NODE_RETRY_STATE_INVALID；其他等待统一安全预算恢复错误。

验收先运行新反例，再覆盖预算、continuation、retry、Managed Client、视觉和
恢复状态投影；必要时扩充冻结源码双平台矩阵。全量与真实预览仍单列。风险为
受保护 Runner 恢复路径，必须证明拒绝发生在运行/请求派发前及旧快照可读。
回退仅本批五文件差异，无数据迁移；不得用回退重置已消费计数。本批不解决
崩溃前未保存 usage、独立后台根预算身份、跨执行超时及未知请求账本恢复。
不调用外部模型、不写业务表、不操作共享栈、不发布，不进入 R8-B。

### continuation 预算恢复：结果与证据

- red01：32 failed / 9 passed，12.22 秒。两项确认普通 Workflow 视觉 V2 的
  已保存计数归零及已耗尽总步数仍完成；其余 30 项为审批、Client Tool、timer
  三类恢复入口对十类损坏 usage 未拒绝。不是 32 个独立根因。
- 生产修复集中在共享 restore_execution_budget_usage 和 Runner 恢复入口：
  拒绝负数、布尔、字符串、浮点、缺失/额外键及调用数超过总步数的快照，
  不做 int/or 容错；已有预算与视觉 fallback 统一恢复三类计数。非重试损坏
  快照在创建恢复 Run/运行内存状态前拒绝；node_retry 沿用既有安全失败路径。
- focus01：85 passed，13.76 秒。新增连续等待测试的 focus02 为 2 failed /
  46 passed，11.10 秒：原因是本批测试把既有 timer_waiting 写成 runtime_waiting。
  根据实际协议修正测试，不修改 SSE。重试测试改为真实 Runner 生成的有效
  continuation，并保留合法对照，避免其他无效字段掩盖预算错误。
- 最终新文件 49 passed，10.20 秒。覆盖 Xpert/App/Evaluator、普通视觉 Workflow、
  缺失/null 旧快照、耗尽后禁止进入操作体、损坏安全终态、合法与非法重试，
  以及连续两次定时等待/两次 Store 重载后的 usage 23→24→26→28。
  测试使用真实 ExecutionStore、Runner、恢复函数与 RunRegistry；视觉准入和
  HTTP 为离线替身，已完成视觉节点不得重跑。不是实际模型、附件或费用证据。
- 冻结 budget-restore-final01-source：HEAD e3ac455553c2d3065bcd8768d78b849c8477392a，
  79 覆盖路径、146995200 字节，SHA-256：
  1f480d3628b2ebe98eb9bcd1aa11d27bdefba2e82ab8aa9b10a0ce63ae02f41f。
  结果追加前 79 个文件哈希全匹配；较前批只改动本开工契约的五文件。
- 57 文件影响面矩阵：Windows 1590 passed（612.37 秒），断网 Linux 1590 passed
  （79.07 秒）。JUnit 的 1590 个 classname/name 一致，零失败、错误、跳过，
  五条既有弃用警告。验证命令为 python -B -m pytest，加 -q -ra、
  -p no:cacheprovider、--tb=short、独立 --basetemp 与 --junitxml。
  仓库外 verify_linux.py 新增 budget-restore 模式，只在原 55 文件上增加本批
  新文件及 continuation 回归；固定本地镜像、断网、只读根、非 root，依赖零差异。
- 三个 Python 文件内存语法、Diff 格式、限定密钥特征扫描通过，暂存区为空。
  证据目录仍为 C:\tmp\modelmirror-r8-verification-20261008\r8-evidence，
  前缀 r8-budget-restore。未运行全量 server/tests/、前端构建或独立预览。

本批只保证已成功保存的 continuation 计数不被恢复路径重置或宽松转换。
不证明崩溃前未保存消耗、旧缺失计数、后台子执行根预算、跨执行期限或不确定
外发账本已闭环；后续优先处理派发前持久计数与根预算身份，不以本批绿测代替。
回退为本批五文件差异，无持久格式迁移；会重新暴露计数归零/损坏容错问题。
无真实模型/客户端调用、业务表写入、共享栈、Commit、Push、PR、合并或部署。

## 剩余门禁顺序与人工验收安排

用户要求依次收尾，人工配合尽量集中在 PR 前。本阶段继续使用当前独立树，不
提交、不调用真实 Provider、不操作共享栈。按以下依赖顺序推进，不能将待做项
通过文档改名关闭：

1. 派发前预算持久记账与恢复权威，随后后台父子根绑定、期限/取消传播。
2. 单实例运行目录排斥、逻辑发生与不确定请求恢复的剩余反例及跨 Store 集成。
3. 刷新上游、核对交叉改动并安全集成；独立 Coding 修复仍独立，不复制未知改动。
4. 最新源码重点/全量后端、前端检查与构建、语法、敏感信息和差异审查。
5. 独立预览的离线故障/重启实操及帮助中心；最后集中需要用户配对、真实调用
   授权或人工确认的验收清单，附精确配置、次数、操作与停止条件。
6. 汇总门禁后等待提交授权，不自动 PR 或进入 R8-B。

### 派发前预算持久化：开工契约

基线及已有 79 路径不变。先单独建立现有 WorkflowExecutionStore 内的可选预算
状态，再接入 Runner 和共享预算准入；不增加数据库、生产依赖或新的业务 Store。
内部状态仅存版本、固定限额和计数，不存模型正文或凭据。旧条目缺省为空并从
合法 continuation 初始化；之后持久计数为权威，旧 continuation 只能落后，不能
超过已记账计数。限额漂移必须拒绝。新字段为加法读取兼容，回退须保留读取器，
不能删除账本或用旧代码重新置零。持久记账不是账单或可安全重发请求的证明。

首批允许 execution_budget.py、execution_store.py、新增
test_workflow_r8a_budget_journal.py 与本卡四文件。验证单/多预算同一快照原子准入、
当前 lease、旧 worker、并发计数、磁盘失败与重启。随后独立集成批允许 main.py、
execution_budget.py、同一回归文件、XPERT_RUNTIME.md 与本卡五文件。派发回调
不得在持久化之前运行，已进入或结果不确定的操作不退计数、不自动重试。
子树存在不同 Store 权威时先拒绝，不采用多个文件顺序扣减伪装原子提交。

集成批补充范围说明：需同步修改上一批 budget_restore 测试中的合法对照，改为
读取真实已落盘 usage，不能再只提高 continuation；新增独立漂移终态反例。
因此该批六文件不可安全拆分，避免测试夹具伪造合法状态或把预算漂移当作租约
接管。Store 类型区分随首批接口收口，不扩大功能范围。

先红测后修复、窄回归后冻结扩大矩阵；禁止为关闭门禁放宽测试或兼容坏状态。
后台独立子执行的根绑定、超时及运行目录排斥仍要分别验收，本批不提前宣称完成。

### Managed Workflow 派发预算：开工契约

- 现场路径核对：Workflow Gateway 的三处文本/Agent 请求在 mark_dispatched 前
  没有统一 execution_operation；Legacy 与 Vision V2 已有独立预算入口。
- 单批四文件：workflow_gateway.py、独立 managed_budget 测试、本任务卡及
  XPERT_RUNTIME.md。不修改 Binding、Provider 传输、视觉逐页预算或模型授权。
- 先以 MockTransport 证明有限预算不能阻止第二次请求，再接入文本流、Agent
  Tool Calling 及文本/JSON unary 的实际派发边界。资格与发生标识检查仍在前，
  预算必须在 mark_dispatched 和网络请求前落盘；未知结果不退还、不自动重试。
- 多模态委托的 vision_json_unary 沿用视觉服务的逐页预算，不重复收费或占槽。
- 窄测覆盖落盘后才发出请求、耗尽、保存失败、排队取消、并发上限及不确定恢复。
  随后将预算 journal 与 Gateway 合并冻结，执行 Windows/Linux 相关回归。
- 无真实外发、无新依赖、无共享栈、无提交。回退不得删除已落盘预算；旧预算
  字段缺失保持兼容，不将准入次数等同实际计费次数。

### 预算持久化与 Managed 派发：本批结果

本批完成两个依赖子批：先在现有 ExecutionStore 保存派发前预算，再补齐
Managed Workflow 文本、Agent 和 JSON 调用的统一准入。不开放任何 Planner
节点、不改变审批、不引入新 Store 或依赖。后台独立子执行根预算仍未完成。

反例与修复证据（全部为合成临时 Store、离线 HTTP 替身）：

| 检查 | 结果 | 证据边界 |
| --- | --- | --- |
| journal 初始红测 | 22 失败 | 新契约接口/字段缺失，不当作 22 个独立根因 |
| journal Store 窄测 | 188 通过、1 跳过 | Windows 符号链接限制，后续 Linux 已覆盖 |
| journal 初次接入 | 93 通过、1 失败 | 原合法夹具只改高 continuation，已不符合权威计数；同步发现预算漂移不应使用租约冲突类别 |
| journal 接入修复 | 140 通过 | 合法夹具读取真实已保存 usage，另增漂移明确 failed 的反例；未放宽断言 |
| Managed 初始红测 | 25 失败 | 五种形态绕过有限预算、落盘、取消排队约束；同一缺口的多维验证 |
| Managed 第一轮窄测 | 84 通过 | 请求前落盘、耗尽、失败与不确定计数、视觉 V2 兼容 |
| Managed 第二轮窄测 | 66 通过 | 实际网关并发槽位、发生标识及多模态委托兼容 |
| 冻结 61 文件 Linux 回归 | 1734 通过，82.48 秒 | 断网、只读、非 root 的固定本地容器 |
| 同一源码 Windows 回归 | 1733 通过、1 跳过，708.71 秒 | 符号链接不可用；同名用例在 Linux 通过 |

两端 JUnit 的 1734 个 `classname::name` 完全相同，无失败或错误；五个既有
依赖弃用 warning 保留，不以 warning 冒充新功能证据。不是全量 server/tests。

固定源码：`budget-journal-final01-source`，基线仍为 `e3ac4555`，81 个 overlay
路径，147036160 字节；SHA-256：
`b436fb89e68343979915d35efb77ae7a08a164911eb0a0c30cd75a65861de0fb`。
验证结束逐项复核 81 个哈希一致。相较上一预算恢复快照只变更九个路径：
main、execution_budget、execution_store、workflow_gateway、budget_restore 测试、
新增 budget_journal / managed_budget 测试、Runtime 文档及本任务卡。
本节是在冻结验证后追加的证据记录，不将其哈希冒充冻结快照中的任务卡哈希。

外部验证根：`C:\tmp\modelmirror-r8-verification-20261008`。
报告位于 `r8-evidence`：`r8-budget-journal-red01.xml`、focus01/02/03、
`r8-managed-budget-red01.xml`、focus01/02、
`r8-budget-journal-linux-final01-junit.xml` 和
`r8-budget-journal-windows-final01.xml`。Linux 复核脚本的 `budget-journal`
模式固定 61 文件；Windows 从该 JUnit 提取同一文件集合并先检查源码哈希。
七个相关 Python 文件内存语法检查通过；全 Diff 空白检查、九文件限定密钥
模式扫描通过；暂存为空。限定扫描不是全仓库秘密审计。

上游门禁刷新但未集成：`git fetch origin main` 后主线为
`7eaaa305cface49ca3eedba6f47a7315f641e584`，本地基线落后 14 个提交，
上游 161 个变更路径与本轮有四个交集：`client/src/data/models.refresh.test.ts`、
`server/main.py`、`server/model_router/repository.py`、
`server/model_router/workload_control.py`。仅核对，无合并或提交；需逐处审查
Provider 新语义与现有发生标识、所有权修改，不可仅按文件数视为简单更新。

剩余门禁继续依次处理：后台子执行根预算身份与期限、取消/不确定派发整链路、
运行目录单实例保护、最新上游及独立 Coding 修复集成、最新全量后端与前端、
独立预览及帮助中心重放。需要人工配对、真实额度授权或操作验收的部分集中到
离线门禁收敛后的 PR 前；当前无人工操作要求。未调用真实模型、未操作共享栈，
未 Commit/Push/PR/Deploy，不进入 R8-B。

### 自动化固定期限：开工契约

当前检查发现 max_runtime_seconds 只用于首次派发的 wait_for，等待及恢复没有
持久总期限。本批收口这一根期限前置条件，不声称后台 Handoff/Goal 的调用次数
根绑定已完成。新发生在首次领取时固定限额、起点及期限，配置变更、等待、续租
和重启不得重新计时。旧已开始且缺少期限的快照保持兼容，不猜测历史授权。
期限到达只阻止后续工作，不等同撤销已提交效果；不确定派发仍必须保持 uncertain。

允许修改 automation_store、automation_coordinator、main、独立 deadline 测试、
本卡和 Runtime 文档六文件。Store、调度器、子执行入口及取消投影须一起验证，
因此不可安全拆为互不验证的实现。先红测，再验证固定时限、等待恢复、迟到完成、
写盘失败、重启、终态不回退、无自动重派与子执行取消；扩大到自动化既有矩阵。
验收使用临时 Store、可控时钟和本地异步替身，不外发。回退保留期限字段读取，
不得删除未知派发记录。无依赖、业务路由或调度能力开放，不提交、不部署。

窄回归补充：既有 automation_dispatch 的快速计时用例原通过修改定义副本为
0.02 秒加速；固定期限生效后须改为仅替换剩余计时入口，不伪造持久限额，
保留真实 asyncio 超时、目标取消和 uncertain 断言。由此增加第七个测试文件，
不是放宽生产限额或删除既有反例。

### 自动化固定期限：本批证据与边界

已完成新发生首次领取时固定总期限，等待/重领/重启不重置；调度器使用剩余
时间，恢复入口及完成提交拒绝超期。父状态先落盘，子取消与 Registry 投影
可补偿重放。这里的投影重放不包含 Provider 重派或业务补偿。
租约先失效的未知结果继续保持 `AUTOMATION_DISPATCH_UNCERTAIN`，到达期限后
仍停止子执行，保留原终态与 completed_at。完成或人工取消终态不被改写。

反例记录：初版红测 11 失败、3 通过，其中三个通过实际只测到 lease 过期，
另有一个夹具漏传 lease；修正夹具、不改生产代码后，14 项全部失败，确证
期限缺口。首次窄回归 105 通过；扩大 228 通过。追加“先失租后超时”反例
单独红测 1 失败，修复后最终 Windows 自动化八文件矩阵 229 通过（27.98 秒）。
新增 deadline 文件共 24 项，含损坏字段、旧快照、子 Store 失败补齐、活跃目标
取消、跨发生拒绝和保存失败回滚。测试不使用真实 Provider。

冻结快照 `automation-deadline-final01-source`：基线 `e3ac4555`，82 个 overlay
路径，147066880 字节，SHA-256：
`da517bbc5c7469098481a8da27275c4e491f65ba97727d90461e5bdf637360ab`。
固定本地镜像、断网、只读、非 root Linux 的 62 文件矩阵为 **1758 通过**，
85.91 秒；5 个既有依赖弃用 warning。Windows 229 项全部包含于 Linux JUnit，
无失败/错误；本次没有在 Windows 重跑完整 1758 项，也没有运行 server/tests 全量。
验证后复核 82 个源码哈希一致；本节为之后追加的证据，不冒充冻结任务卡哈希。

外部证据根仍为 `C:\tmp\modelmirror-r8-verification-20261008`；JUnit 在
`r8-evidence/r8-deadline-{red01,red02,focus01,focus02,focus03}.xml`、
`r8-deadline-uncertain-red.xml` 与 `r8-automation-deadline-linux-final01-junit.xml`。
相较上一冻结快照仅七个任务内路径变化：Store、Coordinator、main、两个
自动化测试文件、Runtime 文档与本卡。五个 Python 文件内存语法通过，全 Diff
空白检查通过，七文件限定凭据模式扫描零命中，暂存为空。不是完整秘密审计。

明确未关闭：此期限依赖系统墙钟，运行中停止为协作式取消和协调器轮询，
不是硬实时抢占。所有后台子执行的根预算身份、逐外部派发的统一截止检查、
Handoff/Goal 的传递绑定仍需单独收口，不能用本批 Automation 通过冒充整链路
门禁。旧已开始且没有期限的快照保留原兼容，不声称具备历史期限证据。
其他待办仍是运行目录互斥、最新上游及 Coding 修复集成、最终全量/前端、
独立预览与帮助重放。当前无人工配合要求，未提交、外发或部署，不进入 R8-B。

### 逐派发父状态检查：开工契约

继续使用本独立树及 e3ac4555 基线，已有 82 路径不回退。发现固定期限入口与
轮询之间存在派发窗口：同一 Agent 的下一次调用、等待并发槽位或 HTTP 客户端
进入期间，父状态可能已改变。本批在共用预算准入及真实请求发送前绑定服务端
检查器；检查器不可由请求字段传入、不可序列化成授权，继承的检查不能被内层
空预算或退出覆盖。拒绝时不进入外部调用；已准入计数不退款，不伪装成账单。

范围为 execution_budget、main、workflow_gateway、新增独立 dispatch_guard 测试、
本卡及 Runtime 文档六文件。共用准入、Runner 绑定与 HTTP 最后检查必须一起
验收，不能仅用假的回调证明生产接入。先红测无预算、队列期间过期和客户端
进入后过期，再测真实 Runner 与 Managed 五种形态（HTTP 替身）；扩展已有预算、
自动化与 Runtime 矩阵。无外发、依赖、Store 或路由增加；不改变无检查器的调用。
回退可恢复检查器调用，不删除任何已落盘预算或不确定回执。后台独立根预算
关联和所有外部 IO 的传递性覆盖仍未关闭，本批不能据此宣告 PR 门禁通过。

### 逐派发父状态检查：2026-10-13 验证结果

已接入服务端作用域检查器，不接受模型或 HTTP 请求提供回调。节点计步、模型
及 Runtime 工具共用准入；并发槽位与预算锁等待后重查。Managed 的三处实际
HTTP 发送前、Legacy unary/stream 及已有 fallback 发送前另作最后检查。
无预算对象仍检查；嵌套空上下文不能移除父约束；上下文结束及取消不污染其他任务。
拒绝于准入前计数不变；已准入后客户端初始化跨过期限则拒绝发送但保留准入计数。
没有承诺撤销已发请求或硬实时截止。

新增 dispatch_guard 文件 26 项。初始红测 20 失败，其中包括新接口缺失和 SSE
字节夹具问题；修正 SSE 解析后单独真实 Runner 红测仍失败，证明入口后跨期限
仍会执行节点，不以缺接口冒充端到端反例。Managed 慢客户端夹具使用实际
`_client_factory`，没有依赖一个未使用的属性。首轮接入 60 通过。扩展后
148 通过、2 失败来自测试引用不存在的 Legacy 配置函数及漏填必填参数；
修正夹具并保留断言后同矩阵 150 通过。新增 fallback 拒绝第二次请求的独立
反例 1 通过。最终 Windows 六文件矩阵 **151 通过**（60.78 秒）；固定镜像
断网、只读、非 root Linux 的 63 文件矩阵 **1784 通过**（89.33 秒）。
Windows 151 项逐名包含于 Linux JUnit；依赖弃用 warning 保留，非全量 server/tests。

冻结 `dispatch-guard-final01-source`，基线仍为 `e3ac4555`，83 个 overlay 路径，
147087360 字节；SHA-256：
`f8e02044b822c06e16f8580158ae40b03c416c7b85e8314326c572d97529fd5b`。
验证后 83 个源码哈希全部一致；本节是之后追加的证据记录，不冒充冻结卡片哈希。
相较上一冻结快照仅六路径变化：execution_budget、main、workflow_gateway、
新增测试、Runtime 文档及本卡。四个 Python 文件内存语法、全 Diff 空白检查
通过；六文件限定凭据模式扫描零命中，暂存为空，不等同全仓库秘密审计。
证据仍在 `C:\tmp\modelmirror-r8-verification-20261008\r8-evidence`：
`r8-dispatch-guard-{red01,red02,focus01,focus02,focus03,fallback,windows-final01}.xml`
及 `r8-dispatch-guard-linux-final01-junit.xml`。

只读刷新上游：`origin/main=262f7cafcfc77ce19263c86e59d0cb1dd7836365`，落后
16 提交、上游 358 个变更路径；交叉仍为 models.refresh.test、main、
model_router/repository 与 workload_control 四文件（616 新增行、131 删除行）。
仅 fetch/核对，未合并、提交或推送；当前测试不是最新主线集成证据。

未关闭的第一优先项仍为后台独立 Handoff/Goal 的根预算持久关联及根并发限制，
不能用进程内 ContextVar 继承冒充后台恢复。后续计数审查也需核对 Legacy
fallback 的逐请求准入与调用次数对应关系，本批只验证其期限拒绝，没有改变
既有 fallback 调用策略。其次为运行目录单实例排斥、上游及独立 Coding 修复
集成、最终全量/前端/秘密扫描、独立预览与帮助重放。本批无真实外发、业务写入、
共享栈操作、Commit/Push/PR/Deploy，不进入 R8-B；暂不需要人工配合。

### 后台父子根预算：持久内核开工契约

2026-10-13，仍在 e3ac4555 独立树，不回退已有改动。本批限定五文件：
execution_budget、execution_store、独立 budget_tree 测试、本卡、Runtime 文档。
先提供服务端委托登记、单一子发生绑定、整链原子扣减与单进程共享并发槽位。
登记必须由正在运行的父执行有效 owner 发起；等待父亲不复用旧 lease，子执行
持自己的 lease 消费已落盘委托；终态祖先拒绝派发。恢复读取持久计数，不复制
ContextVar 或信任 runtime_metadata 中的父 ID。绑定与委托不通过公开 API 提供。

风险是持久身份与额度安全：新增可选私有字段，旧快照缺省为空；不迁移业务数据，
不增加依赖/模型调用/Planner 能力。限定链深度与每父委托数量；损坏、重复消费、
环、重置或跨权威一律拒绝。先测试并行子调用、父等待、重启与旧 worker，再测
保存失败原子性和取消队列，不以新接口存在代替 Handoff 已接入的证据。

验收先运行 test_workflow_r8a_budget_tree.py，再运行预算 journal/restore、
dispatch_guard、managed_budget 与 test_xpert_execution_budget.py。生产 Handoff
登记时机、目标版本/输入固定和恢复回执接入另属随后集成批，当前不能宣称整链
完成。回退关闭新关联入口，保留已保存关联及计数的读取；不得丢弃委托后按独立
根执行重跑。无真实外发、业务写入、共享栈、提交或发布授权扩展。

### 后台父子根预算：2026-10-13 内核批验证

本批五路径已实现：私有双向委托、同一发生只绑定一个子任务、父等待时依赖
预先登记而非旧 lease、祖先终态拒绝、整链一次原子扣减、共享祖先只扣一次。
恢复保留计数，父预算旧对象不覆盖子调用增量；同根并发槽位按 Store 和执行
身份共享，排队期间关联漂移及父取消均拒绝。深度 16、每父登记 1000 上限。
旧快照无字段仍可读，但没有自动推断祖先；公开 serialize_public 未增加私有字段。

新增 budget_tree 42 项，覆盖线程竞争最后额度、等待父亲、三层预算、重复消费、
追溯重绑、伪造 metadata、陈旧 owner、取消队列、提交前后保存异常、重启与
损坏快照。最初三项红测因新 API 缺失，不记为三个生产端到端反例。首次小矩阵
49 通过；扩展 185 通过、1 失败为恢复测试漏 claim，补齐测试而未放宽 lease。
Windows 首轮 192 通过；Linux 首轮 **1824 通过、1 失败**，是本批跨循环检查
错误扩大到整个 Store，影响同 Store 的独立视觉评测根任务，不能归为上游失败。
新增最小反例确认后，将并发 owner 约束限定至具体预算身份，仍拒绝同一根预算
跨循环复制额度。未改动视觉实现或删除原视觉断言。

最终 Windows 七文件 **199 通过**（144.68 秒），包含视觉评测；断网、只读、
非 root 固定 Linux 镜像 64 文件 **1826 通过**（81.32 秒）。Windows 199 项
逐名包含于 Linux JUnit；五条依赖弃用 warning 保留。不是 server/tests 全量，
也不是最新 main 集成、真实 Provider 或预览验收。

冻结 `budget-tree-final02-source`，基线 `e3ac4555`，84 个 overlay 路径，
147128320 字节，SHA-256：
`be49f18834b62db73da222c51af1153e1be95dd0d55f42cfdc146555404ada7c`。
验证后 84 个源码哈希一致；本节为验证后追加记录。相较上一 dispatch-guard
快照恰为开工指定五路径。三个 Python 文件内存语法通过，全 Diff 空白检查通过；
五文件限定凭据扫描只曾误报历史 agent-task 文件名，确认后带词边界扫描零命中，
不等同全仓库安全审计。暂存为空，无 Commit/Push/PR/Deploy 或真实外发。

证据：`C:\tmp\modelmirror-r8-verification-20261008\r8-evidence` 下
`r8-budget-tree-{red01,red02,focus01,focus02,windows-final01,windows-final02}.xml`
及 `r8-budget-tree-linux-final{01,02}-junit.xml`。Linux 验证器仅增加 budget-tree
模式，保留原模式。原失败源码与报告未覆盖。

**未关闭**：后台 Handoff/Goal 的生产登记/消费接入、固定目标与输入发生身份、
未决请求恢复与实际父子调用证据。不能通过 runtime_metadata 父 ID 或复制
ContextVar 代替；不能仅凭本批绿测宣称后台根预算已端到端完成。后续继续核对
Legacy fallback 逐请求计数、运行目录互斥、最新上游/Coding 修复集成、最终
全量与前端、独立预览和人工验收；没有进入 R8-B，暂不要求人工或模型额度。

### 后台父子根预算：Handoff 接入批开工契约

延续当前独立树，五路径：main、execution_store、独立 handoff_budget 测试、
本卡、Runtime 文档。已有预算的 Workflow/Xpert 在四处 Handoff 就绪入口前
登记父预算委托，固定目标发布版本及输入/附件授权等调用语义摘要。摘要与
Handoff 发生 ID 共同组成受限委托键；不保存正文，不使用可变 workflow_task_id
推断祖先。实际执行从 Store 查找唯一登记，验证摘要后创建并绑定子任务，
必须在首个节点派发前完成。重复调用仅可读取确定完成的结果；已绑定但结果
未确定时不得新建子执行或自动重发。恢复使用原子执行的已保存父关联。

不新增 Planner 能力或授权传播，不改 Goal 产品预算。Goal 本身尚无调用总额
契约，不能把每个步骤独立预算冒称 Goal 全局预算；本批先验证已存在 Workflow
根的后台链路。旧无预算 Handoff 保持旧路径。先真实 Runner 加替身 Provider
的离线测试，再扩展 Handoff/Goal/预算/恢复矩阵，保留失败证据。无真实调用、
业务写入、共享栈或提交；回退关闭新派发入口但保留已登记/消费的恢复读取。

本批范围增至六文件：追加 `server/xpert_runtime/agent_tasks.py` 的领取快照边界。
focus03 反例证实 claim 返回 Store 可变对象，lease 替换会同时改变旧持有者的
比较 token；必须在领取锁内复制，而非仅修改调用方测试。派发入口再固定 token，
每次预算派发核对当前 lease。该修改与根预算后台接入属于同一旧执行者隔离目标。
保留 focus03 的 1 failed / 72 passed 原报告，不作为通过证据。

### Handoff 根预算接入：扩展验证与新增阻塞

focus04 五文件测试 95 passed；包含固定委托漂移、父取消、预算耗尽、lease
过期/替换、两次调用间失权、完成结果复用、未知子执行禁止重发及父指针伪造。
扩大用例后冻结 `handoff-budget-final01-source`：85 个 overlay 路径，
147159040 字节，SHA-256
`d88d6e78716c060336b276149c8b79636afaf0f1278341661f73758c536af8a2`。
断网 Linux 65 文件矩阵 1845 passed（90.72 秒），不是全量 server/tests。
真实 Runner/Executor/Coordinator 配合离线 Provider，包含 V2 agent_handoff 和
handoff_router 的 Store 重开续跑。四个 Python 文件内存语法通过；Diff 空白
检查通过；限定六文件凭据模式零命中，不等同全仓安全扫描。暂存为空。

额外离线反例 `probe_handoff_late_settlement.py`（仓库外证据目录）：目标回调
将 lease 切换为新持有者后抛 OWNER_LOST，旧 Executor 的 `_finish_failure`
仍写入 dead_letter/failed 并清除新 lease；断言失败，Provider 调用为 0。
这证实结算侧仍缺领取令牌的原子条件写，不能以 1845 绿测替代该安全门禁。
下一批优先修复成功/异常/等待结算的所有权隔离与跨 Store 投影，不用派发前
检查冒充结算事务。本批不继续扩大到 Executor 重构，未关闭本轮 PR 门禁。
原失败、合成 Store 与源码保留在独立证据目录；无真实模型、共享栈、提交或发布。

Windows 扩展九文件矩阵最终 193 passed（358.30 秒），报告
`r8-handoff-budget-windows-final01.xml`；Linux 报告
`r8-handoff-budget-linux-final01-junit.xml` 为 65 文件、1845 项、零失败/错误。
Windows 用例集合为 Linux 子集。冻结后源码哈希仍一致，仅 Runtime 文档与
本任务卡追加了已验证的阻塞说明；最终 Diff 空白检查通过。以上不替代最新
上游集成、server/tests 全量、前端构建、独立预览及人工验收。

### Handoff 结算所有权批开工契约

用户明确要求覆盖迟到成功、失败及取消竞态。沿用独立分支与既有 85 路径改动，
本批仅允许 agent_tasks.py、handoff_executor.py、新 settlement 测试、本卡与
XPERT_RUNTIME.md 五文件。先保存旧实现反例，再将领取 token/lease/父终态校验
与 Task/Handoff 更新放在同锁同次持久化中；运行投影从当前 Store 快照读取，
不以旧 callback 的 result/error 推断。持久化失败不得继续写成功投影。
不新增公共 API、模型调用、业务存储或 Planner 节点；不修改 main/Goal 产品策略。
风险为高（稳定执行与文件持久化），回退须暂停新派发并保留现有快照，不能放开
失权结算。先 pytest settlement/Handoff/预算，再断网 Linux 扩展矩阵；不提交。

本批反例 red01：10 failed / 5 passed，覆盖失权与过期后的成功、异常、重试、
审批等待与客户端等待。focus01 46 passed，focus02 35 passed，focus03 新增
非法 lease、同一次持久化与投影重放去重后 43 passed；Windows final02 九文件
250 passed。均为离线合成调用。
Linux final02 在原有 `test_executor_cancellation_wins_dispatch_or_late_result`
的 before_dispatch 窗口阻塞：checkpoint 持有源锁，取消无法进入而回调等待
取消完成。已停止本批临时容器，保留原日志，不宣称该轮通过。实现调整为只在
源锁内投影本地 Run 状态，checkpoint 在锁外执行，之后重新读取源状态；没有
修改原验收测试。focus04 原取消测试与新增结算测试共 67 passed。最终矩阵须重跑。

### Handoff 结算批最终证据

- 最终 Windows 十文件：274 passed / 47.03 秒，
  `r8-settlement-windows-final03.xml`。
- 最终断网 Linux 66 文件：1888 passed / 78.04 秒，
  `r8-handoff-settlement-linux-final03-junit.xml`。Windows 全部用例为其子集，
  不是 server/tests 全量，也不是付费 Provider 验收。
- 新增 43 项确定性测试：迟到五类回调、lease 替换/过期、取消提交前后顺序、
  新持有者真实接管完成后旧回调、保存前失败/替换后异常、提交后投影中断与
  Store 重开、重复同步去重、非法 lease、Task/Handoff 单次持久化。
- 源快照 `settlement-final03-source`：基线 e3ac4555，86 overlay，147169280
  字节，SHA-256 `b5e6d8f761847c0e3fd8e49fb08332a44eb2b49586bd149397ee328c7e28fbd4`。
  测试后 86 路径哈希一致；本节为验证后追加。相对上一批恰为开工五路径。
- 三个 Python 文件内存语法、Diff 空白检查通过；五文件限定凭据模式零命中，
  不冒称全仓安全审计。无暂存、提交、PR、真实模型、共享栈或 R8-B 操作。

旧 worker 结算覆盖接管者的已复现缺口在本批关闭。新的 Store 方法同时提交
任务与交接；Registry 是可恢复的本地投影而非第二份终态权威，checkpoint
不持源锁，慢观测后的状态变化触发受限重查。保存结果不确定时阻断该实例后续
持久写入，重开核对磁盘，不退款、不重发目标。旧 Workflow/等待记录无需迁移。
回退不得直接恢复无所有权检查的结算；先暂停新派发，保留快照及人工核对能力。

仍未宣称关闭：全 R8-A 最新上游及独立 Coding 修复集成、全量后端/前端、
运行目录单实例排斥、剩余父子入口预算与不确定请求核对、独立预览/人工验收。

### Legacy 流式回退逐请求预算批开工契约

继续现有独立分支，不撤销前批改动。本批只修改 main.py 的 Legacy Workflow
流式入口、新增 test_workflow_r8a_legacy_fallback_budget.py、本卡及 XPERT_RUNTIME.md。
已读到原请求与回退请求共享一个 execution_operation，先以真实函数和离线
MockTransport 证明是否少计请求，再作局部修复。目标是每个实际请求独立预算
准入，复用已有父子持久预算权威；不扩大回退条件，不新增重试、模型或路由。
风险为高（稳定模型派发边界），无数据迁移/新依赖/公开接口变化。
验收：预算 1/2、父子计数、失败/不确定响应、取消/失权、落盘失败、并发和
Store 重开；先窄 pytest，再既有派发/预算/Workflow 回归及断网 Linux 矩阵。
回退不得恢复预算穿透；出现回归先暂停该回退派发并保留证据，不恢复已用额度。
真实调用、共享栈、提交及 PR 均不在本批范围；完整 PR 门禁另列。

离线 red01：18 项中 9 failed / 9 passed。预算上限 1 仍发送原请求与回退请求；
上限 2 的实际两次请求只留下 1 次计数；第二次请求绕过持久预算写入。
反例快照 fallback-red01-source（87 overlay，SHA-256
98d9585278f7f4ffda7da641322d073f8e6c45a43ab44bbaafc8e53569fb35fd）
及 r8-fallback-red01.xml 已保留。修复仅在现有回退派发前调用统一原子计数入口，
保留已持有的并发槽位，避免 max_concurrency=1 时再次申请同槽造成自锁。

focus01 五文件矩阵 131 passed / 1 failed：预算穿透反例已通过，剩余失败是
测试替换整个 _persist_unlocked 抛出 OSError，却断言其内部包装异常。没有
修改生产异常行为或放宽断言，改为在实际 os.replace 边界分别注入替换前/后
失败，同时验证磁盘计数、内存回滚及 Store 不可用；新增子上限和父身份去重。

### Legacy 流式回退预算批最终证据

- Windows focus02：五文件 136 passed / 21.53 秒，报告 r8-fallback-focus02.xml。
  命令为 `python -B -m pytest` 加 legacy_fallback_budget、dispatch_guard、
  budget_tree、budget_journal、test_xpert_execution_budget 五文件，
  `-q -p no:cacheprovider`，独立 basetemp/JUnit 目录。
- 冻结源码断网 Linux：68 文件 1948 passed / 87.71 秒，报告
  r8-legacy-fallback-linux-final01-junit.xml；Windows 用例全部包含其中。
  使用现有 verify_linux.py 的 legacy-fallback 模式、缓存镜像、非 root、
  只读源码与 network none；不等同 server/tests 全量或真实 Provider 验收。
- 新文件 22 项覆盖：两次请求分别计数、根/子独立上限、内联父身份去重、
  无回退单次计数、无预算旧行为、失败与不确定响应不追加请求、取消/失权、
  消费者关闭、替换前/后存储失败、Store 重开及单并发槽位释放。
- 快照 fallback-final01-source：e3ac4555，87 overlay，147189760 字节，
  SHA-256 d6bb06cd51278dda2bf251eaf4ccbed88737e1361a005ddf38e1234953a9cf64。
  测试期间全部 overlay 哈希一致；本节为验证后追加。
- 两个 Python 文件内存语法通过，git diff --check 通过，暂存为空；本批四路径
  限定凭据模式零命中，不冒称全仓安全审计。生产代码相对 red01 仅增加两行
  （注释及 charge_execution_step），原回退判断及持有槽位生命周期未修改。

本批关闭 Legacy Workflow 流式回退少计一次准入的已复现缺口；保留所有失败
证据，不将预算记录当作供应商账单或全链路请求幂等证明。未调用真实模型，
未操作共享栈，未提交/推送/创建 PR。剩余门禁仍为内联子调用等预算入口、
不确定请求全链核对、运行目录单实例排斥、最新上游及 Coding 修复集成、
全量后端/前端、完整敏感扫描、独立预览与人工验收；不进入 R8-B。

### 内联 External Xpert 根预算批开工契约

用户要求先复现再最小修复。沿用 e3ac4555 独立树，保留已有 87 路径改动。
目标是内联子调用在首个操作前绑定真实持久父预算，脱离 ContextVar/重开后
仍计入原根；同时验证外层 tool_call 持槽是否导致单并发子模型自锁。
允许六路径：execution_budget.py、tool_runner.py、main.py、新 inline_budget
测试、本卡、XPERT_RUNTIME.md。超出默认五文件是因为预算身份读取、工具
并发准入和 Runner 绑定属于三个所有权边界，必须一起验证，不合并职责来凑数。
复用现有发生登记/单次绑定与固定输入摘要；缺失身份、漂移或已派发未知结果
均 fail-closed，不以任意 metadata 伪造父身份，不支持新嵌套等待能力。
不改 Store 格式、Planner、公共 API、资源权限或附件传播；无新增依赖。
风险高：稳定执行与持久预算。先真实函数/离线 Provider 的红测，再预算、
Tool Runtime、资源绑定、Handoff 与断网 Linux 矩阵；全量和预览仍单列。
回退须暂停内联派发并保留父子关联，不可把已有子任务恢复成新根预算。
不提交、不部署、不调用真实模型、不触碰共享栈，不进入 R8-B。

red01 三反例全部失败：子执行无持久父关联、Store 重开后同一调用重跑、
根并发 1 时外层工具持槽导致子模型等待自锁。快照 inline-red01-source，
88 overlay，SHA-256 a09002ced21f74e1c1e984d124621ddee2398f2059dc8d8037a3aaa5ac6cf425；
报告 r8-inline-red01.xml。最小实现 focus01（三文件矩阵）28 passed。
补核错误传播需第七路径 external_xpert_toolset.py：原 Provider 把预算/所有权
异常包装为普通工具错误，V2 Agent 可继续让模型处理，必须保留 fatal 拒绝。
同一父执行已有未成功结算的内联子项时，不允许更换调用位置自动重发。

中间验证保留：focus02 为 89 passed / 1 failed，失败在测试重开 Store 后没有
领取父 lease；补齐真实领取步骤，不放宽生产检查。focus03 为 23 passed /
2 failed，测试错误地将 NativeWorkflowDefinition 直接传入 WorkflowRunRequest，
改为原生 dict 后 focus04 为 24 passed / 1 failed。剩余失败揭示测试对生产路由
的假设错误：绑定 External Xpert 的 workflow_agent 在 V2 开关开启时仍走原
兼容策略。撤掉本批不可达 V2 路径的调用序号变更，不开放新策略；测试改为
在两种开关状态下确认兼容路径、两次子调用及根模型 5 / 工具 2 次计数。
focus05 七文件矩阵 125 passed / 33.34 秒，报告 r8-inline-focus05.xml。
继续补真实定时等待、迟到取消/接管与普通工具伪造委托元数据反例，再冻结源码
执行 Linux 扩展矩阵；不将中间通过描述为全 R8-A 门禁完成。

### 内联 External Xpert 根预算批最终证据

- Windows focus06：七文件 129 passed / 33.07 秒，r8-inline-focus06.xml。
  命令为 `python -B -m pytest` 加 inline_budget、resource_nodes、
  test_xpert_execution_budget、budget_tree、test_xpert_runtime_tool_runner、
  test_xpert_runtime_toolset、test_agent_strategies，`-q -p no:cacheprovider`，
  使用独立 basetemp/JUnit，不连接 Provider。
- 冻结源码断网 Linux：73 文件 2019 passed / 86.48 秒，报告
  r8-inline-linux-final01-junit.xml。现有 verify_linux.py 的 inline-budget
  模式包含本批七文件及前批预算、取消、恢复、结算回归；非 root、只读源码、
  network none、缓存镜像，未安装依赖或操作共享服务。
- 本批新增 29 项：真实 Runner 首派发前持久绑定、同发生重开复用、未知结果
  换位置仍拒绝、上下文丢失后的根上限、两分支共享并发、输入/版本/来源漂移、
  缺失/伪造身份、领取失权、准备期间取消、迟到完成、真实定时等待不算成功、
  普通工具伪造委托元数据不可绕过并发、生产策略兼容路径和零工具预算。
- 源码 inline-final01-source：e3ac4555，90 overlay，147240960 字节，
  SHA-256 d662543ac7be224114606b129e48aa633254c8177d745835e3b3317608024cc4。
  测试后 90 路径哈希一致；相对 inline-red01 的同基线增量恰为约定七路径。
  本节是验证后追加的文档记录，不改变被测生产代码。
- 五个 Python 文件内存语法通过；首次 PowerShell 文本管道受本机编码影响，
  改以原始字节的 Base64 管道重跑同五文件后通过，未改源码编码。全工作树
  git diff --check 通过，暂存为空；七路径限定凭据模式零命中，非全仓安全审计。

结论：本批三项原始红测缺口关闭。复用已有持久预算委托，不新增 Store 格式、
Runtime、公开 API、Planner 能力或 V2 策略许可。计数是准入记录，不是供应商
账单；恢复复用已提交结果，不宣称未知外部请求可自动恢复或自动重试。
回退先暂停内联派发并保留持久父关联，不允许清除关联后以新根执行。

剩余门禁继续限于 R8-A：
1. 运行目录单实例排斥及跨进程崩溃/重开验收。当前 execution_store 构造器只
   有实例内 RLock，不能作为两个后端不能同时占目录的证据；下一批先复现。
2. 其余父子入口、期限与不确定请求的跨 Store 综合故障注入。Goal 总预算仍无
   独立产品契约，不借本批扩权；不把局部委托绑定当作全入口恢复完成。
3. 最新上游和独立 Coding Windows 修复集成，再运行最终全量后端、前端类型/
   单测/生产构建、全 Diff 与完整敏感扫描。
4. 独立预览及人工验收、帮助中心证据。真实模型仍需精确授权；本批未调用。

无 Commit、Push、PR、部署或共享栈变更，不进入 R8-B。

### 运行目录单实例批开工契约

继续 e3ac4555 独立树，保留已有 90 路径改动。当前构造 WorkflowExecutionStore
即会将 running 转为 ready/waiting 并落盘；startup 钩子加锁不能保护构造期。
本批先以真实子进程、合成目录及生产初始化语句复现第二进程改写活执行，再用
标准库 OS 文件锁保护后端进程生命周期。锁须早于 main 的 Runtime Store 构造，
持有到进程退出；shutdown 不释放仍可被在途请求使用的 Store 所有权。
离线 restore-backup 同时取得相同锁，inspect 维持只读；不改快照格式或 Store
单元构造行为，不建立多进程共享执行能力，不以 PID 文件存活推断代替 OS 锁。
限定六文件：main.py、新 xpert_runtime/runtime_directory.py、
execution_store_recovery.py、新 test_workflow_r8a_directory_owner.py、
XPERT_RUNTIME.md、本卡。多于五文件是入口/锁内核/恢复入口必须同一目标收口，
测试和操作说明不可缺；不改其他生产路径、依赖或 Planner。
风险高（进程启动、文件与恢复边界）。测试：双进程拒绝及原快照不变、持有者
强制退出后重新领取、不同目录互不影响、同进程重复领取、符号链接/异常路径、
继承旧句柄、恢复工具拒绝活目录、startup 所有权检查及原恢复回归；然后冻结
断网 Linux 扩展矩阵。所有目录为临时合成目录，不启动后台业务或调用 Provider。
回退须先停止所有该目录写者，不删除锁文件抢占，不恢复允许双后端同时写入。
本批不提交、不部署、不操作共享栈；全量、最新集成及真实预览仍单列。

red01：1 failed / 1 passed，第二进程退出码 0，将首进程仍持有的 running
记录改为 ready；首进程被终止后能恢复为 ready 的正向对照通过。红测使用
main 的目录初始化语句切片和真实 WorkflowExecutionStore，未启动整个 ASGI。
报告 r8-directory-red01.xml，快照 directory-red01-source，91 overlay，
SHA-256 b38ac47ff156abbac7e43b3d82a09abfd4f2cf7a905ad2382f6e1d54f9fd9de4。
加入 OS 所有权及恢复入口保护后 focus01：105 passed / 1 skipped；扩展攻击
focus02：114 passed / 3 skipped。Windows 两项符号链接权限不可用及一项 POSIX
fork 不适用明确保留，由 Linux 重跑；不将 skipped 计作通过。

Windows focus03：116 passed / 3 skipped（29.04 秒）。冻结 final01 的断网 Linux
75 文件矩阵为 2067 passed / 0 skipped（101.24 秒），补齐链接与 fork 边界。
该次 fork 测试提示 pytest 已有线程，因此将故障注入移到独立新进程并设超时，
不改变生产行为或断言，继续 final02 冻结回归。
完整 main 模块导入冒烟通过：第二后端在加载执行快照前拒绝，原快照字节不变；
首后端被强制终止后，新后端恢复为 ready。回执 r8-directory-full-boot01.json，
对应 final01 源码 SHA-256
0c05d6cae721cff5fd6fd2e5ae910f22dc167b7f423bae218608023e4cac49f2。
冒烟为断网容器、完整模块导入，未启动 ASGI 后台服务或 Provider；不等同 UI/HTTP
验收，也未复用真实运行目录或凭据。

### 运行目录单实例批最终证据

- Windows 最终三文件命令：`python -B -m pytest` 加
  `test_workflow_r8a_directory_owner.py`、`test_workflow_retry_store_regressions.py`、
  `test_controlled_write_store_trust.py`，`-q -ra -p no:cacheprovider`，独立
  basetemp/JUnit。focus03：116 passed / 3 skipped；跳过原因见上。
- Linux final02 使用同一 75 文件断网矩阵：2067 passed / 0 skipped，
  114.73 秒，r8-directory-linux-final02-junit.xml。fork 在独立新进程执行后
  不再产生多线程 fork 警告，原断言保留。剩余五项为既有依赖弃用提示。
- 新增 15 项检查：第二进程不改写活状态、强制退出后释放、恢复工具互斥、
  同进程/路径别名拒绝、不同目录独立、残留锁文件非 PID 权威、默认目录一致、
  校验失败释放、目录/硬链接/符号链接拒绝、打开失败脱敏、启动顺序、fork
  不能继承所有权或释放父锁。未通过降低旧恢复校验换取绿测。
- directory-final02-source：e3ac4555，93 overlay，147281920 字节，SHA-256
  0b1577d53afe49ad21d366b5e7eab23eb6f65a844352bd7d92063749e80ff195。
  测试后全部 overlay 哈希一致；相对 red01 恰为约定六路径。final01 至 final02
  只有测试/文档变化，生产代码哈希一致，完整 main 导入冒烟仍对应同一实现。
- 四个 Python 文件内存语法通过、git diff --check 通过、暂存为空。六路径
  限定凭据模式零命中，非完整仓库安全扫描。本节为验证后追加。

本批关闭进程级目录互斥的已复现缺口；不承诺多进程共享、对抗同用户恶意删锁、
网络文件系统锁语义或裸 Store 调用自动受保护。锁保持到进程退出，失败启动须
退出进程后重新启动；回退先停写，保留原快照，不删除运行中锁文件抢占。

下一门禁为跨 Store 的综合恢复、期限及不确定请求证据收口，再进行最新上游/
独立 Coding 修复集成和最终全量后端、前端、完整敏感扫描。独立 HTTP/页面预览、
帮助中心与人工验收仍在 PR 前完成。本批无模型调用、共享栈操作、Commit、Push、
PR 或部署，不进入 R8-B。

### 跨 Store 超时取消恢复批：范围与验收

- 目标：复现 Handoff 超时决定已落盘、子执行取消尚未完成时的重启窗口；
  核对协调器是否错误恢复父工作流，以及迟到结算能否越过超时决定。
- 限定六路径：AgentTaskStore、WorkflowHandoffCoordinator、main 的超时回调、
  新增专用回归测试、本任务卡及 XPERT_RUNTIME 文档。跨 Store 边界需要三处
  生产入口一致，因此超出默认五文件限制；不改节点契约或增加 Planner 能力。
- 先以真实临时 Store 和生产回调构造故障，记录红测；仅在可复现后增加
  原子超时决定与可重入取消协调。不得把不确定外部请求重新派发。
- 验收：落盘前/后失败、取消传播失败、重启与重复扫描、完成/超时竞争、
  旧 worker 迟到及故障条目不饿死其他条目。先窄测，再已有 R8-A 断网矩阵。
- 回退：停止协调器后恢复代码，保留已有超时终态；不得恢复运行或重发请求。
  本批不进行模型调用、共享栈操作、提交、PR 或 R8-B 实施。

#### 超时传播红测与实现

- timeout-red01：1 failed。生产超时回调在交接死信事件后注入异常，再重开
  AgentTaskStore 和 WorkflowExecutionStore。协调器恢复父流程时观察到
  `(pending, waiting, running)`，而非任务、子执行及 Registry 均取消。
  这不是模型输出问题，也不是依赖或计时波动。
- AgentTaskStore 增加原子超时决定：同一锁内提交任务取消与交接死信、撤销
  领取权限；已有其他终态不覆盖。保存失败将实例标记结果不确定，必须重载。
- 协调器对 `HANDOFF_TIMEOUT` 终态在领取父执行前重新完成本地取消传播；
  可处理 waiting/ready 及旧分裂快照。传播失败不恢复父流程，也不阻塞其他
  待办。不调用目标、不新增重试预算、数据库或调度能力。
- timeout-focus01：86 passed。focus02：105 passed / 1 failed，新增真实 Runner
  用例误把协调器正常返回计数当作失败终态恢复计数；生产 Runner 已提交失败并
  清除 lease，后续领取校验返回冲突。只修正计数断言，保留真实失败终态、
  错误码、Registry、零模型调用和预算断言。原五文件 focus03：106 passed。
- 继续补足到期边界和矛盾终态拒绝后，执行冻结源码的既有综合矩阵。

#### 超时传播批最终证据

- 新增 `test_workflow_r8a_handoff_timeout.py` 共 22 项：提交后观测失败重启、
  waiting/ready 的子 Store/Registry 故障、保存前后不确定结果、五种迟到结算、
  三种终态先提交、旧分裂快照、故障条目公平性、缺少协调器、实际 Runner
  失败终态、未到期/已到期以及矛盾终态阻断。全部为合成离线证据。
- Windows 原五文件 focus04：109 passed / 0 skipped，12.25 秒，
  `r8-timeout-focus04.xml`。重跑了此前同一失败检查，未以不同检查替代。
- Linux 缓存镜像、`--network none`、只读源码/根文件系统，76 文件综合矩阵：
  2089 passed / 0 skipped，107.47 秒，`r8-timeout-linux-final01-junit.xml`。
  五项均为既有 FastAPI/PyPDF2 弃用提示。
- 冻结输入 `timeout-final01-source`：HEAD e3ac455553c2d3065bcd8768d78b849c8477392a，
  94 overlay，147312640 字节，SHA-256
  `31fb2b28587df3002b6db6b9ad90d3875838d741b25b91219baef2a2d7365948`。
  相对上批冻结版本仅本批六个约定路径变化，测试后 94 路径哈希全部一致。
- 四个 Python 文件内存语法通过；git diff --check 通过；暂存为空；六路径
  限定凭据模式零命中，不等于全仓敏感信息扫描。本节在冻结验证后追加。

本批关闭已复现的 Handoff 超时传播断点，不宣称远程请求可以撤回、外部扣费
可退或所有长运行集成门禁已完成。工作树仍是原独立分支；未提交、Push、PR、
部署或访问模型，未改共享栈。后续须继续核对综合恢复证据余项、最新上游与
独立 Coding 修复集成，再完成最新全量后端、前端、完整安全扫描及独立预览/
帮助中心/人工验收。未进入 R8-B。

### 剩余门禁收尾：上游集成与最终验证

- 用户要求开始收尾剩余项。本批先锁定远端 main
  `6ff70de608b7ef7972a4ed023f38ce7f44110635`，原基线后 20 个提交、372 个路径；
  与当前修改交叉仅 models.refresh.test.ts、main.py、model_router/repository.py、
  model_router/workload_control.py。保留原工作树，建立独立集成树，不覆盖旧证据。
- 集成范围为已验证的 R8-A 修改与已授权的 Coding Windows 三路径修复；
  不新增 Planner 节点、依赖、Store、外部授权或业务范围。机械合并涉及累计
  多文件，不按功能新批拆散，以固定源码、冲突审查和完整回归作为单一门禁。
- 先检查三方冲突与上游交叉语义，运行针对性矩阵，再运行后端全量及前端
  typecheck/test/build；失败必须归因并重跑，不能用旧基线结果代替。
- 独立预览、帮助中心及人工验收在集成回归通过后继续；不借本授权调用真实
  模型、改共享栈、提交或创建 PR。回退只放弃新集成副本，不回滚原工作树。

#### 2026-10-13 集成过程与前端收口

- 独立树 `C:\tmp\modelmirror-meta-planner-r8-a-integration-20261013`，分支
  `codex/meta-planner-r8-a-integration-20261013`；原 R8 和独立 Coding 工作树保留。
  机械三方合并唯一文本冲突为 main startup，同时保留 Runtime 目录独占与上游
  Provider storage 显式启动，均在后台恢复服务之前。未恢复旧的构造器自动开库。
- 冻结 final01 的 76 文件断网集成组：2083 passed / 6 failed。六项来自
  Managed occurrence 重启夹具使用上游已经改为惰性的 Repository 构造器，错误为
  `provider_storage_not_owned`。仅将两处重启夹具改为关闭旧实例并显式 `.open()`；
  原派发次数、回执与重放拒绝断言保留。原测试文件 Windows 重跑 23 passed。
- final02 源码 SHA-256 为
  `7c37a46f07dca7eaf1b4409a01cf4a1e1e26f7b6c9cf5147b2f0c49ec91da7e1`，
  基线 `6ff70de608b7ef7972a4ed023f38ce7f44110635`，97 overlay。
  后端全量以此输入执行，前端后续仅修改下述两份测试，不改变后端源码。
- 首次 full01 在 pytest 前因验证容器 tmpfs 未允许执行而报 `tsc: Permission denied`。
  full02 对独立容器的临时目录明确允许执行，未改镜像、生产权限或跳过 Worker 构建。
- 前端首次 typecheck 缺少 Card Replica 测试依赖；按 CI 安装两份未变化的 lockfile
  中已有依赖，使用 offline/ignore-scripts，未改依赖清单。typecheck 重跑通过。
- 前端全量首跑：1246 passed / 3 failed。两项目录计数断言仍按 10 月 7 日计算，
  与上游 10 月 13 日快照不一致；固定时间改为 models.ts 所记录的快照时刻，保留
  703/582/96/25 的数量断言和独立过期边界测试。第三项写死已过期的 qwen3-8b；
  其生产文件、测试和目录均与上游 HEAD 完全一致，单文件原样复现 1 failed / 5 passed。
  改为从真实目录第 49 项取样，仍验证前 48 项以外的精确检索和选择保留，不改筛选逻辑。
- 三文件窄测 84 passed；随后原 `npm.cmd run test:run` 完整重跑：159 文件、
  1249 测试通过，后续 Node 17 项通过。`npm.cmd run build`、`npm.cmd run typecheck`
  通过；构建仍有大 chunk 提示，不通过提高阈值掩盖。
- `npm.cmd run verify:help-images`：29 篇已注册文章截图检查通过。
  这只验证现有资产，不等同本轮帮助说明与独立预览实操验收。
- 断网冻结源码 Worker `npm run build`、`npm run test:all` 通过；
  `docker compose --env-file NUL -f docker-compose.yml config --quiet` 通过，未启动共享服务。
- 98 个变更路径的产物规则和四类凭据特征扫描零命中，86 个 Python 文件内存语法通过。
  该扫描有明确规则边界，不宣称能发现任意未知密钥格式。

后端最新全量仍运行中，未记录通过。独立 HTTP/UI 预览、帮助内容同步与人工验收仍待关闭。
本节不改变此前失败记录，也不以真实 Provider 证据描述任何离线测试；未提交或创建 PR。

#### 2026-10-13 最终全量与独立预览

- final02 冻结源码的原后端全量检查已结束：10300 passed / 39 skipped /
  8 warnings / 34 subtests passed，1937.22 秒。JUnit 为
  `r8-integration-full02-junit.xml`。保留 full01 环境失败与 focus01 夹具失败，
  不以窄测替代原失败检查。执行为缓存 Linux 镜像、禁止联网、无凭据。
- 39 项跳过涉及平台、可选解析器/渲染器及专用依赖；不宣称所有环境全绿。
  Windows 四文件补测 33 passed / 3 failed / 2 skipped，三个失败均为未修改的
  Coding Unix socket 路径在 Windows 缺少相应 asyncio 接口；相同测试在上述
  Linux 全量通过。本批不扩展 Coding 协议范围。权限/原子写入子组另有
  32 passed / 2 skipped，其中七项 Windows 原生权限测试均通过；两项 symlink
  受系统权限限制跳过。保留两份独立结果，不用子组通过覆盖扩展组失败。
- Worker 离线 build/test:all：75 本地测试、145 上游检查全部通过。
  完整 ASGI 生命周期验证 startup、Runtime 独占、Provider storage、health200、
  Registry200、Capability22/IR3/默认写授权为空及 shutdown；不只是导入成功。
- final03 与 full02 后端源码相同，仅含前端测试和文档后续修改；归档 SHA-256
  `a8f130002d7e1e78dd493664fd693836e3d100485fc54763882e2c3bac4c652e`。
  新预览使用该源码及已构建前端，独立临时数据、无凭据，后端网络禁止外联。
  前端仅监听本机；未改共享容器、网络或卷。
- 可见画布创建合成 input -> suspend_wait(60秒) -> output(user_input)。
  首次运行重启后跟踪原 task；持久事件为一次 input、timer_waiting、一次
  wait 完成、一次 output 与 workflow_end。重连曾处于服务启动窗口而失败，
  服务就绪后再次跟踪成功，未另行运行。
- 第二次运行等待中点击“取消原运行”；持久事件为 input、timer_waiting、
  workflow_cancelled，无 output。再次重启并超过原定唤醒时间仍为 cancelled。
  画布最近运行刷新后不保留，Runtime 进程内记录也不是本次持久终态证据；
  终态由 WorkflowExecutionStore 核对，不能将空列表描述为恢复成功。
- 帮助文章补充跟踪、重跑、取消及未知外部结果边界。实际截图、逐步实操及
  教程重放另见 `docs/help-center/evidence/r8a-durable-recovery-20261013.md`。

最终帮助构建、范围扫描与人工验收仍以其实际结果为准。本批未提交、Push、PR、
部署共享栈或调用 Provider；未进入 R8-B。回退仍须保留持久终态和不确定回执，
不能用代码回退触发旧请求重发或撤销已提交写入。

#### 最后展示反例与收尾结果

- 预览复现：持久任务 cancelled，顶部已取消，但节点卡片残留运行中。
  `buildRunSteps` 未识别 timer_waiting/workflow_cancelled，且取消 HTTP 确认
  分支未统一清理画布的运行标记。三个针对性反例先红；仅修 WorkflowRun
  前端投影，复用原取消处理，不改 SSE、后端取消或结算协议。补充取消不能覆盖
  done/error/skipped 的测试，完成先到的取消竞争用例仍保留。
- 原两文件测试先重跑 28 passed；补齐终态保持断言后，后续全量覆盖所有新增项。
  UI 重建复核：等待卡为“等待恢复”，取消后为“已取消”，输入保留完成，
  输出不执行。跟踪不新增任务；最终教程从空页面状态重放同样通过。
- 前端 final02 全量 1250 passed / 2 failed：帮助旧文章步骤数及截图基线不匹配。
  恢复操作拆为独立 `recover-workflow-run`，绑定 6ff70de6，并保留旧重试教程
  与旧截图基线；补齐一级入口、搜索与严格基线断言，没有放宽帮助校验。
- 原 `npm.cmd run test:run` final03 完整重跑：159 文件、1253 tests passed，
  424.49 秒；后续 17 Node tests passed。最终 typecheck、build、
  verify:help-images（30 篇）均通过，保留构建大 chunk 提示。
- 对照 full02 冻结输入，当前 89 个后端修改路径哈希全部相同；前端收尾
  未改变后端全量所验证的源码。远端 main 再核验仍为 6ff70de608b7ef7972a4ed023f38ce7f44110635。
- 108 个修改/新增路径：86 个 Python 文件内存语法通过；禁止运行产物规则与
  四类凭据模式零命中；git diff --check 通过，暂存为空。扫描不覆盖任意未知
  密钥格式；截图只含合成内容，原件与转换哈希留在本地证据目录。

本轮约定的本地自动化、最新集成、离线独立预览及帮助重放已完成。
保留前述 39 项 Linux 跳过、Windows Unix socket 三项平台失败和真实 Provider
未调用边界，不称全平台全绿。提交前仍需用户对最终界面/限制进行人工确认，
随后按独立授权提交和创建 PR；本次没有执行这些动作，也不进入 R8-B。
