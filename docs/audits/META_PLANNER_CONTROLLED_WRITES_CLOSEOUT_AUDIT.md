# 受控写入轮次审计收口

日期：2026-09-12。范围：Meta Planner V3 第 7/10 轮，不提前进入 V4 或第 8 轮。

状态更新：用户已确认下文有限收口方案，当前已完成局部投影与内存取证的离线实施，见
[有限整改记录](#有限整改记录2026-09-12)。以下原审计结论、344 项测试及历史 hash 保留为
整改前证据，不混作整改后的结果；整轮仍未完成真实验收，不具备 PR 提交结论。

## 结论

**审计可以形成有限结论，整轮交付仍不能收口。** R1-R12 不是同一个缺陷的十二次独立复现。历史上同时出现过平台投影/兼容错误、传输与观测故障，以及进入 Adapter 的候选违约；不能用这个混合序列证明单模型架构已到上限。

本次在冻结源码上取得以下新证据：

- Legacy 与 Managed 两条真实收集/解析链路，在合成响应配对实验中均保留了 Filter、端口和声明类型，没有将这些合法字段转换为非法字段。
- Filter 对象约束、Serialize 唯一 `json` 输出及合法 Query 记录类型没有发现相互矛盾的要求；完整合成 CRUD 图可编译并生成有效的 pending Proposal。
- **存在可复现的局部生成投影缺口**：表节点的模型 Schema 没有限定 `records` 的对象类型。`records` 声明为 `string` 的反例可通过模型 Schema，但被后续语义检查拒绝。可信来源和跨边类型关系仍必须由语义校验负责。
- 共享三次 completion / 一次修复预算按现有约定执行；TaskPlan 修复用掉机会后，Graph 不再修复。这是当前接口的脆弱点之一，不是额度计数错误，不应暗中追加调用。
- 下游事务、可信来源、Headless 和隔离效果路径取得针对性通过证据；这不等于全部生产行为、真实生成或人工写入验收已通过。

**不能补出的证据仍不能补出**：R12 没有保存原始 Provider 字段与校验输入的同请求配对。新测试是合成证伪，不是 R12 原产物重放，不能据此宣布 R12 的最初偏离一定发生在模型端或系统端。

## 基线与授权

- 工作树：`C:\tmp\modelmirror-meta-planner-controlled-writes-10`；分支：`codex/meta-planner-controlled-writes-10`。
- HEAD：`436d24535c6f0ad80b3a4f18830fb834f90a7b95`。本次开始前已有 89 个本轮待提交路径，不能把整个 Diff 当作本次修改。
- 开始时冻结上述 89 个文件及四个本地测试入口/启动器/安全回执，共 93 个 SHA-256；本次仅允许新增测试和审计文档、同步任务卡，不修改生产代码。
- 收尾时本地 `origin/main` 指向 `6c1288e7e07b6a1ff7c3b82a8826b677950dd727`，与 HEAD 为 `0 / 28`。没有 fetch，不能称为本次在线核验的最新远端；也没有合并、重置或集成这些提交。
- 授权只用于离线针对性测试。没有调用真实模型、读取凭据、操作预览器/共享栈、访问业务记录、批准 Proposal、发布 Xpert 或执行 Git 发布操作。
- 测试通过既有隔离入口禁用 dotenv、清除凭据环境变量、阻止外网并重定向 Store；HTTP 使用 MockTransport，数据库使用临时合成 SQLite。测试没有导入预览启动器。

## 证据与判断

证据分类：**本次执行**表示当前源码上的测试结果；**历史记录**表示任务卡/安全回执，不视为本次原始请求重放；**推断**不得升级为确定根因。

| 问题 | 证据 | 收敛判断 |
| --- | --- | --- |
| 整个实现是否已经不可用 | 本次完整 CRUD 图经两条收集链路进入实际生成服务，合法图成功；实际 Runner 在临时隔离表执行有序 CRUD | 已覆盖的合法路径可用，不能认定整个编译/执行内核失效；也不能由有限正例证明无其他缺陷 |
| Filter 和 Serialize 是否被投影为互相矛盾的契约 | 本次核对同一 Adapter 配置 Schema、NodeContract 端口投影；非法数组/字符串 Filter、错误/缺失 Serialize 输出被两侧拒绝 | 对这些具体形状没有发现内部矛盾，不应继续给它们增加猜测式自动修复 |
| 模型 Schema 通过是否足够 | `records: string` 合成反例通过 Schema，实际 object 来源与 string 输入被语义门禁拒绝 | Schema 不是完整验收器；可将已有的静态 `records` 类型投影补全，不能取消来源/类型校验 |
| 返回到校验器之间是否必然破坏字段 | 配对比对实际发出的字段 Schema、Mock Provider 返回字段、收集后 JSON 和 authorization 输入；两条路由一致 | 本次样例未发生破坏，证伪“这些字段必然被转换坏”；未证明 R12 实际走了哪条路由或返回了什么 |
| 多次补丁是否把合法图全部改坏 | 合法图、精确 Patch、整数兼容、资源诊断和 Headless 往返通过；R12 未走 Graph Patch | 没有证据支持“全部改坏”；历史观测写盘确实改变过调用行为，不能把所有历史补丁称为行为等价 |
| DeepSeek 是否不能承担、是否必须多 Agent | 历史真实调用未稳定通过；没有同目标、同契约、同预算的跨模型或单/多 Agent 对照 | 只能说当前组合未达到交付标准，不能推断模型能力上限，也不构成提前 V4 的依据 |
| 三次预算是否导致非法图被接受 | 合成 TaskPlan 修复耗尽后，非法图失败且只调用三次；非法 Patch 不追加第二次修复 | 预算放大一次格式错误的影响，但安全边界仍有效；不以放宽预算或静默补线收口 |
| 是否需要重做数据库/RAG/Runtime | R12 未进入真实写执行；本次隔离事务、重放与效果反例通过 | 当前证据不支持重做这些模块；保持冻结范围，单独处理可证实的问题 |

代码依据：

- [生成配置 Schema](../../server/meta_agent/generation_contract.py)：`_install_config` 复用 Adapter；`_ports` 对动态表输入主要限定名称/次数，没有投影 `records` 值类型。
- [Adapter](../../server/meta_agent/node_adapters.py)：`validate_intent_node`、`_require_single_output` 与表输出 Schema 推导使用权威契约；模型说明已要求完整的同表 Query/Insert 结果。
- [生成服务](../../server/meta_agent/meta_planner_v2.py)：任务规划与能力编排共用唯一修复机会；非法候选在授权/Adapter 阶段停止，不能把失败占位图的缺模型诊断当作首因。
- [新增配对实验](../../server/tests/test_meta_planner_generation_boundary_audit.py)：本次全部 39 个测试；故意不保存或宣称复原 R12 的未知字段。

## 针对性实验

### 新增 39 项

| 组 | 数量 | 证伪方式与结果 |
| --- | ---: | --- |
| 同请求约束/响应/校验输入配对 | 24 | 12 种合成形状分别经过 Legacy、Managed：两个合法对照通过，其余违约保留原形并由相应门禁拒绝；其中 `records: string` 暴露模型 Schema 表达缺口 |
| 完整生成与修复预算 | 10 | 两条路由各验证直接成功、TaskPlan 修复成功、修复用尽后失败、显式 Graph Patch 成功、Schema 注入 Patch 失败；只用 2 或 3 次 Mock completion |
| 非法 Filter 解析 | 4 | array/string/boolean/integer 不被 JSON/Intent 解析器擅自转成合法 Filter |
| Schema 包装不是任务实例 | 1 | 合成 `type/properties` 包装保留原形，并因缺少 `tasks` / 多余字段被拒绝；该包装不是对 R12 原文的猜测 |

配对实验使用产品的 `collect_chat_completion_text` 或 `ManagedMetaAgentGateway`，不是替换解析器后仅测试假函数。完整生成使用实际 Planner 服务与临时 Authoring Store；所有业务 CRUD 入口设为失败哨兵。资源目录和 Provider 都是合成 fixture，发布检查并不代表真实部署环境的全量验收。

### 相邻回归与真实本地执行

13 个文件，**344 passed / 0 failed / 0 error / 0 skipped，81.41 秒**，包含上面的 39 项，不重复相加。JUnit 为本地忽略文件 `.tmp-cw10-preview/causal-audit-integrated.xml`。

该 JUnit SHA-256：`3e2e30eee751fa624b6bda2a9705d423be4e2a88c51dbac297036398e4aaa898`。测试结果只约束本次冻结源码和临时合成环境。

| 范围 | 数量 | 本次可使用的证据 |
| --- | ---: | --- |
| 新增配对与生成预算 | 39 | 响应形状、实际校验输入、合法/非法生成、修复上限 |
| 生成 Schema、完成状态、控制投影、失败生成 | 113 | 严格完成检查、输入投影、完整图和显式修复，不将 HTTP 200 等同成功 |
| 表 Prompt、整数、独立资源诊断、Headless | 103 | 既有修复的相邻回归、资源漂移拒绝、预览零写入、批准仅生成草稿 |
| Agent Table Backend | 31 | revision/活动 Schema、节点事务、影响上限、账本绑定及容量限制 |
| 实际 Runner 隔离执行 | 3 | Insert-Query-Update-Query-Delete、旧版本评测拒绝、后续失败保留先前提交 |
| 隔离实例 | 12 | 基线/候选独立、写后读、重启、取消、篡改与派发持久化失败 |
| 效果断言 | 43 | 文本冒充成功、伪造影响数、非目标记录变化、重复计分等反例被识别 |

四个 warning 均为既有 FastAPI `on_event` 弃用提示，本次不修改。

命令在本工作树执行，使用已有本地隔离入口：

```powershell
$tests = @(
  'test_meta_planner_generation_boundary_audit',
  'test_meta_planner_generation_contract',
  'test_meta_planner_control_contract_alignment',
  'test_meta_planner_completion_contract',
  'test_meta_planner_write_generation_failures',
  'test_meta_planner_table_prompt_contract',
  'test_meta_planner_integer_projection',
  'test_meta_planner_independent_resource_issues',
  'test_meta_planner_write_headless',
  'test_controlled_write_execution_integration',
  'test_evaluation_write_isolation',
  'test_agent_table_controlled_writes',
  'test_evaluation_write_evidence'
) | ForEach-Object { "server/tests/$_.py" }
& 'C:\tmp\modelmirror-mcp-test-venv\Scripts\python.exe' -B `
  .tmp-cw10-preview/run_generation_contract_checks.py @tests `
  --junitxml=.tmp-cw10-preview/causal-audit-integrated.xml
```

实验过程问题单列，不混为产品缺陷：

- 初次受限进程启动没有进入测试输出，已中止；相同隔离入口在批准的执行环境重跑。没有为此修改产品或外网护栏。
- 新测试初稿 35 passed / 2 failed：正例误在 `connect_data` 中提交 Patch 禁止的 `value_schema`。修正为显式 disconnect/connect，并把原非法输入保留为独立反例；没有放宽产品校验。最终单文件 39 passed，随后纳入 344 项组合再次通过。
- 初始四文件 113 passed 与单文件 39 passed 都是分步验证，不能另加到最终 344 得到虚高总数。

## 收口方案：待用户确认后分段执行

### 1. 冻结范围，补上可观测的失败边界

不撤销已验证的安全内核，不继续增加猜测式 normalizer；不修改目标来换取成功，不加第二次修复。保留 Capability 22、Graph IR V3、逐表授权、可信记录/revision、隔离评测和原审批边界。

在下一次外发前，先完成同一次请求的三点配对能力：实际生效的局部契约、Provider 对应字段的受限形状、进入校验器的对应形状。固定源码/路由身份、阶段、模型、请求与契约 checksum，并明确截断/脱敏是否导致不可重放。对本轮合成测试允许的字段制作小型独立反例，不保存完整 Prompt、任务正文、隐藏推理、凭据或业务数据。未知名称只保留允许的类型、位置、合法性标志和 hash，不能伪称恢复了原文。

这属于取证设施，不是放行逻辑。被动诊断写盘失败不能像 R7 一样改变响应解析或覆盖原异常；派发预算账本仍须失败关闭，两者不能混用。外发前证据通道不可用就不开始付费试验。接受测试至少包含观测开/关语义等价、写盘失败、隐私夹带、同字段偏离定位及无额外派发。

### 2. 只处理已经复现的局部契约缺口

优先将 `records` 的已知类型从 NodeContract 投影到私有生成 Schema；不要再维护另一份类型常量或 Schema，也不要以 Schema 通过替代同表来源与 revision 校验。保留所有现有合法 object/nullable object/array 及契约允许的组合；非法 string、无法证明的类型收窄仍拒绝。

Filter 与 Serialize 当前没有配对矛盾证据，不再为它们增加自动转换。这个微批的理由是补全可表达的契约，不是声称已找到 R12 全部根因；应先取得严格反例、合法对照和公开协议/预算不变证据，再讨论下一次实测。

模型重复填写 variable、source/ref/port 与声明 Schema 带来冗余约束，这是值得后续审计的接口风险，但本次尚未证明必须重写 GraphIntent。不得直接以“简化”为名扩大当前微批、改任务计划/公共 IR 或用服务端猜测记录身份。

### 3. 一次有判别力的真实验证，不再循环碰运气

用户重新批准模型、合成元数据和至多三次 completion 后，固定原完整 CRUD 目标，不拆成只插入一条的简单目标冒充本轮通过；不自动更换模型、不自动重试。Planner 生成与后续执行分别授权。

若失败，依据配对证据只进入以下一个有证据支持的处理方向：

| 首个可证明的偏离 | 后续处理 |
| --- | --- |
| 发出的约束与权威契约相反 | 修正同一投影源，保持后续校验不变 |
| Provider 字段合法，进入校验器后非法 | 修正明确的解析/转换步骤，增加原形保持反例 |
| 对应配置、类型、来源、权限都合法，校验仍拒绝 | 修正被最小反例定位的校验器，不删除安全规则 |
| 约束一致且 Provider 返回时已违约 | 定位为该次输出合规失败；再单独审计私有生成接口缩减或受约束输出，不据一次结果宣布模型能力上限 |
| 配对证据缺失、脱敏后无法区分或调用结果不确定 | 停止付费试验，不再从错误计数推断改善 |

同一字段局部配对不保证整个图合法；其他语义/授权错误必须独立检查。跨模型比较、Provider 强制 Schema、调用预算或单/多 Agent 架构变更均需单独的范围决定和新授权，不把这些选择提前并入 V3 第 7 轮补丁。

### 4. 达到交付门禁后才提交

真实成功候选必须由模型生成后通过完整契约、编译和发布预检，Headless 无损加载；不得人工补图后称为自动生成成功。随后在另行批准的隔离测试环境验收三种写操作、顺序组合、revision 冲突、Schema 漂移、部分成功、恢复与效果证据。不得自动批准 Proposal 或发布。

提交前仍要处理最新上游交点，特别是 `WorkflowExecutionStore` 的可信恢复，保持账本为防重复写的最终证据。需要同一集成基线的全量后端、前端构建/回归、帮助中心与真实预览器验收；历史 Linux 绿测不覆盖其后的多个生成修复。本次没有重新执行这些整轮检查。

历史最新全量尝试仅为 94 passed / 1 failed 后停止，`test_agent_upstream_port.py::test_started_worker_crash_after_model_request_is_never_restarted` 的独立失败尚未在本次复核/修复，不能归并为本轮已解决问题，也不能将未执行部分算通过。详见 [任务卡](../tasks/META_PLANNER_CONTROLLED_WRITES_10.md)。

## 状态与回退

- 本次完成：审计结论分级、39 项新增合成边界测试、344 项定向回归和收口建议。
- 本次未做：生产修复、真实生成/写入验收、上游集成、全量/构建重跑、提交或 PR；以上方案尚待用户确认，不因本报告自动进入实施。
- 本次改动仅为一份测试、此审计及两份历史文档的入口/状态同步。回退只移除本次测试/文档变更，不触及整轮已有实现、测试数据或历史回执。
- 收尾核对：93 个冻结文件中 91 个 hash 不变，两个变化均为允许的历史文档；新增路径仅为本测试和本审计。测试文件 AST 语法通过、`git diff --check` 无错误、新文件尾部空白扫描 0 命中，四个本次相关文件的常见密钥/私钥模式扫描 0 命中，暂存为空。有限模式扫描不是完整泄漏证明；本次没有读取实际凭据。
- 整轮仍为未验收、未提交。若后续停止本轮，按锁定计划关闭新增 Planner/Evaluator 入口并保留 V2 读取/执行兼容，不自动回滚已完成业务写入；该操作亦不是本次审计已执行事项。

## 有限整改记录（2026-09-12）

### 实施边界

用户确认“开始收口”后，只落实上述两项有证据支持的改动：静态 `records` 类型投影、同请求
字段配对。沿用同一工作树与 HEAD，开工冻结 91 个待提交路径及四个忽略目录入口/回执，
共 95 个 hash。没有修改 Adapter、公开 Graph IR、业务 Backend、Runtime 或授权边界，
没有增加 normalizer、修复次数、模型调用或 Provider 强制 Schema。

- `generation_contract.py` 从实际 NodeContract 的 `records` 端口推导声明类型包络；私有
  Schema 不再允许 string/any 等无法证明的声明。合法 object、nullable、对象数组与受限
  union 保留；字段和可信来源不是这个包络的职责。
- 新增 `generation_evidence.py`，用请求局部 ContextVar 连接真实请求、公开 Provider
  `content`、collector 和校验输入。Legacy 在构建请求后观察，Managed 在授权传输构建
  最终请求后观察；不从隐藏推理回填缺失 content。自定义 sender 若改变请求，需要单独
  核对实际发送内容，本摘要不声称观察到 HTTP 线上的全部字节。
- 观察器只有内存运算，输出阶段、路由类别、请求/Schema/模型 hash、受限 Filter/端口/
  声明类型形状及比较状态。最多三条调用、32 个节点、64 条绑定，单正文上限 1,048,576
  字符，最终摘要不超过 65,536 ASCII JSON 字符；超限、重复或缺失都明确降低证据等级。
  `schema_valid` 不是语义验证结果，`same_structure` 不是完整正文相等或根因归属结论。
- 摘要进入已有 Proposal 报告，正常/异常运行通过原 checkpoint 或 metadata 返回；没有
  新 Store。**RunRegistry 是内存结构**：在 Proposal 之前失败的摘要只能从 API/当前进程
  导出，不能承诺重启恢复。下次真实验收要先验证导出链路，并在重启前保存安全响应。
- 观察故障和摘要汇总故障不覆盖原结果，不记录异常正文；业务 Store/预算账本异常仍按原
  规则失败关闭。该隔离仅针对被动诊断，不是将持久化失败改成成功。

### 反例与测试过程

| 检查 | 结果与解释 |
| --- | --- |
| 新 `records` 声明包络测试 | 修复前 20 failed / 17 passed，暴露 string、any、非法数组项与 union 等表达缺口；修复后与权威兼容判定一致，37 passed |
| 投影相邻四文件 | 125 passed；包含上述 37 项，不重复相加 |
| 取证接线反例 | 完整生成十项在接线前均因缺少 `generation_evidence` 失败；接线后两条 collector 都提供配对 |
| 独立内存取证测试 | 17 passed：字段偏离、隐私夹带、远程 Schema 拒绝、截断、重复观察、并发隔离及故障不遮蔽原异常 |
| 接线与邻域测试 | 157 passed，包含前面部分测试，不重复相加 |
| 开关与失败链路证伪 | 最终 27 passed（17 单元 + 10 集成）：合法/非法生成在 on/off/fault 三组中请求和非取证报告完全一致；API 失败保留证据，Store 异常不吞掉、不追加调用 |

集成测试初稿 5 failed / 5 passed，其中一项暴露新观察器 `as_dict` 汇总异常会向外抛出，
已用纯诊断降级处理修复。另四项定位到测试复用了未经重新验证、手动赋值的授权 fixture：
第一次生成被既有 `DataTableWriteGrant` validator 排序，后续组收到不同初始数组顺序。
改成每组独立输入副本后继续比较完整请求和报告，不删除字段、不放宽断言或修改生产授权。
这不是关于 R12 的新根因结论。

独立只读复核另外指出两项新观察器问题：JSON 业务字面量与白名单词碰撞可能留下部分
键/枚举；异常第 4 次调用可能把 validator 归到第 3 个保留项。新增四个反例在修复前
全部失败。现仅允许 Filter 根及 `items/children` 递归，业务 `value` 仅保留类型；独立
`_last_call` 指向实际调用，不污染前三项。四反例与既有取证测试合并后 **31 passed**。
独立复核随后确认两处静态闭合；执行证据由主智能体提供，不声称审查者重复跑过测试。

### 当前离线验收

- 后端相关 43 文件最终 **970 passed / 0 failed / 0 error / 0 skipped，163.17 秒**：全部
  `test_meta_planner*.py`，以及 Meta Agent/Managed、NodeContract、Authoring、Publish、
  Evaluator、Evolution、App、typed Workflow、表事务/实际隔离 Runner/效果证据与共享
  collector 的聊天、RAG 消费者。四个既有 FastAPI 弃用 warning 保留。
- 使用既有禁外网/清除凭据环境/隔离 Store 守卫，Provider 为 MockTransport；这不是对真实
  Provider 或完整生产发布预检的替代。复核修复前另有 966 passed，不重复累加。
- JUnit：忽略目录 `closeout-final-regression.xml`；SHA-256
  `45552793543abb467e3b2afb3a0e360549c80b4191acba173b1c41d440ce2c48`。
- `npm.cmd run build` 退出 0；保留既有大 bundle warning。相关前端四文件 40 passed：
  `metaAuthoring.test.ts`、`DataTableWriteGrants.test.tsx`、`XpertEvaluationsPage.test.ts`、
  `EvaluationWriteCases.test.tsx`；没有修改前端源码或启动预览器。
- 九个相关 Python 文件 AST 语法通过。首次经 PowerShell JSON 管道的语法批查发生编码
  解析错误，改成显式 UTF-8 字节输入后原九文件全部通过；没有为此修改生产源码。
- `git diff --check`、四个新文件尾部空白检查通过；12 个相关文件的常见密钥/私钥模式
  扫描 0 命中。有限模式扫描不是完整泄漏证明，嵌套字面量和私有 canary 另有反例测试。
- 冻结的 95 个文件中 87 个不变，8 个变化为允许的四个生产文件、一份测试及三份文档；
  新增仅观察器与三份测试。预览启动器、离线守卫、历史回执、Runtime、Backend 和前端
  源码不变。JUnit、临时 Store、构建产物不进入 Diff，暂存为空。

本地复跑命令（需保留本工作树既有离线守卫，不可导入预览启动器）：

```powershell
$tests = @(Get-ChildItem -LiteralPath server/tests -Filter 'test_meta_planner*.py' |
  ForEach-Object { 'server/tests/' + $_.Name })
$tests += @(
  'test_meta_agent', 'test_meta_agent_managed_gateway', 'test_workflow_node_contracts',
  'test_xpert_runtime_authoring', 'test_xpert_publish', 'test_xpert_evaluations',
  'test_xpert_structure_evolutions', 'test_xpert_app_api', 'test_workflow_typed_values',
  'test_agent_table_controlled_writes', 'test_controlled_write_execution_integration',
  'test_evaluation_write_isolation', 'test_evaluation_write_evidence',
  'test_native_router_chat', 'test_chat_file_output', 'test_multimodal_chat_foundation',
  'test_rag_managed_generation'
) | ForEach-Object { 'server/tests/' + $_ + '.py' }
& 'C:\tmp\modelmirror-mcp-test-venv\Scripts\python.exe' -B `
  .tmp-cw10-preview/run_generation_contract_checks.py @tests `
  --junitxml=.tmp-cw10-preview/closeout-final-regression.xml
```

### 后续门禁与回退

本批离线结果支持申请一次有判别力的真实生成，不支持宣布 R12 根因全部解决。下一次必须
重新批准模型、完整 CRUD 目标、安全合成元数据和最多三次 completion，并在外发前固定
加载源码、Schema、路由和安全摘要导出方法。观察不可用或调用不确定就停止，不自动重试。
生成与实际写入仍分别授权，不批准 Proposal、不发布 Xpert，也不自动读取业务表。

没有合并上游或重跑全量 `server/tests/`，历史独立失败、最新上游交点、真实生成/写效果、
帮助中心和整轮人工验收均继续保留。未提交、推送或创建 PR，不提前进入第 8 轮或 V4。
回退仅撤销本批私有类型投影与观察接线；已有 V2 节点、业务记录、权限和历史回执不迁移。

## R13：有限收口后的真实证据（2026-09-12）

**结论：本次真实生成仍失败；有限整改不能据此宣布整轮收口或达到 PR 门禁。**

用户授权的一次可见预览器生成保持原完整 CRUD 目标、同一零记录合成表、同一安全授权、
OpenRouter `deepseek/deepseek-v4-flash-0731` 和三次 completion 上限。本次没有修改生产
代码、Prompt、模型或校验，没有执行写节点、审批、发布、操作共享栈或自动重试。

| 阶段 | 本次直接证据 | 结论边界 |
| --- | --- | --- |
| 调用 | 三次 HTTP 200、stop，Provider 上报 55,374 Token，约 96.33 秒 | 排除本次超时、截断和预算外第四次派发；不等于编排成功 |
| 任务规划 | 首次 task_plan 通过，未消耗修复机会 | 本次可进入 Graph Patch 修复，不代表长期成功率已改善 |
| 首次图 | 模型侧 JSON Schema 通过；Intent 解析后四项 resource_contract 失败 | 错误位于两次 Query、Update、Delete；未进入真实编译与发布预检，四项具体原形未完整保留 |
| 唯一修复 | Graph Patch 在 patch_parse 阶段出现 60 项 Schema 诊断 | 缺少 add_node.title，夹带只读投影字段及未知操作；未进行 Patch Apply 或修复后重编译 |
| 字段传递 | 三次公开 Provider JSON 与 collector 的规范化 body hash 分别一致；实际 Schema 与预期 hash 一致 | 没有本次 collector 改写公开 JSON 或发送了另一 Schema 的证据；不追溯证明 R12 |
| 取证预算 | 摘要超过 65,536 ASCII JSON 字符，全部 structure 明细被省略，比较状态降为 incomplete | 安全边界有效，但此次字段级归因目标未完整达成；不是编排失败的原因 |

修复错误中的 `input_sources/output_ports/required_output_ports/control_outcomes/final_source`
与 `_graph_patch_repair_contract.existing_nodes` 的只读摘要同名。实际 Patch 契约仍要求
`title/config/output_variables` 等写入字段并拒绝额外字段。这支持优先检查“只读图摘要与
Patch 写协议形状混用”，但不能从同名字段断言模型内部原因，也不能直接据此提前 V4 或
宣布 DeepSeek/单模型模式已经达到上限。

本次留下的精确边界是：JSON Schema 合法不足以证明资源语义合法；生成公开内容与收集
结果未变异，但修复响应已经不满足发送的 Patch Schema；取证摘要在普通完整 CRUD 图的
三阶段累计体积下不足以保留关键字段。下一步应先离线完成可限额保留首错/阶段差异的取证
验证和 Patch 读写投影的正反例，不通过扩大字节上限、放宽校验或增加付费重试掩盖缺口。
这只是待确认方向，本次没有执行新修复。

- Proposal：`proposal_59d8de24c40f43e185ee3d152fc7486c`，pending/r1，validation=false，未应用。
- Run：`224e72b7-1159-45bd-b1ee-3179ba16a1c4`；completed 是请求终态，不是质量通过。
- 缺失模型错误来自失败后的占位候选，不归因为用户模型配置丢失。
- 前后九个历史提案不变；合成表仍为零记录；运行登记只有此次 meta_planner。
- 外发前守卫 27 passed、安全导出复查 31 passed。本次冻结的生产源码保持不变；上一批
  970 项验证不计为此次重跑。本次没有把旧全量/构建结果冒充新证据，原环境检查中止及所需
  权限复跑已单列记录。
- 预检、回执、安全结果、诊断及事后状态保留在 `.tmp-cw10-preview/r13-*` 忽略目录；
  安全摘要在进程退出前已导出，不依赖内存 RunRegistry 长期保留，也不声称原始响应重放。
- R13 回执 SHA-256：`56a5d1d8a2a7448128dd306db24c7bd2aa1df64c5be2132b8cce70f7223405a5`。
- 结果摘要 SHA-256：`beab2669fb15dad387b0e3540d42eac2560a456bdf6cbe413ad31faa50d33683`。

上游集成、全量回归、真实写效果、帮助中心和人工验收仍未完成。没有提交、推送或 PR；
不进入下一轮，不授权新的真实调用。

## R13 后手术刀式修复（2026-09-12）

**本批只修已复现的契约与诊断缺口，不宣称还原四项原始资源错误，更不以离线通过替代真实生成验收。**

### 改动与不变项

| 位置 | 已实施的局部处理 | 保持不变 |
| --- | --- | --- |
| `generation_evidence.py` | 超限先将重复结构按 hash 引用去重；仍超限才逐结构省略，并只降低相关比较的证据等级；从真实 Patch Schema 推导操作/字段名称，记录受限结构与计数 | 65,536 ASCII JSON 字符上限、三次调用上限、无正文/无文件 I/O；完整 body hash 只用于原有配对，不作完整响应重放声称 |
| `node_adapters.py` | 资源异常细分为字段、运算符、动态输入与字面值错误；模型修复输入和权威资源校验共用谓词操作数 Schema 函数 | 原类型相等规则、字面值类型验证、records 可信来源、授权字段和写入上限 |
| `generation_diagnostics.py` | 保存首错的固定错误码、节点/谓词/输入索引、类型和 Schema checksum；不重复计为泛化错误 | 原 issue 上限、无业务值/Prompt/原始响应、失败阶段与重编译状态 |
| `meta_planner_v2.py` | 去除重复 existing_nodes 摘要；原图明确为只读，命令必填/可选字段从真实 Pydantic Patch 定义生成；为已授权表投影精确动态输入要求；授权/可用性失败后不解析资源 Schema | 严格 Patch 解析器、原任务目标、模型选择、三次预算、唯一修复、公开 IR、Runtime、审批与发布边界 |

`records` 和 `predicate_*` 不可混用。前者仍需同表 Query/Insert 的真实完整结果；后者必须
匹配相应字段的标量类型，`in` 则为该类型数组。没有合法输入来源时继续拒绝，不自动提取
record_id、不改为字面值、不增加属性路径或放开任意类型收窄。

新增资源摘要中的索引从 0 开始；谓词按既有 Filter 深度优先顺序编号。只保存类型与
checksum，不保存字段名、字段值或完整 Schema。精确 Schema 只存在于已授权模型修复
输入中，不复制到普通诊断报告。

### 证伪与独立复核

- 观察器三个初始反例先失败：大报告全量删明细、Patch 变化无法区分、无操作数投影。
  修复后又将独立复核的未知 observation key、逐操作内容 hash、无条件“已观察”表述转为
  三个失败反例并修复。报告仍以硬上限封顶；没有新增逐操作内容 hash，结构摘要不是执行证据。
- 资源六个反例先失败，修复后可区分四个合成资源类型错误及未知字段、非法运算符和字面值
  不符。它们是替代性合成对照，四个原始 R13 fingerprint 不同，不能称原产物重放。
- 通过实际 Legacy/Managed 收集代码和 MockTransport，对照首图资源失败后的两条路径：
  非法 Patch 仍产生 60 项解析错误，未 Apply/重编译，首图四项具体错误仍保留；合法显式
  Patch 完成编译往返并只产生 pending/r1 Proposal。每组最多三次 Mock completion，业务
  记录入口为失败哨兵，未使用真实 Provider。
- Sol 独立复核提出授权失败后诊断泄漏期望类型，已用禁止资源解析的反例复现，再将资源
  授权/可用性检查设为 Schema 解析前置条件。原拒绝结果保留，不增加授权。
- “原图字面值来自业务 Store”的另一条意见经实际调用链核对后撤回：原图来自同次已授权
  completion 输出，资源校验未将 Store 数据回填原图；禁止记录访问的完整模拟亦通过。
  因而没有加入会改变 Patch 语义的字面值 hash 替换。
- 同步 Schema 校验仍信任服务端生成的本地契约，未引入正则执行超时沙箱；当前没有模型
  可向该 Schema 注入任意正则的证据。未来若改变契约来源，应单独复审，不当作本次 R13 根因。

### 验证状态

- 观察器及生成集成：38 passed；资源授权前置及修复链路：65 passed；这些会包含在最终
  联合回归中，不重复累计。早期一次旧英文错误文案断言改为稳定错误码，原拒绝断言保留。
- 完整模拟测试初稿另有两项测试编写问题：调用 Patch 内核用了错误参数名；HTTP 禁止点
  同时拦截 MockTransport。分别按实际内核签名、更低层真实 HTTPTransport 拦截修正后
  原命令重跑；没有改变产品解析/传输代码以让测试通过。
- 最终源码联合回归 **1,017 passed / 0 failed / 0 error / 0 skipped，228.07 秒**；四个
  既有 FastAPI 弃用 warning。覆盖修改前后拒绝、首因保留、严格 Patch、状态恢复及旧模块。
  之前 1,016 项及各微批结果不重复累计。JUnit SHA-256：
  `b89948ea3b7f60be38d9313a828abaf966076a0cab179332842237f981589807`。
- 前端四文件 30 passed / 14.06 秒；生产构建退出 0，仅既有大包体积 warning。八文件 AST
  语法与 Diff 检查通过；常见凭据模式扫描零命中。未启动前端服务、打开预览器或重建容器。

最终复跑清单：`server/tests/test_meta_planner*.py`（当前 28 文件），加 Meta Agent、
NodeContract/Registry、Authoring、Publish、Evaluator、Evolution、App、只读夹具、Agent
Table/受控写 Runtime/执行集成/隔离/效果，以及 Workflow typed values/AI，共 47 文件。
使用既有 `run_generation_contract_checks.py` 离线入口、独立临时 Store、禁外网与禁凭据
环境。JUnit 位于 `.tmp-cw10-preview/r13-fix-integrated-final.xml`，不纳入 Git。

本批测试文件为 `test_meta_planner_generation_evidence.py`、
`test_meta_planner_resource_repair_diagnostics.py`、`test_meta_planner_patch_protocol_boundary.py`
和旧 `test_meta_planner_read_resources.py` 的错误码断言。生产仅改上表四文件；文档仅本审计
与任务卡。回执、预览入口、调用守卫及其他既有待提交内容均按开工 hash 保持不变。

### 剩余门禁

本批未重跑全量 `server/tests/`；此前独立 Worker 恢复测试失败、上游集成、真实生成和
实际写效果、帮助中心与整轮人工验收仍未关闭。R13 历史回执和预览启动器不变，不把本地
工作树源码当作已加载的预览器版本。下一次真实复测须重新授权，外发前重新绑定加载源码、
目标、模型、授权和三次 completion 上限；本次不自行继续调用。

回退只撤销本批观察/诊断/修复输入投影，不撤销已完成的业务数据或整轮能力。本批未提交、
推送、创建 PR、审批或发布，未进入第 8 轮/V4。

## R14 真实复测（2026-09-12）

**结论：仍未通过，不追加调用或补丁。** 用户重新授权的原完整 CRUD 目标在同一隔离
预览器可见页面提交一次，模型与授权不变。调用守卫 27 passed，四文件针对性检查 58
passed 后，重启本任务后端并固定新进程与源码 hash；没有操作共享栈。初次受限端口检查
退出 1，后续授权只读核实原进程仍在；重复启动保护在启动前拒绝，不当作后端故障。

| 层次 | 本次证据 | 判定边界 |
| --- | --- | --- |
| 实际调用 | 三次 HTTP 200 / stop；34.749 秒；51,883 Token | 不是通过验收的证据，无第四次调用或不确定派发 |
| 任务计划 | 首次通过 | 未消耗任务修复预算 |
| 初次图 | 私有 Schema、Intent 解析、资源授权通过，resolve 失败 | 不再是 R13 的四项资源授权拒绝；首个 resolve 错误仅保留 fingerprint，不能还原全部失败详情 |
| 唯一 Patch | 严格解析通过，仅一条 connect_data；既有重复边去重为零操作 | 本次没有 R13 的缺少 title、只读字段夹带或未知操作；模型没有提供有效的语义修复 |
| 最终 resolve | summarize_agent.task 拒绝 object | 同请求结构证明 variable_aggregator.result 为 object，并直接作为 Agent task 输入；当前 NodeContract task 为 string |
| 观察器 | 三份去重结构，28,621 ASCII JSON 字符，无遗漏或 incomplete | Provider/collector 完整规范化 body hash 一致；只观察受限字段，不是完整原始响应重放 |

`graph_ir_v3.py:1218` 的兼容性检查及 `node_contracts.py:3594` 起的 Agent task string
端口与最终诊断一致。只确认这一实际阻断，不推断其余连接全部合法。现有有限输出 Schema
归一化仍发生于修复之后，因此诊断列明前后两个 resolve 阶段，不混为同一错误。

新 Proposal 为 `proposal_e7b6210fdd1c4326a8b604e79bdecf0f`，pending/r1、validation=false、
未应用；Run `1ca48044-0a26-454d-8f78-e7718972f55d` 的 completed 仅表示生成请求结束。
失败占位图的 missing_workflow_agent_model 不作为首因。页面保持未通过与只读状态。
十个历史提案未变，合成表仍为零记录，Schema v1、草稿 revision 4 未变；无实际写节点执行。

安全证据只放忽略目录：`r14-preflight.json`、`r14-safe-result.json`、`r14-after.json`、
`generation-receipt-r14.json`。回执 SHA-256：
`1e9f05e75e5d0bb7febfe1c0e2ef43581d91b11239abf095c39d3caddb6b4965`；结果 SHA-256：
`a811e40022769f590c78475769174016a7dc430b93c7afaa3903121d741943b9`。
Prompt 正文、记录、凭据和隐藏推理不进入证据导出。此轮不修改生产代码、测试或调用守卫。

建议下一步仅离线复核对象消费契约和唯一修复覆盖，再依据反例决定最小处理；不直接放宽
task 类型或代模型改图，不提前多 Agent/V4。最新全量、上游交点、真实写效果、帮助中心及
整轮提交门禁继续待完成。本次未提交、推送、创建 PR、批准或发布。

## R14 后输入契约修复与离线证伪（2026-09-13）

**范围：修复已定位的三处契约传递缺口，不改变 Agent task 的 string 边界。**
本次有局部修改授权，没有新增外发、预览器、共享栈或业务写入授权。

| 缺口 | 本批处理 | 保持的边界 |
| --- | --- | --- |
| 模型侧静态输入声明未充分投影端口类型 | `_ports` 复用既有 `_declared_type_envelope`，依据 Adapter 的真实输入端口生成私有 Schema | 对象属性、资源、真实来源及动态 Schema 仍由权威语义检查判定，不另建类型系统 |
| Resolver 仅暴露首个独立输入类型错误 | 同一个检查循环返回有序 `GraphInputTypeIssue`；V3 不再先用有损 V2 类型名比较 | 任一问题仍阻断编译；未知端口、来源、控制依赖及资源错误不被忽略；旧 V2 拒绝行为保留 |
| 多输入 task 没有准确桥接指引 | 修复提示复用授权后的 Resolver 结果，以节点/输入序号定位具体错连 | 模型必须显式提交序列化节点、数据/控制边及模板调整；不自动改图，不删除其他合法输入，不追加修复调用 |

修复详情仅进入本次模型提示，最多 64 项；类型投影保留 type/nullable/items/any_of，
不夹带对象字段、必填字段清单、默认值或记录正文。持久化仍走既有诊断格式，只新增使用
已有的错误码和节点/输入位置；没有新增 Store。Resolver 直接调用者继续收到原首条英文
异常文本，生成报告使用中文摘要，不以异常文本正则推导多输入事实。

### 反例与对照

- 同一 Agent 同时接收合法 user_input 和非法 pack.object：只提示后者的显式序列化桥接。
- 上游实际为 object、输入伪称 string：仍拒绝，不能通过重命名声明绕过。
- 实际为 string、声明误写 object：不建议额外序列化；保留现有 Patch 后派生类型归一化。
- 合成 CRUD 组合的两处 records 声明错误与末端 object 错连在唯一修复前同时可见；
  空 Patch 仍无效，显式桥接后编译往返稳定，整个 Mock 生成仍不超过三次 completion。
- 未授权表、未授权序列化、未知输入端口、未知来源、变量别名和循环分别拒绝；没有依据时
  不输出猜测的可信类型事实。新旧 Proposal 均不因此自动应用或执行。
- 遍历当前静态输入端口，对比私有 Schema 和权威 `_schemas_compatible` 的类型判断；
  V2 旧错误保留，V3 真实来源守卫不放松；合成敏感字段和值不进入安全类型诊断。

这些是确定性反例和模拟传输证据，不是新的 DeepSeek 实测，也不是缺失原始字段的 R14
完整重放。首次真实 resolve 的错误仍只能按原 fingerprint 保留，不能倒推其唯一原因。

### 验证记录

- 新测试首轮 13 failed / 3 passed。实现后出现三项测试目标与图不一致的前置拒绝，
  修改测试目标为纯输入打包，不调整产品门禁；补齐攻击覆盖后 29 passed / 18.30 秒。
- 六文件初步回归 154 passed / 75.89 秒，不与后续结果累加。
- 首次 48 文件联合回归 1,036 passed / 4 failed / 249.30 秒。失败全部位于旧生成边界
  审计测试：两项依赖旧英文句式，两项依赖旧 authorization 阶段。更新为具体错误码、
  输入位置、resolve 阻断和未进入编译的断言，保留原非法图、零业务写入和调用预算断言。
- 原失败文件与新测试重跑 68 passed / 56.58 秒；随后同范围最终联合复跑 **1,040 passed /
  0 failed / 0 error / 0 skipped，306.12 秒**，四个既有 FastAPI 弃用 warning 保留。
- 五个 Python 文件 AST 检查、全树 Diff/本批新增文件空白检查、七文件常见凭据模式扫描
  通过；`npm.cmd run build -- --outDir ../.tmp-cw10-preview/r14-input-contract-build`
  通过。产物位于忽略目录，没有覆盖预览器的 `client/dist`；未使用 `--emptyOutDir`。
  既有大包体积 warning 与独立 outDir 不自动清空提示不隐藏。本次没有修改前端源码，
  未重跑前端单测或操作 UI。

联合复跑使用既有 `run_generation_contract_checks.py` 离线护栏，当前全部 29 个
`test_meta_planner*.py` 文件，加 Meta Agent/Managed 两入口、NodeContract、Authoring、
Publish、Evaluator、Evolution、App、Workflow typed values/AI、Agent Table/CRUD/受控
写 Runtime/集成/隔离/效果，共 48 文件。JUnit 为
`.tmp-cw10-preview/r14-input-contract-integrated-final.xml`，SHA-256：
`3b327643d43b7fc2dee901832b7cbcbdbfbf250b7268cc1091166604593dbd12`。
本节各中间测试不与最终结果累加，不把 48 文件回归称为全量 `server/tests/`。

代码微批只涉及 `generation_contract.py`、`graph_ir_v3.py`、`meta_planner_v2.py` 及两个
测试文件；文档微批为本审计与任务卡。没有修改 Runtime、权限、Adapter、公开 Schema、
前端代码、预览启动器或调用守卫。历史 R14 回执与结果 SHA-256 与上节一致。

真实复测需要重新授权并先核实预览器加载版本。最新全量、上游交点、实际写效果、帮助
中心和整轮人工验收仍未关闭，不把本批离线通过表述为 PR 提交门禁已满足。回退仅撤销
本批输入投影/诊断/修复提示，不覆盖整轮已有改动或撤销业务数据。

## R15 授权真实复测（2026-09-13）

**结论：原目标真实候选生成通过，整轮提交门禁尚未关闭。**

保持原零记录合成表、目标、11 类节点、三种操作、三个字段及 1 行授权上限，使用
OpenRouter `deepseek/deepseek-v4-flash-0731`。只通过独立预览器点击一次生成，未运行
工作流或业务写节点。加载修复的新后端为 PID 13276，前端 PID 29972 保持不变；调用
守卫 27 项及 2 个子测试通过，初次受限环境无输出检查被结束，不能计为通过。

- 00:52:37 至 00:53:00，23.525 秒，三次 completion 均 HTTP 200/stop，实际 Token
  4,873 / 22,267 / 24,239，共 51,379。无第四次调用、不确定派发或自动重试。
- 初次 resolve 同时定位 update_status、delete_ticket 的两项 DATA_TYPE_MISMATCH；
  唯一 Patch 为两次 disconnect_data 加两次 connect_data，4 -> 4，随后经过原有派生
  Schema 归一化、重新解析、授权、resolve、compile、publish_preflight，全部通过。
  R14 的 object 直接进入 Agent task 错连本次未出现，不能据此声称多次稳定成功率。
- Proposal `proposal_a1e5d910353342da9fb5c402311171a6` 为 pending/r1/未应用，IR
  current，Proposal 与报告 validation 均 true、零 issues。真实页面已展示中文候选
  “受控写入验收工单测试工作流”及可用无头编辑入口。Run 为
  `201b9196-2522-4861-bbc9-b02256bf9dd4`。
- 15 节点候选保留 Insert -> Query -> Update -> Query -> Delete 控制顺序；两个
  records 输入仍直接来自同表 Query，固定 Schema v1/1 行上限。汇总 Agent 实际引用
  序列化结果。这是配置/编译证据，不是数据库写入成功证据。
- 11 个历史 Proposal 状态、revision、应用记录不变；表仍零记录、draft revision=4、
  Schema v1/checksum 不变；运行登记仅本次 meta_planner，没有工作流执行或自动审批。

三阶段请求 Schema 均匹配预期，Provider 与 collector 的完整规范化 body checksum
逐次相同。初次 Graph 的 validator body 不同但被观察结构相同；full_payload_replay
仍为 false，不将结构观察解释为全量重放。三份结构全部保留，omitted_calls=0，
details_omitted=false，紧凑 ASCII JSON 27,113 字符。

安全证据在忽略目录：`r15-preflight.json`、`r15-safe-result.json` 和
`generation-receipt-r15.json`。不保存完整 Prompt、记录或隐藏推理。
回执 SHA-256：`dd90f08e3f024ee9a638288e79a38d2f6ca32c22849b52e5cc5913cb7da51c35`。
结果 SHA-256：`cb32116bdac69b00c6638ff6ee45390c070e087da5cd9070cd9f89a852bd16e2`。

本次仅重载任务后端并写入两份文档/忽略证据，不改实现、测试、Prompt、权限或调用
守卫。最新全量、上游交点、实际写效果、帮助中心及整轮人工验收继续保留。六个序列化
辅助节点的简洁性和既有英文诊断也未在本次优化；不追加调用，不自动提交或创建 PR。

## R15 后验收结论限制与追加门禁（2026-09-13）

用户明确要求：连续失败背景下，一次串行成功不能判定稳定；须先完成实际写入效果、
最新全量与上游集成，再追加一个侧重点不同的泛化实测。本审计维持“单例生成通过，
整轮未验收”，不将此前失败从分母或记录中移除，也不提前采用 V4 多 Agent。

只读刷新上游至 `d246527d5554b16594d390172cfa38876f998ccb` 后，当前任务基线落后
33 个提交；上游 296 文件中，与当前 98 个待提交路径直接相交的只有 `server/main.py`
和 `server/xpert_runtime/execution_store.py`。后者已经核实存在严格加载、原子替换、
前一有效备份与 unavailable 护栏，必须连同本轮私有写日志一起保留，不能以整个旧文件
覆盖。尚未集成，不能据此宣布兼容。

加严门禁详见任务卡“R15 后提交门禁加严”表，核心判据为：

1. 集成、全量与最终实测绑定同一冻结源码、配置、资源和 Dataset 版本。
2. 固定 R15 原候选，用真实隔离 Backend 验证逐节点 Insert/Update/Delete 前后状态、
   revision、查询可见性与哨兵不变；最终空表或回答成功不足以证明中间写入真实执行。
3. 前置门禁通过后新增一个合成质检泛化目标，结合字符串/整数 Schema、条件分支、
   授权更新、只读分支和缺失数据安全终止；一份候选由三个固定初态验证各路径。
4. `workflow_path_match` 与 `workflow_effect_match` 必须同时提供证据，禁止借由
   空分支、无效辅助节点或 Prompt 声称效果来过关。不能为了该目标开放延期能力。
5. 新模型调用分别获得外发内容、模型和预算授权，失败不自动重试或临时更换目标。

本次仅核对来源、现有隔离评测/效果断言实现并更新两份文档，没有进行上游集成、最新
全量、实际数据库写入或模型调用。泛化的具体目标和断言在前置门禁通过后冻结；上述
三种初态是拟定验收方向，不是已运行样例。追加一个案例也只增加跨场景证据，不声称
统计稳定成功率；最终人工验收与提交授权继续独立保留。

## 加严收口的集成结论（2026-09-13）

已在独立集成树将原 98 个待提交路径重放到 `d246527d`，没有移动原预览器或复制其
Runtime 数据。只有主入口出现文本冲突，双方的存储不可用阻断和受控写预检均保留。
执行存储的机械合并仍留下一个实际缺口：新私有写日志未受上游加载、离线检查和持久化
约束。先以 26 个失败反例证实，再统一执行 1,000 项/16 MiB、对象格式及 JSON 安全
校验；保留上游原子备份、不可用状态和显式恢复，不添加自动恢复或重试。

特别验证了 Backend 已提交、日志保存失败、主快照损坏、恢复较旧备份的真实 SQLite
组合：相同请求只读取既有操作账本，改变请求则冲突拒绝。日志不能独立保证 exactly-once，
仍由事务账本决定是否已提交。Store 专项 33 passed；相邻 Store/Runtime/Evaluator
组合 134 passed / 1 skipped，不能把重叠用例累计为独立证据数量。

独立复核曾建议增加通用终态断言，主端复核后撤回：R15 的 Insert/Update/Delete
本就要求逐节点真实前后状态及事务回执，最终空表是正确业务结果，不构成这条用例的
缺陷。保留的缺口是隔离表内尚未有非目标记录的端到端对照，现以原指标补充初态并验证
其最终数据和 revision 不变。四文件 92 passed，不新增公开字段或修改原候选；新增
测试自身一项平铺数据读取错误已修正，没有据此增加产品补丁。

前端新全量为 1,029 passed / 2 failed，两个分时价格失败在相同干净上游对照中均复现；
Worker 断连测试也在上游独立复现 `ConnectionResetError`。这些已核实项不能归因为
本轮，也不能遮蔽尚未结束的全量或用平台差异猜测其余失败。

当前门禁仍是：最新完整回归及逐项归因 → R15 原候选的两初态真实隔离写入与下游
模型验收 → 不同侧重点的非线性生成及三路径实测 → 同基线帮助中心与最终人工验收。
后两次真实调用的内容、模型与预算仍需分别确认，不复用 R15 的三次额度，不提交 PR。
完整源码归档、隔离条件和阶段结果见任务卡末节；本节不把 mock、接口可用或回答正确
当作业务写入或泛化已通过。

## 9 月 17 日恢复核对与新增确定性反例

以上各节保留其当时状态。最新独立收口树基于 `a7d99925`；此前 `d246527d` 两侧 Linux
全量的 90 个失败身份完全相同，但不是全量通过。只读复核与启动器 A/B 发现测试工具自身
递归启动 pytest、误拦本地 Unix socket 的问题，已在忽略目录修正，并在一致的无网络环境
重跑候选与干净上游。未调整产品安全校验、删除断言或把跳过项算作通过。最新上游新增的
RPG Schema 交叉测试在补齐原锁定依赖后通过，同批结果为 181 passed。

### 验收目标漂移

原 R15 提案目前为 approved/r3，记录 4 项 `update_node`，Graph IR 和编译 checksum
均已改变。不能以“同一个 proposal_id”推断它仍是原始生成物；也不能根据仅含安全结构
的 r1 结果导出猜测完整 Prompt/config。已把固定当前 r3 补验与提供原 r1 快照两种路径
列给用户，回归继续；本次不恢复旧 revision、不执行审批、不将新证据归到旧候选。

### 安全终止与效果评测交点

在准备非线性验收时，按真实执行链复现了另一个具体缺陷：隔离写入环境捕获
`WorkflowStreamFailure` 后，仅识别预期 revision 冲突，未接续原有声明式错误终点语义。
于是两条合法用例均在路径与 Backend 效果正确后，被执行结果分类强制归零：

| 用例 | 实际 Runtime / SQLite 事实 | 修复前结果 |
| --- | --- | --- |
| 缺失路径安全终止 | 可信 `router:unmatched`、`EXPECTED_STOP`，写节点未执行，隔离表空，业务哨兵不变 | failed / 0 分 |
| 提交后安全终止 | 可信 `router:matched`、同一错误码，一次 Insert 已提交，原 Backend 回执与数据仍存在 | failed / 0 分 |

仅在错误终点有可信语义记录，且用例错误码、记录错误码和异常错误码三者一致时，将其交回
原路径/效果指标判断；否则保留失败分类。未知错误不会被“有 path”掩盖，错误 outcome
与不符效果仍由原指标拒绝，先前提交不回滚。没有修改生成提示、权限、Backend 或调度。

两条反例先取得 2 failed。加入错误码、缺失 path、错误 outcome、缺失语义 ref 和效果
不符对照后，12 passed；另安排独立只读复核。该结果只证实这一交点，不替代原候选真实
模型调用、非线性生成成功率或整轮提交门禁。完整最终全量、帮助中心 UI 与人工验收继续
单独保留；本批没有外发、共享栈操作、提交或 PR。

### 最终全量对照与剩余证据边界

最终修复版已经完成全量：7,382 passed / 15 failed / 30 skipped，另有 7 项 Workflow
契约通过。最新干净 `a7d99925` 为 6,746 passed / 15 failed / 30 skipped，加相同 7 项
契约。两侧失败身份完全一致；两处失败栈差异仅为随机目录 ID、集合显示顺序。12 项
Worker/Workspace 权限失败和 3 项音频断言均保留原始证据，不能将本次称为“全绿”。
在相同干净主线测试快照中，只补测试所需 `DAC_OVERRIDE/FOWNER` 后，相关三文件
31 passed，覆盖全部 12 个权限失败；仍无网络、宿主挂载或产品权限修改。该单变量
对照完成环境归因，但不改写原全量统计；3 项音频断言仍未修复。

独立复核后补充虚假冲突与空错误码反例，新文件共 16 项；相邻五文件联合 88 passed。
其中最后 4 项只在归档后专项运行，最终全量包含的是前 12 项。没有改变生产代码后仍
引用旧归档的问题，也没有累计重跑数量作为稳定性证据。前端两项价格失败在最新主线
独立副本再次复现，12 passed / 2 failed；候选全量 1,029 passed / 2 failed。

新预览器没有凭据且禁止外联。可见界面验证了显式写授权、默认一行上限、初始化记录和
效果断言保存、Dataset 版本发布；发布合成夹具后活表仍为零记录。它证明配置与发布
隔离，不证明写节点已执行，也不证明原 R15/r1 或新泛化候选成功。帮助中心正式截图与
教程重放仍未通过，原 r1/r3 身份差异仍必须在后续实测中明确。

当前收口判定：上游集成和最新差异回归已有证据；原候选实际写效果、不同侧重点的真实
非线性生成及三路径执行、帮助中心和最终人工验收仍未闭环。不扩大额度、权限、节点或
运行环境，不提交 PR，不把基线失败或单次成功隐藏在“稳定”结论中。

### 当前 r3 的真实写效果补验（2026-09-17）

后续在独立预览器 `15459/16459` 固定当前已审批 r3，未修改其配置、状态或 revision，
也未重新批准或发布。原始 R15/r1 的完整配置仍不可恢复，因此本节仅证明当前 r3，
不回填为原始生成物验收。生产代码与前述最终全量归档保持一致。

通过界面建立并发布评测集 `xeval_dataset_f169301d54b34d0c987f0ec53b02dfb2` v1，
分别使用空表、带一条非目标合成哨兵的初态，配置三个写效果断言和实际语义路径断言。
已审批 Proposal 不在现有 UI 的待审批候选列表中，故使用同一 Evaluation API 固定
`proposal_a1e5d910353342da9fb5c402311171a6` / r3 启动，随后在界面检查报告。
不能将该启动步骤描述为纯 UI 操作或新一轮 Planner 生成。

| 核验项 | 实际证据 |
| --- | --- |
| 运行 | `xeval_run_e46b69cc3d534bcbb250c55ed2fc622d`，2 completed / 0 failed |
| 确定性评分 | 两例 `workflow_effect_match` 与 `workflow_path_match` 均为 100% |
| 真实写入 | 每例独立 SQLite 中 Insert、Update、Delete 各提交一次，各影响 1 行 |
| 记录身份与 revision | 三次操作针对同一真实记录；Insert 为 1，Update 从 1 到 2，Delete 验证 2 |
| 写后读取 | 两次 Query 均实际读到该记录；未使用旧只读夹具替代写后状态 |
| 非目标记录 | 三节点的非目标记录 checksum 前后一致；哨兵业务字段和 revision 1 保持不变 |
| 活表隔离 | 原测试表与新预览器活表均为 0 条记录、0 项受控写入账本 |
| 外部调用 | OpenRouter `deepseek/deepseek-v4-flash-0731` 两次真实汇总，均完成，无不确定派发或自动重试 |
| 用量边界 | Provider 回报 1,898 + 2,025 = 3,923 token；未核对账单，不采用报告的估算 token 代替 |

另以只读 SQLite 查询核对私有 ledger 的请求 checksum、前后状态、影响数和剩余记录，
结果保存在忽略目录的 `r3-write-proof.json`。完整记录、凭据、私有 Runtime 和调用正文
不进入本文、Git 或审计摘要。普通资源读取断言未配置，报告的 `resource_evidence=missing`
如实保留；不能据此宣称独立的只读资源指标通过。

调用前的首次尝试 `xeval_run_7bc5e36e265a4f25b98259c9aba51cfe` 被本次验收启动器
拒绝：固定 Workflow hash 未包含 Evaluator 合法注入的两个 Query Schema 固定字段。
逐字段确认只存在这四处差异后，修正忽略目录的精确允许清单再运行。该次模型调用和
写入均为零；不是产品缺陷，也没有修改生产校验来放行。启动器自身的六项离线测试通过，
仅证明其次数、恢复和请求限制，不替代真实 Provider 或产品证据。

### 本次验收剩余门禁

- 当前 r3 串行候选的两种初态写效果已通过；不代表未经编辑的 r1 或泛化稳定性通过。
- 不同侧重点的分支案例已创建零记录合成质检表和 Schema v1。为新表选择
  `update/status`、最多一行的授权时被平台安全审批拦截，要求用户明确确认该具体范围。
  已停止，不改走 API 绕过，尚未派发该案例的 Planner 或分支执行请求。
- 非线性生成及低分写入、高分不写、缺失安全终止三路径，以及帮助中心正式截图、
  教程重放和最终人工验收仍待完成。既有上游失败继续单列，整轮提交门禁尚未闭环。
- 本次没有新增生产补丁，没有操作共享栈，没有提交、推送或创建 PR，也未进入第 8 轮。

### 非线性泛化实测与独立反例（2026-09-19）

用户随后明确批准零记录合成质检表 `table_7565c77441414604b64c01739977f1d5`
的 `update/status`、最多一行范围，仅用于待审批候选及隔离评测，不允许写活表。
恢复本任务独立预览器后，通过可见界面执行一次生成，未扩大节点、资源、字段或调用预算。
此前关于“等待精确授权、尚未派发”的状态到此结束；历史记录保留。

**结果：本次泛化生成失败，整轮仍未达到提交门禁。**

| 核验项 | 实际结果 |
| --- | --- |
| 目标 | 按批次查一条记录；缺失时安全终止；低分时更新 `status`；高分时不写；互斥分支由 Agent 汇总 |
| 提案 | `proposal_d908c8a4c69f414fa26d0ee9e97e0b5c`，pending/r1，validation=false，未人工修改 |
| Provider | OpenRouter `deepseek/deepseek-v4-flash-0731` |
| 调用 | 任务规划、Graph 编译、唯一 Patch 修复共三次，均 HTTP 200、finish_reason=stop；无第四次或不确定派发 |
| Provider 用量 | 4,007 + 17,216 + 20,418 = 41,641 token；不是账单审计结果 |
| 结构传递 | 三次请求契约与预期 checksum 一致；Provider、collector、validator 均 Schema-valid，受观察结构 checksum 一致 |
| 校验 | 初次编译 8 项问题，唯一 Patch 后仍有 2 项阻断；不能以问题数减少判定通过 |
| 安全边界 | 未执行此候选、未创建三路径评测、未批准或发布；只读 SQLite 核验新合成活表 0 记录、0 操作账本 |

最后两项阻断为：

1. `Router check_found has shadowed or unproven outcomes: unmatched`。
2. `Variable query_result is not reachable at node serialize_query_result.`。

失败后的占位图另有 Agent 缺少模型的错误；这是失败占位产物，不能反推用户的有效模型
选择丢失，也不能用占位图的串行形状代替本次模型 Graph。取证仅保留受限结构，原始模型
响应未完整持久化；本次结论不声称完整 payload 重放或已排除所有语义差异。

**已证实的工程缺陷：根对象判空的静态 witness 不完整。**

`control_flow._input_witnesses` 对 `field=""` 只尝试固定候选值。唯一对象候选是 `{}`，
当权威 Query 结果 Schema 包含必填字段时，该候选被正确的 Schema 校验拒绝；函数却
没有生成另一个满足 Schema 的非空对象。于是可空对象的判空只剩 `None` 一种合法 witness。
`_schema_seed` 已存在，但目前只用于带字段选择的比较分支，不覆盖这个根对象场景。

在相同真实 Runtime 比较函数上执行合成反例，不调用模型、不修改产品代码：

| 输入 Schema | Runtime 对 `null` 与合法对象的结果 | 静态分析结果 |
| --- | --- | --- |
| 可空对象，`score:integer`，无必填字段 | matched、unmatched | matched、unmatched |
| 同一对象，仅增加 `required=[score]` | matched、unmatched | 只有 matched |

这是可重复的类级反例，证实分析器会错误拒绝合法非空路径。它与本次第一条诊断一致，但
由于缺少完整原始 config/control edges，不将其表述为本次 payload 已完整复现或唯一根因。
第二条错误来自独立的控制祖先可达性检查；该检查不依赖 witness 结果，不能承诺修好判空
即可消除它，更不能通过删除可达性校验或替模型补连控制边来放行。

后续最小处理范围应先限制为：按权威 Schema 补齐合法根对象 witness，并覆盖必填、嵌套、
nullable/union、非法值不放行和真实 Runtime 一致性；随后独立核对数据源与消费节点的控制
祖先关系及单次 Patch 修复契约。若原始安全证据不能证明具体边布局，明确保留这一边界，
不猜测还原，不增加提示词补丁、模型轮数或 V4 多 Agent。该处理尚未实施。

离线命令 `python .tmp-cw10-acceptance-20260917/verify_generalization.py` 退出 0，
表示成功核对上述失败与反例，不是产品通过。脱敏证据为忽略目录的
`generalization-proof.json`；次数账本 SHA-256 为
`020e02a22c688cf70b42f4908a012226612516e387e224b1f4af1433b46d9d78`。
生产源码仍与最终全量冻结归档一致；本次只新增忽略目录核对脚本和文档，不重写旧 r3
证据，也不将剩余两次分支运行额度改作新的生成。当前 r3 写效果已通过、非线性生成失败、
三路径运行未执行、帮助中心及最终人工验收未完成，四种状态分别保留。

### 根对象 witness 最小修复复核（2026-09-19）

用户随后授权最小修复，本批仅涉及 `control_flow.py`、既有控制流测试及两份收口文档。
前节的“尚未实施、生产源码未变”描述截至该节取证时刻；本节起生产源码已有明确局部变化。

实现复用已有 `_schema_seed`：原先只供子字段比较使用的对象 seed，现在也加入根对象
候选。required 字段不再只能靠空对象尝试；生产者与消费者的 Schema 校验仍逐个执行，
非法 seed 不能成为分支可达证据。没有改 Schema、比较运算、控制祖先检查、Prompt、
Adapter 授权或唯一修复预算，也没有增加猜测连边逻辑。

| 检查 | 结果与边界 |
| --- | --- |
| 修复前反例 | 修正测试草稿自身的枚举/输出声明错误后，9 failed / 25 passed；九项失败均为缺失合法非空对象 witness |
| 同一专项修复后 | 34 passed；覆盖 required、嵌套、nullable、union、Schema 交集、非法 seed 与 Multi Route |
| 实际契约集成 | 从 Query Adapter/资源快照派生包含系统必填字段的输出 Schema；判空后序列化与 Agent 图编译/反编译/再编译稳定 |
| 独立可达性证伪 | 缺控制祖先的消费仍被拒绝，空 Patch 不改变结果；只有显式修改控制关系才通过，原 Intent 不被原地修改 |
| 相邻回归 | 1,063 passed / 0 failed，238.46 秒；包含上述 34 项，四条既有 FastAPI 弃用警告 |
| 外部效果 | 零新增模型请求、零活表写入；不重启预览器或共享栈，不批准/发布/提交/推送/创建 PR |

修复前、修复后及回归 JUnit 均保留在忽略目录，SHA-256 分别为：

- `root-object-witness-repro.xml`: `3966ff4d7705d445ccdeb399761d44cb5b7c0ccc0fb7f1ab01ed7af75bca037e`
- `root-object-witness-after.xml`: `312aa47c9a81b06c8dccc6e34ddf3adda8e44c030fca28e621f22eef8d403956`
- `root-object-witness-regression.xml`: `e865c4dbdd0415f93f03e5ab7d2270ba4afed5365f0c0e50e71d4ceb7154a9a7`

真实请求次数账本仍为前节的 `020e02a2...3b46d9d78`，完整 hash 已再次核对一致。
AST、Diff 与本批四文件安全扫描通过，暂存保持为空；未改前端，未执行新的前端构建。

**当前结论仅为这一类工程缺陷已修复并通过相邻离线回归，不是整轮验收通过。**
原始失败提案的完整控制边未保存，仍不足以判断第二条数据不可达诊断对应何种具体布局；
合成断边对照不能冒充原 payload 重放。旧全量对应修复前源码，最终全量必须刷新。
预览器未加载此次修复，真实泛化生成、低分更新/高分不写/缺失终止三路径、帮助中心
与最终人工验收继续保留。剩余执行额度不用于新生成，也不提前进入第 8 轮。

局部回退仅恢复 `_input_witnesses` 中 seed 的原作用范围并撤销对应新增测试；不回退
整轮其他改动，不修改历史失败或已验证 r3 回执，不撤销已完成业务效果。

### 相同请求真实复测：在 Patch 应用阶段停止（2026-09-19）

用户重新授权一次复测。只重启本任务独立后端以加载已验证的 witness 修复，不改生产代码、
模型、目标或授权范围。新独立次数账本只允许一项 generation claim、最多三次 completion，
没有评测授权项；旧五次调用账本及 Runtime 保留。经浏览器可见界面点击一次生成，结果如下。

| 项目 | 本次事实 |
| --- | --- |
| Proposal | `proposal_05af76b570ac457dabbddbd8660bb5a3`，pending/r1、未人工修改、validation=false |
| 固定请求 | 与前次 request checksum 同为 `eae0f5aeb6511264f54d13bd3ce96d85ddfddaeb00d7ac0c8483bbce0f5f7fe8` |
| Provider 与预算 | OpenRouter / `deepseek/deepseek-v4-flash-0731`；三次 HTTP 200、stop；无第四次派发 |
| Provider 回报用量 | 3,808 + 17,011 + 21,916 = **42,735 token**；未审计账单 |
| 初次编译 | 12 项问题；原始编译响应在三层均 Schema-invalid，受观察结构相同，没有结构传递差异证据 |
| 已观察的非法纯节点 | `update_values` 为零输入 `json_serialize`；`update_payload` 为两个输入 `json_serialize` |
| 唯一修复 | 33 个操作；第 0 步 `update_node(update_values)` 报 `requires exactly one value input`，`recompile_executed=false` |
| 业务副作用 | 合成活表仍为 0 条记录、0 项受控操作账本；未执行、批准或发布候选 |

**不能将问题数从 12 变为 1 当成修复成功率。** Patch 在首个操作停止，后续操作没有完成，
也没有重新编译整图。该观察足以说明当前卡在非法纯节点的修复应用阶段，但没有完整原始
操作正文，尚不足以断言只是模型能力问题或 Patch 内核缺陷。下一步应离线验证无效节点
能否通过受支持的原子修改恢复，再决定最小处理；不能直接增加提示词、模型调用或放宽门禁。

本次并未到达可验收的控制流分析结果，不能用旧判空错误未出现在最终摘要来证明真实路径
已通过。witness 修复的 34 项专项及 1,063 项回归证据仍有效，但不等于本次完整生成成功。
占位候选的缺模型报错同样不能替代真实 Graph 的诊断。

新调用账本 SHA-256 为 `490350bc1fa729d94e6e24354f14c44440bc9507c7577b89ba5c622df08e89b4`；
旧账本 hash 保持不变。当前生产文件与本次冻结清单逐项一致，后台加载的分析器 hash 为
`184e41114eb462a24b93de9887e74b89acf99beb5ca3c007c7aa848abd57d960`。
安全证据保存在忽略目录 `.tmp-cw10-retest-20260919/retest-proof.json`，不保存完整 Prompt、
响应正文、业务记录或凭据。只读校验脚本退出 0 表示成功核对这次失败和副作用边界，
不是生成通过。整轮 PR 门禁继续未通过，没有新生产补丁、额外调用、提交或共享栈操作。

### Patch 校验时机的因果对照（2026-09-19）

**本次定位结论：非法生成是触发条件，Patch 对中间状态提前执行完整节点形状校验是已证实的修复内核缺陷。**
这不是对真实 33 步 Patch 的完整重放，也不表示其余错误已解决。生产代码本次保持不变。

证据链如下：

1. 实际 Proposal 的安全结构确认：`update_values` 为零输入 `json_serialize`；Patch 第 0 步只提交 `config/op/ref`。第 0–4 步均为配置更新，第 5–10 步断开数据边，第 16 步才开始连接数据边。最终诊断停在第 0 步，未重新编译。
2. `UpdateNodeOperation` 本身不接受输入绑定；输入必须由独立数据边操作改变。`graph_patch.py` 在写入更新副本前调用 `adapter.validate_intent_node(updated_node)`，后者调用 `_validate_json_serialize_shape -> _require_single_input`，精确产生观察到的错误。
3. 同一函数允许 Add/Connect/Disconnect 保留暂时不完整的节点，并在所有操作、延迟删除完成后再次校验全部节点。只有 Update 提前要求完整形状，造成同一原子 Patch 的阶段不一致。
4. 最小反例只交换 Update 与 Connect/Disconnect 的位置：零输入和双输入两种起始形状，配置更新和标题更新两种操作，共四组对照；更新在先均复现首操作失败，修复输入在先均通过最终授权、IR 解析、编译、Workflow 和发布预检。原 Intent 始终未被原地改变。
5. 完全不依赖模型错误的反例也成立：两个均能完整编译的合法图，只改变序列化输入来源及格式/标题，产品自己的 Diff 生成 `disconnect_data -> update_node -> connect_data`，应用在第 1 步失败。只交换后两步，结果与目标图的 Graph/Workflow semantic checksum 一致。
6. 实际 `HeadlessAuthoringService.editor_diff -> preview` 服务链复现同样失败；重排对照返回 `can_apply=true`，Proposal 的全部持久化字段与 revision 保持不变，未调用 Apply、未创建 Xpert。
7. `MetaPlannerV2Service.generate` 使用三个模拟 completion 的端到端对照：相同任务、相同非法初图、相同两个修复操作；仅调换顺序，分别得到 `patch_apply/recompile_executed=false` 与重新编译成功的 valid Proposal。没有 mock 掉 Patch、Adapter 或编译校验。

| 诊断维度 | 结果 |
| --- | --- |
| 零/双输入与配置/标题交叉 | 4 组均确认提前校验及顺序因果 |
| 合法到合法的产品 Diff | 2 组复现；完整 Headless 服务另 1 组确认 |
| Add 与 Update 不对称 | 新增后可接线，但接线前仅改说明也触发提前形状校验 |
| 最终门禁负例 | 缺失输入、未知 ref/端口、非法配置、原生变量/版本夹带、最终类型错连仍拒绝 |
| 模拟生成闭环 | 2 组均恰好 3 次模拟响应；失败/成功只由操作顺序差异决定 |
| 最终离线组合 | 18 项诊断 + 78 项已有测试 = 96 项，44.26 秒，0 failed，4 条已有弃用警告 |

这里的“诊断通过”表示**缺陷被稳定重现**，不是产品已修复；其中已有绿测也不能排除此缺陷。
测试草稿先因把编译 dict 传给领域对象校验、把领域 dataclass 当作 Pydantic 使用而失败，
仅纠正诊断代码的接口调用后重跑原断言，未修改生产行为。完整历史 JUnit 保留，不混为产品失败。

历史核对：提交 `25a2350f` 已存在同一 Update 提前校验及批末校验；当前相对 HEAD 的
`graph_patch.py` 修改仅为 `GraphPatchProgress`。因此本次证据支持“既有纯节点/Patch 契约
整合盲区被新场景暴露”，不支持“本轮写节点补丁制造该行缺陷”，更不支持单凭本次失败
断定 DeepSeek 已到能力上限、必须提前 V4 或引入多 Agent。

**最小修复建议，尚未实施：**只在公共 Patch 内核厘清校验阶段。每步继续校验合法操作、
ref、授权任务、配置 Schema、命名端口和可执行的局部前置条件；依赖全量连线的节点形状
校验统一在原有批末执行，随后完整授权、Schema、控制流、资源、编译和发布门禁不变。
不通过自动重排所有操作解决，因为 ref 创建/删除、配置变化和重接线具有真实顺序依赖；
也不以 JSON 专用豁免、猜测补边、提示词堆叠或多一次付费修复掩盖公共内核问题。

修复验收应将上述缺陷复现改为正向回归，覆盖非法起始图的模型修复和合法起始图的编辑器
变更，并继续验证非法最终图不放行、失败无持久化、原有三次调用上限不变。完整原始
33 步操作未保存，不能保证后续真实复测一定通过；其余图语义错误必须保留独立诊断。

证据位于忽略目录 `.tmp-cw10-retest-20260919/`：诊断测试 SHA-256 为
`4441c59e2e3e1088ed4c0c57b2a570f36ce4c75add9fb314200ac8fd3d525149`，
最终组合 JUnit SHA-256 为
`0ec511dfc806c528ae7aafb3c2e4a67ffccdc75763919526df71baef5dbbd55d`。
冻结源码逐项一致，新旧真实调用账本 hash 不变；本次无真实模型调用、活表写入、
预览器/共享栈重启、审批/发布或 Git 提交。整轮 PR 门禁仍未通过。

### 已授权的 Patch 校验时机修复（2026-09-19）

用户在上一节定位完成后授权修复。本批只有 `graph_patch.py` 一个生产文件变化：
Update 不再对临时节点调用完整形状校验，而由原有批末循环执行；不增加配置豁免，
不改变操作顺序，不替模型猜测输入，不修改任何 Adapter、Prompt、Schema、授权或预算。
新增 ref、端口、配置和任务权限的逐步检查，以及后续完整 Graph/Workflow 门禁均未移除。

| 验证 | 结果与证据边界 |
| --- | --- |
| 正式反例修复前 | 17 failed / 9 passed；包括非法起始图、合法编辑图和遮蔽后续错误的反例 |
| 同一正式反例修复后 | 26 passed；既验证完整批次通过，也验证最终非法图继续拒绝 |
| 共享 Adapter 边界 | Serialize、Deserialize、Aggregate 均可先更新再补齐输入，无 JSON 特例 |
| 生成服务 | 两种顺序均恰好 3 次模拟 completion；不完整修复仍失败且没有第 4 次调用 |
| Headless 服务 | 原始 Diff 无需重排即可 Preview/Apply；Preview 不持久化，Apply 仅将 pending Proposal r1 改为 r2，不创建 Xpert |
| 拒绝与原子性 | 非法配置/原生字段/Handle/checksum、未知引用/端口、任务越权、最终基数和类型错误均拒绝，原 Intent/Proposal 不变 |
| 相邻回归 | 44 个文件，1,089 passed / 0 failed，265.14 秒，包含上述 26 项，4 条既有警告 |
| 语法与前端 | 1,001 个 Python 文件 AST 通过；TypeScript + Vite 生产构建通过，产物隔离于忽略目录 |

测试初稿对 Headless 类型错误采用了错误的返回值预期；按当前接口改为抛错断言后，
先重新取得上述修复前失败，再改生产代码并原样重跑。没有删除拒绝断言或修改接口来通过。
原有 18 项“确认缺陷存在”的诊断保留为历史证据；它们不是修复后的正向回归，不能混用。

本批正式测试为 `server/tests/test_meta_planner_patch_atomic_validation.py`，SHA-256 为
`031f5a84e337a111a63d5c1d7e1a1c66e08ef978dd31abab190066774310e8a9`。
修复前、修复后、相邻回归的 JUnit SHA-256 依次为：

- `e2cb15f1066bbf0261b95701d703f51230570093f3120a4664f1a1dab802f997`
- `55b7de2e48a17eec065292c85a9aca5afebb416613934907c4af4adaed70ea0c`
- `b9a7f611d5df95e9f3e1c8d5fb017c32236b9d5dadc2b1a1d96e0e47722f188f`

生产文件修复后 SHA-256 为
`0627eb3a952a45b681eb2b57109b381249c72fee8b4f072da95c8b133ac09e00`。
前端构建使用专用 `outDir`，没有覆盖运行中预览器的静态文件或重启服务。
Vite 大包体积提示和独立输出目录不自动清空提示均如实保留，不以配置调整制造无警告结果。

**局部修复验证通过，不代表整轮提交门禁通过。** 本次没有真实 Provider 请求、活表写入、
共享栈操作、审批/发布、提交或 PR；全量后端/前端单测及真实泛化三路径没有在本批重跑。
完整真实 Patch 未保存，后续复测仍可能揭示独立图语义错误，不能承诺只移除此调用就
能保证真实候选通过。回退仅恢复这次提前调用及对应新增测试，不回退其他整轮实现或数据。

开工 101 路径 hash 的收尾比对仅命中允许的四个既有文件，另新增一份正式测试，共五个
路径；其余既有差异未动。Diff 检查通过、凭据形状扫描零命中、暂存为空；新旧真实次数
账本 hash 均未变，JUnit 与隔离构建产物均在 Git 忽略范围内。

### Patch 修复后的真实复测：仍未通过（2026-09-19）

本次仅使用用户续批的一次生成、最多三次 completion，通过 `15459` 真实页面触发。
目标、模型、温度、节点范围和零记录合成表的 `update/status/1` 授权未变。
后端 `16459` 已加载上述修复源码；前端、历史 Runtime、两份已消费账本均保留。
新的出站护栏账本独立保存，禁止评测、额外生成及自动重试；六项离线护栏测试通过。

| 核验项 | 本次事实 |
| --- | --- |
| 候选 | `proposal_a9f6998dec084a4eab36c7b9f8cd5d11`，pending/r1，未人工编辑，校验失败 |
| 实际调用 | OpenRouter / DeepSeek V4 Flash 0731，3 次 HTTP 200，均以 stop 完整结束 |
| Provider usage | 4,082 + 16,867 + 19,719 = 40,668 token；不是账单审计 |
| 初次 Graph | 授权阶段两项阻断：动态谓词期望 string / 声明 any，以及一项控制契约错误 |
| 唯一 Patch | 原始 5 步，既有去重规范化后 4 步；到达批末 validation，而非第 0 步提前拒绝 |
| 最终阻断 | `update_status` 缺少条件所要求的 `predicate_record_id`、`predicate_revision` 输入；没有重新编译 |
| 外发结构 | Plan/Patch 三层 Schema 有效；Graph 三层无效；Graph 的 Provider/collector 结构相同，collector/validator 结构不同 |
| 业务效果 | 没有执行候选或评测，合成活表仍为 0 记录 / 0 写账本 |

本次与前次首先阻断的阶段不同。批末形状校验保留了 fail-closed 边界，不能据此删除
谓词门禁或替模型补造 record ID/revision。当前证据只能确认条件契约与最终连线不一致；
需要继续核对配置更新、断边以及既有输入规范化的对应关系，才能区分模型修复不完整
与内核语义问题。没有完整原始 payload，不能把安全形状摘要冒充完整重放，也不能仅凭
collector/validator 的结构差异宣称内容被错误改写。失败占位图的模型缺失不是主因证据。

本次生产源码与启动回执完全一致，请求 checksum 与前两次一致。新账本 SHA-256 为
`208f0ba351461003a90a64aef3e6980bd64f9f6e2d76b973be6f10028bcf5f38`，
启动源码回执 SHA-256 为 `9a480bc7558af947a7dfe6a866f3747cc109e0b8da46c2d7175950ad3a9b91f2`。
忽略目录 `.tmp-cw10-patch-retest-20260919/verify.py` 的退出 0 仅表示上述证据一致，
**不是泛化验收通过**。没有新增生产修复、工作流执行、审批/发布、提交/PR 或共享栈操作。
最新全量、泛化三路径实际效果、帮助中心和最终人工验收仍未完成；整轮 PR 门禁未通过。

### 单模型修复 Harness 后复测：提前止于任务计划（2026-09-19）

本次加载已完成 1,112 项相邻离线回归的四文件修复，保持原目标、模型、温度、节点范围及
`update/status/最多 1 行` 授权，通过 `15459` 页面只触发一次生成。此前启动助手误选缺少
`httpx` 的基础 Python，未产生模型派发；使用既有测试虚拟环境后正常启动，无依赖安装。

| 核验项 | 本次事实与边界 |
| --- | --- |
| 请求一致性 | 请求 checksum 仍为 `eae0f5aeb6511264f54d13bd3ce96d85ddfddaeb00d7ac0c8483bbce0f5f7fe8` |
| 真实调用 | OpenRouter / `deepseek/deepseek-v4-flash-0731`；2 次完整 HTTP 200、stop，无第三次派发 |
| Provider usage | 4,415 + 5,695 = 10,110 token；未审计账单，不将其当作费用 |
| 应用结果 | 生成请求返回 422；唯一计划修复后仍不符合“恰好一个终端任务”，实际终端数为 2 |
| 未到达阶段 | Graph 编译、Graph Patch 修复与逐操作对账均未执行，不能评价本次 Harness 修改效果 |
| Proposal | 未创建新提案；界面保留的是上一次失败的 `proposal_a9f6998dec084a4eab36c7b9f8cd5d11` |
| 数据与源码 | 合成活表 0 记录 / 0 操作；三份历史账本及 86 份冻结源码一致，Proposal Store 未在本次更新 |

这次是**任务计划契约失败**，不是已经修复的 Patch 中间状态错误，也不是尚未执行的
配置替换/端口对齐问题。不能再根据旧占位候选、没有出现旧诊断或 HTTP 200 推断图已通过。
两个模型请求均完整返回；业务 API 随后明确拒绝，并无传输不确定性或继续生成的证据。

本次发现的提示表达缺口有明确代码依据：`TASK_PLAN_SYSTEM_PROMPT` 及
`_task_planning_contract` 规定有界任务 DAG 和 Agent 职责，却没有直接说明
`validate_task_plan` 强制的唯一终端任务要求，以及多个互斥成功分支可以覆盖同一终端任务。
计划修复输入包含校验错误，但缺少该层次区别的显式说明。后续核对应限定到这一任务规划
契约及早期失败证据，不放宽校验、不硬串联互斥分支、不修改下游 Patch、不追加调用轮次。
这仍是可验证的缺口而非单一根因定论；不据此断言换模型或提前多 Agent 能解决问题。

**证据限制：**原始计划响应没有完整保存，因此只知道初稿未通过及修复后的终端数为 2，
不能恢复初稿具体任务划分、初始错误或模型为何忽略修复诊断。任务阶段抛错先于 Proposal
和安全生成证据的持久化；护栏的 claim 结束字段只在成功返回提案时填写，故本次没有
`ended_at`。这不应被解释成未知扣费，两条调用各自已有完成时间、响应 hash 与 usage，
服务日志有本次唯一 POST 的 422；claim 已消费，不得利用未用完的一次预算重开生成。

只读核验脚本首次受沙箱 SQLite 文件访问限制；同一 `mode=ro` 脚本在获准环境重跑通过，
未修改访问模式或记录。安全摘要在忽略目录
`.tmp-cw10-harness-retest-20260919/retest-proof.json`，调用账本 SHA-256 为
`bc912d5bbdb65b07bda1aaf1b539e8c2cc7d6babf3028cdbd6e567d92968432d`，源码回执 SHA-256 为
`279ff81ba8dc2fc11267913cecc3f3475ad224f77d3240387b00ec9c95fad79d`。

本次未改变生产源码、执行工作流/评测或业务写入，也未审批、发布、提交、推送、创建 PR
或操作共享栈。两次完整响应不是成功生成，离线回归也不是新一轮全量门禁；
**泛化候选及三路径效果仍未通过，整轮提交门禁保持未通过。**

## 任务规划契约核对与最小修复（2026-09-19）

### 补充证据与归因边界

前节关于“不能恢复初始错误”的结论需要依据新增证据修正。虽然没有新 Proposal，也没有
原始计划持久化，但既有失败 API 和进程内 RunRegistry 均保留安全生成证据。运行
`30b5722e-e422-493e-a5d8-d0b41fe7323c` 的 `task_plan` 与 `task_plan_v1` 两次请求
checksum 不同，响应在 Provider、collector、validator 六处的规范化内容 checksum 却
完全相同：`83a9a1b729a97f0f1994b8e56c6f791f1fbe7a42ac9a918181a9d26419011567`。
两稿均为两个任务、JSON Schema 有效。因此可以确认唯一修复未改变计划内容，两稿的
语义校验结果一致；仍无法恢复任务正文或解释模型为何重复，不将提示缺口宣称为所有
历史失败的唯一根因。RunRegistry 是进程内状态，不宣称有持久化原始响应或重启取证能力。

只读阶段临时诊断 **18 passed**，现有四文件回归 **90 passed**。其中已证明单个终端
职责可以编译为两个互斥成功 Agent，还可包含显式错误终止；硬串联互斥职责或添加虚假
汇总任务不能通过后续校验。临时证据在 `.tmp-cw10-harness-retest-20260919/`，不是
真实生成成功率证据。

### 实施范围

- 只修改一个生产文件 `server/meta_agent/meta_planner_v2.py`。初稿、唯一修复共用
  `task_graph_contract`，明确一个终端职责、互斥 Agent 共同覆盖任务、保留真实独立职责，
  禁止伪造依赖或无人承担的汇总任务。两条短例与质检目标无硬编码关联。
- 从原校验函数提取同一依赖分析，供校验和 `task_graph_diagnostics` 共用；原有错误
  字符串、顺序、权限判断和拒绝行为保持不变。诊断放在无效正文前，包含终端任务、
  既知依赖与错误码；未知依赖仅在该新投影中计数，不复制任意长文本。解析失败不猜测。
- 单独修正一处提示歧义：禁止的是“为了过门禁强行合并独立职责”，并非禁止合法的
  单任务计划。诊断是只读反馈，不是允许模型新增的输出字段；原生成 JSON Schema 不变。
- 不改 Graph Patch、NodeContract、Capability、资源授权、写入身份、Runner 或 Store；
  不增加模型次数，不自动合并任务、不自动排序分支，不创建兜底候选。

### 验证与剩余门禁

- 新增正式 `test_meta_planner_task_graph_contract.py` 共 21 项。修改前为
  **17 failed / 4 passed**，全部失败对应缺失的共用契约/诊断；修改后原样运行
  **21 passed**。覆盖共享契约、合法/非法 DAG、受限诊断、无法解析时不猜测、互斥分支
  往返、虚假依赖/终端拒绝、两次无效计划即停止及三次预算内创建 pending Proposal。
- 冻结旧校验器后，对 1–4 个节点的全部无自环有向图及六种 Agent 数量/授权组合比较
  完整错误列表，共 **24,990 组完全一致**；另测诊断前置及上下文大小边界。这里的
  24,990 是比较次数，不计作 pytest 用例数，不声称穷尽所有输入。
- 首次相邻回归 **1,133 passed**；最后一句提示消歧后同组及两项临时对照原样重跑，
  最终 **1,135 passed / 0 failed / 194.67 秒**，保留四条既有 FastAPI 生命周期弃用
  warning。其中包含前述 21 项，不重复累计；没有删减原回归断言。
- 最终两个 Python 文件语法编译通过；`npm.cmd run build` 通过，保留既有大 chunk
  警告。未重跑全量 `server/tests/`，这些检查不替代本轮最终全量或真实验收。
- 对照前次 86 个生产文件指纹，仅本批 Planner 文件变化；四份真实调用账本 hash
  不变。当前生产文件 SHA-256：
  `bfa5d4719891a1dbaf9b1bf26c1950e987de1b221de252bb897f46f5426e461a`。

本批未调用模型、访问或写入活表、执行工作流/评测、重启预览器/共享栈、批准/发布或
提交 PR。预览器没有本次修复的真实验收证据；后续复测需加载新代码并取得新的明确授权。
最终泛化生成、三路径效果、最新全量、帮助中心及人工验收继续待完成。

最终 JUnit 为忽略目录 `task-graph-contract-final.xml`，SHA-256：
`444c1001c7866e285dd12f0473b3c7f04d9edc3d9d9fadc56c565d7c44c0510d`。
回退只撤销本批共用契约和依赖诊断投影；保留原校验、历史账本及其他 CW10 改动。

## 任务契约修复后真实复测（2026-09-19）

用户授权的一次同范围 UI 生成已完成，但 **候选仍不合格，提交门禁未通过**。
源码固定为上述 `bfa5d471…`；先通过 6 项护栏测试，再仅重启本任务独立后端。
目标、模型、逐表授权及请求 checksum 与上次相同，没有修改生产代码或扩大范围。

| 层次 | 本次直接证据 | 可作出的结论 |
| --- | --- | --- |
| 外部派发 | 3 次完整 200 / stop，无不确定派发；38,268 Provider-reported token | 已消费本次生成额度，不是账单审计或质量证明 |
| 任务规划 | 1 个任务，0 项错误；Provider/collector/validator 内容 checksum 一致 | 本例首稿通过，未用任务修复；不能外推稳定性 |
| 初次图生成 | 9 个语义节点，JSON Schema 通过；语义校验 4 项问题 | 格式合法不等于可编译或可执行 |
| 唯一 Patch | 7 个操作成功应用并重新校验；仍有 2 项诊断 | 失败不在 Patch 解析/派发层，尚需离线核对图语义 |
| 保留结果 | `proposal_287c1026987545ccb1293e1899f3c533`，pending/r1/invalid | 不可批准；不是已通过的分支候选 |
| 安全边界 | 86 个源码指纹及 4 份旧账本一致，合成活表 0 记录/0 操作 | 未执行工作流、评测、业务写入、审批或发布 |

当前两项未通过诊断为：

1. `check_score` 的 `matched/unmatched` 被报告为 shadowed or unproven。
2. `update_values` 消费的 `query_json` 在其控制路径上不可达。

目前只确认诊断及所在阶段，尚未证明其完整根因。下一批应以离线最小图分别核对
分支 witness/可空记录约束，以及数据生产者与控制先后，不能再次用真实模型试错
代替复现，也不能为通过而放宽契约或自动重写业务图。

页面中的单 Agent 线性图来自现有 `_fallback_candidate`，不是这次模型分支图的
成功产物；缺少 `modelId` 是该保底路径刻意阻止批准，不能归因为模型配置丢失。
Runtime `276758ff-b3c8-4d90-918f-884a1a925fa9` 的 completed 仅表示 API 已完成，
与 `validation.valid=false` 不矛盾。

安全证据位于忽略目录 `.tmp-cw10-task-plan-retest-20260919/retest-proof.json`；
账本 SHA-256 为 `cbbed5759ec99300bf6542c5be5a90548421b469c595e8aa7b1f4b3487efecdf`。
本次没有追加生成或代码修复，没有执行最新全量及分支效果验收，未提交/推送/PR。
