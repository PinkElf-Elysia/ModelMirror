# CW10 系统性收口：分批门禁

## 决策与边界

用户确认系统性调整分批执行。保留 V3 第 7 轮、22 类能力、NodeContract/Adapter/Graph IR V3
权威与三次初始生成预算；不提前进入 V4、多 Agent 产品编排或第 8 轮。
不因失败产物可保存、可查看或可编辑就允许运行、审批或发布。

| 批次 | 单一交付 | 独立门禁 | 当前状态 |
| --- | --- | --- | --- |
| A | 私有失败产物与尝试级诊断绑定 | 原图不被占位替换；失败 Patch 不伪称新图；持久化、脱敏、不可执行 | 实现与离线检查通过 |
| B | 无效草稿的安全加载与人工修复 | 准确展示错误、节点定位、编辑/校验/Diff；revision 冲突；中间稿不能审批 | 实现、离线回归与隔离 UI 验证通过 |
| C | 生成协议与确定性编译分工 | 单一契约派生机械事实；保留真实分支；固定跨目标离线矩阵 | 实现与离线回归通过；真实泛化门禁保留到 E |
| D | 人工显式授权的定向模型修复 | 独立预算/授权/回执；不自动调用、应用或覆盖；复用 Patch 内核 | 实现、离线回归与模拟 UI 验证通过；真实调用留到 E |
| E | 集成与真实泛化验收 | 最新回归、上游兼容、独立预览器、另行授权真实模型与效果证据 | E1 集成完成；E2 总门禁未通过；10 月 1 日本地 G1 单次生成通过，当前候选效果与整体稳定性尚未验收 |

各批完成后报告证据再推进，不把全部范围一次提交。C 若需要正式 Requirement IR、模式库或
Planner/Binder/Reviewer，必须先审计路线依赖并由用户确认。D 不把追加调用藏进原三次预算。
各批结束后停止；既有预览器、调用账本、运行数据与共享栈不变。

## 2026-10-01 生效契约收口后的真实复测

用户再次授权后，从真实预览页面发起一次生成，不人工修改候选，不追加调用。
开始时独立预览端口未监听，页面显示 `Failed to fetch`；只恢复任务所属的
`15489/16489` 本地进程，加载当前 acceptance 工作树，不操作共享栈。
HEAD 仍为 `2bf50146`，运行前后固定的 16 项源码/构建/预览脚本指纹一致。

- 新候选：`proposal_62549c93dfb04e939bca8a0a2f2d40f0`，名称“库存检查流程-DEMO-A”，
  pending/r1、IR current、`human_modified=false`，Workflow 与无副作用发布预检通过。
- 模型仍为 OpenRouter / `deepseek/deepseek-v4-flash-0731`；既定目标、温度 0.2、
  Agent 上限 5 不变。仅合成表查询与 `update/status/1` 授权，无中间件、工具或视觉授权。
  前端显式选择 19 类授权节点不等于扩大系统 22 类能力目录。
- 实际三次 completion：任务规划、能力编译、唯一一次 `recipe_edits_v1` 修复；均
  HTTP 200、`finish_reason=stop`，Provider usage 分别 4,504 / 13,824 / 10,092，
  合计 28,420 token；不确定派发 0，没有额外修复或自动重放。
  本地时间为 10 月 1 日 21:36:06 至 21:37:24（UTC-07:00），对应 UTC 10 月 2 日。
- 初稿通过生效 Schema，但在控制/数据可达性门禁被 `DATA_PATH_NOT_GUARANTEED`
  拒绝。受限修复调整节点输入、建立互斥结论 Agent、替换控制树与最终来源后，
  授权、解析、编译及发布预检均通过；没有把第一次失败改写为一次成功生成。
- 最终图保留 2 个路由、3 个符号场景、2 个互斥成功来源与 1 个错误终点。
  无记录到 `stop_no_record`，低库存路径含 `update_status`，库存足够路径没有写节点。
  这次未出现缺失 `resource_ref`，因此没有触发本批新增的“生效 Schema 不合格转全文
  Recipe 修复”分支；该分支仍只有离线反例证据，不能混称真实验证通过。
- Graph IR checksum：`78a5870f518f09edf53bcc44442e375a1f9f5072851a6981d8721e6a39c6427f`。
  候选 checksum：`eb1ca17bee470ae76e62a6c0eb3c2d09a9d9ba239c440951846328769eb624d2`。
- 15 个旧 Proposal 哈希全部未变，仅新增上述候选；活表仍为零记录，SQLite 哈希未变，
  没有 WAL。评测账本为空，未执行写节点、下游 Agent、Approve 或 Publish。
- 脱敏回执保存于忽略目录
  `.tmp-recovery-e/delivery-preview/effective-contract-retest-20261001/`，包含
  `source-evidence.json`、`generation/calls.json`、`result-evidence-v2.json`。
  首版结果从成功候选不存在的失败产物读取了空尝试项；保留首版，以 v2 从正式 report
  投影的阶段诊断为准。页面已核对；截图文件保存被权限拒绝，不宣称存在截图文件。

结论仅为这一次真实生成及唯一修复通过，不是当前候选写入效果、回答质量、跨任务成功率
或整体稳定性通过。当前候选尚未进行隔离效果评测；最新全量后端、上游集成及泛化门禁
仍需独立完成。先前失败证据继续保留，CW10 仍不进入 PR。本次没有修改生产代码，
没有 Commit、Push、PR 或合并，也没有复用历史额度执行其他场景。

## 2026-10-01 生效契约与修复分流收口

本批依据上一真实失败与离线反事实取证，不新增节点、协议或模型调用。
工作树仍为 `modelmirror-meta-planner-cw10-acceptance-20260927`，分支为
`codex/meta-planner-cw10-acceptance-20260927`，HEAD `2bf50146`。开工时已有 198 个
修改/未跟踪路径且暂存为空；主工作区和历史调用证据不参与修改。

### 根因与实现边界

- 模型收到的 `recipe_schema(request, snapshot)` 已要求资源节点填写授权内的
  `resource_ref`，但通用 Recipe 读取模型允许缺省/null。此前生成入口只依据通用解析成功
  就选择 `recipe_edits_v1`；该协议不能修改资源引用，因而把不可表达的修复任务送入付费调用。
- 新生成在通用解析后、进入 lowering 和修复分流前，使用同一生效 Schema 验证原始 JSON。
  不合格时保留安全失败产物，但不标记为可做受限 edits；复用既有唯一一次严格全文 Recipe
  修复。通过生效契约但有输入/控制/路径问题时仍使用受限 edits，总预算继续最多三次。
- 字段诊断来自生效 Schema；节点 oneOf 只投影其实际声明且获准的 kind，避免把其他节点类型
  的错误混入。诊断最多 64 项，只保存安全位置、约束类别和固定中文说明，不复制校验器原文、
  业务值、资源 ID 或 Schema 正文。旧通用 Recipe 读取器不变，无数据迁移。
- 历史原图未通过当前生成契约时，显式 edits 预检返回 422 和
  `repair_recipe_contract_invalid`，在确认票据、派发账本和模型请求之前停止。
  本批不新增资源绑定编辑器或扩展 edits；不能把“可读取失败产物”说成“可修复所有错误”。
- 不猜测/补填资源，不改变授权、任务、完整路径门禁或原生执行器。补全资源只代表通过此层；
  重复节点和分支缺失数据仍必须被后续门禁拒绝，不能把 Schema 通过当成候选有效。

实现小批限五个文件：`generation_recipe.py`、`generation_diagnostics.py`、
`meta_planner_v2.py`、`model_repair.py` 和新增
`test_meta_planner_recipe_effective_contract.py`。第二小批只同步
`test_meta_planner_recipe_edits.py` 的授权撤销断言及本文，不追加生产修改。
风险为中等：收紧新生成验收并改变不合格产物的唯一修复协议；不放宽付费、资源或审批权限。

### 验证记录

- 开工红测：5 failed，均复现错误分流或旧原图的不可用 edits 仍可准备派发。
- 首次四文件回归：55 passed / 3 failed；三项新增测试误取任务规划阶段而非
  `capability_compile` 的诊断，改为按阶段名称定位，不修改生产判断迎合测试。
- 同组最终回归：75 passed / 0 failed，188.557 秒。包含生效 Schema 一致性、缺失/null/
  越权资源、原生字段注入、任务伪造、非预期类型、三次预算、原产物保留、API 派发前拒绝、
  多表不自动选择、两种业务域的重复位置与路径反证，以及既有显式修复与恢复。
  JUnit：`.tmp-recovery-e/effective-contract-focused-final.xml`。
- 首次扩大回归：1796 passed / 2 failed，962.77 秒。两个失败用例先撤销 `condition`
  授权，却仍要求生成 edits 提示；该图已包含无法删除/换类型的无授权节点，新的派发前拒绝
  符合收口目标。原 Schema/内核对无授权节点的拒绝断言全部保留；新增预检拒绝断言，再在
  授权有效时检查提示与同一编辑契约一致。没有改动生产逻辑以放行这些节点。
  JUnit：`.tmp-recovery-e/effective-contract-affected.xml`。
- 授权测试与本批根因反证同组重跑：67 passed，84.38 秒，4 条既有 FastAPI 弃用警告。
  JUnit：`.tmp-recovery-e/effective-contract-authorization-final.xml`。
- 相同扩大回归清单完整重跑：1798 passed / 0 failed，909.92 秒，4 条既有 FastAPI
  弃用警告。共 87 个测试文件，覆盖全部 `test_meta_planner*.py` 和下列十一项相关回归。
  JUnit：`.tmp-recovery-e/effective-contract-affected-final.xml`，不与局部结果重复累加。
- 最终六个 Python 文件 AST、`git diff --check` 通过；七文件已知凭据签名扫描零命中。
  上次预览的 14 项指纹中，仅本批三个已修改的生成/诊断文件不同，其余 11 项一致；
  `model_repair.py` 不在该旧清单内，不能据此宣称整个工作树无其他差异。
- 工作树最终 199 个修改/未跟踪路径，仅新增本批测试路径，暂存为空；受检路径中没有
  SQLite、Runtime Store、凭据文件、JUnit、日志、上传数据或前端构建产物。

扩大回归命令：

```powershell
$tests = @(rg --files server/tests -g 'test_meta_planner*.py')
$tests += @(
  'server/tests/test_meta_agent.py',
  'server/tests/test_meta_agent_managed_endpoints.py',
  'server/tests/test_meta_agent_managed_gateway.py',
  'server/tests/test_workflow_node_contracts.py',
  'server/tests/test_xpert_runtime_authoring.py',
  'server/tests/test_xpert_publish.py',
  'server/tests/test_xpert_evaluations.py',
  'server/tests/test_xpert_structure_evolutions.py',
  'server/tests/test_xpert_app_api.py',
  'server/tests/test_workflow_typed_values.py',
  'server/tests/test_workflow_typed_ai.py'
)
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -B -u `
  .tmp-recovery-e/run_offline.py @tests `
  --junitxml=.tmp-recovery-e/effective-contract-affected-final.xml
```

runner 的 `TEST_FILES 88` 包含一个 JUnit 参数，实际为 87 个测试文件。
本批测试进程均已结束；这是受影响面回归，不是全量 `server/tests/`。

继续使用既有 `.tmp-recovery-e/run_offline.py`，清除凭据环境、禁用 dotenv、隔离临时 Store
并阻断外网。模拟 completion 仅验证服务链路，不代表 OpenRouter 或实际模型生成质量。
完整 `server/tests/`、新前端构建、最新上游集成、同版预览和真实泛化未在本批执行；
本地 `origin/main` 仍领先 HEAD 四个提交，未刷新远端或整合。

预览器未重启，不用其旧页面证明新代码。未读取凭据、调用真实模型、写活表、审批、发布、
操作共享栈、Commit、Push 或 PR。CW10 总门禁仍未通过。
回退仅撤销本批生效契约入口、分流及预检错误投影，保留旧读取器、安全门禁和失败产物；
不得自动重放旧调用或修改历史 Proposal。后续实际复测必须重新固定代码与外发授权。

## 2026-10-01 位置诊断收口后的真实复测

用户授权后，在 `15489/16489` 独立预览通过真实页面发起一次生成。仅发送既定 E-G1
零记录合成表的安全元数据、测试目标及受限契约；候选权限仍为查询和 `update/status/1`。
本次没有修改生产实现，只启动当前代码的独立预览并记录脱敏证据。

- 新 Proposal：`proposal_f4e64037a95f422ca0c94fc38803a3dc`，r1/pending，校验失败。
- OpenRouter / `deepseek/deepseek-v4-flash-0731` 共 3 次 completion，均为 HTTP 200、
  `finish_reason=stop`；Provider usage 共 27,843 token，无结果不确定、追加调用或自动重放。
  时间为 UTC `2026-10-01 10:10:00` 至 `10:10:49`。
- 初稿 `update_status` 为 `data_table_update`，但缺少 `resource_ref`；查询节点已有正确表引用。
  原始节点准备以 `node_resource:update_status:missing` 阻断；Recipe preflight 标记
  `blocked_by=node_preparation`，完整路径、解析、编译及发布预检未执行。
- 唯一修复返回一项 `update_node`，格式通过但语义没有改变，结果为
  `RECIPE_REPAIR_UNCHANGED`；初稿和修复 Recipe checksum 均为
  `217a932c297721b9c573edad93929e03492768d979c82f6c0dd43e549709b4f0`。
  当前 `UpdateRecipeNode` 只能修改标题、说明、配置和输入，不能补改 `resource_ref`。
  这是本次已确认的修复表达边界，不据此猜测模型内部原因，也不自动扩大资源授权。
- 本次在位置级诊断之前失败，不能用它证明上一批重复位置诊断或分支修复已通过。
  UI 保留失败生成描述与修复入口，审批/评测仍禁用；没有人工 Apply、审批、发布或业务执行。
- 调用前后 14 项源码/构建指纹、14 个旧 Proposal 内容哈希均一致；只新增本次失败 Proposal。
  活表仍为零记录，SQLite 哈希不变且没有 WAL；评测调用账本为空。
- 安全清单和回执保存于
  `.tmp-recovery-e/delivery-preview/occurrence-retest-20261001/`，包括 `source-evidence.json`、
  `generation/calls.json` 与 `result-evidence.json`；均为忽略的本地证据，不提交 Runtime 数据。
  页面截图已在操作工具中显示，但文件保存被拒绝，未声明存在截图文件。

E 总门禁仍失败，不能进入 PR 前收尾。下一步应独立核对资源必填规则、生成投影与受限修复
协议的一致性，再决定有授权约束的修复路径；本次没有追加补丁、放宽校验或进入下一轮。
此前离线回归不重算为本次真实成功；未执行新的全量测试或上游整合，未提交、推送或创建 PR。

## 2026-10-01 重复控制位置的依赖诊断收口

本批响应用户“开始收口”，只修改 Recipe 诊断和唯一修复的上下文，不替换编译器或 Runtime。
工作树/分支仍为 `modelmirror-meta-planner-cw10-acceptance-20260927` /
`codex/meta-planner-cw10-acceptance-20260927`，HEAD `2bf50146`；保留原有未提交变更。
本地 `origin/main` 在前方 4 个提交，本批没有刷新、整合上游或提交 PR。

### 证据与处理

- 上次真实失败的初稿不仅重复了查询序列化节点，也重复了结论 Agent；未更新分支的结论
  已引用该分支没有的写入回执。结构校验在首个重复 ref 停止，完整路径证明没有执行。
  原修复反馈却显示 `branch_repair_required=false` 和依赖问题数 0，并限制只改控制树/终点。
  模型实际只改控制树和最终来源，随后被正确的路径校验拒绝。这是已验证的反馈缺口；
  不能据此断言模型内部原因、模型能力上限或需要提前进入多 Agent/V4。
- 现在只在可信节点准备之后，对原 Recipe 控制位置做受限依赖诊断；保留重复位置，不去重、
  不选择来源、不生成替代 GraphIntent、不自动外提节点。复用现有联合谓词分区，最多 64 个
  控制项、8 个路由和 256 个符号场景。未知节点、错误出口、不可证明谓词、重复可同时到达、
  超限和需要完整图的 Data Merge 均保留 blocked/未知。
- 诊断区分重复节点各自的输入可用性与最终来源角色：反例只暴露语义 outcome、源/目标 ref、
  原始字段/控制位置及数量，不包含 Prompt、业务记录、谓词 witness 或物理路径。
  `recipe_occurrences` 只表示已解析依赖子集；`not_disproved` 不代表可外提，也不代表图有效。
- 未执行的完整依赖证明计数为 null；没有反例但证明未完成时，`branch_repair_required` 也为
  null，而不是 false。既有本地 `not_executed` 遥测保留。反馈允许依据证据联动检查控制位置、
  消费输入和互斥最终来源；仍保留任务、条件、资源、写字段和行数授权，不增加修复轮次。
- 原图仍抛出原有 `RECIPE_REPEATED_NODE`。仅整理控制树的对照仍被完整路径门禁拒绝；
  离线显式分支结论修改可通过现有编译。这是工具链对照，不是模型生成成功或业务执行证据。

### 本批验证

实现小批次限定三个生产文件（`generation_recipe.py`、`recipe_preflight.py`、
`meta_planner_v2.py`）和两个测试文件；通过后单独更新本任务记录，无 Schema/接口迁移。
主要风险是诊断误导修复，因此以负例证据为主，任何未完成证明都不授予可执行或可审批权限。

- 红测：17 failed / 17 passed，复现重复位置缺少依赖事实和未知被写成 false/0。
- 首次扩大证伪：122 passed / 3 failed。其中两项是本批多改了既有本地 `not_executed` 状态，
  已恢复；另一项是新增测试误将普通合法 ref 作为敏感标记，已改用既有规则识别的 canary，
  没有扩大或放宽生产脱敏规则。
- 同一组六个测试文件重跑：125 passed，4 条既有 FastAPI 弃用警告，120.27 秒。
  覆盖两种业务 Schema、显式/模板输入、读错误出口、相关谓词、防伪诊断、受限内容投影、
  控制树单独修改仍失败，以及原有恢复 Preview/Apply、持久化和授权边界。
  JUnit：`.tmp-recovery-e/occurrence-adversarial.xml`。结果不得与扩大回归重复累加。
- 扩大回归：1776 passed，4 条既有 FastAPI 弃用警告，823.00 秒。通过 PowerShell 展开全部
  `server/tests/test_meta_planner*.py`，追加 `test_meta_agent.py`、
  `test_meta_agent_managed_endpoints.py`、`test_meta_agent_managed_gateway.py`、
  `test_workflow_node_contracts.py`、`test_xpert_runtime_authoring.py`、`test_xpert_publish.py`、
  `test_xpert_evaluations.py`、`test_xpert_structure_evolutions.py`、`test_xpert_app_api.py`、
  `test_workflow_typed_values.py`、`test_workflow_typed_ai.py`。共 86 个测试文件，
  JUnit：`.tmp-recovery-e/occurrence-affected.xml`。不是全量 `server/tests/`。
- `cd client; npm.cmd run build` 通过，保留既有大 bundle warning；本批没有改动前端源码。
  五个 Python 文件 AST、`git diff --check` 通过；六个本批文件的已知密钥签名扫描零命中。
  工作树 198 个修改/未跟踪路径，暂存为空；仅新增一个本批测试文件，其余既有变更全部保留。
  SQLite、Runtime、凭据、JUnit、日志与前端构建产物未加入待提交清单。
- 首次红测在只读沙箱创建临时目录时阻塞，尚未进入 pytest；核对并停止该次测试进程后，
  使用受限提权运行同一离线封装。未停止预览器、容器或其他任务进程。

离线入口继续使用 `C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -B -u
.tmp-recovery-e/run_offline.py <测试文件> --junitxml=<报告>`，清除模型凭据环境变量，
禁用 `.env` 加载并阻止外部网络，所有 Store 均位于独立临时目录。

回退范围：停用新增位置级诊断调用及对应提示投影即可；保留未知状态和原有完整门禁，
不撤销业务写入、不重放模型调用、不改已有 Proposal。此次没有额外 Provider 调用、
活表写入、候选 Apply、审批、发布、预览器重启或共享栈操作。
本轮 E 总门禁仍未通过；同版真实模型/效果验收、最新全量后端和上游集成仍是独立待验事项。

## 2026-09-30 统一语义恢复后的真实复测

用户另行授权后，在 `15489/16489` 独立预览通过真实页面发起一次生成。沿用 E-G1 零记录
合成表、查询及 `update/status/1` 候选授权；不执行写节点、下游 Agent、评测、批准或发布。
本次仅补证据，未修改生产代码。源码及前端构建十项 SHA-256 与调用前清单完全一致。

- 新 Proposal：`proposal_2591c14b4ccc499cbc75fab80d25a201`，r1/pending，校验失败。
- OpenRouter / `deepseek/deepseek-v4-flash-0731` 共 3 次 completion，均为 HTTP 200、
  `finish_reason=stop`；Provider usage 合计 26,742 token，无结果不确定或自动重放。
  调用时间为 UTC `2026-10-01 06:53:35` 至 `06:54:01`，即本地 9 月 30 日。
- 初次 Recipe 因 `serialize_query_result` 在两个分支重复出现而被
  `RECIPE_REPEATED_NODE` 拒绝。唯一修复的格式、操作、解析和 Recipe 展开均通过，
  随后 `DATA_PATH_NOT_GUARANTEED` 阻断：公共 `conclude_inventory_check.task` 消费
  仅更新分支存在的 `serialize_update_receipt.json`，未更新分支无法保证提供该输入。
- 控制结构、条件真值、分支可达性及终点覆盖通过，路径数据可用性失败；资源解析、编译和
  发布预检未执行。诊断占位图的 `modelId` 报错不是原始生成失败根因，不得据此更改模型绑定。
- UI 已加载本次失败原图及人工修改入口，批准/评测仍禁用；没有尝试人工 Apply 或额外付费修复。
  这证明恢复协议推进到了语义校验，不证明生成稳定性、业务效果或显式修复的真实成功率。
- 旧 `proposal_51ea5083de43448aa872d93b50b311f4` 保持 r1/pending 且内容哈希不变；
  活表 SQLite 哈希不变；评测调用账本为空。新目录只保存安全回执及源码清单：
  `.tmp-recovery-e/delivery-preview/semantic-recovery-retest-20260930/`，不提交 Runtime 产物。
- 启动独立后端时，首次误用基础 Python，因缺少 `dotenv` 在导入前退出；改用原预览 venv
  后启动成功。该环境错误未发生模型请求，不是本次生成失败原因；共享栈未操作。

结论：真实复测仍失败，不进入 PR 门禁，不追加调用或现场修补。后续需先核对分支汇总输入与
修复上下文的全链路一致性，保留当前路径校验；本次未执行最新全量后端或上游整合。

## 2026-09-30 未展开 Recipe 的统一语义恢复

本批响应用户“开始下一批”，承接最新复合失败的只读核对。只实施受限语义恢复、操作字段契约
和未复核诊断，不进行真实 Provider 调用，不修改现有候选或活表，不提交 PR。
工作树为 `C:/tmp/modelmirror-meta-planner-cw10-acceptance-20260927`，分支
`codex/meta-planner-cw10-acceptance-20260927`，HEAD 为 `2bf50146`；保留全部既有未提交内容。
本地已知 `origin/main` 在前方 4 个提交，本批未刷新或整合上游。

### 本批交付

1. 协议与诊断：四种修改操作的必填/可选字段从同一 Schema 派生并前置到修复提示。协议失败
   导致旧错误未复核时标记 `not_rechecked`，相关计数为 null，不再以零表示错误消失。
2. 后端恢复：严格 Recipe 可通过 `recipe_edits_v1` 原子修改及完整 Preview/Apply；复用既有
   `apply_recipe_edits`、授权、lowering、类型/路径、编译与发布预检。旧 control-flow 协议兼容，
   资源/输入失配草稿仍保持窄人工入口。通过后只增加一次 pending Proposal revision。
3. 显式模型建议：未展开 Recipe 使用相同修改内核及绑定 envelope，不伪造 Intent，不自动应用。
   原有单次确认、请求去重、不确定回执、TOCTOU 与 20 次上限不变；未取得新授权不会派发。
4. 前端与文档：仅在服务端提供 `semantic_repair` 契约时显示语义操作编辑、字段参考及模型修复。
   建议只载入本地编辑区，仍须单独预览/应用；旧 Graph Patch 不能混入 Recipe 建议。

实现按协议/诊断、恢复服务、前端投影、文档四个小批次推进，每步不超过五个文件；跨批共享
文件由主智能体串行修改，不派发并行编辑。没有新 Runtime、Store、生产依赖或 Planner 能力。

### 验证与限制

- 初始两条定向红测均复现：缺少操作字段投影，协议未通过却报告原问题不再出现。修正后
  Recipe edits、preflight 与 template repair 三文件 93 项通过。
- 未展开复合失败的初始三条红测复现缺少语义入口及拒绝模型预检。两种业务 Schema 的
  分支案例现可恢复，并覆盖错误出口、API 往返、revision 一次递增、重启、Schema 漂移与注入。
- 五文件恢复集成组首次 139 passed / 1 failed：新增测试错误地将独立调用回执也纳入“候选不变”
  的全对象相等断言。改为检查 payload/revision/status 不变、回执恰好一条及重复请求不再调用；
  同组重跑 140 passed / 4 条既有 FastAPI 弃用警告。后续新增 API/注入测试纳入扩大回归。
- 前端四文件最终 78 项通过，覆盖未授权显示降级、字段注入、协议混用、限额、显式确认及
  载入/预览/应用分离。首次生产构建发现新增归一化函数返回类型缺少已验证 checksum 字段，
  显式返回受检字段后，同一 `npm.cmd run build` 通过；既有大 bundle warning 保留。
- 扩大回归最终 1612 passed / 4 条既有 FastAPI 弃用警告，耗时 781.74 秒：全部
  `server/tests/test_meta_planner*.py`，加 `test_meta_agent.py`、`test_meta_agent_managed_endpoints.py`、
  `test_meta_agent_managed_gateway.py`、`test_workflow_node_contracts.py` 和
  `test_xpert_runtime_authoring.py`。JUnit：`.tmp-recovery-e/semantic-recovery-affected.xml`。
- 独立兼容组最终 143 passed / 4 条既有警告，耗时 14.20 秒：`test_xpert_publish.py`、
  `test_xpert_evaluations.py`、`test_xpert_structure_evolutions.py`、`test_xpert_app_api.py`、
  `test_workflow_typed_values.py` 和 `test_workflow_typed_ai.py`。
  JUnit：`.tmp-recovery-e/semantic-recovery-compatibility.xml`。两组共 1755 项，不与前述窄测重复累加。
  两组均通过隔离入口 `C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe
  .tmp-recovery-e/run_offline.py <测试文件> --junitxml=<结果文件>` 执行；清除模型凭据环境变量、
  使用临时 Store 并拒绝外部 socket。模拟 Provider 只用于协议验证，不冒充全量后端或真实调用。
- 前端最终命令为 `npm.cmd run test -- --run src/components/meta/MetaPlannerV2.test.tsx
  src/components/meta/FailedDraftRepair.test.tsx src/components/meta/ModelDraftRepair.test.tsx
  src/components/meta/metaAuthoring.test.ts --maxWorkers=1 --fileParallelism=false`，78 项通过。
- 9 个 Python 文件 AST 通过，本批 19 个源码/测试/文档路径的已知密钥签名扫描零命中，
  `git diff --check` 通过。工作树有 197 个既有及本批修改/未跟踪路径，暂存为空；本批仅新增
  `recipeRepair.ts` 与 `test_meta_planner_recipe_semantic_recovery.py` 两个路径，未将 Runtime 数据、
  SQLite、日志、凭据或构建产物加入待提交清单。
- 未做本版真实浏览器、Provider 或业务效果验收，未重启 15489 预览或共享服务，未审批/发布。
  合成夹具与模拟 completion 只证明恢复协议和门禁，不证明单模型生成稳定性或修复成功率。

回退可单独停用 `semantic_repair` 投影及未展开 Recipe 的模型预检，保留旧控制树入口、已有
修复回执和 Recipe/Intent 读取。不撤销历史业务写入，不重放已消费调用，不删除原失败证据。
本批没有 Commit、Push、PR、Merge 或部署；同版预览、真实模型、全量后端和上游集成仍独立验收。

## E-G1 后续受限修复小批次

本次仅响应用户“开始执行下一步定位修复”，不提交 PR，不沿用已消耗完的真实生成额度。
历史失败证据及本次命令结果集中记录在
[E-G1 审计报告](../audits/META_PLANNER_RECOVERY_E_G1_RETEST_20260923.md)。

| 小批次 | 限定文件（共享测试随对应批次复跑） | 独立检查 |
| --- | --- | --- |
| R1 诊断与修复反馈 | `generation_recipe.py`、`generation_diagnostics.py`、`meta_planner_v2.py`、`test_meta_planner_recipe_lowering_recovery.py` | 未知/重复/遗漏 ref、嵌套位置、脱敏、原样修复、三次预算 |
| R2 安全保留与窄恢复 | `failed_artifacts.py`、`failed_recovery.py`、`model_repair.py`、`headless_authoring.py`、同一测试文件 | Recipe/Intent 区分、只改控制结构、完整预检、API、TOCTOU、禁用付费 Patch |
| R3 存储与批准边界 | `authoring_store.py`、`authoring_service.py`、`headless_authoring.py`、同一测试文件 | 不信任报告标签、服务/Store 缺少绑定复核时拒绝、私有回执、重启、旧流程兼容 |
| R4 前端安全投影 | `FailedDraftRepair.tsx`、对应测试、帮助文章 | 只读节点、诊断定位、编辑/预览/应用分离、冲突、桌面/窄屏模拟检查 |
| R5 证据同步 | Recipe、Headless、本文与 E-G1 报告 | 单列真实失败、合成测试、待完成门禁及回退 |

R1–R4 不修改 Runtime、SSE、Capability22、Graph IR V3、业务表或调用预算；R3 只在现有 Proposal
内增加服务端私有恢复回执，不新建 Store。普通 V3 候选继续使用 `GraphPatchEnvelopeV1`。
本轮定位不能弥补已丢失的 E-G1 原始描述；新的保留能力只用于未来产物，不伪造历史原图。
这不是 E3 泛化验收通过，更不代表全量后端、最新上游和真实写入效果门禁已完成。

## E 开工契约

### 2026-09-26 受控文本适配与引用去重

本批响应“开始处理”，沿用 E 工作树、`09e8a7d6` 与 183 个既有变更路径。上一批诊断结论不变，
这次只处理 Agent 文本消费的机械重复，不能以离线编译通过宣布真实生成稳定。

- 限定五文件：`generation_recipe.py`、`meta_planner_v2.py`、
  `test_meta_planner_recipe_text_inputs.py`、Recipe 文档和本文。
- 只有 `workflow_agent.inputs=null` 启用模板来源派生；旧数组形式和省略 inputs 行为不变。
  在当前授权内插入普通 JSON Serialize V2；原 24 节点/40 边、22 类能力、Graph IR V3、
  类型、路径、受控写入、审批与调用预算全部不变。
- 不补业务条件、不重排或删除模型业务节点、不猜来源；模型仍须决定正确分支及其终点。
- 验收：先红测，再跨领域/错误分支、空 Diff、编译往返、Patch、恢复、隔离效果、
  注入/权限/超限反例、历史失败只读重放与旧输入等价，最后关联回归、语法、构建和敏感扫描。
- 回退：提示停用新模式并恢复显式绑定；保留 null Recipe 读取，已生成普通序列化节点无需迁移。
- 本批不调用真实模型、不写活表、不读取凭据、不重启预览、不操作共享栈，不提交或创建 PR。

开工红测 `5 failed / 16 deselected`：现有解析器及模型 Schema 均拒绝新 null 输入模式。
实现后首次核心运行 `31 passed / 5 failed`；五项失败均为本批测试断言的接口假设错误
（错误码在异常属性而非消息正文，Headless state 没有 `can_apply` 字段），按真实契约修正；
未放宽生成或安全校验。

追加一个独立往返小批次：`node_adapters.py`、同一新测试及本文。发现旧显式图与新文本图的
空 `editor-diff` 都返回相同的写节点更新：`_table_editor_filter_from_native` 无条件生成新的
谓词 ref，丢失已有 Adapter 中的语义 ref。旧/新对照红测 `2 failed`，并非本次文本适配引入。
修复只在已有 Adapter filter 能通过原 `_data_table_filter_inputs_from_native` 完整对照时保留；
真实字段/运算/字面值编辑仍重新投影，不隐藏变更、不跳过授权或类型检查。没有改 Runtime。
新增真实 filter 修改反例、原样空 Diff 与布局预览/Apply 检查；不将这个发现扩展为画布重构。
回退仅恢复编辑器 filter 投影，业务执行与存储无迁移。两个小批次合计六文件，不混入既有其他改动。

新增针对性测试 `34 passed / 4 warnings`：

- 两领域各含普通/读取错误分支；每个 Agent 仅在本分支执行适配链，3/4 场景保持不变。
- 9 类非字符串 Schema 经真实 JSON Serialize V2 节点传递，不伪造字符串类型。
- 未授权序列化、未知端口、属性路径、原生变量、无输入、跨分支值、孤立节点和 24/40 上限拒绝。
- 编译/反编译、派生 Graph Patch、空 editor-diff、真实 filter 编辑、布局 Preview/Apply 通过；
  Preview 不改变提案，Apply 仅增加一次 revision，未创建 Xpert。
- 模拟生成仅两次 completion；失败原样修复仍最多三次。含 null 的私有 Recipe 重启读取及配对校验通过。
- 缺失/更新/不写入三路径在临时隔离 SQLite 执行，业务哨兵不变；Agent 响应仍为模拟，不能称为真实模型验收。

历史只读回放 `8 passed / 4 warnings`：两份保留的真实 Recipe 继续按旧数组语义失败，分别仍有
3/4 处类型问题。仅将离线副本 Agent inputs 改成 null 后，派生 4 个序列化节点，模板检查通过且
局部类型问题为零；孤立节点仍明确拒绝，未产生候选。这个人工反事实不是模型新输出，也没有
自动删除节点或修改业务分支。四组合法旧 Recipe 的完整 GraphIntent 与前镜像相等。
保护的历史证据、Proposal Store 和合成表数据库 hash 不变；记录查询/写入及外部调用均为零。

验证命令入口继续为 `.tmp-recovery-e/run_offline.py`。新测试路径为
`server/tests/test_meta_planner_recipe_text_inputs.py`；历史回放为
`.tmp-recovery-e/test_text_inputs_replay.py`，输出独立 `text-inputs-replay-evidence.json`。
JUnit 为 `.tmp-recovery-e/text-inputs-{new-tests,replay}.xml`；初始失败与对照红测另存，未覆盖历史回执。
四个 Python 文件语法和前端 `npm.cmd run build` 通过；仍有既有 FastAPI 弃用及大 chunk 告警。

关联回归通过：全部 `server/tests/test_meta_planner*.py` 加上一批同样的九个相邻测试文件，
共 73 个文件，`1520 passed / 4 warnings`，耗时 915.06 秒；其中已包含新增 34 项，不重复累加。
命令为上述隔离 runner 加显式文件列表及
`--junitxml=.tmp-recovery-e/text-inputs-regression.xml`。四份 Python 语法、前端构建、Diff 和
六文件敏感签名扫描通过。收口时 184 个修改/未跟踪路径，较开工只新增本批测试；暂存为空，
未发现 SQLite、Runtime 数据、日志或构建产物进入待提交清单。

全量 `server/tests/`、最新上游集成、帮助中心实操、预览器重启与真实模型泛化复测未在本批执行。
当前预览器未重新加载本次后端修改，不得直接把旧预览结果作为新代码证据。E3 和 PR 门禁仍未通过；
无新增真实模型调用、活表写入、Proposal 批准、Xpert 发布、提交、推送或 PR。

### 2026-09-26 可信事实与独立诊断分离

本批响应用户“开始收口”，仅实施上一轮定位结论的第一批。基线 `09e8a7d6`，沿用 E
工作树，开工时 182 个既有修改/未跟踪路径、暂存为空；不创建新分支或覆盖其他任务。

真实复测 `proposal_aad68520e37f46c980ab1e33c1e94517` 仍失败：三次 completion 均 HTTP 200/stop，
Provider usage 合计 30255 tokens。首次模板引用缺少显式输入，唯一修复补输入后暴露漏节点与
四处对象到字符串错连。离线复盘 9 项通过，已用请求字节 checksum 证明反馈重建一致；
不是模型成功或业务效果验收。原始首次输出同时已有三类问题，前置模板异常使局部预检全部 blocked。

- 单一目标：可信配置、授权、资源和数据来源准备完成后，模板问题不遮蔽独立类型及控制树诊断。
- 限定五文件：`generation_recipe.py`、`generation_diagnostics.py`、
  `test_meta_planner_recipe_diagnostic_independence.py`、Recipe 契约文档和本文。
- 不改 NodeContract、Adapter、Graph IR、Runtime、权限、接受边界、Prompt 规则或调用预算。
  Agent 文本桥接及引用去重仅保留为下一批审计事项；本批不自动补边、转换值或删除节点。
- 验证：两领域多缺陷红测、来源/配置/授权攻击、诊断上限与脱敏、真实保留 Recipe 离线重放、
  模拟生成的三次预算和失败 Proposal、受影响回归、语法、构建及 Diff/敏感签名检查。
- 回退：恢复该批 Recipe 准备与诊断接入；既有 Recipe/Proposal/调用证据保持可读，无数据迁移。

开工红测：两个领域均在通用 Prompt 引用异常处失败，尚无独立类型和控制树反馈，结果为
`2 failed / 13 deselected`。修复后本批及 Recipe/预检/诊断核心测试 `101 passed`。

离线历史回放与前后编译对照 `7 passed`：

- 保留的首次真实 Recipe 现在同时报告缺失输入、三处类型错连和漏节点；仍不产生候选。
- 保留的实际修复 Recipe 仍报告漏节点及四处类型错连；不会将 Provider HTTP 200 当作编译成功。
- 两领域、普通与读取错误分支共四组合法 Recipe，前后完整 GraphIntent 及 checksum 一致。
- 历史证据、Proposal Store 和合成表数据库 hash 未变；未读业务记录，未写活表，外部调用为零。
- 修复请求由 38814 增至 40890 字符；该值不是 Token，也不证明模型注意力或成功率改善。
  本批只补齐可独立确定的反馈，文本适配和上下文去重仍留待下一批。

验证入口为隔离 runner `.tmp-recovery-e/run_offline.py`；设置独立 Runtime 目录、禁用 dotenv
并阻断外网。核心测试选择本批新测试及 `test_meta_planner_recipe_preflight.py`、
`test_meta_planner_generation_recipe.py`、`test_meta_planner_generation_diagnostics.py`。
回放脚本为 `.tmp-recovery-e/test_diagnostic_independence_replay.py`，不覆盖原复测证据。
JUnit 分别保存在 `.tmp-recovery-e/diagnostic-independence-{red,core,replay}.xml`。

关联回归通过：全部 `server/tests/test_meta_planner*.py` 加 `test_meta_agent.py`、
`test_workflow_node_contracts.py`、`test_xpert_runtime_authoring.py`、`test_xpert_publish.py`、
`test_xpert_evaluations.py`、`test_xpert_structure_evolutions.py`、`test_xpert_app_api.py`、
`test_workflow_typed_values.py`、`test_workflow_typed_ai.py`，共 72 个文件，结果为
`1486 passed / 4 warnings`，耗时 1078.61 秒。使用同一离线 runner，JUnit 为
`.tmp-recovery-e/diagnostic-independence-regression.xml`；其中包含前述核心测试，不重复累加通过数。

三份 Python 文件语法、`npm.cmd run build`、Diff 和五文件敏感签名扫描通过；构建仍有大体积
chunk 告警，pytest 仍有既有 FastAPI `on_event` 弃用告警，本批未处理这些无关项。
前端沿用 code/ref/location 展示，新增 `template_detail` 用于服务端修复反馈；本批未增加界面
引导，也未以构建通过代替人工验收。收口检查为 183 个修改/未跟踪路径，较开工仅新增本批测试，
暂存为空；未发现 SQLite、Runtime 数据、日志或构建产物进入待提交清单。
本批未执行新的真实模型调用、全量 `server/tests/`、最新上游集成或预览器复测；E3、真实泛化和
PR 门禁仍未通过。没有提交、推送、创建 PR、批准 Proposal 或发布 Xpert。

### 2026-09-25 Recipe 预检与修复反馈收口

用户授权本地收口。沿用 E 工作树和 `09e8a7d6`，保护开工时 179 个既有变更路径。
不调用外部模型、不读取凭据、不写活表、不重启预览或共享栈，不批准、发布或提交 PR。

| 小批次 | 范围 | 独立门禁 |
| --- | --- | --- |
| U1 | Recipe 展开、诊断、独立局部预检及正式反例 | 三类分支差异可定位；坏控制结构不掩盖可证明的输入类型/谓词问题；未经解析或授权的事实不推断 |
| U2 | Planner 共用修复上下文与失败结构进展 | failed/blocked 前置、受限 JSON 投影、权威契约去重；三次预算与旧修复入口不变 |
| U3 | 集成测试和证据文档 | 两领域合法/非法成对矩阵、隔离效果、恢复/授权回归、语法、前端构建、Diff 与敏感签名扫描 |

U1 生产代码限 Recipe、diagnostics 和一个无副作用局部预检模块；U2 限 Planner、Recipe
提示投影和对应测试。共享本文记录每批结果。不会创建伪造控制边来绕过全图证明；局部事实
不能替代授权、可达性、发布或效果验收。完整 E2/E3 门禁独立保留。
回退仅撤销本批诊断/反馈接入，保留既有 Recipe、失败产物、调用回执和业务状态。

U1：已完成。正式反例先修正夹具返回顺序错误，再确认三类通用错误码红测；实现后
局部预检、Recipe 与恢复三文件 77 passed。无真实调用或活表读写。

U2：已完成共用修复反馈、公共 Schema 去重和失败结构进展观测。三项针对性红测由失败转为
通过，四文件核心回归 65 passed；扩展最终 72 passed。中途 12 项失败来自新增测试自行规定的
15% 压缩门槛，契约接受/拒绝对照均已通过；删除这个未经计划规定的比例，改为检查结构去重且
长度确实减少。没有为满足比例放宽任何安全或类型断言。局部 nullable 错误仍等待完整路径保护
证明，控制结构不变但原错误消失时不会被进展观测误拒绝。

U3：已完成本批离线收口。历史原图回放 4 passed；受影响 81 文件首次为 1653 passed / 1 failed，
唯一失败是旧测试直接读取内联 Schema 的 properties。将该布局断言改成 Prompt Schema 与
服务端解析均实际拒绝伪造 outputs 后，精确复跑通过；相同 1654 项选择集完整重跑全部通过，
无 errors/skipped。前端四文件 50 passed，生产构建、六文件 AST、Diff 和九文件敏感签名扫描通过。
原 Proposal、表库、调用账本和历史核验产物未改写。当前暂存为空，182 项既有/本批变更路径中
无 Runtime 数据或构建产物；所有本批命令已结束。

本批只完成反馈内核和离线证据，不宣称真实模型成功率改善。Schema 去重后，固定真实样例的
初次提示减少 2026 字符；由于补入精确局部诊断，修复提示反而增加 194 字符，不能称为全面缩短。
未重启预览器、调用 Provider、写活表、审批、发布、Commit、Push 或 PR。最新上游/全量及 E3
门禁继续独立保留，详见 [本批收口报告](../audits/META_PLANNER_RECIPE_PREFLIGHT_CLOSEOUT_20260926.md)。

### 2026-09-24 Recipe 路由与修复闭环离线核验

用户授权验证，不修改生产源码或追加 Provider 调用。对最近失败 Proposal 的两份原始 Recipe、
能力快照和生成/修复请求完成精确 checksum 回放；取证与反例 21 passed、3 failed，
三个红测均指向路由错误缺少可定位结构化反馈。没有放宽断言，结果不代表成功生成。
相关五文件现有回归 100 passed；与新增反例分开记录，不能代替全量或真实泛化门禁。
同时证实判空谓词误用、整条记录数值比较、3 处 Agent 输入类型错误，且修复控制结构未变化。
两领域合法非线性图可编译；谓词写反仍合法的反例确认需要独立业务效果验收。
E3 和 PR 总门禁仍未通过。详见
[路由闭环核验与建议](../audits/META_PLANNER_RECIPE_ROUTE_VERIFICATION_20260924.md)。
后续应统一局部语义预检、可定位反馈与紧凑修复上下文，不能仅再加一条库存样例提示。
建议尚未实施，不提前进入 V4，不增加调用预算，不自动审批或发布。

### 2026-09-23 修复准备边界后的真实复测

用户单独授权后，独立预览载入上一批修复，通过页面完成一次生成、三次 completion。
OpenRouter / DeepSeek 三次均 HTTP 200、stop，Provider usage 共 30561 tokens，无不确定派发。
新 Proposal `proposal_655bec0a57614dc39b59294a5ee70b1f` 已保存为 pending r1，但仍无效。
首次 Recipe 与唯一修复都停在 `recipe_lowering`：路由 `route_record` 缺少显式
`case_1 / case_2 / default`，将路由后继写成并行不能替代该映射。

本次验证了第三次派发和失败原图留存，没有复现上次准备异常；由于更早停在展开阶段，
不能声称覆盖了上次非空类型诊断的同一真实路径。资源/类型/编译阶段未执行。
页面 modelId 附带错误来自诊断占位图，不作为原图根因。
失败原图可读取、不可执行/批准；没有人工修复应用、下游调用或写入，合成活表仍为 0 条。

E3 和 PR 总门禁仍未通过。本轮不改生产源码、不追加调用；后续应离线核对路由描述与
修复反馈的契约闭环，避免对占位报错打补丁。详细回执、范围与未验证边界见
[语义收口核验末节](../audits/META_PLANNER_SEMANTIC_REPAIR_CLOSEOUT_20260923.md)。

### 2026-09-23 修复准备边界收口

用户授权继续本地收口；不包含新的 Provider 调用、实际业务写入、审批、发布或 PR。
工作树与基线不变，沿用 177 个既有修改/未跟踪路径，不重启当前预览器。

- 目标：将诊断安全 JSON 投影、修复准备失败留存与未派发计量作为同一可验证闭环。
- 限定五文件：`meta_planner_v2.py`、`generation_diagnostics.py`、新增
  `test_meta_planner_repair_preparation.py`、本文和语义收口审计报告。
- 先以真实类型校验驱动生成服务的红测，补充准备异常、敏感异常文本、失败图/Recipe 重启读取、
  无法保留的输入及派发后异常边界；再修改两个生产文件。原授权、Store 和协议版本不变。
- 修复只在模型调用之前捕获准备错误；不会把已经进入 completion 的异常改记为未调用，
  不扩大三次预算、不自动重试、不以字符串化任意对象代替受限诊断投影。
- 验收：隔离 runner 执行新专项，然后复跑生成、Recipe、失败恢复、显式修复、Headless、
  Authoring、NodeContract、Publish 等受影响矩阵；语法、前端生产构建、Diff 与敏感签名检查。
  真实复测和 E2 全量/上游门禁仍独立保留，不用离线绿测替代。
- 回退仅撤销本批提示投影与准备边界接入，保留既有失败产物可读；不删除调用回执或 Runtime 数据。

本批结果：已完成五文件收口。真实类型诊断、准备异常与派发后异常对照先红测；
最终专项 14 passed，70 文件受影响回归 1441 passed，前端四文件 50 passed，生产构建及三文件
AST 通过。独立 Sol 仅做受限静态审查，未发现可证实问题，不冒充独立运行验收。
未派发时保留首次安全产物且不增加修复 attempt；已经进入 completion 的异常不被此边界捕获。
原型测试中的接口假设与规范化断言修正、完整结果和 hash 已记录在语义收口审计报告末节。
没有真实调用、业务写入、预览后端重启、审批、发布、Commit、Push 或 PR；E2/E3 总门禁保持未通过。

### 2026-09-23 语义收口后的授权复测

- 使用已固定 E-G1 目标与同一安全资源范围，仅重启本任务后端并在真实预览器点击一次生成。
  两次 OpenRouter / DeepSeek completion 完整返回，Provider usage 共 15900 tokens。
- 第三次修复未派发：`_recipe_repair_prompt` 将非空 `GraphInputTypeIssue` 对象列表直接
  JSON 序列化，导致本地 TypeError。没有新增 Proposal，页面显示的仍是此前失败提案。
- 真实类型校验驱动的离线对照得到 1 passed / 1 failed，已复现相同根因；未修改生产源码。
  测试表仍为零记录、Schema 1，历史账本及生产源码 hash 未变。
- 结论：E3 仍失败。S1-S3 的 1427 项离线证据保留，但不能代替本次已失败的真实边界。
  下一修复建议一并收口诊断安全 JSON 投影与修复准备异常的失败产物留存，尚未实施。
  不追加模型调用，不执行写节点，不批准、发布或提交 PR。
- 具体时间、调用回执、红测命令与 hash 见
  [语义收口核验末节](../audits/META_PLANNER_SEMANTIC_REPAIR_CLOSEOUT_20260923.md)。

### 2026-09-23 生成与修复语义收口

用户确认针对系统薄弱点优化，而非继续给单个库存实例加补丁。本次在既有 E 工作树、
`codex/meta-planner-cw10-recovery-e@09e8a7d6` 上续作；开工时有 173 个既有修改/未跟踪路径。
只修改下列闭环，保留全部历史失败证据，不覆盖脏主工作区，不刷新或重建预览器。

| 子批 | 目标与允许范围 | 检查 | 状态 |
| --- | --- | --- | --- |
| S1 | 控制语义投影、生成反馈与组合反例；`control_flow`、`generation_recipe`、`generation_diagnostics`、Planner 入口及专项测试 | Runtime 真值语义一致；前置失败后的数据证明标记 blocked；字段/判空/分支专属数据组合证伪 | 已通过离线 60 项；生产与观察器共五文件为同一契约闭环，另加专项测试 |
| S2 | 新生成的唯一修复保持 Recipe 语义层；Planner、私有失败产物、修复辅助与专项测试 | 三次预算、严格解析、双工件绑定、完整重新校验、旧工件兼容 | 已通过离线 108 项；原 Graph Patch 仅作旧工件兼容，不增加自动修复次数 |
| S3 | 显式定向修复复用相同 Recipe 输入并由服务端派生 Graph Patch；模型修复、测试、必要安全投影和文档 | 一次明确授权、Patch 往返、漂移拒绝、预览零副作用、无自动应用 | 已通过专项 82 项及独立审查反例复核；集成同一次 69 文件回归 1427 passed，管理端 50 passed，生产构建通过 |

S3 独立只读审查补充：重复输入断开与派发前漂移均先以离线反例复现，再收口桥接器和发送守卫。
有序输入保留可复用前缀，删除按边键去重；仍不能在 64 操作内表达时明确阻断，绝不扩容或自动多次应用。
这组修改耦合语义桥接、既有 Patch 上限异常、调用服务、Transport、Managed 最终发送守卫与回执 UI，六个生产文件共同验证，
不改变公开 Patch Schema、预算、Store、审批或 Runtime。外发末次复核不是跨 Store/Provider 原子事务。

最终离线证据、首次回归失败及兼容夹具迁移见
[语义收口核验](../audits/META_PLANNER_SEMANTIC_REPAIR_CLOSEOUT_20260923.md)。各测试集合重叠，不相加。
本次未运行新源码的真实预览器/Provider、整个后端、hosted CI 或最新上游集成，前端全量未完成，
不覆盖既有 E2/E3 未通过门禁，也不授权提交 PR。

每个子批先运行重点测试，再集成下一子批；文档与证据同步作为独立收尾，不将实现、测试和
安全护栏拆成无法验证的半成品。不得修改 Runtime/表 Backend、公开 IR、SSE、调用上限或
审批边界，不增加生产依赖、Store 或节点。本次没有真实 Provider、活表写入、Apply、提交和 PR 授权。

验收先运行新组合反例及 Recipe/失败恢复/定向修复重点 pytest，再运行 Meta Planner 与
Headless/NodeContract/Publish 受影响回归、后端语法、前端检查和构建。E2 既有全量失败必须
单列；离线通过不代表真实成功率、泛化、效果或 PR 门禁通过。回退为恢复新生成修复的协议
选择与投影接入，保留旧 Proposal/Intent 和所有失败回执可读，不撤销业务写入。

### 2026-09-23 资源 Schema 收口补充

用户“开始收口”授权本地修复与离线验证，不是新 Provider 调用、业务写入或 PR 授权。
当前真实失败及最终检查统一记录在 [E-G1 报告](../audits/META_PLANNER_RECOVERY_E_G1_RETEST_20260923.md)。

1. R6：统一 Recipe/兼容提示的授权资源投影和私有 Schema；不改变公开 IR 或 Runtime。
   覆盖资源投影、两套生成 Schema、生成服务及三份相关测试；合法夹具显式声明授权与活动索引。
2. R7：增加仅资源 kind 枚举错误的安全保留和单一资源替换协议，复用现有私有产物、恢复与提交门禁。
   联动捕获、诊断、Recovery、Headless receipt 与模型修复拒绝入口，必须一起验证，不能只加保留而无审批护栏。
   本批不新建 Store，不把任意 Schema 错误或任意 JSON 变成可编辑草稿。
3. R8：前端资源修复投影、组件/浏览器验证与五处文档同步。真实失败不升级为通过，不修改历史账本。

资源枚举仍严格；保留草稿不是编译契约。最大三次初始生成预算、22 类能力、写入授权及人工审批不变。
跨 Store 原子事务不在此补充范围，Apply 乐观复核不取代审批/Runtime 再验证。

- 用户明确指示进入 E。本批没有沿用历史额度或活表写入授权；不自动批准提案、发布、提交或 PR。
- 源工作树仍为 `modelmirror-meta-planner-controlled-writes-10-closeout`，HEAD `a7d99925`，
  166 项修改/未跟踪、暂存为空。只冻结已确认范围，不修改原预览器、Runtime 数据和共享栈。
- 已抓取并固定 `origin/main@09e8a7d644f626b3e1aa606fcf148f360fc9aadc`，较源基线前进 26 个提交。
  上游 385 个路径与当前范围只在 `server/main.py`、`client/server.mjs` 相交；
  Provider 控制面存在传递依赖变化，不能仅凭无文本冲突判定兼容。
- E1 在新工作树 `C:/tmp/modelmirror-meta-planner-cw10-recovery-e`、分支
  `codex/meta-planner-cw10-recovery-e` 基于该上游建立集成副本。机械转移全部冻结产出，
  保存路径/SHA-256 与补丁；不修改源产出、不提交，冲突只在明确相交文件审查后处理。
- E2 使用隔离 Runtime、无凭据、拒绝外网的入口运行全量后端与前端、语法和构建。
  已知 Worker 断连及模型目录时间敏感断言先归因，不放宽检查，也不混入无关模块修复。
  新发现的本轮缺陷必须先复现，另记不超过五文件的单目标修正小批次。
- E3 冻结合成目标和多路径验收矩阵，建立正式独立预览器及帮助重放证据。
  真实模型、确切外发内容、调用上限和任何实际表写入逐项明确授权后才能执行；
  E1/E2 通过不是整个 E 或 PR 门禁通过，不提前进入路线下一轮。
- 风险：上游集成可能改变网关、超时或调用回执。回退为停止使用新集成副本，源工作树与
  A-D 冻结产出保留不变；不回滚业务数据、不删除回执、不重试未知 Provider 派发。

### E2 协议夹具修正小批次

- 限定 `test_meta_agent_managed_endpoints.py`、本文与
  `docs/audits/META_PLANNER_RECOVERY_E_INTEGRATION.md`；目标仅为使旧 Managed 端点夹具
  使用 C 已确认的 Recipe 协议，并验证协议降级仍被拒绝。不修改生产生成或执行逻辑。
- 原用例在纯上游通过、E 红测复现。迁移第三次模拟响应后，增加无协议标记及伪造 V1 标记
  两条旧完整图攻击；保留三次调用顺序、失败回执和禁止 legacy 回退断言。
- 同路径三文件最终 21 passed；端点 full-run 旧夹具失败与定向复跑分开记录，不篡改全量结果。
- 前端完整依赖后全量 1089 passed / 2 failed，纯上游复现两项失败；生产构建与本地代理
  11 项通过。帮助图片检查三项旧资产失败在纯上游同样复现，未删除资产或放宽门禁。
- 本批安全检查 168 路径、111 个 Python 文件；已知密钥模式与禁止产物 0 命中，暂存为空。
  详细证据与待授权泛化矩阵见上述 E 集成记录，不扩大原授权。

### E1/E2 结果与停止点

2026-09-20：固定上游 `09e8a7d6`，源 166 文件 hash 无漂移；E 分支保留 168 路径修改、
暂存为空，没有 Commit、Push、PR、真实模型、活表写入、审批、发布或共享栈操作。

- 后端全量：7814 passed / 118 failed / 69 skipped / 4 errors，3110.65 秒。
  同次全量中的原 65 文件关键矩阵为 1328 passed，不重复计数、不替代全量。
- 独立纯上游逐例复现 117 个 failed 用例及产生 4 errors 的两个用例；唯一 E 独有差异是
  上述 Managed 旧夹具，修正后定向 21 passed。全量旧夹具失败仍保留原记录。
- Coding、文件/MCP、Skill、多模态及 Plugin Hook 等基线失败分别记录环境、产品或未完全
  归因边界，不将它们全部概括为 Windows 问题。部分 Coding 复跑有波动，也未宣称稳定。
- 最终 111 个 Python 文件 AST 检查、`git diff --check`、已知密钥/产物检查通过；全量
  与帮助门禁未通过，故 E 不完成、不进入下一轮或 PR。测试进程已结束。
- E3 两个固定真实泛化目标及人工/显式模型修复矩阵已写入 E 审计文档，但正式预览、帮助
  重放和真实调用均未运行。应先处理 CI 等价环境与基线门禁，再单独确认外发内容和预算。
- 回退只停用 E 集成副本；A-D 产出、旧预览器和历史调用回执保留，不删除数据或自动重发。

### E2 Linux 核验补充

2026-09-22：仍处于 E2，不修改产品或测试断言，不自动放行基线失败。
详见 `docs/audits/META_PLANNER_RECOVERY_E_LINUX_VERIFICATION.md`。

- 固定 `09e8a7d6`，Python 3.12 / Node 22；独立容器断网、只读输入、不挂载共享数据。
  这不是 hosted CI，也未重跑整个 Quality workflow。
- 成对小矩阵各 123 passed / 3 failed；相同 126 项，源码、脚本与选择集已绑定。
- 首次 Linux 全量合计 8005 项：7964 passed / 9 failed / 32 skipped。三项音频为上游契约/
  fixture 差异，另四项缺 RPG 测试依赖、两项 OpenBLAS 线程资源问题；没有把后六项自动豁免。
- 补齐锁定依赖、固定 BLAS 单线程后纯上游六项通过；E 最终后端合计 8005 项，
  7970 passed / 3 failed / 32 skipped，无 errors，原 65 文件关键矩阵 1328 passed。
  只剩纯上游同败的三项音频断言；小矩阵不重复计数，动态 XLSX 参数 ID 差异单列。
- 本地 `origin/main` 引用已为 `23e6c165`，较已测基线前进 9 个提交；本次未 fetch/合入，
  后续提交门禁仍要求上游新鲜度审查，不能仅凭直接文件交集为空就认定集成通过。
- 音频目录、前端价格和帮助资产问题保留给对应模块独立处理；没有新增 Planner 补丁。
  E3 真实生成、人工/显式模型修复及隔离写入效果仍未开始，不沿用旧调用授权。
- 最终核验记录和命令均已收齐，本轮独立容器全部退出；只有三份文档变化，生产源码无漂移、
  暂存为空。E2 总门禁仍未通过，不提交 PR，也不提前进入下一轮。

## D 开工契约

- 用户明确指示继续 D。沿用 C 工作树与 `a7d99925`，开工 159 项修改/未跟踪、暂存为空。
  不覆盖 A/B/C，不刷新上游、不操作既有预览器、共享栈或真实业务表，不调用真实 Provider。
- D1 限定本文、`model_repair.py`、`authoring_store.py`、`test_meta_planner_model_repair.py`：
  复用失败原图和 Graph Patch 内核；预检展示外发内容、模型及独立的一次调用预算。
  授权绑定原图、Proposal revision、计划、资源/契约与外发 checksum；客户端不能扩大原授权。
- 尝试账本由现有 Proposal Store 的私有原子日志保存，不新建业务 Store，不改变旧快照字段或候选 payload/revision。
  派发前原子占用；同 request ID 只返回既有回执。结果不确定不自动重发，重启只恢复安全状态。
  每个提案最多保留 20 次显式尝试，达到上限即阻断，不删除未知派发以换取预算。
- D2 限定本文、`model_repair_transport.py`、`server/main.py` 和两个路由/传输测试文件：
  复用现有 Managed/legacy 入口，禁止自动降级/重试；路由身份变化使原确认失效。
  只增加可信管理侧修复预检、执行、回执读取接口，不新增业务 CRUD 或运行入口。
- D3 限定本文、`ModelDraftRepair.tsx`、对应测试、`FailedDraftRepair.tsx` 及其测试：
  独立勾选确认，返回建议必须人工载入编辑区；已有本地修改不被覆盖，预览与应用沿用 B。
- D2b 独立复核补强限定本文、`model_repair.py`、`model_repair_transport.py`、`managed_gateway.py`
  与 `server/main.py`：服务端签发 15 分钟确认凭据，绑定精确正文与预算，重启后未使用凭据失效。
  两条传输路径增加缺省关闭的正文 guard；D 派发时验证实际 body 与展示内容完全相同。
  回执防重复仍由现有单进程、单 Store 写者边界承担，不宣称多进程原子性。
- D4 每组最多五文件，补证伪/回归及帮助、Headless、MetaAgent 文档。界面离线验证不替代 E。
- D2c 独立审查的四条离线反例已复现：固定兼容网关端点与计费身份，正文准备后再复核，
  返回模型不符时保存已消费回执但拒绝建议；限定本文、修复 Service/Transport 与 main。
- D1b 限定本文、Proposal Store、修复 Service 与对应测试：私有回执改存于同一 Store 管辖的
  原子日志，不再写入旧提案记录字段。旧二进制回退不隔离提案，日志保留、不自动重放；
  日志损坏只关闭模型修复，不妨碍原提案读取。此调整不新增业务 Store 或迁移真实数据。
- 风险为新增付费调用入口与中断后的重复扣费；必须覆盖重复点击、并发、重启、未知结果、
  授权/Schema/路由漂移、正文泄漏与错误建议不可应用。追加调用不计入原初始生成的三次预算。
- 回退关闭 D 入口；A/B 人工修复与 C 编译保持可用，私有回执不删除、不自动重放。
  D 完成后停止，真实模型外发仍需另行明确授权，E 不随本批启动。

## D 结果与剩余门禁

2026-09-20：完成逐次授权的模型修复入口，只提供建议，不自动修改候选或执行工作流。
没有真实 Provider 调用，不据此宣称单模型生成成功率或泛化稳定性提高。

### 已实现的闭环

- 只读取 A 保留的失败原图，复用 B 恢复状态与现有 Graph Patch 内核。预检展示精确外发正文、
  模型、路由与独立一次调用预算；15 分钟服务端确认凭据绑定原图、revision、计划、授权、
  资源、计费身份、正文与预算，重启后未使用凭据失效。
- 执行前原子登记 request ID 和单次授权；同 ID 只读回执，换 ID 不能重用授权。
  只执行一次 completion，不自动重试或回退 Provider。派发前同时核对实际正文与当前路由。
- 结果只接受受限 Patch operations，经相同授权、类型、控制流与发布预检；状态漂移或返回
  模型不符时不提供可载入建议。已消费、未派发和结果不确定分别记录，缺少实际 usage 不估算。
- 建议必须人工载入编辑区，再独立预览、确认应用；已有本地操作不会被覆盖。
  预检、调用和查看回执都不改变 Proposal revision，不创建草稿、不审批、不运行写节点。
- 同一 AuthoringProposalStore 管辖私有日志，旧提案快照格式保持不变。最多 20 次尝试、
  单提案日志最多 2 MiB；中断派发恢复为 uncertain，不自动重发。日志损坏只关闭 D，
  不妨碍原提案读取。并发保证限于既有单进程、单 Store 写者，不宣称跨进程原子性。

### 证伪与修正

- 四条独立反例首次均失败：确认后凭据轮换、正文准备期间路由漂移、Provider 返回错误模型，
  以及新增私有字段破坏旧提案快照。分别固定路由计费身份、增加实际正文 guard、验证响应模型，
  并将回执放入同一 Store 管辖的私有日志；相同四条重跑通过。
- 另一条重启反例证实日志状态更新超限会误阻断整个 Store；补齐局部异常边界后，原提案集合
  保持不变，只有对应提案的 D 调用失败关闭。没有放松日志上限或忽略错误。
- 首次默认矩阵为 1324 passed / 3 failed；三个失败来自测试夹具实际创建两个提案而断言只有
  一个。改为精确比较重启前后 ID 集合，未改变生产行为；同路径五文件和默认矩阵均重新执行。

### 实际检查

| 检查 | 结果 |
| --- | --- |
| 修复 Service/API/Transport、Managed、completion 五文件最终回归 | 98 passed / 4 既有 FastAPI warnings，97.31 秒 |
| 默认 65 文件离线矩阵最终回归 | 1328 passed / 4 既有 FastAPI warnings，517.92 秒 |
| ModelDraftRepair、FailedDraftRepair、MetaPlannerV2、metaAuthoring、helpContent 五文件 | 最后文档同步后重跑 54 passed，16.63 秒 |
| `npm.cmd run build` | 最后文档同步后重跑通过，3182 模块，Vite 19.34 秒；既有大包超过 4500 kB 的 warning 保留 |
| 八个 Python 文件不执行应用的语法检查 | 通过 |
| D 的 16 个文件已知凭据模式扫描 | 0 命中，不宣称覆盖任意秘密形式 |
| `git diff --check` 与八个新增/任务文件空白检查 | 通过 |
| Sol 限定范围独立只读复核 | 所指出边界已闭合，末次未发现与修正直接相关的新 P1/P2；未执行测试 |

后端命令沿用隔离 runner；默认矩阵仍不是全量 `server/tests/`：

```powershell
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -u -B .tmp-cw10-closeout/run_offline_v2.py
```

五文件命令追加：

```text
server/tests/test_meta_planner_model_repair.py
server/tests/test_meta_planner_model_repair_api.py
server/tests/test_meta_planner_model_repair_transport.py
server/tests/test_meta_agent_managed_gateway.py
server/tests/test_meta_planner_completion_contract.py
```

前端命令（client 目录）：

```powershell
node node_modules/vitest/vitest.mjs run --configLoader runner --maxWorkers=1 --fileParallelism=false src/components/meta/ModelDraftRepair.test.tsx src/components/meta/FailedDraftRepair.test.tsx src/components/meta/MetaPlannerV2.test.tsx src/components/meta/metaAuthoring.test.ts src/content/help-center/helpContent.test.ts
npm.cmd run build
```

### 离线浏览器证据

- 新增独立 15479 前端组件夹具，没有后端代理、Provider 或凭据。所有响应均在浏览器内模拟，
  页面明确展示此限制。未改动原 15459/15469 预览器或共享栈。
- 实际操作验证：查看外发内容后仍需勾选确认；模拟调用完成时本地操作仍为零，预览/应用
  计数均为零；人工载入后才出现一项操作，随后独立预览，再显式执行模拟应用。
- 在 390x844 视口检查换行，实际 scrollWidth/clientWidth 均为 380，无横向溢出。
  结束恢复视口、重载为初始状态，保留明确标记离线的预览页。
- 地址：`http://127.0.0.1:15479/.tmp-recovery-d-preview/index.html`。
  这不是生产后端集成、真实模型、完整帮助教程或实际业务写入验收。

### 保留门禁与回退

- D 不启动 E。全量 `server/tests/`、最新上游集成和真实泛化/效果验收尚未执行；
  A 的全量 Worker 断连门禁仍待 E 处理，不能被本批 1328 项矩阵代替。
- 原 `calls.json`、`launch.py`、`verify.py` 三份冻结材料 SHA-256 不变；工作树共有 166 项
  既有及本批修改/未跟踪路径，暂存为空，待提交路径不含 Runtime 数据、SQLite、构建产物、
  预览夹具或凭据文件。独立预览夹具和 dist 均验证由既有忽略规则排除。
- 本批没有真实模型调用、活表写入、审批、发布、Commit、Push、PR 或共享栈操作。
- 回退只关闭 D 的路由与界面入口，保留私有日志和 A/B 人工恢复路径；不得删除未知派发
  回执来换取新预算，也不得自动重放。真实模型及后续效果验收仍需明确授权。

## C 开工契约

- 用户于 B 完成后授权进入 C。沿用上述独立工作树，HEAD 为 `a7d99925`；开始有 150 项改动，
  不重置此前产出。不调用真实 Provider，不写业务表，不操作既有预览器、共享栈或提交。
- C1 限定本文、`generation_recipe.py`、`test_meta_planner_generation_recipe.py`：
  私有生成描述只表达节点配置、命名来源及结构化顺序/分支/并行；输出变量、端口 Schema、
  原生控制边由既有 Adapter/NodeContract/资源解析器派生，仍降低为 GraphIntent V3。
- 模型继续决定业务条件、数据源、任务归属、顺序/分支和最终来源。编译器不得补业务步骤、
  猜接边、自动加空值保护或将分支串行化。Deserialize expected_schema 是待验证业务契约，
  不是可删去的机械类型；Aggregator 字段与输入顺序保持显式语义。
- C2 限定生成模块、`meta_planner_v2.py`、生成集成测试与本文：正式生成使用简化描述，
  解析后仍走原授权、类型、路径、编译和发布预检；已解析 Intent 的唯一修复继续使用既有
  Graph Patch 内核，不新增模型轮次。旧 GraphIntent/Headless/Runtime 读取不变。
- C3 每次不超过五文件，补固定跨目标矩阵、独立复核与文档。离线验证不证明真实模型成功率。
- 风险为生成格式切换与控制语义翻译；以精确边集合、类型来源、负例拒绝、候选往返和预算测试
  验证。回退只恢复旧生成 Prompt/解析选择，已保存的 GraphIntent、Proposal 与 Runtime 不迁移。
- 不引入 R9 Requirement IR、模式库或 Planner/Binder/Reviewer，不新增表达式引擎、节点或权限。
- C4 每组最多五文件迁移历史测试夹具：新模型入口必须返回 Recipe；原 GraphIntent 的机械故障
  只能作为明确命名的 `LegacyGraphReplayService` 兼容回放，不能冒充新协议或 Provider 验收。
  测试专用子类只跳过模型格式选择，不跳过任何权限、类型、资源、路径或发布校验。
  真实生成、transport/evidence、预算测试保留生产 Service，夹具改用新协议；不全局替换。
  新增降级攻击及 `[True] / [True, True] / [True, False]` 调用边界测试防止掩盖入口回归。

## C 实施与验证记录

2026-09-20：仅改变生成描述到既有 GraphIntent 的确定性编译分工，详细契约见
[简化生成描述 V1](../META_PLANNER_GENERATION_RECIPE.md)。不声称真实生成已稳定。

### 本批范围

- 新 `generation_recipe.py` 从现有 Adapter 配置、NodeContract 和资源快照派生输出 Schema、
  稳定变量及控制边；模型保留来源、业务条件、任务、显式顺序/分支/并行和最终来源选择权。
- `meta_planner_v2.py` 的首次生成与无法降低时的完整修复使用 Recipe；已降低的语义失败
  使用原 Graph Patch。旧图默认编译、Headless、审批和 Runtime 不变，三次预算不增加。
- `generation_diagnostics.py`、`generation_evidence.py` 仅扩展新修复阶段和安全结构字段，
  原观察是被动且有界的，不把降低后的图冒充模型响应。
- 当前生成入口严格要求新协议；省略标记不能降级。已保存的 V2/V3 候选无须迁移。
- 测试新增 Recipe 内核、真实 Service、跨 Adapter、跨业务字段矩阵及两种模拟 transport。
  15 个历史故障测试文件显式采用 `LegacyGraphReplayService`，仅用于旧 GraphIntent 回放。
  新入口、传输、预算、完整修复与降级攻击均使用真实 Service，不使用全局 monkeypatch 替代门禁。
- 文档同步 MetaAgent、Headless、帮助文章及本文；不修改通用画布、节点配置、Evaluator 或 Runtime。

### 证伪与修正

- 独立复核的三条红测复现动态谓词在输入派生前被校验、节点 `config` 未强制显式填写。
  保留相同资源校验，只移动到完整输入建立之后；新增 Schema 与实际解析共同拒绝缺失 config。
- 另两条红测复现默认生成退回完整 GraphIntent、完整修复仍误标 `graph_intent_v3`。
  首次及完整修复强制 Recipe，Patch 后的服务端重编译保持旧图兼容；新标签为 `generation_recipe_v1`。
  测试直接断言编译调用的 `[True]`、`[True, True]`、`[True, False]` 边界。
- 正向矩阵先通过，再施加错误：空值绕过、分支独有输入、无序并行写入、跨表、伪造记录、
  any 收窄、Handle/版本/变量注入及控制流遗漏均拒绝，不以一个先天无效的夹具证明门禁。
- 开发过程中还修正了测试观察回调、模拟资源 Schema、数据类断言及辅助模块循环导入；
  这些测试基础问题不计为产品缺陷。相同失败命令均重跑，不删测试或放松生产校验。

### 实际检查

| 检查 | 结果 |
| --- | --- |
| 新生成、预算、legacy/managed 模拟传输、跨 Adapter 六文件 | 110 passed / 4 既有 FastAPI warnings，85.95 秒 |
| 默认 62 文件矩阵首次 | 1289 passed / 1 failed；唯一失败是完整修复仍断言旧协议标签 |
| 修正该用例为真实 Service + 新协议，重跑相同 62 文件矩阵 | 1290 passed / 4 既有 FastAPI warnings，442.45 秒 |
| 帮助内容修正后重跑 `npm.cmd run build` | 通过，3181 模块，Vite 构建 18.52 秒；既有大包超过 4500 kB 的 warning 保留 |
| MetaPlannerV2、FailedDraftRepair、metaAuthoring、helpContent 四文件首次 | 43 passed / 1 failed；B 批 FAQ 增加编号导致总数 12，超过既有 8 步约束 |
| 保留内容，将 FAQ 合并为载入编辑、预览应用两步后重跑 | 44 passed，13.67 秒；未放宽帮助测试 |
| 四个生产模块和 `server/main.py` 非执行语法检查 | 5 文件通过 |
| 本批 11 文件已知凭据模式扫描 | 0 命中，不宣称覆盖任意秘密形式 |
| Sol 限定四生产文件末次独立只读复核 | 未发现未闭合 P1/P2；未运行测试，不作为独立复测 |

六文件命令使用 `run_offline_v2.py`，显式追加：

```text
server/tests/test_meta_planner_recipe_integration.py
server/tests/test_meta_planner_v2.py
server/tests/test_meta_planner_generation_boundary_audit.py
server/tests/test_meta_planner_generation_evidence_integration.py
server/tests/test_meta_planner_task_graph_contract.py
server/tests/test_meta_planner_recipe_adapters.py
```

完整本批矩阵不传文件参数；它包含所有 `test_meta_planner*.py` 及原先指定的 Workflow、
NodeContract、Authoring、Publish、Evaluator、Evolution、App 相关文件，不等于全量 `server/tests/`。
其中隔离 SQLite 的真实效果覆盖缺记录、低分更新和高分不写入；Agent 响应仍是模拟的。

最终后端命令（工作树根目录）：

```powershell
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -u -B .tmp-cw10-closeout/run_offline_v2.py
```

前端命令（`client` 目录）：

```powershell
node node_modules/vitest/vitest.mjs run --configLoader runner --maxWorkers=1 --fileParallelism=false src/components/meta/FailedDraftRepair.test.tsx src/components/meta/MetaPlannerV2.test.tsx src/components/meta/metaAuthoring.test.ts src/content/help-center/helpContent.test.ts
npm.cmd run build
```

### 保留门禁与回退

- 本批没有真实 Provider 调用，不能据此声明生成成功率、口语需求泛化或真实模型鲁棒性。
- 未重新运行全量 `server/tests/`，未刷新上游或重启预览器；A 的 Worker 断连门禁仍待 E 处理。
- 原 `calls.json`、`launch.py`、`verify.py` 三份冻结材料 SHA-256 与开工时相同。
- 收尾工作树共 159 项已修改或未跟踪路径，包含此前各批产出；暂存为空，
  `git diff --check` 通过，待提交路径不含 SQLite、Runtime Store、构建产物或凭据文件。
- 回退仅恢复旧生成格式选择；持久化 GraphIntent、Proposal、失败产物及 Runtime 均不迁移。
- 无 Commit、Push、PR、审批、发布、共享栈操作或活表写入。下一批 D 仍需用户指示，
  其真实模型调用还需要独立、明确的外发内容和额度授权。

## B 开工契约

- 沿用 A 工作树与 HEAD，开始有 145 项修改/未跟踪，暂存为空。
- B1 限定五文件：本文、`failed_recovery.py`、`headless_authoring.py`、`server/main.py`、
  `test_meta_planner_failed_recovery.py`。复用既有四个 Headless 路由，不新建 Store。
- B2 为前端读取、诊断定位、人工 Patch 编辑、校验/Diff/确认应用及对应测试；单小批最多五文件。
  最后独立同步帮助和验证证据。不进入 C 的生成协议调整或 D 的付费修复。
- B2 限定 `FailedDraftRepair.tsx`、`FailedDraftRepair.test.tsx`、`MetaPlannerV2.tsx`、
  `MetaPlannerV2.test.tsx` 与本文，复用现有样式与 Graph Patch 类型。不编辑通用画布。
- 安全管理侧可以读取 A 已保留的原始 Intent，但不得将它当作 Native Workflow 或 Resolved IR。
  旧提案没有原图时明确不可恢复；不从占位图编造原图。
- 人工编辑使用现有 Graph Patch 内核。中间操作仅保留在当前编辑区，刷新前需要完成修复；
  Preview 无持久化，只有全部现有校验通过后 Apply 才原子增加一次 Proposal revision。
  不另建可执行草稿或放松 `_state` 对正常候选的校验。
- 原始图/诊断、当前本地复核和占位候选三种来源分开显示；只在结构化诊断提供位置时定位。
- Preview 绑定原始产物、当前 Proposal、候选、计划、授权、能力/资源和目标草稿；Apply 重算，
  漂移返回冲突。普通详情仍不返回私有失败正文，已应用候选继续走原 Headless 流程。
- 验证：离线 recovery/failed-artifact/Headless/Authoring 反例、Meta Planner 矩阵、前端测试和构建。
  本批禁止真实模型调用、业务记录操作、审批、发布、共享栈、提交与 PR。
- 回退：移除 recovery 模式入口和前端入口，原有 Headless 路径与 A 私有证据保持不变。

### B1 验证记录

- B4 同步旧回归断言、帮助与界面文案：限定本文、`test_meta_planner_write_generation_failures.py`、
  `FailedDraftRepair.tsx`、`MetaPlannerV2.tsx`、帮助文章 `review-meta-planner-branches.md`。
  旧 GET 422 调整为可读但不可审批的 recovery 响应，不放松普通 Patch 拒绝断言。

- B3 为独立复核发现的安全边界纠正，限定本文、`failed_recovery.py`、
  `headless_authoring.py`、`authoring_service.py`、`test_meta_planner_failed_recovery.py`。
  验证普通入口绕过、跨提案预览重放、无效修改、诊断字段夹带和最终校验中的资源漂移。
  最后一次资源复核在 Authoring 提交锁内执行；不宣称多个领域 Store 已形成跨库事务。
  真正审批和 Runtime 仍独立校验资源，人工修复不会运行候选。

- 先修正测试夹具缺少 preflight 的错误，再证实当前失败图 GET 被 lossless round-trip 门禁阻断。
- 首次实现三文件回归 76 passed / 1 failed：预览绑定误包含 generated_at，导致无修改也冲突。
  排除该非语义字段，保留完整安全 Snapshot 其余字段绑定；新增同输入重复预览 checksum 测试。
- 扩展路由测试首次 91 passed / 1 failed：测试的 Provider tripwire 同时阻断内存 TestClient；
  仅允许此实例的 ASGI transport，外网和其他客户端仍禁止。随后发现并修正新增测试断言误置。
- 重跑相同三文件：92 passed / 4 既有 FastAPI warnings，45.96 秒。
  命令为隔离 runner + failed_recovery、failed_artifacts、headless_authoring 三个测试文件。
- GET 当前复核、Patch Preview、Apply 均未产生模型调用或业务 CRUD。原始失败证据不变，
  Apply 仍通过 Authoring 最终校验，只更新 pending Proposal，不创建 Xpert。
- B2 所需安全节点端口/字段名投影随后加入，需随集成矩阵再验证，不冒充上述 92 项已覆盖。

## B 结果与剩余门禁

2026-09-20：本批实现人工修复，不改变模型生成策略，不声称真实生成稳定性已经提高。

### 已实现的闭环

- 新失败图从 A 的私有产物加载；原始尝试、修复输入、当前复核分开，未执行阶段不伪装通过。
- 原始图不可覆盖；人工操作暂存在页面，复用严格 Graph Patch 内核，预览不落盘。
  仅真实修改且完整校验通过才允许 Apply，增加一次 revision；没有审批、运行或业务写入。
- 失败提案不能通过普通 preview/apply/editor-diff 编辑占位候选；未保留原图的旧提案明确不可恢复。
- 预览凭据绑定 Proposal ID/revision、产物、计划、授权、资源、候选与目标草稿。
  最终校验后的锁内复核发现漂移即拒绝。独立资源 Store 不是跨库事务，审批和 Runtime 仍须重验。
- 页面提供节点定位、语义配置与连接编辑、完整 Patch 编辑、预览 Diff 和显式应用。
  修改后旧预览失效；冲突锁定后必须重新加载。失败期间不开放评测、批准或旧候选保存按钮。

### 命令与证据

| 检查 | 实际结果 |
| --- | --- |
| 独立复核后五条定向证伪 | 首次 5 failed，证实边界缺口；修正后纳入下列同路径回归 |
| recovery/artifact/headless/authoring 四文件 | 110 passed / 4 既有 warning，53.40 秒 |
| 默认 58 文件离线矩阵首次运行 | 1223 passed / 1 failed；旧 GET 422 断言不适用于本批 recovery 响应 |
| 更新该用例，增加不可审批/不可执行/不返回占位候选及普通入口拒绝断言，重跑相同矩阵 | 1229 passed / 4 既有 warning，307.05 秒 |
| MetaPlannerV2、FailedDraftRepair、metaAuthoring 前端测试 | 30 passed，16.44 秒 |
| `npm.cmd run build` | 通过；仍有单包超过 4500 kB 的构建 warning，不在本批做拆包 |
| 六个 Python 文件 `compile(..., 'exec')` | 通过，不导入应用或运行模型 |
| `git diff --check` 与本批新文件空白检查 | 通过 |
| 本批 12 文件已知凭据模式扫描 | 0 命中，不宣称覆盖任意秘密形式 |
| Sol 限定四文件独立只读复核 | 五项闭合，未发现新 P1/P2；子智能体未执行测试，不冒充独立复测 |

后端使用本文件前述 `run_offline_v2.py`。四文件回归追加参数：

```text
server/tests/test_meta_planner_failed_recovery.py
server/tests/test_meta_planner_failed_artifacts.py
server/tests/test_meta_planner_headless_authoring.py
server/tests/test_xpert_runtime_authoring.py
```

前端测试命令：

```text
node node_modules/vitest/vitest.mjs run --configLoader runner --maxWorkers=1 --fileParallelism=false src/components/meta/FailedDraftRepair.test.tsx src/components/meta/MetaPlannerV2.test.tsx src/components/meta/metaAuthoring.test.ts
```

### 隔离浏览器操作

- 使用忽略目录中的专用夹具，前端 15469、后端 16469；未重启 15459 或共享栈。
  该页面挂载真实 `FailedDraftRepair` 组件并调用真实管理侧路由/编译/Apply，
  Capability 与发布 preflight 使用确定性测试夹具，不是完整生产 Meta Agent 验收。
- 后端清空凭据环境、禁用 dotenv、拒绝非 loopback 网络和 httpx Provider 请求，
  Agent Table 业务记录方法设置拒绝入口；仅允许此合成提案的 GET/Preview/Apply。
- 提案 `proposal_132135534a9340939a78897550804a2c`：加载失败原图、定位 write、
  补充 `lookup.result -> write.records`、预览通过。预览后 revision 仍为 1。
- 两窗口均基于 r1 预览；第一窗口确认应用返回 200，r2/valid；第二窗口应用旧预览返回 409，
  保留本地操作并锁定。重新加载看到 r2，没有第三次 revision 或 Xpert 创建。
- 桌面 1280x720 与移动 390x844 观察控件与换行；实际 scrollWidth 分别为 1270/380，
  无横向溢出。恢复临时视口，关闭重复窗口；保留一个明确标记离线的结果页。
- 这些是浏览器操作与确定性编译证据，不是新一轮真实模型或实际写入效果证据。

### 保留边界

- 新失败原图才能恢复；此前未保存正文的真实失败提案不能凭空还原。
- 中间人工操作不跨刷新持久化；完整画布修复和自动修复均不在本批实现范围。
- 本批未重新执行全量 `server/tests/` 或全量前端测试，未刷新上游，未完成真实生成泛化门禁。
  A 记录的全量 Worker 断连失败仍是待处理的独立门禁，不能被 1229 项矩阵代替。
- 原有真实调用 calls.json、launch.py、verify.py 的 SHA-256 与 A 结束时一致。
  工作树有 150 项既有及本批改动/未跟踪，暂存为空；没有 Commit、Push、PR、审批或发布。
- 下一批为 C：生成协议与确定性编译分工。只有用户继续指示后推进，不在本批顺带修改 Prompt。

## A 开工契约

- 工作树 `C:/tmp/modelmirror-meta-planner-controlled-writes-10-closeout`。
- 分支 `codex/meta-planner-controlled-writes-10-closeout`，HEAD `a7d99925584e818e7f2df84b1646256abfc08ed6`。
- 开始有 141 项修改/未跟踪路径，暂存为空；本批不覆盖此前改动。
- 已证实：`MetaPlannerV2Service.generate` 用 fallback 替代不可编译图，未持久化失败 Intent；
  Headless `_state` 要求编译通过后才允许编辑；前端仅遍历 validation.stages，遗漏顶层 issues。
  后两项属于 B，本批不得通过移除 Headless/审批校验伪装修复完成。
- 允许五个文件：`server/meta_agent/failed_artifacts.py`、`server/meta_agent/meta_planner_v2.py`、
  `server/xpert_runtime/authoring_store.py`、`server/tests/test_meta_planner_failed_artifacts.py`、本文。
- 不修改 Runtime、节点授权、验证器判定、模型 Prompt、前端、Provider、依赖或部署。
- 风险：中，新增本地私有作者数据保留；不新增 Store、路由或模型调用，不迁移旧提案。
- 失败图是严格解析的、仍不受信任的 GraphIntent，不是 ResolvedGraphIR 或运行配置。
  必要节点配置/Prompt 仅保存在现有私有 Proposal 数据中；普通序列化与报告不返回正文。
  原始 completion、隐藏推理、凭据不保留。未解析、超限或敏感内容只保留安全原因与诊断。
- 诊断绑定原始生成或唯一修复的图、角色及 checksum；未执行阶段沿用 blocked，
  不根据错误先后猜测因果。旧提案未保留原图时明确不可恢复。

## 验证与回退

采用现有离线 runner，隔离 Store，清除凭据并拒绝外网：

```powershell
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -B `
  .tmp-cw10-closeout/run_offline_v2.py server/tests/test_meta_planner_failed_artifacts.py
```

先建立反例，再实现并运行生成、Headless、Authoring 与审批相关回归。完整交付仍要求
全量后端、前端生产构建、帮助中心与真实预览验收，不能由 A 的绿测替代。
上一批全量在独立 Worker 断连测试失败，94 passed / 1 failed / 其余未运行，不能称为全绿。

回退仅移除生成流程的捕获接入；保留 Store 的私有字段屏蔽和原始产物，避免丢失证据。
不撤销业务写入，不清理 Proposal，不改变活动资源指针。原有读、审批与执行边界不放宽。

## A 验证结果

2026-09-20：仅完成 A 的基础，不等于 CW10 收口或 PR 门禁通过。

### 已实现

- 在原 Proposal payload 中增加服务端专用 `_meta_planner_generation_artifact`，不增加新 Store
  或数据迁移。普通 Proposal 序列化（含详情）、生成响应、诊断报告均不返回其中正文。
- 仅最终生成失败时落盘，最多保留首次图和一次修复；初始成功与修复成功不复制私有正文。
  每份 Intent 最多 1 MiB，整体沿用 Headless 2 MiB 限制与 32 层深度约束。
- 使用当前 GraphIntent、Adapter 配置字段和已有凭据检测，不新建执行契约或类型权威。
  只解析成功但配置值、依赖、控制流等无效的图可以保留；未解析、未知配置字段、敏感内容、
  非 JSON-safe、超限情况明确不保留正文，不静默裁剪后假装仍是原图。
- 每次尝试独立绑定 Intent/诊断 checksum 和诊断对象。Patch 原子失败保留其输入图，
  `result_intent_checksum=null`；确实重编译后失败才保存新图。未执行阶段继续为 blocked。
- 绑定 Proposal revision、固定计划、授权、生成配置、能力快照和候选 checksum；旧整包编辑
  保留原始证据但让恢复读取 stale，不能替换证据。旧提案没有原图时返回 not_retained。
- Proposal 创建前持久化失败不会遗留新的内存提案；已有审批拒绝与 Headless 拒绝原样保留。

### 实际命令与结果

| 检查 | 结果 |
| --- | --- |
| 初次默认沙箱测试进程 | 未完成且无输出，已终止；不作为红测或通过证据 |
| 隔离 runner 的首组用例 | 8 passed |
| 扩展反例与六文件回归首次运行 | 144 passed / 2 failed；一个夹具未到目标阶段，一个发现 NaN 被 JSON-mode 静默转为 null |
| 校正夹具并改为转换前校验，重跑相同六文件命令 | 146 passed / 4 warnings，46.13 秒 |
| 增补信封格式、总量上限后运行 runner 默认矩阵（57 文件） | 1206 passed / 4 warnings，264.65 秒 |
| 四个 Python 文件不执行应用的 `compile(..., 'exec')` 语法检查 | 通过 |
| `git diff --check` 与三个新文件空白检查 | 通过 |
| 五文件已知凭据模式扫描 | 通过，0 命中；不宣称可以识别任意形式的秘密 |
| 本批全量 `server/tests/`、前端构建、真实 UI/Provider | 未运行；不是本批基础的通过声明，更不是整轮验收通过 |

六文件命令使用同一隔离 runner，参数为：

```text
server/tests/test_meta_planner_failed_artifacts.py
server/tests/test_meta_planner_generation_diagnostics.py
server/tests/test_meta_planner_write_generation_failures.py
server/tests/test_meta_planner_v2.py
server/tests/test_xpert_runtime_authoring.py
server/tests/test_meta_planner_headless_authoring.py
```

57 文件矩阵执行：

```powershell
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -u -B `
  .tmp-cw10-closeout/run_offline_v2.py
```

上述都是离线测试，包含模拟 Provider 和临时 Store；不冒充实际模型成功率或业务效果证据。
四条 warning 来自既有 FastAPI on_event 弃用提示。测试进程均已结束，无未完成的测试会话。

### 保留边界与下一批

- 本批恰好五个文件；工作树现有 145 项改动/未跟踪，暂存仍为空。
- 最近冻结的 119 文件中只有本批生成服务发生预期变化；另修改原先干净的 Authoring Store，
  新增模块、测试与本文。11 份历史账本和最近一次 calls.json 的 SHA-256 均不变。
- 没有读取凭据、重启预览器、操作共享栈、真实模型调用、业务写入、审批、发布、Commit、Push 或 PR。
- 页面尚不能加载失败图，现有旧失败图也不能凭空恢复。B 必须先从服务端私有产物建立安全的
  管理侧读取、诊断展示和编辑状态，再接人工校验/Diff；不能用移除现有 `_state` 校验代替该设计。
- A 在此停止。生成策略减负、额外模型修复授权和真实泛化矩阵尚未实施，不宣称成功率改善。

## 受限语义修复收口开工契约

- 基线：`codex/meta-planner-cw10-recovery-e`，HEAD `09e8a7d6`，已有 185 项修改/未跟踪，暂存为空。保留全部既有工作与真实调用证据。
- 目标：首次生成仍为 Recipe；已解析失败只请求局部语义编辑，服务端原子合并后复用原编译与安全门禁。未解析的描述保留一次严格全文修复，调用总预算不变。
- 第一小批：新增 `server/meta_agent/recipe_edits.py`、对应测试及本任务记录，验证有界协议、未知字段拒绝、原子性及未修改部分保持。
- 第二小批：修改 `meta_planner_v2.py`、`model_repair.py`、`generation_evidence.py` 并增加集成测试，自动与显式授权修复共用内核；实际模型输出与服务端合并产物分开取证。
- 第三小批：更新受影响的旧测试协议夹具、Recipe 文档和用户帮助，逐组运行相关回归；每组不超过五个文件。
- 允许修改已有节点的语义配置/输入、控制树及最终来源，允许复制无资源绑定的 Agent 以实现分支局部消费。固定原节点 kind/ref、任务、资源、模型身份；不支持删除原业务节点、新增资源读写、变更授权。
- 禁止修改 Runner、SSE、IR、Store 格式、权限和审批规则；不调用真实模型、不读取凭据、不写活表、不重启预览、不操作共享栈、不提交或创建 PR。
- 验收：隔离 `run_offline.py` 先运行新增测试，再运行 Recipe、显式修复、证据采集与 Meta Planner 相关回归；补充语法、Diff 和敏感信息模式检查。真实 Provider 与整轮门禁另列，不以离线通过替代。
- 风险：中等，仅影响一次修复的模型输出契约。无数据迁移或生产依赖；回退修复入口即可，旧 Proposal/失败产物及人工编辑继续可读。

## 受限语义修复收口结果

2026-09-27：此小批实现与离线验证通过，CW10 整轮仍未通过真实泛化与 PR 门禁。

### 实现与证据

- `recipe_edits_v1` 仅允许修改原节点的显式语义字段、复制无绑定 Agent、替换结构化控制树和设置最终来源；最多 16 操作、64 KiB。禁止替换 kind/ref/任务/资源/模型身份、删除原业务节点或新增资源读写节点。
- 服务端原子合并，未修改内容保持原值。空操作是合法的放弃信号，但与原样修改一样返回 `RECIPE_REPAIR_UNCHANGED`，绝不记为成功或触发重试。
- 自动唯一修复与显式一次授权修复共用内核；后者仍须配对 Recipe/Intent、复核外发正文与授权，再经过既有 Graph Patch 和 Preview。请求重放只读回执，不重复派发，不自动应用。
- 模型操作与服务端合并产物分别取证，不能把服务端重建的 Recipe 当成 Provider 输出。旧无配对 Recipe 的 Intent 修复、未解析全文修复和失败产物读取继续保留。
- 同步前端修复协议标签、帮助文章及 Recipe 文档。未增加页面、审批行为、运行能力、生产依赖或模型调用。
- 对上一真实失败提案 `proposal_9688bb091d1b463c89b614b62f3c8ef2` 做只读回放，Capability Snapshot 与原报告完全匹配。原错误仍可复现；人工构造的三项语义修改通过授权、类型、路径、编译与 Native 校验，三个符号场景保持预期。全部原节点和绑定不变，保护文件 hash 不变。
- 该样本的用户提示正文从 40,448 字符减至 22,752 字符，减少 43.75%。这是人工反事实和字符计数，不是模型新输出、实际 Token 或生成稳定性证明；没有调用发布预检回调或执行工作流。

### 验证记录

使用既有隔离 `.tmp-recovery-e/run_offline.py`：清除凭据环境、停用 dotenv、隔离临时 Store、拒绝外部网络。模拟 Provider 只证明协议和服务实现，不证明真实 Provider 成功。

| 检查 | 结果与解释 |
| --- | --- |
| 新内核开工红测 | 缺少 `recipe_edits` 模块而失败，随后实现；默认沙箱无输出进程已终止，不作为测试证据。 |
| 首次 Recipe/修复重点回归 | 364 passed / 12 failed；旧全文修复夹具与新协议不符，并发现未变化专用诊断映射缺失。修正协议夹具及诊断映射，没有放宽安全门禁。 |
| 首次扩大回归 | 1574 passed / 7 failed；余下两份测试仍返回整份 Recipe 或断言旧协议，随后迁移实际响应和阶段断言，保留 Legacy/Managed、预算、证据校验和零业务写入检查。 |
| 新显式分支修复夹具 | 初次 34 passed / 2 failed，初始化 Schema、图与授权来自不同业务域；统一夹具后同文件 36 passed / 4 warnings，37.31 秒。生产权限检查未变。 |
| 最终扩大回归 | 75 个文件，1583 passed / 4 warnings，693.13 秒。包含新增 36 项，不重复累加。 |
| 前端相关组件 | 4 文件、51 项通过，14.77 秒。 |
| `npm.cmd run build` | 通过；保留大 chunk 告警，不修改阈值。 |
| 15 个 Python 文件 `compile(..., 'exec')` | 通过；不导入应用、不生成 pyc。 |
| Diff 与本批 20 文件敏感签名扫描 | `git diff --check` 通过；已知凭据模式 0 命中，不宣称检测任意秘密。 |

最终后端命令：

```powershell
$tests = @(rg --files server/tests -g 'test_meta_planner*.py')
$tests += @(
  'server/tests/test_meta_agent.py',
  'server/tests/test_workflow_node_contracts.py',
  'server/tests/test_xpert_runtime_authoring.py',
  'server/tests/test_xpert_publish.py',
  'server/tests/test_xpert_evaluations.py',
  'server/tests/test_xpert_structure_evolutions.py',
  'server/tests/test_xpert_app_api.py',
  'server/tests/test_workflow_typed_values.py',
  'server/tests/test_workflow_typed_ai.py'
)
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -u -B `
  .tmp-recovery-e/run_offline.py @tests `
  --junitxml=.tmp-recovery-e/recipe-edits-affected-final.xml
```

runner 的 `TEST_FILES 76` 包含一个 JUnit 参数，实际测试文件为 75。四条 warning 来自既有 FastAPI `on_event` 弃用提示。针对性结果另存 `recipe-edits-kernel-final.xml`；前面的失败记录保留，不改写为通过。

前端命令为 `npm.cmd run test -- --run`，指定 `ModelDraftRepair.test.tsx`、`FailedDraftRepair.test.tsx`、`MetaPlannerV2.test.tsx`、`metaAuthoring.test.ts` 四文件，再加 `--maxWorkers=1 --fileParallelism=false`；随后独立执行生产构建。

### 未完成边界

- 本批只闭合受限语义修复内核及其两个入口；审查发现的“占位候选的缺失模型诊断混入原生成失败”仍需下一独立小批处理，不算本批修复成果。
- 全量 `server/tests/`、最新上游集成、同版预览器重放与真实模型泛化复测均未运行；最新真实失败仍不能改记为成功。
- 预览器没有重启，仍可能使用旧后端；不能用当前页面结果证明本批代码。后续真实调用须重新明确外发内容、模型和预算，不能沿用已消费授权。
- 工作树保持 `codex/meta-planner-cw10-recovery-e`、HEAD `09e8a7d6`；187 项修改/未跟踪中仅新增本批内核和测试两路径，未覆盖开工时 185 项既有工作。暂存为空，未发现 SQLite、Runtime 数据、日志、上传或构建产物进入待提交清单。
- 无真实模型调用、业务表写入、Proposal 批准、Xpert 发布、共享栈操作、Commit、Push 或 PR。所有本批测试进程已结束。
- 回退仅停用新修复协议选择，保留既有 Proposal、失败产物与回执读取；外发协议变化让旧授权 checksum 失效，必须重新确认，不自动重放调用或撤销业务写入。

## 诊断来源隔离复测开工契约

2026-09-27，用户授权隔离复测。本批不继承真实模型调用授权。

- 基线：`codex/meta-planner-cw10-recovery-e`、HEAD `09e8a7d6`；既有 187 项修改/未跟踪，暂存为空。只处理已确认的占位候选诊断混入原生成失败，以及恢复页误展示占位图静态证据。
- 小批一（3 文件）：本文、`server/meta_agent/meta_planner_v2.py`、`server/tests/test_meta_planner_closeout_evidence.py`。先复现，再将占位校验与生成诊断分开；审批仍查询原权威门禁。
- 小批二（4 文件）：本文、`client/src/components/meta/MetaPlannerV2.tsx`、对应组件测试、帮助文章 `review-meta-planner-branches.md`。区分当前原图复核、历史诊断、占位校验与审批结果；历史未分离报告不能猜测归因。
- 不修改 Validator、Runner、模型提示、调用预算、授权、Store 格式或审批规则；不以错误码黑名单隐藏诊断。新报告仅增加来源标签与占位校验字段，无数据迁移。
- 验收：隔离 `run_offline.py` 运行 closeout 红测和相关 Meta Planner/Authoring 回归；前端组件测试及生产构建；语法、Diff、敏感信息检查。真实生成、预览重启和整轮 PR 门禁另列。
- 风险为中等，影响管理侧诊断，不执行模型或业务记录操作。禁止读凭据、付费调用、活表写入、批准、发布、共享栈操作、Commit、Push 和 PR。
- 回退只恢复诊断投影；已保存 Proposal、失败原产物和审批阻断不变。

## 诊断来源隔离复测结果

2026-09-27：本小批通过，未进行真实 Provider 复测，CW10 整轮 PR 门禁仍未通过。

### 根因与处理

- 生成失败且没有可编译候选时，服务端会生成禁止执行的占位图，并故意清空其模型。原实现把该占位图的 Workflow/Publish 校验错误合入 `meta_planner_report.validation`，错误地归到生成失败；Graph 兼容入口与当前 Recipe 入口均已复现。
- 现在占位图完整校验保存在 `placeholder_validation`，携带 `diagnostic_subject=server_synthesized_fallback` 与真实占位候选 checksum。原生成失败只保留生成诊断，标记 `model_generation`，没有真实编译产物时 `validation_candidate_checksum` 为空。实际模型候选仍绑定原 checksum，不按错误码过滤任何错误。
- 返回值的组合门禁标记 `generation_and_authoring`；持久化 `proposal.validation` 仍是审批权威，失败原图、未解析产物和占位图均不能因此运行或批准。失败产物、原图复核和重启读取继续保留。
- 恢复页不再把审批、旧报告与当前原图复核合成一份错误列表，分别显示来源。旧报告没有来源标签时，完整保留为“历史混合诊断（来源未分离）”，不迁移或猜测。恢复模式不展示占位图控制场景与资源快照，状态始终为“待修复”。
- 只修改了两项生产实现、两项测试及本记录与用户帮助，共六个既有工作路径，按两个小批拆分。未修改 Validator、Runner、提示词、模型预算、权限、业务 Store 或审批规则。

### 验证

| 检查 | 结果 |
| --- | --- |
| 后端初始红测 | 1 failed / 5 passed；缺少来源隔离。随后扩展为 3 failed / 5 passed，其中两个直接复现占位模型错误混入；另一负向夹具使用空修复，实际进入占位分支，已改为有效标题修改以检验真实候选错误，不改生产逻辑迎合夹具。 |
| 后端首批回归 | `test_meta_planner_closeout_evidence.py`、`test_meta_planner_failed_recovery.py`、`test_meta_planner_recipe_integration.py`：44 passed / 4 warnings，66.46 秒。 |
| 最终扩大回归 | 同上轮显式 75 文件清单：1585 passed / 4 warnings，646.61 秒。包括新反证测试，不与首批重复累加。 |
| 前端红测 | 新旧报告两项来源分区测试失败，其他 10 项通过。 |
| 最终前端回归 | MetaPlannerV2、FailedDraftRepair、ModelDraftRepair、metaAuthoring 四文件：53 passed，12.41 秒。 |
| 生产构建 | 初次失败：恢复诊断 `info` 与旧列表错误级别类型不同。展示列表改为只消费 `message/stage` 后，同一 `npm.cmd run build` 通过；既有大 chunk warning 保留。 |
| 浏览器模拟检查 | 新启动 `127.0.0.1:15509`，仅本地合成数据，关闭环境文件和后端代理，页面全部 fetch 返回模拟响应或拒绝；新报告四分区、旧报告混合来源说明、禁止评测/批准、占位静态场景 0 项均核对。截图检查当前约 809px 宽视窗文字无重叠。 |
| 语法与文件检查 | 两个 Python 文件 AST 解析通过；Diff 检查通过；六文件已知密钥签名 0 命中；暂存为空，待提交路径仍为 187，未出现 SQLite、Runtime、日志、上传、临时预览或构建产物。 |

后端继续使用 `.tmp-recovery-e/run_offline.py`，清除凭据环境、停用 dotenv、隔离临时 Store、禁止外网。最终 JUnit 记录为 `.tmp-recovery-e/diagnostic-isolation-affected.xml`，重点记录为 `diagnostic-isolation-focused.xml`。一次扩大回归命令遗漏显式文件清单，只传入报告参数，已在没有结果时中止，随后按上轮完整清单重跑；中止运行不作为通过证据。

最终扩大回归与上一节命令相同，只将报告文件名替换为 `diagnostic-isolation-affected.xml`。前端四文件命令及构建也与上一节相同。临时模拟入口位于 `client/.tmp-diagnostic-isolation-preview/`，已确认被 Git 忽略；专用服务与临时标签均已关闭，未重启 15489 预览或共享服务。

### 保留边界

- 浏览器证据仅为无后端的合成页面，不是同版全栈、真实模型或完整教程重放；没有自动应用、审批、发布或业务写入。
- 未运行全量 `server/tests/`、最新上游集成或新的真实生成。离线成功不改变上一真实失败结论，也不证明模型稳定性。
- 旧失败报告保持原文件，不凭空修复或覆盖其历史结果。后续真实复测需要明确外发内容、模型与预算，不继承已经消费的旧授权。
- 无 Commit、Push、PR 或 Merge；回退仅恢复诊断投影，不修改失败产物、Proposal 或业务状态。

## 2026-09-27 同版真实生成复测：首次候选通过

用户本次单独授权真实调用。沿用 E-G1 合成库存目标、零记录合成表及安全 Schema 元数据，固定 OpenRouter / `deepseek/deepseek-v4-flash-0731`，一次生成、最多三次 completion。候选权限仅为该表查询和 `update/status/最多 1 行`；不执行工作流、写入、评测、批准或发布。旧调用预算不复用，新账本与全部历史回执并存。

### 版本与隔离

- 工作树仍为 `codex/meta-planner-cw10-recovery-e@09e8a7d6`，187 个既有修改/未跟踪路径，未混入主工作区。仅刷新本任务独立预览后端 `16489`，前端 `15489` 读取本轮生产构建；不操作共享栈。
- 本次 Planner 源文件 SHA-256 为 `de1119a971e013d2019a68866d155dab5d741f9c89ef545e60d547cfb7ef59f5`；`recipe_edits.py` 为 `8901e7553c4d81b5655067d794287bd88b64f3fe9cb3ba9d468272dbd527230c`；前端构建入口为 `a2b34cbb4fbda837e43aae4f2dd0f2a934cba765824e3679bb078f6407edc223`。
- 启动记录中的底层 Python 解释器缺少 dotenv，第一次重启在派发前退出；改用已存在且依赖已核实的测试虚拟环境启动，不安装依赖。该启动失败未消费模型额度。
- 隔离预览仍阻断写入、评测、批准、发布及额外模型修复入口。视觉目录的非授权外网请求被护栏拒绝；不是新的 Provider 调用或本次 Planner 失败。

### 实测结果

- 真实浏览器仅点击一次“生成候选智能体”。UTC `08:31:40` 至 `08:33:01` 完成，实际两次 completion，均为 HTTP 200 / `finish_reason=stop`，没有不确定请求或自动重试。
- 第一次 usage 为 3,553 Token，第二次为 12,077 Token，合计 **15,630 Provider Token**。`repair_used=false`；剩余一次预算未使用，不自动转用于其他任务。
- 新提案：`proposal_346603e15e064b85bf0d0bc22f9b9954`，revision 1，`pending`、IR `current`。名称为“库存检查与状态更新流程”。生成校验通过，Headless 只读状态的 diagnostics/warnings 均为空。
- Graph checksum：`00453f0011b92d7dbf8240f3e4136a05e3f9a967f53219e6e7899da62746fe04`；候选 checksum：`d4b9a22aa5bd360bea48425a4dac5e80b3f2a3bc650927746cb2b2780a78a4da`。
- 控制流静态证据为 2 个路由、3 个场景、2 个互斥成功来源：查无记录以 `RECORD_NOT_FOUND` 终止；库存小于 5 时更新后汇总；库存不少于 5 时保持不变并独立汇总。五项静态 proof checks 全部通过，不再是全线性工作流。
- 更新节点的 records 直接来自同表 `query_demo_a.result`；只写固定 `status=待补货`，上限 1 行；更新回执仅由更新分支 Agent 消费，未修改分支不依赖更新回执。资源均固定 Schema v1，授权未扩大。
- 合成表实测后仍为 0 条记录、Schema v1；没有执行写节点或下游 Agent。浏览器仅查看候选、调整画布视口，未编辑或应用 Proposal。

### 结论边界

本次是一次真实生成成功证据，不是稳定性、实际写入效果或完整 E3/PR 门禁通过。首次候选已经通过，因此新增 `recipe_edits_v1` 修复协议及新失败报告来源分区未在本次真实失败路径中触发，其此前离线证据仍单列。下一步仍需对该固定候选进行明确授权的隔离分支效果验收，并完成最新全量、上游集成和其他锁定门禁。未 Commit、Push、PR、Merge、审批、发布或写活表。
