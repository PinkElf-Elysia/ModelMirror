# 模镜科研 V0 锁定路线图

> 状态：**V0 规范性基线（Locked）**
> 锁定日期：2026-08-24
> 适用范围：`extensions/ai-research/` 在 V0 完整科研线形成前的产品、架构与分轮实施
> 当前实现：V0.1 / `0.3.0-v0.1` 实施中；AR1 夹具仍为 `fixture_only`、`harness_only`，文献能力固定 `scientificClaim=none`

本文是模镜科研 V0 的决策基线，不是愿景清单。后续实现计划、代码审查和批次验收必须以本文为参照。除本文“变更控制”列出的必要情形外，不得因实现者偏好、记忆、局部优化或临时技术兴趣改变产品主线、阶段顺序、开源组合或自研边界。

本文锁定未来方向，但**不自行授权**当前 AR1 接入模型、数据、外部服务或真实 EvalPack。每一轮仍须单独制定任务卡、确认来源锁和许可证，并遵守当时获批的实施范围。

## 1. 产品定义

模镜科研 V0 的固定产品身份是：

> **AI/Agent Research Project Workbench**：复用成熟开源项目，形成从研究问题到可复核成果包的一条完整 AI/Agent 科研主干。

V0 面向真实的 AI、机器学习、大模型和 Agent 研究过程，而不是泛化到全部 AI4S 学科。它应让研究者在同一 Research Project 下完成：

```text
创建项目与研究问题
→ 文献检索、导入与资料库
→ 带引用的综述与研究缺口
→ 候选假设、人工选择与实验协议
→ Jupyter 交互工作区、代码、数据和基线
→ R&D-Agent 提议—实现—反馈迭代
→ Inspect / Inspect Evals 固定评测
→ MLflow 参数、工程指标、trace 与制品
→ Notebook 分析、图表、结论与局限
→ Quarto HTML / PDF / DOCX 报告
→ Git + DVC + source-lock + receipt 可复现成果包
```

Inspect、MLflow、证据账本和 AR1 Research Console 是执行与复核底座，不是产品中心。单个 EvalPack 是科研项目中的评测内容，不定义模镜科研的产品身份。

## 2. 当前事实与产品缺口

截至 AR1，已交付的是 fixture-only 工程底座：受控创建、排队、运行、取消、Inspect 原始终态、MLflow 记录、证据完整性、只读 Inspect View、网页复核和重启恢复。

| 科研能力 | 当前状态 | V0 要达到的状态 |
| --- | --- | --- |
| 工程夹具执行、取消、证据 | 已有 AR0/AR1 底座 | 归入真实 Research Project |
| Research Project | 已完成真实创建、运行、重启恢复与复核 | 可创建、恢复、导出项目 |
| 文献检索、资料库、引用 | OpenAlex 真实检索及 Zotero 4/4 同步、索引、关联已验收 | 可检索、Zotero 导入、保存来源和引用 |
| 综述与研究缺口 | 已有一项独立 OpenAlex 成果包完整性 `verified`；Zotero 关联项目因上游 Quarto/BibTeX 引用键不一致未通过 | 形成可追溯引用的综述文档 |
| 假设与实验协议 | 未开始 | 候选假设、人工批准、冻结协议 |
| Jupyter 科研工作区 | 未开始 | 可查看和操作代码、数据、Notebook |
| AI/ML 研究迭代 | 未开始 | 通过上游 R&D-Agent 进行受控迭代 |
| 真实模型/Agent 评测 | 未开始，仅工程夹具 | 接一个原版合格 EvalPack |
| 实验追踪 | MLflow 工程底座已存在 | 与项目、迭代、评测和报告关联 |
| 分析、报告、复现包 | 未开始 | 可分析、渲染和重放 |

因此，AR1 不能被表述为“科研产品已成形”；准确表述是“完整科研线所需的执行与证据底座已存在”。只有 V0.1 至 V0.5 全部通过，才可声明形成完整科研线 V0。

## 3. 固定开源组合

### 3.1 主组合

| 科研阶段 | 固定上游职责 | V0 复用方式 | 规划基线 |
| --- | --- | --- | --- |
| 全流程蓝图 | [Agent Laboratory](https://github.com/SamuelSchmidgall/AgentLaboratory) | 参考其文献、实验、报告阶段结构、角色、检查点和笔记格式；不作为唯一运行时 | MIT；调研 commit `d9017d90e329112d2a80b7712f37ee9094d2cd27` |
| 文献与知识 | [Local Deep Research](https://github.com/LearningCircuit/local-deep-research) | 直接作为独立服务复用其检索、资料库、引用报告、历史、笔记和 Zotero 同步能力 | 项目 MIT；V0.1 经 A7 固定 `v1.10.6`、commit `641308272b2143df89c7a946051d2f05ca29b3c1`，官方镜像许可证另按 SBOM 处置 |
| 文献数据与导入 | [OpenAlex](https://openalex.org/) 与 [Zotero Web API v3](https://www.zotero.org/support/dev/web_api/v3/start) | 通过 Local Deep Research 或固定适配调用；使用公开交换格式 | OpenAlex 数据/API CC0；不打包 Zotero AGPL 客户端源码 |
| 假设与研发迭代 | [Microsoft R&D-Agent](https://github.com/microsoft/RD-Agent) | 直接复用 Research/Development 提议、实现和反馈循环；模镜只提供固定输入输出与受控运行包装 | MIT；候选版本 `v0.8.0`，调研 commit `6762f84f9bc0f5c6486c50a00e128a57ac6c3683` |
| 交互计算 | [JupyterLab](https://github.com/jupyterlab/jupyterlab) | 使用上游工作区，不自研 Notebook 编辑器 | BSD-3-Clause；候选版本 `v4.6.3` |
| 代码与数据版本 | [Git](https://git-scm.com/) + [DVC](https://github.com/iterative/dvc) | Git 管代码和文本；DVC 管数据与大制品，V0 使用模块本地命名卷 remote | DVC Apache-2.0；候选版本 `3.67.1` |
| 评测 | [Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai) + [Inspect Evals](https://github.com/UKGovernmentBEIS/inspect_evals) | 沿用 Inspect 公共接口并只接一个通过资格门禁的原版 EvalPack | Inspect AI 当前固定 `0.3.260`；Inspect Evals MIT，候选版本 `v0.18.0` |
| 追踪与证据 | [MLflow](https://github.com/mlflow/mlflow) + 模块账本/receipt | MLflow 记录参数、工程指标、trace 和 artifacts；账本保持生命周期与证据权威 | MLflow Apache-2.0，当前固定 `3.15.1` |
| 分析 | Jupyter + [Polars](https://github.com/pola-rs/polars) / [DuckDB](https://github.com/duckdb/duckdb) | 作为 Notebook 分析库使用，不自研分析引擎 | 精确版本在 V0.5 实施时锁定 |
| 报告发布 | [Quarto](https://github.com/quarto-dev/quarto-cli) | 从 Notebook、QMD、BibTeX 与 CSL 渲染 HTML/PDF/DOCX | MIT；候选版本 `v1.10.18` |

表中的版本和 commit 是规划时的候选基线，不等于已经安装或纳入 `source-lock.json`。进入相应实施轮次时必须重新确认精确版本、依赖、镜像、许可证、可再分发性和兼容性，随后才写入来源锁；不得凭“最新版本”浮动安装。

### 3.2 非默认与明确降级项

- [Open Notebook](https://github.com/lfnovo/open-notebook)：适合笔记和多模态资料，但与 Local Deep Research 的资料库职责重叠，V0 不作为默认服务。
- [PaperQA2](https://github.com/Future-House/paper-qa)：可在后续作为论文精读引擎候选，不替代完整文献工作台；调研候选版本 `v2026.08.12`，Apache-2.0。
- STORM、GPT Researcher：与 Local Deep Research 主职责重叠，V0 不并行引入。
- [AIDE](https://github.com/WecoAI/aideml)：在 R&D-Agent 无法满足固定 AI/ML 研发切口时作为窄化备选，不能无审批直接替换。
- AI Scientist v2：仅作设计参考。其自定义 Source Code License、自动代码执行、GPU 与成本边界不适合作为 V0 默认分发组件。
- SearXNG：V0 默认不单独部署，避免在已有文献检索主服务之外增加 AGPL 和运维面。

## 4. 自研边界

### 4.1 模镜允许拥有的控制面代码

- `ResearchProject` 清单、阶段状态和统一导航外壳。
- 模块服务启动、健康状态和经校验的深链接。
- 固定、可版本化的输入输出适配器。
- 面向既有模镜模型路由的本机 OpenAI 兼容控制桥；Provider key 仍留在模镜侧。
- 项目文件索引、制品关联、source-lock 和 receipt 汇总。
- 上游阶段间的显式交接和人工批准检查点。

这些代码只承担控制面、适配、证据和安全约束，不承担科研方法本身。

### 4.2 V0 禁止自研的科研能力

- 论文搜索、排名、推荐算法或自有文献 RAG。
- 自有科研规划器、假设生成算法或实验设计算法。
- 自有代码/实验 Agent、模型算法或提示策略。
- 自有 EvalPack、数据集、oracle、scorer、排行榜或科学指标。
- 自有 Notebook 编辑器、实验追踪器、论文写作/排版引擎或数据版本器。

若上游没有某项能力，V0 应显示“暂不支持”或延后，而不是临时补写一套自有科研逻辑。任何例外必须走本文第 9 节的路线变更流程。

## 5. 开放交换格式与项目成果

V0 不创造统一科研数据库，也不复制上游内部数据模型。阶段间使用可检查、可导出、可替换的开放文件与标识交接：

| 对象 | 固定交换形式 |
| --- | --- |
| 项目控制面 | `research.yaml` + Markdown |
| 文献 | BibTeX、RIS、CSL JSON、PDF 与来源 URL |
| 综述 | Markdown + citation keys |
| 假设与实验协议 | YAML + Markdown |
| 代码与配置 | Git revision |
| 数据与大制品 | DVC revision |
| 交互分析 | `.ipynb` |
| 评测 | Inspect EvalLog |
| 追踪 | MLflow experiment/run/trace/artifact IDs |
| 报告 | `.qmd`、HTML、PDF、DOCX |
| 重放 | source-lock、receipt、Git/DVC revision 与 replay instructions |

`research.yaml` 只是跨服务控制清单，不演变成新的工作流语言、科研算法描述语言或上游数据模型副本。

## 6. 模块与服务架构

模镜科研继续作为独立、可选、可拆分扩展，不进入默认主包。规划服务面为：

- 现有执行/证据层：Control、Tracking、Worker、Inspect View。
- 文献层：Local Deep Research 独立服务；V0 不默认附加 SearXNG。
- 研发层：R&D-Agent + 受控 Linux/Docker sandbox。
- 工作区层：JupyterLab + Git/DVC + 分析库 + Quarto。

每层使用独立镜像、依赖、命名卷和显式 profile。默认 ModelMirror 镜像、根 Compose、`client/` 和 `server/` 不因安装源码而增大。Research Console 最终演化为项目控制台，但可以通过本机安全深链接打开成熟上游 UI；V0 不为“统一视觉”重写上游完整界面，也不要求 iframe。

项目成果以项目目录和作用域挂载为中心；各上游服务保留自己的内部存储。V0 不引入跨服务统一主数据库。

模型控制桥是 V0.1 的必要平台适配，不是产品主线：它为上游服务提供本机、固定身份、OpenAI 兼容入口，保护 Provider key；V0 不增加多租户、计费、预算、商业化配额或通用 S2S 平台。

## 7. 固定分轮路线

### 已完成底座

- **AR0 / 0.1**：fixture-only Inspect 执行、终态、取消、账本、MLflow 与 evidence receipt。
- **AR1 / 0.2**：fixture-only Research Console、运行/事件/证据/系统页面与 Inspect View。

### V0.1：Research Project 与文献工作台

**用户完成什么**：创建真实 AI/Agent 研究项目，填写研究问题，启动 Local Deep Research，检索论文、导入 Zotero、保存资料库，并生成带引用的综述和研究缺口。

**复用组件**：Local Deep Research、OpenAlex、Zotero API；模镜实现 Research Project 控制清单、服务状态、固定交换适配和本机模型控制桥。

**产品页面**：项目列表、项目概览、Sources/Library、Literature Review、服务状态。

**验收产物**：

- 可重启恢复的 `research.yaml`。
- 至少一个真实 AI/Agent 主题的资料库。
- `literature-review.md`、引用键和 `references.bib`。
- 可核对的来源 URL、导入信息和 source bundle。

### V0.2：候选假设与实验协议

**强制前置批 V0.2-P2R（不构成产品交付）**：ResearchStudio 的锁定 coherence prompt 要求有代码工具时真实执行 Python、无工具时标记 `unexecuted`。首次 P2 资格运行使用纯文本 relay，却接受了模型自报的 `execution.mode=executed`、脚本和 stdout；三次 coherence 阻断证据均无工具调用或沙箱 receipt。该运行还使用六篇全文全部失败的资格夹具而非已验证 V0.1 成果包，最后一次 collision 仅有 `2/4` connector。因此原始 `phase_3_failed` 及费用/哈希必须保留，但只作为失效运行事实，不能证明模型、研究方向或完整产品链路失败。

P2R 必须先复用 Inspect 的公开 Agent/tool/sandbox 能力形成固定 ResearchStudio Host：每个 LLM phase 使用新上下文；只有 coherence phase 可在无网络、非 root、只读根文件系统、无 Docker socket、受限 CPU/内存/时间/文件/输出的临时 sandbox 中运行脚本；外部 API 不接受任意命令。`executed` 必须绑定 tool call、镜像 digest、脚本/输出哈希、exit code 和截断状态。主 JSON 与 `blocking_findings.json` 必须在同一 phase 原子交付。完整资格必须使用一个 integrity=`verified` 的 V0.1 OpenAlex 成果包，并锁定/验证 arXiv、OpenAlex、Semantic Scholar、OpenReview connector profile；缺失凭据或限流只能得到 `degraded`，不能得到 P2 pass。

P2R 首次重跑仍使用已通过 Phase 1 契约的 `openai/gpt-5.4`，以隔离宿主修复变量；旧 run 不得续跑。只有可信重跑再次失败后，才讨论更换 framing；只有模型协议在可信宿主上失败后，才另行申请 Claude/Gemini 比较授权。P2R 通过前不得实现 stage worker、academic relay 产品运行面、假设/协议 API 或 UI，也不得把模块版本或 execution mode 提升为 V0.2。

**用户完成什么**：从 V0.1 的已验证综述与来源包启动 IdeaSpark，逐个查看候选、引用依据、碰撞检索、coherence 与 Phase 3 审计事实；人工选择一个候选后，复用 AI-Researcher 生成七段式实验计划，补齐类型化字段并冻结。未通过上游终态或未经人工批准的候选不得进入协议冻结。

**复用组件**：固定提交 `a785e3aca7a2f0cb9775d45a7f2b5d3bf16f076a` 的 Microsoft ResearchStudio IdeaSpark 主机循环、prompt、确定性脚本、组合卡与终态；固定提交 `e5dd05a90bcadb436c07283c2f429367c6e525d3` 的 NoviScl AI-Researcher 实验计划 prompt、示例和七段式输出。模镜只实现受限模型控制、状态/receipt、输入成果包门禁、结构校验、人工选择与冻结，不自研科研 planner、候选评分、novelty 判断或实验方法。

**固定协议字段**：研究问题、人工批准的候选与来源引用、hypothesis、experiment design、baseline、自变量、数据集、指标、计算约束、停止条件、expected result、pipeline、失败条件、预期制品和风险/局限。AI-Researcher 原始七段式输出必须原样保存；模镜补充字段必须可区分为人工输入，不能伪装成上游生成。

**产品页面**：Research Question、IdeaSpark Attempts、Candidate Evidence、Human Selection、Experiment Plan、Protocol Completion、Freeze History。页面只有在 P2R 可信重跑与协议资格通过后实现。

**验收产物**：逐 phase receipt、全部上游原始终态与失败尝试、`candidate-manifest.json`、版本化 `hypotheses.md`、人工批准事件、AI-Researcher 原始计划、`experiment-protocol.yaml`、SHA-256 manifest 与 V0.3 交接 receipt。完整 P2R 尚未通过时，这些是目标产物而非现有能力。

### V0.3：Jupyter 工作区与 R&D 迭代

**用户完成什么**：打开项目 Notebook，检查代码、数据和 baseline，启动 R&D-Agent Development 阶段，观察提议—实现—反馈迭代，并人工批准或拒绝每轮结果。

**复用组件**：JupyterLab、R&D-Agent、Git、DVC 和受控 sandbox；不自研 Notebook 或代码 Agent。

**产品页面**：Workspace、Code Revision、Data Revision、Experiment Iterations、单轮输入/输出/状态。

**验收产物**：可打开的 `.ipynb`、Git commits、DVC revisions、每轮输入输出、运行日志、批准/拒绝记录和失败恢复证据。

### V0.4：真实评测与实验追踪

**用户完成什么**：在项目内选择一个合格的原版 EvalPack 和实验制品，通过 Inspect 运行固定小规模评测，查看样本进度、原始 EvalLog、MLflow 运行和证据完整性。

**复用组件**：Inspect AI、Inspect Evals、MLflow 和现有 AR0/AR1 账本/receipt。AR1 控制台在本轮被接入 Research Project，而不是继续作为孤立 fixture 页面。

**产品页面**：Evaluation Recipes、Evaluations、Sample Progress、Inspect View、MLflow Run、Evidence Integrity。

**验收产物**：一个通过资格审计且未修改 scorer/task 的 EvalPack、固定配置、EvalLog、MLflow IDs、receipt 和项目关联。V0 不接第二个评测方向，不做排行榜或模型营销结论。

### V0.5：分析、报告与重放

**用户完成什么**：在 Notebook 中分析结果、生成图表、写出结论与局限，用 Quarto 渲染报告，并导出可重放成果包。

**复用组件**：Jupyter、Polars/DuckDB、Quarto、Git、DVC、Inspect 和 MLflow。

**产品页面**：Analysis、Report、Reproducibility Bundle、Replay Check。

**验收产物**：

- `analysis.ipynb`、`report.qmd`、`report.html`，以及按环境可选的 PDF/DOCX。
- `references.bib`、图表和引用来源。
- Git commit、DVC revisions、EvalLog、MLflow IDs、receipt、source-lock。
- 可在干净环境执行的 replay instructions 与重放核验结果。

V0.5 通过后，才可将产品声明提升为“复用开源项目形成了一条可操作、可复核、可重放的 AI/Agent 科研主干”。

## 8. V0 明确不做

- 多租户、RBAC、计费、预算、商业化配额与租户级审计。
- Studio、主站默认导航、根 Compose 或默认安装包集成。
- 公共排行榜、模型横向营销、自动科学结论或科研有效性宣传。
- 自动投稿、同行评审、研究伦理自治或无人监督科研。
- 多学科 AI4S、湿实验、物理仿真和领域专用科学基础设施。
- GPU 集群调度、分布式训练平台和通用云资源编排。
- 同时接入多个 R&D Agent、多个文献工作台或多个 EvalPack 方向。
- 未经批准 fork 并修改上游科研逻辑，以自有实现填补上游缺口。

## 9. 路线变更控制

### 9.1 后续任务的强制引用

任何 V0 实施计划或任务卡必须明确写出：

1. 对应本文的目标轮次。
2. 本轮结束时用户实际能完成的科研动作。
3. 复用的上游项目和进入 source-lock 的精确版本/commit。
4. 模镜仅承担的控制面或适配职责。
5. 可检查的用户产物和验收命令。
6. 尚未覆盖的后续阶段。

审计、许可证、适配、隔离和证据是每轮的必要门禁，但不能替代该轮的产品功能与验收产物。若一个批次只增加门禁而没有推动对应用户动作，必须明确标记为子批次，不能宣称完成产品轮次。

### 9.2 允许提出偏移的必要情形

只有以下情形可以提出路线修订：

1. 上游项目不可获得、已停止维护、存在不可接受的安全问题或与锁定运行环境实证不兼容。
2. 许可证、数据许可、模型条款或可再分发条件与模块分发发生实质冲突。
3. 复现性、正确性或质量门禁出现无法通过且无法在既定边界内修复的硬失败。
4. 仓库已验证的现实架构使锁定方案无法实施，而非仅仅实现不便。
5. 用户明确改变产品目标或批准路线修订。

“实现更方便”“技术更新”“个人偏好”“想先做有表现力的页面”“想先补平台能力”或凭印象认为另一路线更好，都不是偏移理由。

### 9.3 Roadmap Amendment 记录

任何路线偏移在实施前必须先新增或更新一条 `Roadmap Amendment`，至少记录：

- 日期、提出人和用户批准证据。
- 触发事实及可复现实证。
- 受影响的锁定条目和轮次。
- 至少一个保持原路线的备选与被否决原因。
- 最小必要偏移，而非顺带扩张。
- 对完整用户旅程、开源复用边界、许可证、交付时间和回退的影响。
- 新的验收产物、停止条件和回退方法。

未经用户明确批准的 Amendment 只可作为提案，不得据此修改代码或宣称路线已经改变。

## 10. 每轮停止与回退原则

- 每轮必须独立可演示、可关闭、可保留成果文件并可回退到上一轮。
- 新上游服务只有在显式 profile 启动时存在；停止扩展不得启动、停止或连接默认模镜服务。
- 回退优先移除本轮 service/profile、适配器和导航入口，保留项目成果和命名卷；删除用户成果必须另行授权。
- 任一 P0/P1、安全、许可证、证据冲突或真实用户旅程不通时，当前轮不得标记完成。
- 当前 AR1 的 `fixture_only`、`harness_only` 边界持续有效，直到后续获批任务明确修改对应运行边界和来源锁。

## 11. V0 完成定义

只有同时满足以下条件，V0 才算完成：

- 一个真实 AI/Agent Research Project 可从研究问题走到可重放报告，不依赖手工搬运不可追踪状态。
- 文献、引用、假设、协议、代码、数据、评测、追踪、分析和报告均有开放、可检查的交接产物。
- 科研方法能力来自已锁版本的开源上游；模镜自有代码未越过第 4 节边界。
- 每个阶段保留人工检查点、原始上游事实、来源、版本和失败状态。
- 干净环境可按成果包说明恢复关键制品并复核主要结论链路。
- 模块仍可选、独立构建，不扩大默认模镜主包或默认运行面。
- 产品文案准确说明上游来源、能力边界与未经证明的科学结论。

## 12. Roadmap Amendment 台账

后续修订只允许在此追加记录，不得通过无记录地改写旧决策隐藏路线变化。每条记录使用以下格式：

```text
Amendment ID / 日期：
状态：提案 | 已批准 | 已拒绝 | 已回退
用户批准证据：
触发事实与复现证据：
受影响条目/轮次：
保持原路线的备选与否决原因：
最小必要偏移：
用户旅程、开源复用、许可证和交付影响：
新验收产物与停止条件：
回退方法：
```

### Amendment V0.1-A1 / 2026-08-25

- **状态**：已批准。
- **用户批准证据**：用户在收到“增加固定目标模型 relay、Control 改为仅 internal 网络”的最小修订说明后明确回复“批准”。
- **触发事实与复现证据**：V0.1 Control 为直连宿主模型桥加入了可出站网络和 `host.docker.internal`，因此除应用层 URL 校验外仍拥有一般网络出口；把现有网络直接改为 `internal` 后又无法到达宿主桥，真实模型旅程会中断。
- **受影响条目/轮次**：仅 V0.1 模型控制桥的运行拓扑和隔离验收；不改变 V0.1 产品动作、上游组合、固定模型或后续 V0.2–V0.5 顺序。
- **保持原路线的备选与否决原因**：保留 Control 直连并依赖 URL allowlist，无法消除进程被利用后的通用出站面；只把网络设为 `internal` 会切断固定模型桥，不能完成真实文献旅程。
- **最小必要偏移**：复用 Control 运行镜像增加无持久化、无公开端口的 `ai-research-model-relay` sidecar。Control、Tracking、Inspect View 和文献控制网络均为 internal；仅 relay 连接单独出站网络和固定本机 `/api/ai-research/v1` 目标。relay 仅接受 `models` 与 `chat/completions`，禁用环境代理、重定向、任意路径、查询参数和非 JSON/SSE 内容，并限制请求及响应大小。
- **用户旅程、开源复用、许可证和交付影响**：用户旅程与 LDR/OpenAlex/Zotero 组合不变；不引入新依赖或第三方镜像，不改变许可证结论；增加一项显式 literature profile 服务和隔离验证时间。
- **新验收产物与停止条件**：必须证明 Control 无 DNS/HTTP/宿主访问而 Control→relay 可达；relay 任意路径、查询和重定向均失败；固定桥请求保留流式响应并关闭上游连接。任一网络绕过或凭据泄漏存在时，V0.1 不进入可提交候选。
- **回退方法**：停止 literature profile，移除 relay 服务与专用网络，并恢复原桥 URL；不删除项目、LDR、模型、MLflow 或 Inspect 卷。若回退恢复 Control 通用出站，则相应 P1 重新打开。

### Amendment V0.1-A2 / 2026-08-25

- **状态**：已批准。
- **用户批准证据**：用户在收到 Amendment V0.1-A2 的阻塞事实、固定入口 gateway 最小方案及继续实施请求后明确回复“批准”。
- **触发事实与复现证据**：Amendment V0.1-A1 将 Control 的全部网络设为 `internal` 后，Docker Compose 渲染仍含 `127.0.0.1:8890:8080`，但实际容器端口状态为 `{"8080/tcp":[]}`，宿主连接得到 `WinError 10061`。服务内部健康检查通过，证明失败位于 internal-only 容器的宿主发布边界，而非 Control 进程或端口变量。若把普通入口网络重新接回 Control，8790 可用但一般出站 P1 重新出现。
- **受影响条目/轮次**：V0.1 Research Console 的本机入口和隔离拓扑；不改变科研产品动作、LDR/OpenAlex/Zotero 组合、数据格式或 V0.2–V0.5 顺序。
- **保持原路线的备选与否决原因**：直接把 Control 接回非 internal 网络会推翻 A1 的隔离结论；要求用户通过 `docker exec` 操作会失去 Research Console 用户旅程；依赖宿主手工端口转发不可复现；引入 Nginx/Caddy 会增加新的第三方镜像、锁定和许可证面。
- **最小必要偏移**：复用 Control 镜像增加无持久化的 `ai-research-console-gateway`。只有 gateway 连接本机入口网络并发布三个既有回环入口，同时连接对应 internal 网络；Control、Tracking 与 Inspect View 自身不发布端口且保持无宿主/公网路由。gateway 使用三个固定监听器，分别绑定 `ai-research-control:8080`、`ai-research-tracking:5000` 与 `ai-research-inspect-view:7575`，禁用环境代理、重定向和任意上游选择，对请求/响应、头、方法和大小做边界限制，并原样支持 SPA、轮询 API、登记成果下载及原有只读复核入口。
- **用户旅程、开源复用、许可证和交付影响**：恢复计划锁定的 8790 Research Console；不引入生产依赖或第三方镜像；增加一个可选模块服务、一个入口网络及相应安全测试，预计仅影响 V0.1 收口。
- **新验收产物与停止条件**：宿主→gateway→Control 的页面、API、深链和 artifact 下载成功；Control 公共 DNS、公共 IP 和宿主直连均失败；gateway 不能选择外部目标、不能转发未允许头、不能接受超限请求，且不泄漏 token。任一绕过存在时不进入可提交候选。
- **当前验收证据**：使用独立 Compose 项目、独立回环端口和六个互不重叠的 `/28` 子网完成 Full；Console/API/成果链、MLflow outbox、Inspect View 递归 EvalLog、可选 View 降级、Worker/Control/Tracking 重启恢复与默认主包零增量均通过。合法本机 Inspect Origin 返回递归日志，伪造 Host/Origin 返回 400。该证据仅关闭 A2 入口与隔离假设，不关闭真实固定模型、OpenAlex、Zotero 和 LDR 许可证门禁。
- **回退方法**：停止 profile，移除 console gateway 和入口网络；保留所有项目与证据卷。回退后 Console 不可从宿主访问，除非同时重新打开 A1 已关闭的 Control 通用出站 P1。

### Amendment V0.1-A3 / 2026-08-26

- **状态**：已批准。
- **用户批准证据**：真实文献运行失败和最小修订方案说明后，用户明确回复“批准A3”。
- **触发事实与复现证据**：首次真实运行 `lr_7a215aca0a474aa69101912f9f5c3553` 使用 LDR v1.10.5 的 `langgraph-agent`，LDR 向固定模型桥发送 `tools` 与 `max_completion_tokens=30000`；模型桥按冻结边界拒绝 `tools` 和未知字段并返回 422，LDR 将原始研究 `5f2bb80b-aefe-4084-8acb-1b5682a31cc8` 标记为 `failed`。LDR v1.10.5 自身把 `source-based` 列为正式策略，并在工具调用错误说明中明确该策略绕过 tool calling。
- **受影响条目/轮次**：仅 V0.1 固定文献研究 profile 和模型桥的 OpenAI 文本 token 参数兼容；不改变 LDR/OpenAlex/Zotero 组合、固定模型、研究问题、来源与成果格式或 V0.2–V0.5 顺序。
- **保持原路线的备选与否决原因**：继续使用 `langgraph-agent` 必须让模型桥转发和解释完整工具调用协议，违反 V0.1 明确拒绝 `tools` 的安全边界并扩大适配范围；修改 LDR 或自研 Agent 会违反上游原样复用与禁止自研科研算法的边界。
- **最小必要偏移**：固定 LDR 原生策略改为 `source-based`，继续固定 OpenAlex、检索数量、迭代次数和 `public_only` egress。模型桥接受受同一 `1..32768` 上限约束的 `max_completion_tokens`，在转发前规范化为 `max_tokens`；两者同时提供时失败关闭。`tools`、多模态和其他未知字段继续拒绝。
- **用户旅程、开源复用、许可证和交付影响**：真实文献检索、来源、引用综述和 Zotero 用户旅程保持不变；不新增依赖、镜像或许可证面。放弃 LDR 的自主工具选择，换为计划原本固定的 OpenAlex source-based 流程，减少不可控搜索引擎与工具调用。
- **新验收产物与停止条件**：契约测试必须证明 token 别名受限、互斥且规范化，`tools` 仍被拒绝；真实重试必须显示 `source-based + openalex`，产生可核对来源和完整成果包。若 source-based 仍发出工具调用、固定搜索配置漂移或引用成果不一致，V0.1 继续停止。
- **回退方法**：恢复固定策略为 `langgraph-agent` 并移除 token 别名字段；保留首次失败与后续尝试记录及所有项目卷。回退会重新打开已证实的 422 阻塞，除非另行批准扩大工具调用协议。

### Amendment V0.1-A4 / 2026-08-26

- **状态**：已批准。
- **用户批准证据**：第二次真实运行的模型可靠性证据和最小固定 profile 修订说明后，用户明确回复“批准A4”。
- **触发事实与复现证据**：A3 后的真实运行 `lr_5465478d4ae045daa39ae85fa55e08fc` 已确认使用 `source-based + openalex`，OpenAlex 多次返回 200 并产生真实候选；LDR 随后为 OpenAlex 自动启用 LLM relevance filter，并发分批调用固定免费模型。至少一项调用返回不符合 OpenAI completion 合同的响应，模型桥记录 `ai_research_bridge_invalid_response` 硬失败并按平台规则要求重新认证；后续请求 fail-closed，研究在 80% 以原始 `failed` 终止。多项其余响应正文为空，进一步表明该模型不适合并行索引筛选。
- **受影响条目/轮次**：仅 V0.1 固定 OpenAlex profile 的上游原生相关性过滤设置和当前模型重新认证；不改变固定模型、OpenAlex 主检索、source-based 策略、来源包、模型桥门禁或后续轮次。
- **保持原路线的备选与否决原因**：直接重试会再次触发同一并行过滤面且不能解释可靠性；放宽畸形响应硬失败会削弱平台控制面；立即更换模型会改变管理员已批准的固定模型；修改 LDR 并发代码或自研过滤器均越过上游复用边界。
- **最小必要偏移**：通过 LDR v1.10.5 原生最高优先级设置 `search.engine.web.openalex.default_params.enable_llm_relevance_filter=false`，关闭 OpenAlex 的 LLM 二次筛选，继续采用 OpenAlex 原生 `relevance_score`、固定 15 条结果和 source-based 研究。重新运行既有 Chat certification 清除已解释的硬失败；模型桥仍拒绝畸形响应，问题生成和综述仍由固定模型完成。
- **用户旅程、开源复用、许可证和交付影响**：不新增依赖、镜像、许可证或自有算法；减少并行模型调用和失败面，保留真实检索、来源、问题生成和综述。相关性排序由 OpenAlex 上游承担，不再由当前固定模型二次裁剪。
- **新验收产物与停止条件**：固定 profile 契约必须证明该设置为 false；LDR 日志必须证明使用 per-engine setting 且未调度 relevance-filter LLM 批次；重新认证和第三次运行必须保留原始状态并产生成果包。若串行问题生成或报告仍出现畸形/空响应，则停止当前模型，未经新批准不得更换模型或放宽控制门禁。
- **回退方法**：移除该 per-engine 设置并重新解锁 LDR，使其恢复 v1.10.5 默认自动过滤行为；保留两次失败记录、认证记录和所有项目卷。回退将重新暴露已证实的并行模型不可靠风险。

### Amendment V0.1-A5 / 2026-08-28

- **状态**：已批准；本条补记已执行的批准路线，不追溯改写 A3/A4。
- **用户批准证据**：在后续真实旅程收口中，用户批准受限工具协议方案，并明确授权在不落盘 S2S token 的前提下为固定模型完成 Provider 配对、`chat_tools` 重认证和 LDR 解锁；随后多次明确回复“批准”“已配对”“已解锁”，并对真实成果执行人工验收。
- **触发事实与复现证据**：后续实现与验收不再停留于 A3/A4 的 `source-based + chat_text` 假设，而是通过模镜既有模型控制面的 `chat_tools` 资格门禁，转发 LDR v1.10.5 `langgraph-agent` 的受限 OpenAI function-tool 协议。验收账本中运行 `lr_ddaebbcccb0e44a0b8b52abda7ebe421` 保留 `strategy=langgraph-agent`，上游与归一化 outcome 均为 `completed`，成果完整性为 `verified`。本次收口审计发现代码、真实证据与只记录到 A4 的路线图不一致，故追加本条。
- **受影响条目/轮次**：仅 V0.1 固定模型桥的文本工具协议、资格门禁和固定文献策略；不开放任意用户工具、不改变 OpenAlex 主检索、Zotero/本地资料库、成果格式、模型选择或 V0.2–V0.5 顺序。
- **保持 A4 路线的备选与否决原因**：强行把已验证代码回退到 `source-based` 会丢弃已获人工批准且已产生可复核成果的真实路径，并使代码、账本和运维认证再次分裂；继续让路线图声称“拒绝 tools”则会把实际扩大过的协议面隐藏在文档之外。修改或 fork LDR 以消除工具调用仍违反上游原样复用边界。
- **最小必要偏移**：固定 profile 恢复 LDR 原生 `langgraph-agent`，但仍固定 `openalex`、15 条结果、`public_only` egress，并保留 A4 对 OpenAlex LLM relevance filter 的关闭。S2S 桥只接受有界的 OpenAI function tools：工具数量、名称、JSON Schema 形状与总大小受限；assistant tool call 必须引用已声明工具，每个 tool message 必须匹配未完成 call；未知字段、多模态、任意模型和超限请求继续失败关闭。带 tools 的请求必须通过 `chat_tools` 资格门禁，纯文本请求使用 `chat_text`。
- **用户旅程、开源复用、许可证和交付影响**：仍由 LDR 负责搜索编排和工具调用，模镜只承担协议约束、固定模型路由、资格校验与审计；不新增依赖、镜像或许可证面。用户旅程仍是项目内启动文献研究，不获得工具选择或任意执行入口。
- **新验收产物与停止条件**：契约测试必须覆盖工具声明、调用/响应配对、Schema 与请求总量边界、流式 tool-call 终态、`chat_tools` 资格失效和畸形上游响应；真实验收必须保留精确策略、OpenAlex 来源与成果哈希。任意未声明工具、能力门禁旁路、Provider key 泄漏或非固定模型可达均为停止条件。
- **回退方法**：关闭 S2S 桥和 literature profile；如需回到 A4，必须另开修订把 profile、项目记录、桥协议和资格认证一起切回 `source-based + chat_text`，并重新完成真实旅程。不得仅删除工具字段而保留 `langgraph-agent`，也不得删除既有项目和成果卷。

### Amendment V0.1-A6 / 2026-08-28

- **状态**：已批准；仅调整可选扩展的帮助中心交付轮次，不改变产品能力。
- **用户批准证据**：收口审计指出根级帮助中心门禁与 V0.1 固定的 `client/` 零增量边界冲突后，用户明确批准将该门禁推迟到 AR3/Studio 条件入口轮次，继续完成模块内收口。
- **触发事实与复现证据**：根级治理要求所有用户可见变化在同一 PR 修改 `client/src/content/help-center/` 并附同基线截图；V0.1 同时锁定为未安装即不可见的独立可选扩展，禁止修改 `client/`、Studio 路由和默认主包。两项要求无法在本轮同时满足。
- **受影响条目/轮次**：仅 V0.1 的主站帮助中心交付门禁和 AR3 的条件式 Studio 入口；不改变 Research Console、LDR/OpenAlex/Zotero 组合、成果格式、安全门禁或 V0.2–V0.5 顺序。
- **最小必要偏移**：V0.1 继续以模块内 README 提供操作、限制、验收和回退说明，不修改主站帮助中心；AR3 在扩展已安装且健康时增加 Studio 条件入口的同一批次，必须补齐主站帮助文章、截图和从入口开始的真实预览重放。
- **新验收产物与停止条件**：V0.1 必须继续证明 `client/` 逐文件哈希和默认 Compose 清单零增量，并在 PR 的 `Help Center Impact` 中明确引用本修订而不得声称 `None`。AR3 若缺少同一基线的帮助文章、截图和重放证据，不得创建或合并其 PR。
- **回退方法**：如决定在 AR3 前提供主站入口，必须另开修订并在同一实现批次恢复根级帮助中心门禁；不得只增加入口而继续沿用本次延期。

### Amendment V0.1-A7 / 2026-08-28

- **状态**：已批准；升级候选必须先在全新隔离栈证伪，不等于 v1.10.6 已通过产品门禁。
- **用户批准证据**：在真实 v1.10.5 Zotero 研究包的 Quarto/BibTeX 引用不一致被严格拒绝、且官方 v1.10.6 候选和剩余风险说明后，用户明确回复“批准升级并配置”。
- **触发事实与复现证据**：运行 `lr_f5d2f764b1db410d8accddf8caca8e49` 的 LDR 原始状态为 `completed`，但真实 Quarto ZIP 的 QMD 有 939 个引用键、BibTeX/RIS 仅有 186 个来源条目，753 个引用键无对应 BibTeX。v1.10.5 QuartoExporter 会转换全文全部数字引用，却只从来源行生成 bibliography。官方 v1.10.6 发布说明和源码加入分组来源、去重与 Quarto/BibTeX 修复，但上游仍未提供最终的反向引用完整性保证。
- **受影响条目/轮次**：仅 V0.1 的 LDR 版本、官方镜像、SBOM、API 契约和真实文献旅程；不改变固定模型、OpenAlex/Zotero 组合、模镜严格成果校验、模型桥、安全拓扑或 V0.2–V0.5 顺序。
- **最小必要偏移**：来源锁提升到 `v1.10.6`、commit `641308272b2143df89c7a946051d2f05ca29b3c1`、linux/amd64 镜像 digest `sha256:b2c634291de8fb8d0662ab81a0b82ec17ab807109d20d57386042c5bdcd472e5` 和官方 SBOM `sha256:6f9c0e6f762763d2b34207a7638b65bedd37d818bd86e538483b21cb091c6315`。先使用新 Compose 项目和新卷验证；不得原地迁移现有验收资料卷来制造通过。
- **许可证与分发影响**：v1.10.6 amd64 SBOM 为 438 个包，有效未知项由 37 增至 38，GPL/LGPL 声明仍为 100、AGPL 声明仍为 0。集成继续固定为 `external_pull_only`，镜像化、离线捆绑、修改或模镜再分发仍阻断。
- **新验收产物与停止条件**：必须重新通过登录/CSRF、研究启动/状态/取消、Library、Zotero、Quarto/RIS 契约，使用真实固定模型完成 OpenAlex 与已索引 Zotero 集合旅程，并证明每个 QMD 引用键都存在于 BibTeX。若引用仍不一致、API 漂移、迁移要求无法回退、凭据泄漏或许可证门禁扩大，则保持 v1.10.5 现有卷不变并停止切换。
- **回退方法**：隔离验证失败时移除 v1.10.6 候选配置并保留其独立卷用于取证；当前 v1.10.5 验收栈和所有既有卷不变。只有隔离验证通过后才允许另行切换当前验收栈，切换前必须创建并验证 LDR 自身的预迁移备份。

### Amendment V0.2-A1 / 2026-08-28

- **状态**：已批准；本条冻结 V0.2 的产品切口、上游组合与前置资格门禁。
- **用户批准证据**：用户在审计下一轮、要求补足开源复用和完整科研线、并审阅含前置批次的 V0.2 计划后明确要求“开始执行计划”。
- **触发事实与复现证据**：V0.1 已形成真实文献检索、Zotero 资料库和一致成果包，但路线图中的 V0.2 仍只描述抽象的“问题—假设—协议”，没有锁定上游主干、真实用户动作、交接格式和停止条件。最新来源审计确认 Microsoft ResearchStudio 的 IdeaSpark 在固定提交中提供五阶段、单想法、可恢复的主机循环和明确终态；NoviScl AI-Researcher 在固定提交中提供七段式实验计划生成模板。两者均为 MIT 项目，但其运行依赖、模型协议和输出质量仍须独立资格验证。
- **受影响条目/轮次**：V0.2 假设与协议轮次，以及模型桥的受限结构化输出兼容；不改变 V0.1 文献成果，不提前执行 V0.3 的 R&D-Agent/Jupyter/代码与实验，不接 Studio、多租户、计费或商业化。
- **保持原路线的备选与否决原因**：只编写自有表单与提示词会偏离“复用开源形成科研线”的锁定方向；直接运行完整 AI-Researcher 会引入其密钥文件、直接 Provider 客户端和实验执行面；提前接 R&D-Agent 会把假设、协议、代码与实验一次扩成多轮范围；把 ResearchStudio 或 AI-Researcher 当作自动科学裁判则超出上游证据。
- **最小必要偏移**：模块版本提升为 `0.4.0-v0.2`。只允许已验证的 V0.1 文献成果包进入 V0.2。依次复用固定提交 `a785e3aca7a2f0cb9775d45a7f2b5d3bf16f076a` 的 ResearchStudio IdeaSpark 主机循环生成最多三个候选，并复用固定提交 `e5dd05a90bcadb436c07283c2f429367c6e525d3` 的 AI-Researcher 七段式模板生成一个候选的实验计划。候选串行生成，最多五次尝试；模镜不排名、不评分、不补写科研方法。用户必须人工选择候选、补齐缺失的类型化协议字段后方可冻结，冻结后以规范 JSON、Markdown 和 SHA-256 manifest 作为 V0.3 唯一输入。
- **用户旅程、开源复用、许可证和交付影响**：项目新增“假设与协议”阶段，用户可从已验证综述启动候选生成、对照来源证据、选择一个候选、生成并人工补齐协议、冻结和下载成果包。ResearchStudio 与 AI-Researcher 均按精确 commit、archive hash、许可证 hash 和复用文件 hash 锁定；只复制经资格审计的源文件/模板子集，不运行上游安装器，不读取 `keys.json`，不引入直接 Provider key。第三方 notice 随可选模块分发。
- **新验收产物与停止条件**：P0 固化来源、许可证、边界和零默认增量；P1 运行 ResearchStudio 原生自测并验证确定性 host loop、终态与断点；P2 使用现有固定模型桥分别验证 IdeaSpark 每个 LLM phase 和 AI-Researcher JSON 输出，包括截断、畸形 JSON、引用不存在、重复候选、`do_not_generate`、`phase_3_failed` 和资格失效。若必须自创科研 prompt/Agent loop、固定模型无法完成原版链路、引用无法回指 V0.1 来源、私有资料越过 egress、许可证/依赖存在未处置冲突，V0.2 停止且不得自动切换 CKM、R&D-Agent 或其他候选。
- **回退方法**：停止显式 V0.2 profile，移除 stage worker、academic relay、假设/协议 API 与模块内页面；保留 V0.1 项目、文献成果和已有 `hypothesis-protocol` 成果目录。删除项目成果或命名卷仍需另行授权。

### Amendment V0.2-A2 / 2026-08-29

- **状态**：已批准；新增 P2R 资格完整性前置批，不授权 V0.2 产品运行面。
- **用户批准证据**：用户审阅“执行证据来源失真、资格夹具越过真实成果包门禁、collision 仅 `2/4`”的严格审计后明确批准修改 V0.2 工作树中的路线图、协作规则、资格账本、来源锁、模块边界、模型桥及测试，完成 P2R 前置批收口；同时明确禁止模型调用、Commit、Push 和 PR。
- **触发事实与复现证据**：ResearchStudio `coherence_trace.txt` 规定无代码工具时必须标记 `unexecuted`；纯文本 qualification relay 未声明 tools、也未执行模型返回脚本，但三个 coherence 输出均声明 `mode=executed` 并把自报 stdout 作为 blocking evidence。上游回归检查只验证 script/output 非空，Phase 3 又将其提升为优先执行证据。Attempt 1 的完整 `blocking_findings.json` 还晚于 Phase 3 audit 生成。端到端运行使用六篇摘要级资格夹具，全部 fulltext=`failed`，且最后一次 collision 只有 OpenAlex 主导的 `2/4` connector 覆盖。
- **受影响条目/轮次**：仅 V0.2-A1 的 P2 资格解释、ResearchStudio Host 适配和进入产品批次的门禁；不改写旧 artifact、终态、usage/cost，不改变 V0.1 文献能力、V0.2 上游组合、候选人工批准或 V0.3–V0.5 顺序。
- **保持原路线的备选与否决原因**：继续纯 Chat Relay 并接受 `unexecuted` 会与上游把 blocking handoff 视作 executed evidence 的链路冲突；立即换模型或 framing 无法隔离坏宿主变量；更换上游会越过 A1；删除旧证据会破坏可追溯性。
- **最小必要偏移**：在任何产品实现前增加 V0.2-P2R。允许复用现有 Inspect 0.3.260 的公开 Agent/tool/sandbox 能力，为锁定 ResearchStudio phase 提供真实、可收据化的临时代码执行；调用方仍不能提交任意命令。资格状态改为 `qualification_invalid_execution_provenance`，模块对外版本继续保持 `0.3.0-v0.1`。桥接的 structured output 仅属于固定 hypothesis workload，且 hypothesis 发现/调用不得依赖当前 literature 资格。
- **用户旅程、开源复用、许可证和交付影响**：本批不增加用户功能。继续复用原 ResearchStudio/AI-Researcher commit；Inspect 已在模块来源锁中，不新增科研算法。若后续固定 sandbox 需要新的镜像或依赖，必须先锁 digest、SBOM 和许可证，不得借 P2R 引入通用代码执行服务。
- **新验收产物与停止条件**：保留失效 run 的原始终态、哈希和费用；新 run 必须有逐 phase receipt、可信 code-exec provenance、原子 multi-output、verified V0.1 bundle 以及完整 connector 资格。首先以 GPT-5.4 从新 run 重放。任一伪执行、输出截断不可判定、任意命令入口、bundle 不完整、connector degraded、凭据泄漏或上游科研 prompt/loop 被修改时继续 NO-GO。
- **回退方法**：移除 P2R host/profile 与未通过的新 run，保留失效 run 和 V0.1 项目成果；桥关闭后恢复纯 V0.1。不得删除用户成果、旧资格证据或命名卷。
