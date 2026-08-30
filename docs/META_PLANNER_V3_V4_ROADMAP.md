# Meta Planner V3/V4 锁定路线

最后更新日期：2026-08-30
状态：目标/锁定路线
维护人：模镜团队

## 决策摘要

Meta Planner 后续能力分为两次大迭代：

1. **V3：十个独立轮次**。先完成 Graph IR、无头编排协议和节点语义，再按纯计算、控制流、只读资源、视觉、受控写入、长运行的依赖顺序开放能力，最后完成规划质量增强和收口审计。
2. **V4：轮次待定**。只在 V3 收口审计通过后规划，研究多智能体规划、案例检索、评测反馈、自适应修复和跨平台编译等更高阶能力。

V3 的十轮依赖方向是本阶段唯一正式路线。后续可以根据实现证据调整轮次内部批次、文件位置、测试方法和小范围边界，但未经新的源码审计、风险评估和用户确认，不得合并关键依赖阶段、跳过安全门禁、替换 ModelMirror Runtime 或把 V4 能力提前混入 V3。

本路线不包含用户故事、目标客户、商业价值量化等无法由仓库证明的需求板块。这些内容留待产品负责人补充。

## 基线与证据边界

- 路线编写基线：`origin/main@11d35c1d84c0a18a05779a37d60cb59ef7a25867`。
- 节点与 Planner 审计基线：PR #328 合并提交 `7dcbf49480c73e6b64edf6999b8f3b59d8c03f8c`。
- EvoAgentX 唯一可复用基线：官方 `v0.1.4@aad19b912f640161ea07e8904d9237cd34fde5f1`。
- 当前实现事实仍以代码、测试、[META_AGENT.md](./META_AGENT.md) 和 [NODE_CONTRACT_V3.md](./NODE_CONTRACT_V3.md) 为准；本文描述目标路线，不表示相应能力已经交付。

本轮审计确认：

- NodeContract V3 已覆盖绝大多数节点事实，当前主要阻塞不再是“缺少节点名称”，而是 Planner IR、Adapter、动态资源、效果语义和评测契约不足。
- Capability Snapshot 已是 V3，但 Planner 仍使用 Typed IR V2；二者必须继续独立版本化。
- 当前仅有 `input`、`output`、`workflow_agent`、`external_xpert`、`knowledge_base`、`toolset_resource`、`plugin_resource` 七类能力进入 Planner Snapshot。
- 其中只有 `workflow_agent` 具备完整业务节点编译/反编译 Adapter；输入、输出由编译器管理，资源类通过绑定记录编译。
- JSON、Agent Table、知识检索、视觉和控制流节点虽已在工作流侧逐步落地，但不能据此直接宣布 Planner 可生成。
- Update 模式对无 Adapter 节点 fail-closed 是正确安全边界；V3 必须通过 Adapter 和 IR 扩展消除能力差距，不能通过放宽校验绕过。
- 当前 kind 级策略不足以表达“同一种节点因配置不同而产生不同副作用、等待和公开入口风险”；V3 必须引入配置解析后的效果事实。
- Evaluator 已能比较最终输出，但尚不足以证明分支路径、状态变更、附件、等待和补偿行为正确。
- Structure Evolution 的可变异节点范围受同一 IR/Adapter 缺口限制，不能先于 Planner 语义扩张。
- 旧生成器与 Meta Planner V2 并存属于兼容状态；在 V3 收口前不得仓促删除，在 V3 第十轮必须给出保留、隐藏或迁移结论。

## 架构不变量

以下边界贯穿 V3 和 V4：

1. **ModelMirror Runtime 是唯一执行权威**。不引入 EvoAgentX、LangGraph、Temporal、Airflow、Argo 或其他项目的 Workflow Runtime 取代 classic runner。
2. **NodeContract 是节点事实权威**。Registry、Validator、Planner、Evaluator、Evolution、App 和发布预检不得各自维护另一套节点能力表。
3. **Planner Adapter 是编译权威**。节点出现在画布或 Registry 中不等于 Planner 可生成；必须存在版本化 Adapter、匹配 compiler checksum，并完成往返验证。
4. **候选隔离**。生成、修复和进化只产生 Authoring Proposal、候选图和报告；不得静默写草稿、发布版本、改变活动资源或启动副作用运行。
5. **显式授权**。资源、操作、副作用、附件和高风险能力必须由请求 scope 授权；模型不能构造物理 ID、SQL、路径、凭据或任意 binding handle。
6. **先读后写，先同步后长运行**。只读节点和纯控制流稳定前不开放写节点；写入效果和幂等稳定前不开放等待、循环、Handoff 和 Automation 编排。
7. **同预算评测**。候选与基线固定数据集、模型、资源版本、随机参数和预算；复杂节点必须有路径或效果层证据，不能只看最终文本。
8. **兼容优先**。IR 升级必须保留 V2 Proposal、旧 Snapshot 和已发布 Xpert 的可读、可运行或明确迁移路径。
9. **公共入口默认拒绝**。V3 不以扩大 App 副作用能力为目标；任何公开能力必须通过独立安全审计。
10. **不保存隐藏推理和敏感正文**。Planner 只保存公开计划、选择理由、结构化 issues、版本/checksum 和安全统计。

## V3 十轮交付路线

每轮必须单独制定执行计划、建立独立分支和 PR，并在计划阶段再拆分可控批次。本文只锁定依赖方向、目标和退出门禁，不预先规定实现文件。

| 轮次 | 代号 | 核心目标 | 退出门禁 |
| --- | --- | --- | --- |
| 1 | `META-PLANNER-GRAPH-IR-V3-04` | 建立可表达真实工作流语义的 Graph IR V3 | 当前七类能力与 V2 生成结果行为等价，V2 可兼容读取 |
| 2 | `META-PLANNER-HEADLESS-AUTHORING-05` | 建立无头能力查询、候选预览、类型化 Patch 和冲突诊断 | UI 与模型共用同一编排协议，无法表达的修改显式 fail-closed |
| 3 | `META-PLANNER-PURE-NODES-06` | 开放无副作用的纯数据变换节点 | 类型往返、错误策略和 Adapter 往返稳定，Evaluator 可确定性验证 |
| 4 | `META-PLANNER-CONTROL-FLOW-07` | 开放条件、多路、合流和终止语义 | 路径可达、outcome handle 和覆盖评测闭环，不包含循环/等待 |
| 5 | `META-PLANNER-READ-RESOURCES-08` | 开放知识检索与 Agent Table 查询 | 资源授权、版本/Schema 快照、漂移和只读评测闭环 |
| 6 | `META-PLANNER-VISION-09` | 开放显式附件作用域内的视觉理解 | 附件型评测、模型能力和跨作用域拒绝闭环 |
| 7 | `META-PLANNER-CONTROLLED-WRITES-10` | 开放受控 Agent Table 写节点 | 操作级授权、幂等、影响范围和效果断言闭环 |
| 8 | `META-PLANNER-LONG-RUNNING-11` | 在前置语义成熟后审计并开放长运行节点 | 等待、恢复、补偿、幂等和路径评测达到可证明边界 |
| 9 | `EVOAGENTX-PLANNER-QUALITY-V3-12` | 强化需求建模、能力检索、模式复用和 Reviewer | 与固定基线同预算比较，质量提高且成本、失败不越界 |
| 10 | `META-PLANNER-V3-CONSOLIDATION-13` | 完成兼容、策略、评测、旧入口和延期项收口 | 发布 V3 冻结报告并决定 V4 是否准入 |

### Round 1：Graph IR V3

Graph IR V3 至少需要表达：

- 显式控制边、数据边、资源绑定边和元数据节点。
- 命名输入/输出端口、source/target handle、基数和类型化变量映射。
- 条件 outcome、合流和终止元数据。
- 资源引用、固定版本、动态 Schema 摘要和授权要求。
- 配置解析后的副作用、外部 IO、等待、幂等、失败和补偿元数据。
- 稳定节点/边引用、规范化 checksum 和 V2 兼容转换。

本轮不得开放新节点。退出条件是当前七类能力经 `compile -> decompile -> compile` 后结构稳定，且 V2 候选仍可读取和批准。

### Round 2：Headless Authoring

建立 Planner、前端编辑器和后续 Optimizer 共用的无头编排协议：

- 按授权读取节点、资源、端口、动态 Schema 和可用操作。
- 创建候选预览并返回类型、授权、版本和效果诊断。
- 使用类型化 Graph Patch 修改候选，禁止让模型直接重写任意 Workflow JSON。
- 使用 revision、stale、冲突和 lossy conversion 诊断保护人工工作。
- 提供 Adapter SDK/测试夹具，后续每开放一种节点只增加 Adapter，不新增旁路编译器。

本轮仍不开放新节点。没有这一层，后续节点扩张会再次把 Planner Prompt 变成第二套 Registry。

实现契约与回退边界记录在
[META_PLANNER_HEADLESS_AUTHORING.md](./META_PLANNER_HEADLESS_AUTHORING.md)。Round 2 保持
七类能力不变，并采用 V3 单写、V2 无损升级、有损候选兼容只读的边界。

### Round 3：Pure Nodes

首批开放无副作用且可确定性评测的节点，候选范围以真实 NodeContract 和 Runner 支持为准，目标包括：

- JSON 序列化与反序列化。
- 变量赋值、变量聚合和列表操作。
- 对象变换、数据聚合、数据合并和数据集比较。
- Annotation 仅作为元数据保留，不参与执行。

每个节点必须补齐输入输出类型、默认值、错误策略、Adapter、反编译和确定性评测。不得为了快速开放而把复杂值退化为字符串。

### Round 4：Control Flow

目标包括条件、多路分支、显式 outcome edge、合流/fanout 和终止。重点不是“让模型画更多线”，而是建立：

- 条件表达式和端口结果的类型检查。
- 分支覆盖、不可达路径、唯一/多终点和合流规则。
- 候选评测中的路径覆盖和错误路径证据。
- 编译器生成 handle，模型只引用受限 outcome/ref。

Iteration、等待、HITL、Handoff 和 Trigger 不进入本轮。

### Round 5：Read Resources

首批动态资源节点为：

- `knowledge_retrieval`。
- `data_table_query`。

必须固定用户授权范围、知识版本或活动指针语义、Agent Table SchemaVersion、字段/操作白名单和漂移处理。Evaluator 必须固定资源快照。只有 chunk 或最终文本命中、却无法证明资源版本和查询契约正确的评测，不足以通过本轮。

### Round 6：Vision

开放 `vision_understanding`，要求：

- 输入只能来自运行元数据中显式共享的附件。
- Capability Snapshot 只暴露支持图像输入的安全模型标签和受限配置。
- Planner 不得构造物理路径、Base64 或跨作用域 Asset ID。
- 新增附件型 Dataset/Evaluator 夹具，验证页数、视觉块、失败策略和跨作用域拒绝。
- 公共 App 继续保持拒绝，除非另立安全审计。

### Round 7：Controlled Writes

只开放 Agent Table 的 insert/update/delete，不开放任意 SQL、HTTP、Sandbox、Browser 或 Client Tools 写入。必须具备：

- 表和操作级显式授权。
- 固定 SchemaVersion、字段白名单和类型校验。
- 稳定 operation ID、幂等重放和 revision 冲突。
- 更新/删除条件与最大影响行数门禁。
- 候选预览中的副作用摘要，以及 Evaluator 的隔离夹具和效果断言。

写节点的发布与评测策略必须根据解析后的配置判定，不能只依赖 node kind。

### Round 8：Long Running

这是 V3 内唯一允许在审计后进一步拆分的高风险轮次。候选范围包括：

- iteration/subworkflow。
- HITL、wait/resume。
- Handoff。
- Trigger/Automation。

是否全部交付必须由前七轮证据决定。任何能力只有在持久 execution、lease、幂等、取消、超时、补偿、恢复和路径评测均有明确契约时才能开放。若证据不足，应拆为后续独立轮次或明确延期，不得用文案宣布支持。

### Round 9：Planner Quality V3

在节点语义稳定后再强化 EvoAgentX 相关规划能力：

- 引入显式 Requirement IR，区分目标、约束、输入、输出、质量、成本和风险。
- 从 NodeContract、资源和授权中检索候选能力，而不是把完整目录塞入 Prompt。
- 建立版本化、许可证可追溯的子图模式库。
- 将生成分为 Planner、Binder、Reviewer 等逻辑阶段，但保持有界模型调用和一次修复。
- 使用固定 Benchmark 对比任务覆盖、编译通过率、人工修改量、质量、延迟和成本。

该轮可以适配 EvoAgentX 的规划、评测和候选搜索思想，也可以审计其他宽松许可证项目，但不得导入另一套 Runtime。

### Round 10：V3 Consolidation

V3 结束前必须形成审计报告，至少包括：

- 全量节点 `contract / adapter / planner / evaluator / evolution / app` 矩阵。
- V2 Proposal、Snapshot、旧生成接口和已发布 Xpert 的兼容结论。
- 旧生成器的保留、隐藏、迁移或删除依据。
- Structure Evolution 改用 V3 Snapshot 和 Adapter 的证据。
- Planner 质量、编译成功率、人工修改量、执行成本和失败分布。
- 未开放节点和延期理由。
- V4 准入或继续冻结的结论。

V3 未完成该报告前，不得宣布 Planner V3 完成。

## V3 顺序门禁

- Round 1–2 完成前，不开放任何新 Planner 节点。
- Pure Nodes 和 Control Flow 未稳定前，不开放动态资源。
- 只读资源未稳定前，不开放写节点。
- 写入效果、幂等和评测未稳定前，不开放等待、循环、Handoff 或 Automation。
- 节点语义尚在变动时，不提前做多智能体 Planner、持续学习或跨平台编译。
- 通用 HTTP、任意代码、Browser、Client Tools、Automation 和公共 App 副作用不是 V3 默认范围；需要独立审计才能纳入。

## V4 边界与准入

V4 的目标不是继续堆节点，而是在 V3 已证明可靠后研究自适应和跨域规划。轮次数现在不锁定，必须在 V3 第十轮依据真实数据决定。

可审计的候选研究域包括：

- 多智能体 Planner/Reviewer 角色协作。
- 案例检索、子图模式选择和任务特定规划记忆。
- Evaluator 反馈驱动的 Prompt、绑定和结构优化。
- 跨工作流平台的 Capability IR 与多后端编译器。
- 运行反馈、成本/延迟/质量约束下的自适应修复。
- 旧工作流迁移、解释、差异审查和自动修复候选。

V4 准入条件：

1. V3 收口报告通过并明确剩余风险。
2. 有覆盖真实节点、资源、路径、副作用和附件的版本化 Benchmark。
3. Graph IR、Adapter、NodePolicy 和 Evaluator 契约稳定。
4. 有足够真实使用数据证明高阶规划能带来收益。
5. 每个拟复用项目和文件完成许可证、依赖与安全审计。

V4 仍不得自动发布、静默修改线上版本或绕过 ModelMirror Runtime。

## 开源复用与许可证策略

本路线鼓励避免重复造轮子，但“参考”“适配”和“复用代码”必须严格区分。以下是当前研究候选，不是依赖承诺：

| 项目 | 可参考领域 | 当前许可证事实 | 默认判定 |
| --- | --- | --- | --- |
| EvoAgentX `v0.1.4` | 任务规划、工作流生成、Evaluator、候选搜索 | MIT | 逐文件 `reuse/adapt`，保留版权与 NOTICE |
| LangGraph | 状态图、持久状态和人机协作模式 | MIT | 优先参考/适配概念，不替换 Runtime |
| Apache Airflow | DAG、分支、Trigger Rule 和任务状态 | Apache-2.0 | 参考控制流语义；不引入执行器 |
| Argo Workflows | DAG 模板、重试、退出处理和 Artifact 依赖 | Apache-2.0 | 参考声明式契约；不依赖 Kubernetes Runtime |
| Temporal | Durable Execution、Event History、Command/Activity 边界 | MIT | 参考长运行语义；不自动引入服务端 |
| Flowise | 可视化节点和 Agent Flow 交互 | 社区与企业目录许可证混合 | 仅逐文件审计后的宽松许可路径可复用 |
| Dify | 工作流、RAG 和工具交互行为 | 修改版 Apache-2.0，含附加条件 | 默认仅行为参考、独立重写 |
| n8n | 节点 UX、执行恢复和凭据交互 | Sustainable Use License/企业条款 | 默认仅行为参考，不复制代码 |
| Xpert | 节点领域模型和产品交互 | AGPL-3.0 | 仅行为参考、独立重写 |

实施时必须：

- 锁定官方仓库、tag/commit 和相对文件路径。
- 核验仓库许可证、目录/文件级例外和第三方依赖许可证。
- 在审计台账记录 `reuse/adapt/rewrite/reject`、SHA-256、版权、NOTICE 和测试映射。
- 宽松许可证代码也必须保留归因，不得只复制片段而删除来源。
- Copyleft、source-available、混合或不明确来源默认只能作行为参考并独立重写，除非新的法律/许可证审计明确允许。
- 不从本地无 Git 快照、博客转贴、截图或来源不明代码复制实现。

此处是工程合规护栏，不是法律意见；每个实际引入仍需在对应 PR 中完成许可证核验。

## 变更控制

允许在单轮计划中灵活调整：

- 模块和文件组织。
- 内部数据结构命名。
- 小批次顺序、测试方法和预览方式。
- 基于证据缩小单轮范围或把高风险能力延期。
- 在不改变边界的前提下选择自研、适配或重写。

以下变化必须先形成新的审计记录并获得用户确认：

- 改变十轮依赖顺序或合并跨门禁轮次。
- 跳过 Graph IR、Headless Authoring、效果语义或 Evaluator 前置条件。
- 替换 classic runner、NodeContract、Authoring Proposal 或当前资源 Store 权威。
- 自动批准、自动发布、静默覆盖人工草稿或放宽高风险能力授权。
- 把任意代码、Browser、Client Tools、Automation 或公开 App 副作用纳入默认范围。
- 提前确定 V4 轮次，或把 V4 自适应能力混入 V3。
- 引入未审计许可证、来源不明代码或新的强耦合运行时。

## 每轮交付门禁

每轮至少需要：

1. 从最新干净 `main` 建立独立工作树。
2. 明确当前轮的事实基线、验收检查、回退路径和不做事项。
3. 先做 NodeContract/Adapter/IR/Policy 的窄测试，再做 Meta Planner、Workflow、Publish、Evaluator、Evolution 和前端回归。
4. 对生产依赖和许可证变更单独审计。
5. 检查候选、Runtime Store、模型输出、凭据、上传数据和构建产物未进入提交。
6. 人工验收只证明实际执行过的入口，不用页面可见或容器健康代替功能证据。
7. 独立 PR，清晰标注已验证、未验证、兼容风险和回退方法。

## 参考

- [EvoAgentX v0.1.4 releases](https://github.com/ANative-Lab/EvoAgentX/releases)
- [LangGraph repository and MIT license](https://github.com/langchain-ai/langgraph)
- [Apache Airflow repository](https://github.com/apache/airflow)
- [Argo Workflows repository](https://github.com/argoproj/argo-workflows)
- [Temporal repository](https://github.com/temporalio/temporal)
- [Flowise license](https://github.com/FlowiseAI/Flowise/blob/main/LICENSE.md)
- [Dify license](https://github.com/langgenius/dify/blob/main/LICENSE)
- [n8n license](https://github.com/n8n-io/n8n/blob/master/LICENSE.md)
- [Xpert repository and AGPL license](https://github.com/xpert-ai/xpert)
- [EVOAGENTX_AUDIT_V014.md](./EVOAGENTX_AUDIT_V014.md)
- [NODE_CONTRACT_V3.md](./NODE_CONTRACT_V3.md)
- [META_AGENT.md](./META_AGENT.md)
