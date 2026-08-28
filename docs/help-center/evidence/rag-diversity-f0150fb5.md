# RAG P0 PR3：rebase 后验收记录

日期：2026-08-27。基线：`f0150fb5daebcf5a4a70b0f350e6129dae7ff8d0`。
本记录对应该基线上的 PR3 增量，不表示上述基线已包含本功能。

## 范围与兼容

- V3 在绝对相关性过滤后，依次执行规范化文本去重、source-block 主引用选择、可证明的重叠文本合并、文档上限和 Top-K。
- `max_chunks_per_document` 默认为 2，可设置为 1–50；不同文档和不同 source block 的独立候选保持上游顺序，不引入 MMR 或新评分。
- source-block 去重只选一个最高分主引用，但保留组内兄弟用于安全合并；无法证明字符跨度、文本重叠及页/结构身份一致时不合并。主 citation ID 保留，额外合并项记录为 `merged_chunk_ids`。
- `candidate_stage_counts` 记录 raw、threshold、text_dedup、source_block_dedup、overlap_merge、document_limit、final；另记 `overlap_merged_chunk_count`。
- V2 继续走旧 selection 路径，不新增默认文档上限。不调参、不新增依赖、不修改活动索引或历史数据。

本批涉及 3 个后端文件、1 个专项测试、3 个帮助内容/展示文件及本记录。超过默认 5 文件的原因是公开字段、实际查询、纯选择器、证伪测试与同批帮助证据必须保持一致；未拆入其他行为合同。

## 自动验收

后端统一在 `modelmirror-server:latest` 一次性测试容器执行；`--network none`，仅挂载本批隔离工作树，不传入凭据，不挂载共享数据或 Docker socket。

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 专项与兼容 | `python -m pytest server/tests/test_rag_retrieval_diversity.py server/tests/test_rag_retrieval_scoring.py server/tests/test_rag_retrieval_v2.py -q` | 通过：56 passed |
| RAG / Knowledge / Benchmark | `python -m pytest server/tests/test_rag*.py server/tests/test_*knowledge*.py server/tests/test_benchmark*.py -q`（容器内 shell 展开） | 通过：337 passed，4 warnings |
| 帮助内容 | `npm.cmd run test:run -- src/content/help-center/helpContent.test.ts` | 通过：5 passed |
| 前端全测 | `npm.cmd run test:run` | 通过：123 files，750 tests |
| 类型检查 | `npm.cmd run typecheck` | 通过；普通权限受 `C:\tmp` 两份生成型 tsbuildinfo 缓存 EPERM 阻断，删除精确缓存并仅提升该命令权限后通过，未改类型配置 |
| 生产构建 | `npm.cmd run build` | 通过；保留既有 large-chunk warning |
| 全量后端（最终 Diff） | `python -m pytest server/tests/ -q` | 失败：5020 passed，20 failed，29 skipped，6 warnings，1263.80s |
| 干净主线失败归因 | 四个失败文件在同一基线的干净对照工作树复跑 | 通过归因：相同 20 failed，69 passed，4 warnings |

全量中的 20 条失败位于 `test_agency_worker_bridge.py`、`test_expert_team_agency_execution.py`、`test_skill_finder.py`、`test_skill_semantic_rerank.py`。同一基线的干净对照工作树精确复现相同 20 条：`server/orchestration_worker/dist/main.js` 与该目录下的 TypeScript 依赖均不存在，导致 Worker 报 `Agency worker build output is unavailable`，并使三个 Node/TypeScript 对照测试无法加载。本批不修改这些模块、不跳过其测试。

## 最终证伪攻击

提交前不把既有绿测视为正确性证明，额外攻击了三个增量假设，并先观察红灯再修复：

1. **V2 字段隔离**：显式提交 `max_chunks_per_document` 时，通用解析器原先会保留该 V3 字段；进一步通过 `absolute_relevance_v1` 覆盖时，V2 执行回执也会错误宣称存在文档上限。修复后 V2 配置出口始终移除该不可执行字段，不改变旧选择器和分数语义。
2. **重叠链闭包**：最高分主 chunk 位于链尾时，一次顺序扫描会跳过尚未与主 chunk 相交、但可在中间 chunk 合并后证明连续的链首。修复为有界多轮闭包扫描，并对 3 个 chunk 的全部 6 种输入排列验证完整合并。
3. **可重放性**：上述测试同时锁定主 citation ID、字符范围、合并 ID 集、合并数量以及 V2 payload，避免“内容看似正确但回执或兼容合同漂移”。

这三项修复后重新运行 56 条专项/兼容测试与 337 条 RAG/Knowledge/Benchmark 回归，均通过。真实 Provider 调用未执行：本批变更位于 Provider 已返回候选之后的确定性选择阶段，外部 embedding/rerank 响应不能增加对这三个缺陷的判别力，反而会引入非确定性与额度消耗。

## Help Center Impact

- 影响：用户可见的 V3 去重及默认每文档最多 2 条结果。
- 更新：`client/src/content/help-center/index.ts` 的 RAG 条目；单条目验证基线通过 `HelpArticlePage` 展示，不改写其他条目的历史验证基线。
- 独立前端预览：本批工作树，loopback 端口 15432，无后端服务重建。
- 可见操作：帮助首页 → 工作台与设置下的「RAG 知识库」→ 查看 V3 说明、日期和基线；返回首页后再次通过可见链接重放。
- 实际结果：标题、去重说明、默认 2 条、`2026-08-27`、`f0150fb5` 均正确显示；返回帮助首页后再次通过可见链接重放成功，页面 error 日志为空。
- 边界：本项只验证帮助页面及导航；未通过产品 UI 构建索引或执行检索，不等于真实模型质量或 P0 最终集成验收。未上传资料、未调用真实 Provider、未发送私有语料。

## 提交边界与回退

分支已重新同步至 `origin/main@f0150fb5`，保留上游 Provider Batch 帮助增量和本批 RAG 增量；既有 `stash@{1}` autostash 备份未被改动。本批只执行用户授权的 Commit、Push 和 PR 创建，不合并或部署。

本批无数据迁移，回退仅撤销本批代码/帮助变更，不删除任何索引或历史数据。CI 状态以远端 PR 为准；五个顺序 PR 完成后的零真实调用隔离集成门禁仍未执行。
