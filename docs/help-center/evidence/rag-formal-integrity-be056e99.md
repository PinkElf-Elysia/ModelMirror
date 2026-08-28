# RAG P0 PR4：Formal 可重放性验收记录

日期：2026-08-28。功能实现与全量后端基线：`be056e994d99fca2dcc158bd42cc71e8ebfc3db7`；提交前集成基线：`18f7bbbf725bf44e84be633ed31e34f160658536`。
本记录对应上述基线上的 PR4 增量，不表示任一基线已包含本功能。

## 范围与兼容

- Evaluation Run 读取时重新解析知识库、Pipeline Version、语料快照、执行配置、Backend、代码与 Provider 回执，投影为 `current`、`orphaned` 或 `unreproducible`，不改写历史指标。
- Formal 在首个检索槽位前验证不可变 Gold、完整 case/target 账本、同一语料和版本实际 Top-K；任一引用或 checksum 漂移均在 Provider 调用前失败关闭。
- 每个 case 保存脱敏的执行模式、稳定失败原因和 Provider route receipts；Provider route receipt 不记录 query 正文、检索文本、endpoint 或凭据。
- Promotion 重新计算可重放性和执行完整性。旧 `promotion_gate.passed=true`、hash/degraded Embedding、fulltext Embedding 调用、fingerprint mismatch、Rerank fail-open、非法回执和非零错误均不能绕过。
- 历史记录继续可读；本批不迁移数据、不改活动索引、不调用真实 Provider。

## 严格证伪增量

在既有绿测之后，将 Formal 完整性合同视为待证伪对象，新增 14 个攻击用例并确认了 6 个可实际绕过的缺口：

1. 运行时代码指纹未覆盖 Model Router 的执行依赖，路由实现变化可能不使 Formal 失效。
2. Formal target ledger 只校验版本引用，未拒绝重复或与版本不一致的内部 `target_id`。
3. Python 的 `bool` 可被 `int` 校验接受，`call_count=true` 可能伪装成一次调用。
4. 无 Rerank 的 fulltext Formal 可接受伪造的零调用 Provider receipt。
5. managed receipt 缺少或错误类型的 `reason_codes` 会被清洗为合法空数组。
6. fulltext 的空字典或错误类型 receipt 在归一化后丢失“回执曾存在”的信息，仍可冒充真正的无 Provider 调用。

修复后，代码指纹覆盖 Model Router 路由依赖；Formal 要求两个内部 target ID 非空、唯一且逐项等于版本 ID；Provider receipt 对调用数、原因码和“无调用即无回执”语义执行严格类型与原始存在性校验。14 个攻击用例全部转绿。

## 自动验收

后端测试统一使用 `modelmirror-server:latest` 一次性容器，`--network none`，只挂载隔离工作树，不传凭据、不挂载共享数据或 Docker socket。

| 检查 | 结果 |
| --- | --- |
| PR4 专项与兼容 | 80 passed，4 warnings |
| 最新集成基线 RAG / Benchmark 文件集合 | 358 passed，4 warnings |
| 前端聚焦 | 2 files，8 tests passed |
| 最新集成基线前端全测 | 124 files，771 tests passed；Node header check 通过 |
| 最新集成基线 TypeScript | typecheck 通过 |
| 最新集成基线生产构建 | 通过；3162 modules，保留既有 large-chunk warning |
| 功能实现基线全量后端 | 5090 passed，20 failed，29 skipped，6 warnings |
| 精确基线归因 | 20 个失败在 detached `be056e99` 基线以同一失败名称全部复现 |

全量后端中的 20 个失败仅位于 Agency Worker 构建产物、Expert Team Worker 和 Skill 的 Node/TypeScript 装载边界。候选与精确基线对四个失败文件分别重放，均为 `20 failed, 69 passed`；本批修改的 RAG / Knowledge / Benchmark 路径没有失败。严格证伪期间首次全量曾额外出现一个无代码交集的 PDF 单页资源限制失败，隔离重放为 `1 passed`，最终实现的第二次全量未再出现该项，因此不将其归因为本批回归。`be056e99..18f7bbbf` 只修改模型目录与多模态证据文件；变基后重新运行 RAG / Benchmark 定向回归及全部前端门禁。

所有后端验收均在禁网容器中运行。此次授权的可选真实调用未使用：本轮发现和修复的是代码指纹、账本身份、严格类型与“无调用即无回执”合同，外部 Provider 成功响应不能独立证明这些安全属性；为避免产生无信息增益的额度和数据外发，本记录不将真实调用伪装成必要验收证据。

## Help Center Impact

- 影响：评测页新增“可重放 / 引用已失效 / 不可重放”状态，并在可重放、Formal、执行完整性和 Promotion Gate 同时通过前禁用推广。
- 更新：`recover-unavailable-feature` 增加 RAG Formal 恢复说明；单条目使用本记录的验证日期和基线，不改写其他帮助内容的历史元数据。
- 独立预览：本批工作树，loopback 端口 15327，不连接共享后端。
- 实际结果：页面显示验证日期 `2026-08-28` 与基线 `be056e99`；“多模态 Adapter”和“RAG Formal”标题各且仅各出现一次，普通视口无截断、重叠或重复。
- 边界：浏览器只验证静态帮助内容。状态组合与推广按钮由前端单元测试覆盖；未运行真实 Formal、未上传资料、未调用真实 Provider。

## 回退

本批没有数据迁移。回退仅撤销 PR4 代码、测试、文档和帮助内容；活动索引、历史 Gold 与历史评测记录无需回滚。
