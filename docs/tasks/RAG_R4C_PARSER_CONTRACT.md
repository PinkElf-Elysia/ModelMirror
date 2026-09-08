# RAG 4C：统一解析合同与 Golden Fixtures

## 目标、基线与权限

- 唯一目标：解析 → 结构化 Block → Chunk → 索引输入的结构与失败语义正确性。
- 基线：PR #367 已合并；`origin/main@f67f4013ad081ad27c31ab385dd6f5e1a3627edc`。
- 分支：`codex/rag-p1-r4c-parser-fixtures`；工作树：`C:/tmp/modelmirror-rag-r4c-parser-fixtures`；创建时干净。
- 不修改主 checkout、历史索引/评测、共享容器/数据；不调参，不新增生产依赖，不调用真实 Provider。
- 本卡记录基线 SHA，不声称包含未来最终提交自身 SHA。下列测试、预览与 Git 状态是发布前验收快照；随后用户已授权 Commit、Push 和创建 PR，实际发布状态以 Git/PR 为准。Merge、Deploy 未授权。

## 已核实入口与待证伪项

| 结论 | 证据 | 判定 |
| --- | --- | --- |
| RAG PDF 绕过共享受限 Worker，在主进程私有提取 | `server/rag/document_processor.py::_pdf_pages` | 已证实 |
| 共享 PDF Worker 有 spawn、超时、内存及输出预算 | `server/file_assets/document_parser.py::_parse_text_pdf` | 已证实，复用并回归 |
| ProcessedDocument 已拒绝空 Block；但未携带结构化变换回执 | `StructuredDocumentProcessor.process` | 已证实，保留空值护栏 |
| PDF 页边清理只返回自然语言警告 | `_clean_pdf_pages` | 已证实，新增脱敏操作记录 |
| XLSX 已保留 sheet、A1 row_range 和文本内坐标；不需要另造 cell_range | `_parse_xlsx_workbook`、`_shared_section_blocks` | 已证实，fixture 验证空单元格及端到端传递 |
| 聚合 parser 被固定为 legacy，4B 未获得完整内容资格 | `RagService._content_index_contract` | 已证实，4C 以实际 profile/receipt 而非标签启用 |
| 部分处理失败、布局不明或缺失回执能否绕过激活/评测 | service/evaluation 资格边界 | 红测复现，解析回执及实际 artifact 校验后阻断 |

## 实施边界

顺序小批次，红测先于对应行为实现：

1. 共享受限 PDF 解析、保守坐标布局、清理回执及固定 fixtures。
2. RAG processor/版本 profile、artifact 回执与失败/降级资格阻断。
3. Chunk/source metadata 链路验证、兼容回归、Help 与独立零调用 UI 验收。

允许范围：共享 file-assets 解析模块；RAG parser/processor/service 与必要的 metadata/execution/evaluation 接线；相应 tests/fixtures；RAG Help 和工程证据。
最终超过 5 个文件是因为共享解析、调用者和其发布资格必须一起验证；各小批次仅改一个合同，不混入在线排序、生成或全库迁移。
路由兼容：新增 parser/transform receipt 和明确 error_code；历史记录只读投影，不改写。
持久化：仅新建临时测试/候选 artifact；无历史数据迁移。现有 source/page/sheet/row_range 优先复用。

## 验收与风险

- 正常路径：Markdown 标题/代码/表格，PDF 页码/安全双栏/可靠表格，XLSX sheet/坐标/空单元格；Chunk 身份与预算不回退。
- 失败路径：空/损坏/部分页失败/扫描无 OCR/不确定布局必须明确失败或 diagnostic-only，不能获得新晋级证据。
- 资源路径：PDF 只能在共享可终止 Worker 运行；异常、超时、输出/内存边界回归；不得真实 OCR。
- 主要风险：误判复杂 PDF 布局、共享 Chat parser 回归、历史 profile 被默认升级。无法证明阅读顺序时降级，不猜测。
- Receipt 不记录删除的原文、路径、密钥；无密钥哈希仅作完整性绑定，不是匿名化或认证签名。
- Fixtures 为仓库自有生成材料，固定 manifest/checksum；注入的损坏/部分页失败与真实几何 fixture 分开标识。

## 验证矩阵

| 检查 | 命令/方式 | 状态 |
| --- | --- | --- |
| 红/绿测 | `python -m pytest server/tests/test_rag_parser_contract.py server/tests/test_rag_parser_pipeline_contract.py -q` | 首轮 parser 11 红 / pipeline 9 红；最终 38 passed |
| 受影响回归 | processor、pipeline_execute、retrieval_scoring/diversity、vision、file_asset_extended_formats | 包含于下列 50 文件矩阵，全部通过 |
| 模块矩阵 | RAG/Knowledge/Benchmark/File Asset | 934 passed、1 skipped（无回归） |
| 最后 Formal 补测 | `python -m pytest server/tests/test_rag_evaluation.py server/tests/test_rag_evaluation_integrity.py server/tests/test_rag_parser_pipeline_contract.py -q` | 114 passed；补测之后无生产代码改动 |
| 后端全量 | `python -m pytest server/tests/ -q`，失败与本基线对照 | 6373 passed、29 skipped、21 failed；同一原始基线重跑 5 文件得到 76 passed、完全相同 21 failed |
| 前端 | RAG/Canvas/Help 定向、全测、build | 定向 64 passed；全量 982 passed、2 failed；基线目录单文件 52 passed、相同 2 failed；最终 build passed |
| 静态与秘密 | diff-check、语法、受审范围与秘密扫描 | passed；未发现匹配的凭据/私钥特征；不是秘密不存在的形式证明 |
| 发布前截图门禁 | `npm.cmd run verify:help-images` | 初次 8 处资产错误；修复格式与旧图归档后 passed，不放宽验证规则 |
| 离线验收 | 新隔离预览，严格 Fake/全文，零真实调用 | 正常、布局降级、扫描无 OCR、空检索、刷新持久化与 Help 图片均通过；最终第四轮退出验收仍待 4C 合并 |

全量后端是在最后新增的全文 Formal 测试参数之前启动并收集；上表不将随后 114 项与全量数字相加伪称一次全量。最后增量仅修改测试辅助函数与断言，生产代码已被全量覆盖。

## 已关闭的证伪项

- API 响应模型曾丢弃 processor receipt / error_code；Job 和 Preview 共用安全投影后保留。
- 画布 processor/chunker preview 曾丢失降级信息；metadata 与 warnings 现均可重放。
- 可选或低报 XLSX dimension 曾隐去有效行；复用现有边界做实际行扫描，不扩大 Data X 或解析上限。
- 相邻文字带重叠、重复表格页边误报删除、多表格渲染绕过单页总预算均有独立红→绿测试。
- 解析回执与 source/artifact/版本行不一致、缺失或降级时阻断新资格；重新封存旧 Formal 清单仍不能绕过。
- 历史 Formal 测试辅助函数会把全文配置改成 vector；新增模式断言先复现失败，再显式指定测试模式。
  现在已发布测试 Gold → 认证 API → 两个实际全文候选 → 84 次配对本地检索 → 读取回执 → 篡改后失去可重放资格完整通过。
  这是人工构造的本地集成 fixture，**不是业务 Gold、真实模型调用或质量晋级证据**；原 hash/vector 拒绝断言保留。

## 基线与环境失败

- 后端失败 node ID 集合与 `f67f4013` 完全一致：14 Agency Worker 构建产物缺失，3 Expert Team Worker 构建产物缺失，3 Skill Node/TypeScript 环境失败，1 模型路由性能上限。
- 性能上限 10 ms：当前观测 16.098 ms，基线 14.310 ms。两次均失败，但没有足够统计证据宣称性能等价；相关生产模块无 Diff，本批不改动该门槛。
- 前端模型目录：计数预期 509 / 实际 507；inactive 预期 6 / 实际 8。相同源码基线同样失败，不修改目录数据来让本批通过。
- 初始预览仅 internal 网络无法从宿主机访问；新增独立固定目标入口代理，后端继续只连接 internal 网络并拦截出站 HTTP。
  此为私有验收 harness，不进入产品 Diff；未因此修补 RAG 逻辑。
- 验收快照时 CI 尚未运行。提交授权后已刷新上游，仍为 `f67f4013`，无需 rebase；后续 CI 状态以 PR 检查为准，不能据本地专项绿测宣称 CI/合并门禁全绿。

## 独立预览与证据位置

- Compose project：`modelmirror-rag-r4c-preview-20260908`；前端 `http://127.0.0.1:15174/rag`，固定 API 入口 `127.0.0.1:18175`。
- 后端无共享挂载、无凭据，临时数据在容器 `/tmp/r4c-data`；旧 15164 预览和主 checkout 不变。
- 正常候选：5 个来源 / 18 Blocks / 14 Chunks，parser/lexical/chunker current；两项页边操作含页码、数量和哈希，无删除原文。
- 降级候选：歧义布局 1 Chunk + 扫描文档明确失败；`rag_layout_degraded` / `scanned_pdf_requires_ocr` 在 UI 可见，首次激活禁用，刷新仍成立。
- 两个候选均 `ready` 且 `active=false`；仅版本级本地全文查询，不运行浏览器 Formal、不调用真实 Provider、不自动激活。
- Help 三张截图来自本次实际页面；8 个变更后的后端生产文件与预览容器逐字节 SHA-256 一致。
- 脱敏摘要：[RAG_R4C_PARSER_EVIDENCE.json](RAG_R4C_PARSER_EVIDENCE.json)。详细 JUnit/构建日志在 `C:/tmp/rag-r4c-20260908/evidence/`，不提交大型日志或运行数据。
- 报告归档后仅删除本批 `network=none` 测试容器，临时测试状态可按 fixtures 重建；独立预览和全部报告保留。
- 发布前验收工作树：27 个 tracked 修改、23 个 intended untracked 文件，共 50 个文件；当时暂存为空，上游为 `f67f4013`，无 rebase/commit/push/PR/merge/deploy。上述状态不覆盖随后经授权执行的提交与 PR 创建。
- 提交收口补跑 CI 同款截图检查发现：3 张浏览器实际 JPEG 被命名为 PNG，替换后还留有 2 张未引用旧图。仅裁去截图无关边缘并编码真实 PNG，保留原始捕获及裁剪坐标/哈希；未合成、改写截图文字、重采样或改变颜色。新图均宽 800px、最大 187,438 bytes，检查通过。两张 `436d2453` 历史截图逐字节移至 `docs/help-center/evidence/screenshots/436d2453/`，未删除证据。最终范围是在上述 50 文件之外增加这 2 项重命名；生产代码不变。
- 截图修正后 `npm.cmd run typecheck` 与 `npm.cmd run build` 再次通过（构建 23.06 秒）；首次 typecheck 的 `tsbuildinfo` 写入被沙箱以 EPERM 拒绝，在授权工作树写权限下重跑，不更改类型检查配置。构建日志为私有 `frontend-build-publication.log`。
- 截图导出后另跑 `npm.cmd run test -- --run --maxWorkers=1 src/content/help-center/helpContent.test.ts`：14 passed，作为独立补测，不并入先前全量数字。
- 完整暂存后 PDF fixture 被宿主机 `diff=astextplain` 当成普通文本，触发 PDF xref/字典固有尾空格检查。仅在 fixture 目录增加 `.gitattributes`，将 PDF/XLSX 声明为 binary，防止文本过滤或换行转换破坏固定字节；不删改 PDF 空格、不更改 checksum、不关闭源码检查。暂存 blob 与原始 fixture 逐项比对。最终为 51 个源码/测试/文档/资产文件加 2 项旧截图重命名。

## 回退与完成定义

仅撤销本 PR 源码/测试/文档；不切换活动版本，不删除新旧索引，无数据回滚。
只有所有声明检查完成且本轮回归为零后才能声称 4C 完成；环境/既有失败、未运行门禁分别报告，不以窄测替代端到端证据。
