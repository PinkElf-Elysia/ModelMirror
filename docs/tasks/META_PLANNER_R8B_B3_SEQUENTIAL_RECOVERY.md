# R8-B B3：顺序子项恢复取证

## 开工契约

- 基线：`ba2e5022461c2b414e4aa0fd973cace618d58472`，PR #421 已合并。
- 工作树：`C:\tmp\modelmirror-r8b-b3-sequential-recovery`；分支：`codex/r8b-b3-sequential-recovery`，创建时干净。
- 上批远端 CI 核验时：Frontend quality、Windows Project Host、File readiness、AI Research 有效路由/边界检查通过；Backend quality 仍在剩余后端测试阶段。Provider 审计、Worker 和工作流契约步骤已通过。未把运行中描述为全绿。
- 本批先补批量发生标识与 B2 预算/恢复组合的离线证据，仅修改本卡及 `server/tests/test_workflow_r8b_batch_recovery.py`。不修改生产实现、依赖、公共接口或持久结构。
- 不重跑已有工作流或预览任务，不访问模型、业务数据或共享栈；测试仅使用临时 Store 和合成输出。旧预览保留不动。

## 已核对事实与待验证

1. `reserve_subworkflow_batch` 已固定目标版本、条数、输入摘要及根执行；同批发生标识按父执行与节点确定。
2. 现有 iteration V2 按索引顺序调用 `run_private_subworkflow_call`，收集结果后输出；B2 已给该桥接入口增加逐调用契约与根预算绑定。
3. 现有迭代运行测试主要覆盖正常顺序与类型结果；已有批量 Store 重载测试不能替代整个父 Runner 的中途恢复。
4. 新探针验证：第一项完成后重开 Store 并认领父任务，第一项复用不增加派发，第二项不能越过根预算；逐项输入变化即使复用原批次摘要也必须被拒绝。
5. 此阶段不预设生产缺陷。父 Runner checkpoint 丢失、真实新进程中间项不确定、取消发生在两项之间及完整 Provider 身份链仍需后续组合证据，不能以本次两个探针通过宣称 B3 完成。

## 验收与回退

最小验收：`python -B -m pytest server/tests/test_workflow_r8b_batch_recovery.py -q -ra --tb=short -p no:cacheprovider`。

受影响回归：同参数运行 `test_workflow_r8b_call_recovery.py`、`test_workflow_r8b_entry_evidence.py`、`test_workflow_iteration_v2.py`。无网隔离容器，源码只读挂载后复制至临时目录，所有生成数据位于临时盘。

任何失败先区分探针设置、既有兼容路径及真实契约缺口；保留失败证据，不为绿测放宽断言。需要生产修复时先记录可复现因果链及限定路径。

回退仅撤销新增测试与本卡，不涉及运行数据。未授权本批提交、推送、PR 或部署。

## 本次结果

2026-10-18，Python 3.12，复用本地验证镜像 `modelmirror-r9a1-linux-validation:4932e3b4` 的运行环境，但测试源码来自本卡固定的新工作树，不使用镜像旧源码。容器 `--network none --read-only --cap-drop ALL --security-opt no-new-privileges`；源码只读挂载到 `/source`，`server/` 复制到 `/tmp`，临时 Store 在 tmpfs，结束后容器自动移除。

- 最小探针：**2 passed / 4 warnings，9.54s，退出 0**。
- 四文件受影响回归（包括新探针）：**48 passed / 4 warnings，19.79s，退出 0**，无跳过。
- 四条警告均为既有 FastAPI `on_event` 弃用，未屏蔽。
- 已证实的边界：批量标识接入 B2 后，已完成项重载复用不再次派发；下一索引不能穿透持久根预算；单项输入漂移被调用契约拒绝且两个 Store 内容不变。
- 本次未修改生产代码，也没有证据支持为上述三项另加补丁。探针直接调用真实批量预留与子调用桥，不等于完整父 Runner 的迭代中途崩溃恢复；后续须补父级恢复入口及真实进程中断的组合反例。
- 本次没有执行全量、前端构建、用户预览或真实 Provider。上批远端 Backend quality 仍运行中；继续保留为未完成远端检查，不据本次局部通过关闭。

## 范围边界

- Planner 继续 22 类；不开放子流程、迭代 Adapter 或 Headless 权限。
- B1 实际资源权威接入保持独立前置，不能借此测试认定已完成。
- 仅单实例、固定已发布目标、有限顺序项；不开放并行、递归、嵌套等待或写入。
- 真实 Provider、计费和用户预览验收均不在本次离线探针证据内。

## 父 Runner 故障批：复现与最小修复

用户追加授权补齐完整父 Runner、中途进程崩溃和项间取消证据，发现缺口先定位再最小修复。本节是上一节仅测试阶段之后的独立小批，不改写原阶段证据。

- 修改范围：`server/main.py`、`server/xpert_runtime/execution_store.py`、新增 `server/tests/test_workflow_r8b_parent_batch_recovery.py` 及本卡。连同上一小批新增测试，当前工作树共五个变更文件；无依赖、API、Planner 或自动恢复协调器扩展。
- 测试通过真实 `_run_workflow_response` 执行父图和 iteration V2；临时发布合成子流程，模拟模型输出。子进程在第一项完成进度帧处或第二项模型计数持久化后以 `os._exit(73)` 中断，另一个全新进程重开 Store、认领父任务并以真实持久 continuation 进入父 Runner，不在测试中补写队列。
- 原始结果：**3 failed，59.12s**。两项生产缺口均为父 continuation 为空，恢复在 `validate_resume_queue` 以 `WORKFLOW_CONTINUATION_INVALID` 拒绝。第三项为探针错误地期望 `ValueError`，实际 `WorkflowExecutionConflictError` 正确阻止取消任务认领；只修测试异常类型，不将它归因生产缺陷。
- 根因：子项发生标识、结果和根预算已经持久化，但普通迭代运行没有保存父调度位置；单独调用子桥的重载测试未覆盖该层。
- 最小修复：`checkpoint_running` 在同一 Store 锁内检查 running 与 lease，校验队列后 copy-on-write 原子保存；落盘失败不泄漏内存更新并关闭 Store。父 Runner 在批量派发前保存当前节点、队列、变量及调度上下文；逐项结果仍由原子执行账本决定复用，不保存另一套项进度。
- **快照只覆盖批量执行窗口**：批量结果齐备后、下游执行前清除；不将历史批量快照用于重跑后来已经发生的下游效果。批量结束后的普通图崩溃仍 fail-closed，不宣称任意节点都可自动恢复。
- 存在暂存敏感变量或不可恢复 tool policy 的旧路径不新增批量快照，继续保持原兼容运行边界。变量过滤继续使用真实 `ephemeral_variable_names`，不清空安全名单；不能证明可恢复的上下文不作可恢复承诺。
- 修复初次复跑 **3 failed，31.81s**，是新调用漏传现有脱敏函数的必填参数；已明确补传真实运行态字段集合，未修改脱敏规则或断言。保留此实现错误记录，不将它写成基线问题。
- 增补反例：取消/完成/接管/遗漏 lease 均不能写入或清除快照；写盘失败无内存泄漏；批量后下游开始时崩溃不能复用过期快照。
- 本批不改变旧预览、不重跑用户任务、不调用真实 Provider。内部认领加父 Runner 的跨进程恢复不是自动协调器或用户恢复入口验收。

后续门禁：扩大恢复/预算/调度回归、Provider 清单语义核对（main 模块摘要因生产修复变化，不能直接刷新放行）、帮助与预览影响评估及本批最终全量。尚未完成前不得宣布 PR 就绪。

### 父 Runner 批次验证结果

- 三文件回归：`test_workflow_r8b_parent_batch_recovery.py`、`test_workflow_r8b_batch_recovery.py`、`test_workflow_iteration_v2.py`，**22 passed / 4 warnings，85.67s，退出 0**。包括全新进程恢复、第二项派发不确定、项间取消和批量后过期快照拒绝；模拟输出不等于真实 Provider 验收。
- 扩大回归覆盖 B2 call recovery、entry evidence、Provider origin，R8-A continuation、execution fencing、budget journal、resume ownership，以及 subworkflows、iteration V2、deployments、run contract、typed values，共 13 个文件。第一次 **1 failed / 309 passed，26.43s**，唯一失败为隔离容器没有 `/tmp/client/src/types/workflow.ts`，尚未执行前后端契约断言。补充同基线只读前端源码路径后，原样重跑得到 **310 passed / 4 warnings，27.81s，退出 0**；未修改断言或生产实现。
- 追加过期 lease 参数后，`test_workflow_r8b_parent_batch_recovery.py -k checkpoint`：**6 passed / 4 deselected，4.88s，退出 0**。覆盖取消、完成、过期接管、当前 lease 到期、遗漏 token，以及快照落盘失败；写入和清除均受到相同所有权检查。4 个跨进程用例此前已通过，本次有意不重复运行。
- 上述运行使用 Python 3.12 无网临时容器，不访问旧预览、外部模型或业务表。四条回归警告为既有 FastAPI 生命周期弃用；未屏蔽。
- `git diff --check` 通过；仍为五文件范围，无暂存、提交、推送或 PR。本次恢复变化不增加 Planner 能力，仍为 22 类。
- Provider 清单只读探针：首次直接在 Linux 容器运行检查命令因 Windows 工作树 `.git` 指针不可解析而报 `CalledProcessError`。随后由宿主 `git ls-files --cached --others --exclude-standard` 提供同基线文件列表，保持原 Python 3.12 扫描器和校验器执行，得到 **947 个出口候选**；唯一错误为 `source module drift`，仅 `server/main.py` 模块摘要变化，无新增、消失或未分类出口。生产 Diff 仅增加批量快照保存与清除，未修改 Provider 调用、授权、身份或预算计数入口。尚未更新清单，故此门禁仍未关闭；该探针不冒充原始 CLI 在本机工作树直接通过。
- 2026-10-18 16:33 UTC 复核 #421：已合并到 `ba2e5022461c2b414e4aa0fd973cace618d58472`，6 项成功、5 项跳过，Backend quality 仍在运行剩余后端测试，Compose 检查尚未执行。此处不是最终 CI 结论，不以本批局部绿测替代。

回退本批生产修复时须同时撤销父 Runner 的快照保存/清除调用与 Store 方法，保留原 B2 子项账本和预算契约；不删除运行数据。不承诺撤销后仍可恢复本批新增的在途批量快照，应先停止新增批量派发并完成或明确终止相关在途执行。

## 2026-10-20 中断恢复与清单收口

- 中断后检查：仍为上述五文件，无暂存或提交；没有本批全量验证容器或完成回执，不能把准备脚本算作已执行全量。旧预览保持运行，未重跑任何旧任务。
- 只读复核远端 `main` 仍为 `ba2e5022461c2b414e4aa0fd973cace618d58472`。#421 Quality run `39143015569` 最终为 success，Backend quality、Frontend quality、Windows Project Host 均 success；这是上批远端证据，不替代本批测试。
- 清单语义核对作为独立两文件收口小批，仅修改 `docs/audits/provider-coverage.json` 与本卡；此前生产修复与测试不再扩展。当前累计六文件来自两个小批，不引入新功能。
- Python 3.12.14 对基线和当前 AST 比较：顶层仅 `_run_workflow_response` 改变；导入、常量、其他函数均未改变。947 个出口候选的 ID 集合相同；81 个 entry、分类、理由、归属和证据路径不变。唯一模块漂移为 `server/main.py`。
- 人工逐项审查差异：派发前的快照仅保存队列、调度、脱敏变量及已有运行上下文；派发后清除快照。新增 Store 方法在锁内检查 running/lease 后原子落盘。未增加网络出口、模型重试或新身份来源；子执行仍由 `_subworkflow_provider_parent` 校验持久父身份，预算仍经持久父子绑定和原账本消费；恢复绑定仍使用原 `resume_from`。敏感变量与不可恢复 tool policy 的快照保护未放宽。
- 更新前保留失败：原清单校验仅报告 `source module drift`。只更新已核对的 `main.py` AST SHA-256：`a85923e9e6bec67b5929dbeea0ad8b415ed354adb606574c47822de96e36a529` → `e689caf94ffaeb61b3b23cdfa1faf6a0f6b60a4d697cf0482e4b423322be9e18`；没有生成或批量替换 sites。
- 更新前四文件定向回归：`test_provider_coverage.py`、`test_workflow_r8b_provider_origin.py`、`test_workflow_r8b_call_recovery.py`、`test_workflow_r8b_entry_evidence.py`，**79 passed / 4 warnings / 16 subtests passed，26.34s**。从 Git 文件白名单归档 5441 个文件，容器逐项 SHA-256 核对无缺失或不一致；全程无网、临时 Store。警告为既有生命周期弃用。
- 证据目录：`C:\tmp\modelmirror-r8b-b3-gates-20261020`。此时全量、前端构建与最终预览影响检查仍未完成，后续结果另行追加。

### 清单复核与预览影响

- 清单更新后，在无网容器中解包同一 Git 白名单快照并建立仅用于文件枚举的临时 Git 元数据，运行原始 `python -B scripts/check_provider_coverage.py`：**通过，81 entries / 947 registered candidates**。没有覆盖扫描器或校验器，未创建提交；该结果只证明源码覆盖，不证明真实 Provider 可用性。
- 预览 `modelmirror-r8b-preview-20261018` 只读挂载旧 `modelmirror-r8b-gates-20261018/source.tar`；没有本批生产修复。前端另有 `client-dist.tar` 构建产物，因此不能仅由后端源码包推断其帮助页内容版本。未改容器、卷、挂载或运行记录。
- 只读访问 `http://127.0.0.1:15739/help/recover-workflow-run` 返回 HTTP 200、HTML 壳 427 字节；首次沙箱套接字权限拒绝，授权的只读检查随后成功。此项是可达性，不是渲染或操作验收。
- 源码核对：`WorkflowRun.tsx` 的“继续跟踪”读取原任务 `/stream`，不是批量任务重新派发；人工 `/resume` 处理审批/文本等待；定时协调器要求原 timer/node_retry 的等待身份。B3 没有修改这些入口或新建自动恢复协调器。
- 帮助文章 `recover-workflow-run.md` 的真实范例仍明确为无模型、无业务写入的定时等待与取消；这些旧实操证据不得升级为 B3 顺序批量跨进程恢复或真实 Provider 验收。当前四个父 Runner 跨进程测试使用内部认领与真实持久 continuation，尚不证明 UI 自动恢复。
- 影响结论：无前端交互、SSE、公共请求结构或 Planner 开放变化；内部恢复行为有变化，不能仅因没有前端 Diff 就宣布 Help Center Impact 为 None。后续 PR 材料须明确“批量窗口内部恢复”和“普通用户跟踪等待”之间的边界；同基线新预览操作及帮助重放仍未执行。遵守本次禁止重跑旧任务的边界，不用旧截图关闭该门禁。
- 初次前端类型检查失败于缺少 `experiments/ai-rpg-engine/card-replica` 的 React 类型依赖。核对 `.github/workflows/quality.yml` 已有对应独立 `npm ci`；按该目录现有锁文件离线补齐，无源码、锁文件或类型检查降级，原命令正在复跑。原失败日志保留。
- AST 语法检查四个变更 Python 文件通过；`git diff --check` 通过，暂存为空。增量及新测试/任务卡的常见凭据模式扫描无匹配，不能据此宣称完成全部安全审计。

### 前端最终结果

- 补齐独立依赖后，原 `npm.cmd run typecheck` **通过**，退出 0。
- `npm.cmd run test:run`：**164 files / 1302 tests passed，403.04s**，后续 **17 项 Node 测试通过**；整体退出 0。未跳过失败测试或调整阈值。
- `npm.cmd run build` **通过**，Vite 15.47s；保留大 chunk 警告，无构建配置变更。
- `npm.cmd run verify:help-images` **通过**：33 篇已注册文章，路径、真实 PNG、尺寸、体积、替代文本、公开残留及截图基线检查均通过。该静态检查不替代新预览操作或视觉验收。
- 后端源码快照完成后再次核对工作树的 server/client 与 Provider 清单共 2111 个文件，SHA-256 均与测试快照一致。期间仅追加本卡；依赖安装和构建产物保持忽略，不进入交付。
- 中断导致外层后端工具会话丢失，但本批测试容器仍在运行；通过 `docker logs --follow` 与 `docker wait` 重新收取原运行，不重新派发全量。旧前缀日志与后续 recovered 日志分别保留。

### 本批后端全量与最终边界

- 在固定源码快照中按锁文件离线安装 RPG/Worker 依赖，Worker `build`、`test:all` 通过；无外网和真实 Provider 派发。
- 实际全量参数：`python -B -m pytest server/tests/ -q -ra --tb=short -p no:cacheprovider --basetemp /tmp/pytest --junitxml=/evidence/backend-full-01.xml`。**10818 passed / 36 skipped / 8 warnings / 50 subtests passed，2025.77s**。`docker wait` 退出回执与容器退出码均为 **0**，完成于 `2026-10-20T11:47:47.861Z`。
- 独立解析 JUnit：0 failure、0 error；10904 条计数包含 10818 个通过项、36 个跳过项与 50 个 subtest。B3 两个新测试文件全部 12 项在此次全量中执行。
- 36 个跳过项：16 个 Windows 原生句柄/Project Host；5 个固定语言服务执行器；1 个专用 mcp-files 文件接口；11 个 Office parser；1 个隔离 renderer；2 个 docx/matplotlib 缺失依赖。未为本批添加跳过条件，不能把它们记为通过；#421 的 Windows CI 只能证明上批基线，不能替代本批平台验证。
- 8 条警告包括 FastAPI 生命周期弃用、PyPDF2 弃用、Pydantic 前向引用、既有 Dify OpenAPI operation ID 重复及多线程 fork 提醒，均保留，未降低检查或警告阈值。
- 证据：`backend-full-01.xml`、`backend-full-01-recovered.log`、`backend-full-01-recovered-exit.json`；首段 `backend-full-01.log` 因外层工具中断停在早期进度，不独立用作完成证据。本次只接回原容器，没有启动第二次全量。
- 四个变更 Python 文件额外使用内存 `compile(..., 'exec')` 通过，不生成源码目录缓存。
- 当前可关闭：Provider 清单语义核对、原始审计 CLI、本批 Linux 全量、前端类型/全量测试/构建、帮助截图静态检查、预览影响评估。不能关闭：同基线新预览操作/帮助重放、真实 Provider 恢复与计费证据，以及上述专用平台跳过项。B3 内部跨进程恢复不等于普通用户自动恢复入口已开放。
- 未重跑旧工作流、未使用旧截图冒充新验收、未开放 Planner 节点、未提交/推送/创建 PR。后续提交前仍须单独决定预览验收范围，不自动重启现有预览或沿用历史调用授权。
- 回退本批实现时，Provider 清单对应模块摘要须随实际源码重新核对，不保留新摘要匹配旧实现；不批量刷新其他出口分类。

## 2026-10-20 专用平台补验与新预览准备

本批只准备新独立预览、补验平台跳过项并核对帮助渲染，不重跑旧任务、不调用模型、不开放 Planner 节点、不提交 PR。以下补验不改写先前全量的 36 skipped 原始回执。

### 平台结果

- Windows 原生补验使用宿主 Python 3.13.9，覆盖权限、临时文件、Project Host 及原生提交/撤销：**102 passed / 1 skipped，107.08s**。按 JUnit 的 classname/name 逐项对应，原 Linux 全量中 16 个 Windows 跳过项全部通过。补验自身另有目录 symlink 不可用的跳过；该同名用例在 Linux 全量通过，但不据此声称已验证 Windows symlink 分支。
- 固定语言服务镜像 `modelmirror-coding-worker-slot-a:shared-pr412`，无网络、当前源码快照：**4 passed / 1 failed，20.45s**。失败为 `test_typescript_server_definition_and_diagnostics_rebuild_after_restart`，首次符号列表没有 `double`。单独保留原断言、增加局部变量输出复现：**1 failed，14.49s**；此次定义列表为空，符号列表反而含 `double`。尚未确定底层因果链，不能靠重复运行偶然通过关闭。
- 语言服务版本为 TypeScript 5.9.3、typescript-language-server 5.3.0、pyright 1.1.411，与当前 Dockerfile 固定版本一致。`code_intelligence.py`、`executor.py` 和该测试文件均与 HEAD 相同，且此执行链不依赖本批修改的父 Runner/Execution Store；目前归为未修改链路中暴露的平台红项，不归因 B3，也不宣称其根因已解决。初始化完成信号与首个语义查询的时序仅为后续待核对方向。
- Office parser/renderer 使用 `modelmirror-mcp-files:wave29-v1` 的既有依赖及当前源码，无网运行原 unittest：**18 run / 17 passed / 1 skipped，3.270s**。覆盖原全量的 10 个 Office 依赖跳过项和 1 个 renderer 跳过项；另一个真实 socket/Landlock 链用例仍跳过。未通过设置开关冒充专用链路。
- 宿主已有 matplotlib/PIL 的确定性图表用例：**1 passed，34.34s**，生成合成图表、核对重复输出字节一致并拒绝额外 URL 字段；不安装依赖或调用网络。
- 原 36 个跳过项的当前对应结果：**32 个补验通过、1 个失败、3 个未验证**。未验证为 Office API→独立 socket/Landlock bridge、Office parser socket/Landlock worker 链，以及缺少 markitdown 的 DOCX 注册产物用例。专用隔离拓扑必须真实搭建，不能以同进程 parser 通过替代。
- 回执目录沿用 `C:\tmp\modelmirror-r8b-b3-gates-20261020`：`windows-platform-01.xml`、`lsp-platform-01.xml`、`lsp-diagnose-02.xml`、`artifact-platform-01.xml`。Office unittest 结果来自本次工具运行输出，不冒称已有 JUnit 文件。

### 同基线独立预览

- 新目录 `C:\tmp\modelmirror-r8b-b3-preview-20261020`；源码为 Git 白名单 5441 文件快照，启动逐项校验 SHA-256；前端使用本批已通过构建的 dist。后端与前端源文件及 Provider 清单与全量测试快照逐项核对，任务卡后续追加不影响运行源码。
- 新后端 `modelmirror-r8b-b3-preview-20261020` 仅接内部网络 `10.253.239.0/28`，新空运行卷，不复制旧连接、密钥或任务；定时部署/Goal/Automation 协调器默认关闭。静态前端 `modelmirror-r8b-b3-preview-20261020-client` 通过独立接入网络 `10.253.239.16/28` 暴露本机 `15749`，仅前端连接两个网络，后端无外网出口。
- Docker 默认地址池耗尽及内部网络未建立宿主端口映射两项准备失败均保留。先核对既有 Docker/宿主路由，再使用未冲突显式网段；没有借用或修改共享网络。后端初次启动的 Chroma 遥测被无外网环境阻断，随后为此空预览关闭遥测并重启；没有重跑用户任务。
- `http://127.0.0.1:15749/api/health`、`/help/recover-workflow-run`、`/api/workflow/node-registry`、`/api/meta-agent/capabilities` 均 HTTP 200。实际 Capability 为 V10、IR V3、**22 类**，不包含新增子流程/迭代 Planner 能力。
- 已在真实预览器读取帮助全文并检查首屏、步骤和截图区域渲染。文章明确区分继续跟踪与重新运行，并保留“仅实测无外部调用的定时等待与取消”等边界；旧截图只证明旧实操，不证明本批新操作。此项关闭页面可达与有限渲染检查，不关闭新任务交互、全视口视觉验收或 B3 自动恢复门禁。
- 旧 `15739` 预览及记录未动。新预览没有发布、激活、运行、取消任何工作流，也没有配对、保存 Provider 连接或真实模型调用。

### 剩余门禁与集中人工配合

1. TypeScript 红项：需独立小批继续取证、定位后决定最小修复，不与 B3 父恢复逻辑混改；未授权扩大 Coding 实现范围。
2. 三个专用平台未验证项：准备正确隔离环境后补验；无需用户提供凭据或模型额度，不用跳过规则关闭。
3. 新预览操作：后续若需要发布/激活合成固定子流程、启动新的父流程或等待/取消任务、重启本批后端，须集中明确数量和对象后授权；不能沿用旧任务或旧授权。新预览只读浏览无需 Provider 连接。
4. B3 恢复证据目前来自内部认领及真实父 Runner 的跨进程自动化故障注入。用户界面没有新开放的“批量自动恢复”入口；应单独确认采用该证据边界，不要求用户在界面执行不存在的能力。
5. 真实 Provider 恢复/计费本次明确禁止，继续记为未验证，不作为本次离线成功结论。提交授权、最新上游与最终暂存审查仍留在后续，本次不提交。

## 2026-10-20 授权后的平台与预览收口

用户明确允许上述独立平台定位、新预览合成操作及证据边界。以下为追加证据，不覆盖先前失败、跳过或准备阶段结果；禁止真实模型、业务写入、旧任务重跑、Planner 开放和提交仍有效。

### 平台补验结论

- TypeScript 进一步取证发现真实 tsserver 子进程 `SIGABRT`，不能将空结果归因初始化延迟。固定沙箱 `RLIMIT_NPROC=256`，当时 48 个容器内 UID 65532 共 233 个线程。临时测试只改独立非特权 UID，保持源码、镜像、资源限制、Landlock、无网与原断言，原五项测试连续三个预定进程均通过：7.17s、8.47s、7.35s。没有生产代码、测试断言、重试或睡眠补丁。
- 独立定位详见 `CODING_LSP_INITIALIZATION_CLOSEOUT.md`。未捕获原生崩溃栈；共享部署同 UID 争用及客户端对子服务退出可能显示空结果的风险保留，不借局部环境隔离宣称生产部署问题全部消除。
- Office parser/renderer 真实私有 socket/Landlock 链：18 项全部通过，unittest 4.408s，JSON 包含加载的总时长 5.212s；API 无 docx/pptx 依赖的双容器 bridge：1 passed，5.65s；DOCX 确定性注册产物：1 passed，4.60s。无网络或生产依赖变更，保留环境准备失败回执。
- 对应原全量的 36 个跳过项现已全部取得补验通过证据；原全量仍为 10818 passed / 36 skipped，不改写原回执。Windows 补验自身额外的目录 symlink 分支仍未验证。
- 回执：`lsp-uid-control-05.xml`、`lsp-isolated-06.xml`、`lsp-isolated-07.xml`、`office-chain-05.json`、`docx-artifact-04.xml`、`office-bridge-01.xml`，均位于既有本批证据目录。专用文件 sidecar 验证后停止，只保留本批合成卷，不操作共享栈。

### 新预览授权与操作记录

- 新预览保持本机 15749、私有新卷与后端无外网。仅为本批合成验收启用既有 Runtime 子流程与定时恢复开关；Goal/Automation 协调器关闭，不新增任何 Planner Adapter，能力仍为 22 类。
- 以 Store 建立三个新合成图，不冒称通过画布创建。子图 `wf_8f28a5e1d08e4ad0933a6adbf92bebc1` 通过已授权 API 发布并激活 v1，checksum `96caf5789739c0c034629e184e9b41f8e67bc8a6f658ba82b3820704ba8d3bf9`。该 API 准备与后续真实预览器操作分别记录。
- 新父图 `wf_6bb6dfaffaf54d6f9c96eb03105a2fb7` 在真实预览器点击运行一次。父任务 `9c950ed1df6c4895849498eea04593dc` 完成；固定子执行 `wfx_c24f1d851c094217b7e1b9303f627cfd`、任务 `wft_ce9d731a021c266b39c0f720b7aa55c6` 完成，返回固定合成文本。没有模型或工具调用；不将此无模型链路当作真实 Provider 预算验证。
- 新等待图 `wf_8423bf85c50a4251ac5a1fdcd1460494` 的首次任务 `c308d78e35194ee1b1d41bfa7f24b4d2` 在 12:47:13 UTC 已完成，后端实际启动时间为 12:48:38 UTC。上下文中断期间错过了等待内重启窗口，故该例只证明定时等待与继续跟踪，不计作跨重启验收。没有隐瞒失败或直接重复派发；已单独请求补充一次新的等待中重启授权。
- 新取消任务 `b1d21358b13a44edbf3528efb48938cb` 在真实预览器确认等待后点击“取消原运行”，随后只重启本批后端。取消发生于到期前；12:53:30 UTC 只读核对已超过原到期时间至少 10 秒，仍为 cancelled，只有 input 完成一次，无 output 完成事件、无 workflow_end、无结果。真实页面结果区显示“已取消”且无最终交付。
- 安全回执 `preview-acceptance-01.json` 按精确 task ID 核对本批四个执行（父、子、首次等待、取消），没有额外执行；父子各节点仅完成一次。所有节点均在本地输入/输出/子调用/等待白名单，写入账本为空；没有通过答案文本代替执行证据。
- 预览截图已尝试捕获，但工具写本地文件遭权限拒绝，不能声称已有归档 PNG。AX 状态与私有 Store 证据保留；需要交付截图时应由用户补充或使用被允许的保存方式，不重跑任务换取截图。

### 同基线复核

- 本轮补验与预览过程中没有改动运行源码。再次逐项核对 server/client 与 Provider 清单共 2111 个文件，当前工作树、全量测试快照、新预览快照无差异。
- 只读 `git ls-remote origin refs/heads/main` 仍为 `ba2e5022461c2b414e4aa0fd973cace618d58472`。当前累计七个变更文件，新增的 Coding 独立收口卡是文档，不将其算作 B3 生产修复。
- 父 Runner 的批量窗口跨进程恢复仍以内部认领、真实 continuation 和故障注入测试为证据；新预览只验证固定子流程调用、普通定时等待及取消，不宣称 UI 已开放批量自动恢复。
- 最终 `git diff --check` 通过，`git diff --cached --name-only` 为空；新预览能力接口再次返回 V10 / IR V3 / 22 类，没有子流程或迭代 Planner 节点。

### 当前尚未关闭的边界

1. 新预览的等待中重启验收：首次时序不成立；用户随后单独授权的一次补充验收已通过，见下节。保留首次记录，不将原任务再运行作为“恢复”。
2. 本批新结果截图的文件归档：已核对真实渲染，工具保存受限。用户可对保留结果页截图，不需要再执行任务。截图不能替代 Store 的重启时间和执行计数证据。
3. Windows 目录 symlink 分支、共享部署同 UID 配额争用、真实 Provider 恢复与计费均保留未验证/未修复边界；本批没有扩大这些环境或外发权限。
4. 本批没有提交授权，尚无本批远端 CI；#421 成功只属于上批。提交前仍须复核最新上游、预期文件范围和用户对上述边界的验收，不自动创建 PR。

### 补充一次等待中重启验收

- 授权范围：15749 独立预览新增一次 90 秒合成等待，确认持久 waiting 后立即重启该后端；禁止旧任务重跑、模型、业务写入和 PR。只修改本卡与临时验收脚本，不改生产源码、配置面板或工作流定义。
- 真实预览器新开任务页，输入固定合成文本后仅点击一次“运行工作流”。新任务 `2a017e5fc05c41ff98aee8b72cb3c99f`，原 run `e0d4e90d-baf6-4e65-99ff-629ab4123948`。
- 13:12:15.567 UTC 持久 Store 断言通过：status=waiting、wait_kind=timer、input 仅完成一次、output 未执行，到期时间 13:13:19.029 UTC。读取与重启在同一受控命令中顺序执行；剩余时间不足 30 秒会拒绝重启。
- 独立后端实际启动时间 13:12:16.410 UTC，早于到期时间；13:12:34.172 UTC 重启后再次读取仍为同一 task/run 的 waiting，不依赖旧浏览器状态判断恢复。
- 13:13:19.562 UTC 完成。task ID 不变，新 run `4c3d4fd1-5846-419f-8e11-7b27abea4431` 的 `previous_run_ids` 精确包含原 run；input/wait/output 各一次完成事件，workflow_end 一次，最终输出与原合成输入一致。本批总执行数仅从 4 增为 5，没有额外任务或重复派发。
- 页面到期后点击“继续跟踪”显示完成和相同最终文本；没有使用“重新运行”。该入口用于跟踪，不宣称持续等待页面会自动刷新到完成。
- 原始安全回执保存于本批证据目录：`timer-restart-02-before.json`、`timer-restart-02-container.json`、`timer-restart-02-resumed-waiting.json`、`timer-restart-02-after.json`。首次失误回执仍保留，未覆盖。
- 结论：关闭本批新预览的普通定时等待跨后端重启门禁。不将此证据升级为 B3 批量自动恢复 UI、真实 Provider 恢复/计费、多实例或业务副作用验收；其他已列未验证边界与截图归档仍保留。

## 独立交付整理与人工暂停点

- 用户要求先完成可独立处理事项，到人工门禁前暂停；不提交、不重跑任务、不调用模型。
- 本小批四个文件：本卡、`docs/help-center/evidence/r8b-b3-preview-20261020.md`、正式恢复帮助文章及其现有内容测试。生产实现、既有回执、Provider 清单和部署不变。
- 重新读取最终生产 Diff、两个新增测试及仓库帮助规则；远端 main 仍为 `ba2e5022461c2b414e4aa0fd973cace618d58472`。源码修复仅涉及受限批量快照及原子 Store 方法，清单分类与出口未新增。
- 新证据索引整理通过项、失误、未验证项、回退和 PR 草案，列出三个截图的准确运行标识与禁止动作。尚未收到本批截图，不制造占位图片或虚假 hash。
- 帮助文章只补充恢复边界；保留旧截图来源与验证元数据，待新图与人工检查后再整体核对。追加文案后的新教程重放未执行，不能沿用先前其他批次的免重跑许可；人工阶段应明确采用已有相同步骤证据或另行授权。
- 本轮帮助内容变更后，旧 2111 文件“全一致”结论仅适用于变更前状态。后端及生产前端逻辑未变；帮助文章和测试的差异单列，执行对应定向测试、构建与静态图片检查，不为文案重跑旧工作流。
- 实际结果：帮助定向测试 **18 passed，3.88s**；33 篇帮助图片静态检查通过；生产构建通过，Vite 17.64s，保留 chunk 警告，均退出 0。没有替换运行预览的产物，因此新文案渲染不能记为已验收。
- 交付范围审查：累计 10 个预期文件，来自此前实现、清单核对、独立平台记录及本次四文件帮助批，不作一次大范围生产修改。暂存为空；与旧验证快照相比，2111 个检查文件仅帮助文章与测试两处变化。有限凭据模式扫描无匹配；原始回执 hash 记入新证据索引。
- 当前暂停于人工门禁：用户提供父子完成 `e0f474af`、补充恢复完成 `4c3d4fd1`、取消 `bdfcc24e` 三类截图；不要刷新或重跑。收到后再处理图片归档、正式帮助元数据和最终渲染，不提前宣布整批完成或提交 PR。

## 2026-10-20 图片补齐与最终帮助核对

- 上节是图片收到前的历史暂停点。本次四张有效原图已逐字节归档：父调用两张分段图、补充等待中重启图、等待中取消图。第一次等待 `a5f757b8` 的截图不作为跨重启证据；正确恢复图为 `4c3d4fd1`。
- 经用户确认，恢复/取消图仅等比缩小至 1000px 并编码为 PNG，不裁剪、不量化、不重绘。公开图为 1000x895 / 156489 字节与 1000x590 / 97156 字节，原件和旧公开图全部保留。准确路径、hash 和授权记录见 `docs/help-center/evidence/r8b-b3-preview-20261020.md`。
- 帮助元数据更新到 `ba2e5022`、2026-10-20；18 项帮助测试、33 篇帮助图片检查和生产构建通过，保留 chunk 警告。当前构建在独立 15759 只读静态页核对两图、图注和恢复边界；未替换 15749 实操预览，未重启其后端，未重跑任何任务。
- 用户认可已有相同步骤实操用于本次帮助文案增补，不要求新教程重放；该许可不能代替最终人工验收或扩展未验证范围。取消截图正文缩放后小字较小，原图与文字图注保留。
- 当前暂停点：用户最终检查帮助图文与已列证据边界，随后另行决定是否授权提交。Windows symlink、共享部署同 UID 配额风险、真实 Provider 恢复/计费仍保留，不用人工确认替代技术证据。没有本批提交或远端 CI，不提前开放 Planner 节点。

## 提交授权与最新上游核对

- 用户随后明确确认图文验收通过，授权本批独立 PR，禁止重跑任务、调用模型、提前开放 Planner 节点或自动合并；上述人工暂停点关闭，未验证技术边界不变。
- 发布前获取最新 main `3bfbbddeb845ff4d48bacf9f237c6060b661d095`。新增 #422 为模型目录、快照、目录测试及其他帮助文案，共 8 个路径，与本批 21 个预期路径无交集，不涉及 Runner、Store 或 Provider 出口。
- 保留已验收的 `ba2e5022` 本地基线，不冒称最新组合已完成本地全量；远端 PR CI 作为独立组合证据，结果在提交后如实报告。本次只更新本卡和证据索引中的验收状态，不改生产实现或重放任务。
