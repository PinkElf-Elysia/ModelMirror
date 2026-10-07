# Meta Planner 简化生成描述 V1

本文件对应 CW10 系统性收口批次 C。它是模型输出到既有 GraphIntent V3 的私有编译输入，
不是新的运行 IR、Requirement IR、表达式引擎或多 Agent Planner。公开生成请求、22 类能力、
授权、Proposal 审批、Runner 和 SSE 不变；Headless 失败恢复的受限扩展见下文。

## 2026-09-30：协议与输入收口后的真实复测

经用户本轮授权，在独立预览器 `127.0.0.1:15489` 实际点击一次生成，使用当前
`codex/meta-planner-cw10-acceptance-20260927` / `2bf50146` 工作树及原 E 隔离目录。
仅由后端使用已批准的 OpenRouter 连接，调用 `deepseek/deepseek-v4-flash-0731`。
测试目标、零记录合成表及查询、`update/status/1` 授权均保持原范围。

- 三次 completion 均为 HTTP 200、`finish_reason=stop`，实际 Token 分别为
  4,006、14,024、10,062，总计 28,092；没有第四次调用或不确定派发。
- 候选为 `proposal_51ea5083de43448aa872d93b50b311f4`，`pending r1`，
  `validation.valid=false`、`human_modified=false`、`can_approve=false`。
- 首次 Recipe 的协议与输入结构解析通过：Query 和 Terminate 使用显式空输入数组，
  条件输入有明确来源。本次未重现上一轮协议缺失及非 Agent null 输入问题；
  单个样例不证明该类错误已被模型稳定避免。
- 首次展开失败为 `RECIPE_REPEATED_NODE`：`query_after_update` 出现在两个互斥分支。
  没有形成有效 IR，后续授权、资源解析、编译与发布预检仍被阻断，不将其标为通过。
- 唯一修复采用 `recipe_edits_v1`，返回的 `replace_control_flow` 缺少
  `control_flow` 且多出 `config`，`update_node` 缺少 `node_ref`。修复未应用，
  前后控制结构 checksum 相同，不能把错误类别变化解释为原问题已经修好。
- 三阶段请求的 Schema checksum 均与预期契约一致，Provider、Collector 与 Validator
  的安全结构投影一致。现有证据不支持传输层改写结构，但没有完整请求/响应逐字重放证据。
- 失败 Recipe 已安全保留为 `recipe_v1`，页面能定位首次及重复出现位置，并展示只读节点和
  控制结构人工修复入口。本次没有编辑、预览或应用人工修复，不能据此宣称人工修复闭环通过。
- 前后源文件哈希一致；合成活表前后均为 0 条记录，Evaluation 调用为 0，
  没有运行写节点、调用下游 Agent、批准、发布、Commit、Push 或 PR。

安全回执保存在忽略目录
`.tmp-recovery-e/delivery-preview/protocol-input-retest-20260930/`：
`source-manifest.json`、`generation/calls.json`、`result-safe.json`。
不提交 Runtime Store、原始 Prompt 或凭据。结论仍为真实生成未通过，不能进入 PR 收尾；
下一步应固定本次保留的 Recipe，离线核对控制结构与修复操作契约，再决定范围，不能追加碰运气调用。

## 2026-09-30：协议与输入契约失败闭环（离线收口）

本批仅收口协议识别、输入模式、修复反馈及受限失败草稿。基于现有独立工作树
`codex/meta-planner-cw10-acceptance-20260927` / `2bf50146`，保留全部既有改动；不刷新
历史回执，不操作共享栈、活表或已发布资源，不调用外部模型，不提交 PR。

分批边界：先统一生成契约投影与结构诊断，再接入优先修复清单，最后补齐已知输入格式错误的
安全保留和人工预览/应用。单批默认不超过五个文件，前后端恢复契约分别验证后集成。
执行 Schema、资源授权、三次调用预算及审批语义不变，不自动将 null 替换为空数组或猜测绑定。

### 已核对的链路缺口

- 前一轮首次产物缺少 `generation_protocol_version`。安全顶层字段投影与原异常指纹一致，
  但没有完整原文，不宣称完成原始响应重放。唯一修复之后，Query 和 Terminate 的 null 输入
  仍不合法。传输成功不能证明格式有效，更不能证明后续类型或路径通过。
- 旧入口在协议缺失时先抛错，遮蔽独立输入错误；六类结构失败虽有普通错误消息，但优先修复
  清单为空，错误地进入输入/路径层。失败留存仅能救回资源 kind 错误，不能救回已知输入模式错误。
- 交叉证伪另发现 `Literal[1]` 会把布尔值 true 当作 1，和对模型公开的 JSON Schema 不一致。
  本批增加前置拒绝；不扩大 Schema，也不将布尔值归一化为协议版本。

### 实现边界

- `recipe_input_contract` 同时驱动解析器、模型 Schema 和节点提示投影。仅 Workflow Agent
  可用 null 推导模板来源；空数组只表示没有显式绑定，不能免除 Condition 等节点的必需输入。
- 完整严格 Recipe 一次收集协议与输入错误。固定错误码、字段位置和受限 expected/actual
  进入 `repair_focus`，优先层为 `protocol_or_schema`；未执行的类型、授权和路径仍为 blocked。
  诊断不按异常文本猜测类型，不回显输入值、Prompt 或任意校验器上下文。
- 新增私有 `recipe_input_draft_v1`，仅保留协议缺失/null、已知非 Agent null 输入这类受限
  错误。原有字段 grammar、Adapter 配置、资源范围、1 MiB / 32 层上限和秘密扫描仍生效。
  字符串输入、未知字段、非法配置、未知节点及未授权资源不进入此恢复格式。
- 保留的是经过既有安全模型规范化的草稿，不是逐字 Provider 原文；缺失协议以 null 标记，
  不补成 1。它不能直接编译，所有预览都重新经过严格 Recipe、Graph IR、类型、资源和发布预检。
- 人工入口新增 `recipe_inputs_v1`，最多 25 个操作：`replace_recipe_inputs` 只替换指定
  ref 的输入列表，`confirm_recipe_protocol` 只在缺失时显式确认 V1。每节点最多 50 个引用，
  重复/未知 ref 拒绝；不可修改配置、任务、控制结构、资源或授权。没有自动 null 转空数组。
- UI 原样显示 null，协议确认默认不勾选。预览不保存，Apply 重新核对 revision、artifact、
  candidate、资源与 snapshot，成功只增加一次 pending Proposal revision；原失败产物保留。
  重启后退出失败修复模式并恢复正常编辑/审批预检。未实际批准任何提案或创建 Xpert。
- 输入草稿仍禁止 Graph Patch 模型修复，且在读取 Provider 路由前拒绝。没有额外模型调用、
  自动重试或新授权。已过期且没有安全原文的历史失败候选不能据 checksum 回填。

### 离线验证与回退

反例先红后绿：输入投影/多错误报告 2 项、修复清单 6 项、安全留存 13 项、人工恢复 7 项、
UI 6 项，以及布尔协议 true 1 项。没有删除失败断言或放宽执行门禁。

| 验证 | 结果与时序 |
| --- | --- |
| 较宽回归 | 82 文件、1,713 通过、0 失败，715.13 秒；在补充布尔协议护栏前启动，不冒充最终全量结果 |
| 最终代码针对性回归 | 10 文件、197 通过、0 失败，123.81 秒，涵盖生成、诊断、留存、HTTP 恢复与旧流程 |
| 补充重启后审批预检断言 | 16 通过、0 失败，23.28 秒；与上述结果重叠，不累加计数 |
| Meta Agent 前端组件 | 7 文件、78 通过；包含修复面板 22 项 |
| 前端生产构建 | `npm.cmd run build` 通过；保留大包体积警告，没有调高限制 |
| 语法与静态检查 | 本批 12 个 Python 文件 AST 通过；15 个涉及文件的行尾空白与常见密钥模式检查通过 |

后端四条警告均为现有 FastAPI 生命周期弃用提示。验证使用
`.tmp-recovery-e/run_offline.py`，全新临时 Store、剥离凭据、禁外网；不使用活表或真实模型。
最终针对性证据为忽略目录中的 `protocol-input-closure.xml` 和
`protocol-input-recovery-api.xml`，不提交测试数据或构建产物。主要复核命令：

```powershell
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -B .tmp-recovery-e/run_offline.py
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -B .tmp-recovery-e/run_offline.py server/tests/test_meta_planner_recipe_input_contract.py server/tests/test_meta_planner_recipe_parse_feedback.py server/tests/test_meta_planner_recipe_input_recovery.py server/tests/test_meta_planner_recipe_input_recovery_api.py server/tests/test_meta_planner_generation_recipe.py server/tests/test_meta_planner_recipe_schema_recovery.py server/tests/test_meta_planner_recipe_integration.py server/tests/test_meta_planner_recipe_text_inputs.py server/tests/test_meta_planner_failed_recovery.py server/tests/test_meta_planner_generation_diagnostics.py --junitxml=.tmp-recovery-e/protocol-input-closure.xml
# 在 client 目录执行
npm.cmd run test -- --run src/components/meta --maxWorkers=1 --fileParallelism=false
npm.cmd run build
```

仍未执行本批最新全量 `server/tests/`、上游刷新/集成、新代码浏览器验收或真实 Provider 复测。
本批不证明模型生成成功率、泛化稳定性或整轮 PR 门禁；没有 Commit、Push、PR、共享栈操作、
预览后端重启、活表写入或外部额度消耗。

回退可先关闭新格式的人工 Apply，再恢复本批投影与反馈逻辑；保留新格式的只读识别及既有
失败产物，不删除历史 Proposal，不重新派发已消耗额度的调用，不撤销已完成的业务写入。
已通过人工恢复的候选仍为普通 Graph IR V3，不依赖该私有草稿格式执行。

## 2026-09-30：模板来源诊断与阶段化修复收口

本批基于 `codex/meta-planner-cw10-acceptance-20260927` / `2bf50146`，延续既有未提交工作。
针对下一节保留的真实失败产物，只调整生成描述校验顺序、安全诊断与模型修复提示，不改候选、
Runtime、资源授权或调用预算。生产范围为 `generation_recipe.py`、`generation_diagnostics.py`、
`recipe_preflight.py`、`meta_planner_v2.py`；另有两份针对性测试和本文档，分两批独立验证。

### 根因与处理

- 自动推导 Agent 输入时，首个非法 Prompt 引用提前抛错，导致后续独立结构问题无法同时反馈；
  显式输入和自动输入的诊断能力不一致。现统一收集原始模板错误，合法引用只用于补充检查，
  非法占位符不被删除、替换或自动接线，仍使候选失败。
- 修复重点曾丢失模板引用的定位细节，并过早提供泛化的分支重构建议。现保留字段内从 0 开始
  的 `reference_index`、原因及受信任节点的真实输出端口；未知表达式只存 checksum，不回显
  Prompt 或未知来源原文。没有路径反例时不指引克隆分支；整图修复与受限编辑共用同一清单。
- 原始来源在插入编译器文本辅助节点之前校验，防止模型猜中后生成的辅助 ref 而获得引用权限。
  已有文本适配逻辑不变；不增加新的自动业务编排。
- 合法引用子集复用原控制流分析器检查依赖，且仅在配置、授权、结构、类型和谓词域前置满足时
  产生 `known_sources_only` 反例。完整路径证明仍是 `blocked`，子集即使无错误也只能是
  `partial`，不能放行图或返回可执行替代图。
- `terminate_error` 之后仍有节点时，给出带控制位置的 `RECIPE_AFTER_TERMINAL`。
  修复进展新增仍存、新增、未再观察到的问题计数；调整占位符次序不能掩盖相同错误。
  “未再观察到”不等于“已解决”，最终仍必须重新通过全部门禁。

### 保留产物离线重放

使用与真实候选一致的 Capability Snapshot，在内存中重放
`proposal_249e43ad0c4b43d4b216a7e6ae96984a` 的首次及修复后 Recipe：

- 首次同时定位 `check_stock_low.result` 无数据输出，以及公共 `conclusion_agent` 消费
  分支独有 `update_status.result` 的反例；后者仅属于合法来源子集证据。
- 修复后仍有原模板错误，另有 `terminate_not_found.config.message` 非法属性引用和
  终止节点之后继续执行，共为“原错误仍存 1 项，新增 2 项”。控制树变化不构成修复成功。
- 两份产物仍被拒绝，持久化 Proposal 文件前后 hash 相同；未回填旧回执、未修改失败候选。
  本批没有外部调用、活表写入或预览重启，页面仍运行先前代码。

### 验证与剩余门禁

新增反例先红后绿：第一批 8 项失败，第二批提示/进展检查 6 项失败；补充攻击还发现第一版
顺序调整会接受猜中的辅助 ref，已以原始命名空间先验校验修正。最终新增 18 项覆盖两种业务域、
显式/自动输入、安全脱敏、64 项诊断上限、结构独立性、部分路径证据、两种修复入口和错误存续。
既有隐私测试仍验证原文不泄露，仅更新新增安全诊断字段的精确断言，没有放宽接收门禁。

最终 19 个受影响测试文件共 **445 项通过，0 失败**，344.87 秒，包含先前六文件的 146 项重点
测试，不累加计数。新增 18 项测试包含在总数内；4 条警告均为现有 FastAPI 生命周期弃用提示。
六个修改的 Python 文件 AST 检查通过；Diff 格式、全部七文件的行尾空白和常见密钥模式检查
通过，暂存区为空。没有用禁用测试或放宽 Schema 处理失败。

命令入口为阻断外网且使用全新临时 Store 的本地 `.tmp-recovery-e/run_offline.py`，结果文件为
忽略路径 `.tmp-recovery-e/template-contract-focused.xml` 与
`.tmp-recovery-e/template-contract-regression.xml`，均不提交。

```powershell
$tests = @(
    'template_repair_contract', 'recipe_diagnostic_independence', 'recipe_text_inputs',
    'recipe_source_feedback', 'repair_priority', 'recipe_edits', 'recipe_preflight',
    'recipe_lowering_recovery', 'recipe_model_repair', 'recipe_integration',
    'control_domains', 'control_flow', 'generation_diagnostics', 'resource_repair_diagnostics',
    'failed_artifacts', 'failed_recovery', 'model_repair_api', 'write_delivery_contract'
) | ForEach-Object { "server/tests/test_meta_planner_$_.py" }
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -B .tmp-recovery-e/run_offline.py @tests server/tests/test_meta_agent.py --junitxml=.tmp-recovery-e/template-contract-regression.xml
```

回退只恢复本批来源检查顺序、诊断投影与修复提示，现有候选和数据保持不变。本批不运行真实
Provider；未在本批刷新或合入新的上游提交。全量后端、前端构建及加载新代码后的真实复测
仍是独立门禁，不能据离线结果宣布模型生成稳定、业务效果验收通过或达到 PR 门禁。
未提交、推送或创建 PR。

### 本批后续授权真实复测：结构解析仍未通过

用户单独授权后，在 `15489` 真实点击一次生成；仅重启本任务 `16489` 后端，复用原运行目录
和已批准连接的后端内存解析。目标、零记录合成表、query 与 update/status/最多 1 行范围不变。
独立回执位于忽略路径 `.tmp-recovery-e/delivery-preview/template-source-retest-20260930/`，
旧账本不修改；未执行写节点、下游 Agent、评测、批准或发布。

- 新候选 `proposal_101b3c7c117a4c149464124f9f541f83` r1，`pending`，`validation.valid=false`。
- OpenRouter / `deepseek/deepseek-v4-flash-0731` 共 3 次 completion，均 HTTP 200 / stop；
  实际 Token 为 3,919 / 12,489 / 14,877，总计 31,285。没有不确定派发或第四次调用。
- 任务规划通过；首次 Recipe 的安全结构投影显示：索引 0 的 `data_table_query`、索引 1 的
  `condition`、索引 2 的 `terminate_error` 均为 `inputs=null`。只有 `workflow_agent`
  可以使用该模式；其他节点必须使用输入数组，没有输入时使用空数组。
- 唯一修复走完整 `generation_recipe_v1`，没有进入受限编辑。修复后的 Condition 改为
  一个显式输入，但 Query 和 Terminate 仍为 null，模型校验明确定位 `nodes.0`、`nodes.2`。
  两次 Recipe 均未通过结构解析，授权、类型解析、编译和发布预检均被阻断。
- 三次调用的 Provider、collector、validator 正文 checksum 各自一致，实际请求的 Schema
  checksum 与预期一致。没有证据表明传输层改写了产物；也不能把 HTTP 200 当作契约通过。
- 两次失败产物均为 `retention_status=unparsed`，`recipe=null`、`intent=null`。页面如实显示
  原图未保留，没有可编辑的真实失败图。现有安全结构投影足以证明输入类型问题，不能重放完整
  原始 Recipe，亦不能据占位图证明模型生成了线性流程或通过了后续路径校验。
- 本次未到达上一批模板来源及合法来源子集路径检查，因而不能验证该修复的真实模型效果。
  活表仍为 0 条记录，Schema v1；评测调用及 claim 均为 0。未修改或替换新候选后冒充模型结果。

下一步应先离线核对节点输入模式在生成 Schema、提示与完整 Recipe 修复反馈中的一致性，以及
结构解析失败时的安全产物保留边界。不得直接把所有 null 改为空数组：Condition 等有必需输入的
节点仍须由合法来源显式绑定。本次不实施该后续调整，也不追加外部调用；不能宣布稳定或达到 PR 门禁。

本次加载的生产代码 SHA-256：

| 文件 | SHA-256 |
| --- | --- |
| `generation_recipe.py` | `e849384327484e256aae71f15692db6145115e3fc3d7b8bf5849e501fce08d9a` |
| `generation_diagnostics.py` | `a2e67b59699737177cc1c48d8a5106d581c374c4acf33ee3387dea8edb87a7d1` |
| `recipe_preflight.py` | `a24a79a7f55338957abc26f8c518a785bce60708a7b99c2fda1c4d82cf55e7b9` |
| `meta_planner_v2.py` | `0f4f6fee066ba046be0d7d949797818787d7b0ec423cf3c99fb27787160cc505` |

## 2026-09-30：受限编辑协议一致性收口

### 本批后续授权真实复测：仍未通过

用户单独授权后，在 `15489` 预览真实点击一次生成。仅重启本任务 `16489` 后端加载新代码，
保留原连接、零记录合成表、query 与 update/status/最多 1 行范围；没有扩大权限或执行写节点。
本次独立回执目录为忽略路径
`.tmp-recovery-e/delivery-preview/recipe-edit-contract-retest-20260930/`，旧账本保持不变。

- 新候选：`proposal_249e43ad0c4b43d4b216a7e6ae96984a` r1，`pending`，验证失败。
- OpenRouter / `deepseek/deepseek-v4-flash-0731` 共 3 次 completion，均 HTTP 200 / stop，
  实际 Token 为 3,963 / 12,631 / 8,652，总计 25,246；没有不确定派发或第四次调用。
  Provider、collector、validator 三处正文 checksum 每次相同，三次输出均通过各自 JSON Schema。
- 首次 Recipe 在 `conclusion_agent` 的 `task_input` 引用了 `check_stock_low.result`。
  `check_stock_low` 是只有输入端口和控制 outcome、没有数据输出端口的 Condition，
  因此在 Recipe lowering 阶段触发 `RECIPE_TEMPLATE_SOURCE_UNKNOWN`；尚未到达控制流证明。
- 唯一修复包含 clone/update/control/final 四个操作，`patch_parse` 和 `patch_apply` 均通过，
  已实际进入重新编译。这次没有复现上一轮未知或克隆目标被拒绝的现象，但单次观察不能证明稳定。
- 保存的修复后 Recipe 仍包含同一 `check_stock_low.result`，失败位置和指纹不变；
  新克隆的 `conclusion_not_found` 又引用 `terminate_not_found.config.message`，并被放在
  `terminate_error` 之后。这些是保留产物中可直接核对的问题，不把尚未执行的后续校验描述为已通过。
- 活表记录数仍为 0，评测调用数为 0；未批准、发布、执行工作流或创建 PR。
  未手工修正候选后冒充模型产物，停止继续消耗额度。下一步需离线核对首要模板错误的反馈与
  修复目标选择，不能因为编辑协议层通过就判断整条链路已收口。

加载代码指纹（SHA-256）：`recipe_edits.py` 为
`02a886444b73fa4b884a7b4d329cbd2120d8e530916d7bea4f34e7e6d09cb512`，
`generation_diagnostics.py` 为 `321876f5a59251bae9bdf8fd4e7ca58551006f1750716fa9142b621ef705fae3`，
`meta_planner_v2.py` 为 `a9223a45ec89cf82d13b9aaa2de3c6a5ae5d2dec7a2bb7d9d7bc486d808389d9`。

### 离线修改与验证记录

本批在 `codex/meta-planner-cw10-acceptance-20260927`、`2bf50146` 基线上延续已有变更，
只修改 `recipe_edits.py`、`generation_diagnostics.py`、`meta_planner_v2.py`、
`test_meta_planner_recipe_edits.py` 和本文档。风险限于修复提示与诊断投影；不改变 Runtime、
业务写授权、编译门禁、持久化模型或三次调用预算，不重启预览、不消耗外部额度。

### 已证实的边界与未证实的原因

- 对上一节真实失败候选的只读核对表明，修复提示已准确指出公共消费者及不写入路径反例；
  不能再将本次失败归因于 repair_focus 缺少原节点定位。
- 未知目标、同批克隆目标和授权已失效的原节点均被同一通用错误拒绝，原安全回执无法区分。
  由于没有保存原始修复操作目标，不能回填或猜测上一轮真实失败的具体操作序号。
- 受限编辑提示复制了整图修复清单中“类型替换须使用新 ref”的指令，与现有操作集不一致。
  该矛盾是已证实的 Harness 缺陷，但尚不能证明它是模型失败的唯一原因。
- 对该候选的内存核对中，保留原 Agent、克隆一个分支结论、替换控制树和最终来源即可通过
  原生编译。这证明既有操作集能表达该修复，不证明模型会稳定地产生它，也不是业务效果验收。

### 实现与验收边界

- `recipe_edit_contract` 从原 Recipe 和现有授权派生可编辑 ref；Schema、修复提示和执行内核
  共用该范围，不接受模型扩大。克隆必填字段直接来自现有操作模型。
- `update_node` 仍只修改原节点；同批克隆节点必须在 `clone_agent` 中一次提供完整
  `task_input` 和需要覆盖的 `role_prompt`，未覆盖角色继续继承。没有新增操作或自动改图。
- 受限编辑使用专属清单，移除整图类型替换指令，保留首要阻塞、关联消费者和分支终点检查。
- 三类拒绝分别记录 `RECIPE_EDIT_UNKNOWN_NODE`、`RECIPE_EDIT_CLONED_NODE_IMMUTABLE`、
  `RECIPE_EDIT_NODE_UNAUTHORIZED`。诊断包含从 0 开始的操作序号、固定操作名和 ref checksum；
  不保存原始 ref、Prompt、记录或完整操作配置，失败仍不部分保存。
- 新增反例修改前为 6 失败，分别对应诊断不可区分和提示缺少共享范围；不是放宽校验后绿测。
  验收覆盖不同业务域、Schema/提示/内核一致性、原子拒绝及生成入口的三次预算与 pending 状态。

最终验证：17 个受影响测试文件共 **423 项通过，0 失败**，326.64 秒；另跑仓库要求的
`test_meta_agent.py`，**4 项通过**。先前 78 项重点检查已包含在 423 项中，不重复计数。
本批新增 7 项测试，其中完整生成入口验证失败诊断留存、三次调用后停止、候选仍为 pending
且不创建 Xpert 草稿。全部为阻断外网的隔离测试，Provider 为测试替身。
4 个修改的 Python 文件 AST 检查通过；Diff 格式与五文件常见密钥模式扫描通过，暂存区为空。
测试警告仅为现有 FastAPI 生命周期弃用；没有通过禁用测试或修改旧断言处理失败。

```powershell
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -B .tmp-recovery-e/run_offline.py server/tests/test_meta_planner_repair_priority.py server/tests/test_meta_planner_recipe_source_feedback.py server/tests/test_meta_planner_recipe_edits.py server/tests/test_meta_planner_write_delivery_contract.py server/tests/test_meta_planner_recipe_preflight.py server/tests/test_meta_planner_recipe_diagnostic_independence.py server/tests/test_meta_planner_recipe_lowering_recovery.py server/tests/test_meta_planner_recipe_model_repair.py server/tests/test_meta_planner_recipe_integration.py server/tests/test_meta_planner_recipe_text_inputs.py server/tests/test_meta_planner_control_domains.py server/tests/test_meta_planner_control_flow.py server/tests/test_meta_planner_generation_diagnostics.py server/tests/test_meta_planner_resource_repair_diagnostics.py server/tests/test_meta_planner_failed_artifacts.py server/tests/test_meta_planner_failed_recovery.py server/tests/test_meta_planner_model_repair_api.py --junitxml=.tmp-recovery-e/recipe-edit-contract-regression.xml
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -B .tmp-recovery-e/run_offline.py server/tests/test_meta_agent.py --junitxml=.tmp-recovery-e/recipe-edit-contract-meta-agent.xml
```

回退仅恢复本批共享范围投影、提示与诊断处理，不改变任何现有候选或业务数据；旧失败回执
仍保持原证据，不补造新诊断。全量后端、前端构建和真实复测不属于本次离线结果，后续仍需
独立门禁，当前不能宣布稳定或进入 PR。

## 2026-09-30：首要阻塞与关联检查收口

### 授权真实复测：仍未通过

后续经用户单独授权，在 `15489` 预览点击一次“生成候选智能体”，仅复用已批准的
OpenRouter 连接及零记录合成表范围；模型仍为 `deepseek/deepseek-v4-flash-0731`。
候选为 `proposal_7d01b375b4624c939738373d2bee0ef3` r1，保持 `pending`，校验失败。

- 已重新启动本任务独立后端加载修复，不重建共享栈。启动器独立回执目录首次引起本地模块
  查找冲突，已在任何外部派发前修正启动路径；该问题不属于本次模型编排失败。
- 三次 completion 均为 HTTP 200 / stop，共 25,825 个实际 Token（4,021 / 12,570 / 9,234）。
  Provider、collector、validator 三处正文 checksum 每次均一致。没有追加第四次调用。
- 首次 Recipe 已采用 `first` 与 `is_null`；条件域、分支覆盖和终点覆盖通过，仍因公共汇总
  消费低库存分支独有的 `update_status.result` 而触发 `DATA_PATH_NOT_GUARANTEED`。
  因此不能把本次失败归为上次的数组/对象类型错误。
- 唯一修复使用 `recipe_edits_v1`，结构观察记录 3 次 `clone_agent`、3 次 `update_node`、
  一次控制树替换和一次最终来源修改。Provider 输出不符合本次限定 Schema，应用阶段返回
  “只能修改原图中仍在授权范围的节点”。该检查同时涵盖未知 ref、新克隆 ref 及未授权 kind；
  安全回执未保留完整操作目标，不能凭错误文案猜测具体哪个 ref，也不能宣称已定位唯一根因。
- 失败产物已保留，未手工修正后冒充模型结果。活表记录数仍为 0，隔离评测调用数为 0，
  未批准、发布、提交或创建 PR。此次仍不足以证明生成稳定性；下一步应离线核对原节点与
  新克隆节点的编辑边界及诊断可定位性，不继续盲目消耗额度。

本次私有调用回执位于忽略目录
`.tmp-recovery-e/delivery-preview/repair-priority-retest-20260930/generation/calls.json`，不提交。
复测源码指纹：`recipe_preflight.py` 为
`d0397b3bba11721a9d78fdf2e0658192d286160aee19acfeaa012a2613d21fbd`，
`meta_planner_v2.py` 为 `0debcc5ef8afed69ad99900faec804865461df7788f3e70b9dc4e6cd30ff58d1`。

### 离线修复范围与验证

本批仍使用 `codex/meta-planner-cw10-acceptance-20260927`，基线 `2bf50146`，
只修改诊断投影、修复提示、针对性测试及本文档，不改 Runner、控制流证明、资源授权或存储。

真实失败候选 `proposal_a0e2b48d83714753848f35e4ea929fef` r1 的证据确认：

- 首次 Recipe 使用 Query `return_mode=list`，却把数组交给读取顶层字段的条件。
  数组即使只有一条也不是对象；不能将其笼统描述为缺少非空保护。
- 在内存中同时修正返回类型与空值判断后，才显露公共汇总引用分支独有写回执的错误。
- 唯一一次模型修复保留类型错配，克隆两个分支 Agent 后遗漏原 Agent 的控制位置。
  原节点不可静默删除；已有协议可保留原 Agent、仅克隆另一分支。
- 三次响应均完成，Provider、collector、validator 的正文 checksum 一致。
  这排除了本次所观察链路的正文丢失，不证明模型生成稳定。

原 `repair_focus` 只投影数据依赖；因此条件域失败时可能为空，虽然完整语义诊断已包含错误。
受限编辑提示还遗漏了完整修复检查清单。本批复用现有事实，将已证实的结构、输入类型、
条件域阻塞置于后续路径依赖之前；不从标题或用户目标推断修复方案。

- 条件域诊断包含原 Recipe 配置/输入位置、受信任来源类型，以及由本次编译 source map
  派生的关联消费者。直接绑定与 Prompt 模板引用均纳入，编译器辅助节点不作为编辑目标。
- 关联位置去重，最多 64 条并报告省略数量；不复制 Prompt、比较值或记录正文。
- 控制覆盖展示声明节点、遗漏、重复与未知 ref，复用既有控制树校验，不增加自动删节点或接边。
- 保留修复检查清单，明确返回类型改变后需同步核对空值判断、字段访问及可信记录输入。
  `clone_agent` 是新增而非替换，原 Agent 与克隆节点均须显式安排。
- 授权或解析前置未通过时不虚构类型/路径结论；空的局部问题列表不代表完整验证通过。

新增离线反例修改前为 7 失败、2 通过，覆盖质检数值与事件状态两类数据、完整 Recipe 修复与
受限编辑两种入口。真实失败产物只在内存中回放，没有修改 Proposal，也没有消耗外部额度。
六个已有语义操作可在内存中修正三条业务路径并完成原生编译，但不是模型成功或业务执行证据。

验证命令：

```powershell
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -B .tmp-recovery-e/run_offline.py server/tests/test_meta_planner_repair_priority.py server/tests/test_meta_planner_recipe_source_feedback.py server/tests/test_meta_planner_recipe_edits.py server/tests/test_meta_planner_write_delivery_contract.py server/tests/test_meta_planner_recipe_preflight.py server/tests/test_meta_planner_recipe_diagnostic_independence.py server/tests/test_meta_planner_recipe_lowering_recovery.py server/tests/test_meta_planner_recipe_model_repair.py server/tests/test_meta_planner_recipe_integration.py server/tests/test_meta_planner_recipe_text_inputs.py server/tests/test_meta_planner_control_domains.py server/tests/test_meta_planner_control_flow.py --junitxml=.tmp-recovery-e/repair-priority-regression.xml
```

最终上述 12 个测试文件共 **327 项通过，0 失败**，耗时 234.14 秒；4 条警告来自现有
FastAPI 生命周期接口弃用。本批新增 10 项测试包含在总数内，不重复计数。
首轮扩大回归曾暴露授权前置失败被泛化为结构错误的问题，已收紧为既有 `RECIPE_` 结构诊断，
保留原授权反例断言后重跑通过。本次未重跑全量后端或前端构建，也未重启独立预览加载新代码。

本批不启动真实生成、隔离汇总、共享栈或 PR；不将旧全量测试的基线红项视为通过。
回退只恢复本批提示投影，现有候选、Runtime 数据和所有执行门禁保持不变。

## 2026-09-28：写入交付证据投影收口

- 工作树：`C:\tmp\modelmirror-meta-planner-cw10-acceptance-20260927`，分支
  `codex/meta-planner-cw10-acceptance-20260927`，集成基线 `2bf50146`。
  延续已有 CW10 变更，暂存区为空；不修改原独立预览或固定验收候选。
- 已定位：Adapter 已声明 Update/Delete 只返回 `matched/affected`，但 Recipe 的
  `recipe_node_contracts` 丢弃了 `output_binding_contract`；任务规划只有通用对象端口及计数说明，
  没有区分请求值和实际状态的交付语义。
  这是模型可见契约的信息丢失，不是写事务失效，也不能据此把全部模型失败归因于此处。
- 本批仅修改 Adapter 输出说明、Recipe 投影、任务规划投影、一份针对性测试和本文档。
  不改 Runtime、返回 Schema、编译器、授权、审批、持久化或三次调用预算；不自动添加业务查询。
- 验收先以跨生成/修复入口的契约测试复现缺口，再用不同字段和类型的合成表核对
  “仅计数 / 显式请求值 / 显式写后查询”三类汇总输入。真实 SQLite 效果与模拟 Agent 输入
  分开判定，离线结果不证明真实模型汇总质量。随后执行 Meta Planner 相关回归和前端构建。
- 风险为模型提示变化；回退只恢复本批三处提示投影，不改变已有 Workflow 或数据。
  不调用外部模型、不读凭据、不写活表、不提交 PR。
- 修复复用 Adapter 的唯一输出事实：Recipe 及其两类修复保留完整输出契约；任务规划只读取
  `output_evidence.shape/delivery_rules`，仍不接收配置或变量绑定职责，也不重复完整端口 Schema。
  请求值、事务回执、Query/Insert 的时点记录明确区分；只有目标确需核对写后状态且已有读取授权
  时才要求显式写后查询，编译器不自动补查询。现有候选不因提示修复而自动改变。
- 首次投影证伪矩阵共 32 项，修复前 20 项失败、12 项通过。其中 12 个失败对应四类表节点
  在 Recipe 及两类 Recipe 修复中的输出契约丢失；旧 Graph 三条路径的 12 项通过。
  另 8 项最初要求任务规划也携带完整契约；复核旧护栏后改为只验证其共用的简短交付说明，
  不把完整配置和 Schema 职责重新塞给任务规划阶段。
  第一版额外的范围测试误把“没有操作授权”视为“kind 不进入提示目录”，已改为显式 kind 范围
  夹具；没有因此改变生产授权或目录过滤行为。
- 修复后新测试 59 项通过，其中 18 项使用两种字段类型、三种交付输入方式及三条业务分支，
  经 Recipe 编译、classic runner、真实 SQLite 隔离 Backend 和 Evaluator 执行。仅 Agent 为
  输入捕获替身，不根据预期答案模拟业务回答；各次业务哨兵和非目标记录均未变。

| 实际接入最终 Agent 的证据 | 离线核对结论 |
| --- | --- |
| 写前记录 + `matched/affected` | 写入正确，但输入不含更新值；保留此反例，不能宣称模型知道写后字段。 |
| 再显式接入本次固定请求值 | 请求内容可见，但仍不能当作写后读取的记录或新 revision。 |
| 再显式安排并接入写后 Query | 新字段和 revision 与隔离 Backend 实际记录一致，回执与时点状态同时可用。 |
| 无记录 / 不满足修改条件 | 前者走错误终点且不调用 Agent；后者不执行写入，不向 Agent 混入另一分支回执。 |

针对性组合回归 185 项通过；未改动的固定真实候选另有 24 项离线重放通过，包含基线和候选
各自的隔离执行，共 48 个执行项，仍不评判真实汇总答案。前端生产构建通过，保留既有大块
体积告警；4 个 Python 文件语法 AST 检查通过，本批 5 文件已知凭据模式扫描零命中。
扩大回归 **1644 项通过，0 失败、0 错误、0 跳过**，覆盖 Meta Planner 全部测试文件及节点契约、
Authoring、Publish、Evaluator、App、Structure Evolution 和类型化工作流；包含上述 185 项，
不可相加当作独立样本。可重放命令：

```powershell
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -B .tmp-recovery-e/run_offline.py server/tests/test_meta_planner_write_delivery_contract.py server/tests/test_meta_planner_table_prompt_contract.py server/tests/test_meta_planner_generation_contract.py server/tests/test_meta_planner_harness_closeout.py server/tests/test_meta_planner_recipe_integration.py server/tests/test_meta_planner_recipe_text_inputs.py server/tests/test_meta_planner_recipe_model_repair.py server/tests/test_meta_planner_scoped_write_modes.py --junitxml=.tmp-recovery-e/delivery-focused.xml
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -B .tmp-recovery-e/run_offline.py .tmp-recovery-e/acceptance-20260927/test_fixed_candidate.py --junitxml=.tmp-recovery-e/delivery-fixed-candidate.xml
$tests = @(Get-ChildItem -LiteralPath server/tests -Filter 'test_meta_planner*.py' | Sort-Object Name | ForEach-Object { 'server/tests/' + $_.Name })
$tests += @('server/tests/test_meta_agent.py', 'server/tests/test_workflow_node_contracts.py', 'server/tests/test_xpert_runtime_authoring.py', 'server/tests/test_xpert_publish.py', 'server/tests/test_xpert_evaluations.py', 'server/tests/test_xpert_structure_evolutions.py', 'server/tests/test_xpert_app_api.py', 'server/tests/test_workflow_typed_values.py', 'server/tests/test_workflow_typed_ai.py')
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -B .tmp-recovery-e/run_offline.py @tests --junitxml=.tmp-recovery-e/delivery-regression.xml
```

原始结果保留在本工作树的忽略目录 `.tmp-recovery-e/`，不提交日志：

| 证据 | SHA-256 |
| --- | --- |
| `delivery-focused.xml` | `e0367f338f448f929bc05f2523b8cea18c9aa6fe79643630a0caa9238af68d60` |
| `delivery-fixed-candidate.xml` | `e61c6e15379d17333178f1a2615ea60d53818fb688205b2aead0f89090549968` |
| `delivery-regression.xml` | `25c378795a7648abddd820f9536fc503c3f5644dc0948d49f55f539753bf966b` |

先前验收结果仍以 [2026-09-27 验收审计](audits/META_PLANNER_CW10_ACCEPTANCE_20260927.md) 为准。
本批没有重跑完整 `server/tests/`，不把先前已复现于基线的红项改为通过；也没有重跑真实生成或
汇总，不能声称 `contains` 已提高或泛化稳定。原固定 Proposal、Dataset 和预览运行结果不变。

## 职责分工

| 模型必须决定 | 服务端确定性派生 |
| --- | --- |
| 节点 kind/ref、中文标题、说明、任务归属 | 原生节点 ID、Handle、布局及执行契约 |
| Adapter 业务配置、条件、数据来源端口 | 输入变量和其真实来源类型、输出变量与端口 Schema |
| 顺序、分支 outcome、并行路径 | 现有 GraphIntent 控制边，不依据文字目标猜接边 |
| 授权资源 ID、绑定目标、Prompt Profile | 现有资源版本、安全摘要和权限检查 |
| Agent Prompt、最终来源 | 模板来源去重、稳定变量；显式选用文本模式时的受控序列化 |

`json_deserialize.config.expected_schema` 仍是模型提出、Runtime 必须验证的业务契约，
不能删除它或把 `any` 当作具体记录。Aggregator 字段映射及输入顺序、Merge 左右来源、
Update/Delete 的直接可信记录来源仍由模型显式选择。编译器不补空值保护、业务条件或业务节点，
不把互斥分支串行化，不缩减用户目标来换取通过。

## 描述格式

顶层必须含 `generation_protocol_version=1`，`ir_version` 保持 3。节点没有 `outputs`；
数组形式的输入仅有 `port/source_ref/source_port`，每个节点必须显式提供 `config`。
仅 `workflow_agent` 可显式使用 `inputs=null`，从其模板派生文本输入；省略 inputs 仍按旧空数组读取。
配置 Schema 来自同一 Adapter，按授权 kind 投影；未知字段失败，不另设原生配置后门。

```json
{
  "generation_protocol_version": 1,
  "ir_version": 3,
  "name": "中文摘要",
  "nodes": [{
    "ref": "answer",
    "kind": "workflow_agent",
    "title": "形成摘要",
    "task_ids": ["answer"],
    "inputs": null,
    "config": {
      "role_prompt": "忠实概括输入。",
      "task_input": "{{input.user_input}}"
    }
  }],
  "control_flow": [{"type": "node", "node_ref": "answer"}],
  "final_output": {"sources": [{"node_ref": "answer", "port": "result"}]}
}
```

上例仅说明字段形状，必须与实际固定任务计划及授权共同校验，不是自动追加的模板。

### 显式文本输入模式

`workflow_agent.inputs=null` 时，模型只在 `role_prompt/task_input` 中声明
`{{source_ref.source_port}}`。编译器按两个字段内首次出现的顺序派生并去重 `task` 输入，
最多 50 个；重复占位符仍保留在 Prompt 中。未知来源、原生变量名和属性路径一律拒绝。
没有模板来源不会自动补入 `input.user_input`，仍由原必填输入门禁拒绝。

来自权威端口的字符串直接连接；其他 JSON-safe 类型必须经过普通 `json_serialize` V2、
`format=compact` 节点。它是候选内可见、可编辑的既有纯节点，不是假装把源 Schema 改成字符串，
也不是 Runtime 的隐式 `str(...)`。nullable/union/any 来源同样执行真实 JSON 序列化，
因此字符串分量可能带 JSON 引号；不能把它描述为与直接字符串传递完全相同。
真实执行继续遵守 JSON V2 的错误和 5 MiB 上限。

- 只有当前授权和有效 Adapter 同时包含 `json_serialize` 才能插入；不扩大纯节点授权。
- 稳定 ref 从 consumer/source/port 派生，冲突即拒绝；每个 Agent 使用本分支内的适配节点，
  不把分支结果提升到共同前驱，也不跨互斥分支共用适配节点。
- 模型控制树只放置原始业务节点；编译器将适配链插在该 Agent 之前，保留所有入边 outcome。
  不从数据引用猜控制先后，不补路由/空值保护，漏节点和分支独有数据仍由原门禁阻断。
- 适配节点及边计入原 24/40 上限。最终图、Patch、资源授权、路径分析和发布检查照常执行。
- 数组形式保持严格兼容：来源必须出现在显式 inputs 中，Agent task 只能接字符串；
  旧失败产物不会被隐式改写。要使用新方式必须显式把该 Agent 的 inputs 改为 null。

`recipe_preflight.text_input_lowering` 只保存派生 Agent 数和适配节点数，不保存模板或来源正文。

- `control_flow` 数组表示顺序；路由项的 `branches` 必须完整列出其实际语义 outcome。
- 分支为 `{outcome_ref, steps}`，`steps` 使用同样的结构。空分支仅表示显式绕行，必须有后续合流，
  不能让一个 outcome 无终点。成功来源是否互斥仍由原 ControlFlowAnalyzer 判定。
- 并行为 `{type: "parallel", paths: [[...], [...]]}`；每条路径非空，不隐式建立左右顺序。
- 每个执行 ref 恰好出现一次。共同后续节点放在结构之后，不能用重复 ref 表达循环或共享步骤。
- 仅支持这种有界结构化控制流；不宣称能表达任意 DAG。既有完整 GraphIntent/工作流读取不受限制。
- `resources/middleware/prompt_profile_ids` 沿用现有严格绑定契约，不新增资源能力。

生成资源选择统一由 `resource_generation_contract` 从当前授权与 Capability 交集投影：
节点自有资源只使用 `resource_ref.resource_id`；顶层 `resources` 只绑定四类 Agent 资源，
不包含 Agent Table。没有 Agent 绑定授权时，私有 Schema 将 `resources` 限制为空数组；
节点资源 ID 和绑定 kind/ID 同样按授权缩窄。Query 与各写操作授权互不继承。
无活动索引的知识库不进入知识检索节点候选，但旧 Agent 知识绑定的 warning 语义不变。
Recipe 与兼容 GraphIntent 提示使用同一投影；这不是约束解码或替代服务端授权检查。

上限：24 个执行节点、40 条派生控制边、8 层控制结构、每个并行项最多 8 条路径；
JSON 最深 32 层且不超过 1 MiB。原路由、符号场景、类型、权限、调用和写入预算仍全部适用。

## 解析与修复

```text
任务规划 -> 简化描述解析 -> recipe_lowering（控制结构及可信端口派生）-> GraphIntent V3
         -> 授权 -> 资源/类型/路径解析 -> Native 编译 -> 发布预检 -> pending Proposal
```

输出类型由 Adapter 配置及资源快照确定；输入类型完整继承已选来源，不能向 consumer 期望类型
收窄。动态谓词的资源校验在完整输入派生之后执行，不移除校验。类型不匹配或路径不能保证值存在
仍被拒绝。普通输入端口从 NodeContract 读取，视觉附件仍只能使用现有可信附件槽位。

模型初次返回旧完整 GraphIntent，即使省略协议标记，也不接受为新生成成功；最多使用既有唯一
修复机会。严格解析成功后，唯一修复使用 `recipe_edits_v1`，包括已产生 Intent 但后续语义门禁
失败的情况。未解析的描述仍使用 `generation_recipe_v1` 完整修复。已解析描述完整进入修复上下文，
但模型只提交受限操作，不重抄整个图；变量、类型与原生连边仍由服务端派生。未解析文本沿用
有界原文反馈。三次总调用预算不变，不增加自动重试。
旧 Intent-only 兼容入口仍使用 `graph_patch_v1`，不猜测还原其结构化 Recipe。

Condition/Multi Route 的输入选择、类型、空值及 outcome 真值含义由同一控制契约投影到生成和
修复提示。控制证明分别记录结构、条件输入域、分支可达性、终点和数据可用性，状态明确区分
`passed/failed/blocked`。前置输入域失败后的空依赖诊断不是数据可用性通过；重新修正条件后
必须再检查此前未证明的分支和数据。独立反例已能确认的错误仍显示 failed，不被前置阻断掩盖。

控制引用错误区分 `RECIPE_UNKNOWN_NODE`、`RECIPE_REPEATED_NODE` 和 `RECIPE_OMITTED_NODE`。
诊断提供受限 ref、结构位置及重复引用的首次位置；重复 ref 是当前结构语法不支持的表达，
不再被直接等同于控制循环。唯一一次修复会收到这些结构化诊断及边界清单。
分支遗漏、重复或多余统一记录 `RECIPE_BRANCH_OUTCOMES_MISMATCH`，包含 `branches` 的位置、
期望出口、实际计数及缺失/重复/多余差异；普通节点不能通过附加 branches 获得新出口。
修复仍失败且规范化描述未变化时记录 `RECIPE_REPAIR_UNCHANGED`，不增加调用。
若描述已变化，但同一个结构化错误及整个控制结构仍未变化，另记录
`RECIPE_CONTROL_FLOW_UNCHANGED`。此信息只是进展观测，不是接受条件；配置修复确实消除原错误时，
不会因控制结构未变而被该观测拒绝。是否可接受始终由完整重校验决定。
兼容诊断字段 `recompile_executed` 表示进入共享重校验函数；是否真正到达 Native 编译须读取
`phase_results.compile`。Recipe 在解析阶段被拒绝时后者仍为 blocked，不表示执行过节点。

### 局部预检与修复上下文

节点配置、范围、来源端口和资源事实解析完成后，`recipe_preflight` 复用已有 Adapter、类型检查
及 ControlFlowAnalyzer 的有界输入分区，收集局部输入基数、类型和单个路由事实。
因此，控制结构展开失败不会再把这些已经可证实的问题全部藏在首个分支错误之后。
配置、授权范围或输入来源尚未解析完成时标记 `blocked`，问题数为未知，不报成零问题。

可信节点事实的准备不再包含 Prompt 插值。准备完成后，模板引用、局部类型/谓词和控制树
分别检查；模板失败仍拒绝生成 GraphIntent，但不会遮蔽已能独立确定的类型错误或控制树问题。
`RECIPE_TEMPLATE_REFERENCE_INVALID`、`RECIPE_TEMPLATE_SOURCE_UNKNOWN` 和
`RECIPE_TEMPLATE_INPUT_MISSING` 区分引用形式、不存在的来源和缺少显式输入。
诊断保留节点及 `config.role_prompt/task_input` 位置；只有已解析且安全的来源 ref/port
可进入详细反馈，未知或敏感引用仅保留 checksum，不保存 Prompt 正文。

`template_bindings_status`、`invalid_template_reference_count` 和 `control_structure_status`
描述各自局部检查，不代替完整路径证明。引用计数按无效占位符出现次数统计，详细诊断仍按
既有上限去重、最多保存 64 条并报告省略数。即使有多个错误，也只使用已有唯一修复请求；
不会替旧显式绑定补输入、删除节点或连接分支；只有上述 `inputs=null` 模式执行受控文本适配。

- 最多保留 64 条类型问题，另记录总数与省略数；最多证明 8 个路由，超出只记 blocked。
- 输入类型及谓词可能出口不依赖伪造的控制边，不构建替代图、不自动选择分支。
- 若整个输入域无合法 outcome，或某个声明出口在完整局部分区中不可达，记录 failed。
- 若合法值可到达全部出口，但部分空值会抛错，记录 `blocked/path_guard`；上游判空保护可能使
  全图合法，不能仅凭局部空值错误拒绝这种图。
- 局部结果始终标明完整路径证明尚未执行；可达性、终点、数据可用性及发布仍走原全图门禁。
- 诊断不带业务取值、Prompt、标签、记录或字段正文；敏感形态 ref/port 仅保留 checksum。

首次唯一修复和已有显式语义修复复用同一反馈入口。前置排列本次错误、局部事实与修复清单，
再给目标、固定计划、失败描述及完整权威契约，避免先展开重复 Schema 才看到失败依据。
模型 Schema 通过 `$defs` 和 `allOf` 共享节点公共字段，保留各 kind 的 Adapter 配置、资源范围、
任务归属和未知字段拒绝规则。它仍是同一个 Recipe V1，不是删减验证或新协议。
生成提示提供领域无关的互斥结构片段；不填入测试目标的字段、阈值或正确分支。

去重只保证重复结构减少，不保证整个修复提示更短；有用诊断也会占用上下文。
字符长度不是模型 token 使用量，离线接受边界与反例不能证明真实模型注意力或成功率改善。

### 编译来源映射与可操作修复反馈

文本适配错误可能发生在编译器生成的 JSON Serialize 节点上，但模型只能修改原始 Recipe。
每次修复重新降低严格解析的 Recipe，生成服务端私有 `input_origins`：

- 数组输入映射到原始 `nodes[i].inputs[j]`；模板派生输入映射到实际引用的
  `config.role_prompt/task_input`，同一字段内重复引用去重，跨字段引用保留两个位置。
- 新插入序列化节点的输入继承该来源映射，不从 `text_` 前缀或标题猜测。
  原始节点本身可以使用类似前缀，仍按真实编译来源判断。
- 映射在本次编译中重新生成，不接受模型字段、旧报告或客户端声明；解析/授权准备失败时
  不复用上次映射。它不是 Graph IR 字段，不进入候选 checksum 或 Runtime 数据。

修复请求前置 `repair_focus`，将数据路径反例投影到原始消费节点、配置/输入位置及唯一控制位置，
并保留源 ref/port、违反场景数和语义 outcome。它不保存模板、业务值、记录或 witness 正文；
敏感形态 ref 使用 checksum。最多 64 条，另报总数与省略数。来源映射缺失或与实际输入不符时
显示 `mapping_status=unavailable`，不得猜测位置或把失败当作通过。
`path_proof_status` 同时前置，区分 `failed/passed/blocked`；解析或授权尚未通过时，
依赖问题列表为空仍必须显示 blocked，不能视为路径证明成功。

修复仍由模型决定业务结构。必要时可用不同的互斥 Agent ref 覆盖同一固定任务，但必须保留
真实业务步骤、任务和授权；服务端不自动拆分 Agent、补空值、移动写节点或选择终点。
完整路径证明、类型、授权及发布门禁继续执行。该反馈同时用于唯一自动修复和已有显式语义修复，
不增加模型次数或自动重试。

修复 Prompt 中的 `lowering_diagnostics` 只投影错误与阶段状态，不重复发送完整绑定遥测、
路由字段 checksum 和全图 checksum；依赖反例集中在 `repair_focus`，局部反馈通过数量和字段名
指向它。完整被动诊断报告仍保持原样，`required_schema`、节点/资源契约、授权和失败 Recipe
均不删减。首次生成提示和实际编译结果不因此改变。

#### 本批核验边界

- 工作树 `codex/meta-planner-cw10-recovery-e`，基线 `09e8a7d6`；仅修改 Recipe 编译、局部预检、
  Planner 修复提示、专项测试及本文五个文件，保护此前的 184 项既有变更。
- 历史 `proposal_8122b55b495d4b89887f03e6f7c99baf` 两次原始描述离线回放均仍失败；
  Snapshot、首次生成请求和降低后 Intent checksum 与原回执一致，Proposal、表库和调用账本未变。
  新反馈定位 `conclude_agent.config.task_input` 及库存未触发更新的反例，不改写历史产物。
- 同一样例的修复 user Prompt 从此前核验的 44,674 字符降至 40,734 字符；完整 Schema 与授权
  不变。这是字符对照，不是 token 测量或真实模型成功率证据。
- 专项测试入口为 `server/tests/test_meta_planner_recipe_source_feedback.py`，使用
  `.tmp-recovery-e/run_offline.py` 禁用凭据加载和外网，只写隔离临时 Store。
  首次开工红测未完成：沙箱下隔离目录启动阻塞后停止，不计作通过或已复现红测。
  扩展关联组首次 `131 passed / 1 failed`；失败来自新增服务测试误用允许缺失/空值的 Schema，
  与分支夹具的必填数值契约不一致。仅统一该测试快照，不放宽生产路径校验或测试断言。
- 最终六文件关联组 136 项通过（136.22 秒，4 条 FastAPI 既有弃用警告），其中新增专项 26 项，覆盖来源伪造、
  缺失映射、双字段引用、错误 outcome、前置 blocked、唯一修复预算和 pending Proposal。
  4 个 Python 文件 AST 语法检查、`npm.cmd run build` 和本批五文件敏感模式扫描通过；
  前端仍有大 bundle 警告。
- 扩大回归覆盖全部 `test_meta_planner*.py`，以及 Meta Agent、NodeContract、Authoring、
  Publish、Evaluator、Structure Evolution、App 和 typed Workflow 的九个既有测试文件；
  1546 项通过（657.46 秒，4 条相同弃用警告），包含上述关联组，不重复累计数量。
  离线命令为 `python -B .tmp-recovery-e/run_offline.py <上述文件列表>`，结果保存于忽略目录的
  `source-feedback-focused-final.xml` 与 `source-feedback-affected-final.xml`。
  全工作树 `git diff --check`、本批五文件空白及敏感模式扫描通过；暂存区为空，185 项未提交路径
  为既有 184 项加新专项测试文件，没有 Runtime 数据、SQLite 或构建产物进入待提交清单。
- 历史回放另一次临时断言失败来自校验脚本使用了省略 null 的模型 checksum，而历史产物使用
  完整保留字典 checksum；改用既有 `_retained_intent` 核对后两次产物均严格相等，未修改生产契约。
- 真实模型、最新上游及全量后端仍是独立门禁。本批不操作预览器或共享栈、不写活表，
  不审批、发布或提交 PR，不能据此宣布本轮已稳定或达到 PR 门禁。

Schema 约束不是 Provider 的约束解码保证，也不证明业务意图正确。生成证据比对原始模型结构，
不把降低后的图冒充模型返回；`generation_input_format` 描述初次输入，`repair_protocol` 单独描述修复。
编译后的安全 IR 仍保存在原报告，不持久化新的描述 Store。

## 失败与兼容

- 旧 V2/V3 候选、Headless 和 Runtime 继续沿原路径读取；不强制迁移，审批始终针对实际候选。
- 已降低的失败 Intent 复用 A/B 的私有产物和人工修复。严格解析、通过配置与敏感信息检查但尚未
  降低为 Intent 的 Recipe，复用同一私有产物保存规范化描述及独立 checksum。它不是可执行图，
  不写入普通报告，也不冒充原始 Provider 字节；无法解析、敏感或超限时仍只保留安全诊断。
- 新的已降低失败同时保留 `source_recipe/source_recipe_checksum` 与 Intent；独立 checksum 和
  重新降低结果必须一致。`recipe` 字段仍专指尚未降低的旧恢复形态，避免改变其整包编辑门禁。
  单份 Recipe/Intent 各不超过 1 MiB，包含所有尝试及诊断的私有产物整体不超过既有 2 MiB 上限，
  不新建 Store。旧 Intent-only 失败不强制迁移。
- Schema 合法的 Recipe 恢复入口接受 `recipe_edits_v1`，复用下述四种受限语义操作；任务、资源、
  模型身份与授权保持锁定。旧 `recipe_control_flow_v1` 的单个控制树替换继续兼容。
  人工和模型建议均必须重新通过完整编译与发布预检，不能保存一份可批准的部分修复。
  应用须绑定产物、提案、候选、授权、契约及资源快照，成功后只更新 pending Proposal 一次。
  普通整包编辑不能把该占位候选改成可批准图；不能把未展开的 Recipe 伪装成 Graph Patch。
- 若严格解析仅在 `resources[i].kind` 枚举失败，可保留为 `recipe_resource_draft_v1`，不是合法 Recipe。
  其余结构与 Adapter 配置必须有效，资源 ID 必须属于当前授权目录，并继续执行大小、深度、有限数值和
  敏感信息检查。未知字段、其他 Schema 错误及不在目录的 ID 不能走此保留路径。
  管理端只允许 `recipe_resource_bindings_v1` 的一个 `replace_recipe_resources` 操作；替换数组仍须
  严格符合四类 Agent 绑定契约及原授权。节点、控制结构和任务不变，其他语义门禁仍失败时不能应用。
  该路径不自动纠正模型输出、不重试、不触发付费修复，不扩大严格生成 Schema。
- 此恢复能力仅覆盖修改后的新失败产物；过去未保留正文的 E-G1 提案不能追溯重建。
  本次没有通用 JSON 编辑器，也没有扩大任务、节点或模型调用授权。
- 原有失败占位仍不可批准，不会因为格式正确、生成完成或通过静态检查就执行业务写入。
- 模型仍可能选错业务来源、条件或路径；本批只减少重复机械声明，不能据离线绿测宣称生成稳定。

## 显式语义修复

严格解析并安全保留的未展开 Recipe，以及已有 Intent 且保留匹配 `source_recipe` 的失败，
均可在 D 的同一个一次授权入口请求语义修复。资源/输入种类失配的宽容保留草稿不因此开放模型修复。
预检使用受限语义修改 Schema、原节点对应 Adapter 配置、固定表的安全字段 Schema、控制语义和诊断；
外发确认显示 `repair_protocol=recipe_edits_v1`。不发送完整资源快照或表记录。
配对 checksum、资源 Schema 或重新降低结果不一致时，外发前拒绝，不降级为猜测修复。

模型只返回受限操作，服务端原子合并到固定 Recipe。未展开的 Recipe 直接使用绑定 revision/产物/候选
checksum 的 `RecipeEditsPatchV1` 进入完整预览；已有 Intent 才派生既有 Graph Patch、应用到原 Intent，并核对规范化
语义往返。输入顺序保留，重复边按完整键断开一次；尽量保留可复用前缀，避免全量重连。
派生结果仍最多 64 操作，无法表达时标记 `repair_patch_unrepresentable`，要求分批人工修改，
不扩大预算、自动拆分、追加调用或直接写入图。输出 Schema 仅由 Adapter 与资源快照决定。

发送守卫复核实际正文、路由和 Proposal/目标/资源依据，Managed 在异步客户端进入后、派发标记前
执行守卫。已派发后状态变化仍可能产生费用，结果变 stale，禁止载入；这不是跨 Store/Provider
原子事务。模型返回后继续执行原完整 Preview，最终仍须人工载入、预览、确认 Apply。
没有配对 Recipe 的旧 Intent 保留原 Graph Patch 修复。每次追加调用仍需独立预检、正文审阅和显式费用确认；
开放入口不等于继承已消费的授权，不自动调用、载入、应用或批准。

### 受限修改内核

自动唯一修复与人工逐次授权修复复用 `recipe_edits.py`，不是新的运行协议或权限权威：

| 操作 | 范围 |
| --- | --- |
| `update_node` | 只改原节点显式提供的标题、说明、Adapter 配置或输入；未给字段保持原值，kind/ref/task/resource 不可改。 |
| `clone_agent` | 只复制原有无资源/中间件绑定 Agent；任务、模型及其余配置继承原节点，显式指定新 ref、标题和任务输入。 |
| `replace_control_flow` | 只替换 Recipe 控制树；原节点仍必须恰好出现一次，分支、数据可用性和条件类型继续完整校验。 |
| `set_final_output` | 设置语义最终来源；互斥与唯一到达继续由原控制证明核验。 |

- 每次最多 16 操作、64 KiB，同一节点最多修改一次；整批深拷贝原子合并，拒绝重复目标、未知字段及原生配置注入。空操作可表达无法安全修复，但与无变化操作一样判定为 `RECIPE_REPAIR_UNCHANGED`，不算成功、不重试。
- 不能删除业务节点、新增读写节点、变更资源/任务/模型身份或扩大授权。无法在此范围内修复时保留失败，转人工编辑，不偷偷切回全文生成。
- `inputs=null` 与首次生成系统提示一致：Agent 从模板派生数据来源；显式数组仍必须覆盖全部模板引用。
- 修复提示保留完整原描述与源位置反例，只投影原图涉及的节点契约。表字段仅提供名称、类型、必填、Schema 版本和 checksum；不包含默认值、描述或记录。
- 四种操作的必填/可选字段直接从同一 Pydantic Schema 派生，并置于失败描述前。`replace_control_flow`
  必须提交 `control_flow`，不能包在 `config` 内；`update_node` 必须提交 `node_ref`，不靠猜测补全。
- 模型返回操作的 Provider/collector/validator checksum 独立配对，服务端合并的 Recipe 只进入编译，不冒充 Provider 输出。
- 合并前失败保存原输入及 `repair_input`，不伪造新图；合并后仍需经过降低、授权、类型、资源、控制流、编译和发布预检。
- 修复未执行到原失败阶段时，进展标记 `not_rechecked`，相关问题数为 null 而非零；界面明确显示“尚未复核”。
- 未增加自动调用、Store、IR、SSE、Runner 或审批行为。新建议是待人工预览的受限 Recipe 操作或 Graph Patch；过去回执与 Proposal 继续可读。
- 此修改不证明外部模型能正确选择业务分支；真实成功率、操作预览器验收和整轮提交门禁仍需单独验证。

### 已保留失败样本的离线核对

2026-09-27，对 `proposal_9688bb091d1b463c89b614b62f3c8ef2` 的原始生成描述做只读回放，
当前安全 Capability Snapshot checksum 与原报告一致。原图仍在公共结论读取仅部分路径存在的
写入结果处失败；没有把已知失败改成通过。

人工构造三项受限操作（复制无绑定结论 Agent、调整控制树、设置两个互斥最终来源），经过同一
`apply_recipe_edits`、降低、授权、类型、路径与 Native Workflow 校验后通过，保留三种符号场景。
全部原节点及资源/中间件绑定保持不变。只验证协议和编译器可表达该修复，没有调用模型、执行
工作流、发布预检回调或写入业务记录；受保护提案、表文件、调用账本与授权记录 hash 保持不变。

同一失败上下文的用户提示正文由 40,448 字符降至 22,752 字符，减少 43.75%。这只是本地字符计数，
不是实测 Token、费用下降或模型成功率。模型能否正确选择这三项操作仍待单独授权实测。

## 验证分层

### 2026-10-02：写入请求证据由编译器派生

固定候选回放暴露了一个确定性缺口：更新回执只有 `matched/affected`，即使写入成功，
汇总 Agent 也不因此获知请求的新字段值。仅在生成 Prompt 中要求模型手工抄写请求值不能保证交付。

- Planner Native 编译阶段只针对显式消费 Update 回执的 Agent 派生受限请求上下文；
  允许追踪透明 JSON Serialize 输入，不穿过 Agent、字段映射或任意聚合猜测来源。
- `literal` 来自同一已校验 Update 配置；`input` 只标记已显式接入的同一 JSON Deserialize
  来源。缺少动态请求值时明确标记缺失，不补连线、不加查询、不扩大输出回执。
- Graph Intent 与模型配置不保存这份派生副本。Native Agent 的 `taskInput` 附带上下文，
  `plannerWriteRequestContextV1` 保存可验证的编译器附录；反编译恢复原始模板，重新编译再从
  当前写入配置生成，避免 Headless 改值后保留过期证据。编辑器不得篡改该附录。
- 固定值以紧凑 JSON 编码，转义模板起始字符，禁止将业务字符串解释为变量引用；
  附录和源模板合计仍受既有 8,000 字符上限约束，超限拒绝，不截断。
- 请求值不是写后状态；仍须核对真实回执，`affected=0` 不得宣称成功。需要实际写后状态时，
  必须已有查询授权与显式写后查询，不暗示新 revision 或跨节点事务。
- 已保存候选和已发布 Workflow 不自动修改。旧候选往返核对仅投影掉新编译器附录，保留
  其他字段的严格比较；实际升级只能在用户预览并确认 Apply 后保存，revision/checksum 门禁不变。

该修订不增加模型调用或 Runtime 行为。不同字段、值、隔离效果和编辑漂移的自动化证据，
不能替代同版真实模型及浏览器验收。关闭派生入口时须保留附录反编译读取兼容；不批量重写
历史 Proposal，不重放调用或撤销业务写入。完整门禁见
[收口记录](./audits/META_PLANNER_CW10_CLOSEOUT_20261002.md)。

1. 当前协议：使用真实 Service + 模拟 completion，检查新 Schema、严格降级拒绝、预算、修复与 Proposal。
2. 传输证据：通过离线 legacy/managed collector 对比发送 Schema 与接收/校验输入，不访问真实 Provider。
3. 确定性矩阵：不同业务字段、空值/写入/不写入/读取失败、并行 Merge、纯节点、知识、视觉和绑定往返。
4. 临时 SQLite：冻结分支样例执行真实隔离 Backend，Agent 使用模拟响应；不是模型生成质量或活表验收。
5. 历史兼容：`tests/meta_planner_legacy_replay.py` 仅回放已解析旧图的机械故障，不是当前模型入口证据。
   它不绕过权限、类型、资源、控制流、Patch、发布门禁；真实生成/传输/预算测试不得使用该替身。

实际命令、失败分类和未完成门禁见 [分批记录](./tasks/META_PLANNER_SYSTEM_RECOVERY_BATCHES.md)。
真实 Provider 成功率、完整前端人工验收、最新上游和全量测试属于独立集成门禁，不能由本矩阵替代。

## 回退

受限修改可回退修复入口的协议选择，停用 `recipe_edits_v1`；保留历史回执、Recipe/Intent 与待审批
Proposal。显式修复预检正文或协议变化后旧授权 checksum 自动失效，必须重新确认，不重放已派发调用。

来源映射收口可单独恢复此前修复提示和预检投影，停用私有 `input_origins` 输出；普通编译、
Graph IR、持久化产物和 Runtime 均不依赖它，无数据迁移。不删除历史失败或重放已消费的真实调用。

本次文本适配可先从生成与显式修复提示中停用 `inputs=null`，恢复显式输入及手工序列化要求；
保留解析/降低读取，以便已有 null Recipe 失败产物继续校验配对 checksum。已经降低的候选仅含
普通 JSON Serialize V2，不需要 Runtime 或数据迁移。不要删除历史产物或更改它们的 checksum。

停止新生成及显式模型修复，恢复先前生成 Prompt 和协议选择；保留已保存 GraphIntent、Proposal 和
失败工件读取。新增 `source_recipe` 不占用旧 `recipe` 判别字段，旧读取可继续使用配对 Intent。
不删除数据，不撤销业务写入，不扩大节点权限或提前进入 V4。
