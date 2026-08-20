# ModelMirror Benchmark

最后更新日期：2026-08-09

## 1. 定位

Benchmark 产品层为 Xpert Evaluator、Knowledge Evaluation 和后续 Agent Workspace
适配提供统一目录与来源元数据。它不创建新的正式数据集 Store：

- Agent/Xpert 数据集继续由 `XpertEvaluationStore` 保存。
- RAG 数据集继续由 `KnowledgeEvaluationStore` 保存。
- 后续生成与校准任务只在 `BenchmarkJobStore` 保存任务状态，不复制正式数据集。

当前已交付 `EVOAGENTX-BENCHMARK-CATALOG-01`、
`EVOAGENTX-BENCHMARK-GENERATOR-02` 与 `XPERT-RAG-BENCHMARK-STANDARD-03`。
RAG 定向生成和 Agent Workspace 适配仍为后续独立轮次。

## 2. 统一 Manifest

每个目录 Pack 使用 `BenchmarkManifest`：

| 字段 | 含义 |
| --- | --- |
| `pack_id / version / kind` | 稳定 Pack 标识、不可变版本和评测类型。 |
| `locales` | Pack 覆盖语言。 |
| `coverage / difficulty` | 能力覆盖与难度摘要。 |
| `metric_policy` | 核心确定性指标及附加指标边界。 |
| `target_requirements` | 可运行目标与副作用要求。 |
| `source / license` | 数据来源和许可证声明。 |
| `case_count / document_count / checksum` | 用例、托管语料数量和规范化 SHA-256。 |

目录 Pack 不可编辑。checksum 基于规范化用例计算，启动时会验证用例 ID、评分契约和
指标白名单。

## 3. 内置 Agent Pack

| Pack | 用例数 | 核心指标 | 覆盖 |
| --- | ---: | --- | --- |
| `mm-agent-instruction-bilingual-v1` | 20 | exact / contains | 指令、格式、排序、转换、负向约束。 |
| `mm-agent-structured-json-bilingual-v1` | 16 | JSON Schema | 对象、数组、嵌套、布尔、空值和 Unicode。 |
| `mm-agent-multiturn-bilingual-v1` | 16 | exact | 上下文召回、更新优先、实体和干扰信息。 |
| `mm-agent-abstention-bilingual-v1` | 12 | JSON Schema | 缺失证据、冲突证据、单位不足和可验证弃答。 |

全部 64 条用例均为 ModelMirror 自有中英双语合成内容，不引入网络下载、外部 Provider
或第三方受版权保护语料。核心分数只使用 `exact_match`、`contains` 和
`json_schema`；LLM Judge 不参与目录核心门禁。

## 4. 实例化与版本

“添加到工作区”执行一个原子操作：

1. 校验 Pack manifest、case ID、确定性指标和 checksum。
2. 在 `XpertEvaluationStore` 创建可编辑 Dataset 草稿。
3. 自动发布与目录 Pack 完全一致的不可变 v1。
4. 返回 Dataset，后续编辑只改变草稿并形成新的显式版本。

Dataset 兼容新增：

- `origin`：旧数据默认 `manual`，目录实例为 `catalog`。
- `catalog_ref`：固定 Pack ID、版本和 checksum。
- `provenance`：安全来源、许可证和语言摘要。
- `coverage`：能力、难度和指标策略。
- `calibration`：目录实例以 checksum 完整性标记为 `calibrated`；用例编辑后转为
  `stale`。

这些字段也固定到 DatasetVersion。旧 JSON Store 无需离线迁移，读取时自动补安全默认值。

## 5. 针对性生成与校准

`EVOAGENTX-BENCHMARK-GENERATOR-02` 可以为以下固定目标生成待审核评测集：

- Xpert 草稿 revision。
- 已发布 XpertVersion。
- 固定 revision 的 Xpert Authoring Proposal。
- Prompt Profile 草稿 revision，并固定一个已发布 XpertVersion 作为宿主。

服务端只向生成模型提供受限 Agent Prompt、输出契约和安全能力摘要。会话样例必须由
用户显式选择；附件、长期记忆、私有工具输出、凭据和物理路径不会进入能力快照。
默认生成中英双语 12 条用例，允许 6–30 条；一次任务最多一次生成和一次 JSON 修复。
正式生成调用会向兼容网关请求 `response_format=json_object`；不支持该约束的模型应明确失败，
而不是把不可解析输出写入评测集。一次修复仍是上限，不会进行无界重试。

生成前会把固定目标编译为一组安全 `target_anchors`，例如 Agent `rolePrompt`、会话输入
契约、输出 Schema、允许工具、知识资源或 Prompt Command。每条生成用例必须保存：

- 服务端先确定性生成逐题 `case_blueprints`，固定 locale、主覆盖项、1–3 项能力矩阵、
  难度、真实锚点、专业焦点、压力类型和可观察要求；模型只负责专业题面、历史、解释字段和
  不依赖固定资源的文本 Gold。工具必选/禁用集合、知识文档名及 JSON Schema 由服务端注入。
- Toolset 覆盖只能使用固定 ToolsetVersion 中已启用的真实工具名；知识引用只能使用固定活动
  知识版本中的安全文档名；Prompt Command 样例必须真实以固定 `/alias` 发起。缺少这些
  可验证资源时，对应能力不会进入可选覆盖。
- 工具、知识与命令题还会在逐题蓝图中固定 `required_tool_name`、
  `forbidden_tool_names`、`required_document_name` 和 `required_prompt_alias`；结构输出题固定
  `required_json_schema`。模型不能以同类但错误的资源替代，也不需要回传这些服务端字段。

- `targeting.target_refs`：引用一个或多个真实锚点 ID，且锚点必须支持该用例覆盖项。
- `targeting.capability_matrix`：组合 1–3 个真实能力轴，主覆盖项必须在矩阵中。
- `targeting.focus_terms`：只能取自锚点抽取的专业术语，并必须直接出现在题目或历史消息中。
- `targeting.pressure_types`：记录冲突上下文、缺失证据、工具诱饵、Schema 边界等压力类型。
- `targeting.rationale`：说明该用例具体验证目标的哪项行为。
- `targeting.challenge`：说明边界或对抗压力点。
- `targeting.discriminator`：说明该目标与未配置的通用底模应产生何种可观察差异。
- `targeting.difficulty`：`basic / edge / adversarial`。
- `targeting.blueprint_id`：对应服务端逐题蓝图，用于核对模型没有自行改写覆盖设计。
- 服务端只允许规范化派生元数据：移除未授权的额外 matrix 值、从合法 matrix 恢复主覆盖项、补齐真实 anchor 引用，以及移除未在题面出现的多余 focus 声明。规范化不得改写题目、历史、Gold、Schema、工具期望或难度；所有动作写入 `targeting.normalization_notes` 并在管理 UI 展示。规范化后仍缺少专业焦点或合法覆盖时继续拒绝。

用例数不少于 6 时，边界和对抗样例各不少于 25%，基础样例不超过 30%，并必须覆盖每个
用户选择的能力项。选择两个以上能力时，至少 60% 用例必须组合多个能力，每个能力必须
出现在组合题中，并在可行时形成至少三种不同组合。通用常识、算术、翻译或任意格式题若
无法指向目标锚点，将被生成校验拒绝。管理 UI 逐例展示能力组合、专业词、压力类型、
区分证据、锚点安全摘要、Gold 契约和固定基线校准分数；不会展示完整 Prompt。

专业性不是依赖模型自述：服务端从固定 Prompt 和资源摘要中提取有限 `focus_terms`，模型
不得发明域外术语，且所声明术语必须可在实际输入中定位。若目标本身只有“通用助手”式
描述而没有专业焦点，预检会明确警告；此时生成器只能证明通用契约，不能宣称领域针对性。

蓝图还要求能力在题面和 Gold 中可观察：`structured_output` 必须提供 JSON Schema，
`multi_turn` 必须含至少两条历史且包含 assistant turn，`tool_routing` 必须提供工具调用
期望，`knowledge_citation` 必须引用固定文档名，`prompt_command` 必须使用固定命令别名。
边界题至少暴露一个结构化挑战信号，对抗题至少暴露两个；只在 metadata 中自报难度不会通过。
单一工具能力的对抗题会固定一个必选工具和一个题面可见的禁用诱饵工具，使两项压力都能由
`tool_call_match` 验证，而不是按能力类别数量推断难度。

覆盖矩阵根据目标真实能力选择：指令遵循、结构化输出、多轮上下文、工具路由、知识引用
和 Prompt Command。工具路由增加确定性 `tool_call_match` 指标，只比较工具名、必需/
禁止集合与稳定调用顺序，不保存参数或工具结果。

生成后自动用固定目标执行一次受限校准。校准只报告评分契约可执行性、基线分数、过易、
过难、重复和 Gold 泄漏，不会根据当前回答修改 Gold：

- `calibrated`：可发布。
- `warning`：人工确认警告后才可发布。
- `pending / failed / stale`：禁止发布。
- 用例编辑或目标 checksum 漂移后必须重新校准。

“至少 80% 用例过易”是有效质量警告，不会被隐藏或自动改写 Gold。用户应根据逐例目标
证据和分数编辑或重新生成样例；旧生成任务缺少 `targeting` 时会明确标记为不可验证针对性。
即使校准基线高分，复合能力比例、专业词命中和区分证据仍作为独立的生成门禁与人工证据，
不会被单一平均分替代。

`BenchmarkJobStore` 只保存生成与校准任务状态，正式用例仍写入
`XpertEvaluationStore`。任务重启恢复时复用相同 generation job 已创建的数据集，避免
重复草稿；已完成 Evaluator work item 继续遵循原有不重复执行规则。

## 6. API

```text
GET  /api/benchmarks/capabilities
GET  /api/benchmarks/catalog?kind=agent_response
GET  /api/benchmarks/catalog/{pack_id}
POST /api/benchmarks/catalog/{pack_id}/instantiate
GET  /api/benchmarks/instantiations/{job_id}
POST /api/benchmarks/instantiations/{job_id}/cancel
POST /api/benchmarks/generations/preflight
GET  /api/benchmarks/generations
POST /api/benchmarks/generations
GET  /api/benchmarks/generations/{job_id}
POST /api/benchmarks/generations/{job_id}/cancel
POST /api/benchmarks/calibrations
GET  /api/benchmarks/calibrations/{job_id}
```

生成器 V5 的校准同时执行固定专业目标与同模型通用对照。通用对照保留原工作流
骨架、模型和输出契约，但移除领域 Prompt、Prompt Command、资源绑定及持久读写能力。
校准报告同时返回 `baseline_score`、`generic_counterfactual_score` 和
`targeting_advantage`。默认要求专业目标至少领先通用对照 `0.10`，否则产生针对性
warning。高分本身不再等同于过易：只有当大量样例高分且专业目标未显著领先通用对照
时，才报告“容易且缺少区分度”。

专业针对性门禁由服务端计算 `professional_evidence`。工具名、知识文档名和 Prompt
Command 别名继续精确匹配；领域 Prompt 则允许“精确 focus term”或“至少两个来自引用
锚点的专业标记”，避免因抽取短语的表面差异误杀真实专业场景。该证据摘要只保存匹配
术语、分数和锚点 ID，不保存完整 Prompt。

对 OpenAI 兼容推理模型，Benchmark JSON 模式可在 `message.content` 为空时，从
provider-specific reasoning 字段中仅提取一个包含 `dataset` 契约的 JSON 对象。外围推理
文本不会返回、持久化或进入日志；普通聊天不启用该兼容路径。每次生成仅保存安全诊断：
`finish_reason`、content/reasoning 字符数、是否找到契约、候选顶层键名及标准 token usage。
空内容、契约缺失或截断后的解析失败都只进入既有的一次修复机会，不增加重试次数或盲目提高
token 上限。Benchmark 生成与修复请求固定使用 `reasoning.effort=low`，避免复杂能力矩阵把
completion 预算主要消耗在推理通道；该配置不影响普通聊天或 Evaluator 调用。

目录列表返回 Manifest 摘要；详情返回固定用例。接口不返回完整 Xpert Prompt、工具结果、
知识正文、凭据、物理路径或 Runtime Store 数据。

## 7. 前端

`/agents/evaluations` 按以下视图组织：

- 标准基准：浏览 Pack 并添加到工作区。
- 针对性生成：选择固定目标、覆盖矩阵、模型和用例数，查看生成与校准进度。
- 我的评测集：编辑、导入、发布及配置基线/候选。
- 运行报告：查看运行记录、总体指标和逐样例结果。

实例化完成后自动进入新 Dataset 草稿。v1 已发布，可立即选择固定版本运行；继续编辑不会
改写 v1。

Xpert Studio、Meta Planner Proposal、Prompt Profile 与 Evolution Proposal 均提供
“生成评测集”入口。生成草稿仍须在“我的评测集”中人工审阅；校准不会自动发布。

## 8. RAG 引擎标准 Benchmark

`modelmirror-rag-foundation-bilingual-v1` 是 ModelMirror 自有的离线双语合成 Pack：

它的职责是验证检索、分块、索引、引用、无答案处理和版本切换的确定性行为，并为不同
Retrieval Profile 提供相同语料上的回归对照。它不是任意业务知识库的质量证明，也不得
作为用户知识库候选版本的唯一 Promotion Gate。业务质量必须使用基于目标知识库、固定
索引版本和真实问题分布生成并经人工审核的定向 Gold 评测集。

- 12 份 Markdown 文档，覆盖标题、表格、相似术语、长章节和冲突信息。
- 40 条查询：事实定位 12、同义改写 8、父子段/章节 8、跨语言 6、无答案 6。
- 初始索引固定为 General Processor、Parent-child 分块、本地 hash embedding、向量与
  FTS5 双索引、Full-text Top-K 10、无 Rerank。
- Gold 以 `document_key + anchor_key + anchor_phrase` 随 Pack 固定；实例化后必须在真实
  索引中唯一解析到 `document_id / chunk_id / source_block_id`，否则整个任务失败。
- 标准引用使用 `match_mode=source_block`，因此同一锁定语料可以公平比较 Recursive 与
  Parent-child 等不同分块候选；初始 chunk ID 只保留为诊断。

RAG Pack 实例化是可恢复异步任务，依次创建托管知识库、导入语料、构建双索引、解析
Gold、发布不可变评测 v1 并激活初始索引。未完成知识库不会进入普通列表；失败或取消会
清理半成品。托管知识库固定 `corpus_locked=true`，拒绝上传、删除文档和 Knowledge Inbox
写入，但允许修改流水线、构建候选、评测、激活、回滚和删除整个知识库。

Knowledge Evaluation Set 现在支持不可变递增版本。运行可显式固定
`eval_set_version`；草稿后续编辑或归档不会改写已有版本和报告。未传版本时继续按旧
revision 快照运行并保留 stale 规则。

无答案样例使用 `expected_no_result=true` 且必须没有 Gold 引用。Recall、MRR、nDCG 与
Citation 只聚合有答案样例；无答案样例单独报告 `no_result_accuracy` 与
`false_positive_rate`。标准 Pack 建议 Gate 为 Recall@5 0.70、Citation Coverage 0.70、
No-result Accuracy 0.80，但不会自动推广候选索引。

### 8.1 知识库定向 Gold 与 Formal 评测

`rag-gold-v2` 把 Gold 身份与生成来源版本拆开。语料身份只固定知识库、文档 ID/content
hash、source-block ID/content hash；Processor、分块、Embedding、Retrieval 与 Rerank
配置只进入目标执行指纹，不进入 `corpus_snapshot_hash`。因此不同检索实现只能在完全相同
语料快照上进入 Formal 比较。

- 正式生成固定为 42 条：30 条正例、12 条 corpus-near 困难负例；六类正例各 5 条，正负例
  均按中英文平衡。预检要求 3–35 份文档、至少 18 个稳定 source block，并在调用生成模型前
  证明文档占比、每块最多两次引用和五组跨文档多证据分配存在可行解。
- 服务端按文档分层抽样，最多发送 40 个、每个 1,200 字符且总计 48,000 字符的证据单元。
  Blueprint 固定题型、语言、难度和 evidence ID；模型只生成问题、逐证据 anchor quote 与公开
  理由。语义改写和跨语言题不再被强迫复制词面 marker。
- 连续规范化复制达到 32 字符直接拒绝；语义/跨语言题达到 12 字符、其他题达到 24 字符时
  产生泄漏警告，人工批准必须填写理由。修改 query、Gold、标签或 targeting 会清除该题旧审核。
- 42 条全部由人工在 UI 中逐题批准。拒绝项不得发布；发布 checksum 覆盖 cases、审核记录、
  coverage、provenance、calibration、corpus snapshot 与 qualification manifest。
- `rag-gold-v2` 不运行发布前真实检索校准，避免在正式门禁前重复消耗额度。发布后只允许一次
  `rag-eval-v2` Formal：一个 baseline、一个 candidate、同一语料、SHA-256 Seed 配对交错、
  每题每目标一次、无 warm-up、无自动重试。普通 6–30 条生成继续作为 legacy/diagnostic smoke。
- Formal 固定分母包含失败用例；失败质量记 0，失败耗时进入延迟。晋级同时要求绝对指标、
  Citation Precision@5、零错误、完整 30/12 分母、配对 bootstrap 95% CI 下界和 P95 延迟门禁。

入口位于 `/rag/:kbId/evaluation`。生成只创建草稿；发布、候选构建、Formal 运行、激活、部署
均为独立动作。候选即使通过也保持 `ready/promotion_required`，不会自动推广或激活。

## 9. 安全与后续边界

- Pack 内容与 checksum 随仓库版本发布，不在运行时联网更新。
- 标准核心 Benchmark 不执行真实副作用、HITL、Browser、Sandbox 写入或外部实时数据。
- 目录实例化不运行 Xpert、不批准 Proposal，也不修改线上资源。
- 定向生成默认创建待审核草稿，并必须完成同 revision 的受限校准后才可发布。
- 标准 RAG Pack 与知识库定向 Gold 的职责已经分离；后续不再扩张新的通用 RAG Pack。
- RAG Benchmark 使用 `KnowledgeEvaluationStore`，不会复制到 Xpert Dataset Store；目录 API
  不返回语料正文、内部锚点短语、chunk 全文、embedding 或物理路径。
- General Agent Workspace 最终只接目录和运行摘要，不替换 Penguin Benchmark Runtime。

## 10. 回归

```bash
python -m pytest server/tests/test_benchmark_catalog.py server/tests/test_benchmark_generator.py -q
python -m pytest server/tests/test_xpert_evaluations.py -q
python -m pytest server/tests/test_rag_benchmark_generator.py server/tests/test_rag_benchmark_standard.py server/tests/test_rag_evaluation.py -q
cd client
npm.cmd run build
```

新增后端包必须同步复制到 `server/Dockerfile`，并在共享栈空闲后通过真实镜像重建验证。
