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

## 最终冻结证据窗

最终四源窗口为 `2026-09-04T05:30:41Z` 至 `05:31:32Z`
（本地 `2026-09-03 22:30:41` 至 `22:31:32`）。四个响应均为 HTTP 200；
模型目录满足 `data.length = total_count = 576` 且 `links.next = null`，市场与
通用目录的非 Batch exact ID 集合均为 505，双向差集为空。

| 来源 | 条目 | SHA-256 |
| --- | ---: | --- |
| 通用模型目录 | 576 | `a8b24f865b426595cddbbded2c26ebafb3a388549414cedf75dbd9b1d4346264` |
| Images 专用目录 | 48 | `bac57ac3a2439db643d83ad106ce6ef7f5b5262432966d36257ec7c83da2dc1e` |
| Videos 专用目录 | 28 | `0439d4cea2bf84c9921c2801ef74c8179c058332bac2bd86a00d08027b538ca8` |
| 模型市场侧栏 | 576 | `bd8ebcac0519f1e7321636a2384931478f2f9b77ac8c2b484f2c5635405573a1` |

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

## 五阶段收敛证据

| 阶段 | 目标 | 阶段后结果 |
| --- | --- | --- |
| 1 | 缺失非 Batch 模型 | 2 -> 0；计数 582 -> 584，live 503 -> 505 |
| 2 | Batch 服务档位 | 缺失、陈旧、孤儿、元数据差异全部为 0；总数仍为 71 |
| 3 | 普通模型结构化元数据 | 16 个模型、17 个字段 -> 0 |
| 4 | 生命周期 | 不一致保持 0；73 个可能不可用与 6 个过期入口均保留 |
| 5 | 市场侧栏快照 | 结构漂移 104 -> 0；同冻结窗总漂移为 0 |

同一冻结 manifest 连续两次执行完整审计，均返回 `clean`、退出码 0，且
可执行漂移签名一致：

`d0aa00ed6a9ebd51b3fc0715e0d3e5de2d01f5d3e47226953c24b9dfa412238a`

随后重新抓取上述最终四源窗口。模型、Images、Videos 哈希未变；市场响应哈希
因实时指标变化而更新。最终审计观察到 92 个纯波动市场变化、0 个结构变化，
wrapper 按策略仍返回 `clean` 和退出码 0；可执行签名与重复审计相同。这同时
验证了 Skill 对“结构变化需处理、纯指标波动只观察”的默认门禁。

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
- 前端 `tsc -b && vite build`：通过；保留既有大 chunk 警告。
- 最新四源完整审计：wrapper 退出码 0，状态 `clean`；同一 manifest 重跑的
  可执行签名稳定。

## 验证边界

- exact ID、缺失/陈旧、Batch、元数据、生命周期、模态、任务能力、价格口径、
  市场结构和 readiness 均由仓库脚本重新计算，不以网页卡片数量替代 API 证据。
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
