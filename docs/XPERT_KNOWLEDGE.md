# Xpert Knowledge Pipeline Runtime

最后更新日期：2026-08-11

## 目标

Knowledge Pipeline 把安全草稿配置推进为可恢复的本地索引构建任务，同时保持现有上传和查询入口兼容。核心契约是：**构建候选、隔离预览、人工激活、随时回滚**。

## Strategy Router 与 Auto Tuner

- Router V1 使用经审阅的确定性规则和聚合语料画像，只把 Chunker/Retrieval 建议写入草稿。
- Auto Tuner 固定知识版本、来源、评测版本与规则版本，在隔离 trial namespace 中搜索并验证参数。
- 调优前必须通过 `RAG Strategy Tuning Readiness V1`：Catalog 标准 Pack 仅作回归护栏；正式检索选择至少需要 30 条正样例，阈值搜索另需 12 条已审核语料近邻困难负例。
- `RAG Strategy Tuner V4` 保留 V3 的 Recall/nDCG/困难负例误召回 Pareto 和语义去重，并增加重复 Holdout 与配对统计；不安全的阈值改善会保留基线。
- 定向 Gold 生成器的“策略调优证据”模式可生成 30–60 条用例，默认 30 条正样例与 12 条待审核困难负例；审核和重新校准仍是解锁 threshold 调优的必要条件。
- 跨分块证据除了稳定 `source_block` Gold，还必须覆盖稀疏、单块密集和多块密集问题；真实索引与排序指纹无差异时自动退化为仅检索调优。
- Processor、Vision 与 Embedding Profile 在本轮调优中固定，不参与候选变化。
- 只有通过 Holdout、Evaluation Gate、Pareto 与有效改善门槛的胜者才会物化为普通候选版本。
- Holdout finalist 每题重复检索 3 次并使用中位延迟；质量差异以固定 Holdout 内的 3 组分层重采样和 1,000 次配对 bootstrap 形成 90% 区间，统计非退化失败时禁止物化。
- 物化版本始终是 `promotion_required`；完整评测通过后仍需用户显式 Promote，绝不自动激活。
- 调优运行的直接检索和最终复跑都遵守候选 Profile 的 Top-K；普通 Evaluation API 保持既有最大 K 评测语义。
- trial 索引不可激活、不可见于普通版本列表，终态后清理；持久 Coordinator 在重启后复用已完成工作。

## 数据模型

- Pipeline Graph：知识库级可编辑 DAG，保存节点、端口、坐标、配置、`graph_revision` 和 `compiled_draft_version`；Graph 只编译 Draft，不直接执行索引。
- Pipeline Draft：知识库级可编辑配置，包含递增 `version`、分块大小和重叠量。
- Pipeline Job：固定 draft version、源快照、六段执行状态、尝试次数、错误摘要和 candidate version。
- Pipeline Version：不可变候选索引摘要，记录版本号、来源、文档数、chunk 数和激活时间。
- Active Version Pointer：每个知识库最多一个 active version ID；切换指针不重写历史候选。

Job 状态为 `queued / running / succeeded / failed / cancelled`。Stage 固定为 `load / vision / process / chunk / embed / store`；视觉未启用时 `vision` 为可观测的跳过阶段。

## 多模态理解

图片和扫描 PDF 不进入 legacy 即时向量索引，而是标记为 `pipeline_required`。Knowledge Graph 可在数据源与结构化处理器之间加入 `image_understanding`：

```text
data_source -> image_understanding -> structured_processor
            -> chunker -> embedding -> dual_index -> retrieval
```

视觉节点必须显式选择支持图片输入的注册模型。PDF 使用 PDFium 渲染，图片使用 Pillow 解码和格式校验；自动页面选择默认处理文字少于 80 字符或图片覆盖率达到 12% 的页面。模型严格返回 `ocr_visual_summary_v1`，服务端转换为 `image_ocr`、`image_description`、`visual_table` 和 `visual_chart` 块，并保留页码和来源 block ID。

每页结果按 source hash、视觉模型和配置 hash 持久化。失败重试或服务重启只重跑失败页；`continue_on_error` 允许有可用内容的文档带 warning 继续，`strict` 任一选中页失败都会阻止候选 ready。视觉正文、图片 Base64、prompt 和密钥不会进入 checkpoint 或公开 Job/Version 响应。

## 结构处理与生成式索引

`process` stage 不再把文档直接退化为无结构文本。它先产出稳定的 `ProcessedDocument / DocumentBlock`，支持标题、段落、列表、表格、代码和 PDF 页面，并保留标题路径、页码、字符偏移与安全元数据。

每个 Job 固定一个不可变 `processor_profile`：

- `general`：保留结构后进入递归或父子分块。
- `qa`：通过现有 newAPI/OpenRouter 兼容网关生成严格 JSON 问答，索引问题并返回答案与来源段。
- `summary`：生成文档/章节摘要，索引摘要并返回对应原文上下文。

生成批次最多尝试两次。`failure_policy=continue_on_error` 允许部分文档成功后生成带 warning 的候选；`strict` 遇到任一失败即阻断 ready。所有文档失败时 Job 失败且不创建 candidate version。

Job 为每个 source 持久化处理状态、内容 hash、配置 hash、尝试次数、耗时和计数。Retry 复用 hash 完全匹配的完成产物，只重跑失败 source；向量和 FTS5 索引仍从全部成功处理产物重新原子构建，不复用半成品索引。

## 数据源契约

执行请求可以包含知识库 document IDs 和最多 5 个 Xpert 文件引用。`source_document_ids=null` 表示使用当前知识库全部文档，空数组表示不选择知识库文档。Xpert 文件必须携带所属 Xpert、conversation 和 asset ID，并且只能由对应 `XpertContextStore` 解析。

创建 Job 时立即完成去重和私有快照。后续源文件归档不会破坏已创建 Job；跨 Xpert 伪造引用会被拒绝。公开响应只返回文件名、来源类型、大小和 ID，不返回快照路径或正文。

## 执行与恢复

`KnowledgePipelineExecutor` 在 FastAPI 启动时运行，单进程内一次只处理一个 Job。服务重启时，遗留 `running` Job 回到 `queued`。每个阶段开始和完成都会更新持久化 metadata，并写入 `knowledge_pipeline` RunRegistry checkpoint。

RunRegistry 是内存态。若持久化 Job 中的旧 `run_id` 在新进程不存在，executor 会创建 recovery run，并记录 `recovery_of_run_id`。失败或取消会删除候选 namespace；active version 不受影响。

## 预览、激活与回滚

1. 执行成功后产生 `ready` candidate version。
2. 使用版本 query API 对候选 namespace 做隔离检索。
3. 用户确认后调用 activate API，原子切换 active pointer。
4. 激活任意历史 ready version 即完成回滚。

普通 `/api/rag/query`、Chat RAG、`knowledge_retrieval` 和 `knowledge_citation` 都由 `RagService` 解析 active version。旧知识库尚未激活候选时继续查询 legacy namespace。

## 离线检索评估与 Promotion Gate

Evaluation Set 按知识库保存问题、标签和期望引用。引用使用稳定 `source_document_id`，并可进一步固定 chunk、source block 与页码；候选版本内部 namespace 不会进入标注契约。评估集使用 revision 乐观并发，Evaluation Run 启动时固定完整评估快照、1 至 5 个不可变目标版本和检索配置。

后台 `KnowledgeEvaluationExecutor` 对每个用例执行无生成检索，计算 Recall@1/5、MRR@10、nDCG@10、Citation Hit/Coverage、无结果率、错误率和 P95 延迟。Run 与 checkpoint 只保存 ID、rank、分数、数量、耗时和错误摘要，不保存完整问题、文档正文、snippet、prompt 或密钥。服务重启后 queued/running run 会安全恢复。

Promotion Gate 有两种模式：

- `advisory`：展示阈值与回归结果，但保留原有人工激活路径。
- `required`：激活必须提供通过的 Evaluation Run；服务端还会校验知识库、候选版本和当前评估集 revision，过期结果不能用于 Promote。

默认门禁检查 Recall@5、MRR@10、Citation Hit、无结果率、错误率、P95 延迟及相对基线回归。`POST /pipeline/versions/{version_id}/promote` 是带评估证明的激活入口；历史 ready 版本仍可通过相同门禁回滚。

## Knowledge Agent 与审批写入

已发布 `workflow_agent` 可显式配置 1 至 5 个知识库并启用 Runtime 知识工具：

- `knowledge_search`：按活动版本 profile 执行向量、全文或混合检索，多库结果稳定融合，每库失败只返回 warning。
- `knowledge_get`：以知识库和 chunk ID 精确读取活动 namespace 的受限上下文，禁止跨 namespace 获取。
- `knowledge_cite`：返回稳定 CitationAnchor，不额外调用模型生成回答。
- `knowledge_propose_write`：创建待审批提议，不直接写文档或索引。

搜索最多返回 10 条、每条正文最多 2,000 字符；精确读取最多 8,000 字符。工具只能访问节点 `knowledgeBaseIds` 的显式作用域，并继续通过 Runtime policy、audit、middleware 和 checkpoint。

`KnowledgeWriteProposal` 持久化标题、内容、标签、来源 Xpert/conversation/Goal/Handoff/run、revision 和审批/构建状态。同一知识库、来源 run 与内容 hash 的 pending 提议会去重。`/rag/:kbId/inbox` 是唯一审批中心；聊天页只显示数量与跳转入口。

批准提议会创建受管 Markdown 文档，并以当前活动版本的精确来源快照作为基础语料；没有活动版本时使用当前资料库文档。系统随后复用已保存且通过预检的 Draft/Graph 创建 Pipeline Job。候选版本不会自动激活，且标记 `promotion_required=true`：必须运行 Evaluation Gate 并调用 `/promote`。拒绝不会创建文档、Job 或版本；Job 创建失败会回滚受管文档并保持提议 pending。

## API

- `GET /api/rag/pipeline/graph`
- `PUT /api/rag/pipeline/graph/{kb_id}`
- `POST /api/rag/pipeline/graph/{kb_id}/validate`
- `POST /api/rag/pipeline/graph/{kb_id}/preview-node`
- `POST /api/rag/pipeline/graph/{kb_id}/execute`
- `GET /api/rag/processor-capabilities`
- `POST /api/rag/pipeline/draft/{kb_id}/processor-preview`
- `GET /api/rag/vision-capabilities`
- `GET /api/rag/evaluation-sets`
- `POST /api/rag/evaluation-sets`
- `GET /api/rag/evaluation-sets/{evaluation_set_id}`
- `PATCH /api/rag/evaluation-sets/{evaluation_set_id}`
- `POST /api/rag/evaluation-sets/{evaluation_set_id}/cases`
- `PATCH /api/rag/evaluation-sets/{evaluation_set_id}/cases/{case_id}`
- `DELETE /api/rag/evaluation-sets/{evaluation_set_id}/cases/{case_id}`
- `POST /api/rag/evaluation-sets/{evaluation_set_id}/import`
- `GET /api/rag/evaluation-gate/{kb_id}`
- `PATCH /api/rag/evaluation-gate/{kb_id}`
- `POST /api/rag/evaluation-runs`
- `GET /api/rag/evaluation-runs`
- `GET /api/rag/evaluation-runs/{run_id}`
- `POST /api/rag/evaluation-runs/{run_id}/cancel`
- `GET /api/rag/knowledge-write-proposals`
- `GET /api/rag/knowledge-write-proposals/{proposal_id}`
- `PATCH /api/rag/knowledge-write-proposals/{proposal_id}`
- `POST /api/rag/knowledge-write-proposals/{proposal_id}/approve`
- `POST /api/rag/knowledge-write-proposals/{proposal_id}/reject`

- `POST /api/rag/pipeline/draft/{kb_id}/execute`
- `GET /api/rag/pipeline/jobs`
- `GET /api/rag/pipeline/jobs/{job_id}`
- `POST /api/rag/pipeline/jobs/{job_id}/cancel`
- `POST /api/rag/pipeline/jobs/{job_id}/retry`
- `GET /api/rag/pipeline/versions`
- `GET /api/rag/pipeline/versions/{version_id}`
- `POST /api/rag/pipeline/versions/{version_id}/query`
- `POST /api/rag/pipeline/versions/{version_id}/activate`
- `POST /api/rag/pipeline/versions/{version_id}/promote`

## 安全边界

- 不自动激活候选索引。
- required Promotion Gate 不允许使用失败、跨知识库、跨版本或过期评估集 revision 的运行结果激活候选。
- 知识写入提议不得绕过 Inbox；批准只构建候选，提议候选禁止直接 activate，必须评估通过后 promote。
- Graph 保存必须校验当前 revision；非法图和过期 revision 不得修改 Draft 或创建 Job。
- Graph 节点预览不持久化，最多返回 20 条截断摘要；Embedding/索引/检索节点只返回能力与 profile，不返回向量或正文。
- Processor preview 不持久化，最多返回 20 个截断块或生成项。
- 不返回本地绝对路径、vector namespace、embedding、完整 chunk/附件正文、prompt、工具输出或密钥。
- Checkpoint 只记录 ID、状态、数量、长度和错误摘要。
- 图像理解默认关闭；启用时必须通过显式模型、PDFium/Pillow 和网关预检。图片/扫描 PDF 不允许绕过候选构建与人工激活。
- 当前只保证单后端进程内一致性，不承诺多进程或分布式任务领取。

## 验收

```bash
python -m pytest server/tests/test_rag_pipeline.py -q
python -m pytest server/tests/test_rag_processor.py -q
python -m pytest server/tests/test_rag_pipeline_execute.py -q
python -m pytest server/tests/test_rag_vision.py -q
python -m pytest server/tests/test_rag_evaluation.py -q
python -m pytest server/tests/test_rag_integration.py -q
python -m pytest server/tests/test_workflow_knowledge_citation_node.py -q
```

人工验收必须覆盖候选预览、评估集标注、多版本指标对比、required Promotion Gate、首次激活、第二版本切换、历史版本回滚、失败/取消不污染 active index，以及容器重启后的 Job/Version/Evaluation Run 恢复。
