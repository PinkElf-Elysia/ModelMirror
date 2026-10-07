# META-PLANNER-CONTROLLED-WRITES-10

状态：收口中；固定候选隔离效果验收通过，整体 PR 门禁未通过，未提交。

## 当前状态（2026-10-03）

以 [2026-10-02 收口证据与剩余门禁](../audits/META_PLANNER_CW10_CLOSEOUT_20261002.md) 为当前依据；下文较早日期的测试数字和“待验收”描述保留为历史，不覆盖本节。

- 最新集成工作树为 `codex/meta-planner-cw10-final-20261002`，HEAD `8c2a0120`；201 个交付路径已从旧收口树逐字节集成，与新增 14 个上游路径无直接交集。15509 已验收预览仍固定旧树 `codex/meta-planner-cw10-closeout-20261002@0de311ec`；10 月 3 日在确认端口空闲后原样恢复旧服务，没有替换源码或 Runtime，冻结回执复核一致。
- 固定副本 r2 的三场景各两次真实隔离评测通过，6 完成、0 失败，路径与写入效果均为 100%；4 次 OpenRouter / DeepSeek V4 Flash 0731 汇总，实际 usage 4,228 Token，无自动重试或不确定请求。本次只读复核冻结回执一致，不记为新代码上的再次真实调用。
- 两个库存不足实例各更新一行，其他实例无写入；六个私有 Backend、保护记录、零记录权威表及原 Store 均独立核对。已完成项重启后没有重复运行、调用或写入，候选仍为 pending/current r2。
- 已只读重放帮助入口、候选静态证据、评测候选、运行报告、三分支和合成初始化查看；完整人工修复/定向修复教程与持久化截图仍未验收。未配置资源读取及答案文本断言，不宣称这些指标通过。
- 最新主线集成后的重点后端 321 项、独立旧运行契约 7 项、前端类型检查与生产构建通过；前端全量 1,182 passed、3 failed，代理测试另行 11 passed。三个前端失败和九项帮助图片问题均在最新干净主线复现。后端最新全量在约 70% 时中断，恢复后进程已不存在且没有终态 XML/JSON，故不计为完成；旧树全量数字仅保留于审计历史，不替代本次结果。
- 最新主线已知六项后端红项经复跑：安装既有锁定 RPG 依赖后四项 Schema 测试通过；剩余两项音频目录版本断言在最新干净主线同样失败。本次未修改音频、RPG、依赖声明或 CI；本地依赖补齐不证明上游 CI 已修复。
- 本批不新增真实调用，不改候选或 Prompt，不批准、发布、提交或创建 PR；不提前进入第 8 轮。

## 历史进度

2026-09-19 本地系统收口更新：见 [全链路收口核对](../audits/META_PLANNER_CONTROLLED_WRITES_SYSTEM_CLOSEOUT.md)。已收紧路径证明、权威类型与失败产物来源，并清理单模型阶段冲突；最新相邻后端 1,206 项、前端 30 项和生产构建通过，三路径真实隔离 SQLite 集成通过，但 Agent 文本为替身。只完成离线验证，未追加真实调用。全量入口仍有未修改模块的 Windows 断连失败，PR 门禁未通过。下段全量数字是历史冻结源码结果，不作为当前修订的全量证据。

最新审计：见 [收口证据与门禁](../audits/META_PLANNER_CONTROLLED_WRITES_CLOSEOUT_AUDIT.md)。R15 原串行目标已通过一次真实候选生成，但不代表稳定性或实际写入验收。2026-09-17 已刷新至 `a7d99925` 的独立收口树；修正离线测试启动器并复现、最小修复隔离写入的预期安全终止误判。最终后端全量为 7,382 passed / 15 failed / 30 skipped，另有 7 项契约测试通过；与最新干净主线没有候选独有失败，不能描述为全绿。原提案已漂移至编辑后的 r3，真实写效果、非线性泛化、帮助中心与提交门禁仍未通过。

## 开工契约

- 基线：`origin/main@436d24535c6f0ad80b3a4f18830fb834f90a7b95`；PR #364 已合并。
- 工作树：独立 `codex/meta-planner-controlled-writes-10`；开工时无修改。
- 主工作区存在无关修改，未参与实施。AI Research 的基线检查失败单列，不在本轮修复。
- 目标：只开放三种 Agent Table V2 写入 Adapter，最终 Capability 19 -> 22，IR 保持 V3。
- 授权：表、操作、可写字段、最大影响行数显式授权，默认关闭，默认影响 1 行、硬上限 100。
- 记录身份：更新/删除仅使用同一运行中同表 Query/Insert 的可信收据及 revision。
- 业务值：允许 Agent 输出经 JSON Deserialize V2 与字段 Schema 双重校验后写入。
- 原子性：节点事务；允许顺序多写，不承诺跨节点回滚、补偿或自动重试。
- 评测：仅手工/合成初始化数据；隔离 Backend；禁止业务表写入与活表回退。

## 范围与风险

高风险：本地持久化、写入幂等、资源授权、评测隔离。复用现有 Store/Runtime，不新增依赖、数据库产品或破坏性迁移。

允许路径：`server/data_tables/`、`server/workflow_native/`、`server/meta_agent/`、`server/evaluations/`、相关 Runtime/发布集成、测试、现有管理端、文档与帮助中心。

禁止：共享栈、现有用户数据、未经逐项授权的凭据使用和外部模型调用、SQL/外部工具写入、公共 App 或 Evolution 权限扩张、RAG/Data X 改造、第 8 轮能力。下文记录的真实生成是一次性追加授权，不扩展为自动复测或执行写节点的权限。

全轮超过五个文件，因为同一契约必须覆盖授权、编译、执行、隔离和证据。每个内部微批保持不超过五个文件，跨层集成单独验证，不能用部分能力冒充闭环。

## 批次状态

| 批次 | 目标 | 状态 |
| --- | --- | --- |
| 0 | 基线、任务卡、独立工作树 | 通过 |
| 1 | V2 配置与操作授权契约 | 已实现；22 类精确投影及拒绝测试通过 |
| 2 | 可信记录、原子写入与账本恢复 | 已实现；事务、revision、账本、取消与持久化失败测试通过 |
| 3 | Adapter、控制流与 Headless 往返 | 已实现；三类节点往返、原生字段注入、Schema 漂移和审批零业务写入测试通过 |
| 4 | 版本化初始化夹具与隔离执行 | 已实现；真实 classic runner 的基线/候选隔离与恢复测试通过 |
| 5 | 配置级门禁与效果断言 | 已实现；效果证据、私有正文隐藏及部分成功测试通过 |
| 6 | 界面、文档、全量回归 | 见顶部 2026-10-03 当前状态；早期测试数字不再作为当前门禁。固定 r2 隔离效果通过，最新基线已独立集成；完整教程与全量质量门禁仍未关闭 |

能力只有在真实 Adapter 与 compiler checksum 一致后进入 Snapshot；批次 1 不提前公开未实现节点，22 类精确校验在批次 3 完成。

## 协作

Astra 负责契约、权限、运行来源、集成和最终验收。Sol 的 SQLite 子任务仅修改其独立工作树中的 `server/data_tables/store.py` 与新增专用测试；前端子任务另用独立工作树，限定初始化编辑器、写授权和安全报告投影及对应测试。父端逐项审查并集成，不直接整树覆盖；子任务不操作浏览器、容器或业务数据，不提交。

## 验证与交付

- 重点：Agent Table、Workflow、NodeContract、Graph IR、Headless、Publish、Evaluator、App、Evolution。
- 后端：`python -m pytest server/tests/ -q`；相关文件 `py_compile`。
- 前端：生产构建、单元测试、独立预览器与帮助中心重放。
- 检查：`git diff --check`、敏感信息、SQLite/Runtime/构建产物排除。
- 真实模型与实际测试表写入另行授权；本次没有继承 Vision 09 调用授权。
- 人工验收和提交授权后才创建独立 PR，不自动合并。

### 当前验证记录（2026-09-08）

- Python 环境：现有独立测试虚拟环境 Python 3.12；测试显式加载 `pytest_asyncio` 和 `anyio`，数据使用临时合成目录。以下自动化回归未调用真实模型、读取或修改用户业务记录；后续授权的真实生成单列。
- `test_evaluation_write_isolation.py`、`test_evaluation_controlled_write_runner.py`、`test_controlled_write_runtime.py`：39 passed。先复现取消后继续派发及未声明业务字段绕过，再验证定点修复；保留旧 JSON 对象行为。
- Headless、Evaluation API 与隔离组合：64 passed；视觉/Capability/实际 Runner 组合：124 passed。均为自动化证据，不代替预览器真实验收。
- 最终受影响后端组合：586 passed，覆盖 Agent Table、真实本地 Runner、NodeContract、Graph IR、Headless、Publish、Evaluator、App 和 Evolution；保留 FastAPI 既有弃用 warning。三类写节点的批准测试禁止 Backend CRUD 调用，仅产生 Xpert 草稿。
- 最后证伪：无 `input` 的普通根节点分流到同表双写，修复前未拒绝，修复后拒绝；有序对照仍通过。私有 JSON 未知键名经 Pydantic 错误位置泄漏的三组反例先失败，摘要改为仅返回安全错误类型后通过。未改 classic 调度器或旧节点执行行为。
- 前端新增/受影响五个测试文件：40 passed；未应用或无效 JSON 阻止保存且不发送请求，异常写证据隐藏正文，授权字段直接显示类型。`npm.cmd run build` 通过，仅保留原大 bundle 提示。
- 最后全量前端（`npm.cmd test -- --reporter=dot`）：994 passed、2 failed，136 个文件通过、1 个失败。两个失败均在未修改的 `models.refresh.test.ts`：`nex-n2-mini/pro` 的目录过期时间为 `2026-09-08T00:00:00Z`，当前动态有效数量从 509 降为 507。模型目录及对应测试与基线相同；不在本轮修改目录或放宽断言。独立 `node --test server-headers.node.mjs` 通过。
- 第一轮全量后端：6129 passed、147 failed、67 skipped、4 errors。发现缺少 Agency worker 构建产物后，用相同 lockfile 的独立依赖副本完成构建；未增加依赖。
- 第二轮全量后端：6151 passed、125 failed、67 skipped、4 errors。结构化结果保留于本地临时 `mm-cw10-full-23.xml`，不是全绿。后续在未修改的 `436d2453` 独立工作树复跑失败项，其中 118 项已复现于基线；4 项本轮能力计数/生成目录断言已修正并纳入最终 586 项通过结果。
- 剩余 3 项全量失败中，RSS 编码用例与 Renderer 短超时用例已在当前树单独复跑通过，Office sidecar 的 50 ms 总 deadline 仍有不稳定失败；没有修改其超时或放宽断言。4 个 error 为两个超长参数用例的 Windows pytest 环境变量错误，本轮仍需支持环境中的全量验证，不能只凭分类宣称全量通过。
- 最终 `py_compile` 覆盖 42 个变更 Python 文件；`git diff --check` 通过；变更范围禁止产物路径及敏感模式命中均为 0。SQLite、Runtime、构建产物和临时报告不纳入提交。
- 用户追加授权回归及一次预览器真实生成后，执行独立 Linux 离线回归。容器使用现有测试依赖镜像，`--pull never --network none`、无宿主挂载、6 GiB / 4 CPU / 1024 PID 上限；没有启动、停止或重建共享栈。
- 测试固定 `436d2453` 加本轮 67 个文件的源码归档，SHA-256 为 `28b29a009e706e3239182f2836da43a6d09d616a72a4734ba2ab7d75163a03ea`；测试过程中逐文件核对无源码漂移。依照 CI 的隔离方式先运行 `test_workflow_run_contract.py`（7 passed），再运行 `server/tests/ --ignore=server/tests/test_workflow_run_contract.py`（6331 passed、30 skipped、7 warnings）；两个退出码均为 0，合计 6338 passed，耗时约 1121 秒。
- Linux 跳过项明确保留：Windows 原生/句柄检查、专用 mcp-files/Renderer/语言服务器环境、缺少的 docx/matplotlib 依赖，以及测试镜像只有 Node 20 而未满足 Node 24 的上游 worker 检查。跳过不代表通过；这次结果不能覆盖上述独立环境边界。安全源码清单、JUnit 与容器退出回执保留于本任务忽略目录，不纳入提交。
- 独立空数据预览器已在回环地址 `15439/16439` 启动，页面与 API 均确认 V10、22 类节点；默认写授权、查询表授权、写节点及视觉授权均为空或关闭。三种写节点在未配置逐表授权前不可选。
- 初次启动时 Provider 管理面尚未配对，真实生成被阻断。用户随后完成配对、在本预览器保存新的 OpenRouter 连接，并明确批准使用 `deepseek/deepseek-v4-flash-0731` 生成一次，最多三次 completion；未继承上一轮凭据或额度授权。
- 未执行独立预览器业务表写入、共享栈操作、提交、推送或 PR。真实生成不包含执行写节点、批准 Proposal 或发布 Xpert 的授权；帮助中心实操与重放仍未完成。

### 独立预览器真实生成（2026-09-08）

- 用户明确授权零记录合成表“受控写入验收工单”的安全元数据、测试目标和写节点选择：`insert/update/delete`、字段 `title/status/severity`、每节点最多 1 行；查询与写入授权分别选择，其他资源及中间件不授权。目标为插入、查询、更新、重新查询取得 revision、删除及 Agent 汇总，只生成待审核候选。
- 外发前的独立预览器守卫发现两次 UI 提交仍携带中间件授权并拒绝，均未派发 completion。已证实根因：前端能力选择器统一优先使用 `kind`，但中间件的真实授权 ID 是 `id`，使默认勾选显示与实际 scope 不一致。最小修改仅调整按分组取 ID，并增加回归测试；没有放宽服务端授权检查或守卫。
- 三个反例在旧取 ID 行为下先失败，修复后受影响五个前端测试文件合计 43 passed；再次执行 `npm.cmd run build` 通过，保留原 bundle 提示。此项前端修复发生在上述 Linux 全量之后，后端生产代码未改变。一次误触发的全量前端测试已中止，不计作通过。
- 通过可见界面重新确认全部授权后，实际 OpenRouter completion 派发 3 次，均为 HTTP 200 且 `finish_reason=stop`，未观察到不确定派发，也未发生第四次请求。按服务端 usage 回执，任务规划、能力编译、唯一修复分别为 4,060 / 22,319 / 19,758 Token，总计 46,137 Token；不推断实际费用。
- 实测结果为**失败**：Proposal `proposal_1109f5707db44f54bb6e79f013c1d27d`，revision 1、`pending`、`validation.valid=false`、`repair_used=true`、`repair_protocol=graph_patch_v1`。末次诊断为 `update_work_order` 的业务值、记录及条件端口未与语义配置一一对应。本次不能记为真实候选生成验收通过。
- 已确认诊断链：`_validate_data_table_write_shape` 拒绝端口集合或基数不一致；修复失败后 `_fallback_candidate` 创建占位图，并清空首个 Agent 模型以保持不可批准，因此“缺少模型”是占位图的次生诊断，不是本次凭据或 Provider 调用失败。
- Headless 状态读取当时另返回 500：占位图不含任务要求的 `data_table_query`，`HeadlessAuthoringService._state` 的 preview 校验异常未在该路径转换为安全诊断。后续最小修复记录见下；不能用普通 Proposal 可读取代替 Headless 可编辑闭环证据。
- 证据边界：现有失败分支只持久化最终诊断和占位 IR，未保留被拒 Intent 的端口集合或修复操作摘要。因此尚不能证明具体缺失/多余的是哪个输入，也不能仅根据末次 6 个输出 Token 断言模型返回空 Patch。后续先补可定位且不含业务正文的端口诊断与离线反例，不猜测或自动补写记录来源。
- 生成后通过只读记录接口复核合成表 `count=0`；未运行工作流、写入测试记录、批准 Proposal 或发布 Xpert。额度已用完，停止模型派发；真实复测必须另获授权。安全调用回执和日志仅保留于本任务忽略目录，不提交凭据、完整 Prompt 或模型原始响应。

### 定点诊断与最小修复（2026-09-08）

- 范围为三个生产后端文件、一个新测试文件和本任务卡：`node_adapters.py`、`meta_planner_v2.py`、`headless_authoring.py`、`test_meta_planner_write_generation_failures.py`。不修改记录来源、写入授权、变量推导、Runtime 或审批规则，不自动补线，不扩大模型调用预算。
- 已证实两个缺口：写节点校验只报告端口不一致，唯一修复没有得到确切的缺失/重复/多余输入诊断；失败占位候选的 Headless round-trip 校验抛出未转换的 `ValueError`，产生 500。原始被拒 Intent 未保存，因此无法还原本次模型究竟遗漏哪条边；不把猜测作为根因结论。
- Adapter 沿用原有集合与基数校验，增加类型化错误和受限诊断：节点 ref、应有/缺失/重复端口及多余输入的位置。未知端口只记录位置，不回显其内容或业务值。唯一 Patch 修复读取同一个校验器的诊断；编译与修复两次尝试分别保存阶段、通过状态、问题数量和端口摘要，不保存原始模型输出。
- Headless 只在 preview 校验边界捕获 `ValueError`，转换为 `headless_candidate_invalid`（HTTP 422）；无效候选仍不可编辑或 Apply，不绕过固定计划和资源校验。未捕获其他异常冒充普通校验失败。
- 先用合成失败候选复现两个失败：实际 API 为 500、服务层泄漏 `ValueError`；修复后与已有写入 Headless 测试共 22 passed。端口诊断、修复上下文及分阶段记录的反例先得到 12 failed / 2 passed，再完成修复。
- 最终定点命令：`python -m pytest server/tests/test_meta_planner_write_generation_failures.py server/tests/test_meta_planner_controlled_writes.py server/tests/test_meta_planner_write_contracts.py server/tests/test_meta_planner_write_headless.py -q -p pytest_asyncio.plugin -p anyio.pytest_plugin --basetemp <独立临时目录>`，85 passed。另复跑 Meta Planner V2、Headless、V3 证伪、只读资源、纯节点、控制流、视觉 Adapter/Graph/Headless、NodeContract 与 Authoring 共 277 passed；合计 362 项通过，保留 FastAPI 既有弃用 warning。
- 使用现有测试虚拟环境，关闭第三方 pytest 自动加载并显式加载上述两项插件。默认临时目录遇到 Windows 权限错误后改用预先确认不存在的独立目录，未修改 ACL 或清理其他任务目录。确定性 stub 明确提供 `connect_data` 时可在三次 completion 内生成有效 pending 候选；空 Patch 仍失败。这是离线修复协议证据，不是真实模型生成成功证据。
- 仅重启本任务 `16439` 独立预览后端，并以诊断模式禁止加载 Provider 凭据；前端 `15439` 和共享栈未重建。原失败 Proposal 的 Headless GET 实测为 422，返回受控错误及缺少必需 Query 的诊断。它没有被改成有效候选，也没有修改原失败报告。
- 原 Proposal 仍为 revision 1、pending，payload checksum 与修复前一致；合成表仍为 0 条。调用账本 SHA-256 仍为 `739042d70fe4308371ddb72de2282d01873e8f67c76e4342e92b230d25b22a1d`，完成次数仍为 3。没有新增 completion、执行写节点、批准或发布。
- 同时通过前端端口 `15439` 的 API 代理核验相同 422 结果。`python -m py_compile` 检查本批四个 Python 文件通过；`git diff --check` 通过；本批五文件敏感模式命中为 0，全树待提交路径未出现 SQLite、Runtime、构建或临时产物，暂存区为空。
- 这次修复之后尚未重跑全量后端及前端构建；上文 Linux 6338 passed 对应更早源码，不能代替最新全量门禁。后续授权真实复测结果见下。

### 授权真实复测 R2（2026-09-08）

- 用户明确要求真实预览器模型调用实测后才能提交，并追加一次复测授权。沿用同一 OpenRouter 连接、`deepseek/deepseek-v4-flash-0731`、原合成表和原目标，最多三次 completion；仅生成候选，不执行写节点、批准或发布。
- 只重启本任务 `16439` 后端，沿用 `15439` 前端。复测使用独立 `generation-receipt-r2.json`，未清空或覆盖原账本。外发守卫继续限定目标、模型、表、操作、字段和 1 行上限；其他资源和中间件均未授权。真实生成前复核 V10 共 22 类能力，合成表为 0 条记录。
- 通过可见界面核对配置并点击一次“生成候选智能体”。三次 completion 均收到 HTTP 200 / `finish_reason=stop`，actual usage 为 2,955 / 20,279 / 22,719 Token，合计 45,953；不确定派发为 0，没有第四次请求，不推断实际费用。
- **实测失败，提交门禁未通过。** 新 Proposal 为 `proposal_b3e197af5a6e466a9bb80a030f1870ba`，revision 1、pending、`validation.valid=false`、`human_modified=false`。payload checksum 为 `feb17d4ccbee5ec28369eee5bd90f3767b530c277c0d7381342c12bf7e85f86a`。保留原失败记录，不通过人工改写候选把本次实测改记为成功。
- 新增的阶段证据确认：能力编译阶段 1 项错误，走唯一 `graph_intent_v3` 整体修复，未进入 `graph_patch_v1`；修复后 8 项错误。因此本次不能作为真实模型使用端口 Patch 诊断完成修复的证据。
- 最终可定位错误：两个 Query config 含不允许的 `max_affected_rows`；Update 缺少必填 `filter`；Delete 应绑定 `predicate_id/records`，实际缺少 `predicate_id` 且多出位置 1 的输入；四个节点消费无生产者的 `insert_id`。这些是已证实的契约违例，不能仅凭它们推断初次无法解析的具体字段，也不能通过补造 ID、删除条件或放宽授权处理。
- 页面最终显示“候选未通过固定计划与编译契约校验，暂不能进行类型化编辑”，重新加载候选仍受控拒绝。前端代理的 Headless 状态接口返回 422 / `headless_candidate_invalid`，没有再出现该路径的 500。占位图缺少 Agent 模型仍是失败保留机制，不是此次 Provider 认证失败。
- 复测后合成表仍为 0 条记录，Proposal 仍为 revision 1；未运行、批准、发布或创建 PR。新账本 SHA-256 为 `040e10b3f14b6327ef0f0275bca1d520d7f606cd1e293f8650ff494878633a6f`；原账本 hash 仍与 R1 一致。
- 本次没有修改生产源码或降低断言。下一步应先离线核对生成/整体修复上下文与 Query/Write 动态端口及输出契约的一致性，再确定最小修改；不盲目追加调用、不扩大本轮范围。下一次真实调用需新授权。

### R2 后模型契约定点修复（2026-09-08）

- 用户仅授权定位与最小修复，下一次真实模型复测仍需单独授权。本批只修改 `node_adapters.py`、`meta_planner_v2.py`、`test_meta_planner_table_prompt_contract.py` 和本任务卡；不修改 Preview 守卫、原测试目标、模型、授权范围、Runtime、业务表或历史失败 Proposal。
- **已证实的平台缺口**：首次生成、整体 GraphIntent 修复及 Patch 修复共享静态 NodeContract 端口投影。该投影只有 `predicate` 端口族，但真实 Adapter 要求每个动态谓词对应 `predicate_<ref>`，并要求 `values` 随 `value_source` 出现或消失、`records` 对 Update/Delete 始终存在。模型上下文没有这套可用于构图的条件绑定契约。
- **输出说明缺口**：Insert 的真实 `result` 是完整记录对象；Update/Delete 是 `matched/affected` 计数对象。原静态端口仅投影宽泛类型，没有说明 `record_id` 不是独立端口或变量、也不支持属性路径。本批增加明确的整个结果绑定说明，不生成 `insert_id`，不增加属性提取节点，不替模型连接记录来源。
- **不归因于缺失 Schema 的错误**：Query 不允许 `max_affected_rows`、Update 必须有 `filter`，原模型上下文已经包含这两项约束。它们是模型违反已有配置 Schema；本批仅从同一 Pydantic 模型派生 allowed/required 字段摘要，帮助区分读写配置，不放宽、自动补值或删除这些检查。首次无法解析的具体字段仍未留存，不能断言已解释两轮真实失败的全部原因，也不能保证模型下次一定遵守契约。
- Query/Write 的应有输入集合改为由 Adapter 内同一个函数计算，供原校验器和模型投影共同使用；原集合、基数、资源、revision 与授权规则保持不变。三个模型路径得到相同的 `input_binding_contract`、`output_binding_contract` 和 `config_field_contract`，仅在模型上下文移除误导性的静态输入端口族。说明也按实际节点裁剪：Query 不携带写入 `values/records` 要求，Insert 不携带谓词输入要求。Registry API、Capability V10/22 类、compiler checksum、IR、Headless/SSE 协议和三次调用上限不变。
- 先补反例：15 项明确因缺少模型绑定契约失败；另外 3 项测试错误地从未注入报告的编译产物读取 IR，已改为直接检查真实 `resolve_graph_intent` 输出，不计为产品缺陷。修复后首轮专项 23 passed；随后增加 `is_null` 模式及完整顺序链验证，最终新测试文件共 25 项。字段摘要与 Pydantic 对齐、13 种输入配置与校验器对齐、缺失/重复/伪造端口拒绝、R2 四类违例拒绝、脱敏与授权范围均有断言。
- 完整合成链为 Insert、Query、Update、重新 Query、Delete、三份真实输出的序列化及 Agent 汇总，未添加属性提取或虚构 ID。编译、反编译、再编译的语义 checksum 一致；对全部原生字段比较时只按稳定 ID 规范化边数组顺序，符合既有反编译器按语义 ref 排序的契约，不忽略边内容。测试把记录读写入口替换为失败哨兵，证明本次仅编译，没有执行业务操作。
- 定点回归命令：`python -m pytest server/tests/test_meta_planner_table_prompt_contract.py server/tests/test_meta_planner_write_generation_failures.py server/tests/test_meta_planner_controlled_writes.py server/tests/test_meta_planner_write_contracts.py server/tests/test_meta_planner_write_headless.py -q -p pytest_asyncio.plugin -p anyio.pytest_plugin -p no:cacheprovider --basetemp <本批独立目录>`，**110 passed**。相邻 Meta Planner V2/Headless/V3 证伪/只读/纯节点/控制流/视觉、NodeContract、Authoring、Publish、Evaluator、Evolution 和 App 共 15 文件，**370 passed**；最终把上述 20 文件在同一新隔离目录再次合并运行，**480 passed / 70.36 秒**，保留既有 FastAPI 弃用 warning。
- Windows 默认 Store/缓存写权限阻断曾导致两次收集失败；另一次失败报告输出停滞后明确中止，不计通过。最终使用全新的隔离 Runtime 与 pytest 目录、关闭 dotenv 和真实凭据、显式加载两个测试插件重跑，未修改 ACL、未清理其他任务目录。`py_compile` 因缓存写权限失败后在本任务范围重跑通过。
- 完整授权合成夹具的首次生成 Prompt 从 70,851 字符增至 77,455 字符，增加 6,604 字符（约 9.3%）；这是字符统计，不是实际 Provider Token/费用。没有新请求或额外修复轮次。
- `git diff --check` 通过；本批四文件敏感凭据模式命中为 0，全树待提交路径未包含 SQLite、Runtime、构建或临时产物，暂存区为空。本批未重跑全量 `server/tests/` 或前端构建，不用专项结果替代剩余交付门禁。回退本次契约投影修复不会更改已有 Workflow、Proposal 或数据表持久化格式。
- R1/R2 调用账本 SHA-256 仍分别为 `739042d70fe4308371ddb72de2282d01873e8f67c76e4342e92b230d25b22a1d` / `040e10b3f14b6327ef0f0275bca1d520d7f606cd1e293f8650ff494878633a6f`，未重启或操作独立预览器、未读取凭据、未新增 completion、未批准、发布、提交或创建 PR。修复仅有离线证据，下一次真实复测等待用户授权；最新全量与提交门禁仍见下文。

### 授权真实复测 R3（2026-09-08）

- 用户单独授权本次复测，沿用 R2 的 OpenRouter 连接、`deepseek/deepseek-v4-flash-0731`、零记录合成表、中文目标、逐表三种写操作和每节点 1 行上限。通过可见预览器点击一次“生成候选智能体”；没有改写目标、降低校验、执行工作流、批准或发布。
- 仅将忽略目录中的启动器账本改为 `generation-receipt-r3.json`，重启本任务 `16439` 后端，保留 `15439` 前端和共享栈。守卫离线测试 6 项通过；调用前验证 V10 共 22 类能力、合成表 0 条、R3 账本 0 次。实际载入的 `node_adapters.py` / `meta_planner_v2.py` SHA-256 分别为 `a88c720e4735279b7a101e12990c6c59350151c5176efbb9c1ef3c41e7b6d42f` / `7dc18cfac79c764d057f0b0e81e470bb069d5c2781a904b9962217fa1494cc7a`，对应上节离线修复；本次未修改生产代码。
- 三次 completion 均为 HTTP 200 / `finish_reason=stop`，实际 usage 依次为 3,166 / 4,619 / 24,538 Token，合计 **32,323 Token**；不确定派发为 0，没有第四次请求。最终报告确认顺序是任务规划、唯一任务计划修复、能力编译，而不是能力编译后的 Graph Patch 修复；不能根据请求序号猜测阶段。
- **实测仍失败，提交门禁未通过。** Proposal 为 `proposal_d40e7444dd1a43c6bc18560e5d05ae1e`，revision 1、pending、`human_modified=false`、`validation.valid=false`、`repair_protocol=task_plan_v1`。payload digest 为 `04840f21eb87dcedaa6ddcc5bd36fd2ca16b9adcba3d8c5f64102b670d982eb8`，不以人工改写替换本次结果。
- 能力编译阶段记录 1 项主校验错误：`value_type` 不符合 V2 兼容类型枚举。独立端口诊断另外确认 `delete_work_order` 需要 `predicate_id/predicate_revision/records`，缺少 `records`；无重复端口和多余输入。唯一修复已被任务计划消耗，故该编译结果没有再次调用模型修复。首次任务计划失败的具体字段未持久化，不作猜测。
- 只读检查发现 `validate_blueprint_authorization` 仍经 `_typed_blueprint` 将 V3 的 `value_schema.type` 直接传入 V2 `value_type`；V3 支持 `integer`，旧枚举不含该类型。这是应先离线验证的兼容投影缺口，不能只增加 Prompt 或把所有问题归因于模型。被拒原始 Intent 未持久化，本次不声称已经恢复全部错误配置或完成根因修复。
- 预览器最终显示候选未通过固定计划与编译契约校验、画布只读。前端代理的 Headless GET 为 422 / `headless_candidate_invalid`，没有该路径的 500。失败占位图缺少模型和 Query 不视为原始生成图的证据；原始编译诊断以上述阶段摘要为准。
- 复测后表仍为 0 条、Proposal revision 仍为 1。R3 账本 SHA-256 为 `7ca8a1bd4ba08994737cc1fbec53d1c6cc1f3e836776eb7f642ecaf80d371d69`；R1/R2 账本 hash 与前述记录完全一致。未执行写节点、批准、发布、提交或 PR，未新增生产依赖或修改共享栈。后续真实调用仍需新授权。
- 本次是最新定点修复的真实失败证据，不替代最新全量回归、上游兼容集成及完整写入验收；这些交付门禁继续保留。

### R3 后整数兼容投影最小修复（2026-09-08）

- 用户先要求快速证伪，再明确授权手术刀式修复。本批限定 `schemas.py`、`graph_ir_v3.py`、`meta_planner_v2.py`、新增 `test_meta_planner_integer_projection.py` 和本任务卡五个文件；风险在规划/编译兼容层，不涉及 Runtime、业务表、节点授权、Prompt 文案、模型调用预算或审批协议的修改。测试启动器仅存于本任务忽略目录。
- **已证实根因**：使用同一合法 Deserialize、Serialize、Agent 合成图，`number` 和 `array<integer>` 可通过，顶层 `integer` 可通过 V3 Schema、Adapter 与 Resolved IR，却在 `_typed_blueprint` 和 `graph_intent_to_v2` 的旧 `MetaPlannerValueType` 枚举处失败。反向兼容函数 `_value_schema` 另把 `integer` 降为 `any`。这些是平台兼容投影缺口，不是 Provider 认证或运行时 JSON 解析失败。
- 修复让 V2 兼容绑定与反向转换复用 NodeContract 的 `WorkflowValueType`，保留整数标签，不转换为 `number` 或 `any`。解除枚举阻断后，反例进一步确认旧授权校验错误拒绝 `integer -> number`；本批仅将其粗类型比较改为复用已有 `_schemas_compatible`。完整 V3 类型、nullable、来源和字段检查仍由原解析器执行；旧 V2 的 `any` 兼容规则不变，未知类型仍拒绝，无需持久化数据迁移。
- 先运行新回归得到 **7 failed / 20 passed**，失败均位于整数兼容投影；单独的反向转换反例另得到 **1 failed**。只修类型投影后为 **1 failed / 27 passed**，剩余失败准确定位到旧授权校验的整数到数值误拒绝，再完成共享兼容判断修复。未降低断言或自动修补图。
- 最终新增测试共 **29 项**：覆盖权威类型枚举一致性、两条 V3/V2 投影、整数与可空/嵌套整数保留、完整 Native 编译/反编译/再编译 checksum，以及真实 Query 记录配合整数 revision 谓词的删除编译链。浮点、布尔、文本和 null 不能冒充整数；`number/any/nullable integer/boolean -> integer` 的不安全连接仍拒绝。新测试通过网络/SQLite 失败哨兵证明编译不执行 Provider 或数据库操作。
- R3 的 `delete_work_order` 缺少 `records` 是独立有效诊断；缺少该端口的候选仍拒绝，不自动补线、不从 ID 或 revision 谓词伪造记录身份。R3 被拒原始 Intent 未保存，本批不声称还原全部模型配置、修复其任务计划或保证下次生成成功。
- 定点验收目标为 `python -m pytest server/tests/test_meta_planner_integer_projection.py -q`。相邻回归覆盖全部 `test_meta_planner*.py`，以及 `test_meta_agent.py`、`test_workflow_node_contracts.py`、`test_xpert_runtime_authoring.py`、`test_xpert_publish.py`、`test_xpert_evaluations.py`、`test_xpert_structure_evolutions.py`、`test_xpert_app_api.py`、`test_workflow_typed_values.py`、`test_workflow_typed_ai.py`，共 **24 个文件、563 passed / 104.77 秒**。使用既有测试虚拟环境，显式加载 `pytest_asyncio.plugin` / `anyio.pytest_plugin`，禁用缓存插件、dotenv 和真实凭据，阻断外部网络；保留 4 条既有 FastAPI 弃用 warning。
- 环境失败保留：首次组合收集因默认 MCP Store 的 SQLite 写权限出现 12 个 error；显式隔离该 Store 后为 562 passed / 1 failed，失败是 Data X 临时文件路径长 285 字符且父目录存在。第一次短目录尝试未进入测试阶段，已中止；获得本任务临时目录写权限后，同一 Data X 用例在短路径中单独通过，再用全新短目录完成上述 563 项组合回归。没有修改 Data X/MCP 生产代码、ACL 或测试断言，也没有清理其他任务目录。一次新测试夹具直接插入原始控制边字典的序列化 warning 已改为使用现有类型化边构造器，不计作产品缺陷。
- 四个本批 Python 文件的 `py_compile` 通过，缓存写到独立临时目录；`git diff --check` 通过。本批五文件敏感凭据模式命中为 0，全树待提交路径未包含 SQLite、Runtime、构建或临时产物，暂存区为空。未在本批重跑全量 `server/tests/` 或前端构建，早期 Linux 全量不能替代当前交付门禁。
- R1/R2/R3 调用账本 SHA-256 与上文完全一致，`node_adapters.py` 和 `headless_authoring.py` 文件 hash 未变。未读取凭据、操作或重启预览器、调用模型、写业务表、批准、发布、提交、推送或创建 PR。真实复测仍需用户新授权，历史失败 Proposal 保持原状。
- 本批回退只恢复这三个兼容层文件中的上述小块修改，不回滚本轮其他实现或持久化数据；但会重新暴露合法顶层整数候选被拒的问题。真实生成、完整写入验收和提交门禁继续独立保留。

### 授权真实复测 R4（2026-09-08）

- 用户单独授权复测。固定 R3 的零记录合成表、中文目标、OpenRouter `deepseek/deepseek-v4-flash-0731`、查询及三种写操作授权、`title/status/severity` 字段和每节点 1 行上限；只通过可见页面提交一次生成，不执行写节点、批准或发布。没有修改目标、生产代码、校验或调用预算。
- 复测前发现本任务 `15439/16439` 均未监听，仅恢复这两个独立预览服务，保留原 Runtime；未操作共享栈。忽略目录启动器改用全新的 `generation-receipt-r4.json`，调用守卫离线测试 **6 passed**。页面提交前核验 Capability V10 / 22 类、表 0 条、R4 completion 0 次及完整授权范围；原错误页不能操作后打开同一合法 HTTP 地址的新预览标签页。
- 载入的三个整数修复文件 SHA-256：`schemas.py` 为 `a6e2f1ad8bd4cc39affc4ecf1532346e7474b66e9bccd7211ca69fdc599aae80`，`graph_ir_v3.py` 为 `71275de95573ed7bc6b1581c9afbedd0eac33779ecff61aea05a45683f5afa94`，`meta_planner_v2.py` 为 `b3baac9d45c2a080686539e66eced1fca0b4b1d941db32cf8a26a04e88803b4e`。记录仅用于绑定本次执行源码，不代表最新全量门禁已完成。
- 实际派发 **3 次 completion**，均为 HTTP 200 / `finish_reason=stop`，实际 usage 依次为 **3,867 / 26,589 / 25,274 Token**，合计 **55,730 Token**。不确定派发为 0，无第四次请求或自动重发。报告确认采用任务规划、能力编译、唯一 `graph_patch_v1` 修复路径。
- **实测失败，仍不可提交 PR。** 新 Proposal 为 `proposal_206a46ba81cc404187c2a533306d0931`，revision 1、pending、`human_modified=false`、`validation.valid=false`；payload digest 为 `a7458c8e9a498f4457fa45742b03fe9b109f57e3ed17d119a52f116b506403a5`。没有人工改写本次失败候选。
- 分阶段证据：首次能力编译 `issue_count=24`，唯一 Patch 修复 `issue_count=1`，两阶段 `input_contract_issues=[]`。末次直接阻断为 `Control edge already exists.`；源码对应 `graph_patch.py` 的 `ConnectControlOperation`，以 `source_ref + outcome_ref + target_ref` 检出已存在的控制边后拒绝。修复上下文中的重复数据边已由既有精确 no-op 处理记录为 warning，控制边随后在同一 Patch 内核被拒；本次不放宽该检查或自动重连。
- 证据限制：首次 24 项编译问题仅保存数量，原始被拒 Intent、Patch 及具体重复控制边未保存，不能据此断言只剩一个问题、确认首次全部错误类型或承诺去重即可成功。旧整数兼容问题有上节离线证据，但不能用本次缺少问题明细证明原始生成图的所有类型均正确。下一步应先离线复现控制边重复 Patch，并补齐受限编译诊断，再确定最小修复，不继续盲目消耗额度。
- 页面展示新失败候选及锁定画布；经 `15439` 前端代理读取 Headless 状态为 **422 / `headless_candidate_invalid`**，未出现该路径 500。占位图的缺模型、缺 Query 仍是次生诊断，不作为原始生成图的证据。复核表仍为 **0 条**，未执行工作流、业务写入、批准、发布、提交、推送或 PR。
- R4 安全调用账本 SHA-256 为 `6be22af1c1339fad1ad239f76eb25327e05642b60265ac23b9b3a81e79179256`；R1/R2/R3 hash 保持不变。账本、日志及 Runtime 只保留于忽略目录，不提交凭据、完整 Prompt、原始模型输出或记录数据。本次授权已使用，后续真实复测需新授权。

### R4 后离线复现与累积修复审查（2026-09-08）

- 用户要求先复现，避免连续失败后继续堆叠补丁。本批仅增加隔离的合成复现夹具并更新本任务卡；没有修改生产源码、Prompt、严格校验、修复预算或原测试目标，没有发起真实模型请求、操作浏览器、重启服务、执行工作流、批准或发布。复现使用合成图，不能冒充未持久化的 R4 原始 Intent。
- **直接阻断已复现**：同一合法合成 Update 图故意缺少 `records` 输入。确定性 completion 依次提供任务计划、该无效图、显式 `connect_data` 修复时，可得到有效 pending Proposal；只在同一修复中再增加一条已存在的 `lookup:success -> write` 控制边，就以 `Control edge already exists.` 失败。两者都严格三次 stub completion，修复温度为 0，不创建 Xpert 或访问业务记录。这是故障触发条件的成对证据，不是真实 Provider 成功证据。
- **共享 Patch 内核行为符合当前严格协议**：精确控制边重复在原始及既有归一化路径都拒绝；先断开再连接保持合法；不同 outcome 不按重复边处理，但仍受完整解析器校验；前序元数据修改不会在失败后泄漏到原图。遍历长度 1–4 的 340 种连接/断开序列，结果与有序集合协议一致。Handle、join、Schema 和 checksum 注入仍拒绝。本批没有将 `connect` 改成 upsert，也没有静默删除模型操作。
- **不支持“最近四次修复新增了这些归一化补丁”的归因**：以当前 HEAD `436d24535c6f0ad80b3a4f18830fb834f90a7b95` 作 AST 对照，`_normalize_repair_control_only_outputs`、`_normalize_repair_patch_required_outputs`、`_normalize_repair_duplicate_data_edges` 和 `_normalize_adapter_outputs_for_repair` 均已存在且函数 AST 完全未变。数据边精确 no-op 去重与控制边严格拒绝的差异是继承行为；可以单独审计一致性，但不能据此要求回滚已经证伪并修复的整数兼容缺陷。
- **不支持“修复模型没有收到原控制边”的归因**：`_patch_repair_prompt` 已包含完整 `base_graph_intent.control_edges`，并发送前 30 项校验诊断；24 项错误全部在该范围内。合成测试直接断言两者均存在。仅追加相同说明没有已证实的平台缺口作为依据；模型如何理解这些内容仍无法从已保存报告确定。
- **已证实的观测缺口**：`_generation_attempt` 对非写入端口错误只保存数量。两组完全不同的 24 项合成错误得到完全相同的阶段摘要，说明事后无法从该摘要恢复错误类别。Patch 重复控制边异常发生在后续 Adapter Schema 归一化及重新编译之前，异常分支将 issues 替换成这一条消息。因此 R4 的 `24 -> 1` 是两个不同阶段的记录，不能视作 23 项错误已消除，也不能证明控制边去重后即可通过。
- 复现命令：`C:\tmp\modelmirror-mcp-test-venv\Scripts\python.exe -B .tmp-cw10-preview/run_integer_checks.py .tmp-cw10-reproduction/test_r4_reproduction.py`，**356 passed / 62.54 秒**。相邻命令使用同一隔离启动器运行 `test_meta_planner_write_generation_failures.py`、`test_meta_planner_table_prompt_contract.py`、`test_meta_planner_integer_projection.py`、`test_meta_planner_headless_authoring.py`、`test_meta_planner_v3_falsification.py`，**142 passed / 30.66 秒**。均保留 4 条既有 FastAPI 弃用 warning。498 项离线检查不替代真实生成、最新全量回归或提交门禁。
- 测试环境失败单列：首次启动在创建短路径临时目录时受沙箱权限阻断，栈定位到 `tempfile.mkdtemp` 后停止；获本批临时目录权限后，首次收集又因测试与预览启动器同目录、`server.py` 与后端 namespace 同名而误导入启动器。该已退出测试进程曾在内存加载 Provider 凭据并按原内容重存 R4 调用账本，未输出凭据、派发 HTTP、运行 uvicorn 或执行工作流。随后仅将本批新建测试移至独立忽略目录 `.tmp-cw10-reproduction/`，不再与预览启动器共目录；完成上述全部离线检查。没有把环境失败归因于产品或修改生产导入逻辑。六个相关生产文件及 R1–R4 四份安全调用账本 SHA-256 与本批开始时完全一致。
- **建议的下一最小批次，尚未实施**：先保留各阶段受限、结构化诊断，至少区分解析、授权、类型/端口、控制流及 Patch 冲突，并记录失败操作序号、已验证 ref/端口/outcome、规范化前后操作数量和 checksum；未知值只保留类别/位置，不保存 Prompt、业务值、原始模型输出或秘密字段。另明确标记后续重新编译是否执行，避免再次把提前退出误读成问题收敛。保持严格 Headless/Patch 内核与三次调用预算不变。
- 后续每次修复只处理一个有可重放反例的假设；先证明失败，再做最小改动，并验证操作顺序、原子性、授权及邻接回归。精确重复控制边是否可以在模型修复专属入口作为 no-op，需要单独审计，不能扩展为忽略非法边或对图自动补线；在完整首阶段诊断仍缺失时，不把它当作已确认的全链修复方案。新的真实复测仍须单独授权，本轮继续不满足 PR 提交门禁。

### R4 后受限阶段诊断批次（离线验证完成）

- 用户授权补齐上一节诊断缺口。工作树/分支保持本轮专用目录与 `codex/meta-planner-controlled-writes-10`，HEAD 为 `436d2453`，开工时 70 个既有变更路径、暂存区为空；不操作脏主工作区或并入上游交点。
- 本批限定五个文件：新增 `server/meta_agent/generation_diagnostics.py`、修改 `server/meta_agent/meta_planner_v2.py` 与 `server/meta_agent/graph_patch.py`、新增 `server/tests/test_meta_planner_generation_diagnostics.py` 和本任务卡。旧 `generation_attempts` 字段保持兼容，新增报告诊断为旁路数据，不更改 Workflow/Proposal schema、数据库或运行协议。
- 目标是保存受限阶段明细、失败 Patch 操作位置、归一化前后计数/checksum，以及后续编译是否真正执行。新增诊断禁止保存原始 Prompt、模型响应、错误正文、配置值、凭据或物理路径。风险集中在持久化诊断的脱敏和诊断不得改变原拒绝结果。
- 禁止修改 Prompt、目标、授权、Adapter 许可、归一化算法、严格 Patch 判定、异常文本和三次模型调用预算；不自动补线、不执行模型、业务表或预览器。不提交、推送或创建 PR。
- 最小验收使用隔离启动器执行 `server/tests/test_meta_planner_generation_diagnostics.py`，先确认反例失败，再运行全部 Meta Planner、Headless/Authoring/NodeContract 及相邻回归。生产语法、Diff、敏感模式和历史调用账本 hash 必须核对；最新全量后端、前端构建和真实验收继续独立列作交付门禁。回退仅撤销新增诊断与进度接线，不影响既有 Proposal 的读取及原有严格校验。
- 已实现 `meta_planner_report.generation_diagnostics`（诊断版本 1），分别保留任务计划、能力编译和唯一修复的实际执行阶段、失败类别、固定错误码、受限位置/ref 和 SHA-256 指纹。每阶段最多保留 64 项明细，同时记录总数、分类计数和省略数；Pydantic 的多个子错误独立计数，不能直接与旧 `generation_attempts.issue_count` 中的单条合并消息等同。未知错误只保存类别/安全位置/指纹，不复制错误正文或动态字段名。
- Patch 内核只增加可选的被动进度观察器，原有操作、异常类型、文本和接受条件不变。诊断记录归一化前后操作数/checksum，以及失败操作的归一化后零基序号、可精确匹配的原始操作序号、操作类型和已知 ref/端口/语义 outcome；配置和值不保存。归一化已改变的操作不能精确映射时保留空原始序号，不猜测。整个 Patch 的后置校验失败时操作序号为空，不能归咎于最后一条操作。
- `recompile_executed` 严格表示是否已重新进入 `_compile_and_validate` 管线，不表示 Native 编译或验证成功；`checks_executed` 明确区分解析、授权、解析资源、编译和发布预检。Patch 提前失败时为 false，整体 Intent 修复进入解析后即使解析失败也为 true。首阶段错误明细不会被修复阶段的一条异常覆盖。
- 新增专项先复现诊断缺失，再补齐；扩展反例额外发现并修正诊断自身的三项问题：不同节点上的相同 Schema 错误被合并、同一字段的不同自定义错误原因指纹相同、整体 Intent 修复解析失败时管线进入标记遗漏。修正仅涉及诊断身份和标记，错误原因只进入指纹计算，不持久化正文。最终 **23 passed / 10.91 秒**，覆盖合成的 24 项错误定位、归一化删除操作后失败序号、整批后置失败、非法 Patch、阶段保留、64 项上限、脱敏、Store 重开、三次 stub 调用预算及开启/关闭观察器的结果等价。
- 最终相邻命令：`C:\tmp\modelmirror-mcp-test-venv\Scripts\python.exe -B .tmp-cw10-preview/run_integer_checks.py`，**25 个文件、586 passed / 65.04 秒**，其中包含上述 23 项；另用同一启动器复跑 `.tmp-cw10-reproduction/test_r4_reproduction.py`，**356 passed / 44.02 秒**，保留 340 种控制操作序列与成对因果反例。两次均退出 0，保留 4 条既有 FastAPI 弃用 warning。测试使用短路径独立合成 Store，禁用 dotenv/外部网络，不导入预览启动器；专项和复现还直接禁止 HTTP/业务记录调用。这些均为离线证据，不是真实 Provider 成功。
- 四个本批 Python 文件的 `py_compile` 通过，缓存仅写独立临时目录；`git diff --check` 通过。敏感模式扫描的唯一命中是测试中的 `example.invalid` 合成凭据 URL，用于验证脱敏，Provider Key、私钥和长 Bearer 字面值命中均为 0。四份 R1–R4 调用账本，以及 `headless_authoring.py`、`node_adapters.py`、`graph_ir_v3.py`、`schemas.py` 的 hash 与本批前完全一致；四个归一化函数和五个 Prompt 方法 AST 与 HEAD 一致。本批没有改变授权、归一化或修复提示策略。
- **保留边界**：诊断只随新生成的 Proposal 报告保存；任务计划两次失败或外部 completion 异常在创建 Proposal 前抛出时，沿用原行为，不另建诊断 Store。R4 原始 Intent/Patch 和 24 项明细不能追溯恢复，旧报告不会被改写。新增诊断不把重复控制边变成 no-op，也不证明下一次真实生成必然成功。
- 本批未读取凭据、调用真实模型、写业务记录、重启/操作预览器或共享栈、提交、推送或创建 PR。暂存区为空；最新全量 `server/tests/`、前端构建、上游交点集成、真实生成/写入验收和帮助中心仍是独立未完成门禁。下一次真实复测须获得新的明确授权，不把此次离线诊断补齐判定为本轮验收通过。

### 授权真实复测 R5 与因果诊断（2026-09-08 本地 / 2026-09-09 UTC）

- 用户授权本次复测与诊断。沿用 R4 的 OpenRouter 连接、`deepseek/deepseek-v4-flash-0731`、相同中文目标、零记录合成表、11 类生成能力、三个业务字段及每写节点 1 行上限。目标 checksum、生成模型与 Agent 上限配置与 R4 一致；不执行工作流、业务写入、批准或发布。本批不修改生产代码、Prompt、归一化规则或授权。
- 受限终端最初未能读取监听进程，不能据此判定服务停止。使用只读进程核验确认预览器仍在运行后，仅重启本任务 `16439` 后端以加载新增被动诊断和 `generation-receipt-r5.json`；`15439` 前端、共享栈、R1–R4 账本保持不动。守卫独立测试 **6 passed**，调用前确认 R5 计数 0、合成表 0 条。刷新后表单默认值重新收窄到原授权范围。第一次按钮操作被工具安全审核拦截，未到达后端；核验生产 Capability 中“触发器”实际为 `input`、“变量打包”为已授权的 `variable_aggregator` 后，再执行同一按钮。后端日志只有一次生成 POST，未通过其他接口绕过浏览器调用。
- 三次 completion 均为 HTTP 200 / `finish_reason=stop`，依次为任务规划、能力编译、唯一 Graph Patch 修复。实际 usage 为 **3,163 / 26,389 / 24,171 Token，合计 53,723 Token**；不确定派发 0，无第四次请求。回执 SHA-256 为 `7644029497ebddccd623569889570d0956715c686ddca93b0aba187c3a899912`。
- **真实结果仍失败，不能提交 PR。** 新 Proposal 为 `proposal_c9f52011ba124d949e74587ec144f9ac`，revision 1、pending、`human_modified=false`、`validation.valid=false`。实际阶段诊断已经持久化，旧 R4 Proposal 与回执未被替换。
- 任务计划没有校验错误。首次能力编译止于授权/契约检查，共 **3 项**：`insert_ticket` 缺少表资源引用；`update_ticket` 和 `delete_ticket` 的输入缺少 `predicate_record_id_filter`，两者已有 `records`，没有重复或额外输入。插入节点错误指纹 `73496e857b53cb5018769948ecb8ffc5ea03b9f973a0bd6b616858466a1e4ee2` 与源码消息 `Node insert_ticket requires a data_table resource.` 完全匹配；另两个指纹也与相同 ref、索引和端口集合构造出的 `PlannerWriteInputContractError` 精确匹配，不是根据模糊错误标题猜测。
- 本次不是 R4 的重复控制边失败。唯一修复的 **3 个 Patch 操作均应用完成**，规范化前后操作数及 checksum 相同（`bee9ae36b0af47d8184af5921ea746be94a189cf25e953668536d260f7364171`）。随后 `output_normalization` 阶段报 `Node kind data_table_update requires a resource reference.`；`recompile_executed=false`，没有进入重新解析/Native 编译。失败操作序号为空符合该实际阶段，不能归咎于第 3 条操作，也不能把 `3 -> 1` 解释为其余问题已经修复。
- **已证实的平台诊断盲点**：`validate_blueprint_authorization` 中 Adapter 配置/输入形状校验失败后直接 `continue`，跳过该节点独立的资源存在性检查。离线成对反例确认，缺失动态谓词端口可以掩盖同一节点的缺失 `resource_ref`；仅补端口时 Patch 完成后才在输出归一化暴露缺少资源，而同一次 Patch 显式补齐端口与资源引用后，相同合成反例能够通过。这里证明的是诊断依赖顺序问题及一个可靠因果机制，不代表已还原 R5 的完整原始 Intent/Patch，亦不保证真实候选只剩这一问题。
- 离线命令：`C:\tmp\modelmirror-mcp-test-venv\Scripts\python.exe -B .tmp-cw10-preview/run_integer_checks.py .tmp-cw10-reproduction/test_r5_diagnosis.py`，**9 passed / 7.71 秒，退出 0**。包含三项真实错误指纹核对、Update/Delete 的独立错误遮蔽反例、仅修端口与同时显式修资源的成对生成反例。使用独立短路径 Store，禁用 dotenv、凭据及外部网络，直接禁止 HTTP 与业务记录访问；completion 仅为三个本地 stub，不计作 Provider 成功，保留 4 条既有 FastAPI 弃用 warning。该脚本为忽略目录内的诊断材料，不混入生产提交。
- 另有静态契约投影风险：当前模型用的节点自有资源指引明确枚举 Query/Knowledge，写节点投影虽提供 grants、字段和输入端口，但没有同等明确的逐 kind `resource_ref` 位置契约。此项是可审查的投影差距，不把它直接归因为 R5 模型遗漏资源的唯一原因；本批没有通过追加 Prompt 掩盖它。
- 日志没有 HTTP 500；失败候选 Headless GET 两次为 422，符合现有拒绝行为。唯一 traceback 来自未选中视觉能力的目录读取被 `PREVIEW_EGRESS_NOT_APPROVED` 守卫在外发前阻断，已与本次生成失败区分。表仍为 0 条，R4/R5 Proposal 仍为 pending/r1，没有审批、发布或运行 POST。七个相关生产文件、守卫以及 R1–R4 回执 hash 与复测前完全一致。
- **下一最小修复建议，尚未执行**：先让不依赖合法 config 的资源引用/范围检查独立收集，避免端口错误吞掉资源错误；依赖合法 config 的 Schema/字段校验仍有序、严格执行。增加“同节点同时缺端口与资源，唯一修复能同时收到两项”的回归，并保留未知资源、授权、来源、版本及原子性拒绝。不得自动补资源/补线、忽略重复控制边或增加修复次数。节点资源投影是否一并收口须以独立契约反例再确定，不直接改写大量提示。新的真实复测仍需单独授权。
- 本批仅新增忽略目录诊断材料并更新本任务卡。最新全量后端、前端构建、上游交点集成、真实有效生成与完整写入验收仍未完成，不以九项离线反例或 HTTP 200 替代。未提交、推送、创建 PR 或提前进入下一轮。

### R5 后独立资源错误收集最小修复（离线验证完成）

- 用户明确授权本批最小修复。沿用专用工作树 `codex/meta-planner-controlled-writes-10`，HEAD `436d2453`；开工时 73 个既有变更路径、暂存区为空，远端跟踪分支已领先 16 个提交，本批不进行上游集成或触碰主工作区。
- 范围限定 `server/meta_agent/meta_planner_v2.py`、`server/meta_agent/write_contract.py`、新增 `server/tests/test_meta_planner_independent_resource_issues.py` 与本任务卡。仅将资源引用/目录存在性/逐表逐操作授权的独立诊断与依赖合法 config 的字段/Schema 校验分离，消除端口错误后的提前退出。复用原授权权威，不增加第二套许可规则。
- 首先增加失败回归，要求同一节点的端口与资源错误同时进入 `validation_issues` 和受限阶段诊断；再做小范围顺序调整。`resolve_write_grant` 继续执行原完整授权检查，独立 scope 选择不读取非法配置字段；失败节点仍阻止后续解析/编译，不自动补引用或跳过检查。风险是校验顺序调整意外放宽许可、非法 config 导致新异常，以及遗漏完整写授权校验，均需成对反例覆盖。
- 最小验收：通过既有隔离启动器运行新测试，随后运行全部 Meta Planner、NodeContract、Headless/Authoring、Publish、Evaluator、Evolution、App 与工作流类型相邻回归，执行本批语法、Diff/敏感扫描并核对 R1–R5 账本和相邻生产文件 hash。测试关闭 dotenv、真实凭据和外部网络，并禁止业务记录访问；不导入预览启动器。当前最新全量后端、前端构建及真实验收仍作为独立门禁。
- 禁止修改 Prompt/目标、Adapter/节点授权、Graph Patch 接受语义、输出归一化、三次调用预算、Runtime/Store、前端、依赖和发布协议；不读取凭据、不调用模型、不重启或操作预览器/共享栈，不提交或创建 PR。回退只恢复这两处后端校验组织方式，不改变已有 Proposal/Workflow/数据表格式。
- 已将配置/端口失败后的 `continue` 延后至独立资源检查完成之后。非法配置明确清空局部 `parsed`，仍不进入依赖合法配置的资源解析、字段或端口后续验证。资源缺失、不应携带资源、目录不存在及读/写操作未获授权均可与配置错误同时记录，不修改原错误文本、分类或诊断脱敏策略。
- 从 `resolve_write_grant` 原逻辑提取 `resolve_write_grant_scope`，只依据节点资源 ID、节点操作和既有 grants 选择唯一授权；不消费未经验证的 config。原完整 resolver 委托该函数后继续执行原字段及影响上限校验，合法节点路径没有新增许可。非法 config 时仅执行 scope 部分，避免对字典型 `max_affected_rows` 进行数值比较等新的异常，不将非法字段默认为合法值。
- 新增测试最初为 **21 failed / 7 passed**，其中一项测试夹具误将固定单条 Insert 的 `max_affected_rows` 当作合法配置；依据现有 Schema 改为分别验证该额外字段取 1/2 均拒绝。未修改生产契约。修复前同一组测试明确为 **20 failed / 9 passed**，失败均是独立资源错误未收集或未传给修复；生产改动后 **29 passed / 11.14 秒**。
- 测试覆盖 V2/V3 的端口与资源共现、Update/Delete 缺失谓词、逐操作授权/未知表、非法 config 不调用依赖配置的 resolver、合法节点仍受字段和行数约束、非法 Query/Pure 配置不掩盖资源越权，以及唯一 Patch 同时收到两项错误的完整生成反例。反例中的第二阶段仅使用本地 stub：不显式补表引用时仍失败；显式修好两项时才能继续编译。原 Intent 不被自动改写，调用保持三次，Proposal 仅 pending/r1，未创建 Xpert。
- 命令使用 `C:\tmp\modelmirror-mcp-test-venv\Scripts\python.exe -B .tmp-cw10-preview/run_integer_checks.py server/tests/test_meta_planner_independent_resource_issues.py`；随后不带文件参数执行同一隔离启动器完成 **26 个文件、615 passed / 92.11 秒**，包含上述 29 项。两者退出 0，保留 4 条既有 FastAPI 弃用 warning。测试使用独立短路径 Store，关闭 dotenv/凭据/外部网络；本批专项直接禁止 HTTP 和业务记录操作，不以 stub 成功代替真实 Provider 验收。
- 两个修改的后端文件、新测试及 `server/main.py` 的 `py_compile` 通过，字节码写入独立临时目录。五个 Prompt 方法与四个归一化函数 AST 与 HEAD 一致；Adapter、Graph Patch、Headless、Graph IR、Schema、诊断模块和预览守卫 hash 与本批开始时一致。R1–R5 全部调用账本 hash 未变，R5 仍是原始失败证据，不在历史记录上制造修复后的成功结果。
- 本批未运行最新全量 `server/tests/` 或前端生产构建，也未重启预览器、读取凭据、调用真实模型、写业务数据、批准、发布、提交或创建 PR。当前预览器仍保留 R5 旧结果。修复只解决独立诊断缺失，不自动解决模型的资源绑定遗漏；写节点的模型资源投影差距仍未调整。真实复测须新授权，整轮提交门禁仍未通过。

### R6 真实预览器复测（首请求超时，修复效果未验证）

- 用户再次授权相同范围的真实复测：原零记录合成表、目标及逐操作/字段/单行授权不变，OpenRouter `deepseek/deepseek-v4-flash-0731`，一次生成、最多三次 completion。本批不增加业务写入、工作流执行、审批、发布或提交授权。
- 核验并只重启本任务 `16439` 独立后端，`15439` 前端与共享栈未动。第一次启动直接使用底层 Python，因缺少 `httpx` 在守卫初始化前退出，未创建 R6 账本或发出模型请求；随后用已通过相邻回归的现有 `modelmirror-mcp-test-venv` 启动成功，未安装或升级依赖。唯一启动配置改动是将账本路径从 R5 切换到新 R6 文件；原守卫、目标、预算和历史账本不变。
- 页面核对 Planner/Agent 模型、11 个已选节点、合成表查询与写入范围、三个业务字段和影响上限 1 均与 R5 一致后，仅点击一次“生成候选智能体”。R6 于 **2026-09-09 04:43:31 UTC** 派发第一条非流式任务规划请求，请求正文 9,814 字节；至 **04:48:31 UTC** 被既有 300 秒超时守卫终止。账本为 `generation_state=failed`、`halted=true`、仅一条 `state=uncertain`；未派发第 2/3 次调用，不自动重试。
- 日志堆栈落在 `raw_plan = await self.completion(...)` 内的 `httpx` 响应 `aread` / HTTP11 响应正文接收，外层为 `asyncio.timeout(300)` 的 `TimeoutError`；本地生成接口返回 HTTP 500。只能证明完整 completion 未在时限内交付，不能据此认定 Provider 未收到请求、未计费、已完成生成或某一网络/模型组件是最终根因。没有完整 `finish_reason`、响应状态码或 usage 回执，用量应为未知，不能记作 0 Token。
- 本次未进入计划解析、能力编译或唯一修复，因此既不能证明独立资源诊断修复通过真实验收，也不能把 R6 归类为该修复再次发生编译失败。页面生成按钮已恢复，仍保留 R5 的旧失败候选。运行创建时间后的 Proposal 数为 0；现有 5 个 Proposal 均 pending/r1；合成表查询仍返回 0 条记录。没有运行、记录写入、批准或发布请求。
- 八个相关生产文件及守卫的 hash 与复测前一致，R1–R5 回执逐一核对未变。新安全回执为 `.tmp-cw10-preview/generation-receipt-r6.json`，SHA-256 `e6c74e2d326bb17349b37058fd13b371c755e4d4471a64ad7a37553e750fae09`；日志为 `server-r11`，启动环境失败另存 `server-r10`，均在忽略目录，不进入提交。只补充本任务卡，不改 Prompt、契约、超时、生产代码或历史证据。
- 本次授权已因不确定派发关闭；不使用剩余额度再次生成。后续应先独立定位响应超时，不以延长超时、放宽断言或追加编译补丁替代证据。新的真实调用仍需用户授权；整轮有效生成、写入验收与提交门禁仍未通过。

### R6 后超时最小处理与 R7 授权复测

- 本批只修改生成入口空异常反馈、新增定点测试，以及忽略目录中的预览守卫/测试和本任务卡，共五个文件。保持 300 秒、三次 completion 上限、原始目标/Prompt、严格编译及禁止自动重试。不会修补新的模型输出或扩大写入权限。
- 预览守卫仅记录响应头到达时间、状态码、白名单请求标识的 hash、正文接收字节数与首末片段时间，不记录正文、原始 header 或异常私有消息。生产入口仅为 TimeoutError/空异常提供非空中文错误；HTTP 状态和 Managed 分支不变。
- 先用无外网测试复现正文挂起和空异常，再执行相邻回归、语法与 Diff 检查。之后单独切换新 R7 回执并仅重启本任务后端，用真实页面执行一次已授权生成；不执行写节点、审批、发布或提交。回退只撤销本批错误反馈与观测层，历史回执不改写。
- 开工工作树 HEAD 为 `436d2453`，74 个既有变更路径、暂存为空；当前远端跟踪分支领先 24 个提交，本批不集成上游。全轮门禁仍独立保留。
- 新增守卫测试先得到 2 项缺失阶段字段失败、6 项通过；补充观测后 8 项全部通过。真实 httpx MockTransport 覆盖响应头已到达、部分正文后挂起、原 300 秒参数、取消关闭、重启不重发及完整正文/usage 不变；原始正文、请求标识和 Cookie 不进入回执。模拟仅用于离线机制验证。
- 入口新增 4 项空 TimeoutError/ReadTimeout/空异常/空白异常测试，及 6 项既有 Managed 接口测试，共 10 passed。第一次测试命令在受限环境中未输出测试结果便停止；随后使用现有隔离 venv 的获准环境执行通过，不将停止的命令算通过。测试不启用应用生命周期或访问真实 Provider。
- 默认隔离启动器执行 27 个文件，619 passed / 64.07 秒，4 条既有 FastAPI on_event 弃用 warning；四文件 py_compile 与 git diff --check 通过。最新全量后端和前端构建本批未运行。
- 15439/16439 原服务均已退出，未停止任何其他进程；使用原专用启动器恢复前后端，日志新存 client-r3/server-r12。另一步仅将启动器回执路径切换为新 R7，R1–R6 历史回执未改写。浏览器连接恢复后，通过可见表单恢复原中文目标、两个 DeepSeek 模型、无工具/中间件及合成表选择。
- 勾选可写字段与启用写节点时，浏览器操作审查要求操作时再次确认，已停止且未绕过。R7 账本仍 generation_claimed=false、halted=false、calls=[]，没有发出真实模型请求；不以 619 项绿测宣称真实生成验收。预览页面保留等待用户确认，未执行工作流、审批、发布、提交或创建 PR。

### R7 真实生成：观测守卫写盘故障，未通过

- 用户在操作时明确确认后，选择原合成表的 title/status/severity 与三个写节点。发起前通过可见表单核对原目标、两个 DeepSeek 模型、11 个节点、无其他资源及影响上限 1；只读 API 确认表为零记录。移除表单尾部多余逗号以恢复原目标，服务端第一阶段请求仍为 9,814 字节，checksum 与 R1–R6 完全一致。
- 2026-09-12 09:47:16 UTC 仅点击一次生成。第 1 次 completion 约 14.05 秒，第 2 次约 166.97 秒，均 HTTP 200、完整 body、finish_reason=stop。实际返回 total_tokens 分别为 3,069 与 24,886。第二阶段结果进入唯一修复；这不是 R6 的首请求超时复现，也不能证明候选已通过校验。
- 第 3 次在 09:50:17 UTC 派发，09:50:20 收到 HTTP 200。在 `ObservedBody -> record_chunk -> _save -> temp.replace` 保存正文接收统计时出现 Windows PermissionError / WinError 5；随后异常处理保存 uncertain/halted 也遇到同一替换失败。最终 generation_state=failed，但第 3 条回执停留 dispatched/receiving_body，不能把尚未写入的失败标志当作请求仍正常运行。generation_claimed=true 且 calls 已为 3，原守卫仍拒绝再次生成或追加 dispatch。
- 这是新增预览观测层暴露的本地写盘故障，不能归因于 Planner、模型超时或受控写 Runtime。文件检查未显示只读属性；具体占用者/权限阻断来源尚未确定，不把并发读取或安全软件推测写成事实。现有八项守卫测试没有覆盖 Windows 原子替换失败，以及失败回执再次写盘失败的边界。
- 第三次没有完整 completion/finish_reason/usage，计费状态未知；已知两次合计 27,955 Token 不是本次总用量。未自动重试、延长预算、改写历史回执或修补新模型输出。原始 R7 回执 SHA-256 为 `1e53eaa79709be66474f37bb8044183712dacddadf90878aa414b67840c9fd3e`，保留在忽略目录。
- 页面已退出生成状态并显示错误；现有五个 Proposal 仍为原 pending/r1，最新仍是 R5，未新增 Proposal。合成表依旧零记录；server-r12 请求日志仅一条生成 POST，HTTP 500，无业务写入、审批或发布。第三次不确定派发后本次授权结束；下一步应先独立复现并处理观测守卫的原子写失败，不据此扩大生产代码修复范围。未提交或创建 PR，整轮门禁仍未通过。

### R7 守卫写盘复现与修复：开工范围

- 用户仅授权复现修复，不含新模型调用。本批只允许修改忽略目录的 `cw10_guard.py`、原守卫测试、新 Windows 写盘反例与本任务卡，最多四个文件。禁止修改 server/client 生产代码、Prompt、授权范围、300 秒/三次 completion 限制、启动器回执路径或 R1–R7 历史证据；不重启预览/共享服务，不使用凭据，不执行数据表操作，不提交。
- 开工 HEAD `436d2453`，专用工作树与分支不变，现有暂存为空；远端跟踪分支领先 26 个提交，本批不集成。先保存现有变更文件、守卫、启动器和 R7 回执的 hash，修复后检查未授权路径无变化。
- 先用 Windows 真实文件句柄复现缺少删除共享的 reader 阻止原子替换，并用允许删除共享/关闭句柄作对照；它只证明可触发机制，不证明 R7 的具体占用者。再验证观测写盘故障是否切断模型响应，以及失败保存是否丢失已知 uncertain 状态。
- 修复目标是区分已持久化的派发预算与正文接收观测，避免每个网络片段都同步替换同一 JSON；持久化故障仍须失败关闭，不能跳过派发前回执、隐瞒结果不确定或重发模型。内存中的已知失败状态不得被旧磁盘快照覆盖，失败保存不能掩盖原始异常。
- 验收仅使用本地合成响应/临时文件、无外网测试，覆盖 Windows 句柄、暂时/持续写盘失败、派发前失败零请求、正文完整接收、超时/取消、重启与预算封闭。运行既有守卫测试、专项反例、语法、Diff/敏感扫描；生产文件未变则不以重复全量绿测替代真实模型门禁。回退只撤回预览守卫与测试，不回滚业务记录，不改写历史回执。

### R7 守卫写盘修复：离线结果（2026-09-12）

- **复现通过，R7 具体占用者仍未知。** 修改守卫前，四项定点反例均报错：正文观测写盘直接切断 httpx 响应；派发前写盘失败未形成稳定的内存封闭状态；保存失败状态的 PermissionError 覆盖原始 TimeoutError；完整响应的终态写盘失败无法保留已知结果。另用真实 Win32 只读句柄复现 `Path.replace` 的 WinError 5，关闭同一句柄后替换成功。首次实验中“仅增加 FILE_SHARE_DELETE 即可替换已打开目标”的对照在本机也失败，不能把该假设当作修复；正式反例以打开/关闭目标句柄为控制变量。相关语义参考 [CreateFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew) 和 [MoveFileExW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw)，不据此指认 R7 的具体进程、ACL 或安全软件。
- 只修改忽略目录的 `cw10_guard.py`，新增 `test_guard_persistence.py` 并更新本任务卡；原 `test_guard.py` 未改。响应头和正文片段只更新受锁保护的内存统计，在完成、超时或取消时合并到安全回执，不再在读包回调中执行文件替换。派发前仍必须完成 flush/fsync/原子替换；失败时记录内存 `not_dispatched`，本次及后续请求均阻断。
- 已知状态以进程内快照保留，写盘失败后固定 `halted=true` 和安全错误码 `PREVIEW_RECEIPT_UNAVAILABLE`；后续收尾不能再用旧磁盘内容覆盖 uncertain、usage 或失败状态。异常路径的保存再次失败时保留原始 TimeoutError/CancelledError，不暴露原始文件路径。完整响应未可靠保存时不得返回生成成功；已有持久化 dispatched 标记在重启后转为 uncertain 并封闭预算，残留 pending 或损坏回执直接拒绝启动，不自动恢复额度或重发请求。
- 验收命令为既有 venv 的 `python.exe -B .tmp-cw10-preview/test_guard_persistence.py -v`：**19 passed / 1.264 秒**，其中 8 项继承原回归、11 项新增反例，3 项使用真实 Windows 句柄，未跳过。原 `test_guard.py -v` 又单独得到 **8 passed / 0.466 秒**，不重复计为 27 个独立用例。覆盖文件打开/fsync/替换失败、读包期间持有文件锁、连续两次失败保存、取消、终态不可靠、残留/损坏文件、进程中断快照及预算不可重开。HTTP 响应全部来自本地 stub 或 httpx MockTransport，不是真实 Provider 证据。
- 三个守卫/测试文件的 py_compile 通过，字节码使用独立临时目录；git diff --check、四文件尾随空白与敏感凭据模式扫描均通过（命中 0）。逐一比较开工时的 78 个文件 hash，仅允许修改的守卫发生变化；生产文件、原测试、启动器保持原值。R1–R7 七份历史回执 hash 均与原记录一致，暂存区为空；守卫和测试继续由 .gitignore 排除。本批未运行全量后端或前端构建，整轮旧结果不升级为最新门禁。
- **边界与代价：** 接收中的字节数和时间戳不再实时写入磁盘，磁盘 phase 可能直到终态才更新，不能以它判断请求是否仍正常生成。若进程在响应接收中丢失，只能依据派发前记录标记不确定，不能恢复未落盘的 usage；持续磁盘故障仍阻断，而非绕过持久化继续调用。本批没有网络重试、真实模型调用、凭据读取、预览器/共享服务重启、业务写入、审批、发布、提交或 PR。当前预览进程尚未加载修复，R7 原始失败结果保持不变；后续重启和新真实复测需另行授权，整轮提交门禁仍未通过。

### R8 授权真实复测：执行范围

- 用户在守卫离线修复完成后新授权一次复测。沿用原 OpenRouter `deepseek/deepseek-v4-flash-0731`、零记录合成表、中文目标、11 个节点、title/status/severity 三字段、查询及逐操作写授权和每节点 1 行上限；只生成一次候选，最多三次 completion，不执行写节点、审批或发布，不自动重试。
- 复测前重新运行 19 项守卫离线检查，全部通过（0.905 秒）；保存 79 个源码/守卫/历史回执文件 hash。唯一启动改动为新 R8 回执路径，原 R1–R7 不覆盖。只重启已核验归属的本任务 16439 后端，15439 前端和共享栈保持不动。后端只在内存中使用原已授权 OpenRouter 连接凭据，不输出密钥、不改写 Provider 配置。
- 通过真实预览器表单核验并点击一次生成；读取脱敏阶段诊断和安全回执判断结果，不以 HTTP 200 或守卫测试代替候选有效性。若任何请求结果不确定，立即关闭本次授权并保留未知用量；若编译失败，保留失败候选与诊断，不在本次复测中追加生产代码修复或模型请求。

### R8 真实生成：守卫修复通过实测，候选仍未通过（2026-09-12）

- **真实调用完整，但整轮验收失败。** 10:22:06 至 10:27:49 UTC，通过预览器仅发起一次生成，耗时 342.38 秒。三次 completion 均为 OpenRouter `deepseek/deepseek-v4-flash-0731`，HTTP 200、body_complete、finish_reason=stop，分别耗时 41.10 / 169.76 / 131.32 秒，实际 total_tokens 为 3,473 / 25,248 / 30,013，合计 **58,734**。未出现第四次调用、不确定派发、自动重试或 WinError 5；没有增加原每次 300 秒限制。第一阶段请求仍为 9,814 字节、checksum `d34989cb2c9be89c4daeaf9907c739b743383c3a2a71a6aeacef0881b16aed45`，原测试目标未修改。
- 新增 Proposal `proposal_b72b5b1771534069a9b765aaa7b26382`，pending/r1、validation=false。任务规划通过；首次能力编排停在 intent_parse，18 项 Schema 诊断为六个节点的 resource_ref 各夹带 kind/target_ref，以及六个 resources.kind 不符合绑定资源枚举。唯一 Graph Intent 修复仍停在 intent_parse：九条控制边缺少 source_ref/target_ref，改用不允许的 from/to，加上一个 resources.kind 错误，共 37 项。两次失败均未到达动态资源执行，不能归因于业务写入 Runtime、数据库 revision 或响应截断。
- 页面中的“待修复的智能体候选”来自 `_fallback_candidate`；其 missing_workflow_agent_model / xpert_workflow_agent_missing_modelId 是服务端故意清空首个 Agent 的 modelId 以阻止审批产生的次生诊断，不是用户没有选择模型，也不应通过给占位草稿补模型来绕过失败。实际需要继续审计的是首次生成与唯一修复的资源/控制边协议一致性，本次没有放宽 Schema 或改写模型输出。
- **前端返回链另有未定根因的问题。** 页面在后端完成前已显示 fetch failed，服务端随后仍保存上述失败 Proposal。刷新页面后可看到本次待修复候选，类型化编辑保持锁定。`client/server.mjs` 的代理使用 fetch，并仅把 error.message 返回客户端，没有记录 cause；本次前端日志没有足够信息证明具体超时类别，不能将 5 分钟代理超时写成已证实根因，也未擅自延长超时。该返回问题与模型产物 Schema 错误必须分别诊断。
- R8 回执 generation_state=returned、generation_claimed=true、halted=false、validation_valid=false；returned 仅表示生成服务已返回，不代表候选有效。完整安全回执 SHA-256 为 `161175d99b54b9e4793e0173a844dae0f45e1f68939f743e3b61bfbfab6dcc99`。终态后才读取回执；R1–R7 七份历史 hash 均保持原值。后端另有视觉模型目录请求被 PREVIEW_EGRESS_NOT_APPROVED 拦截的日志，调用栈位于 vision capabilities 的目录刷新，未进入已授权的三次生成调用，不将其算作已发生的额外外发或本次编排失败首因。
- 只读复核合成表 count=0、items=0，原五个 Proposal 仍为 pending/r1、没有 applied_resource_id；唯一新增的失败 Proposal 同样未应用。79 个开工文件 hash 对比只有独立启动器的新回执路径发生变化；server/client 生产源码及守卫本体未改。本次仅更新启动器路径和任务记录，没有执行写节点、审批、发布、提交或创建 PR。最新全量后端、前端构建和提交前上游集成本次未运行，整轮提交门禁仍未达到；三次授权已经用完，后续修复和真实复测须另行授权。

### R8 离线复现与契约核对：开工边界

- 用户同意仅做复现、核对和定位。本批不修改 server/client 生产源码、Prompt、Schema、校验、预算或授权；不调用外部模型、不读取凭据、不重启既有预览/共享服务，不执行业务写入、审批、发布或提交。允许更新本任务卡，并在忽略目录新增至多两个离线复现脚本及其安全结果文件。
- 固定本树 `436d2453`、79 个现有源码/启动器/守卫/R8 回执 hash；远端跟踪分支领先 26 个提交，本批不集成。两条证据链分别验收：Graph Intent 最小反例、生成/修复所见 Schema 与解析器一致性、受限错误摘要覆盖；以及用与预览器相同的 Node 运行时导入原样 `client/server.mjs`，连接独立 loopback 合成慢服务，记录代理错误 cause 和后端完成时间。
- 复现只保存诊断类别、位置、计数、源码 hash、版本与时序；不保存完整 Prompt、原始模型输出、记录正文或密钥。语义错误夹具为从 R8 脱敏诊断构造的合成反例，不冒充原始产物重放。代理测试使用自动分配的独立端口、原默认超时和可关闭的测试进程，不触碰 15439/16439。完成后验证生产文件与历史回执未变，报告事实、推断和未验证边界，不据离线通过宣称真实生成通过。

### R8 离线定位结果（2026-09-12）

- **已复现的失败层：** 合成反例与 R8 持久化诊断逐位置、逐错误类型比对一致，初次 18 项、修复 37 项；原始失败模型正文未持久化，故不声称进行了完整产物重放。只改反例中的结构字段为正确形状后，Schema 解析通过；该对照只证明字段契约，不证明工作流类型、授权、可达性或真实生成有效。
- **排除契约漂移：** 以 R8 原 goal/plan/scope 和仍相同的 Capability hash 重建当前生成与完整修复输入，二者 required_schema 均等于实际 GraphIntentV3 Schema，规范化 SHA-256 为 `347cd76b9f7c621011d77c13ff1116cdf774a58c0bbb8489a4aa88436235e26e`。graph_intent_contract、读资源 guide 和 canonical example 也完全一致。resource_ref 明确只允许 resource_id；控制边明确使用 source_ref/target_ref/outcome_ref；资源绑定枚举没有 data_table，本次 scope 的 resource_binding_kinds 为空。不能通过增加 from/to 别名、容忍额外字段或扩大资源绑定来“修复”这次失败。
- **排除本次错误摘要丢失假设：** 该 18 项合成错误经过真实 `_safe_exception_message` 后为 1,408 字符，18 个位置均保留，最后一项完整；完整修复输入的 validation_issues 原样使用该摘要。此结论限于与 R8 位置/错误类别相同的反例，不能外推为任意大错误集都不会截断。
- **确定事实与尚未证明的诱因：** 当前 Legacy 与 Managed completion 都使用 `response_format={"type":"json_object"}`，GraphIntent Schema 位于模型上下文，不是 Provider 端强制 Schema。非法 GraphIntent 仍是合法 JSON，因此完整 HTTP/JSON 返回不能保证编排正确。模型上下文中 graph_intent_contract 原样重复两份，重复部分约 26,434 UTF-8 字节；重建的生成用户消息共 74,006 字节。canonical example 仅一个 Agent，控制边为空、resource_ref 示例为零；顶层系统规则重点描述 node-owned reads，写节点规则在更深的契约中。重复信息和示例覆盖不足是后续可定点改善的候选因素，尚无因果实验能证明它们导致 R8 失败，不直接归因为“模型能力不足”或“Prompt 一定有错”。
- **代理机制已独立复现：** 使用同一 `C:\Program Files\nodejs\node.exe`，Node v24.18.0 / bundled Undici 7.28.0，直接导入原样 client/server.mjs（SHA-256 `e5ae4eedacb8bf618d4638094dbb0c22a31cc6bd4a2e5e2c87bfe75a62dc9792`）。测试只将监听限制为 loopback，并旁路观察 fetch 错误后原样抛回，不改变超时或响应逻辑。100 毫秒合成响应为 200；342.4 秒合成响应在 304.133 秒触发 TypeError/fetch failed，cause=HeadersTimeoutError / UND_ERR_HEADERS_TIMEOUT，客户端得到 500；合成后端在 342.590 秒才完成，此时下游已断开。它证明前端代理等待响应头的默认约 300 秒时限短于三次串行 completion 的可能总耗时。官方 [Undici 超时说明](https://github.com/nodejs/undici/blob/main/docs/docs/getting-started.md) 亦区分 headersTimeout 与 bodyTimeout，二者默认均为 300 秒。R8 当时未记录 cause，不能伪称已恢复那一请求的底层异常，但原实现、运行时与失败时序相符；该机制独立于模型 Schema 错误，修好代理不会使候选自动有效。
- **复现脚本自身的首次失败与安全边界：** Python 脚本最初因 namespace package 搜索顺序误导入忽略目录的 server.py，触发测试子进程内的既有预览配置初始化，包括读取配对配置和将原连接凭据解析到内存；随后在导入 server.main 时退出，未打印凭据、未发出模型请求。这不符合本批“不读取凭据”的目标，已向用户说明，不能隐瞒为普通绿测。仅修正复现脚本 sys.path，增加审计钩子禁止读取预览启动器、私有 Runtime、配对文件与 .env；正式复现只先做 localhost 安全元数据读取，再阻断网络，生产入口仅使用 AST 读取，未导入 server.main。既有服务进程及 Provider 配置未主动修改，未重开模型预算。
- **命令与结果：** `python.exe -B .tmp-cw10-preview/repro_r8_contract.py` 最终 8 passed / 1.481 秒；覆盖 Schema/guide 一致性、18/37 错误精确复现、合法形状对照、摘要覆盖、示例与重复体积、两路 JSON 输出契约。`node.exe .tmp-cw10-preview/repro_r8_proxy.mjs` 完成快/慢两组原样代理对照，断言通过，合成后台等待约 342.6 秒，零外部模型请求。两个临时端口已经关闭，15439/16439 仍由原 PID 12908/49572 持有；安全结果存于忽略目录，不进入 Runtime 正式 Store。
- **下一最小修改方向，尚未实施：** 将模型侧节点资源引用与 Agent 资源绑定明确分层，生成和完整修复共用精简且包含非空控制边/节点资源示例的权威投影；保持原测试目标、业务 Prompt、Schema 严格拒绝和三次预算，不增加“猜字段”归一化。该方向涉及系统级生成上下文调整，需用户单独确认，不能在本批悄悄改 Prompt。代理作为独立小批次解决生成总时限与逐次模型时限不一致、保留安全错误 cause 和请求终态关联；不得用自动重新生成或扩大每次模型超时代替修复。两条链必须分别验证，不能以页面恢复掩盖无效候选。
- 最终对比 79 个开工 hash 全部不变，含生产源码、预览启动器、守卫及 R8 回执；R8 仍三次调用、原 SHA-256，Proposal pending/r1，合成表 count=0。只新增忽略目录的两份脚本/安全结果并更新任务卡，无业务写入、审批、发布、提交或 PR。全量后端、前端构建和真实模型生成本批未运行，整轮提交门禁仍未达到。

### R8 定点处理：第一小批开工（2026-09-12）

- 用户批准按离线结论处理。本批限定五个文件：`server/meta_agent/meta_planner_v2.py`、既有 `test_meta_planner_v2.py` / `test_meta_planner_table_prompt_contract.py`、新增 `test_meta_planner_prompt_projection.py` 和本任务卡。只统一生成/完整修复/Patch 修复的模型契约位置、消除重复并补充授权内的字段片段；不改原测试目标、业务 Prompt、任务计划、Schema、Adapter、Runtime、权限或调用上限。
- 开工仍为 `436d2453` 专用脏工作树，已固定 82 个现有文件 hash。禁止触碰既有预览服务、共享栈、凭据和历史回执，不调用外部模型，不执行业务写入、审批、发布、提交或 PR。
- 先新增反例验证单份契约、三入口等价、非空控制边合法形状、节点/Agent 资源分层、逐操作授权与字段注入拒绝；随后运行隔离启动器的专项及 Meta Planner 相邻回归。保留原 R8 失败诊断，不将合成测试描述为真实模型成功。回退仅撤回本小批的上下文投影与测试修改。
- 第二小批独立处理生成代理的总等待时限与安全错误回执，不修改单次模型超时、自动重发或其他 API 代理；在第一小批验证完成后单独记录范围与验收。

### R8 第一小批离线结果与第二小批开工

- 新增投影反例在生产修改前 10 项均失败；修改后专项三文件 57 passed / 18.06 秒，相邻 28 文件 629 passed / 90.52 秒，均退出 0，保留 4 条既有 FastAPI 弃用警告。第一次沙箱测试启动无输出，已中止，不计通过；随后使用已检查的独立禁外网启动器运行。Schema、Adapter、Runtime 和三次预算未修改。
- 生成、完整修复、Patch 修复现在只携带一份顶层 graph_intent_contract；字段片段通过真实 Pydantic 类型构造，并按 Adapter 资源类型、目录存在性和逐操作授权筛选。示例明确不是完整任务，不会用单 Agent 示例替代用户要求的查询/写入。原生 Handle、from/to、额外资源字段及 data_table 伪装绑定仍严格拒绝。真实生成改善尚未验证。
- 第二小批限定五个文件：`client/server.mjs`、新增 `client/server-meta-planner-proxy.mjs`、既有 `client/server-headers.node.mjs`、`client/Dockerfile` 与本任务卡。Dockerfile 仅复制新增服务器模块，不改变镜像依赖；本批不运行 Docker、不重启 15439/16439 或共享栈。回退仅撤回该生成路由的专用代理和模块复制项，其他代理保持原逻辑。
- 仅 POST `/api/meta-agent/generate-xpert-candidate` 使用 Node 内置 HTTP(S) 流式代理，避免 fetch 的默认 300 秒响应头时限；总窗口固定 960 秒，覆盖本次预览守卫最多 3 × 300 秒和 60 秒本地处理余量。生产 Legacy 路径原本没有统一 read deadline，Managed 有自己的预算；本改动不宣称所有生产 completion 都有 300 秒限制，也不更改任一模型 deadline。总窗口仍可能耗尽，超时只能报告结果不确定，不得自动重发。
- 验收使用 loopback 合成服务：慢响应跨过原 300 秒时限、快速响应、后端错误透传、总 deadline、下游取消、半截响应、编码/多 Cookie/请求体保留、安全错误关联和单次派发。测试通过既有 `node --test server-headers.node.mjs` 纳入回归；长时对照显式设置测试环境开关，不修改生产时限。参考 Node.js v24 官方 HTTP 的 destroy/timeout 语义与流式背压说明，不引入 Undici 依赖。

### R8 两项定点修复：最终离线验收（2026-09-12）

- **最终模型上下文版本：** 三入口只保留一份权威契约；资源片段使用真实 `kind/resource_ref` 字段，补齐 ref/title 后直接通过节点 Schema，不另造 `node_kind` 别名。节点资源示例与 Agent 资源绑定分别采用原 Pydantic 类型，按目录存在性、节点选择和逐操作授权筛选。合法控制边片段非空；原严格 Schema、授权、业务目标、任务规划 Prompt、模型参数与三次预算保持不变。示例改善不构成真实生成通过证据。
- **代理长时实测通过：** `MODELMIRROR_PROXY_SLOW_TEST=1 node --test server-headers.node.mjs`，11 passed / 0 failed / 0 skipped，总耗时 343.41 秒。实际导入生产 `client/server.mjs`，在独立端口连接合成后端；后端等待 342.4 秒后返回，客户端在约 342.66 秒得到完整 HTTP 200，派发数恰好 1。响应中的合成 `validation=false` 原样保留，不把传输成功解释为候选有效。该对照跨过旧代理已复现的约 304 秒故障点，未调整生产 960 秒总窗口或单次模型超时。
- 其余代理检查覆盖：原 GET 健康接口、精确 POST 路由匹配、请求正文、压缩响应和 Content-Length、多 Cookie、后端 409/422/500 与 307 原样透传、不跟随重定向、总 deadline、持续正文不续期、断开/半截响应、2 MiB 流式正文及一次派发。超时/连接失败生成受限中文错误、随机关联 ID、白名单 cause 和 unknown 结果；不保存 Prompt/请求正文/URL，不自动重发。关闭连接不保证后端任务已取消，因此文案明确要求先核对候选。
- **测试工具自身的两次失败单列：** 初次真实入口测试给 GET 健康请求错误地发送了默认 `{}` 正文，返回 400；改为空正文后请求断言通过，但 Windows 清理钩子先删除仍被子进程作为 cwd 使用的目录，报 EBUSY。只修改测试请求与清理顺序，先退出测试子进程再移除其唯一临时目录，生产代码未因此增加补丁；未正常退出的测试已中止，不计通过。后续快/慢完整专项均退出 0，最终核对没有残留的合成代理子进程。
- **最终源码回归：** 已检查的 `python.exe -u -B .tmp-cw10-preview/run_integer_checks.py`，28 文件 **629 passed / 73.28 秒**，保留 4 条既有 FastAPI 弃用 warning；不将中间 629 项重复计数。前端 `npm exec -- vitest run --configLoader runner --maxWorkers=1 --fileParallelism=false` 加四个显式文件（metaAuthoring、DataTableWriteGrants、XpertEvaluationsPage、EvaluationWriteCases），**40 passed / 20.33 秒**。`npm run build` 退出 0，保留大 bundle warning；Python 四文件语法与 Node 模块语法检查通过。
- **范围复核：** 对比开工 82 个 hash，只有原先列入清单的 `meta_planner_v2.py`、两份既有后端测试、`client/server.mjs` 和任务卡变化；新增两份生产/测试模块及原先干净的 Node 测试和 Dockerfile 均在授权两小批范围内。Schema、Adapter、Runtime、预览启动器、守卫和 R8 回执 hash 未变。九文件凭据模式扫描 0 命中，Diff 检查通过，暂存为空；构建产物继续忽略。15439/16439 仍分别由原 PID 12908/49572 持有，没有重启既有预览或共享栈。
- **仍未验证：** 本批没有真实 Provider 调用、业务写入、审批、发布、提交或 PR；原 R8 失败候选和三次调用回执不变。最新全量 `server/tests/`、全部前端单元测试、Docker/Node 22 镜像验收及真实预览生成未运行，帮助中心与上游集成门禁仍待收尾。当前常驻预览未加载本次服务端修复；须另行授权加载并执行真实复测，不能沿用已耗尽的 R8 额度。整轮 PR 门禁仍未达到。

### R9 授权复测：执行范围（2026-09-12）

- 用户在 R8 两项定点修复完成后重新授权复测。保持原 OpenRouter `deepseek/deepseek-v4-flash-0731`、中文目标、11 个节点、唯一合成表、查询及 insert/update/delete 授权、title/status/severity 三字段和每节点 1 行上限；仅在真实预览器点击一次生成，最多三次 completion。不得执行工作流或写节点，不批准、发布、自动重试或追加生产修复。
- 复测前固定 90 个现有源码、启动器、守卫与历史回执 hash。19 项守卫测试全部通过（1.112 秒）；R8 已终态且 hash 未变，合成表 count=0，六个既有 Proposal 均 pending/r1、未应用。只有本任务卡与独立启动器回执路径允许修改；新 R9 回执及日志使用未占用路径，不覆盖 R1–R8。
- 已核验 15439/16439 仍是本任务原 Node/Python 进程。只重启这两个独立服务以加载上下文投影与专用代理；后端沿用原获准的 OpenRouter 连接凭据，仅在内存使用，不更改 Provider 配置或接触共享栈。恢复同一表单选择后核对再派发，按真实终态分别判断传输链与候选有效性；无法可靠确认调用结果时立即停止并保留未知用量。

### R9 真实生成：输出预算截断，未通过（2026-09-12）

- **一次生成，两次实际调用，未新增候选。** 11:56:32 至 11:57:22 UTC，使用原 OpenRouter `deepseek/deepseek-v4-flash-0731`，总耗时 49.519 秒。任务规划调用 32.728 秒，HTTP 200、body_complete、finish_reason=stop，实际 3,910 Token；能力编排调用 16.663 秒，HTTP 200、完整响应体但 finish_reason=length，completion_tokens=8,192、total_tokens=22,641。已知实际总量 **26,551 Token**，没有不确定派发，不把完整 HTTP 响应误称为完整 Graph Intent。
- 原目标 checksum `cd08899368e0da5c95d4ca5bfd53c24f622204fc39844027724dfd2cbb17ec51` 与历史一致；第一阶段请求仍为 9,814 字节、checksum `d34989cb2c9be89c4daeaf9907c739b743383c3a2a71a6aeacef0881b16aed45`。浏览器输入过程带入的一个尾部字符在提交前通过回读发现并移除，不改变原任务。第二阶段请求 58,341 字节，checksum `0b87dff49cf76dc05289738a59b8fb7fe1f113088943a1f4e1c30e2ab53d1387`；它加载了上批上下文投影，但本次截断结果不足以验证 R8 的资源/控制边 Schema 问题已解决。
- **直接阻断原因已证实，长度增长诱因未证实。** 当前能力编排入口传入 `8_192` 输出上限，真实回执到达同一 completion_tokens 并报告 length。原守卫立即设置 halted=true；生成服务随后进入唯一完整修复调用路径，但在外发前被 PREVIEW_APPROVED_BUDGET_EXHAUSTED 拒绝。因此实际没有第三次 completion，也没有额外生成、自动重试或代码修补。未保存原始模型正文或隐藏推理，不能推断截断前字段是否合法、具体哪段内容耗尽预算，亦不能将输入上下文长度等同于输出预算。
- **代理返回正常，长时真实边界本次未覆盖。** 新代理回执 request_id=`3ebef0e2-2c5a-447a-8b59-5897b2252afd`，state=completed、elapsed_ms=49,753、upstream_status=500、cause=null；浏览器退出生成态并显示后端 PREVIEW_APPROVED_BUDGET_EXHAUSTED，未出现 fetch failed。本次不足 300 秒，不替代上批 342.66 秒的合成慢响应对照，更不能称为长时真实生成通过。页面仍是 R8 的旧失败候选，不能当作 R9 新产物。
- 终态只读核对六个原 Proposal 的 ID/status/revision/applied_resource_id 全部不变，均 pending/r1、未应用；合成表 count=0、items=0。本次后端访问日志只有一条生成 POST，返回 500，无工作流运行、数据库写入、审批或发布。视觉目录刷新仍有 PREVIEW_EGRESS_NOT_APPROVED，调用栈在 image catalog 的外网请求前阻断，不属于上述两次获准 completion，也不是本次截断首因。
- 新 R9 安全回执 SHA-256 为 `bbb5a14f66d64537925f60856f5437bcce6806b21fe4f6faab1adbe2f83b271e`，保存在忽略目录；日志为 server-r14/client-r4。授权已因 length 关闭，不以未使用的第三次名额继续请求。下一步应先离线核对输出预算与 finish_reason 在生成/修复之间的传递及错误归因，再决定最小处理；不直接提高上限、放宽断言或再加字段补丁。本次未提交、推送或创建 PR，整轮门禁仍未达到。
- 收尾比较 90 个开工 hash，只有本任务卡和启动器回执路径变化；server/client 生产代码、守卫、R1–R8 回执均未变。启动器 py_compile、git diff --check 和两文件敏感凭据模式扫描通过（0 命中），暂存为空，新回执和日志继续被 Git 忽略。独立预览器保持运行供查看失败状态；本批没有运行全量后端、前端构建或 Docker，不把上批结果升级为本次全轮提交门禁。

### R9 完成状态最小修复：离线验证（2026-09-12）

- **根因与范围。** 当前 Planner completion 回调只接收文本，Legacy 未订阅或校验 finish_reason；预览护栏在 length 后封闭预算，但将响应交给解析器，后续修复才被二次预算错误阻断。Managed 也只检查 JSON 正文，可能把 length 加合法 JSON 记为 passed。本批先新增六个真实适配路径的合成反例，修改前全部失败，分别复现次生预算错误、空正文错误、截断 JSON 错误及错误接受。此结论不解释 R9 的 8,192 Token 究竟消耗在可见输出还是推理上。
- **仅三个生产文件。** 新增 `server/meta_agent/completion_contract.py` 作为 Planner 专用校验，`main.py` 仅增加显式启用的响应观察接口和候选入口接线，`managed_gateway.py` 复用同一校验。非 stop、缺失/未知结束状态或 Provider 错误均在 JSON 提取、Graph 编译和唯一修复之前停止；length 返回中文截断错误及 `meta_planner_output_truncated`，不创建失败占位 Proposal。通用聊天的默认解析行为不变，不调整 Prompt、响应格式、4,096/8,192 上限、三次 completion 总限制、Schema、Adapter、Runtime 或预览护栏。
- **安全证据。** 完成回执只包含调用序号、白名单结束状态、请求上限、正文字符数、错误标志及 prompt/completion/total/reasoning Token 数值；未知或非法用量保持 null，不从正文长度估算推理用量。不保存正文、隐藏推理、Provider 原始错误、任意 finish_reason 文本或密钥。候选失败 API 和 Run metadata 保留首因及回执，正常候选写入既有完成 checkpoint；Managed 截断调用记为 failed 并保留已知用量，不记为 passed 或 uncertain。未增加 Store 或持久化协议。
- **测试。** 新增 `test_meta_planner_completion_contract.py` 的 54 项反例/对照，覆盖任务规划、能力编排、唯一修复三个阶段；合法/截断/空 JSON；length、缺失状态、过滤及未知状态；Provider 错误夹带；数值用量脱敏；零额外派发、零 Proposal，以及非 Planner 聊天兼容。既有 Managed 正常响应夹具补充真实完成契约中的 finish_reason=stop，缺失状态另有拒绝用例，未放宽断言。测试只使用合成 HTTP 响应和隔离临时 Runtime，不作为真实 Provider 验收。
- **最终回归。** 已审阅的 `python.exe -u -B .tmp-cw10-preview/run_integer_checks.py` 显式选择全部 `test_meta_planner*.py` 及 Meta Agent/Managed/Agency、NodeContract、Authoring、Publish、Evaluator、Evolution、App、Typed Workflow：32 文件 **723 passed / 103.68 秒**。另选 `test_xpert_runtime_chat.py`、`test_native_router_chat.py`、`test_chat_file_output.py`、`test_multimodal_chat_foundation.py`、`test_rag_managed_generation.py`：5 文件 **88 passed / 25.82 秒**。合计 811 项，不重复计入中间六项和 80 项专项。保留既有 FastAPI on_event 弃用 warning。`npm.cmd run build` 退出 0，仅既有 bundle 体积 warning；三个生产文件及两个测试文件 py_compile 通过，临时字节码已移除。首次沙箱测试启动无输出，已中止后使用上述隔离启动器重跑，不计通过。
- **回退与剩余边界。** 回退只撤销本批 Planner 完成校验和接线，已有 Proposal/Workflow/数据库不迁移、不回滚。当前预览器未重启加载，R9 及历史回执不改写，调用授权保持关闭。本批未调用真实模型、读取预览凭据、执行验收表写入、审批、发布、提交、推送或创建 PR，也未操作共享栈。最新全量 `server/tests/`、上游集成和真实预览器复测仍未完成；不能据本次离线通过认定生成成功率已解决或整轮 PR 门禁通过。

### R10 授权复测：执行范围（2026-09-12）

- 用户在完成状态最小修复后重新授权一次复测。仍使用 OpenRouter `deepseek/deepseek-v4-flash-0731`、原中文目标、11 个节点、唯一零记录合成表，以及 title/status/severity 三字段的 insert/update/delete 授权和每节点 1 行上限。只在真实预览器点击一次生成，最多三次 completion；不执行写节点、不审批、不发布，异常后不自动重试。
- 开工复核 95 个源码、启动器、守卫及历史回执 hash，全部与上批收尾一致；19 项离线守卫测试通过（1.272 秒）。六个旧 Proposal 均 pending/r1、未应用，合成表 count=0。R9 已失败终态且保持原 hash；R10 回执与新日志路径未占用，不覆盖历史证据。
- 仅重启属于本任务的 16439 后端加载完成状态校验，15439 前端保持不变。沿用已经批准的 OpenRouter 连接，仅在后端内存使用凭据；不改 Provider 配置、不接触共享栈。本次仅允许任务卡和独立启动器的回执路径变化，不追加生产修复或调整 Prompt、输出上限、Schema 和调用预算。

### R10 真实生成：正常完成但控制流不合格（2026-09-12）

- **真实结果仍未通过。** 在原预览器仅点击一次生成，13:00:20 至 13:02:41 UTC，总耗时 140.889 秒；实际三次 OpenRouter `deepseek/deepseek-v4-flash-0731` completion 均 HTTP 200、body_complete、finish_reason=stop。任务规划 79.473 秒 / 3,676 Token，能力编排 58.983 秒 / 19,345 Token，唯一 Graph Patch 修复 2.105 秒 / 22,816 Token；合计 **45,837 Token**，没有第四次请求或不确定派发。生成接口和代理 HTTP 200 只表示已返回，不能替代 validation=false 的失败结论。
- **完成状态修复已加载并产生真实安全回执。** Runtime run `a73cf4e4-57e1-4b60-b922-ceeca13c47e7` 的完成 checkpoint 保存三条回执，上限仍为 4,096/8,192/8,192，正文字符数为 3,170/16,377/251；Provider 本次上报 reasoning_tokens 均为 0，不反推 R9 用量分布。此次未出现 length，因此验证了正常完成路径及诊断接线，不声称真实覆盖了截断拒绝分支；该分支仍以此前合成反例为证据。目标及第一阶段请求 checksum 与历史完全一致。
- **明确的首因及证据边界。** 新 Proposal `proposal_a2e36bf18baf40dc9d1d6ca841784e2f` 为 pending/r1、未应用、validation=false。首次编译与唯一修复均在 control_flow 检查产生相同两项 fingerprint：`query_after_insert`、`query_after_update` 必须分别完整且唯一地连接 success/error outcome，但实际图不满足。Patch 正规化前后均两项操作、checksum 不变，并确实重新编译，仍未消除这两项问题。现有安全诊断未保存失败 Intent 的边正文，不能仅凭错误文案断言具体是缺失、重复还是错误 outcome；下一步应离线核对语义 outcome 推导、生成契约投影与 Patch 修复输入，不放宽控制流断言或猜测追加连接。
- **占位提示不是新首因。** 页面显示“待修复的智能体候选”，其中 missing_workflow_agent_model 来自 `_fallback_candidate` 主动清空首个 Agent 的 modelId 以阻止审批；不是本次模型忘选或 Provider 模型目录失效。真实修复失败才触发该占位路径，不能把占位图的 Agent-only 结构认作模型已正确生成 CRUD 工作流。
- **传输及状态核对。** 代理 request_id=`0f33badc-2298-4956-94fd-42fe3e343fb3`、state=completed、elapsed_ms=141,095、upstream_status=200、cause=null。新后端 server-r15 只有一次生成 POST，无业务写入、工作流执行、审批或发布；六个原 Proposal 的 ID/status/revision/applied_resource_id 均不变，唯一新增为上述失败 Proposal；合成表 count=0。首次重载命令未完成，核验仍为旧 PID 后才以已验证 PID 停止并启动新后端；模型调用始于新后端健康且新回执 calls=0 之后。后端新 PID 47376，前端 PID 50648 未重启。
- **证据保留及剩余门禁。** 新 R10 回执 SHA-256 为 `84f751922cdbaf1c21ff6f9afd22d1d8a96f667390029ff09393301c371272a0`；R9 及历史回执不改写。三次额度已使用，本次授权不能继续生成。未追加生产代码、修改 Prompt/Schema/预算、在工具或诊断输出中暴露凭据、操作共享栈、提交、推送或创建 PR；当前预览保留失败状态供查看。全量后端、上游集成、真实合格生成及整轮提交门禁仍未通过。
- **本次范围检查。** 95 个开工 hash 中仅本任务卡和独立启动器回执路径变化，生产源码、守卫及历史回执均不变；git diff --check 通过、暂存为空、两文件凭据模式扫描 0 命中。启动器首次经默认编码 stdin 进行语法检查时遇到 UnicodeEncodeError；显式 UTF-8 后同一文件检查通过，不因验证工具编码问题修改源码。新回执及日志保持 Git 忽略，不将历史 811 项测试或前端构建算作本次重新执行。

### R10 后系统核对与一致性修复（2026-09-12）

- 用户明确要求停止逐错误付费重试，全面、精确地核对后再实测。本批冻结 96 个开工 hash，与 R10 收尾一致；不读取凭据、不新增 completion、不操作预览器/共享栈或业务表，不批准、发布或提交。详细分层证据、修复范围和实测前门禁见 [系统核对报告](../audits/META_PLANNER_CONTROLLED_WRITES_GENERATION_AUDIT.md)。
- 发现模型契约仅凭 control-only 端口存在与否声明出口，造成 condition/multi_route/terminate 以及 read stop 模式与权威语义不一致。R10 原始失败边未保存，不断言该矛盾是其唯一诱因；正确 stop/error_output 完整图及正确显式 Patch 在修改前即能编译，Runtime 和严格校验不是应被放宽的对象。
- 本次只改三个生产文件：control_flow.py 共用出口事实及配置相关投影，meta_planner_v2.py 在三入口使用该投影并给修复提供实际控制边/诊断，generation_diagnostics.py 保存有界安全计数和独立控制图 checksum。未增加归一化补丁、自动连边、重试、节点能力、写权限或模型预算；保持原目标、Schema 和审批边界。
- 新测试修改前 10 failed / 2 passed。当前相邻 30 文件 704 passed / 138.44 秒，写入及隔离执行另 10 文件 163 passed / 53.06 秒，合计 867 项定向通过；包含 366 组判定等价对照、正确/错误唯一 Patch、私有字段脱敏及非法配置日志上界。全量、构建和最终范围复核在收尾后补记，不能以定向通过宣称真实验收。
- Sol 仅基于给定事实完成不使用工具的独立攻击用例侧审，未操作文件、工作树、浏览器或模型；Astra 负责实际复现、修复和集成。当前预览保持 R10 失败状态，未加载本批源码。
- 前端生产构建、五文件纯语法检查及 Diff 检查通过。全量尝试约 5% 时已有失败并主动中止，无残留该测试进程；改为首失败即停后，94 passed / 1 failed（43.20 秒），首项是未修改的外部 Worker 管道断开测试，`ConnectionResetError` 覆盖预期 `EngineUnavailableError`。该模块不依赖本批三个 Planner 文件，但本批未复跑纯基线全量，不把其他未分类失败称为基线绿测。具体记录及安全 XML 位置见审计报告，整轮全量/真实验收门禁仍未通过。
- 七文件常见凭据模式扫描 0 命中，暂存为空；96 个开工 hash 的变化均限于本批范围，原启动器、守卫及 R1-R10 回执未变。新增/改动仅三个生产文件、两个测试和两份文档，未混入运行数据或构建产物。既有四个 Patch 归一化函数未修改，不增加自动修图分支；预览仍运行原后端 PID 47376 / 前端 PID 50648，未加载新源码或重新开放额度。

### R11 授权真实复测：未通过，未进入 Resolved IR（2026-09-12）

- 用户在系统核对修复后授权一次真实预览器复测。开工核验 99 个源码、文档、启动器、守卫及历史回执 hash，全部与上批收尾一致；19 项调用护栏测试通过（0.981 秒）。原中文目标、OpenRouter `deepseek/deepseek-v4-flash-0731`、11 类授权节点、唯一合成表、title/status/severity 字段、三种写授权及每节点 1 行上限均未修改。只生成候选，不运行写节点、批准或发布。
- 仅将忽略目录启动器切换到新 R11 回执。首次重启命令未正常完成，核验旧后端仍在后，才停止已确认归属的旧进程并启动新后端：PID 21104、父进程 43132，日志 server-r16；前端 PID 50648 未变。提交前确认后端 health=ok、Capability V10/22 类、新回执 generation_claimed=false/calls=[]。未操作共享栈，原授权 OpenRouter 连接仅由后端在内存使用，未改 Provider 配置或访问其他凭据。
- **本次真实结果失败。** 14:10:14 至 14:13:15 UTC，在原预览器点击一次生成，耗时 181.214 秒。三次 completion 均 HTTP 200、body_complete、finish_reason=stop，分别耗时 10.992 / 15.212 / 154.855 秒；实际 total_tokens 为 2,845 / 4,513 / 18,983，合计 **26,341**。请求上限分别为 4,096 / 4,096 / 8,192，Provider 上报 reasoning_tokens 均为 0，无截断、第四次调用、不确定派发或自动重试。第一阶段仍为 9,814 字节、checksum `d34989cb2c9be89c4daeaf9907c739b743383c3a2a71a6aeacef0881b16aed45`，原目标 checksum 不变。
- **阶段定位。** 首次 task_plan 因缺少必填 `tasks` 而失败；第二次 task_plan_v1 修复通过，已消耗唯一修复机会。第三次能力编排通过 Intent 解析，但停在 authorization/Adapter 校验：安全诊断为 node_config 8 项、type_ports 25 项。可确认问题包括 Insert 配置夹带 max_affected_rows/writable_fields、Update 配置夹带 writable_fields、Update/Delete 缺少 filter、三处 Query 动态谓词与输入不一致、Agent 模板变量缺少显式绑定、Agent 输出非 string，以及多个输入引用未知变量。未生成第二次 Graph Patch 修复，不通过扩大预算弥补模型输出。
- **不得误判控制流修复证据。** checks_executed 仅为 intent_parse/authorization，但 authorization 内部也执行控制流分析；不能据此称完全未进入控制流检查。该次未进入 Resolved IR、编译和发布预检。新诊断已保存控制图 checksum `10f2cbdda742449f1e632aa20b92ca9ea519bbfc66f7b693914e978a91329f8f`；control_contract_issues=[] 只代表本地出口计数未报告问题，不代表完整控制流或数据可达性通过。此次没有进入 Graph Patch 分支，不能声称上一批唯一修复上下文已被真实验证，也不能据此认定同一控制流错误重现。原始失败模型正文仍不持久化，后续只能基于受限诊断和独立离线对照审计，不猜测未保存的具体边。
- 新 Proposal `proposal_60a354aa571d481482bc09eabe88de07` 为 pending/r1、validation=false、未应用；Runtime run `8cf69840-ae7c-46c4-9ef9-5afd84b8b58b` 的 completed 仅表示生成接口已返回失败候选，不是验收成功。页面保持“待修复的智能体候选”和只读锁定状态。占位图的 missing_workflow_agent_model 仍为服务端阻止批准产生的次生诊断，不是此次用户选型错误。
- **状态与授权边界。** 七个旧 Proposal 的 ID/status/revision/applied_resource_id 完全不变，合成表复测前后均为零记录；新后端日志仅一条生成 POST，无业务写入、工作流执行、审批或发布。R11 原始回执 SHA-256 为 `cee849d79eb9b96abddc0bc9b916914f57b036fb7c9d120f169b315e71213d74`。本次额度已结束，未追加生产修复或修改 Prompt/Schema/Guard，未提交、推送或创建 PR。下一步仍需离线核对任务计划及 Adapter/数据绑定的完整生成契约，不以再付费碰运气或自动补齐非法模型字段替代修复；整轮提交门禁保持未通过。

- **范围复核。** 99 个开工 hash 中仅独立启动器回执路径和本任务卡变化，生产源码、守卫及 R1-R10 历史回执未变；git diff --check 通过，暂存为空，两文件常见凭据模式扫描 0 命中。新回执和日志仍在忽略目录；未重跑全量后端或前端构建，不以历史离线测试替代此次失败的真实生成门禁。

### R11 后生成契约收口：开工边界（2026-09-12）

- 用户批准按只读核对结论处理，不授权新的真实模型调用。首次任务规划和能力编排请求已离线逐字节重建，SHA-256 与 R11 回执一致；不能归因于旧进程或说明未发送。修复后的任务计划包含 Update 返回新 revision、Agent 提取记录身份供后续使用的错误前提；粗粒度任务摘要未表达真实返回与可信来源边界。
- 更正 R11 阶段表述：`authorization` 内部也执行控制流分析，不能从 `checks_executed` 没有单列 control_flow 推断完全未运行。R11 未进入后续 Resolved IR、编译与发布预检，且没有 Graph Patch 调用；原始非法 Intent 未保留，不能重放其全部具体绑定。
- 本次保持 `436d2453` 的独立工作树，冻结 R11 收尾的 100 个文件 hash。禁止改变 Runtime、NodeContract 许可、公开 Graph IR/TaskPlan、旧 Proposal 读取、写权限、模型/Token/修复预算；不操作预览器、共享栈、凭据、业务表、审批、发布或 Git 远端。
- 分三个可验证小批：一是私有生成契约与反例测试（任务规划/修复共用受限 Schema，Adapter 派生按 kind 的 Graph/Patch Schema 和真实表返回摘要）；二是有界安全数据绑定诊断与攻击测试；三是相邻回归、语法、构建、敏感信息扫描及文档收尾。每批只处理同一目标，已有全量失败单独保留，不混入修复。
- 主要允许路径为 `server/meta_agent/` 的生成投影、被动诊断及相关测试；不改节点执行和数据存储。先记录新增反例失败再实现，使用已有离线隔离启动器验证。回退只撤销本批模型侧契约与诊断变更，不改写历史回执或持久化业务状态。真实生成是否改善须另行授权验证。
- 首微批涉及五个源码/测试文件，另同步本任务卡：两处既有测试必须同时改为检验新的私有投影及旧公共 Schema 不变，否则会把计划内私有 Schema 收口误报为公共协议回归。这六个文件保持同一个契约目标，不纳入额外功能。

### R11 后生成契约收口：本次收尾（2026-09-12）

- 私有任务 Schema、Graph/Patch 按 Adapter 分支的 Schema、真实返回摘要与安全绑定诊断已完成；公共协议、授权、Runtime、模型及三次调用预算未修改。新增投影不等于 Provider 约束解码，仍由现有语义/资源/发布门禁拒绝非法图，不自动修正记录身份或连边。
- 新契约反例先取得 9 failed / 4 passed，诊断新增四项先全部失败，再实现修复。最终 31 个 Planner/相邻文件 732 passed，14 个写入/隔离/视觉文件 231 passed，两组互不重叠，共 **963 passed**。实现中遇到的空 allOf、测试局部变量错误已修复并重跑。
- 14 文件组首次三项 PDF 失败定位为原离线脚本在 Windows spawn 子进程重入 pytest；新增忽略的主入口包装，原外网/存储护栏不变，重跑同一组通过。PDF 产品实现、时限、断言、预览启动器和调用守卫没有调整。
- 全量后端以修正后的隔离入口重新尝试，**94 passed / 1 failed**，首失败停止。仍为未改动的 `test_agent_upstream_port.py::test_started_worker_crash_after_model_request_is_never_restarted`，Worker 断连后取消帧触发 `ConnectionResetError`。XML 95 tests / 1 failure 保留在忽略目录；不将剩余未执行测试称为通过，也不将该独立问题混入本批。
- `npm.cmd run build` 通过，保留大 bundle warning；8 个改动 Python 文件及一个本地测试入口的纯语法编译通过。11 个本批拟交付文件的常见凭据模式扫描 0 命中，`git diff --check` 通过、暂存为空。100 个开工 hash 中仅九个允许路径变化，另新增两个源码/测试文件；预览与历史调用证据保持原 hash。
- 已更新 [系统核对报告](../audits/META_PLANNER_CONTROLLED_WRITES_GENERATION_AUDIT.md) 和 MetaAgent 文档，详列模型 Schema 能表达与不能替代的校验、声明型诊断边界、测试启动问题、回退和剩余门禁。常驻预览未重启加载；本次外部模型调用、业务写入、审批、发布、提交、推送与 PR 均为零。
- **仍未达到整轮 PR 门禁。** 真实成功生成、完整写操作验收、最新上游集成及全量独立失败继续保留。新的真实调用须重新明确授权，不能沿用 R11 额度；本轮不提前进入第 8 轮。

### R12 授权真实复测：未通过，生成契约仍未稳定（2026-09-12）

- 用户在 R11 后生成契约收口完成后授权一次真实预览器复测。103 个冻结文件 hash 全部一致，调用护栏测试 `python.exe -B -m unittest discover -s .tmp-cw10-preview -p 'test_guard*.py' -q` 为 **27 passed / 1.414 秒**。原中文目标、OpenRouter `deepseek/deepseek-v4-flash-0731`、11 类节点、唯一零记录合成表、title/status/severity 字段、insert/update/delete 授权及每节点 1 行上限不变；只生成候选，不执行写节点、审批或发布。
- 独立启动器只将新回执改为 `generation-receipt-r12.json`，不重置旧回执。经进程和端口归属核验，仅重启本轮 16439 后端：新 PID 56816、父进程 43148，日志 server-r17；15439 前端 PID 50648 不变。启动后 health=ok、Capability V10/22 类、snapshot hash `f5b1b74eec0e7f532db31855c38b96253d196f698c0cdcfeb84f0f161d16c0c9`，新回执尚未 claimed、calls=0。原连接凭据仅由已授权后端解析到内存，未读取或输出凭据文件内容，未修改 Provider 配置。
- 首次点击被电脑操作安全审查阻止，原因是输入节点的历史标题为“触发器”。核对真实 Capability 和前端绑定后确认其 kind 为编译器管理的 `input`，不是延期的 Trigger 能力，22 类集合没有其他 trigger kind；当时回执 calls=0、generation_claimed=false。凭已核实的契约事实再次使用同一可见按钮提交，未更改标题、授权或守卫，也未改用直接 API 绕过。新后端日志仅一条生成 POST。
- **真实结果仍失败。** 15:55:18 至 15:59:24 UTC，耗时 **245.666 秒**，三次 completion 均 HTTP 200、body_complete、finish_reason=stop；耗时分别为 107.081 / 3.319 / 135.060 秒。Provider 上报 total_tokens 为 4,752 / 5,278 / 20,821，合计 **30,851**。无截断、第四次调用、结果不确定或自动重试；三次额度已耗尽。目标 checksum 仍为 `cd08899368e0da5c95d4ca5bfd53c24f622204fc39844027724dfd2cbb17ec51`。任务规划和编排请求分别为 18,170 / 66,331 字节，说明本次与旧 R11 请求不同，但体积变化本身不是质量改善证据。
- **确定的失败阶段。** 首次 task_plan 的安全诊断为两个 missing（含 tasks）和两个 extra_forbidden（含 type）；另两个字段名经过脱敏，不能猜测原始内容。第二次 task_plan_v1 修复通过，计划只包含一个最终汇总任务，消耗唯一修复机会。第三次 capability_compile 通过 Intent 解析后在 authorization/Adapter 校验失败：4 个 Query/Update/Delete filter 不符合对象契约，5 个 Serialize 节点不满足唯一 json 输出契约，Update/Delete 两个输入把上游 object 声明为 string。报告为 11 条验证消息，安全诊断因 Union 分支分别记录而为 node_config 13 项、type_ports 2 项；二者计数口径不同，不得混算。
- 新诊断保留 4 条 INPUT_CONTRACT_CONFIG_INVALID 和 12 条受限数据绑定摘要，未发生截断；两处类型错配均有唯一来源、唯一输出且 variable_matches_source=true，故这两处不是“未知变量”，而是声明型类型冲突。filter 原始值、非法输出端口原文和完整失败 Intent 未持久化，不能断言具体错误值或声称原产物重放。control_contract_issues 为空也不代表完整控制流通过；此轮仍未进入真实 Resolved IR、编译或发布预检，未调用 Graph Patch 修复。失败占位图的 missing_workflow_agent_model 是阻止批准的次生错误，不是用户模型配置丢失。
- 新 Proposal `proposal_9bd40683916c473397a38d8f0fec3609` 为 pending/r1、validation=false、未应用；Runtime run 为 `a29e5d45-1ecb-4a6a-a507-95762526da19`。预览页面保持“待修复的智能体候选”和只读锁定状态。8 个旧 Proposal 的 ID/status/revision/applied_resource_id 完全不变，合成表前后均为零记录。新回执 SHA-256 为 `0855b39b0beb21995f1a333b54c3e3f2e5a6c01a7df40afe59358139117505a3`。
- **结论与边界。** 上批私有 Schema 和安全诊断已经加载，但没有使这次生成通过，不能以 HTTP 成功、错误数减少或离线测试代替验收；也不能据此将根因归为业务表 Backend、revision 冲突或模型已无能力。仅凭安全诊断尚不能确定模型反复偏离契约的最终原因，本次不继续修生产代码或付费试错。全量独立失败、上游集成、真实写效果验收及帮助中心门禁继续保留；未提交、推送或创建 PR，未进入第 8 轮。
- **范围收尾。** 103 个冻结 hash 中仅本任务卡及忽略目录启动器的回执路径变化，生产源码、调用守卫与历史回执均未变；两文件常见凭据模式扫描 0 命中，启动器纯语法、git diff --check 通过，暂存为空。新日志与回执仍在 Git 忽略目录。本次未重跑全量后端或前端构建，不将上一批离线结果算作本次新证据。

### R12 后审计收敛与针对性验证（2026-09-12）

- 用户授权本地针对性验证和形成收口方案，不授权再次外发。冻结 89 个既有待提交文件及四个本地入口/回执共 93 个 hash；本批只有新增测试及审计文档、两份文档入口/状态同步，不修改生产代码、预算、Prompt、模型或权限。
- 新增 `test_meta_planner_generation_boundary_audit.py`：39 项合成证伪，分别经过实际 Legacy collector 和 Managed collector；逐项配对真实发出的局部 Schema、Mock Provider 字段与实际 authorization 输入。合法完整 CRUD 图通过，非法 Filter/Serialize 端口保留原形且被拒绝，未发现这些样例被中途转换坏。
- `records` 声明为 string 的反例通过模型侧 Schema，但被 object 来源与 string 输入的语义检查拒绝。确认该局部 Schema 投影未充分表达静态类型，不把它宣布为 R12 全部根因；R12 原始字段仍无法重放。任务修复耗尽后的 Graph 失败与唯一显式 Graph Patch 成功分别验证，最多三次 Mock completion，未额外调用。
- 联合 13 文件 **344 passed / 0 failed / 0 error / 0 skipped，81.41 秒**，包含上述 39 项，不重复累计；覆盖整数兼容、资源诊断、Headless、真实本地 Runner 隔离 CRUD、事务/revision、恢复/取消与效果伪造。四个既有 FastAPI 弃用 warning 保留。证据在忽略目录 `causal-audit-integrated.xml`，不是实际 Provider 或业务表验收。
- 实验自身问题单列：首次受限进程启动未进入测试后中止，原隔离入口在授权执行环境重跑；新增测试初稿两项正例误夹带 Graph Patch 禁止的 Schema，改用显式 disconnect/connect，并保留原非法输入为反例。没有改产品校验以换取绿测。
- 推荐收口方向为：先完成安全同请求配对和已复现的局部生成投影缺口；再由用户单独批准一次有判别力的完整目标实测；生成及写效果验收后才进入整轮提交门禁。生产实施方案尚待用户确认，不提前多 Agent/V4，也不追加猜测式自动修复。
- 未操作预览器/共享栈、未读取凭据或业务数据、未调用模型、未批准或发布，未提交/推送/PR。本次不重跑全量后端或前端构建，旧全量失败、上游交点、真实写效果及帮助中心门禁继续保留。
- 范围复核：93 个冻结 hash 中只有两份允许的历史文档变化，新增路径只有本次测试和审计；生产源码、预览启动器与历史回执不变。AST 语法、Diff/新文件空白检查通过，常见敏感模式 0 命中，暂存为空；没有提交 SQLite、Runtime、JUnit、日志或构建产物。

### 审计后有限收口：开工契约（2026-09-12）

- 用户确认实施审计报告的有限方案。本次从同一独立工作树继续，开工冻结 91 个既有待提交路径及四个本地测试入口/启动器/回执共 95 个 hash；不使用脏主工作区，不合并上游、不提交。
- 微批一：私有生成 Schema 从现有 NodeContract 补全 `records` 的静态类型投影，配对合法/非法类型测试；不改变公开 Graph IR、Adapter、写权限或 Runtime。
- 微批二：增加有界、默认不保存正文的内存字段观察器与纯单元证伪，覆盖字段配对、隐私、截断、异常隔离和并发隔离。
- 微批三：接入实际请求、Provider content 及校验输入三个位置，复用现有 Proposal 报告及内存运行记录；全程不在网络接收路径新增文件写入。不增加调用、重试、Schema 强制解码或新业务 Store，不保存隐藏推理。
- 最小验收使用 `.tmp-cw10-preview/run_generation_contract_checks.py` 的合成环境，先取反例失败，再修复并重跑；相邻回归覆盖两条 completion 路径、生成/Patch、授权、Headless、事务与隔离效果，另运行相关语法、生产构建、Diff/敏感检查。既有全量独立失败继续单列。
- 禁止真实模型调用、预览器/共享栈、凭据及业务表访问、批准和发布。离线通过后再由用户单独授权实测；回退只撤销本批私有投影与观察器调用，不回退整轮数据能力或历史回执。

### 审计后有限收口：离线结果（2026-09-12）

- `records` 从 NodeContract 投影合法声明类型。新增 37 项与权威兼容判定配对的反例和对照，先取得 20 项失败，再修复并通过；未改公开 IR、Adapter、Runtime、Filter/Serialize 或写授权。
- `generation_evidence` 提供请求局部、最多三次调用的 Schema/Provider content/collector/validator 安全配对。未知名称只保留 hash；截断/缺失/重复分别降低证据等级，不读取隐藏推理或记录 Prompt/业务值。没有诊断文件 I/O，不增加模型调用；观察和汇总异常不遮蔽原结果，Store 异常仍按原规则失败。
- 新增单元 17 项、集成 10 项；同请求合法/非法对照在观察 on/off/fault 三组中完整请求及非取证报告一致。测试中发现并修复新观察器的摘要汇总异常隔离缺口；四项比较差异另定位为复用的手动赋值 fixture 被既有授权 validator 排序，改独立输入副本，不改生产逻辑。
- 独立只读复核发现并由四项反例复现观察器的嵌套字面量白名单碰撞、第 4 次调用错挂问题；只修 Filter 脱敏范围和 last_call 归属，取证测试共 31 passed。复核确认两处静态闭合，不将它们描述为 R12 根因。
- 当前 43 个相关后端文件最终 970 passed / 163.17 秒，四个既有弃用 warning；前端四文件 40 passed / 11.83 秒；`npm.cmd run build` 退出 0，仅既有体积 warning。详情、测试分组与最终 JUnit hash 见审计附录，不累计重复运行以放大通过数。
- Proposal 报告可持久化摘要；Proposal 创建前失败的 API/RunRegistry 摘要仅在当前进程可用，实测必须在重启前导出安全响应。没有新增持久化 Store，也不将内存 checkpoint 描述为可恢复证据。
- 本批未真实外发、启动/操作预览器或共享栈、读凭据/业务表、审批/发布、合并上游、提交/推送/PR。真实生成须新授权，实际写入另行授权；全量独立失败和整轮其他门禁不因本批通过而消失。
- 收尾：九文件 AST 语法、Diff/新文件空白检查通过，常见敏感模式 0 命中；95 个冻结 hash 只有8个允许路径变化，新增四文件均为本批观察器/测试。暂存为空，未纳入 Runtime 数据、JUnit、日志或构建产物，预览入口与历史回执未改。

### 有限收口后的 R13 真实复测（2026-09-12）

- 用户重新授权一次原完整 CRUD 目标的真实预览器生成，仍限定 OpenRouter 的 `deepseek/deepseek-v4-flash-0731`、最多三次 completion；不执行写节点、不审批或发布。原目标 checksum 为 `cd08899368e0da5c95d4ca5bfd53c24f622204fc39844027724dfd2cbb17ec51`，未缩小目标、替换模型或修改 Prompt。
- 开工固定 95 个待提交路径和五个忽略目录入口/回执，共 100 个 hash；历史 R12 回执及 970 项回归 JUnit hash 与上批记录一致。原预览器已停止，启动本任务前端 15439（PID 29972）、后端 16439（PID 26164、父进程 53504），不操作共享栈。只将启动器回执切到新的 `generation-receipt-r13.json`，预算守卫未改。
- 外发前预算/回执守卫 27 passed；安全观察和失败导出 31 passed / 37.02 秒。第一次受限环境检查未进入测试且没有创建隔离临时目录，确认进程归属后结束；相同检查以所需临时目录权限重跑通过。不能将被结束的检查计为通过。Capability V10/22 类、snapshot hash 与 R12 一致，合成表零记录，九个历史提案均 pending/r1/未应用；新回执尚未 claimed。
- 通过可见页面恢复原目标、两个 DeepSeek 模型选择、11 类节点、唯一合成表、三个字段及三种操作，每节点上限 1 行；清空工具、中间件及其余资源。只点击一次“生成候选智能体”。真实调用于本地时间 21:09:44 至 21:11:20 完成，约 96.33 秒；三次均 HTTP 200、finish_reason=stop，总 Token 分别为 5,156 / 23,569 / 26,649，合计 **55,374**。未自动重试、无第四次调用或不确定派发。
- **仍未通过验收。** task_plan 首次通过；capability_compile 通过模型侧 JSON Schema 和 Intent 解析，在四个节点的资源语义检查失败：`query_after_insert`、`update_ticket`、`query_after_update`、`delete_ticket`。随后唯一 Graph Patch 修复在 patch_parse 阶段失败，记录 60 个 Schema 诊断，不是 60 次调用。add_node 缺少 title，夹带 input_sources/output_ports/required_output_ports/control_outcomes/final_source；另外有未知操作标签。未进入修复应用或重编译。
- 三次实际请求的 Schema checksum 都与预期契约一致，Provider 公开 JSON 与 collector 的完整规范化 body checksum 逐次相同，故本次没有公开 JSON 在 collector 中被改写的证据。首次 Graph 的 validator 完整 body hash 不同，但受观察字段的 structure hash 相同；不能据此声称整个转换行为已被重放证明。
- **证据缺口必须保留。** 总摘要达到 65,536 ASCII JSON 字符上限，现有保护省略全部 structure 明细并标记 incomplete；三个阶段及 hash/schema_valid 仍保存，但无法逐字段恢复四项初次资源错误。修复响应中的额外字段与 existing_nodes 只读投影同名，是协议形状混用的线索，不是已证明的模型内部成因。不再追加付费调用，先离线验证有界取证与 Patch 读写投影边界；具体修改需另行确认。
- 新 Proposal `proposal_59d8de24c40f43e185ee3d152fc7486c` 为 pending/r1、validation=false、未应用；Run 为 `224e72b7-1159-45bd-b1ee-3179ba16a1c4`。Run 的 completed 只表示生成请求结束。失败占位图的 missing_workflow_agent_model 来自回退候选，不代表用户模型配置丢失，更不是首次失败根因。页面保持只读锁定。
- 安全摘要已从完成 checkpoint 和 Proposal 导出至忽略目录 `r13-safe-result.json`、`r13-safe-diagnostics.json`；原始 Prompt、业务记录或隐藏推理不在导出文件内。R13 回执 SHA-256 为 `56a5d1d8a2a7448128dd306db24c7bd2aa1df64c5be2132b8cce70f7223405a5`，结果摘要为 `beab2669fb15dad387b0e3540d42eac2560a456bdf6cbe413ad31faa50d33683`。
- 九个历史提案 ID/status/revision/applied_resource_id 全部不变，表仍为零记录，运行登记只有本次 meta_planner。生产源码、测试和调用守卫未改；本次只改两份证据文档及忽略目录的预览回执路径、增加安全证据文件。未集成上游、重跑全量或构建、执行工作流、提交或创建 PR；整轮其余门禁继续保留。

### R13 后手术刀式修复：开工契约（2026-09-12）

- 用户授权按本次取证结论进行局部修复及离线验证；不再外发模型请求，不启动或操作预览器、共享栈、业务表，不集成上游、提交或发布。
- 微批一仅修改有界观察器、其测试及本任务卡：先复现报告超限丢失全部结构和 Patch 操作不可区分，再以去重和受限操作摘要修复，保持 65,536 字符上限与无正文策略。
- 微批二仅修改 Adapter、结构化诊断、生成服务、针对性测试及本任务卡：统一动态谓词输入要求与权威资源校验，保留可定位且无业务值的首因；不猜测 R13 已丢失的原始 Filter。
- 微批三仅修改修复提示的协议投影、针对性测试及文档：区分只读图状态与可写 Patch 操作，继续使用原严格 Schema，不增加自动语义改写、修复次数或模型预算。
- 验收顺序为反例先失败、局部通过、相邻模块回归、语法及前端生产构建、安全与冻结范围复核。真实生成、完整全量、上游交点及整轮业务写效果门禁仍单列，不用离线成功替代。
- 回退只撤销本批观察、诊断及模型输入投影，不改变写入授权、事务、Runtime、公开 IR/Patch 和历史回执。

- 微批一已取得 3 个预期失败反例，修复后观察器与生成管线集成测试 34 passed。大报告先去重，超限才逐项降级，新增 Patch 操作类型/字段名脱敏摘要；不会恢复 R13 已丢失正文，也不宣称模型问题已解决。独立只读复核进行中。
- 微批二先取得 6 个预期失败；引入资源错误码、节点/谓词/输入位置和类型 checksum，动态谓词的模型投影与资源校验使用同一函数。初次相邻回归为 77 passed、1 项旧英文错误文案断言失败；下一微批同步为稳定错误码，拒绝条件不变。
- 微批三的读写分离只调整模型修复输入：原图只读、命令字段从既有 Pydantic Schema 推导、取消重复的 existing_nodes 投影，并投影已授权资源的精确谓词输入类型。不会把 records 变成标量或代模型修改 Filter。
- 独立复核已完成：观察器三个边界反例和未授权资源 Schema 解析反例均先失败后通过；“原图字面值来自业务 Store”经真实调用链核对撤回，不新增猜测式替换。详细改动、反例、测试编写问题和未关闭门禁见 `docs/audits/META_PLANNER_CONTROLLED_WRITES_CLOSEOUT_AUDIT.md` 的 R13 后修复段。
- 最终 47 文件联合回归 **1,017 passed / 0 failed / 0 error / 0 skipped，228.07 秒**；前端四文件 30 passed，生产构建通过，八文件 AST/全树 Diff/常见凭据模式检查通过。四个既有弃用 warning 与大包体积 warning 保留。开工 100 个冻结 hash 仅本批允许文件变化，新增两个测试，暂存为空；没有新增真实调用或操作预览器，不把此结果作为 PR 提交门禁。

### R14 授权复测：开工契约（2026-09-12）

- 用户重新授权一次原完整 CRUD 目标的真实预览器生成；仅 OpenRouter 的 `deepseek/deepseek-v4-flash-0731`，最多三次 completion。目标、合成表、11 类节点、三个业务字段、三种操作及每节点 1 行上限保持不变；不运行工作流、不写业务记录、不审批或发布。
- 冻结 97 个待提交路径及五个忽略目录入口/回执，共 102 个 hash。初次受限端口检查退出 1，不能作为未监听证据；授权只读复核确认 15439/16439 仍由 R13 进程持有，重复启动保护已在启动前拒绝。仅重启本任务后端，不操作共享栈。保留 R13 回执及全部历史数据，启动器改用全新的 R14 回执。
- 允许修改仅本任务卡、对应审计记录和忽略目录中的回执路径/安全验收证据；不再改生产源码、测试、Prompt、模型、预算或权限。先验证原调用守卫和完成摘要，再从可见页面只点击一次生成。
- 以新回执、候选真实 validation、结构化诊断及生成前后表/提案状态判断结果；请求结束不代表验收通过。失败后保留证据，不自动重试或继续修改。历史全量失败、上游集成、实际写效果及帮助中心门禁仍单列。

### R14 真实复测结果（2026-09-12）

- 外发前调用守卫 27 passed，四文件针对性检查 58 passed，保留四个既有 FastAPI 弃用 warning；未重跑全量或构建。后端新 PID 56636、父进程 61280，明确启动自本工作树且晚于四个修复文件修改时间；前端 PID 29972 保持不变。Capability V10/22 类及 snapshot hash、表 Schema v1/checksum 与 R13 相同。
- 通过可见页面保持原目标与全部授权，只点击一次生成。22:55:34 至 22:56:08 共 34.749 秒；三次均 HTTP 200、finish_reason=stop，实际 Token 为 4,927 / 22,890 / 24,066，合计 **51,883**。没有第四次请求、不确定派发或自动重试。
- **验收仍失败。** task_plan 首次通过；初次 Graph 通过私有 Schema、Intent 解析和资源授权，在 resolve 阻断。唯一 Graph Patch 通过解析、归一化、应用及重新解析/授权，仍在 resolve 阻断，不是 R13 的资源授权或 Patch 形状解析失败。
- 本次模型修复仅提交一条 `connect_data`，指向已存在的 `query_after_insert.result -> update_ticket.records`，按既有重复边无操作规则从 1 条归一化为 0 条；不是未知操作。最终错误明确为 `Node summarize_agent input port task rejects its value type.`。同请求安全结构显示末尾 `variable_aggregator.result` 的 object 直接进入汇总 Agent 的 task，而当前权威 task 端口为 string。这里只确认实际阻断点，不把它断言为全部潜在错误或未经复现的系统性根因。
- 有界取证本次保留三份去重结构，紧凑 ASCII JSON 为 28,621 字符，omitted_calls=0，各结构 details_omitted=false，无 incomplete。三次请求 Schema 与 intended 一致，Provider/collector 的完整规范化 body checksum 逐次一致；初次 Graph validator 的完整 body hash 不同但被观察结构相同，继续保留非完整重放边界。
- 新提案 `proposal_e7b6210fdd1c4326a8b604e79bdecf0f` 为 pending/r1、validation=false、未应用；Run `1ca48044-0a26-454d-8f78-e7718972f55d` 的 completed 仅表示生成结束。失败占位图 missing_workflow_agent_model 仍为次生错误，不是用户选型丢失。页面已核验显示候选未通过并保持只读。
- 十个历史提案的 ID/status/revision/applied_resource_id 完全不变；合成表仍为零记录、Schema/草稿 revision 不变；运行登记只有本次 meta_planner，无工作流执行、业务写入或审批/发布。
- 忽略目录保留 `r14-preflight.json`、`r14-safe-result.json`、`r14-after.json` 和新回执，不保存完整 Prompt、记录或隐藏推理。回执 SHA-256：`1e9f05e75e5d0bb7febfe1c0e2ef43581d91b11239abf095c39d3caddb6b4965`；安全结果：`a811e40022769f590c78475769174016a7dc430b93c7afaa3903121d741943b9`。
- 本次停止继续外发和修改。后续先离线核对对象消费契约与单次修复覆盖，任何生产修复及真实复测需另行确认；不以本次错误阶段后移或离线绿测替代验收，不提前 V4。本轮提交门禁仍未达到。
- 收尾核对：102 个冻结 hash 仅两份证据文档和忽略目录启动器回执路径变化，生产/测试/调用守卫及 R13 回执不变；暂存为空。Diff 空白检查通过，六份本批文档/安全证据常见凭据模式扫描零命中；新回执、JUnit 和导出文件均确认被 Git 忽略。四文件离线 JUnit 为 58 passed / 0 failed / 0 error / 0 skipped，28.281 秒。

### R14 后输入契约最小修复（2026-09-13）

- 用户授权本次局部处理和离线验证。继续使用本任务独立工作树，HEAD 为 `436d24535c6f0ad80b3a4f18830fb834f90a7b95`；开工时 97 个待提交路径、暂存为空，不接触脏主工作区。
- 代码微批限定五个文件：`generation_contract.py`、`graph_ir_v3.py`、`meta_planner_v2.py`、新增 `test_meta_planner_input_contract_alignment.py` 及旧 `test_meta_planner_generation_boundary_audit.py`。文档微批只更新本任务卡和收口审计，不调整 Runtime、写权限、公开 IR/Patch、Capability 数量、调用预算或修复后类型归一化时点。
- 三个处理点：从现有 Adapter/NodeContract 投影静态输入声明类型；权威 Resolver 汇集独立输入类型错误并记录节点/输入位置；唯一 Patch 修复按具体数据边提示显式序列化，不再只处理 task 端口恰好一个输入的情况。Agent task 仍为 string，既有合法文本输入不得删除，空 Patch 不能消除 object 到 Agent 的错连。
- V3 的完整类型校验不再经过有损 V2 类型名比较；旧 V2 仍保留原检查。新生成失败报告使用中文消息和既有 `DATA_TYPE_MISMATCH` 码，Resolver 的首条异常文本兼容旧调用方。授权、资源、配置、未知来源、循环等前置错误仍可先行阻断，不宣称能一次报告所有潜在错误。
- 首批反例为 13 failed / 3 passed；修复后曾有三项纯节点测试被“目标要求查表但图未查表”门禁正确拒绝，只修正合成测试目标，未改产品门禁。补齐兼容、静态端口、伪造来源及脱敏反例后，29 项针对性测试通过。
- 首次 48 文件联合回归为 1,036 passed / 4 failed。四项失败均为旧边界测试依赖英文错误句式或 `authorization` 阶段，现改为核验权威 `resolve`、稳定错误码、具体输入位置及未进入编译；非法候选、调用上限和零业务写入断言保留。原失败文件与新测试重跑为 68 passed；同范围最终联合复跑 **1,040 passed / 0 failed / 0 error / 0 skipped，306.12 秒**，保留四个既有 FastAPI 弃用 warning。
- 合成组合保留 R14 观察到的 Query 联合类型声明、写入 records 的非空 object 声明和 Agent 多输入 object 错连；第一次修复可同时收到三个独立类型问题。没有原始 Filter/Prompt/完整响应，不能将该组合描述为 R14 完整重放，也不能据此还原初次真实 resolve 的首条错误。
- 本次不调用模型，不操作或重启预览器/共享栈，不读取凭据或实际业务记录，不批准、发布、提交或创建 PR。R14 历史回执及安全结果 hash 不变；离线成功不等于已通过真实生成或业务写效果验收。
- 五文件 AST 语法、全树 Diff/本批新增文件空白检查及七文件常见凭据模式扫描通过。生产构建通过，产物写入忽略的独立验收目录，不覆盖正在使用的 `client/dist`；保留既有大包体积 warning 和独立 outDir 不自动清空提示。最终 JUnit SHA-256 为 `3b327643d43b7fc2dee901832b7cbcbdbfbf250b7268cc1091166604593dbd12`；JUnit、构建产物均被 Git 忽略，暂存为空。本次未重跑全量 `server/tests/`，未集成上游，未运行前端单测或真实 UI 验收。
- 回退仅撤销本次输入投影、结构化输入诊断与修复提示连接及对应测试；不得回退整轮能力、旧回执或已发生的业务写入。真实复测、最新全量、上游兼容集成、帮助中心和整轮验收继续单列。

### R15 授权复测：开工契约（2026-09-13）

- 用户重新授权一次原完整 CRUD 目标的真实预览器生成；仅使用 OpenRouter `deepseek/deepseek-v4-flash-0731`，最多三次 completion。保持原目标、唯一零记录合成表、11 类节点、三个字段、三种写操作及每节点 1 行授权上限；只生成 pending Proposal，不运行写节点、不审批或发布。
- 开工冻结 98 个待提交路径及五个忽略目录入口/证据，共 103 个 hash，暂存为空。只允许两份证据文档、忽略目录启动器的回执路径及安全证据变化；不再调整生产代码、测试、Prompt、模型、调用守卫或授权范围。
- 受限端口检查返回拒绝访问，不能视为未监听；授权只读复核确认前端 15439/PID 29972、后端 16439/PID 56636，后者明确启动于本工作树且早于最新修复。仅重载本任务后端并使用全新 R15 回执，不操作共享栈，保留 R14 回执和全部历史数据。
- 先核验调用守卫、最新源码及表/提案状态，再通过可见页面点击一次生成。以实际校验、分阶段诊断和安全回执判定，不以请求 completed 代表成功；失败后不自动追加调用或修改。

### R15 真实复测结果（2026-09-13）

- **本次真实候选生成通过。** 使用原目标和原授权，从可见页面只点击一次“生成候选智能体”。00:52:37 至 00:53:00，共 23.525 秒；三次 completion 均为 OpenRouter `deepseek/deepseek-v4-flash-0731`、HTTP 200、finish_reason=stop，实际 Token 为 4,873 / 22,267 / 24,239，合计 **51,379**。没有自动重试、第四次调用或不确定派发。
- 新后端 PID 13276、父进程 13668，启动于本工作树且晚于三份修复源码；前端 PID 29972 未变。外发前守卫测试 27 passed、2 subtests passed；受限环境下无输出的第一次检查已核实进程后结束，不计为通过。Capability 仍为 V10/22 类，snapshot hash 与 R14 一致。
- task_plan 首次通过。初次 Graph 在 resolve 一次报告 `update_status`、`delete_ticket` 的两项 `DATA_TYPE_MISMATCH`；R14 的 object 直接进入 Agent task 错连本次未出现。唯一 Patch 含两次 disconnect_data 和两次 connect_data，四项操作均保留，随后通过原有 output_normalization、Intent、授权、resolve、compile 和 publish_preflight。没有额外修复机会或本次生产改动。
- 新提案 `proposal_a1e5d910353342da9fb5c402311171a6`，标题“元智能体规划：受控写入验收工单测试工作流”，为 pending/r1、未应用，实际 Proposal validation 与报告两项门禁均 valid=true、零 issues，IR 为 current。Run 为 `201b9196-2522-4861-bbc9-b02256bf9dd4`。画布与无头编辑入口已在可见页面核验，不仅依赖请求 completed。
- 候选共 15 个节点，包含三个真实写节点、两个 Query、六个 JSON Serialize、一个变量打包、一个汇总 Agent 及输入/输出。插入、查询、更新、重新查询、删除具有明确控制顺序；Update/Delete 的 records 直接来自对应 Query，Schema v1、每节点影响上限 1 行未扩大。插入/更新字面值与固定合成目标一致；汇总 Agent task 实际引用序列化结果，最终来源仍为 Agent。此处只核验候选配置，未执行这些节点。
- 十一个历史提案的 ID/status/revision/applied_resource_id 完全不变；表仍为零记录、draft revision=4、Schema v1/checksum 不变；运行登记仅有本次 meta_planner。没有业务写入、审批、发布、模型追加、提交或 PR。
- 取证保留三份去重安全结构，紧凑 ASCII JSON 为 27,113 字符，omitted_calls=0，各结构 details_omitted=false。三次请求 Schema 与预期一致，Provider/collector 完整规范化 body checksum 均相同；初次 Graph 的 validator body 不同但受观察结构相同，仍不宣称完整响应重放或因果排他证明。
- 忽略目录 `r15-preflight.json`、`r15-safe-result.json` 与回执保存安全状态、类型和 checksum，不保存完整 Prompt、业务记录或隐藏推理。回执 SHA-256：`dd90f08e3f024ee9a638288e79a38d2f6ca32c22849b52e5cc5913cb7da51c35`；安全结果：`cb32116bdac69b00c6638ff6ee45390c070e087da5cd9070cd9f89a852bd16e2`。
- 结论仅为“原完整 CRUD 目标在一次生成、一次修复预算内通过候选门禁”。不等于多案例稳定性、真实写效果或整轮 PR 验收；六次序列化的简洁性和既有英文 Schema 刷新 warning 也不据此宣称已优化。本次不再修改实现或继续外发。
- 收尾核对 103 个冻结 hash，仅两份证据文档与忽略目录启动器的回执路径变化，生产/测试/调用守卫及 R14 证据不变；暂存为空。Diff 空白检查通过，五份本批文档/证据的常见凭据模式扫描零命中，四份新回执/安全导出/JUnit 确认被 Git 忽略。守卫 JUnit 包含 27 项测试及 2 个子测试，共 29 条，零失败/错误/跳过；本次没有重跑最新全量或前端构建。

### R15 后提交门禁加严：写入效果与非线性泛化（2026-09-13）

- 用户明确否定“单例通过即稳定”。R15 只证明原串行 CRUD 候选在三次 completion 内通过；保留此前连续失败、修复轮数和费用证据，不重置统计，不宣称泛化或工程稳定性已验证。
- 本批仅记录加严门禁并核对上游/现有验收入口，允许修改本任务卡及收口审计；不修改生产实现、生成 Prompt、调用守卫、权限或运行数据，不新增节点，不提前进入第 8 轮或 V4。
- 已刷新 `origin/main`，当前为 `d246527d5554b16594d390172cfa38876f998ccb`；本工作树 `436d2453` 落后 33 个提交，上游涉及 296 个文件，与本轮 98 个待提交路径的直接交点为 `server/main.py` 和 `server/xpert_runtime/execution_store.py`。这是交点检查，不是已经完成集成。

| 门禁 | 固定验收内容 | 通过标准 | 当前状态 |
| --- | --- | --- | --- |
| 上游集成与冻结 | 保留执行快照原子写入、备份检查、损坏/不可用 fail-closed；将本轮私有写日志纳入加载及 16 MiB 校验，保留旧快照兼容；主入口保留双方受影响行为 | 同基线兼容、恢复及交点回归通过，记录源码/配置/资源指纹，不覆盖整文件或无关变更 | 未运行 |
| 最新完整验证 | 集成后的重点证伪、全量 `server/tests/`、语法、前端类型/单测/生产构建、Diff 及敏感信息扫描 | 全部保留实际结果，基线失败、环境阻塞和未运行项单列；旧 1,040 项相邻绿测不代替全量或真实验收 | 未运行 |
| R15 真实写效果 | 固定 `proposal_a1e5d910353342da9fb5c402311171a6/r1`；Evaluator 内真实隔离 SQLite，空初态和一个非目标合成哨兵两种初态；不手改候选规避问题 | Insert/Update/Delete 各一次，字段前后值、Query 最新 revision、非目标记录未变、最终记录集合和部分完成收据一致；真实业务表不变；不能仅凭回答或最终空表判定 | 未运行 |
| 非线性泛化实测 | 前三项通过后只新增一个不同侧重点的真实生成目标，重点覆盖读取、类型化条件、互斥写/不写与安全终止，不再重复串行 CRUD 链 | 候选通过同一契约及原三次调用预算；逐路径运行同时验证 `workflow_path_match` 与 `workflow_effect_match`，不执行的分支必须零写入，非目标数据不变 | 未运行 |

- 最终提交证据必须绑定同一集成后版本。若先对旧基线运行写入验收，只能作为中间证据；涉及运行/持久化的集成改动后必须复核受影响边界，不混用基线。保持 R15 原提案与历史回执，不自动批准、发布或执行活表写入。
- 泛化目标拟采用合成质检场景：查固定批次；无记录时安全终止且不新建；有记录但数值低于阈值时只更新授权状态；达到阈值时保持记录不变，互斥分支由 Agent 汇总。使用新增合成 Schema 的字符串与整数维度，三种初始化覆盖写入、不写和缺失分支；不得引入循环、等待、跨表事务、表达式引擎或自动补偿。具体字段、目标全文、断言和调用预算在前三项通过后冻结，不临时迎合模型输出。
- 多余节点、虚假分支、未消费的读取结果、只在 Prompt 描述写入以及路径/效果断言失败均不能算泛化通过。失败先保留本次证据并离线定位，不自动换模型、改目标、放宽门禁或追加调用。
- 真实写入验收的下游 Agent 请求、泛化生成与路径运行分别需要明确的外发内容、模型和调用上限授权，不复用 R15 已消耗的额度。当前未发新模型请求、未写测试或业务记录、未重建预览/共享栈、未集成或提交。帮助中心及人工最终验收要求仍保留；一个新增泛化案例通过也不等于统计稳定性。

### 上游交点与剩余门禁

#### 加严门禁执行：集成与离线验证开工（2026-09-13）

- 用户授权开始收口。先完成上游集成及离线验证；真实 Provider、预览器实际写入、审批、发布和 PR 仍需各自门禁与授权，不复用 R15 额度。
- 为不更动原预览器代码及其 98 个未提交路径，新建本轮专用集成工作树 `C:\tmp\modelmirror-meta-planner-controlled-writes-10-integration`，基于冻结 `origin/main`，使用 `codex/meta-planner-controlled-writes-10-integration` 分支；原工作树、历史回执和 Runtime 数据保持原位。只复制 Git 明确列出的本轮源码/测试/文档差异，不复制凭据、Runtime、上传数据或构建产物；保存文件 hash 和原始差异作核对。
- 微批一为机械三方集成，不扩张功能；其多文件范围来自已授权整轮差异重放，实际生产交点仅 `server/main.py` 与 `server/xpert_runtime/execution_store.py`，不得以整文件覆盖处理交点。微批二限定执行快照私有日志校验、对应证伪测试与本任务卡，默认不超过五个文件。微批三仅运行离线门禁和记录结果；若出现新缺陷先按失败边界另定微批，不边跑边叠加猜测性修复。
- 集成必须保留上游所有语义；私有日志需在首次冻结、持久化、加载与离线检查使用同一对象/条数/16 MiB 约束，旧快照缺省空日志。保存失败回滚内存并转 unavailable；账本对重复写入保持最终权威。
- 最小验收为 Store 恢复、Controlled Write Runtime/Evaluator 及 API 启动隔离测试；随后执行全量 `server/tests/`、后端语法、前端 typecheck/test/build、Diff 和敏感信息扫描。测试进程使用新临时 Store、无凭据环境和外网拒绝，不导入真实预览启动器。基线失败和平台限制单列，禁止删断言、跳过失败或伪装真实 Provider。
- 回退保留原工作树和全部备份；集成失败仅在本轮新工作树中逐项修复，不重置原分支、不删除其他工作树、不自动撤销业务记录，不创建临时提交或推送。

- R15 生成结束时的旧远端跟踪分支为 `a61f04da`，当时落后 14 个提交；2026-09-13 加严门禁检查刷新至 `d246527d`、落后 33 个提交。本次已在新集成树重放原 98 个路径并处理两个交点，原分支保持未变；没有重置或创建临时提交。
- PR #366（`8429aa1f`）强化 `WorkflowExecutionStore` 的可信恢复，与本轮新增私有日志位于同一文件。提交前同步必须完整保留上游原子快照、前一有效备份及 unavailable 失败关闭语义，不能用本树旧版整个文件覆盖。
- 同步后将私有日志对象、条数和 16 MiB 限制纳入上游加载/检查校验，并复跑恢复检查、失败写盘、私有正文脱敏与受控写入恢复。旧快照缺省空日志兼容；备份可能早于最近一次冻结，最终防重复仍依赖 Backend operation ledger，不能宣称文件日志单独保证 exactly-once。
- 早期源码的 Linux 后端全量验证已通过；前端时效性基线失败与独立环境跳过项单列。R15 原目标真实生成现已通过候选校验，历史失败保留，不替代真实写效果验收。最新全量门禁、上游兼容集成、独立预览器整轮人工验收、同基线帮助文章/截图/重放仍未完成。本轮尚未达到提交门禁，未提前进入第 8 轮。

回退：关闭新 Planner/Evaluator 入口，保留 V2 读取与执行兼容；不自动回滚业务记录。

### 加严收口：上游集成与离线证据（2026-09-13）

- 集成树为 `C:\tmp\modelmirror-meta-planner-controlled-writes-10-integration`，分支 `codex/meta-planner-controlled-writes-10-integration`，HEAD 为 `d246527d5554b16594d390172cfa38876f998ccb`。原树及 R15 预览器、提案、凭据和回执均保持原位；只重放 Git 明确列出的源码、测试和文档，备份原 98 路径及 hash。
- 三方应用只有 `server/main.py` 一处文本冲突；保留上游 `WorkflowExecutionStore.available` 阻断和本轮写执行预检。其余直接交点 `execution_store.py` 机械合并后，经反例证明新私有日志尚未进入上游加载/检查门禁，因此仅补统一对象、条数及 16 MiB 校验，不改上游恢复策略。
- 私有日志反例先取得 26 failed / 5 passed；修复后 Store/Runtime/Evaluator 五文件 134 passed / 1 skipped。新增备份早于冻结而 Backend 已提交的两种恢复反例后，存储边界单文件 33 passed；原请求返回账本回执，变更请求拒绝，不产生重复插入。Windows 无法建立测试符号链接的一项跳过明确保留。
- 只补一份效果测试的空初态/隔离非目标记录参数化。逐节点前后值和 Backend 回执仍为原指标权威，最终真实查询验证非目标记录及 revision 不变。四文件组合 92 passed；这是确定性工作流与真实临时 SQLite，不是 R15 原候选或真实下游模型验收。初稿错误地将平铺查询返回当作 `data` 包装对象而出现 1 failed / 3 passed，只修正测试访问方式，未改产品行为。
- 前端 typecheck、生产构建、Worker 构建/完整单测、11 项服务器代理测试通过；全量 Vitest 为 1,029 passed / 2 failed。两项价格测试在干净 `d246527d` 对照树同样失败，12 passed / 2 failed；不修改目录、时间或断言以换取全绿。后端 AST 检查 998 个文件通过，16 篇现有帮助文章图片静态校验通过，但不等于本轮新帮助流程已验收。
- 当前 Windows 全量与两个断网 Linux 容器分别运行集成源码和干净上游。Linux 依照 CI 分进程运行 Workflow 契约与其余全量，不挂载宿主目录；每个容器固定 6 GiB、4 CPU、1024 PID，未拉取镜像、安装依赖或操作共享栈。现有镜像只有生产 Worker 依赖，首次启动在测试前因无根锁文件停止；后续核对已安装包的锁定版本/完整性，并使用本轮已构建且固定 hash 的 JS 产物，未放宽产品校验。
- 集成归档 SHA-256 为 `ffcf9f4498b0415e6ce3cd70f4bfeac46bd6d93c43389f10a2c365ca9fdc279c`，干净上游归档为 `fc93ee6c3394fa0f8d1abd4e348ce340a4fede1d338720b800cfb2aa36ec2b11`，共同 Worker 产物为 `44f34f23d0b25424e5cf5ab546ac308c25e064965da5b6640008dc308c15c82a`。Linux 收集包含最新效果参数化；Windows 全量开始较早，新增效果参数化以独立 92 项组合补验，生产源码一致。
- 安全检查未发现禁止提交的产物路径；两处凭据形状匹配经上下文核对均为故意攻击测试哨兵，不是实际凭据。没有读真实密钥、发模型请求、运行 R15 写节点、批准、发布、提交或 PR。最终完整测试结果与差异归因完成前，真实写入及泛化授权不会自动派发。

### 恢复收口与基线刷新（2026-09-17）

- 用户要求延续授权继续收口，不重复确认已批准事项。本次先回收上次全量结果并核验源码、上游和实际目标，不把旧进程状态视为当前事实。
- `d246527d` 集成树 Linux 全量为 **7,272 passed / 90 failed / 30 skipped**，干净上游为 **6,648 passed / 90 failed / 30 skipped**；两侧单独执行的 Workflow 契约均为 7 passed。按完整用例身份比较，失败集合完全相同，无候选独有失败。唯一规范化失败栈差异是 `workspace_` 随机 ID，实际均在 `os.chown` 因容器 capability 限制失败。这是同环境差异证据，不是全量通过。
- 用例身份减少的 8 项中，5 项来自 XLSX 参数内生成的 ZIP 时间字节，另 3 项为本轮能力数量/写权限测试更名；不是删除 8 项覆盖。Windows 全量另为 **7,162 passed / 165 failed / 4 errors / 69 skipped**，保留其平台及依赖差异，不用 Linux 结果抹去。
- 中断期间 `origin/main` 前进 8 个提交至 `a7d99925584e818e7f2df84b1646256abfc08ed6`。新增内容主要属于独立 RPG 实验；与本轮唯一源码交点为 `server/main.py` 的可选 Managed Chat 结构化输出。保留该上游契约，不在本轮启用其 feature flag 或改动实验。
- 从该提交建立 `C:\tmp\modelmirror-meta-planner-controlled-writes-10-closeout` / `codex/meta-planner-controlled-writes-10-closeout`，三方重放前一集成树 53 个跟踪文件并逐 hash 复制 46 个新增文件，无冲突。99 项中仅 `server/main.py` 因上游增量变化；暂存为空。前一集成树完整备份的 patch SHA-256 为 `990640848c7efe32e4a82ac6e7160467a214a630ac562ca585ec2772705b17d7`，原 R15 的 98 个冻结源码 hash 仍全部相同。
- 最新收口树与最新干净上游的断网 Linux 全量再次启动，使用相同本地镜像、资源限制和已校验 Worker 产物。当前结果未收齐，不写成通过；未拉取镜像或操作共享栈。
- **验收目标发生实际漂移。** 原提案 `proposal_a1e5d910353342da9fb5c402311171a6` 当前为 approved/r3，`human_modified=true`，有 4 项 `update_node` receipt；Graph IR checksum 从 `2137447f1f9c6bda03e9f00dc4399cb42dc3c30b84e2965d8404bbc33e9666fc` 变为 `a8d2802f42f54ba4238396b2e6a0ec4709adbea5c6a094ada01e796d8aa980d1`，编译校验和也变化。不能把当前 r3 作为未经编辑的 R15/r1 证据。原 r1 安全摘要没有完整 Prompt/config，不能猜测还原；已向用户明确列出固定 r3 补验或提供 r1 导出两种选择，其余回归继续。
- 本次没有执行上述审批、回退人工编辑、模型外发、业务写入、提交或 PR。写入效果、非线性泛化、同基线帮助中心实操与最终人工验收仍独立保留。

### 隔离回归启动器与安全终止证伪（2026-09-17）

- 对旧 Linux 的 90 个共同失败做独立只读分类：20 项 `chown` 权限、11 项本地 Unix socket 被外网守卫误拦、52 项 PDF 子进程未返回、1 项降权权限、3 项 TypeScript 测试依赖路径、3 项既有音频目录/格式断言。分类不等于所有根因已验证；后续按实际重跑结果覆盖结论。
- 对同一源码的 PDF 校验与 socket 超时两项做启动器 A/B：旧版 2 failed，修正版 2 passed。旧版在模块顶层启动 pytest，`spawn` 子进程再次导入时递归启动 pytest；新版只在 `__main__` 入口执行，并仅放行本次临时目录内的 Unix socket。外网仍拒绝。旧容器原始日志与失败结果保留；9 月 17 日两次旧启动器全量保存部分日志后停止，不能计为完成。
- 修正版全量使用本机已有镜像组合的 Node 24.19.0、Python 3.12.14；仅补齐与仓库 lockfile 完全匹配的本地 TypeScript/RPG 纯 JS 测试依赖。不联网安装、不改锁文件。容器无网络、主机挂载或端口，仅增加测试所需 `CHOWN/SETUID/SETGID`，保留其余 capability 拒绝和 no-new-privileges；不调整产品安全策略。
- 上游交叉专项首轮因错误 Python 环境缺少 DuckDB 产生收集错误；正确环境为 177 passed / 4 failed，4 项均复现为新增 RPG Schema 测试缺少 `ajv`。补齐锁定依赖后同一批 **181 passed / 0 failed**。前端类型检查及生产构建通过，Vitest 为 **1,029 passed / 2 failed**，两个价格失败与前次一致；另行补跑被短路的代理/HTTP 头测试 **11 passed**。
- 非线性验收前新增真实 SQLite + Runtime + Evaluator 证伪：零写入安全终止、写入提交后安全终止均已记录正确语义错误和真实效果，仍被 `run_xpert_evaluation_target` 误标为执行失败。修改前 **2 failed**；仅补充可信错误终点与异常安全码一致的分类，不修改事务、权限、调度或已提交状态。
- 同时加入错误码不符、没有 path、错误 outcome、缺失语义 ref、效果不符的对照，共 **12 passed**。错误 outcome 与效果仍由原指标分别判定；未知失败继续失败，已提交节点不回滚。这是离线真实 Backend 证据，不是原 R15 候选或真实模型验收。代码微批限 `server/main.py` 与新测试文件 `test_evaluation_write_error_paths.py`。
- 生产修复后须重新冻结并跑最终全量；不能用修复前正在运行的归档结果冒充最终源码验证。旧 R15 原源码、回执与实际运行资源均未更改。

### 最新全量对照与界面配置证据（2026-09-17）

- 最终生产源码归档 SHA-256 为 `fa8dce954c3682d598b7d8bbd6eafd3feef93dd3280792b27b476cf2150161b8`，最新干净上游归档为 `644d5ab263b6e79356af2bc04b5448e903eb80d2f443d2dc205aa2cd8ef8e795`。使用相同 Python 3.12.14、Node 24.19.0、锁定测试依赖和断网容器执行全量；Workflow 契约按 CI 分进程执行，没有隐藏跳过全量中的失败文件。
- 最终候选为 **7,382 passed / 15 failed / 30 skipped**，干净 `a7d99925` 为 **6,746 passed / 15 failed / 30 skipped**；两侧另有契约 **7 passed**。失败身份完全一致，候选独有失败为 0。644 个新增测试身份中包含 12 项本次安全终止测试；8 个减少身份仍为前述动态 XLSX 参数和 3 项测试改名。两处规范化栈差异逐项核对为 `workspace_` 随机 ID 和 Python 集合输出顺序，不是不同错误。
- 15 项共同失败中，12 项涉及 Worker/Workspace 对跨 UID 测试目录的读写或 chmod，3 项为音频目录版本/格式的既有断言；完整原始日志保留，不降低安全断言。该结果只证明相同测试条件下没有新增失败，不能据此宣称全量或生产就绪。
- 对这 12 项再做单变量环境复核：复制本任务干净主线测试容器的本地无凭据快照，只增加 `DAC_OVERRIDE/FOWNER`，保留无网络、无宿主挂载、资源限制和 no-new-privileges；相同三个 Workspace/Worker 测试文件 **31 passed / 0 failed / 15.13 秒**。未改源码、断言或产品执行权限，未将专项通过数补算到原全量结果；它证明这些失败与本次测试容器对跨 UID 文件操作的限制有关。3 项音频断言仍为未修复的基线失败。
- 独立只读复核没有发现本次错误分类修复的新问题，并建议补充交叉反例。加入“同时声明预期错误终点和虚假 revision 冲突”两项，以及空/缺失错误码两项后，单文件共有 16 项；与四个相邻 Runtime/Evaluator 文件联合 **88 passed / 0 failed**。最后 4 项测试是在全量归档冻结后添加，生产代码未变化；它们由该独立专项覆盖，不虚增全量通过数。
- 前端全量 **1,029 passed / 2 failed**；将最新干净主线的 `client` 提取至独立临时副本，核对 lockfile 一致后执行 `ModelCard.test.ts` 与 `tokenPricing.test.ts`，再次得到 **12 passed / 2 failed**，断言位置和失败原因与候选一致。生产构建、typecheck 及独立代理/HTTP 头 **11 项**通过。没有修改价格、音频目录或它们的断言来制造绿测。
- 新建本任务独立预览器 `15449/16449`，所有 Store 均为空的新目录，凭据环境清空、外网硬拒绝；不复制原 R15 Runtime 或用户凭据，不操作 `15439/16439` 和共享栈。通过可见界面创建零记录合成表并发布 Schema v1：`batch:string`、`score:integer`、`status:string`。验证逐表写授权缺省为空、三种操作缺省关闭、查询权限不被隐含、仅 `update/status` 的授权可配置、默认影响上限为 1，且仍需独立选择对应节点。
- 通过“我的评测集”创建仅用于 UI 配置验证的用例，填写手工构造的两条合成初始化记录与 `demo_update` 前后字段断言，保存并发布 Dataset v1；随后返回数据表页，确认活表仍为 **0 条记录**。此过程没有执行工作流、模型、写节点或审批；`demo_update` 是界面演示 ref，不伪装成已生成候选。现场截图已检查，但正式帮助文章、截图归档和按教程重放未完成。
- 同一批证据保存在忽略目录 `.tmp-cw10-closeout/`，包含最终及上游 JUnit、精确失败对照和前端主线对照；预览 Runtime 位于 `.tmp-cw10-ui-20260917/`，不纳入 Git。本次未提交、推送或创建 PR，未执行真实 Provider 请求或业务表写入，未进入第 8 轮。
- 最终范围核对：冻结的 100 个路径中仅两份收口文档与后加 4 项反例的测试文件发生变化，生产源码与全量归档一致；后端 1,000 个 Python 文件 AST 通过，Diff 空白检查通过。100 个待提交路径无 SQLite/Runtime/构建产物等禁止路径，常见真实密钥形状扫描 0 命中，暂存为空。

### 当前 r3 实际写效果验收（2026-09-17）

- 延续已批准的实测范围，在独立 `15459/16459` 预览器固定当前已审批 r3，保持原 Proposal 内容、状态及 revision。原 R15/r1 与 r3 的身份差异继续保留；本次不重建 r1、不再次批准或发布，不把当前结果归因到未经人工编辑的候选。
- 界面创建并发布 `xeval_dataset_f169301d54b34d0c987f0ec53b02dfb2` v1，覆盖空表和一条非目标合成哨兵两种初态。因现有 UI 仅列待审批候选，已审批 r3 使用同一 Evaluation API 启动，报告在预览器中检查；没有把 API 启动描述为浏览器生成。
- 实际运行 `xeval_run_e46b69cc3d534bcbb250c55ed2fc622d` 为 **2 completed / 0 failed**，两例的效果及语义路径指标均为 **100%**。独立只读 SQLite 核验每例的 Insert、Update、Delete 均真实提交一次、各影响一行，身份相同；revision 为插入 1、更新后 2、删除前置校验 2。两个 Query 均读到真实写后记录；非目标 checksum 一致，哨兵内容及 revision 保持不变。
- 原测试表与预览器活表均仍为 **0 条记录、0 项受控写入账本**。OpenRouter `deepseek/deepseek-v4-flash-0731` 实际完成两次汇总，Provider 回报共 **3,923 token**；未核对账单，无不确定派发、自动重试或额外模型请求。资源读取断言未配置，`resource_evidence=missing` 如实保留，不宣称只读资源指标已验证。
- 首次启动器尝试 `xeval_run_7bc5e36e265a4f25b98259c9aba51cfe` 在任何模型或写入前拒绝，因为精确 Workflow hash 未纳入 Evaluator 对两个 Query 注入的固定 Schema 字段。逐字段确认只有这四处合法 overlay 后，仅修正忽略目录允许清单；未修改产品代码、权限或校验。失败记录保留。验收启动器自身六项离线测试通过，不替代真实执行证据。
- 本次安全证据位于忽略目录 `.tmp-cw10-acceptance-20260917/`，包括来源 receipt、次数账本和 `r3-write-proof.json`；不提交 Runtime、SQLite、完整记录、Prompt 或凭据。生产源码与最终全量冻结归档一致，本次仅补充文档。
- 非线性案例已建立零记录合成质检表 `table_7565c77441414604b64c01739977f1d5`。选择该表 `update/status`、最多一行时被平台安全审批拦截，要求明确到具体表和字段的确认；已向用户说明并停止，没有通过 API 绕过，也没有派发该案例模型调用。
- **门禁仍未全部闭环**：当前 r3 的串行写效果通过，不代表原 r1 或泛化稳定性。低分更新、高分不写、缺失安全终止三路径实测、帮助中心截图及教程重放、最终人工验收仍待完成。未提交、推送、创建 PR 或操作共享栈。

### 精确授权后的非线性生成与停止条件（2026-09-19）

- 用户明确批准新合成表 `table_7565c77441414604b64c01739977f1d5` 的 `update/status`、最多一行，仅用于待审批候选与隔离评测，禁止活表写入。恢复本任务 `15459/16459` 预览器，通过界面只触发一次生成，沿用同一目标、OpenRouter / DeepSeek V4 Flash 0731 及最多三次 completion。没有以 API 绕过授权，没有复跑已完成 r3 项目。
- **泛化生成失败。** 新提案 `proposal_d908c8a4c69f414fa26d0ee9e97e0b5c` 为 pending/r1、validation=false、human_modified=false。三次调用均完成、HTTP 200、finish_reason=stop；Provider 回报 4,007 / 17,216 / 20,418，合计 **41,641 token**。本次收口累计五次真实调用、45,564 token，含此前 r3 的两次汇总；未做账单审计，无自动重试或不确定派发。
- 初次 Graph 报告八项问题，唯一 Patch 后仍有判空 `unmatched` 无法证明可达，以及 `query_result` 在 `serialize_query_result` 不可达两项阻断。三次响应在 Provider、collector、validator 的受观察结构一致，私有响应 Schema 均有效、请求 Schema checksum 与预期一致；不能归因为本次传输截断，也不能把语法有效等同于图语义正确。
- 原始模型响应未完整持久化。失败占位图的模型缺失和串行形状不是本次实际模型 Graph，不据此替换真正的两项阻断。通过只读 SQLite 核验合成活表仍为 **0 条记录、0 项操作账本**；没有创建或执行该候选的三路径评测，没有批准、发布或修改活表。
- 离线最小反例确认独立分析器缺陷：根对象 `is_null` 对可空对象且 `required=[score]` 时，仅找到 `None`；固定候选 `{}` 不满足 required，被校验拒绝，未补满足 Schema 的非空对象。真实 Runtime 对 `None` 和合法对象均有明确互斥结果，而静态分析漏掉非空分支；移除 required 的单变量对照正常。反例只证明这一类缺陷，不宣称完整复现此次 payload。
- 另一项数据不可达由独立的控制祖先检查产生，不能视为判空错误的自然连带结果。后续最小修复应分别覆盖 Schema 合法 witness 与数据控制关系/单次 Patch 契约，不猜测补边、不放宽可达性、不叠加 Prompt 或扩大模型预算。本次尚未实施生产修复。
- 忽略目录 `verify_generalization.py` 只读核验退出 0；它验证了失败、调用边界、空表和合成反例，**不是泛化测试通过**。`generalization-proof.json` 仅包含安全摘要和 checksum，不含完整 Prompt/记录/凭据。核对 100 路径冻结清单，仍只有两份文档和全量后已单独验证的测试文件有变化，生产源码未变；未重复运行全量和构建。
- 提交门禁保持未通过：r3 实际串行写效果 2/2 通过；泛化生成失败；低分更新/高分不写/缺失安全终止三路径未运行；帮助中心截图/教程重放及最终人工验收未完成。剩余两次分支执行额度不改作追加生成，不创建 PR，不进入第 8 轮。

### 根对象判空最小修复：开工契约（2026-09-19）

- 用户授权最小修复。本批继续在 `a7d99925` 的独立 closeout 工作树实施；开工为 100 个未提交路径、暂存为空，脏主树、原预览 Runtime 和历史回执不动。
- 允许路径限 `server/meta_agent/control_flow.py`、`server/tests/test_meta_planner_control_flow.py` 及本任务卡/收口审计，共四个文件。目标仅为补充满足生产者和消费者 Schema 的根对象 witness；不改比较函数、Schema、节点数量、授权、Prompt、控制边或自动修复次数。
- 先补 required 对象、嵌套字段、nullable/union、Multi Route、非法 seed 过滤及 Query 真实输出 Schema 的反例，运行修复前失败；再做局部实现。数据不可达使用断开/显式恢复控制边的独立对照，不替模型猜测原始图，不以判空修复自动消除该诊断。
- 最小检查为离线 `test_meta_planner_control_flow.py`，随后扩展 Meta Planner、Graph IR、Headless、只读/写入资源与 Workflow 控制流相邻测试。调用既有 `run_offline_v2.py` 使用独立空 Store、清空凭据并拒绝外网；不启动真实模型，不重启预览或共享栈。
- 语法、Diff、敏感信息及前后源码范围核对必须完成。本批改变生产文件后，旧全量仅作为前一冻结源码证据，不能继续称为新修复的最终全量；最终全量与真实泛化复测另行保留。回退只撤销本批 witness 补充和对应测试，不回退其他整轮改动、已提交效果或历史失败记录。

### 根对象判空最小修复：离线结果（2026-09-19）

- 仅将现有 `_schema_seed` 的对象候选生成移至根字段/子字段共用位置，并在根字段比较时加入这些候选；所有值仍经过生产者与消费者两份 Schema 校验。没有新增推断器，没有修改比较 Runtime、可达性、Prompt、控制边、授权、节点或调用上限。
- 新增 12 项参数化用例，覆盖 required/nested/nullable/union、两份 Schema 的交集、非法 seed 不放行、Multi Route default、权威 Query 系统字段及编译往返。最初测试草稿包含不合法的 Adapter 枚举与缺失输出声明，先修正测试本身；修正后的生产代码修改前结果为 **9 failed / 25 passed**，失败均指向缺失非空对象 witness。局部修复后同一文件 **34 passed / 0 failed**，没有删除失败断言。
- 独立断边对照仍产生 `Variable query_result is not reachable at node encode.`；空 Patch 不能放行，仅显式断开错误控制边并连接正确边后通过原校验和编译。它验证两项检查相互独立，不证明失败提案的具体边布局，未增加自动连边或修复提示。
- 离线相邻回归覆盖 Meta Planner、Graph IR、Headless、NodeContract、Workflow、受控写入、发布、Evaluator、Evolution、Authoring 与 App：**1,063 passed / 0 failed / 238.46 秒**，四条既有 FastAPI 生命周期弃用警告。该组合包含前述 34 项，不将二者相加。AST、Diff 与四文件敏感信息扫描通过；未修改前端，本批未重跑前端构建或全量后端。
- 三份 JUnit 位于忽略目录 `.tmp-cw10-closeout/`，分别为 `root-object-witness-repro.xml`、`root-object-witness-after.xml` 和 `root-object-witness-regression.xml`；回归文件 SHA-256 为 `e865c4dbdd0415f93f03e5ab7d2270ba4afed5365f0c0e50e71d4ceb7154a9a7`。真实调用账本 hash 与修复前一致；本批未调用模型、执行活表写入、重启预览器/共享栈、提交或创建 PR。
- **门禁仍未闭环。** 旧全量对应修复前冻结源码，不能代替本批之后的最终全量；预览器未重启，仍不是此修复的 UI 实测证据。非线性候选须在独立授权下重新生成并验证三条执行路径，另需同基线帮助中心与最终人工验收。不将未完整还原的数据不可达诊断写成已修复，不把单类 witness 修复宣称为通用符号分析完备性。

### witness 修复后的授权泛化复测（2026-09-19）

- 用户授权复测后，只重启本任务 `16459` 后端，保留 `15459` 前端、已有 Runtime 和历史五次调用账本。新增忽略目录 `.tmp-cw10-retest-20260919/` 冻结当前源码及独立授权：原目标、模型、温度、节点范围、合成表 `update/status` 最多一行均不变；仅一次生成、最多三次 completion，不启用评测或活表写入。既有护栏六项离线测试通过，新账本不重置或覆盖历史账本。
- 在真实预览器点击一次生成，得到 `proposal_05af76b570ac457dabbddbd8660bb5a3`，pending/r1、human_modified=false、validation=false。三次调用完整结束、HTTP 200、finish_reason=stop，Provider 回报 3,808 / 17,011 / 21,916，合计 **42,735 token**；不是账单审计。请求 checksum 与前次完全相同；生产文件与本次启动冻结清单一致，加载的 `control_flow.py` SHA-256 为 `184e41114eb462a24b93de9887e74b89acf99beb5ca3c007c7aa848abd57d960`。
- **复测失败且已经停止。** 初次 Graph 编译有 12 项问题；原始编译响应在 Provider/collector/validator 三层均不符合所发送 Schema，但受观察结构一致。安全结构显示 `update_values` 是零输入的 `json_serialize`，另一个序列化节点有两个输入。唯一修复 Patch 共 33 步，在第 0 个 `update_node(update_values)` 操作因 `Node update_values requires exactly one value input.` 停止，未重新编译。不能将“一条最终错误”解读为其他 11 项问题已经解决。
- 本次在更早的 Patch 应用阶段停止，未形成可验收 Graph，因此不将旧判空/数据可达性错误未出现在最终摘要解释为它们已通过真实验证。失败占位图另报缺少 Agent 模型，仍不是所选模型丢失的证据；没有依据该占位图改变模型、Prompt 或生产代码。
- 只读验证确认合成活表仍为 **0 条记录、0 项写入账本**，旧账本 hash 未变。忽略目录 `verify.py` 退出 0 表示失败、调用边界和数据不变已核验，不是候选通过。新账本 SHA-256 为 `490350bc1fa729d94e6e24354f14c44440bc9507c7577b89ba5c622df08e89b4`，安全摘要为 `retest-proof.json`。未自动重试、未执行候选、未批准/发布、未提交/推送/创建 PR。
- 后续应先离线核对无效纯节点的输入契约和原子 Patch 修复顺序，区分模型生成不合法节点与修复内核的可恢复性；不据此直接叠加提示词、放宽端口门禁或启动新的付费生成。最终全量、三路径效果、帮助中心和人工验收仍未完成。

### Patch 中间状态校验：只读定位与离线复现（2026-09-19）

- 用户要求先复现、精确定位，禁止盲目补丁。本批仅新增忽略目录的离线诊断测试，并更新本任务卡与收口审计；没有修改生产代码、既有测试、Prompt、授权、调用次数或预览器。所有模拟 completion 与 Store 都在无凭据、拒绝外网的临时环境中执行。
- 已确认两层事实：模型初图包含非法零输入/双输入序列化节点；随后 `apply_graph_patch` 对 `update_node` 立即执行完整 `validate_intent_node`，因此在后续数据边操作之前拒绝中间状态。`add_node/connect_data/disconnect_data` 却允许中间状态不完整，且函数末尾已有全节点校验。修复协议的阶段不一致，不是最终端口门禁本身错误。
- 合成 A/B 只交换同一 Patch 的操作顺序：更新在先时于第 0 步重现相同错误；修复输入在先时通过授权、Graph IR、Native 编译、Workflow 和发布预检。合法图重接输入并修改标题/配置时，服务端 `editor-diff` 自己生成的 `disconnect_data -> update_node -> connect_data` 同样失败；调整顺序后 Headless Preview 可应用，Proposal revision/payload 不变。没有执行 Apply。
- 最终组合 **96 项完成，零失败**：18 项诊断与 78 项既有纯节点/Headless/Patch 边界测试，44.26 秒，四条既有 FastAPI 警告。诊断断言确认缺陷仍然存在，不能作为修复或 PR 通过证据。测试初稿两次因 dict/领域 dataclass 的接口使用不正确报错，分别改用现有候选转换校验和 `asdict` 后重跑；没有改产品迁就测试。
- 历史提交 `25a2350f` 已有提前全节点校验；当前 `graph_patch.py` 相对 HEAD 只增加进度诊断，未改变该校验语义。因此不能将此特定缺陷归因于本轮新加的写节点补丁，也没有证据据此提前多 Agent 或更换模型。
- 原真实 Patch 安全结构确认第 0–4 步为配置更新、接线从第 16 步才开始；完整配置与后续操作引用未持久化，不能证明真实 33 步只修此处就一定全过。当前仅定位了首个确定阻断，其他图语义问题与三路径效果仍待验证。
- 建议下一最小批仅统一 Patch 校验阶段：保留每步配置、ref、端口、任务授权等局部校验，将依赖完整连线的节点形状校验统一留在原批末及后续完整解析/编译门禁；不自动重排、不猜测补边、不弱化 Schema、不添加 Prompt 特例或模型调用。该生产修复尚未实施。
- 忽略目录 `.tmp-cw10-retest-20260919/` 保存 `test_patch_intermediate_diagnosis.py` 与 `patch-intermediate-final-evidence.xml`；JUnit SHA-256 为 `0ec511dfc806c528ae7aafb3c2e4a67ffccdc75763919526df71baef5dbbd55d`。生产源码与上一真实复测冻结清单一致，新旧调用账本 hash 均未变。无新增真实模型调用、活表写入、审批/发布、提交/推送/PR 或共享栈操作。

### Patch 批末形状校验修复：开工契约（2026-09-19）

- 用户已授权执行上述定位后的最小修复。继续使用 `codex/meta-planner-controlled-writes-10-closeout` / `a7d99925`，开工 101 个未提交路径、暂存为空；冻结现有差异的逐文件 hash，保护原主树与本轮全部既有工作。
- 本批限五个文件：`server/meta_agent/graph_patch.py`、新增 `server/tests/test_meta_planner_patch_atomic_validation.py`、`docs/META_PLANNER_HEADLESS_AUTHORING.md`、本任务卡与收口审计。只有公共 Patch 内核改变行为；不得修改 Adapter、Schema、Prompt、自动排序/补边、资源授权、经典 Runner 或调用预算。
- 中等风险：改变共享 Patch 的校验时机，但不增加 API、节点、数据迁移或外部 IO。操作的配置/引用/端口/任务检查仍逐步执行；完整节点形状必须在同一内存批次结束时通过，后续权限、类型、控制流、资源与发布门禁保持原位。失败不得持久化。
- 先把已确认的零/双输入、配置/标题更新、合法图重接线、服务级修复及 Headless Diff 转为正向回归，取得修复前失败；再局部移除 Update 的提前完整形状校验，不弱化批末检查。负例覆盖最终缺输入/多输入、配置注入、非法端口、类型错连、失败原子性与调用上限。
- 最小命令为隔离 `run_offline_v2.py server/tests/test_meta_planner_patch_atomic_validation.py`；随后运行已有纯节点/Headless/Patch 边界及 Meta Planner、Graph IR、NodeContract、Workflow、Authoring、Publish、Evaluator、Evolution、App 相邻回归，后端语法、前端生产构建、Diff 与敏感信息扫描。测试使用新临时 Store、无凭据环境并拒绝外网。
- 本批不调用真实模型、不执行活表写入、不重启预览器或共享栈、不审批/发布/提交/PR。旧付费失败和原始诊断文件保持不变。回退仅恢复本批 Update 校验时机和对应新增测试，不回退整轮其他代码或历史业务效果；完整真实复测与整轮提交门禁继续单列。

### Patch 批末形状校验修复：结果（2026-09-19）

- 生产改动仅一处：移除 `update_node` 分支的提前 `validate_intent_node`，以注释说明后续数据边操作可补齐输入；原有 Adapter 存在性、配置 Schema、输出 Schema 推导、任务检查与批末全节点形状校验全部保留。没有自动重排、补边或新增修复机制。
- 新正式回归共 26 项，覆盖零/双输入、配置/标题修改、Deserialize/Aggregate 重接线、新增后修改、最终缺失/超额输入、伪造原生字段/版本/Handle/checksum、非法引用/端口、任务越权、最终类型错连、单次模型修复和 Headless Preview/Apply。所有 completion 均为模拟响应；成功 Apply 只增加一次临时 Proposal revision，没有创建 Xpert。
- 修复前同一正式测试为 **17 failed / 9 passed**，失败指向提前校验或被其遮蔽的后续门禁；移除该调用后原样测试为 **26 passed / 0 failed**。正式负例草稿最初误将 Headless 类型错误预期为返回 invalid Preview，依据现有接口纠正为抛错后，重新记录修复前结果；未修改生产接口或降低断言。
- 复用上一批 43 个文件的相邻回归范围并加入新文件，共 **1,089 passed / 0 failed / 265.14 秒**，包含前述 26 项，不重复累计；四条既有 FastAPI 生命周期弃用警告。范围包含 Meta Planner、Graph IR、纯/控制/资源/视觉/写节点、Headless、NodeContract、Workflow、Authoring、Publish、Evaluator、Evolution 和 App。
- 后端 **1,001 个 Python 文件 AST 通过**。`npm.cmd run build -- --outDir ../.tmp-cw10-retest-20260919/patch-atomic-client-build` 通过，包含 TypeScript 构建；产物位于忽略目录，未覆盖预览器的 `client/dist`。Vite 保留大包体积及目录在项目根外不会自动清空的警告，未为消除警告修改配置。
- 修复前/后/回归 JUnit 分别为忽略目录中的 `patch-atomic-before-final.xml`、`patch-atomic-after.xml`、`patch-atomic-regression.xml`；回归 SHA-256 为 `b9a7f611d5df95e9f3e1c8d5fb017c32236b9d5dadc2b1a1d96e0e47722f188f`。修复后 `graph_patch.py` SHA-256 为 `0627eb3a952a45b681eb2b57109b381249c72fee8b4f072da95c8b133ac09e00`。
- 本批只完成该已定位缺陷的局部修复与离线验证。旧诊断反例和付费回执未改写，不能将其当作修复后验收；未重跑全量后端/前端单测，未重新调用真实模型，未重启预览器。实际 33 步 Patch 未完整保存，解除首个阻断不保证其余图语义问题已解决。三路径真实效果、最新全量、帮助中心与最终人工验收仍是整轮 PR 的独立门禁。
- 收尾逐项比对开工 101 路径 hash，仅四个允许的既有文件变化并新增一份正式测试，共五个路径；其他整轮差异未动。Diff 空白检查通过，五文件常见凭据形状扫描零命中，暂存为空；新旧真实调用账本 hash 均未变，JUnit 与构建产物均被 Git 忽略。

### Patch 修复后的授权单次复测（2026-09-19）

- 用户再次授权后，只重启本任务 `16459` 后端并加载已验证的 `graph_patch.py`，前端 `15459` 和原 Runtime 保留。旧后端退出时其父进程随之退出，确认端口空闲后才启动新后端，没有重复服务或模型派发。新忽略目录 `.tmp-cw10-patch-retest-20260919/` 独立冻结授权、源码和账本；之前两份已消费账本原样保留。六项离线护栏测试通过，启动时 Capability 仍为 22 类、快照 hash 未变。
- 通过真实预览器仅点击一次生成，模型仍为 OpenRouter / `deepseek/deepseek-v4-flash-0731`，原目标、温度、节点范围及 `update/status/最多 1 行` 完全相同；请求 checksum 仍为 `eae0f5aeb6511264f54d13bd3ce96d85ddfddaeb00d7ac0c8483bbce0f5f7fe8`。三次 completion 均完整结束，HTTP 200、finish_reason=stop；Provider 回报 4,082 / 16,867 / 19,719，合计 **40,668 token**，未做账单审计。
- **复测仍失败。** `proposal_a9f6998dec084a4eab36c7b9f8cd5d11` 为 pending/r1、human_modified=false、validation=false。初次 Graph 在授权阶段报告两项问题：`update_status` 的动态谓词输入期望 string、实际声明 any，以及一项控制契约错误。唯一 Patch 共五步，既有规范化移除一个重复连边无操作后为四步；已进入批末 `validation`，不是上次第 0 步提前校验。批末仍要求 `predicate_record_id`、`predicate_revision`、`records`，实际缺少前两者，故未重新编译。
- 任务计划与 Patch 在 Provider/collector/validator 三层 Schema 均有效；初次 Graph 三层均无效，Provider 到 collector 受观察结构相同，而 collector 到 validator 不同。不能将后者未经核对归因于传输丢失，也不能把安全形状摘要当作完整原文重放。当前只证明最终条件契约与连线不一致，不断言是模型漏改配置或新引入的 Patch 缺陷；后续须核对条件配置、断边和现有规范化之间的对应关系，不能通过补造记录身份、放宽谓词类型或新增付费重试处理。
- 只读 `verify.py` 核验通过的是失败事实、调用边界、源码未漂移及合成活表仍 **0 记录 / 0 操作**，不是候选通过。新账本 SHA-256 为 `208f0ba351461003a90a64aef3e6980bd64f9f6e2d76b973be6f10028bcf5f38`，安全证据为同目录 `retest-proof.json`。本批只追加两份证据文档与忽略目录脚本，没有修改生产代码，没有执行工作流、评测、活表写入或审批/发布；无额外重试、提交、推送、PR 或共享栈操作。
- PR 门禁仍未达到：非线性候选生成失败，三路径效果未执行；最新全量、帮助中心及最终人工验收继续单列。单次复测不能证明泛化稳定性，也不能用失败占位图的模型缺失覆盖上述真实阻断。

### Patch 对账与单模型修复 Harness：开工契约（2026-09-19）

- 用户授权最小处理，并明确单模型的提示契约和有限注意力也是可靠性边界。继续使用 closeout 工作树 `a7d99925`，开工 102 个未提交路径、暂存为空；不变更模型、三次 completion 上限、节点权限或 V3/V4 路线。
- 上一轮只读核对完成 14 项离线对照和 94 项重点测试：完整替换业务条件并解除失效谓词输入可编译；保留动态条件却断开输入仍正确失败；三路径合成图可编译往返。真实 Graph 的单处受观察 Schema hash 差异可由默认字段补齐精确复现。现有真实 Patch 缺少逐操作配置证据，不能猜测具体操作内容或忽略初图独立控制流错误。
- 批次 A 最多四个文件：`generation_diagnostics.py`、`graph_patch.py`、新增被动对账测试及本任务卡。只记录 update/connect/disconnect 的目标安全 ref、配置 hash、受限条件结构与输入端口前后摘要；记录有条数/字节上限，不保存 Prompt、业务值、资源内容或凭据。观察失败不得改变 Patch 的原子性、接受条件或异常。
- 批次 B 最多五个文件：`meta_planner_v2.py`、`node_adapters.py`、`graph_patch.py`、新增修复 Harness 测试及本任务卡。集中呈现错误与修复规则，明确 config 全量替换、以最终配置推导端口、可信 records 与业务筛选的区别；替换歧义或重复规则，不新增自动补线、自动改条件、兜底模板或模型轮次。记录冻结合成请求的提示字符构成，不把字符数当作模型 Token 或质量证据。
- 文档收尾仅同步 Headless 说明和任务卡。最小验收为新测试的修复前失败与修复后通过，随后运行 Meta Planner、Graph IR、Headless、NodeContract、Workflow、Authoring、Publish、Evaluator、Evolution、App 相邻回归、语法和敏感信息扫描。所有测试复用隔离 `run_offline_v2.py`；模拟 completion 只验证合同和编排，不证明真实模型成功率。
- 本批中等风险，仅影响生成诊断与模型输入组织；无数据迁移、生产依赖或业务执行变化。不调用真实模型、不写活表、不重启预览/共享栈、不审批/发布/提交 PR。回退只撤销本批观察与提示调整，不撤销既有 Patch 批末校验修复或其他整轮代码。真实复测、三路径效果、最新全量和人工门禁继续独立保留。

### Patch 对账批次 A：离线结果（2026-09-19）

- 新增 11 项正式回归，修复前均因缺少逐操作证据而失败；实现后结合原有诊断和原子 Patch 测试共 **64 passed / 0 failed**，26.42 秒，四条既有 FastAPI 警告。服务级模拟仍最多三次 completion，失败候选不进入重新编译，成功候选仍仅为 pending/r1。
- 对账只观察已应用的 update/connect/disconnect，不参与决策；保存原始/有效配置 hash、哈希化谓词字段/ref、有限条件结构、输入计数/端口摘要。限制 64 条、65,536 字节，省略和观察失败均单列。记录未改、后续恢复旧配置、合法替换以及批末失败可区分；注入的观察器异常不影响原结果。
- 未读取记录、未调用真实模型。没有改变配置校验、Schema、记录来源、事务、资源授权或 Patch 操作顺序。JUnit 位于忽略目录 `.tmp-cw10-repair-harness-20260919/evidence-before.xml` 与 `evidence-after.xml`；进入批次 B 前保留此独立检查点。

### 单模型修复 Harness 批次 B：结果与边界（2026-09-19）

- `update_node.config` 的完整替换、省略/null 不变语义标注在操作 Schema，并直接投影到命令摘要；没有改变 Patch 实际执行语义。修复输入前置原图错误和三步核对顺序，明确最终配置决定最终端口，不能把旧配置的诊断当作不可变端口清单。Adapter 共用规则区分可信 `records` 身份/revision 与业务筛选，不增加字段路径、自动改条件、自动补边或身份伪造例外。
- 新 Harness 测试修复前为 **5 failed / 7 passed**，失败准确对应缺失配置语义、诊断排序和共享记录来源说明；既有非法配置/注入拒绝已通过。第一次实现回归暴露本批新加 `deepcopy` 缺失导入，产生 **43 failed / 64 passed**；补齐导入后原样重跑同组为 **107 passed / 0 failed / 61.61 秒**。失败 JUnit `harness-before.xml`、`harness-after.xml` 保留，不改写为绿测；最终同组为 `harness-green.xml`。
- 相同冻结合成请求的修复提示由 **72,735 字符 / 76,707 UTF-8 字节**变为 **68,617 字符 / 73,183 字节**，字符减少约 **5.7%**。顶层 `validation_issues` 从字符偏移 33,114 移至 2,282，`repair_contract` 从 68,527 移至 2,354。主要采用紧凑 JSON 与重排，不截断目标、授权、原图或 Schema；这是字符/结构证据，不是实际 Token、成本、注意力或模型质量测量。
- 固定夹具的调整后主要构成为 `required_schema` 31,657 字符、`graph_intent_contract` 21,383、`repair_contract` 3,575、原图 3,465；新增语义只放在权威 Schema/Adapter 与统一修复步骤中。没有另建表规则、换模型、多 Agent、兜底模板或额外模型调用。保留非空业务筛选、原权限与三次 completion 上限。
- 复用前批 44 份测试并加入本次两份正式测试，相邻回归共 **46 文件、1,112 passed / 0 failed / 290.88 秒**，四条既有 FastAPI 生命周期警告。范围包含 Meta Planner、Graph IR、Headless、NodeContract、Workflow、受控写入、Authoring、Publish、Evaluator、Evolution 与 App；包含上述重点测试，不重复累计。命令使用隔离 `run_offline_v2.py`，报告为忽略目录 `harness-regression.xml`。
- **1,003 个 Python 文件 AST 通过**，Diff 空白检查与八个本批文件的常见凭据形状扫描通过；扫描不等于完整安全审计。对照最近实测 86 份源码指纹，仅四个允许的后端文件变化；三份真实调用账本 hash 均保持不变，暂存为空，JUnit/备份均被 Git 忽略。另新增两份正式测试，并更新 Headless 说明和本任务卡；没有改其他整轮文件。
- 本批没有真实调用、活表访问/写入、预览器工作流/评测运行、预览重启、共享栈操作、审批/发布或提交/PR。未重跑全量后端和前端构建，旧全量不作为当前源码的最终门禁。真实失败 Patch 原文仍不完整，新增观察器不能倒推出历史具体操作；新的模型泛化成功率、非线性三路径效果、最新全量、帮助中心和最终人工验收继续待完成。

### 修复 Harness 后同范围授权复测：开工（2026-09-19）

- 用户再次授权复测。保持原目标、OpenRouter / `deepseek/deepseek-v4-flash-0731`、温度 0.2、最多两个 Agent、八种所选节点及合成表 `table_7565c77441414604b64c01739977f1d5` 的 `update/status/最多 1 行`。仅一次真实 UI 生成，最多三次 completion；不追加重试、不执行工作流或评测、不批准或发布。
- 仅为加载已完成 1,112 项相邻回归的四文件修复重启本任务 `16459` 后端，保留 `15459` 前端、原 Runtime、已消费的三份账本。新忽略目录 `.tmp-cw10-harness-retest-20260919/` 固定源码与授权，复用原计数/外发护栏和既有批准连接；不读取或输出凭据原值，不操作共享栈。
- 本批不修改生产代码；允许新增忽略目录启动/核验脚本、安全回执和两份证据文档。先运行既有六项离线护栏测试，再通过页面检查授权并单击生成；完成后核验模型调用、Proposal 状态、逐操作对账、活表零记录/零账本及源码未漂移。任何失败或不确定派发均停止，不以失败占位图推断真实模型行为。

### 修复 Harness 后真实复测：任务计划阶段阻断（2026-09-19）

- 用户额度恢复后继续原授权，没有新增一轮生成。启动准备曾因选用了缺少 `httpx` 的基础 Python 失败，发生在模型派发前；改用本任务既有测试虚拟环境后启动成功，没有安装依赖或修改产品。六项离线护栏测试通过，启动冻结检查通过；页面目标、模型、温度、授权和请求 checksum 与前次一致。
- 在 `15459` 真实页面仅点击一次“生成候选智能体”。本次只用了 **2 次 completion**：任务计划及唯一计划修复，HTTP 均为 200、finish_reason 均为 stop；Provider 回报 4,415 + 5,695 = **10,110 token**，不是账单审计。随后应用接口返回 **422**，错误为 `Task-plan repair failed: Task plan must have exactly one terminal task; found 2.`。没有派发第三次调用或追加生成。
- **本次失败未进入 Graph 编译或 Graph Patch 修复。** 没有创建新 Proposal；页面仍保留上次 `proposal_a9f6998dec084a4eab36c7b9f8cd5d11` 的失败候选，不将其当作本次结果。故无法据此次运行判定刚改的 Patch Harness 有效或无效，也不能把未出现旧错误解释为已经解决。
- 原计划响应与计划修复响应没有完整持久化。已知原计划未通过校验，最终计划含两个终端任务；**原计划的具体问题不能反推为同一个错误**。此阶段抛错发生在 Proposal/生成证据持久化之前，调用护栏只在返回 Proposal 时填写 claim 的结束字段，所以本次 claim 无 `ended_at`；两个派发都有完整响应，应用请求已返回 422，并非仍在生成或调用结果不确定。一次生成 claim 已消费，不能用剩余一次额度自动重开生成。
- 代码核对发现任务阶段提示明确要求有界 DAG、任务职责及依赖，但没有直接表达校验器的唯一终端任务规则，也没有解释互斥分支 Agent 可以覆盖同一终端职责。锁定的多成功终点语义不等于多个终端任务。该提示/校验表达差异是下一批的具体核对范围，**不是已经证明的唯一因果**；不放宽唯一终端规则、不把两个分支硬串联、不修改下游 Patch 或增加模型次数。
- 只读核验确认三份历史账本 hash 原样保留，86 份冻结源码一致，合成活表仍 **0 记录 / 0 操作**，Proposal Store 的修改时间和全部项目更新时间均早于本次生成。核验脚本首次因沙箱下 SQLite 文件访问失败，使用同一 `mode=ro` 脚本在获准环境中重跑退出 0；没有改变数据库访问模式或业务数据。退出 0 只证明失败事实及安全边界一致，不是生成验收通过。
- 新调用账本 SHA-256 为 `bc912d5bbdb65b07bda1aaf1b539e8c2cc7d6babf3028cdbd6e567d92968432d`；源码回执 SHA-256 为 `279ff81ba8dc2fc11267913cecc3f3475ad224f77d3240387b00ec9c95fad79d`。安全证据位于忽略目录 `.tmp-cw10-harness-retest-20260919/retest-proof.json`。本批仅新增本地启动/核验助手及证据文档，未改生产代码、运行评测/工作流、写活表、审批/发布、提交/推送/PR 或操作共享栈。最新全量、三路径效果、帮助中心和最终人工验收仍未闭环。

### 任务规划语义契约最小修复：开工（2026-09-19）

- 前次只读核对补充了现有 RunRegistry 的进程内安全证据：两次任务计划在 Provider、collector、validator 三层的规范化正文 checksum 均相同，故唯一修复没有改变计划 JSON。修正前节“原计划问题不能反推为同一个错误”的证据边界：在完整内容 hash 一致的新增证据下，两次计划具有相同校验结果；仍无原始任务语义或依赖边可供还原，不推断模型为何重复。
- 用户授权最小修复。基线仍为 `a7d99925` 加本工作树 CW10 改动，104 个未提交路径、暂存为空。允许正式路径仅 `server/meta_agent/meta_planner_v2.py`、新增 `server/tests/test_meta_planner_task_graph_contract.py`、本任务卡及收口审计四个文件；忽略目录只保存对照、JUnit 和校验回执。
- 目标：初稿与修复共用简短的任务 DAG 语义；从同一依赖分析生成具体终端与依赖诊断。唯一终端任务、互斥 Agent 共担职责、真实任务依赖与辅助节点边界保持一致。不得改 Graph Patch、编译器、NodeContract、Capability、JSON Schema、权限或模型预算，不自动合并任务、伪造依赖或补虚假终端。
- 先运行新测试观察缺失契约/诊断导致的红测，再进行单文件生产修改；验证同一组、现有 Meta Planner/Graph IR/Headless/授权与效果相邻回归、语法、前端构建、Diff 和敏感形状扫描。测试使用既有隔离离线 runner；本批不调用真实模型、不执行活表写入、不重启预览器或共享栈。
- 风险为内部提示和修复反馈变化，不变更公开请求、持久化 Schema 或 Runner。本批回退只撤销共用任务契约和分析结果投影，保留原唯一终端及依赖校验；不回退整轮其他代码。离线通过不能代替真实泛化生成、三路径效果、最新全量及人工提交门禁。

### 任务规划语义契约最小修复：离线结果（2026-09-19）

- 正式变更为四个文件，仅一个是生产文件。初稿与修复共用短小 `task_graph_contract`，区分唯一终端职责、互斥实现及真实独立任务；不硬编码质检目标。修复诊断前置，依赖分析与校验器共用；未知依赖仅在新诊断中计数，不复写任意长文本，解析失败明确 unavailable。生成 Schema、校验错误内容与顺序、节点权限和三次总调用预算不变。
- 新增 21 项正式回归，修改前 **17 failed / 4 passed**，修改后同组 **21 passed**。首次相邻回归为 **1,133 passed**；复核提示后将“不能把所有任务压成一个”改成不得强行合并真正独立职责，避免误伤单任务正例，随后完整重跑该组及两项临时对照：**1,135 passed / 0 failed / 194.67 秒**，四条既有 FastAPI 生命周期 warning。1135 包含新增 21 项，不重复累计。
- 冻结旧校验器并穷举 1–4 个节点的无自环有向图，配合 Agent 数量和授权组合，共 **24,990 组完整错误列表一致**。这是一次临时等价测试的内部比较次数，不冒充 24,990 个 pytest 用例，也不声称证明所有可能输入。正式测试还覆盖未知依赖、自依赖、循环、重复 ID、硬串联分支、虚假终端、解析/字段注入及调用上限。
- 两个 Python 文件语法编译通过；`npm.cmd run build` 通过，保留既有大 chunk warning。Diff 检查通过，四个本批文件的常见凭据形状扫描无命中，不将其当作完整安全审计。对照前次 86 个源码指纹，仅 `meta_planner_v2.py` 预期变化；四份真实调用账本均保持原 hash。
- 最终源码 SHA-256：`meta_planner_v2.py=bfa5d4719891a1dbaf9b1bf26c1950e987de1b221de252bb897f46f5426e461a`；新测试为 `2661cca7e547e5ec3a699c235a567c65a6236299388f12657ad9a82f6e0a8b46`。JUnit `.tmp-cw10-harness-retest-20260919/task-graph-contract-final.xml` 为 `444c1001c7866e285dd12f0473b3c7f04d9edc3d9d9fadc56c565d7c44c0510d`；保留 before/after/初次回归文件，旧证据不覆盖。
- 未运行全量 `server/tests/`、真实模型、Workflow/Evaluation 或业务写入；未重启预览/共享栈，未审批/发布、提交或 PR。预览器尚无这次修复的真实验收结果。最终泛化生成、三路径效果、最新全量、帮助中心和人工验收仍待完成，不提前进入下一轮。

关键验证命令（仅离线隔离环境）：

```powershell
& 'C:\tmp\modelmirror-mcp-test-venv\Scripts\python.exe' -B '.tmp-cw10-closeout/run_offline_v2.py' server/tests/test_meta_planner_task_graph_contract.py --junitxml=.tmp-cw10-harness-retest-20260919/task-graph-contract-after.xml
& 'C:\tmp\modelmirror-mcp-test-venv\Scripts\python.exe' -X pycache_prefix=.tmp-cw10-harness-retest-20260919/pycache -m py_compile server/meta_agent/meta_planner_v2.py server/tests/test_meta_planner_task_graph_contract.py
# 最终相邻回归复用 harness-regression.xml 的 46 个已锁定测试文件，加入上述正式测试和临时校验器等价对照。
# 前端在 client/ 执行 npm.cmd run build，不安装依赖、不部署。
```

### 任务契约修复后真实复测：前置通过、图候选仍阻断（2026-09-19）

- 用户授权一次同范围复测。在独立 `15459` 页面保留原目标、DeepSeek V4 Flash 0731、温度 0.2、Agent 上限 2 和合成表逐项授权，实际点击一次生成。先通过 6 项离线调用护栏测试、冻结 86 个源码指纹及最终 JUnit，再仅重启本任务 `16459` 后端；前端、Runtime 数据和共享栈未变。请求 checksum 仍为 `eae0f5aeb6511264f54d13bd3ce96d85ddfddaeb00d7ac0c8483bbce0f5f7fe8`。
- 共 **3 次 completion**：`task_plan`、`capability_compile`、`graph_patch_v1`，全部 HTTP 200 / stop / 完整响应；Provider 回报 4,184 + 16,723 + 17,361 = **38,268 token**，未核验账单。没有第二轮生成、额外修复或不确定派发。
- **任务计划首稿通过**：1 个任务、0 项任务契约错误，未消耗任务计划修复。三层任务计划正文 checksum 一致。本次证明该具体请求进入了下游图编译，不证明任务规划已稳定或提示修改是唯一原因。
- 初始图在语义校验阶段报告 4 项问题；唯一 Patch 的 7 个操作应用并重新校验后，当前仍报告 2 项：`Router check_score has shadowed or unproven outcomes: matched, unmatched`；`Variable query_json is not reachable at node update_values.`。这些是当前门禁暴露的诊断，不等于已定位完整根因，也不保证处理后没有后继错误。生成 JSON Schema 通过不代表控制/数据依赖正确。
- 新增 `proposal_287c1026987545ccb1293e1899f3c533`，**pending / r1 / validation.valid=false**。Runtime `276758ff-b3c8-4d90-918f-884a1a925fa9` 为 completed，仅表示生成接口完成。页面 0 路由的小型线性图是 `_fallback_candidate` 的不可审批保底候选，不能当作模型成功编译的分支结果；`modelId` 空值也是该保底路径主动设置的防误批准标记，不归因为用户未选模型。
- 只读核验脚本退出 0：86 个冻结源码一致、四份历史账本原样保留、仅一次生成 POST、仅一个新 pending Proposal，目标合成活表 **0 记录 / 0 操作**。未运行候选/评测、批准 Proposal、发布 Xpert、追加模型调用或提交 PR。本次没有生产代码变更，只保存忽略目录助手与安全证据及本节记录。
- 调用账本 SHA-256：`cbbed5759ec99300bf6542c5be5a90548421b469c595e8aa7b1f4b3487efecdf`；源码回执 SHA-256：`d1e55b20a8259ebd67ba90c6e3aa3a3db4e8327c18f10a1b5d01529d2bbbb5a6`。证据在 `.tmp-cw10-task-plan-retest-20260919/retest-proof.json`，同时 Proposal 保留受限分层诊断，不保存完整外发 Prompt 或原始响应供重放。
- **仍不通过 PR 门禁。** 下一批应先离线核对分支可达性与 `query_json -> update_values` 的数据/控制依赖，区分模型语义错误、Harness 表达不足及分析器缺口；不直接放宽门禁，不把剩余错误自动归因于模型，不追加真实试错。分支泛化、三路径写入效果、最新全量、帮助中心及人工验收仍待完成。
