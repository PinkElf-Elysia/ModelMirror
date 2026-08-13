# OpenRouter 模型快照、价格与分类审计（2026-08-13）

## 结论

本轮先完成目录与契约修复，再用 OpenRouter 官方全模态目录进行全量对照。修复后的快照覆盖完整：OpenRouter 当前 477 个非 Batch 条目在本地缺失 0 个，输入/输出方向推导出的 operation 和权威岗位能力差异均为 0；62 个 `:batch` 条目继续只作为服务档位，不计入模型快照数量。

分类整理依据已纠正为 OpenRouter `/models` 模型市场侧边栏，而不是 `/rankings` 或单独的用例榜单。当前市场数据保存 479 个非 Batch 模型的 Series、Providers、Model Authors、Categories、折扣、可蒸馏、ZDR、区域、模型年龄、工具调用成功率、Artificial Analysis 和 Design Arena 元数据。页面同步侧边栏的离散选项和值域；不再显示 Top 排名卡片或排名徽章。

`?category=` 单项响应只作为 Categories 筛选项的补充对照，不能驱动 Series、能力、排名或整个模型分类。模镜自己的“任务能力”仍作为独立附加层保留，并明确标记为非 OpenRouter 侧边栏项。“523 可直接调用”的运行时统计口径继续冻结，未因分类整理而改变。

可机读明细见 [`openrouter-classification-audit-2026-08-13.json`](./openrouter-classification-audit-2026-08-13.json)，重跑脚本为 [`scripts/audit-openrouter-classifications.mjs`](../scripts/audit-openrouter-classifications.mjs)。

## 数据源与粒度

审计时间：2026-08-13 05:07 UTC。

官方只读来源：

- 通用全模态目录：`GET https://openrouter.ai/api/v1/models?output_modalities=all&sort=newest&offset=0&limit=1000`
- 图片目录：`GET https://openrouter.ai/api/v1/images/models`
- 图片价格：`GET https://openrouter.ai/api/v1/images/models/{model_id}/endpoints`
- 视频目录：`GET https://openrouter.ai/api/v1/videos/models`
- 模型市场筛选数据：`GET https://openrouter.ai/api/frontend/v1/models/find?active=true&fmt=cards`
- Categories 单项对照（可选）：`GET https://openrouter.ai/api/v1/models?category={category}`

OpenRouter 当前侧边栏包括 Input Modalities、Discounted、Context length、Prompt pricing、Series、Categories、Supported Parameters、Distillable、Zero Data Retention、In-Region Routing、Output pricing、Model age、Tool Calling、Inactive Models，以及当前页面上的 Artificial Analysis、Design Arena、Providers 和 Model Authors。模型市场接口当前返回 540 个服务记录，按基础模型 ID 合并 `:batch` 后形成 479 个市场快照；其中 114 个模型带 Categories 记录，22 个模型带折扣记录。`architecture`、专用媒体目录、`supported_parameters`、`reasoning` 和定价字段继续作为结构化契约证据。

| 粒度 | 数量 | 口径 |
|---|---:|---|
| OpenRouter 目录条目 | 539 | 包含服务档位 |
| Batch 服务档位 | 62 | ID 以 `:batch` 结尾，不计模型快照 |
| 非 Batch 条目 | 477 | 与本地当前条目逐 ID 对照 |
| `canonical_slug` 实体 | 467 | 免费、别名等目录条目可能共享模型身份 |
| 专用图片模型 | 42 | 以图片目录为准；通用目录另有 2 个 Auto 图片输出路由，不作为图片生成模型 |
| 专用视频模型 | 23 | 以视频目录为准 |
| 本地模型快照 | 537 | 477 个当前条目 + 55 个可能不可用保留条目 + 5 个明确过期条目 |
| 本地生命周期 | 477 / 55 / 5 | 明确可用 / 可能不可用 / 明确过期 |
| 本地现场候选 | 532 | 477 明确可用 + 55 可能不可用；明确过期不进入现场候选 |

## 已完成的修复

### Qwen ASR 与 Zonos

- 新增 `qwen/qwen3-asr-1.7b` 与 `qwen/qwen3-asr-0.6b`，分别保留 OpenRouter 当前的音频输入、transcription 输出、价格和参数。
- 两个 ASR 已接入版本化 STT 档案和通用 WAV、MP3、M4A、FLAC、OGG/Opus、WebM、AAC 校验；当前状态为 `interaction_adapted + manual_required`。未完成真实短音频人工验收前，不宣称 verified，也不把它们作为“已确认可直接调用”的新证据。
- `zyphra/zonos-v0.1-hybrid` 与 `zyphra/zonos-v0.1-transformer` 已从当前上游目录缺失集合中识别，保留可进入入口，状态为“可能不可用”，并排在明确可用模型之后、明确过期模型之前。

### 最终核对新增 Qwen Reranker

- 最终全量核对时，OpenRouter 新增 `qwen/qwen3-reranker-8b`；已按普通快照补入，并与本轮其它新卡一样放在第六行以后。
- 该模型为文本输入、Rerank 输出，进入 RAG 入口；零 token 字段不解释为免费，按请求型计费标记，价格以 Rerank 专用端点为准。

### 12 项结构化元数据漂移

以下项目已按本次全模态目录回写，并有前端回归断言：

1. `qwen/qwen3.8-2.4t-a95b` 上下文长度。
2. `nvidia/nemotron-3.5-lightning` 上下文长度与工具参数。
3. `bytedance-seed/seed-2-1-turbo` 支持参数集合。
4. `deepseek/deepseek-v4-pro-0813` 支持参数集合。
5. `~deepseek/deepseek-v4-flash-latest` 输入/输出价格。
6. `z-ai/glm-5.2` 输入/输出价格。
7. `moonshotai/kimi-k2.7-code` 输入/输出价格。
8. `deepseek/deepseek-v4-pro` 输入/输出价格。
9. `qwen/qwen3.5-35b-a3b` 输入/输出价格。
10. `qwen/qwen3.5-397b-a17b` 输入/输出价格。
11. `z-ai/glm-4.6` 输入/输出价格。
12. `inclusionai/ling-3.0-tiny:free` 到期生命周期。

更新脚本和审计脚本现在都会保存并比较 `pricing.overrides`，以后同类漂移会被离线审计直接拦截。

### DeepSeek V4 默认模型定向回退

- 因 V4 Pro 当前存在运行问题，模型市场首页已恢复 V4 Flash 位于第二排首位，V4 Pro 后移一排；两者快照、契约和分类记录均保留。
- Agent Builder / General Agent 的前后端默认模型，以及经典工作流中的 Agent、LLM、工作流智能体节点默认模型，统一回退到 `deepseek/deepseek-v4-flash-0731`。
- 该回退只改变新建配置和默认展示，不迁移或覆写用户已经保存的工作流、Agent 或 Xpert 配置。

### 分段 token 价格

- 数据模型新增 `pricing_overrides`，按 `min_prompt_tokens` 选择对应输入/输出单价。
- 模型卡、对比视图和对话页设置显示“起始价格”和每个阈值后的价格；支持 Batch 的模型会分别显示实时与批处理分段，不再把长提示词档位隐藏在基础价后面。
- 专家团运行后的费用估算按实际输入 token 命中分段价格。
- 已覆盖 Seed 2.0 Code 的 128K 阈值和 Grok 4.6 的 200K 阈值。

### 图片价格覆盖

- 图片生成目录不再只读取 Seedream 5 Pro 与 Grok Imagine Image 2.0 两个模型的 endpoint 价格。
- 当前对专用图片目录中的每个生成模型读取 `/images/models/{model_id}/endpoints`，并用并发上限 8 控制请求压力。
- 单个价格 endpoint 失败时只让该模型价格为空，不移除模型、不把整个目录降级为离线，也不显示伪造的零成本。
- 自动化测试验证每个图片生成模型都会发起价格请求，且单模型 503 能安全降级。真实只读冒烟确认 Seedream 5 Pro endpoint 返回普通输出图 `$0.045`、高分辨率输出图 `$0.09`、输入参考图 `$0.003`。

## “523 可直接调用”的冻结口径

本轮不改 `ModelListPage` 的可直接调用逻辑，也不把 523 重新定义成静态快照字段。该数字来自验收环境中的运行时聚合：通用网关可调用集合、网关 ready 回退、以及音频/图片/视频实时能力目录任一确认，都会让模型计入；运行时目录没有返回前，页面不显示一个假定的 0。

因此，`523` 只能作为此前共享栈验收时的环境证据，不能由“537 快照”“532 现场候选”或“477 当前上游条目”直接相减得到。Qwen ASR 本轮仅为已适配、待人工验证，也不会被文档虚增为新的可直接调用证据；但现有 gateway-ready 通用回退仍可能在运行时把这类静态 `ready + chat` 条目计入，这正是后续统计专项需要消除的歧义。统一统计口径留待后续审计，本轮只记录证据边界，不改算法。

## 分类全量核对

### 权威结构化分类：通过

将 477 个当前非 Batch 条目逐一按 OpenRouter `architecture`、图片目录和视频目录推导后：

- 本地 operation 缺失/多出：0。
- 本地权威岗位能力缺失/多出：0。
- 上游当前条目未进入本地快照：0。
- 本地仍标成 live、但已从上游消失：0。
- 55 个上游缺失但未明确到期的条目全部位于 uncertain 区；4 个上游缺失且明确过期的条目位于 expired 区。
- `inclusionai/ling-3.0-tiny:free` 仍出现在上游目录，但 `expiration_date=2026-08-13` 已生效，因此本地按 expired 处理。

这一层可以继续作为门禁：输入/输出、工具参数、专用媒体目录和服务档位都具有可复核的上游字段。

### `/models` 侧边栏分类整理结果

- `openrouter_market` 独立保存模型市场筛选快照，不再保存或展示分类排名。
- Series 使用当前 20 个侧边栏选项；Categories 使用当前 12 个选项；Supported Parameters 使用当前 25 个选项，其中包含 `prediction`，不包含侧边栏未列出的 `tool_choice`。
- Providers 保存实际端点提供商，Model Authors 保存作者 slug，两者不再与模型作者展示名混为同一个“用人单位”筛选。
- Discounted、Distillable Yes/No、ZDR、EU/US 区域、上下文最小值、输入/输出价格区间、模型年龄和工具成功率均按实际侧边栏语义筛选。
- Artificial Analysis 的 Intelligence/Coding/Agentic Index，以及 Design Arena 的 8 个 ELO 分类均进入独立基准筛选区。
- `?category=` 只核对 Categories 归属的交集，不导入排名，也不作为其它筛选项或能力的权威来源。本次 12 类各返回 20 条；有 6 个“单项响应存在、市场 placements 缺失”的差异，集中在两个免费档位，作为非门禁参考记录，不反向覆盖 `/models` 实际筛选数据。
- `operations` 与结构化岗位能力继续由输入/输出模态、图片/视频专用目录和工具参数推导。477 个当前条目的 operation 差异为 0，结构化岗位能力差异为 0。
- 翻译不再作为本地已验证岗位能力；只在 OpenRouter Categories 中作为市场分类筛选。Qwen ASR、TTS、Embedding、Rerank 不再因 `multilingual` 描述误入翻译能力。
- 编程能力仍允许由名称/描述派生，但必须具备文本输出，代码 Embedding 不再被当成编程对话模型。
- 推理能力只接受 OpenRouter `reasoning` 对象，或 `reasoning`、`include_reasoning`、`reasoning_effort` 参数。`math` 与 `analysis` 不再从 reasoning 自动复制。
- 计费新增 `pricing_basis`：`token`、`media`、`request`、`dynamic`、`free`。只有 ID 明确属于 `:free` 档位或 `openrouter/free` 才进入免费分类；媒体和请求型计费在列表、对比页和对话设置中显示对应计费形态。
- provider 对未知作者保留规范化作者名，不再全部折叠为“其他”；当前 477 条中 `provider=其他` 为 0。
- 作者与端点提供商从当前市场快照动态生成；Series、Categories 和 Supported Parameters 的可选值按当前侧边栏顺序固定并由审计脚本核对。

### 分类门禁

分类审计脚本现在会在以下任一条件出现时返回非零：当前上游模型缺失、结构化 operation 或岗位能力不一致、模型市场快照漂移、侧边栏离散选项漂移、可选 Categories 对照不一致、无结构化信号的 reasoning、非文本翻译/编程污染、非明确免费条目被标免费、媒体/请求计费基础错误，或当前 provider 仍被折叠为“其他”。

后续若要继续细化，应新增独立的评测证据层，例如 `verified`，而不是把模型名称或营销描述提升为权威能力。数学、分析、代码检索等新分类也应先定义独立证据和验收口径。

## 验证证据

已执行：

```text
npm.cmd run test:run -- src/data/models.refresh.test.ts src/data/openrouterBatch.test.ts src/utils/tokenPricing.test.ts src/utils/imageCostEstimate.test.ts
# 4 files, 26 tests passed

npm.cmd run test:run -- src/pages/ModelListPage.layout.test.tsx src/components/ModelCard.test.ts src/components/filters/FilterPanel.test.tsx
# 3 files, 13 tests passed

npm.cmd run build
# tsc -b + Vite production build passed

npm.cmd run test:run -- src/data/models.refresh.test.ts src/pages/AgentWorkbenchPage.test.tsx
# 2 files, 25 tests passed

docker run --rm -v "${PWD}:/workspace" -w /workspace modelmirror-server \
  python -m pytest server/tests/test_agent_workspace_runtime.py server/tests/test_xpert_publish.py -q
# 27 passed

docker run --rm -v "${PWD}:/workspace" -w /workspace modelmirror-server \
  python -m pytest server/tests/test_multimodal_stt.py server/tests/test_multimodal_image.py -q
# 23 passed

node scripts/check-multimodal-readiness.mjs
# expected after final refresh: 537 snapshot / 62 serving variants / 477 live / 55 uncertain / 5 expired

node scripts/audit-model-modalities.mjs --catalog <models.json> --image-models <images.json> --video-models <videos.json>
# missing live 0; metadata mismatch 0; batch mismatch 0; image/video contract mismatch 0

node scripts/audit-openrouter-classifications.mjs --models <models.json> --images <images.json> --videos <videos.json> --market <models-find.json> [--category-dir <optional-category-reference-directory>]
# exit 0; 477 source models missing 0; market snapshot mismatch 0;
# operation/job mismatch 0; sidebar option mismatch 0; provider other 0;
# ?category= six differences are informational and non-gating

npm.cmd run test:run
# 56/58 files and 269/271 tests passed; 2 failures in the parallel full-suite run:
# NodePalette.test.ts is the pre-existing baseline mismatch (expected list omits
# vision_understanding); AgentWorkbenchPage.test.tsx timed out waiting for its
# initial heading under full-suite contention, but the file passes in isolation.
```

## 风险与回退

- 两个 Qwen ASR 尚未完成真实音频人工验收；回退时从 `MANUAL_TRANSCRIPTION_PROFILES` 移除即可，目录快照仍可保留。
- 图片目录刷新现在最多并发 8 个价格请求，可能增加一次冷刷新延迟和 OpenRouter 公开 API 压力；回退可恢复按模型白名单取价，但会重新造成价格覆盖缺口。
- 分段 token 价格是向后兼容扩展；回退可删除 UI 展示与费用选择 helper，基础价格字段仍在。
- 分类整理会改变原先错误的榜单、翻译、编程、推理、免费和 provider 筛选结果；回退可撤销 `openrouter_market`、生成的市场快照、筛选 UI 与推导规则。冻结的“523 可直接调用”运行时算法未修改。
