# 本地 RAG 知识库集成指南

本文件说明模镜本地 RAG 模块的架构、API、扩展方式和测试方法。该模块位于 `server/rag/`，前端入口为 `/rag`，聊天页可选择知识库进行检索增强问答。

最后更新日期：2026-08-09

> **当前状态：** `/rag` 是 ModelMirror 本地主路径。知识流水线已支持候选版本、
> 人工激活/回滚、Processor、可选视觉理解、向量 + FTS5 双索引、检索评测和
> Promotion Gate。下方按日期保留的段落是增量记录；较早段落中的“planned”
> 只代表当时状态。

## 2026-08-09 增量：知识库定向 Gold 生成与校准

`/rag/:kbId/evaluation` 可以针对一个固定 `ready / active` 索引版本生成待审核评测集。
用户可限制文档范围、语言、覆盖矩阵、数量与 seed；预检会显示稳定 source block 数量、
抽样规模、预计发送字符数和外部模型数据提示。

生成器最多向已选择模型发送 40 个受限证据块，每块最多 1,200 字符、合计最多 48,000
字符。服务端先固定 Blueprint、Gold evidence 和 query marker，再验证模型返回的 exact
anchor quote。最终 Gold 以 `source_block` 为评分主依据并保留初始 chunk ID 诊断；未知、
跨库或跨版本引用，以及不能关联固定证据标记的通用问题都会被拒绝。

生成后会自动对固定索引运行一次真实检索校准。校准只记录 Gold rank、难度分桶和错误摘要，
不会用 Top-K 结果改写 Gold。`warning` 发布需要人工确认；无答案题还必须逐题确认。任何用例
编辑都会使校准 `stale`。任务重启后复用已有草稿继续校准，不会再次调用生成模型。

该能力用于具体知识库的业务质量证据；标准双语 RAG Pack 仍只承担检索引擎回归。生成和
校准不会重建、激活或推广知识版本，也不会返回路径、embedding、完整正文或密钥。

## 2026-08-09 增量：RAG 引擎标准 Benchmark 与版本化评测

Benchmark Catalog 新增 `modelmirror-rag-foundation-bilingual-v1`。它使用 12 份
ModelMirror 自有中英双语 Markdown 语料和 40 条固定查询，离线构建 General +
Parent-child + hash embedding + 向量/FTS5 双索引的托管知识库。实例化任务可恢复，只有
索引、40 条 Gold 与不可变评测 v1 全部成功后，知识库才进入正常列表并激活初始版本。

该 Pack 是检索引擎的一致性与回归基准，只能证明分块、双索引、引用、无答案处理和版本
切换在固定合成语料上的行为。它不代表任意业务知识库的真实质量，也不应替代针对目标
知识库、固定索引版本和实际问题分布生成并人工审核的 Gold 评测集；该能力已由上方定向
Gold 生成闭环补齐。

托管 Benchmark KB 使用 `origin=benchmark_catalog`、`corpus_locked=true`：语料上传、
文档删除和 Knowledge Inbox 写入返回 409；流水线草稿、候选构建、固定评测、激活、回滚
和删除整个知识库保持可用。初始 Full-text Profile 是离线可重复基线，用户可在同一锁定
语料上构建 Recursive、Vector 或 Hybrid 候选。

Knowledge Evaluation Set 新增不可变递增版本 API：

```text
POST /api/rag/evaluation-sets/{eval_set_id}/publish
GET  /api/rag/evaluation-sets/{eval_set_id}/versions
GET  /api/rag/evaluation-sets/{eval_set_id}/versions/{version}
```

运行请求可传 `eval_set_version` 固定 Gold 快照；未传时继续使用兼容 revision 模式。
标准 Gold 使用 `match_mode=source_block`，以稳定 source block 跨分块比较，初始 chunk ID
只作诊断。`expected_no_result=true` 表示正确行为是空召回，且不得同时提供引用；报告单独
计算 No-result Accuracy 与 False-positive Rate，正样例指标不会被负样例稀释。

## 2026-07-16 增量：离线检索评估与 Promotion Gate

新增 `/rag/:kbId/evaluation` 与文件型 Evaluation Store。知识库可维护带 revision 的问题集，通过检索预览把稳定 `source_document_id`、chunk/source block 和页码标记为期望引用，再对最多 5 个不可变候选版本运行同一快照。

指标包括 Recall@1/5、MRR@10、nDCG@10、Citation Hit/Coverage、无结果率、错误率和 P95 延迟。评估执行使用 `generate_answer=false`，不会为每条测试问题调用回答模型；安全结果仅保存排名、ID、分数、耗时和错误摘要。

新增 API 族：

```text
GET/POST   /api/rag/evaluation-sets
GET/PATCH  /api/rag/evaluation-sets/{evaluation_set_id}
POST       /api/rag/evaluation-sets/{evaluation_set_id}/cases
PATCH/DELETE /api/rag/evaluation-sets/{evaluation_set_id}/cases/{case_id}
POST       /api/rag/evaluation-sets/{evaluation_set_id}/import
GET/PATCH  /api/rag/evaluation-gate/{kb_id}
POST/GET   /api/rag/evaluation-runs
GET        /api/rag/evaluation-runs/{run_id}
POST       /api/rag/evaluation-runs/{run_id}/cancel
POST       /api/rag/pipeline/versions/{version_id}/promote
```

Promotion Gate 的 `advisory` 模式只提示，`required` 模式则强制校验运行成功、知识库与候选版本一致、评估集 revision 未过期且阈值通过。旧索引与现有查询协议保持兼容；Chat、Workflow、Xpert、Goal 与 App 仍只消费当前 active version。

## 2026-07-15 增量：图像与扫描 PDF 知识理解

RAG 上传现支持 PNG、JPEG、WebP 与扫描 PDF。视觉源不会进入旧版即时索引，而是标记为 `pipeline_required`；必须在 Knowledge Canvas 中加入 `image_understanding`、显式选择支持图片输入的模型、执行候选版本并人工激活后才可检索。

```text
data_source -> image_understanding -> structured_processor
            -> recursive_chunker | parent_child_chunker
            -> embedding -> dual_index -> retrieval
```

- PDF 页面由 `pypdfium2`/PDFium 渲染，图片由 Pillow 解码；上传校验真实格式、声明 MIME、损坏文件、10MB 文件限制和 40MP 解压像素限制。PDFium 原生渲染在进程内串行执行，避免多页 worker 同时渲染时的原生崩溃；后续 VLM 请求仍按配置并发。
- `pdf_page_strategy=auto` 选择文字少于 80 字符或图片覆盖率至少 12% 的页面；画布预览可临时使用全页策略。
- VLM 沿用现有 LLM Gateway/OpenRouter，不新增供应商 SDK。严格 JSON 输出转换为 OCR、视觉描述、表格和图表块，并继续经过 General/QA/Summary Processor。
- Job 新增 `vision` stage，候选版本固定 `vision_profile`。逐页缓存以 source/config hash 隔离，失败页可重试，重启后可恢复。
- 检索和 Citation 响应保持兼容，并可增加 `page_number`、`visual_kind`、`source_block_id` 诊断字段。

安全能力摘要使用 `GET /api/rag/vision-capabilities`。Graph 节点预览最多返回 20 个截断视觉块，不返回原图、Base64、本地路径、正文全集、prompt 或密钥。第三方许可证见 `server/THIRD_PARTY_NOTICES.md`。

## 2026-07-13 增量：可执行知识流水线画布

新增 `/rag/:kbId/pipeline` 和服务端 Knowledge Pipeline Graph。画布不是新的索引执行系统：Graph 经过校验后编译为现有 Draft，随后仍由 `KnowledgePipelineExecutor` 按 `load / vision / process / chunk / embed / store` 执行并生成隔离候选版本。

真实节点固定为：

```text
data_source -> structured_processor -> recursive_chunker | parent_child_chunker
            -> embedding -> dual_index -> retrieval
```

图校验要求 DAG、端口匹配、必需阶段各一个、只有一种分块器、无孤立启用节点，并强制向量与 FTS5 双索引同时启用。`image_understanding` 是可选真实阶段，启用时只能位于数据源和结构化处理器之间。

新增 API：

```text
GET  /api/rag/pipeline/graph?kb_id=
PUT  /api/rag/pipeline/graph/{kb_id}
POST /api/rag/pipeline/graph/{kb_id}/validate
POST /api/rag/pipeline/graph/{kb_id}/preview-node
POST /api/rag/pipeline/graph/{kb_id}/execute
```

Graph 使用 `graph_revision` 乐观并发。保存成功会原子生成新的 Draft version；旧 Draft PATCH 也会同步已有图节点配置并保留坐标。Graph Execute 固定 graph revision 与 draft version，再创建既有 Pipeline Job。节点预览最多返回 20 条截断项，不持久化、不写索引，并且不会返回本地路径、完整正文、embedding、prompt 或密钥。

## 2026-07-13 增量：成熟文档处理与生成式索引

Advanced RAG V2 的候选构建现在先执行结构感知 Processor，再进入分块和向量/FTS5 双索引。TXT、Markdown 和 PDF 统一转换为 `ProcessedDocument / DocumentBlock`；块保留稳定 ID、字符偏移、标题路径和页码。Markdown 表格与代码围栏保持原结构，PDF 可移除跨页重复页眉页脚。

Processor profile 固定进 draft、Job 和 candidate version，核心字段如下：

```json
{
  "mode": "general",
  "model_id": "deepseek/deepseek-chat",
  "failure_policy": "continue_on_error",
  "extract_title": true,
  "preserve_tables": true,
  "preserve_code_blocks": true,
  "remove_repeated_headers_footers": true,
  "max_generated_items": 20
}
```

- `general`：结构块进入现有 recursive 或 parent-child 分块。
- `qa`：严格 JSON 生成问答，索引问题，命中后返回答案与来源段。
- `summary`：索引文档/章节摘要，命中后返回对应原文上下文。

新增安全 API：

```text
GET  /api/rag/processor-capabilities
POST /api/rag/pipeline/draft/{kb_id}/processor-preview
```

Preview 最多返回 20 个截断结构块或生成项，不写草稿、Job 或索引。Job 按文档保存 `pending / processing / completed / failed`、尝试次数和安全计数。重试只复用 source hash 与 processor profile 均匹配的完成产物，并只重跑失败文档；随后仍从完整成功产物原子重建两类索引。

`continue_on_error` 在至少一个文档成功时允许产生带 warning 的候选；`strict` 遇到任一文档失败都阻止 ready；所有文档失败不会产生候选版本。公开响应、日志和 checkpoint 不包含正文全集、问答全文、prompt、本地路径、embedding 或密钥。

## 2026-07-13 增量：Advanced RAG Retrieval V2

候选知识版本现在固定分块、Embedding 与检索 profile，并原子构建向量和 SQLite FTS5 双索引。`/rag` 可以配置递归字符分块或父子分段、有序分段标识符、Embedding 模型、全文/向量/混合检索、权重、Top-K、score 阈值、候选倍数和可选 Rerank。

新增安全能力摘要：

```text
GET /api/rag/retrieval-capabilities
```

以下接口接受可选 `retrieval` 对象；未传时使用活动版本固定的 profile：

```text
POST /api/rag/query
POST /api/rag/pipeline/citations
POST /api/rag/pipeline/versions/{version_id}/query
```

示例：

```json
{
  "kb_id": "kb_xxx",
  "question": "退款政策是什么？",
  "retrieval": {
    "mode": "hybrid",
    "vector_weight": 0.7,
    "fulltext_weight": 0.3,
    "top_k": 5,
    "score_threshold": 0.1,
    "candidate_multiplier": 4,
    "rerank_enabled": true,
    "rerank_provider": "auto"
  }
}
```

混合检索使用加权归一化 RRF；Rerank 按“专用 API → OpenAI-compatible LLM JSON → 融合原排序”降级。响应会增加可选的 `vector_score`、`fulltext_score`、`fused_score`、`rerank_score`、`parent_lifted` 与安全 warnings，原有 CitationAnchor 字段保持兼容。

父子分段只索引子段。召回后回答上下文使用父段，引用仍指向命中子段。向量与全文索引必须同时成功，候选版本才可 ready；任一失败会同时清理两个候选索引，不切换 active version。旧索引不自动迁移，继续使用 vector-only legacy 路径。

本实现曾以 Xpert 领域配置和本地 Dify 1.14.1 作为分块、检索异常的行为样本；
它们不是当前运行依赖。项目未复制 AGPL 或许可证不明确的容器实现。GraphRAG、
实体关系、社区摘要与图检索暂缓。

## 2026-07-12 增量：版本化 Knowledge Pipeline 执行

Pipeline Draft 现在可以创建真实 ingestion job。后台执行器固定草稿版本和显式数据源，依次执行 load、process、chunk、embed、store，生成与当前检索隔离的候选索引。候选版本必须先预览并由用户手动激活；激活旧版本即完成回滚。

新增 API：

```text
POST /api/rag/pipeline/draft/{kb_id}/execute
GET  /api/rag/pipeline/jobs?kb_id=&status=&limit=
GET  /api/rag/pipeline/jobs/{job_id}
POST /api/rag/pipeline/jobs/{job_id}/cancel
POST /api/rag/pipeline/jobs/{job_id}/retry
GET  /api/rag/pipeline/versions?kb_id=
GET  /api/rag/pipeline/versions/{version_id}
POST /api/rag/pipeline/versions/{version_id}/query
POST /api/rag/pipeline/versions/{version_id}/activate
```

执行请求可选择知识库 document IDs，也可携带最多 5 个用户明确选择的 Xpert 会话附件引用。附件会在创建 Job 时去重并快照；API 不返回快照路径、向量 namespace、完整文件正文、embedding 或密钥。普通查询统一读取 active version；没有 active version 的旧知识库保持 legacy index 兼容。

详细状态机、激活安全边界与恢复规则见 `docs/XPERT_KNOWLEDGE.md`。

## 2026-07-09 增量：Knowledge Pipeline Stage 草稿

本地 RAG 的 Knowledge Pipeline 只读视图已从单纯的 FileAsset / Artifact / Chunk / CitationAnchor 摘要，扩展为 Xpert 式四段 stage 草稿。新增 API：

```bash
curl "http://localhost:8000/api/rag/pipeline/draft?kb_id=kb_xxx"
```

响应只包含摘要元信息：

```json
{
  "kb_id": "kb_xxx",
  "stage_count": 4,
  "stages": [
    {
      "id": "stage_data_source",
      "kind": "data_source",
      "title": "数据源",
      "status": "ready",
      "item_count": 1,
      "summary": "上传文件已映射为 FileAsset 元数据。",
      "metadata": { "asset_count": 1, "document_count": 1 }
    }
  ]
}
```

该 2026-07-09 版本的四个 stage 为 `data_source`、`processor`、`chunker`、
`image_understanding`；当时 `image_understanding` 仍是 `planned` /
disabled 占位。它已在 2026-07-15 升级为可选真实阶段，当前状态以前文
“图像与扫描 PDF 知识理解”为准。安全响应仍不返回本地文件绝对路径、完整
chunk 文本、embedding、prompt 或密钥。

## 2026-07-08 增量：Workflow CitationAnchor 节点

Classic workflow 已新增 `knowledge_citation` 节点，复用本地 RAG Knowledge Pipeline 的 citation 生成能力。节点读取 `queryVariable` 中的文本，使用可选 `knowledgeBaseId` 和 `top_k` 调用 `RagService.create_pipeline_citations(...)`，并把结果写入 `outputVariable`：

```json
{"citations":[...],"citation_count":1}
```

该节点只输出 CitationAnchor 摘要，包括 `chunk_id`、`document_name`、`score`、`snippet` 等字段；不会返回本地文件绝对路径、embedding、完整上传文件内容或密钥。它不改变上传、切分、检索、向量存储、`/api/rag/query` 或聊天 RAG 行为，只是让 workflow 和后续 Agent 能引用同一套只读知识元数据视图。
维护人：模镜团队

## 1. 概述

本地 RAG 模块提供版本化知识库能力：

- 创建和删除知识库。
- 上传 TXT、Markdown、PDF、PNG、JPEG 和 WebP；图片与扫描 PDF 需要执行
  含视觉阶段的知识流水线。
- 结构化解析、分段、Embedding，并原子构建向量与 FTS5 候选索引。
- 预览、评测并人工激活候选版本，支持回滚。
- 基于知识库检索片段并生成回答。
- 在面试间选择知识库，让回答附带引用来源。

组件关系如下：

```text
用户浏览器
  |
  | /rag 管理资料库
  v
React RagPage
  |
  | /api/rag/*
  v
FastAPI RAG Router
  |
  +--> RagService
        |
        +--> document_parser.py  解析 TXT / Markdown / PDF
        +--> splitter.py         本地递归字符 / 父子分段与字符偏移
        +--> embedder.py         OpenAI-compatible Embedding API，缺失 key 时 hash fallback
        +--> vector_store.py     ChromaDB 持久化，缺失依赖时 LocalJsonVectorStore fallback
        +--> lexical_store.py    SQLite FTS5 与中英文规范化词元
        +--> reranker.py         专用 Rerank API / LLM JSON fail-open
        +--> OpenRouter Chat     生成 RAG 回答，缺失 key 时抽取式 fallback
```

默认目录：

```text
server/rag/uploads/       # 原始上传文件
server/rag/storage/       # metadata.json、本地 fallback 索引
server/rag/storage/chroma_db 或 CHROMA_DB_PATH # ChromaDB 持久化目录
```

## 2. 如何添加新的文件格式支持

文件解析入口是 `server/rag/document_parser.py`。

添加新格式的步骤：

1. 在 `SUPPORTED_EXTENSIONS` 中加入扩展名，例如 `.csv`。
2. 在 `parse_document()` 中增加分支：

```python
if extension == ".csv":
    return _read_csv(path)
```

3. 实现解析函数，将文件内容转换为纯文本：

```python
def _read_csv(path: Path) -> str:
    rows = path.read_text(encoding="utf-8").splitlines()
    text = "\n".join(rows)
    return _ensure_text(text, path.name)
```

4. 为新格式补充测试：上传对应文件，确认返回 `chunk_count > 0`，并能通过 `/api/rag/query` 检索到内容。

注意：解析函数只负责“转纯文本”，不要在 parser 层做向量化或模型调用。

## 3. 后端 API 文档

所有端点前缀均为 `/api/rag`。

### 创建知识库

```bash
curl -X POST http://localhost:8000/api/rag/knowledge_bases \
  -H "Content-Type: application/json" \
  -d '{"name":"产品手册"}'
```

响应：

```json
{
  "id": "kb_xxx",
  "name": "产品手册",
  "document_count": 0,
  "created_at": 1781600000.0,
  "updated_at": 1781600000.0
}
```

### 列出知识库

```bash
curl http://localhost:8000/api/rag/knowledge_bases
```

响应：

```json
{
  "knowledge_bases": [
    {
      "id": "kb_xxx",
      "name": "产品手册",
      "document_count": 2,
      "created_at": 1781600000.0,
      "updated_at": 1781600100.0
    }
  ]
}
```

### 删除知识库

```bash
curl -X DELETE http://localhost:8000/api/rag/knowledge_bases/kb_xxx
```

响应：

```json
{ "ok": true }
```

### 上传文档

支持 `.txt`、`.md`、`.markdown`、`.pdf`，单文件上限 10MB。

```bash
curl -X POST http://localhost:8000/api/rag/knowledge_bases/kb_xxx/documents \
  -F "file=@测试文档.txt"
```

响应：

```json
{
  "id": "doc_xxx",
  "kb_id": "kb_xxx",
  "filename": "测试文档.txt",
  "size": 128,
  "chunk_count": 1,
  "created_at": 1781600000.0
}
```

### 列出文档

```bash
curl http://localhost:8000/api/rag/knowledge_bases/kb_xxx/documents
```

响应：

```json
{
  "documents": [
    {
      "id": "doc_xxx",
      "kb_id": "kb_xxx",
      "filename": "测试文档.txt",
      "size": 128,
      "chunk_count": 1,
      "created_at": 1781600000.0
    }
  ]
}
```

### 删除文档

```bash
curl -X DELETE http://localhost:8000/api/rag/documents/doc_xxx
```

响应：

```json
{ "ok": true }
```

### 查询知识库

```bash
curl -X POST http://localhost:8000/api/rag/query \
  -H "Content-Type: application/json" \
  -d '{"kb_id":"kb_xxx","question":"什么是模镜？","top_k":4}'
```

响应：

```json
{
  "answer": "根据知识库资料：模镜是一个 AI 平台。",
  "sources": [
    {
      "chunk_id": "doc_xxx_chunk_0",
      "doc_id": "doc_xxx",
      "document_name": "测试文档.txt",
      "text": "模镜是一个 AI 平台。它支持多种模型。",
      "score": 0.83
    }
  ]
}
```

常见错误：

- `400`：文件格式不支持、文档无法解析、问题为空。
- `404`：知识库或文档不存在。
- `413`：上传文件超过 10MB。

## 4. 向量数据库配置与维护

默认优先使用 ChromaDB：

```bash
pip install chromadb langchain-text-splitters pdfplumber PyPDF2 python-multipart
```

相关环境变量：

```bash
RAG_VECTOR_STORE=chroma
CHROMA_DB_PATH=./chroma_db
RAG_STORAGE_DIR=server/rag/storage
RAG_UPLOAD_DIR=server/rag/uploads
RAG_CHUNK_SIZE=500
RAG_CHUNK_OVERLAP=50
```

Embedding 配置：

```bash
EMBEDDING_API_BASE=https://api.openai.com/v1
EMBEDDING_API_KEY=sk-...
EMBEDDING_MODEL=text-embedding-3-small
```

如果没有 `EMBEDDING_API_KEY`，系统会自动使用确定性 hash embedding，便于本地开发和 CI 测试。生产环境建议配置真实 Embedding API。

RAG 回答生成使用 OpenRouter：

```bash
OPENROUTER_API_KEY=sk-or-...
RAG_LLM_MODEL=deepseek/deepseek-chat
```

如果没有 `OPENROUTER_API_KEY`，查询接口会返回抽取式答案，即直接基于最相关片段组织回答。

备份建议：

- 备份 `server/rag/storage/metadata.json`。
- 备份 `CHROMA_DB_PATH` 指向的 ChromaDB 目录。
- 备份 `server/rag/uploads/` 原始文件。

## 5. 测试指南

后端语法检查：

```bash
python -m py_compile server/rag/*.py
```

RAG 集成测试：

```bash
python -m pytest server/tests/test_rag_integration.py -q
python -m pytest server/tests/test_rag.py -q
```

测试特点：

- 使用临时目录保存 metadata、uploads 和本地向量索引。
- 使用 hash embedding，不依赖外部网络。
- 禁用 LLM 生成，返回抽取式答案，确保 CI 可重复。

新增测试建议：

1. 构造一个临时知识库。
2. 上传最小可解析文件。
3. 查询一个能命中文档关键词的问题。
4. 验证 `answer` 和 `sources`。
5. 清理文档和知识库。


## 2026-07-08 增量：知识流水线 Beta

本地 RAG 现在额外提供一层只读 Knowledge Pipeline 元数据视图，用于对齐 Xpert 的知识产物模型。该层不会改变上传、切分、embedding、向量存储、检索测试或 `/api/rag/query` 响应协议。

新增模型映射：

- `FileAsset`：由已上传 document 派生，包含文件名、大小、扩展名、mime、知识库 ID 与 document ID，不返回 `stored_path`。
- `Artifact`：由 document 派生，表示可被检索和引用的文档产物，包含 `file_asset_id`、标题和 `chunk_count`。
- `KnowledgeChunk`：由向量索引中的 chunk 派生，只返回 chunk ID、序号、文本摘要和字符长度，不返回 embedding。
- `CitationAnchor`：由现有检索结果派生，包含 chunk ID、document 名称、score 和 snippet。

新增只读 API：

```bash
curl 'http://localhost:8000/api/rag/pipeline/assets?kb_id=kb_xxx'
curl 'http://localhost:8000/api/rag/pipeline/artifacts?kb_id=kb_xxx'
curl 'http://localhost:8000/api/rag/pipeline/artifacts/artifact_doc_xxx/chunks'
curl -X POST http://localhost:8000/api/rag/pipeline/citations \
  -H 'Content-Type: application/json' \
  -d '{"kb_id":"kb_xxx","question":"如何使用资料？","top_k":4}'
```

前端 `/rag` 的“知识流水线 Beta”折叠区已展示当前知识库的数据源、处理器、分块器、图像理解 stage 草稿，并保留 assets / artifacts / chunks 计数和最近 artifacts。后续知识类工作流节点、Agent 引用和 citation 面板会基于这层 schema 继续扩展。

最后更新日期：2026-07-16

## 2026-07-10 Update: Knowledge Pipeline Draft Config And Preflight

The local RAG pipeline now exposes a safe editable draft layer:

- `GET /api/rag/pipeline/draft?kb_id=...` returns `draft_id`, `version`, `updated_at`, `editable`, stages, counts, and safe stage config.
- `PATCH /api/rag/pipeline/draft/{kb_id}` persists safe draft fields only: uploaded file source mode, local parser, local recursive character chunking, `chunk_size`, and `chunk_overlap`.
- `POST /api/rag/pipeline/draft/{kb_id}/preflight` returns readiness, warnings, per-stage checks, and document/artifact/chunk counts.

Validation boundaries: `chunk_size` must stay between 100 and 4000, and `chunk_overlap` must be non-negative and smaller than `chunk_size`. Image understanding is optional, but enabling it requires an explicit vision model and the renderer/model-gateway preflight. Draft changes alone do not rebuild indexes or change chat/workflow retrieval until a candidate version is executed and activated. Responses must not expose local stored paths, full chunk text, images, embeddings, prompts, tool outputs, or secrets.

Last updated: 2026-07-16

## 2026-07-16 增量：Knowledge Agent 审批写入

`workflow_agent` 现在可以在 Runtime 工具模式下显式启用知识读取或写入提议，并固定 1 至 5 个知识库作用域。`knowledge_search/get/cite/propose_write` 复用活动版本检索、Runtime policy/audit/middleware 和现有 Pipeline executor；模型不能访问节点未声明的知识库。

写入采用“提议 -> Inbox 审批 -> 候选构建 -> Evaluation Gate -> Promote”流程：

1. 模型调用 `knowledge_propose_write` 创建 pending 提议，活动索引不变。
2. 管理者在 `/rag/:kbId/inbox` 编辑、批准或拒绝，修改使用 revision 乐观并发。
3. 批准后以活动版本来源快照加受管 Markdown 文档创建候选 Job；拒绝不产生任何索引副作用。
4. 提议候选不能直接激活，必须评估通过并由 `/promote` 切换活动版本。

新增接口：

```text
GET   /api/rag/knowledge-write-proposals
GET   /api/rag/knowledge-write-proposals/{proposal_id}
PATCH /api/rag/knowledge-write-proposals/{proposal_id}
POST  /api/rag/knowledge-write-proposals/{proposal_id}/approve
POST  /api/rag/knowledge-write-proposals/{proposal_id}/reject
```

工具响应、审计和 checkpoint 只保留 ID、状态、分数诊断、长度与安全错误摘要，不保存完整知识正文、提议正文、prompt、路径、embedding 或密钥。GraphRAG、实体关系抽取、社区摘要和图检索继续暂缓。
