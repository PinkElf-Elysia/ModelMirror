# OpenRouter 2026-09-03 模型更新与漂移收口审计

审计日期：2026-09-03（America/Phoenix）
工作树起始基线：`origin/main@efa63af2`
提交集成基线：`origin/main@e927db55`
工作方式：隔离工作树；未读取密钥、未发起付费模型调用、未改共享栈。

## 更新结论

本轮完成 MAI-Transcribe 2 专用转写适配，并用仓库内
`openrouter-update` Skill 的五阶段 `update` 流程收口当窗全部可执行漂移。

- `microsoft/mai-transcribe-2` 以 `audio -> transcription` 接入现有转写工作区，
  使用 OpenRouter JSON `input_audio` 契约；目录价按 `$0.10/音频小时` 解释，
  页面在可读取时长时给出提交前估价。真实短音频仍待人工验收，因此不宣称
  Provider 调用已验证。
- 新增 `inclusionai/ling-3.0-flash-fin`：标准 `text -> text` Chat，
  262,144 上下文，输入/输出 `$0.06/$0.18` 每百万 Token。
- 新增 `nvidia/nemotron-3.5-content-safety`：标准
  `text+image -> text` Chat，复用图片理解链；安全分类语义不被误建模为图片生成
  或 Moderations 专用端点。
- 两张新增卡片均通过既有刷新列表置于第六排以后；既有 `:free` 档继续作为
  独立计数卡片，只有 `:batch` 被聚合为不计数的服务档位。
- Batch 收口：新增 `x-ai/grok-4.3:batch`，移除
  `nvidia/nemotron-3-ultra-550b-a55b:batch`，修正
  `google/gemini-3.7-flash:batch` 价格与
  `mistralai/mistral-medium-3-5:batch` 上下文。Grok Batch 使用基础请求 ID、
  文本限定的 `/v1/chat/completions`、24 小时窗口和 30 天保留期；价格为
  `$1/$2`，提示词达到 200k Token 后为 `$2/$4`。
- 修正 16 个模型的 17 个结构化字段差异：12 个价格字段、2 个
  `supported_parameters` 字段和 3 个到期日期字段。
- 市场侧栏快照按完整市场响应重建；供应端点、分类、折扣、零数据保留、地区等
  结构字段已与同窗一致。工具成功率、Artificial Analysis 与 Design Arena
  继续作为波动观察项，不进入可执行漂移签名。
- PR 创建后的发布前复核又捕获并收口两项新漂移：
  `deepseek/deepseek-v4-flash-vision-exp` 基础输入/输出牌价更新为
  `$0.44/$1.32` 每百万 Token；`deepseek/deepseek-v4-pro-0813` 的市场提供商
  更新为 Novita、Together，并同步折扣与零数据保留标记。

## 最终冻结证据窗

最终四源窗口为 `2026-09-04T06:55:31Z` 至 `06:55:34Z`
（本地 `2026-09-03 23:55:31` 至 `23:55:34`）。四个响应均为 HTTP 200；
模型目录满足 `data.length = total_count = 576` 且 `links.next = null`，市场与
通用目录的非 Batch exact ID 集合均为 505，双向差集为空。

| 来源 | 条目 | SHA-256 |
| --- | ---: | --- |
| 通用模型目录 | 576 | `6cf85397acac6dc913b2eb21f861d5024cd69569fc132ec05787462ed3a5e20b` |
| Images 专用目录 | 48 | `bac57ac3a2439db643d83ad106ce6ef7f5b5262432966d36257ec7c83da2dc1e` |
| Videos 专用目录 | 28 | `0439d4cea2bf84c9921c2801ef74c8179c058332bac2bd86a00d08027b538ca8` |
| 模型市场侧栏 | 576 | `162d6cd19b812c5d93b5405b1356bac52ca6dddf63414ab707d7ad6cd892de37` |

最终计数：

- 本地计数快照：584
- 当前上游明确存在：505
- 可能不可用但保留入口：73
- 明确过期：6
- 非过期适配分母：578
- Batch 服务档位：71，全部不计入模型总数

Images 仍为 48 条，Videos 仍为 28 条；专用媒体目录无缺失快照。
`openrouter/auto` 与 `openrouter/auto-beta` 的通用图片输出声明仍没有 Images
专用目录证据，仅保留审计说明，不自动开放图片生成。

## 初次五阶段收敛证据

| 阶段 | 目标 | 阶段后结果 |
| --- | --- | --- |
| 1 | 缺失非 Batch 模型 | 2 -> 0；计数 582 -> 584，live 503 -> 505 |
| 2 | Batch 服务档位 | 缺失、陈旧、孤儿、元数据差异全部为 0；总数仍为 71 |
| 3 | 普通模型结构化元数据 | 16 个模型、17 个字段 -> 0 |
| 4 | 生命周期 | 不一致保持 0；73 个可能不可用与 6 个过期入口均保留 |
| 5 | 市场侧栏快照 | 结构漂移 104 -> 0；同冻结窗总漂移为 0 |

初次冻结 manifest 连续两次执行完整审计，均返回 `clean`、退出码 0，且
可执行漂移签名一致：

`d0aa00ed6a9ebd51b3fc0715e0d3e5de2d01f5d3e47226953c24b9dfa412238a`

PR 创建前的 `05:30Z` 四源窗口曾只观察到 92 个纯波动市场变化、0 个结构变化，
wrapper 按策略返回 `clean`。PR 创建后的 `06:40Z` 发布前复核检测到两项新变化：
Flash Vision Exp 的基础牌价，以及 V4 Pro 的提供商、折扣和零数据保留结构字段。
先执行 metadata-only 阶段，再重建市场侧栏快照；每阶段都以新的输出目录重跑
完整 wrapper。在该 `06:40Z` 修复窗口上，模态、分类、readiness 三项底层审计
均退出 0，结构与波动差异均为 0，wrapper 返回 `clean`、退出码 0。

紧接发布前又抓取上表所列的 `06:55Z` 最终窗口，并对同一 manifest 连续审计
两次：两个 wrapper 都返回 `clean`、退出码 0；市场结构差异保持 0，仅观察到
87 个 `tool_call_success_rate` 波动，因此底层 classification 审计按约定退出 1，
但不构成可执行漂移。两次可执行签名相同：

`d0aa00ed6a9ebd51b3fc0715e0d3e5de2d01f5d3e47226953c24b9dfa412238a`

MAI 小时价另以市场原始字段复核：`display_pricing` 为 `Audio Hours`、单位
`/hour`，`pricing_json` 使用 `microsoft_stt:audio_hours = 0.1`。将同一冻结窗
合成为“数值仍为 0.1、单位改为 `/minute`”的故障样本并同步清单哈希后，Skill
完整 wrapper 报告 `market_unit_label_mismatch`，状态为 `drift`，显式 PowerShell
进程退出码为 2；因此不会把仅数值相同的单位漂移误报为 clean。

## 自动验证

- 模型目录、Batch 与转写前端专项：4 个文件、68 个测试通过。
- STT、音频目录、普通与托管 Batch 后端专项：58 个测试通过；仅保留既有
  FastAPI `on_event` 弃用警告。
- updater、小时价数值/单位/SKU 保护、exact-ID 与签名稳定性测试：17 个测试通过。
- `check-multimodal-readiness.mjs`：通过，报告计数为 584/71/505/73/6/578。
- `skill-creator` 的 `quick_validate.py`：`Skill is valid!`。
- 变基后前端全量测试：131 个测试文件、919 个测试通过；附带的网关 Cookie 测试
  1 个通过。
- PR 后续分类专项：模型数据、模型列表、侧栏筛选和 Chat 布局共 4 个文件、
  66 个测试通过；updater、价格与签名保护 17 个测试通过。
- 前端 `tsc -b && vite build`：通过；保留既有大 chunk 警告。
- 最新四源完整审计：wrapper 退出码 0，状态 `clean`；结构漂移为 0，87 个
  工具成功率波动仅观察；同一 manifest 重跑的可执行签名稳定。
- PR 后续漂移回归：锁定 Flash Vision Exp 的 `$0.44/$1.32` 基础牌价，以及
  V4 Pro 的 Novita/Together、折扣与零数据保留市场字段。

## 验证边界

- exact ID、缺失/陈旧、Batch、元数据、生命周期、模态、任务能力、价格口径、
  市场结构和 readiness 均由仓库脚本重新计算，不以网页卡片数量替代 API 证据。
- 当前价格审计口径覆盖基础 input/output 牌价和既有 UTC 时钟分段价；OpenRouter
  上游的 cache-read 单价与按星期限定的 `utc_days` 尚未进入本地数据结构，因此
  本文不宣称这两项完整价格契约已经对齐。
- MAI 的小时价格数值覆盖在所有 updater 阶段前都会失败关闭；完整分类审计还会
  校验市场的显示 SKU、小时单位和 `pricing_json` SKU。本轮真实冻结窗复审为
  0 差异，合成单位漂移则按预期拒绝。
- 未发起真实模型调用，因此这里只能宣称目录、契约、界面和静态/模拟测试通过，
  不能宣称 MAI 或两个新增模型的 Provider 端到端调用已验证。

公开来源：[完整模型目录](https://openrouter.ai/api/v1/models?output_modalities=all&sort=newest&offset=0&limit=1000)、
[Images 目录](https://openrouter.ai/api/v1/images/models)、
[Videos 目录](https://openrouter.ai/api/v1/videos/models)、
[模型市场侧栏数据](https://openrouter.ai/api/frontend/v1/models/find?active=true&fmt=cards)、
[Speech-to-Text 契约](https://openrouter.ai/docs/guides/overview/multimodal/stt)。
