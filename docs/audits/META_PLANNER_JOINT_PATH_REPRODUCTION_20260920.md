# CW10 联合路径失败离线复现

## 结论与范围

本次复现已完成，尚未修复生产实现，也未重新进行真实模型调用。

针对 Run `7d4d9a5e-e223-4963-a399-2bafd6eee925` / Proposal `proposal_33709f32c5f147b9aa071d206b800641`，用离线结构夹具复现了实测修复后的全部五条错误，包括相同的终点冲突场景编号。相同授权能力下的合法非线性对照图可以完成编译、反编译和再编译。

这不是完整模型输出重放。原始 Graph/Prompt 未保存；本次保留了这个证据限制，没有把诊断占位候选当作模型原图，也没有从相同报错反推唯一的原始控制边。

当前有证据支持的判断是：**首稿包含联合语义错误，模型可选配置与实际可用能力未充分对齐，唯一修复上下文又遗漏了可独立发现的错误。** 仅补局部输入或控制边不足以闭环。不能据此宣判单模型已达上限，也不能把问题全部归咎于 DeepSeek；同样不能宣称继续堆提示词即可稳定。

本次只新增本文及被 Git 忽略的离线证据，不修改生产文件、不调用 Provider、不执行 Workflow/Evaluator、不读取或写入业务记录、不重启预览器、不批准 Proposal、不提交 PR。

## 证据固定

- 工作树：`C:/tmp/modelmirror-meta-planner-controlled-writes-10-closeout`。
- 分支：`codex/meta-planner-controlled-writes-10-closeout`。
- HEAD：`a7d99925584e818e7f2df84b1646256abfc08ed6`，保留原有 130 项未提交改动。
- 核验上轮冻结的 108 个源码/测试/文档文件和 9 份历史调用账本均未变化。
- 最近一次账本 SHA-256 仍为 `4ee134fe0578ee157c8827e6628bfa3afd2b41dc3051c8130c21b940ff5a2cc8`。
- 仅读取指定失败 Proposal 的既有 `router_inputs`、端口关联诊断等安全投影，固定到新证据目录；读取前后 Proposal Store hash 相同。
- 网络、HTTP 请求与 SQLite 业务记录访问均由离线测试守卫禁止；测试 Store 指向独立临时目录。

## 真实结果中的确定事实

| 事实 | 证据 | 可以作出的结论 |
| --- | --- | --- |
| 首稿共 8 节点，仅 1 个 condition，无 multi_route | Provider、collector、validator 三份结构投影一致 | 没有独立的空结果保护节点 |
| `check_score` 为 `lt`，比较 `score`；输入是 nullable object，字段本身是 required integer | 两阶段 `router_inputs` 的权威类型投影 | 此处是对象可能为 null，不是 score 字段可空或数值类型不匹配 |
| `update_values` 是无输入的 json_serialize，输出为 `json` | 三份结构投影及输入计数诊断 | 首稿把一个缺输入的序列化节点用于写值路径 |
| Update 的 values 引用 `update_values.value`，而源节点只有 json 输出 | 三份结构投影；修复前后 `source_node_count=1`、`source_output_count=0` | 错误出口真实存在且修复后未消除，不是来源节点完全不存在 |
| 唯一 Patch 给 update_values 接入 user_input，config checksum 不变 | Patch 收据和修复后 binding 投影 | 修复只让序列化节点能读取运行文本，并没有得到经 Schema 验证的业务字段对象 |
| 唯一 Agent 同时绑定查询文本与写入回执文本 | 节点 7 的数据输入投影 | 高分不写路径不能保证产生低分写入回执；不能靠多接一条边满足该输入义务 |
| Patch 只有 update_node、connect_data、两次 connect_control，无增删节点、无断边 | 严格 Patch 投影 | 本次不属于“完全没改控制边”，但也没有补出独立空值保护或改变终点数据依赖结构 |

“对象可能为 null”是本次补取权威类型投影后的确认结果；上轮仅凭 `CONDITION_FIELD_REQUIRES_OBJECT` 不足以区分 null、array 等非对象输入，本次没有沿用那个不充分推断。

## 离线复现与反证

### 联合失败

构造与已知节点类别、来源错误及业务边界一致的八节点结构，使用合成 Schema，不访问实际表。得到的错误字符串为：

1. `Router check_score has unproven outcomes: 可达输入触发 CONDITION_FIELD_REQUIRES_OBJECT，缺少有效的路径保护。`
2. `Scenario 4 reaches 2 terminals (success=['explain_agent'], error=['terminate_not_found']).`
3. `Data source query_batch.result is not available in every scenario and is not guaranteed for update_status.records.`
4. `Data source query_batch.result is not available in every scenario and is not guaranteed for serialize_query.value.`
5. `Variable query_result is not reachable at node serialize_query.`

复现机制：查询错误路径没有 result，但不受它约束的控制根仍可到达写节点、序列化节点及成功终点；可空查询成功结果直接进入字段比较。对普通合流，现有分析器按至少一个已到达控制入口判断执行，不是新增一条控制边就能将其他入口变成必要条件。见 `server/meta_agent/control_flow.py:518`。

这是一个能产生相同错误的结构反例，不证明原始模型控制边与该夹具逐条相同。实际原图中不安全入口的精确位置仍未完整留存。

### 合法对照

同样只开放本次八类能力，最多两个 Agent，Query 与 update/status/最多一行授权不扩大：

- 查询成功且结果为 null：固定错误终点，不写、不调用 Agent。
- 查询成功且有记录、score < 60：固定对象写值，使用直接可信 Query 记录，序列化查询与更新回执，由低分 Agent 汇总。
- 查询成功且 score >= 60：不写，由高分 Agent 只消费查询证据。
- 查询执行错误：进入安全错误终点，不把它当作“查询成功但无记录”。

四个符号场景均恰好一个终点；编译往返 checksum 稳定。改变 ref 名称、节点展示顺序和边展示顺序后仍能通过。空值非法路径也由现有 `evaluate_typed_condition` 确认会触发相同错误，非仅根据采样名称推测。

因此本次目标能够由现有 IR、节点和权限表达。此结论只排除了该目标的表达性阻塞，不是实际模型生成质量或业务执行验收证据。

## 已定位的工程缺口

### 1. 当前授权下不可实现的写值模式仍默认展示

`DataTableInsertPlannerConfig` 及 Update 继承配置默认 `value_source=input`（`server/workflow_native/node_contracts.py:704`）。生成/修复 Schema 从 Adapter 安装完整配置，不按本次可用生产者裁剪该模式（`server/meta_agent/generation_contract.py:62`）。

但当前执行契约对所有 input 写值都要求直接来自 `json_deserialize`（`server/meta_agent/write_contract.py:89`），并非只有 Agent 生成数据时才检查。本次授权没有该节点，因此合法路径只能显式选择固定对象 literal。离线验证了显式 input 和省略 value_source 两种配置都被模型配置 Schema 接受，后者解析为 input。

这不会绕过最终安全门禁，但把当前范围内没有合法实现的选择交给了模型，且是默认选择。不能通过自动增授 JSON Deserialize、修改运行时限制或静默切换已有候选的业务值来修复。

### 2. 可独立发现的来源出口错误被阶段遮蔽

前置校验按变量名与控制祖先查可达性；类型诊断遇到找不到来源 Schema 时跳过（`server/meta_agent/meta_planner_v2.py:2324`）。原图的 `update_values.value` 正好没有来源 Schema。

在控制路径全部修正的隔离反例中，前置 authorization 返回零问题、零类型问题，但随后 resolver 明确拒绝 `unknown source port`。进一步把出口改成 json 后，又能确认 string 不能成为对象写值。单独消除路径错误仍不是完整修复。

同时，安全遥测本已记录 `source_output_count=0`，修复上下文精简时移除了整段 `data_bindings`（`server/meta_agent/repair_context.py:90`），却没有对应的结构化错误接替。原始图仍在 Prompt 中，故不能声称模型完全看不到该事实；准确说法是**确定性已知的独立错误没有成为明确修复义务**。

### 3. 联合路径错误没有进入同一个结构化审查范围

当前 dependency worklist 能展示数据反例，但路由输入类型错误与双终点错误主要仍是全局文本/索引。在复现中 worklist 只有 query、两个数据相关节点及写节点，不包含 `check_score` 与 `terminate_not_found`；原始图与错误文字仍保留。

仅补一个 serializer 输入后，五项联合错误仍然存在。添加不正确的控制边可能保持旁路，或者先触发重复 outcome，使后面的路径证明被阻断；错误数量减少不等于路径改善。

现有完整校验会阻断这些图，未发现本次错误可以绕过批准或执行安全门禁。缺口集中于**一次修复预算内的完整诊断和模型编排支持**，不应以放宽终点、来源或类型规则解决。

## 最小处理方向，尚未实施

1. **能力可行性投影**：从同一写值契约推导当前授权下可用模式及来源要求。没有授权的 JSON Deserialize 时，生成/修复契约不能把 input 模式当作可实现默认项；保留 literal，并让模型根据明确业务目标显式选择。不自动改图、增权限或放宽 Runtime。
2. **完整修复义务**：在不信任无效/未授权资源类型的前提下，独立报告来源 ref/port/variable 完整性与写值来源限制。来源端口缺失不需要业务记录或私有 Schema即可证明；用共享检查避免前置诊断与 resolver 再次漂移。精简 Prompt 前验证每条非冗余事实有等价诊断保留。
3. **路径级 harness**：同一修复上下文聚合路由输入可空性、反例场景、相关控制入口、错误/成功终点与下游必需值。优先让模型处理“无记录、低分修改、高分保持”路径义务及受影响子图，再输出同一原子 Patch，而不是只处理第一个缺失端口。明确区分查询 error 与成功空结果；不新增模型调用、隐藏重试、第二套规划 IR 或自动连边。

以上是需要确认后的最小修复范围，不是声称已找到能保证真实模型成功的提示词。正式修复后应先跑保持业务语义的正反例、来源伪造和权限测试，再取得授权进行同一冻结目标实测；不能把本次手写合法图充当真实模型验收。

## 验证记录

最终命令，工作目录为上述独立工作树：

```powershell
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -B .tmp-cw10-closeout/run_offline_v2.py `
  .tmp-cw10-joint-repro-20260920/test_joint_repro.py `
  server/tests/test_meta_planner_control_dependency_repair.py `
  server/tests/test_meta_planner_path_proof.py `
  server/tests/test_meta_planner_control_domains.py `
  server/tests/test_meta_planner_repair_context.py `
  -s --junitxml=.tmp-cw10-joint-repro-20260920/final-focused.xml
```

结果：101 passed，4 个已有 FastAPI 生命周期弃用 warning，22.52 秒。其中新复现 19 项、控制依赖 18 项、路径证明 32 项、类型域 14 项、修复上下文 18 项。包含预期拒绝断言，不代表 101 个模型候选成功。

首轮复现曾因新夹具错误使用 data_type 而非安全目录的 type 导致 13 fail / 1 pass；修正仅限夹具。第二轮 13 pass / 1 fail：错误加边先触发重复 outcome，证伪了“仍能进入后续路径分析”的测试假设；已单列该阶段遮蔽反例，并保留旧 XML。第三轮 17 项通过，补取既有安全投影并增加两条断言后执行最终 101 项。未修改生产代码以使测试通过。

本次未跑全量后端、前端构建或真实额度验收。既有全量入口失败及未运行项仍然有效，PR 门禁仍未通过。

| 证据 | SHA-256 |
| --- | --- |
| `.tmp-cw10-joint-repro-20260920/test_joint_repro.py` | `9a5fd3b7b8a15cf320a25d6ca43506d0217c9878a585c58d7ffcbf37491cfc8a` |
| `.tmp-cw10-joint-repro-20260920/safe-projection.json` | `16e0f80fe800b254857081268000539f788ca61c76920d0114d96da51f25c63f` |
| `.tmp-cw10-joint-repro-20260920/final-focused.xml` | `667e9b40556113659da1ca85f9ac3212c9e0569a20d478b131c63c319d89d962` |

证据目录已确认被 Git 忽略。未修改历史账本、旧复测报告或 Runtime 数据；无 Commit、Push、PR、发布或合并。本次没有生产改动，因而没有生产回滚动作。
