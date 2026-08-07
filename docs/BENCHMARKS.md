# ModelMirror Benchmark

最后更新日期：2026-08-07

## 1. 定位

Benchmark 产品层为 Xpert Evaluator、Knowledge Evaluation 和后续 Agent Workspace
适配提供统一目录与来源元数据。它不创建新的正式数据集 Store：

- Agent/Xpert 数据集继续由 `XpertEvaluationStore` 保存。
- RAG 数据集继续由 `KnowledgeEvaluationStore` 保存。
- 后续生成与校准任务只在 `BenchmarkJobStore` 保存任务状态，不复制正式数据集。

当前已交付 `EVOAGENTX-BENCHMARK-CATALOG-01`。定向生成、RAG 标准 Pack、RAG
定向生成和 Agent Workspace 适配仍为后续独立轮次。

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
| `case_count / checksum` | 用例数量和规范化 SHA-256。 |

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

## 5. API

```text
GET  /api/benchmarks/capabilities
GET  /api/benchmarks/catalog?kind=agent_response
GET  /api/benchmarks/catalog/{pack_id}
POST /api/benchmarks/catalog/{pack_id}/instantiate
```

目录列表返回 Manifest 摘要；详情返回固定用例。接口不返回完整 Xpert Prompt、工具结果、
知识正文、凭据、物理路径或 Runtime Store 数据。

## 6. 前端

`/agents/evaluations` 按以下视图组织：

- 标准基准：浏览 Pack 并添加到工作区。
- 我的评测集：编辑、导入、发布及配置基线/候选。
- 运行报告：查看运行记录、总体指标和逐样例结果。

实例化完成后自动进入新 Dataset 草稿。v1 已发布，可立即选择固定版本运行；继续编辑不会
改写 v1。

## 7. 安全与后续边界

- Pack 内容与 checksum 随仓库版本发布，不在运行时联网更新。
- 标准核心 Benchmark 不执行真实副作用、HITL、Browser、Sandbox 写入或外部实时数据。
- 目录实例化不运行 Xpert、不批准 Proposal，也不修改线上资源。
- 后续定向生成默认创建待审核草稿，并必须完成同 revision 的受限校准后才可发布。
- RAG Benchmark 使用 `KnowledgeEvaluationStore`，不会复制到 Xpert Dataset Store。
- General Agent Workspace 最终只接目录和运行摘要，不替换 Penguin Benchmark Runtime。

## 8. 回归

```bash
python -m pytest server/tests/test_benchmark_catalog.py -q
python -m pytest server/tests/test_xpert_evaluations.py -q
cd client
npm.cmd run build
```

新增后端包必须同步复制到 `server/Dockerfile`，并在共享栈空闲后通过真实镜像重建验证。

