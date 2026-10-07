# 受控写入生成链路系统核对

日期：2026-09-12。范围：V3 第 7/10 轮的候选生成与唯一修复链路，不进入下一轮。

版本说明：本文保留 R10/R11 的修复历史，不代表 R12 后最新结论。R12 后的独立核对、同请求合成配对实验及待确认收口方案见 [受控写入轮次审计收口](META_PLANNER_CONTROLLED_WRITES_CLOSEOUT_AUDIT.md)。

## 当前结论（R11 后）

R11 仍未通过真实生成。首次任务规划与能力编排请求已离线逐字节重建，匹配该次安全回执；不能归因为新说明未加载。已接收的修复计划包含“Update 返回新 revision”“Agent 提取记录身份供后续写入”等错误前提，首次/修复 Schema 也未一致表达实际 Adapter 边界。

本次修复只收口私有生成契约和被动诊断：任务规划/修复共用 expert-only Schema；Graph/Patch 按真实 Adapter 配置和已授权 kind/ref 生成 Schema；表摘要明确完整记录、计数返回及可信来源。公共 Graph IR/TaskPlan、NodeContract 许可、Runner、写入授权和三次调用上限不变。

这些改动消除已证实的契约矛盾，但不构成真实生成成功证据。模型仍可能违反 Schema 或提出不合理任务；自然语言职责不是服务端自动推断记录身份的权限。后文先保留 R10 后核对历史，再列本批证据，不能把历史测试算作本次重新执行。

## R10 后结论（历史）

目前不能把连续失败归结为一个数据库执行缺陷，也不能据离线测试通过宣称真实生成已经合格。

- 历史失败涉及输入契约投影、V2/V3 兼容、独立错误遮蔽、传输/观测、完成状态和模型违约等不同层。应按阶段核对，不再采用“修正最后一条错误后马上付费重试”的方式。
- 本次确认了一个仍存在的跨节点投影缺陷：模型侧出口列表与实际配置相关的控制语义不一致；同时缺少可区分缺失、重复和意外出口的结构化诊断。
- 同一个完整的合成 CRUD 图，在修改前即能按 `stop` 和 `error_output` 两种策略编译往返；缺少两条 error 边的图，在修改前也能通过正确的显式 Patch 修好。因此没有证据支持修改调度器、自动接边、降低类型检查或放开写权限。
- R10 原始被拒 Intent/Patch 未持久化，不能确定当时是哪两条具体连边错误，也不能断言本次发现的投影矛盾是 R10 的唯一诱因。

## 基线与授权

- 独立工作树：`C:\tmp\modelmirror-meta-planner-controlled-writes-10`。
- 分支：`codex/meta-planner-controlled-writes-10`；HEAD：`436d24535c6f0ad80b3a4f18830fb834f90a7b95`。
- 开工核对 96 个源码、测试、预览启动器、守卫及历史回执 hash，与 R10 收尾一致；不把工作树中的整个轮次 Diff 当成本次新增修改。
- 远端跟踪分支已领先 26 个提交，本批未 fetch/rebase 或集成；主工作区、共享栈和现有预览进程不参与修复。
- 本批不读取凭据、不调用外部模型、不执行业务表写入、不批准或发布，不提交、推送或创建 PR。R10 的三次调用授权已经耗尽。

## 分层证据

历史逐次记录见 [任务卡](../tasks/META_PLANNER_CONTROLLED_WRITES_10.md)。下表区分历史证据与本次重新核对的源码/测试。

| 层 | 已证实事实 | 当前处理与剩余边界 |
| --- | --- | --- |
| UI 授权范围 | 早期中间件勾选用 kind 而非真实 id，守卫曾在外发前拒绝 | 既有按分组取 ID 修复保留；本批不操作 UI，不推断当前页面的勾选状态 |
| 任务规划 | R3 的唯一修复被任务计划消耗，能力编译没有第二次修复额度 | 当前三次总预算正确；不能把“没有再修复”当成 Runtime 故障，不增加调用 |
| 类型兼容 | 合法 V3 integer 曾在 V2 兼容枚举/粗类型判断中被误拒绝 | 既有统一类型与兼容判断保留，整数和不安全收窄回归重新运行 |
| 配置与资源 | 写输入与配置动态关联；配置失败曾跳过同节点独立资源检查 | 既有 Adapter 输入契约与独立资源检查保留；缺端口、越权、失效字段仍拒绝 |
| 控制出口投影 | 原投影仅按 control-only 数据端口是否存在，输出 success 或 success/error | **本次修复**：从既有语义解析器与 NodeContract 配置范围派生；condition、multi_route、terminate、读节点失败策略均逐一核对 |
| 控制校验 | error_output 必须每个出口恰好一条；普通 success 可 fanout | 原判定正确；提取共用事实计算但保持接受/拒绝行为，366 组出口多重集对照防止误杀或放宽 |
| 唯一 Patch | 已有完整 base_graph_intent；精确重复控制边应拒绝 | 不声称模型从未收到原控制边；补充计算后的缺失/重复/意外出口及带索引的边列表，不自动生成修复操作 |
| 诊断归因 | R10 的两项错误修复前后指纹相同，Patch 两项操作已应用并重新编译 | **本次修复**：阶段摘要保存受限出口计数和控制图 checksum，区分“没有改变控制图”与“改了但仍非法”；不能从 Patch 归一化 checksum 相等推断图没变 |
| 传输与观测 | R6 首请求结果不确定；R7 观测写盘中断响应；R8 前端提前返回失败 | 既有守卫/代理修复保留。R6 远端最终状态和 R7 具体占用进程仍未知；不追加本地超时补丁 |
| 完成状态 | R9 finish_reason=length，旧链路曾被后续预算错误掩盖；R10 三次均 stop | 保留 Planner 专用完成校验；不提高 4096/8192 上限，不把 HTTP 200 当成有效候选 |
| 失败占位与审批 | fallback 故意清空首个 Agent 的模型以阻止批准 | 正确保留；不能给失败占位图补模型制造验收成功，生成和审批均不得执行业务写入 |
| 写入运行与评测 | 生成阶段尚未执行真实写节点；已有事务、revision、账本及隔离评测实现 | 本次补跑离线合成运行与效果断言，不把它们称为真实 Provider 或真实业务写入验收 |

## R10 后一致性修复（历史）

仅三个生产文件：

1. `server/meta_agent/control_flow.py`：保留既有 `semantic_outcomes`、原生 Handle 映射与符号场景算法；提取共用出口基数事实及原有错误判定。模型投影按配置 Schema 中的 `failure_action` 枚举、`routes` 长度范围派生变体，不再另建乐观出口列表。
2. `server/meta_agent/meta_planner_v2.py`：生成、完整修复、Patch 修复共用配置相关的 `control_contract`。Patch 修复增加实际控制边及计算后的出口诊断；不扩大可生成节点、操作或授权，不提供自动选定目标的补边功能。
3. `server/meta_agent/generation_diagnostics.py`：只保存节点安全 ref/索引、出口名、计数、边索引与控制图 hash，不保存目标记录、字段值、Prompt 或原始模型输出。非法超大 routes 配置不会扩充持久化出口名；超出已支持出口集合只计数。

`control_graph_checksum` 仅表达节点 kind/语义出口、控制边和最终来源，不代替完整 Graph IR 或候选 checksum，也不用于授权与 Apply。节点/边展示顺序不影响该 hash；诊断边索引从 0 开始，关联当前阶段原始边数组。

机器 Schema、原生变量/Handle 拒绝、写入来源收据、逐表授权、当前活动 Schema 要求、单节点事务、严格重复控制边规则及三次模型调用上限均不变。编排模型收到的契约元数据发生了修正；原业务测试目标和任务规划 Prompt 未修改。不得把此项描述为“完全没有改变模型输入”。

## R10 后证伪与验证（历史）

- 新增 `test_meta_planner_control_contract_alignment.py`。修改前 10 failed / 2 passed：发现三入口投影与结构化诊断缺口，同时证明两种失败策略的正确图原本就能编译。
- 单次显式修复对照：正确 error 边可通过真实生成服务、解析、编译、发布预检并产生 pending/r1；空 Patch、错误 outcome 或重复边继续失败。所有 completion 均为本地 stub，业务记录读写入口设为失败哨兵。
- 反向校验：七类配置、366 组出口基数/最终来源组合与修改前规则等价；检查普通 fanout、最终来源无出边、动态 Multi Route、缺失/重复/意外出口和语义图 checksum。
- 诊断反例：非法 1000 条 routes 曾让新摘要膨胀到 39,292 字符，本批收口为白名单出口和省略计数；不是放宽非法配置的验收。私有 ref、业务值与配置正文不进入安全摘要。
- 30 个 Planner/Graph IR/NodeContract/Headless/Authoring/Publish/Evaluator/Evolution/App 相邻文件：**704 passed / 138.44 秒**。
- 10 个 Backend/受控写入/恢复/隔离评测/Managed/Workflow 文件：**163 passed / 53.06 秒**，与上一组无文件重叠；定向总计 **867 passed**。保留既有 FastAPI 弃用警告。
- 测试使用既有 venv 和 `.tmp-cw10-preview/run_integer_checks.py`：关闭 dotenv、凭据、外网及 pytest 自动插件，所有 Store 重定向到独立临时目录。不导入预览启动器。
- 测试环境/命令错误单列：首次受限进程无输出后中止；写入组合首次误写一个不存在的测试文件名，未运行测试；更正为实际 `test_workflow_control_data_nodes.py` 后同组通过。未修改产品以掩盖上述工具错误。
- `npm.cmd run build` 通过，保留既有大 bundle warning；五个改动 Python 文件的纯语法编译通过，未导入应用或写字节码。
- 全量 `server/tests/` 尝试出现多个失败，在约 5% 进度主动中止，未生成完整 XML；核验没有残留的该测试进程。随后以 `--maxfail=1` 在同样隔离环境重新取得具体结果：**94 passed / 1 failed，43.20 秒**。失败是 `test_agent_upstream_port.py::test_started_worker_crash_after_model_request_is_never_restarted`：Worker 已断开，异常处理继续发送取消帧，`stdin.drain()` 抛出 `ConnectionResetError` 而非预期的 `EngineUnavailableError`。对应 `server/agent_upstream/port.py` 和测试与 HEAD 无 Diff，且该模块不依赖本次 Planner 修改；本批不修复它，也未在纯基线环境复跑全部早期失败，不能把它们统称为已证明的基线失败。
- 首失败 XML 保留于忽略目录 `.tmp-cw10-preview/systematic-backend-first-failure-20260912.xml`，统计 95 tests / 1 failure / 0 errors / 0 skips。它不是完整回归报告；全量门禁仍未通过。解析 XML 的一次工具命令误只读取首行，已更正为结构化读取完整 XML，仅输出统计，不影响测试结果。
- 收尾复核：96 个既有 hash 中，仅本批的两份既有生产文件、一份测试和任务卡改变；另增加原先干净的 `control_flow.py` 修改、新专项测试及本审计。预览启动器、守卫和 R1-R10 历史回执均未变化，原前后端 PID 保持不变。七文件常见凭据模式扫描 0 命中，`git diff --check` 通过，暂存为空，待提交路径未混入 SQLite、Runtime、日志或构建产物。未提交、推送或创建 PR。

## R10 后待办（由后续 R11 记录更新）

本批定向离线核对已完成，全量尚有独立阻塞与未分类项。需先处理或明确隔离这些门禁，再由用户重新确认真实调用范围。不得自动沿用已耗尽额度，不通过改写目标、调高 token、增加修复次数或手工修正 Proposal 宣称通过。

重新授权后的验证仍应固定同一零记录合成表、原中文目标、逐表三操作授权和每节点 1 行上限；核验加载源码、模型、请求上限及历史账本后，只发起一个生成实例。成功条件是新候选 `validation.valid=true`、真实编译结构具备要求的读写链、Headless 可无损加载，且审批/业务表状态未被隐式改变。真实执行写节点需要另外明确范围，生成成功不能替代效果验收。

若仍失败，新的出口摘要应足以区分缺失、重复、意外 outcome，以及 Patch 未应用/已应用但复编失败。必须先核对此证据，不再自动进入下一次调用。依旧不保存完整失败 Prompt 或隐藏推理。

## R11 后修复边界

- `generation_contract.py` 是私有模型输入投影，不是新公共协议或第四套 Validator。配置 Schema 直接来自 Adapter，嵌套 `$defs` 规范化重定位；Graph 根据授权 kind 分支，Patch 的已有 ref 使用其真实 kind 配置，新增 ref 仍经过现有原子 Patch 与授权校验。共用定义只去重、不截断。
- 新模型任务计划使用严格的 `GenerationTaskPlan`，首次与唯一修复完全一致；旧公共 TaskPlan 继续读取原数据。模型任务文字可以描述判断职责，不能改变 Query/Insert 的完整记录结果或给 Update/Delete 虚构 revision。
- JSON Schema 表达不了的跨节点引用、动态谓词精确集合、类型兼容、自定义配置规则和资源权限，仍由原有权威校验执行。本次没有开启 Provider 约束解码，没有以 Schema 通过代替完整编译，也没有扩大修复次数。
- 诊断 V2 从 Adapter 的既有动态输入计算函数取得表端口事实，区分配置无效、缺少、重复和多余输入。新增数据摘要包含节点/输入索引、来源数量、变量是否一致、声明类型及 hash；动态谓词 ref 和未知端口只保留 hash/位置，业务字段、值、Prompt 和原始回复不落盘。
- `data_graph_checksum` 用于对比声明连线及 Schema；展示顺序、标题、Prompt 和配置正文不进入该 hash。它不用于授权或 Apply。`declared_*` 不是权威资源 Schema，未解析/不唯一来源不伪造为匹配。新增端口与绑定明细共用 64 条上限，其余明确省略。
- R11 的 authorization 阶段内部也运行控制分析；已更正任务卡原先“未进入控制流检查”的错误标题。原失败图没有完整持久化，本次反例是独立构造，不宣称精确重放所有 R11 边。

## R11 后验证记录

| 检查 | 本次结果 | 证据边界 |
| --- | --- | --- |
| 私有生成契约反例 | 修改前 9 failed / 4 passed；最终新文件 24 passed | 覆盖任务首轮/修复一致、额外字段、按 kind 的配置、嵌套引用、端口、作用域和合法节点组合；不是 Provider 生成 |
| 绑定诊断反例 | 修改前新增四项均 failed；修改后进入通过的相邻回归 | 缺谓词端口、变量别名、未知来源端口、无效配置、200 条输入灌入；被动观测不改变校验判定，私有标记不落盘 |
| Planner 与相邻契约 | 31 文件，732 passed，103.05 秒 | 包括 Graph IR、NodeContract、Headless、Authoring、Publish、Evaluator、Evolution、App；本地 completion stub 验证三次上限 |
| 写入、隔离与视觉回归 | 14 文件，231 passed，61.43 秒 | 与上一组文件不重叠，合计 963 passed；数据均为隔离合成夹具，无活表或真实 Provider |
| 全量后端尝试 | 94 passed / 1 failed，30.68 秒，首失败停止 | `test_agent_upstream_port.py::test_started_worker_crash_after_model_request_is_never_restarted`；Worker 断连后发送取消帧，`stdin.drain()` 抛出 `ConnectionResetError`。源码/测试与 HEAD 无 Diff，本批未修；剩余全量未执行完，不能称全量通过 |
| 前端生产构建 | `npm.cmd run build` 通过 | 保留既有大 bundle warning；本批未改前端源码，未把构建当作真实 UI 验收 |
| 语法、Diff 与扫描 | 8 个 Python 源码/测试文件及一个忽略的测试入口编译通过；`git diff --check` 通过；11 个拟纳入本批文件的常见凭据模式 0 命中 | 纯语法编译未导入应用或生成字节码；敏感标记泄漏另有反例测试，不宣称静态模式扫描覆盖所有秘密 |

测试过程中的修正单列：新 Schema 首次检查发现空 `allOf`，以及扩展测试中的一个局部变量引用错误，均修复后重跑相同测试。14 文件回归初次为 228 passed / 3 failed；三个 PDF 失败的子进程日志重复出现 `TEST_FILES 14`。现有离线入口在顶层执行 pytest，被 Windows `spawn` 重入；新增忽略的 `run_generation_contract_checks.py` 复用原环境/网络护栏，只在主进程启动 pytest，随后同一 14 文件组 231 passed。没有修改 PDF 产品实现、超时/资源限制或失败断言，也没有改动原启动器、真实调用守卫或历史回执。

可复核命令均在独立工作树执行，Python 为已有 `C:\tmp\modelmirror-mcp-test-venv\Scripts\python.exe`：

```text
python .tmp-cw10-preview/run_integer_checks.py
python .tmp-cw10-preview/run_generation_contract_checks.py <上述 14 个相邻文件>
python .tmp-cw10-preview/run_generation_contract_checks.py server/tests/ --maxfail=1 --junitxml=.tmp-cw10-preview/generation-contract-backend-first-failure-20260912.xml
cd client
npm.cmd run build
```

14 个相邻文件为 `test_agent_table_controlled_writes`、`test_controlled_write_execution_integration`、`test_controlled_write_runtime`、`test_evaluation_controlled_write_runner`、`test_evaluation_write_evidence`、`test_evaluation_write_isolation`、`test_meta_agent_managed_gateway`、`test_workflow_data_table_nodes`、`test_workflow_control_data_nodes`、`test_evaluation_vision_fixtures`、`test_evaluation_vision_evidence`、`test_evaluation_vision_runtime`、`test_evaluation_vision_store`、`test_xpert_evaluation_resource_fixtures`，均位于 `server/tests/`。全量首失败 XML 为 95 tests / 1 failure / 0 errors / 0 skips，并非完整全量报告。

同一 21 类授权合成夹具中，共用定义去重前后：Graph Prompt 从 75,755 降为 71,317 字节，Patch Prompt 从 82,630 降为 71,267 字节；任务 Prompt 为 17,096 字节。这里比较本批新投影内部的重复定义处理，不是 R11 原请求的相同比较，也不是实际 Token 或 Provider 用量。未截断契约、改变目标或增加调用/输出预算。

本批共四个生产文件、四个测试文件及三份文档。100 个开工 hash 中仅九个允许文件变化，另新增私有投影模块与专项测试；预览启动器、调用守卫、历史回执、公共 Schema/许可和执行/存储实现不变。新增测试入口和 XML 保持忽略，不进入交付；暂存为空，未提交、推送或创建 PR。

## 回退与未完成

回退只撤销本批三个文件中的出口投影、事实提取和新增被动诊断，保留之前已独立验证的完成状态、整数兼容、资源授权和预览护栏；不迁移或回滚 Proposal、Xpert 和数据表。错误投影会重新出现，因此回退后应保持真实复测入口关闭。

最新上游集成（包括 `execution_store.py` 恢复语义交点）、真实合格生成、完整写操作人工验收及帮助中心重放继续单列。本批不以“所有历史根因都已确定”或“下次一定成功”作为结论。

R11 后新增部分的回退仅撤销私有 Schema 接线、任务摘要和诊断 V2 增量；保留既有控制流修复、权限、事务、恢复与诊断 V1 兼容，不修改历史 Proposal、回执和业务数据。真实调用额度未续用，常驻预览本轮未重启加载。
