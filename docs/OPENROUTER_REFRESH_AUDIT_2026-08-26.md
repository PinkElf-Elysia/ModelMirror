# OpenRouter 模型快照审计（2026-08-26）

## 口径与来源

- 基线：`origin/main@d8a3ad8c`（PR #301 合并后）。
- 通用目录：`/api/v1/models?output_modalities=all&sort=newest&offset=0&limit=1000`，PR 前最终抓取为 561 条，其中 497 条非 Batch、64 条 Batch 服务档位。
- 专用目录：Images 47 条、Videos 26 条。
- 模型市场侧边栏：`/api/frontend/v1/models/find?active=true&fmt=cards`，PR 前最终抓取为 561 条服务记录、498 个聚合模型快照。
- 五批修正及验收期追加后：568 个计数快照、64 个不计数 Batch 档位；497 个明确可用、65 个“可能不可用”、6 个已过期，首页/站内口径为 562 个非过期快照。

本轮先完成四个 Recraft Styles 图片模型适配，再按“补缺失模型、补 Batch、修结构化漂移、复核生命周期、刷新市场快照”五个独立批次闭合审计差异。历史条目没有删除，“可能不可用”入口继续保留。

## 本轮闭合

- `recraft/recraft-v4-styles`
- `recraft/recraft-v4-styles-pro`
- `recraft/recraft-v4-styles-vector`
- `recraft/recraft-v4-styles-pro-vector`

四条记录均已同时进入静态快照、Images 专用能力目录映射和模型市场侧边栏快照。适配后 `image_api_missing_from_snapshot=0`、`video_api_missing_from_snapshot=0`，权威 operation 与岗位能力误差均为 0。

## 第一批：补齐和适配新模型

以下 7 个非 Batch 实时条目已加入静态快照，并按实际输入/输出契约进入普通 Chat 或多模态理解入口：

1. `tencent/hy-mt2-7b`
2. `thinkingmachines/inkling-small:free`
3. `thinkingmachines/inkling:free`
4. `z-ai/glm-5.2:free`
5. `minimax/minimax-m3:free`
6. `minimax/minimax-m2.7:free`
7. `mistralai/ministral-8b`

`thinkingmachines/inkling-small:free`、`thinkingmachines/inkling:free` 和 `minimax/minimax-m3:free` 的图片、音频或视频能力是输入理解能力，输出仍为文本，因此没有错误接入专用媒体生成工作区。修正后 `source_models_missing_locally=0`。

人工验收期间目录又新增 `z-ai/glm-5.3-flash`，已在 PR 前追加：文本、图片和视频输入，文本输出，1,048,576 Token 上下文，强制推理，输入/输出价格分别为 `$0.075/M` 与 `$0.25/M`。它沿用普通多模态 Chat 契约，不建立专用图片或视频生成入口。

## 第二批：Batch 服务档位

- 新增 `google/gemini-embedding-2:batch`，作为 `google/gemini-embedding-2` 的不计数服务档位，调用端点为 `/v1/embeddings`。
- 修正原有 7 个 Batch 档位的价格、上下文等漂移。
- 最终 64 个 Batch 档位全部并入对应模型的对话页设置，不在主页重复形成模型卡片；输入统一按当前 Batch API 的文本限制处理。
- 复核结果：缺失、过期、孤儿和元数据不一致均为 0。

## 第三批：结构化元数据漂移

修正 64 个重合基础模型的结构化元数据：价格 39、支持参数 20、上下文 9、描述 8、到期时间 5、名称 2；同一模型可同时命中多个字段。12 个专用媒体模型继续使用 Images、Videos 或专用音频契约覆盖，未被通用目录的继承字段覆盖。复核中发现并补固 `black-forest-labs/flux-video-upscale` 的保护项，同时让生成器持续保留本地隐私与调用提示。最终 `metadata_mismatches=0`，Images 与 Videos 专用目录缺口均为 0。

## 第四批：生命周期复核

- 原 11 个“本地明确可用、官方实时目录已缺失”的入口改为“可能不可用”：`ai21/jamba-large-1.7`、`deepcogito/cogito-v2.1-671b`、`google/gemma-3n-e4b-it`、`inclusionai/ling-2.6-1t`、`inclusionai/ling-2.6-flash`、`inclusionai/ring-2.6-1t`、`nvidia/nemotron-3-nano-30b-a3b:free`、`nvidia/nemotron-nano-12b-v2-vl:free`、`nvidia/nemotron-nano-9b-v2:free`、`openai/gpt-oss-20b:free`、`qwen/qwen-plus-2025-07-28:thinking`。
- `mistralai/devstral-2512` 当前重新出现在官方实时目录，恢复为明确可用。
- `stealth/ox-alpha` 在本轮首次抓取时仍在通用目录中但已声明到期，最终验收抓取时已从通用目录和市场目录移除；本地继续保留其已过期入口。
- 71 个本地历史保留条目当前由 65 个“可能不可用”和 6 个已过期条目组成；均未删除。复核结果 `uncertain_status_mismatches=0`。

## 第五批：侧边栏分类与市场快照

- Series、Categories、Supported Parameters 三组离散选项集合与 OpenRouter 当前 `/models` 侧边栏完全一致。
- 本地没有“已产出岗位能力但筛选项缺失”或“空筛选项”。
- 498 个聚合市场快照已按同一时点的官方响应刷新；497 个当前非 Batch 模型可直接对照，动态市场快照不一致为 0。
- 权威 operation 不一致为 0，权威岗位能力不一致为 0，供应商落入“其他”为 0，分类筛选缺口为 0。
- 供应端点、折扣、ZDR、分类投放、工具调用成功率和基准分数属于高频动态字段；本次结论绑定上述抓取时间，不应视为永久不变。

## 最终结论

- 当前官方 497 个非 Batch 条目全部有本地快照；缺失为 0。
- 当前官方 64 个 Batch 服务档位全部挂接到基础模型；不计入 567 个模型快照总数。
- 基础元数据、Batch 元数据、生命周期、权威任务分类、侧边栏离散选项和动态市场快照在同一审计输入下均无未闭合差异。
- 通用目录中仍有 `openrouter/auto`、`openrouter/auto-beta` 声明图片输出但未出现在专用 Images 目录；它们是通用路由器，不据此伪造专用图片生成契约。

## 后续增量复核（2026-08-26 本地 / 2026-08-27 UTC）

本次在加入 `qwen/qwen3.8-flash` 与 `meta/muse-image` 后，使用同一组重新抓取的官方输入完成全量复核：

- 通用目录 562 条：499 个非 Batch 实时模型、63 个 Batch 服务档位；本地为 570 个计数快照、499 个明确可用、65 个“可能不可用”、6 个已过期，站内非过期口径 564。
- Images 专用目录 48 条、Videos 专用目录 26 条；新增 Muse Image 走专用 Images API，并继续以专用能力目录约束可用参数。
- 修正 10 个重合基础模型的当前结构化漂移：`z-ai/glm-5.3-flash` 的上下文和支持参数，另 9 个模型的价格或到期时间。
- 移除已不在官方目录中的 `z-ai/glm-5.2:batch` 服务档位；当前 63 个 Batch 档位均挂接到基础模型且不计入模型快照数。
- 模型市场响应包含 562 条服务记录，聚合为 500 个模型快照；动态供应端点、折扣、ZDR、分类、工具调用成功率和基准分数已按同一时点刷新。
- 修正后通用元数据、Batch、生命周期、权威 operation、岗位能力、侧边栏离散选项、Images/Videos 覆盖和市场快照均由后续审计命令复核；最终结果以实际命令输出为准。

历史章节保留其原抓取时点与口径，不应用后续数字反向改写历史审计结论。

## 可复现命令

```powershell
node scripts/audit-model-modalities.mjs --catalog <models-all.json> --image-models <image-models.json> --video-models <video-models.json>
node scripts/audit-openrouter-classifications.mjs --models <models-all.json> --images <image-models.json> --videos <video-models.json> --market <models-find.json>
```

以上命令使用同一组抓取输入可重现本次零差异结果；没有发起付费模型调用，也没有用健康检查代替实际契约对照。
