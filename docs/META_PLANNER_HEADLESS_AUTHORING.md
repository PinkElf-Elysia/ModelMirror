# Meta Planner Headless Authoring

最后更新日期：2026-09-20
状态：V3 Round 7 实施契约，验收证据见对应任务记录

## 目标

Headless Authoring 是 Meta Planner、管理端编辑器和后续 Optimizer 共用的候选编排协议。
它只修改 pending `AuthoringProposal`，不写 Xpert 草稿、不发布版本，也不启动 Workflow。

固定链路：

```text
Proposal state -> editor diff / typed patch -> side-effect-free preview
-> checksum-bound apply -> existing proposal validation -> human approval
```

## Patch 边界

`GraphPatchEnvelopeV1` 最多包含 64 个有序操作，并绑定 Proposal revision、当前 Graph
authoring checksum 和当前 compiled candidate checksum。

四个 Headless API 的 JSON 正文最多 2 MiB、嵌套最多 32 层；超限请求在进入
Pydantic、Adapter 或 Proposal 服务前失败关闭。

Patch 只接受 Planner ref、命名端口和 Adapter config。原生节点 ID、Handle、资源版本、
NodeContract Schema、执行策略与 checksum 均由服务端推导，客户端或模型不能注入。

支持 Xpert 元数据、Workflow Agent、五种纯数据 Adapter、四种控制流 Adapter、两种
只读资源 Adapter、视觉 V2、三个受控写入 Adapter、控制/data 边、输出变量、四类 Agent 资源、中间件、Prompt Profile、
最终输出和布局。`input/output` 由编译器管理；
布局不会改变 Graph checksum，但会改变 candidate checksum。纯节点配置、原生变量名、
端口 Schema 和 binding ID 由 Adapter 推导，不能通过 Patch 注入。

Patch 按顺序作用于内存副本。每步仍检查配置 Schema、ref、命名端口与任务授权；
同一批次内允许输入连线暂时不完整，例如“断开旧输入、更新配置、连接新输入”。
依赖完整连线的节点形状与输入基数，在所有操作及显式依赖解除完成后统一校验。
最终无效节点仍拒绝，后续完整类型、控制流、资源及发布门禁不变。服务端不会自动
重排操作、猜测缺失边或持久化中间状态；新增 ref 仍必须先创建再使用。

`update_node.config` 提供对象时完整替换配置，不做字段合并；省略或 `null` 保留原配置。
修改一项配置时也必须保留其他必要字段。这个语义直接标注在操作 Schema，模型命令摘要
从同一 Schema 投影，不另设一套 Patch 规则。配置改变后，输入端口按最终配置核对；
原图诊断中的端口不是修复后必须保留的端口，失效输入必须在同批 Patch 中显式解除。

## 预览与应用

Preview 从实际 Proposal 反编译 GraphIntent，重新解析 Graph IR、编译候选，并执行
Workflow、资源和发布预检。Preview 不持久化、不调用模型，也不创建运行。

Apply 必须携带 Preview checksum。服务端重新读取 Proposal、能力快照和目标 Xpert，
再完整重算 Preview。以下任一变化均拒绝应用：

- Proposal revision 或候选 checksum 漂移。
- 更新目标的 draft revision 漂移。
- 相关 NodeContract、Adapter、资源版本或动态 Schema 失效。
- Patch 端口、类型、基数、控制图、授权或最终输出不合法。

`set_node_resource` 用于知识检索、数据表查询及三个 V2 写节点的节点自有资源，
不能替代 Agent 的 `bind_resource`。Preview/Apply 会重新解析知识库活动索引或固定的
Agent Table SchemaVersion；知识活动指针变化给出 warning，表 Schema checksum、资源
授权或固定版本失效则阻断。模型和客户端仍不能提交原生资源字段或版本。

无关 Capability Snapshot 变化只产生 warning。Apply 成功只增加一次 Proposal revision，
随后复用现有 Authoring validation。安全 receipt 最多保留 20 条，只记录操作类型、前后
checksum、诊断计数和时间。历史 receipt 会按安全字段重新规范化，未知字段和无法验证的
记录不会继续进入 typed Apply。Proposal 写盘失败时，内存 revision 与 payload 同步回滚。

## 编辑器与兼容

管理端画布先调用 `editor-diff` 将差异转换成 Graph Patch，再执行 Preview 和用户确认。
原始 `WorkflowDefinition` 从不直接进入 Headless 持久化。Proposal 授权范围之外的节点、
未授权中间件和 compiler-managed 节点修改均 fail-closed；无法表达的编辑返回逐项诊断。
Meta Planner Proposal 创建时的 `authorized_scope` 是不可扩大的授权上界；旧整包 PATCH
可以编辑候选内容，但不能修改该上界，运行时仍只使用它与当前能力快照的交集。

- Graph IR V3 使用 Headless Authoring。
- 可唯一恢复变量来源的 V2 候选可 Preview，并在首次 Apply 时升级为 V3。
- `lossy_conversion` 的 V2 候选保持旧兼容读取、校验和审批路径，禁止 Headless Apply。
- 旧整包 Proposal PATCH 继续可用，但会将 Graph IR 标记为 `stale`。

CW10 批次 C 的模型首次输出私有简化描述，服务端确定性降低为 GraphIntent V3。
2026-09-23 语义收口后，新生成的唯一修复也使用完整 `generation_recipe_v1` 描述；已降低但
语义失败时不再切换模型输出语言。旧 Intent-only 兼容入口保留 Graph Patch。总调用上限仍为 3。
见 [简化生成描述](./META_PLANNER_GENERATION_RECIPE.md)。Headless 仍以原 GraphIntent/Patch 为权威，
不接受简化描述直接覆盖 Proposal。
兼容 Graph Patch 修复应用后，服务端会按 Adapter 配置刷新其派生输出 Schema，并记录
安全 warning；正常生成路径中的 Schema 伪造、未知端口和非法配置仍然 fail-closed。

单模型修复输入将错误与修复契约置于参考材料之前，按“完整最终配置、输入对齐、整批
核对”组织步骤，并用紧凑 JSON 保留完整授权、原图和操作 Schema。表 Adapter 共用说明
区分可信 `records` 的身份/revision 校验与非空业务 `filter`；不能将整个记录接到标量
谓词，也不能伪造身份、删除必要条件或扩大目标集合。这些提示不替代服务端校验。

生成诊断会被动观察已应用的 `update_node/connect_data/disconnect_data`，保留前后配置
checksum、输入摘要和受限条件结构。最多 64 条、65,536 字节，另计省略数和观察失败数；
谓词字段/ref 与动态端口只保留 hash，不保存值、Prompt 或资源正文。即使整批被拒绝，
仍可核对配置是否未改或被后续操作恢复。观察失败不影响 Patch 原子性或原本的接受/拒绝，
该证据也不表示节点已经执行。离线提示契约测试不能代替真实模型成功率与效果验收。

## CW10 全链路收口补充

条件分析使用已授权 Adapter/资源 Store 的权威类型，按同一输入来源联合检查路径，
不把 consumer 的较窄声明当作运行时事实。证明规模、Schema 或工作预算超限仍阻断。
节点/控制边展示顺序不再改变场景编号与 Graph 语义 checksum。现有比较 Runtime 不变。

生成诊断 V3 明确 passed/failed/blocked；可独立确认的类型错误不会被控制错误掩盖，
但未授权来源及其依赖不得生成猜测类型。原解析/编译门禁及 Patch 原子性保持不变。
失败占位图标为 `server_synthesized_fallback/fallback_unapprovable`，不是成功模型产物。
report.validation 只表示 `pre_authoring_proposal`，批准依据始终为 proposal.validation。

Task Plan 只接收职责级能力信息，详细配置留给图编译；Patch 修复不再混入“返回完整图”
指令。只删除重复和展示性 Schema title，保留全部权限与校验，最多三次 completion。
见 [本地收口证据及尚未完成的真实门禁](./audits/META_PLANNER_CONTROLLED_WRITES_SYSTEM_CLOSEOUT.md)。

## API

```text
GET  /api/meta-agent/authoring/proposals/{proposal_id}
POST /api/meta-agent/authoring/proposals/{proposal_id}/editor-diff
POST /api/meta-agent/authoring/proposals/{proposal_id}/patch/preview
POST /api/meta-agent/authoring/proposals/{proposal_id}/patch/apply
POST /api/meta-agent/authoring/proposals/{proposal_id}/repair/preflight
POST /api/meta-agent/authoring/proposals/{proposal_id}/repair/execute
GET  /api/meta-agent/authoring/proposals/{proposal_id}/repair/receipts
GET  /api/meta-agent/authoring/proposals/{proposal_id}/repair/receipts/{request_id}
```

Capability Snapshot V10 暴露 authoring protocol、操作 JSON Schema、Adapter authoring
checksum、`task_binding` 和限制。Headless 编辑范围为二十二种受支持能力；纯节点、
控制流、只读资源和视觉节点不得承担任务，只读资源节点只能使用其 Adapter 声明的资源类型。

## 尚未展开的 Recipe 恢复

安全保留的描述用 `source_format=recipe_v1` 区分于 `graph_intent_v3`；`intent=null`，
不从诊断占位图构造假 Intent。可信管理侧可读取规范化 Recipe，普通报告仅含安全摘要。
现有 `patch/preview` 与 `patch/apply` 的 recovery 模式新增受限联合契约：

- `patch.protocol_version=recipe_edits_v1` 使用生成阶段同一个受限修改内核，最多 16 项、64 KiB：
  更新原节点语义字段、复制无绑定 Agent、替换控制树和设置最终来源；不增加业务读写能力。
  任务、资源、模型身份和授权锁定；原节点不得删除，原有全部语义门禁继续执行。
- `recipe_control_flow_v1` 的 `replace_recipe_control_flow` 继续兼容，仍只能替换有界控制树。
- `expected_graph_checksum` 在此模式绑定保留 Recipe，另有产物与候选 checksum；不得混用普通 Graph Patch。
- 预览重新降低、授权、解析、编译并执行发布预检，不执行工作流、业务写入或模型调用。
- Apply 重算预览及全部并发检查，只在完整通过后增加一次 pending Proposal revision。
  失败时保留原始证据，普通整包 PATCH 不得编辑其占位候选绕过恢复流程。
- 成功恢复在现有 Proposal 中保存服务端私有回执，绑定产物 checksum 与恢复 revision；客户端不能
  写入或读取此字段。它不依赖可变 `human_modified/candidate_origin` 标签，重启后继续阻止绕过。
  审计操作收据保留真实协议及 `before_checksum_kind`，不会把 Recipe checksum 当作已解析图的证据。

服务端状态只为严格 Recipe 提供 `semantic_repair` 的操作字段契约和配置 Schema；前端不能自行
推定能力。缺少投影的旧服务仍使用控制结构入口。旧的未保留描述不能凭空恢复。
未展开 Recipe 现在可以走显式一次模型建议；结果仍以同一个 Preview/Apply 内核校验，不能直接写回。
修复在协议解析/应用阶段失败、未重新检查旧错误时标记 `not_rechecked`，不将未观测问题数报告为零。

仅资源种类枚举失配的保留草稿使用 `source_format=recipe_resource_draft_v1`，不能伪装成
Schema 合法的 Recipe 或 Intent。保留前核对资源 ID 属于授权目录，其余结构、配置和安全检查
保持严格。对应恢复协议为 `recipe_resource_bindings_v1`，唯一操作为 `replace_recipe_resources`，
最多 40 项，允许空数组。不能混用控制结构修复或 Graph Patch，也不能编辑节点、任务、模型或授权。
预览先恢复严格 Recipe，再经过授权、lowering、类型/路径、编译及发布预检；Apply 重算并绑定
同样的 checksum/revision。回执标记 `before_checksum_kind=recipe_resource_draft_v1`，只记录操作与
checksum，不复制资源正文。付费修复预检在读取 Provider 前返回 `repair_recipe_requires_manual_resources`。

提交前复核是跨 Store 的乐观检查，不是多 Store 串行化事务。资源若在最后检查后变化，pending
Proposal 的历史 validation 不构成执行授权；审批与运行必须继续重新验证，不能仅信任 Apply 报告。
普通 V3 候选仍使用 `GraphPatchEnvelopeV1`；本例外只处理尚未形成 Intent 的有界描述。
私有回执字段为兼容新增、旧数据缺省为空，不需要批量迁移或建立新 Store。

## 显式模型修复

CW10 批次 D 只处理 A 已安全保留、B 可以人工编辑的失败原图或严格 Recipe。没有安全保留正文时不猜测修复。
初始生成仍最多三次 completion；这里是用户逐次、独立确认的一次追加调用，不自动启动。

- Preflight 只读复核失败图、原始授权、计划、资源和目标 revision；返回精确外发正文、模型、
  当前路由、输出 Token 上限及 15 分钟有效的服务端签名确认凭据，不调用 Provider 或写入回执。
- 确认绑定正文、预算、候选和契约 checksum。修改内容、模型、兼容网关计费身份或相关状态后
  必须重新确认。Managed 无有效精确 Binding 时阻断，不降级到兼容网关；正文实际准备结果
  与确认内容不符时在派发前拒绝，不静默压缩或补充。兼容路径还要求响应模型精确匹配。
- 执行正文最大 512 KiB，输出 Token 范围 512–16000，默认 12000；模型建议最多 64 KiB。
  严格 Recipe 和有配对 `source_recipe` 的新失败使用 `recipe_edits_v1`，只接受最多 16 项语义操作。
  已有 Intent 的路径由服务端派生最多 64 个已有 Graph Patch 操作并验证语义往返；未展开路径直接
  绑定 Recipe envelope 后完整预览。旧 Intent-only 失败使用 `graph_patch_v1`，只接受 `operations`。
  服务端构造 revision/checksum envelope，不允许模型夹带版本、Handle 或 Schema。
  追加要求最多 2000 字符，不能扩大授权。疑似凭据阻断，原图内 Prompt 仍属于需明确审阅的外发内容。
- 派发前在现有 Proposal Store 的私有原子日志占用 request ID 与授权 nonce，最多 20 次/提案。
  同 request ID 重读原回执；同 nonce 不能换 ID 再派发。并发占用、候选漂移和历史次数变化阻断。
  单进程、单 Store 写者是当前边界，不宣称跨进程事务或账户总额预算。
- 实际发送守卫再次核对 Proposal、目标和资源快照；Managed 守卫位于异步客户端进入之后、
  派发标记之前。该处漂移返回 stale 与未派发回执，重复请求只读取原回执。不能保证外部发送与
  各 Store 变更原子化；派发后的漂移仍保留真实调用证据并禁止建议应用。
- 有序输入由服务端派生有界断开/重连，重复边只断开一次。超过 64 操作明确标记
  `repair_patch_unrepresentable`，不能用扩大上限或自动多批次应用代替人工修复。
- 重启时未完成的派发标为 `uncertain`，不自动重发；尚未使用的确认凭据失效。结果不确定后的
  新调用需再次确认可能重复计费。回执保存失败也不会启动第二次调用。
- 结果为 `suggested/invalid/failed/uncertain/stale` 之一，只记录安全状态、usage、checksum 和
  私有解析后的建议，不保存完整 Provider 响应、外发 Prompt 或隐藏推理。没有 usage 即不可验证；
  Provider usage 不是审计账单。普通 Proposal API 不返回日志或建议，管理侧按需读取。
- 建议不会自动载入、应用、批准或执行。人工载入 B 编辑区后仍需重新 Preview/Apply；已有
  本地操作不被覆盖。调用期间状态变化时禁止载入旧建议，Apply 始终重新检查实际权限与资源。
  历史 suggested 回执仍保留历史事实；按需读取时若依据已变化，则有效状态为 stale，移除 Patch
  与可应用预览，不把历史成功标记当作当前可用授权。

日志由同一 AuthoringProposalStore 管理，单提案最多 2 MiB，使用 fsync + 原子替换。
不改变旧 `authoring_proposals.json` 的记录字段与候选 payload/revision。日志损坏时关闭 D，
不隔离原提案；关闭 D 或回退旧提案读取代码不能清理此日志。普通修改与旧版本保存不会擦除回执。
当前已完成离线接口、模拟传输与独立前端模拟浏览器检查；该浏览器不连接后端或 Provider。
真实付费修复、生产链路完整重放与泛化成功率仍属于 E 的独立验收。

## 受控写入

写节点必须在原 Proposal 的 `data_table_write_grants` 内；Apply 不能扩大表、操作、字段
或影响上限。`plannerWriteIntentV2` 是编辑器的非可信语义请求，仅包含资源 ref、Adapter
配置及命名输入来源，不能携带 Native 版本、变量、Handle 或授权。服务端经 Adapter 重新
编译后移除此临时字段；无效或无法表达的编辑阻断，不保存旧值冒充新编辑。
Preview 展示固定 Schema、业务字段、影响上限和副作用，但不读取待修改记录；批准仍只写
Xpert 草稿。见[受控写入契约](./META_PLANNER_CONTROLLED_WRITES.md)。

## 视觉附件

视觉 V2 编辑仅允许页面策略、页数、边长和页面失败策略。固定模型、Binding、附件输入
变量和执行版本不能通过 Patch 或整包 editor-diff 替换。`vision_attachment` 返回单附件
需求、固定模型、Managed 要求和调用上界；Apply 再次检查 Binding，沿用现有 revision 与
checksum 门禁。详见 [显式附件视觉契约](./META_PLANNER_VISION.md)。

## 回退

本轮没有数据迁移。回退时可移除 Headless API 和管理端入口，现有 Proposal、V2/V3 IR、
Xpert 草稿与发布版本仍可读取。不得通过回退清理 Runtime Store 或覆盖用户 Proposal。
