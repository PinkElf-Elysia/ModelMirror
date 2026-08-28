# 任务卡：RAG P0 收尾 PR5（3C/3D）

## 1. 单一目标

- 本次要完成：消除 Benchmark 首 child、marker 泄漏和大 source block 误命中偏差，并建立 tuning、calibration、held-out qualification 互斥且可追溯的锁定集合合同。
- 本次明确不做：不调用真实 Provider、不调参、不重建或激活索引、不迁移历史 Gold/评测记录、不进入分块、全文排序或生成层优化。

## 2. 证据

| 结论 | 等级 | 证据路径或命令 |
| --- | --- | --- |
| 当前 evidence 会按 child 优先排序并按 source block 取首项 | 已证实事实 | `server/benchmarks/knowledge_generation.py::snapshot_target` |
| 非 paraphrase/cross-language 题仍会被强制 marker 约束 | 已证实事实 | `server/benchmarks/knowledge_generation.py::prepare_generation` |
| source-block Gold 目前只校验 block ID，不验证 anchor | 已证实事实 | `server/rag/evaluation.py::_source_matches_reference` |
| Strategy Tuner 当前用同一数据集选择候选并校准阈值 | 已证实事实 | `server/rag/strategy_tuner.py::preflight`、`::_search` |

## 3. 影响范围

- 允许修改路径：`server/benchmarks/`、`server/rag/`、对应 `server/tests/`、相关 Knowledge Evaluation 前端与帮助文档、本任务卡。
- 禁止修改路径：活动索引、`server/rag/storage`、共享容器、持久化数据、非 RAG 模块。
- 预计文件数：12–18 个，按三个内部小批分别审查和验收。
- 影响路由/API：Benchmark generation evidence、Evaluation reference/role/publish、Strategy Tuner V3 request/preflight、Formal admission。
- 影响持久化数据：仅新增向后兼容字段；不迁移或重写历史记录。
- 新增或升级依赖：无。
- 涉及密钥/网络/文件/子进程/公开访问：仅现有本地元数据文件；本批验证不访问真实 Provider。

超过 5 个文件时说明无法安全拆分的原因：行为合同跨越生成器、API schema、不可变版本存储、Tuner 执行和 Formal 门禁；只改其中一层会形成可绕过的半合同。实施仍按每个内部小批约 3–5 个生产/测试文件推进，最终作为一个原计划 PR5 交付。

## 4. 验收标准

### 场景 1：可信正例 Gold

- Given：同一 source block 有多个 child，且 canonical block 有可定位 anchor。
- When：生成并发布 V3 held-out qualification。
- Then：采样不偏向首 child，Gold 保存 anchor span/hash，检索结果必须覆盖 document、source block 和 anchor 才计为命中。

### 场景 2：隔离的数据集角色

- Given：独立的 strategy tuning、threshold calibration 和 held-out 集合。
- When：执行 V3 Tuner 或 Formal admission。
- Then：Tuner 只能分别读取前两者且阻断重叠/近重复；Formal 只接受从未进入 Tuner 的已发布 held-out V3。

### 失败场景

- Given：非 exact lexical marker 复制、缺失完整语料 no-result 复核、无人工批准、角色/校验和重叠、历史 V1/V2 或只命中同一大 block。
- When：尝试发布、Tuner 或 Formal。
- Then：必须 fail-closed，历史记录仍可展示为 legacy/diagnostic。

## 5. 实施顺序

1. 模型/契约：canonical evidence、anchor、no-result verification、四类集合角色和 V3 请求。
2. 校验/安全：发布 checksum、跨集去重、held-out/Tuner 互斥、历史降级。
3. 执行：候选选择只用 tuning，阈值校准只用 calibration，Formal 只用 held-out。
4. 前端：展示角色、anchor/no-result 证据和阻塞原因；不增加隐式自动操作。
5. 文档：同步帮助中心与收尾证据。

## 6. 验证矩阵

| 检查 | 命令或步骤 | 预期 | 状态 |
| --- | --- | --- | --- |
| 语法/类型 | `python -m py_compile`（7 个变更生产模块） | 通过 | 通过 |
| 目标测试 | Benchmark、Evaluation、Integrity、Strategy Tuner、Qualification 五文件 | 通过 | 112 passed；最终 Tuner 修复后另行 30 passed |
| 扩大回归 | `pytest server/tests/ -k "rag or benchmark or knowledge" -q` | 通过 | 435 passed，4712 deselected |
| 全量后端 | `pytest server/tests/ -q` | 通过或明确区分既有基线 | 5097 passed、29 skipped、20 failed；同一基线 SHA 定向复现相同 20 failed（69 passed），均为 Agency Worker 构建产物或容器 Node/TS 环境缺失 |
| 前端全测 | `npm.cmd run test -- --run --maxWorkers=1` | 通过 | 124 files、776 passed；默认并行模式两次各有 1 个未改动用例触发 5 秒资源超时，两个用例单跑均通过 |
| 构建 | `cd client && npm.cmd run build` | 通过 | 通过；仅保留既有 chunk size warning |
| Docker/人工验收 | 最终 P0 统一隔离验收由五个 PR 合并后执行 | 本 PR 不单独运行真实 Provider | 不适用 |
| 静态审计 | `git diff --check`、冲突标记/TODO 扫描 | 通过 | 通过 |
| 敏感信息扫描 | Diff 与任务卡常见密钥模式扫描 | 无密钥/运行数据 | 通过 |

## 7. 风险与停止条件

- 主要风险：把历史 V1/V2 误升级为新晋级证据；Tuner 数据泄漏；anchor 合同与实际结果文本不一致。
- 兼容风险：旧客户端仍提交 `eval_set_id/version`；旧评测仍读取 source-block-only Gold。
- 安全风险：不得把语料正文或密钥写入运行回执；完整语料复核只保存脱敏定位信息。
- 触发停止的条件：必须迁移共享数据、需要新增依赖、真实 Provider 才能验证、或无法在不改变历史数据的前提下完成合同。
- 需要用户确认的问题：提交、推送、PR 创建和任何真实额度调用均需另行授权。

## 8. 回退

1. 回滚本 PR 的代码、测试和文档提交。
2. 无需恢复活动版本/指针。
3. 不影响持久化数据；历史记录未迁移。
4. 重新运行 PR4 合并基线的 RAG/Knowledge/Benchmark 定向测试。

## 9. 完成定义

- [x] 实现只覆盖声明范围。
- [x] 正常与失败路径均有验证。
- [x] 公共接口和数据影响已说明。
- [x] Diff 已审查，无用户改动被覆盖。
- [x] 无密钥、运行存储或构建产物进入提交。
- [x] 文档与 Harness 已同步。
- [x] 未知产品信息仍明确标为待确认。
