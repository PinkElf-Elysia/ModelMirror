# RAG 全文 V2：帮助实操与复放证据

## 基线与范围

- 日期：2026-09-07；冻结基线 `436d24535c6f0ad80b3a4f18830fb834f90a7b95` 加本次受审 4B 增量。不是文档自身未来提交 SHA。
- 工作树：`C:/tmp/modelmirror-rag-r4b-lexical-v2`，分支 `codex/rag-p1-r4b-lexical-v2`。
- 文章：`client/src/content/help-center/articles/understand-rag-content-contract.md`；入口 `/help/understand-rag-content-contract`。
- 首次操作和公开截图视口为 923 × 889；写作后复放沿用应用当前默认视口，最终检查为 1433 × 898。未为截图调整视口、伪造内容或修改页面样式。不是跨断点布局一致性的证明。
- 入口：本任务独立预览 `/rag`；后端只在 internal Docker network 中运行，零挂载，独立临时 storage/uploads。浏览器经仅限 loopback 的 API relay 访问，转接耗时不作延迟指标。
- 使用 General、全文模式、关闭 Vision/Rerank，环境 `RAG_DISABLE_LLM=1`，网关地址及密钥为空。未运行真实 Provider、Formal、Tuner、激活或部署。

## 自有材料与隔离

样例 `lexical-v2-walkthrough.md` 为本任务自写的 583 字节 Markdown，包含完整工单 ID、有序短语、中文备份段落和近邻干扰段落；不含真实用户材料。
SHA-256：`0d4115b4a94b716e07ed4bf2d91df50bfca93dbce4b3f39a37c35f1c3a4c4ee0`。

- 第一次操作：`kb_678ddc1f02524fc28991cc7e28eeb254`，Job `kpjob_aa04d698b2f94be2b296d2c4da9ed324`，一次构建得到 4 Chunk。
- 写作后清空任务上下文的方法：新建空白知识库，不复用第一次的草稿、Artifact、候选或查询结果，也不删除用户资料。
- 教程复放：`kb_3df246d589824d0d8eccf25aca1303d4`，Doc `doc_881a8bc6607a457d8016becf29e68ebe`，Job `kpjob_651d2a0598f542a09dc3243cbfa53443`，Version `kpv_19a9dadb7826448187a2fa3946304e30`。
- 两个任务自有知识库均无活动版本。它们不作为真实模型质量证据或任何后续 Formal 数据。

## 按成稿教程复放

| 步骤 | 页面动作与实际结果 |
| --- | --- |
| 1 | “新建知识库” → “创建”；新知识库显示 0 份文档。 |
| 2 | “选择文件（可多选）”上传自有样例；显示“1 个源文件已保存并等待流水线”和“等待流水线处理”。 |
| 3 | 展开“知识流水线”；默认“递归估算 Token 分块”，500 估算 Token、目标总重叠 50。 |
| 4 | 保持 General、图像理解 disabled、Rerank off，仅切换“检索模式”为“全文检索”；Top-K 保持 5、阈值保持未配置。 |
| 5 | “保存草稿”；Draft v2，分块/全文 current、解析 legacy_read_only，向量后端“不适用（fulltext-only）”，提示不能首次激活或晋级。 |
| 6 | “运行预检”；显示 ready，明确此时尚未生成 Chunk。 |
| 7 | “执行草稿”一次；attempt 1、候选就绪，9 Block、0 Generated Item、4 Chunk；vector not applicable、fulltext ready、lexical-v2，“激活”禁用。 |
| 8 | 输入 `NOVA-2042` 后选择版本“预览”；命中“工单与回滚”章节，初始 1/40、普通词 0/0、规则通过 1、送入后续排序 1。 |

补充复放：`NOVA-9999` 返回无来源，回执仍显示 0/40、0/0、0、0。打开“流水线画布”并选择“递归分块器”，可见 500/50；刷新后 Graph r1 / Draft v2、fulltext / Top-5 和预算保持。未执行第二个 Job。

首次操作另验证：`"red blue storage"` 只命中正确词序段落；中文“数据库备份恢复”初始召回 2 条，要求普通词命中 4/6，经规则过滤剩 1 条正确来源。

页面操作均为真实 UI 交互；随后仅用只读 API 核对上述复放候选的 active=false、active_version_id=null、embedding effective provider=none / dimension=0 / status=not_applicable，以及下列索引回执：

- indexed_tokens_hash：`6075b5b04d66b4cbaa678955d4367722ee530c61cc315f0594de31172af31748`
- chunk_sequence_hash：`a59b5c96ef8f867a0236d476924b709f6703b2e15e1b75b4eb28ace96a0af10a`
- candidate_namespace_fingerprint：`dc74045541a7b10057687ec85978793074c060408f268f7cd871a8f06041287b`

## 实操发现与恢复

- 首次空结果分支漏掉全文回执：新增 3 条失败回归，最小修复仅把现有回执传入空结果响应；重新启动本任务后端后，两次独立知识库的不存在 ID 均保留回执。
- 文档清单显示“等待流水线处理”和“当前索引片段尚未激活”并不否定候选已构建；教程改为以执行任务和索引版本区分状态。
- 本地答案关闭时，既有通用提示仍含“Managed Provider 调用未成功”。这不是实际发生 Provider 调用的证据；教程明确区分本地非模型结果，不在 4B 改动生成层。后续生成层工作应校正该提示的具体原因。
- internal network 的常规端口映射未可达，使用仅连接该任务容器 loopback 的 relay；未放宽外网隔离。不是生产部署方案。
- 浏览器文件选择器存在一次工具等待偏长；上传最终只生成一份文档，无自动重复上传。

## 图片与帮助入口

公开图片均来自首次真实预览，原始浏览器字节为 JPEG；使用已有开发工具转编码为 PNG，不裁剪、不缩放、不改内容，无生产依赖新增。

| 文件（`client/public/help-center/436d2453/`） | 大小与尺寸 | SHA-256 |
| --- | --- | --- |
| `rag-estimated-token-budget.png` | 104618 字节，923 × 889 | `153d289ccdeb61f17e1e5f8ba4da39eb86c3708027d27f208bf13a74c3a72607` |
| `rag-diagnostic-candidate.png` | 121394 字节，923 × 889 | `8f044a04e310dbd1dbae8cc6eb9418daee9a71b84630f84829b664c8da30003f` |

旧 `92b7e6df` 下同名两图因已不被文章引用而退役，可从 Git 基线恢复。帮助首页搜索“估算 Token”可进入文章；两张图的自然尺寸及 loaded=true 已通过 DOM 只读观察核实，文章费用说明和编号步骤已目视检查。

`npm.cmd run verify:help-images` 通过；Help 与相关 UI 定向 61 项通过，生产构建通过。完整测试数字及基线差异以 `docs/RAG_R4B_CHECKPOINT_20260907.json` 为准。

收尾后已关闭临时标签页、Vite、loopback relay，删除经 ID/标签复核的两个任务容器与其空闲 internal network。两个自有知识库的临时状态保存于 `C:/tmp/rag-r4b-20260907/preview-runtime-archive`（8 文件、201822 字节）；验收 XML/JSON 已单独归档。没有删除共享数据或活动索引。

## 未验证边界

没有真实模型质量、生产性能、共享数据迁移、复杂 PDF/OCR 或 4C 解析保证。内容合同仍 partial/diagnostic，不能把本次预览当作 Formal 或晋级通过。全文的有界空结果不证明全库答案不存在；无密钥哈希只标识完整性，不提供匿名化或特权写入防护。
