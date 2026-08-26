# 模镜全模态与常用格式缺口审计

> 2026-08-13 的 OpenRouter 全模态目录、价格与分类复核已迁移到 [`OPENROUTER_CATALOG_AUDIT_2026-08-13.md`](./OPENROUTER_CATALOG_AUDIT_2026-08-13.md)。下列 2026-08-06 目录数量保留为历史批次证据，不再代表当前快照。

> 审计基线：多模态与音频闭环批次 A–I，以及未验证模型收尾批次 A–H；实时模型目录复核日期 2026-08-06。OpenRouter 快照为 517 个模型（462 个实时条目 + 52 个可能不可用的保留条目 + 3 个到期条目），另有直接 OpenAI 精选档案。
> 本文描述的是当前真实能力和分阶段交付边界，不代表一次性承诺支持所有格式。
> 文件输入 A–H 的发布门禁、回退与验收证据见 [`FILE_INPUT_CLOSURE_ACCEPTANCE.md`](./FILE_INPUT_CLOSURE_ACCEPTANCE.md)。
> 统一输出资产闭环的独立协议、边界和发布证据见 [`FILE_OUTPUT_CLOSURE_ACCEPTANCE.md`](./FILE_OUTPUT_CLOSURE_ACCEPTANCE.md) 与 [`file-output-readiness.json`](./file-output-readiness.json)。

## 0. 2026-08-06 图片、音频与视频能力复核

### 0.1 分类口径

模型招聘会的两个筛选维度不可互相替代：

| 筛选维度 | 回答的问题 | 数据来源 | 例子 |
|---|---|---|---|
| 工作技能（可接收输入） | 用户可以给模型什么 | `input_modalities` 精确匹配 | 图片输入、音频输入、视频输入、文件输入 |
| 岗位能力（可完成任务） | 模型和模镜当前能完成什么任务 | 输入/输出方向 + operation + 实时能力目录 | 图片识别、图片生成/编辑、语音转写、音乐生成、视频理解、视频生成 |

因此，“图片生成”模型不会因为输出图片而出现在“图片输入”筛选中；“音乐生成”也不会被误标为“音频理解”。宽泛的 `capabilities` 只保留作底层检索信号，不再作为这两个 UI 分类的判定依据。

### 0.2 实时目录核对结果

| 指标 | 数量 | 说明 |
|---|---:|---|
| OpenRouter 实时模型 | 462 | 2026-08-05 下载的通用目录 |
| 本地 OpenRouter 快照 | 517 | 含 55 个保留历史条目；本轮不删除旧模型 |
| 实时目录缺失于快照 | 0 | 已合并 15 个新增模型 |
| 重合模型模态不一致 | 0 | 输入、输出模态与实时目录一致 |
| 图片输入 | 239 | 仅表示模型接受图片，不等于输出文本 |
| 图片理解 | 182 | `image` 输入且 `text` 输出 |
| 专用图片生成 | 40 | 以 `/api/v1/images/models` 为准 |
| 音频输入 / 音频理解 | 45 / 26 | 理解要求同时输出文本 |
| 语音转写 / 语音合成 / 音频生成 | 14 / 19 / 4 | 按输出方向拆分 |
| 视频输入 / 视频理解 | 57 / 50 | 理解要求同时输出文本 |
| 专用视频生成 | 21 | 以 `/api/v1/videos/models` 为准 |

通用目录有 42 个图片输出条目，其中 `openrouter/auto` 和 `openrouter/auto-beta` 不在专用图片生成目录。两者继续作为聊天/调度入口，不能仅因声明图片输出就进入专用图片生成工作区。

### 0.3 本轮发现并修复的根因

1. 后端图片输入使用模型名称关键字白名单，不能覆盖新模型和非典型命名；现改为实时 `image → text` 能力确认，并使用 5 分钟缓存、30 分钟错误时缓存。
2. 前端曾用宽泛 `capabilities` 判断工作技能，把输入和输出混在一起；现改为只精确匹配 `input_modalities`。
3. 图片识别和图片生成曾都被归入普通 `chat`；现增加方向明确的 `analyze_image` 与 `generate_image`。
4. 旧图片生成走 Chat SSE，而 OpenRouter 当前使用专用 `/api/v1/images`；现增加专用能力目录、完整结果校验、能力驱动参数和图片生成/编辑工作区。
5. 原生模型目录曾只保存模型 ID 并默认文本能力；现保留连接目录返回的 `architecture.input_modalities` 与 `output_modalities`，避免原生路由和招聘会再次丢失模态。
6. 音频、视频卡片曾先把静态 operation 当作“实时确认”；现只有实时目录且本地交互状态为 `ready` 才显示已适配入口。静态快照仍可浏览，但不伪装为已验证调用。
7. 图片上传曾把 PNG、WebP 和 GIF 一律缩成最长边 1024px 的 JPEG，透明背景和细节会损失；现将识别输入上限提升到 2048px，优先保留 PNG/WebP，透明 PNG 只有在 5 MiB 限制下无法交付时才以白色背景降级为 JPEG。GIF 仍只按静态帧处理。

### 0.4 未验证模型收尾结论

- 21 个专用视频生成 profile 已逐批完成人工提交、轮询、完整播放和下载验收；高费用批次使用最低可用规格，Veo Lite 的受控高级参数也已验收。实时目录若新增模型，仍默认进入“需要人工核验”，不会自动继承 verified。
- `openai/gpt-transcribe`、`meta/muse-spark-1.2` 和 `thinkingmachines/inkling-small` 已完成短音频人工验收并固化到版本化档案，不再依赖 `MULTIMODAL_VERIFICATION_MODEL_IDS`。
- `gpt-4o-mini-tts` 的普通内置声线和 MP3 输出已接入现有朗读与语音生成入口；缺少直接 OpenAI `audio` 连接时准确显示“需配置”，不显示“待适配”。同一模型的声音克隆 operation 仍单独延期。
- `meta/muse-spark-1.1` 保留“上游暂不可用”结论；两个 Gemini `latest` 浮动别名不作为独立候选；这三项均保留原因和复核日期。
- 未出现在实时目录的 52 个模型不能仅凭目录缺失判定下线：继续计入现场候选人与适配口径，默认排在目录和分类底部并标记“可能不可用”。3 个明确到期模型默认隐藏且不计入现场口径。

可机读结论保存在 `docs/multimodal-readiness.json`。离线门禁会核对生命周期数量、状态原因、视频 verified registry 与人工证据，避免目录或文档再次漂移：

```text
node scripts/check-multimodal-readiness.mjs
```

复核脚本：

```text
node scripts/audit-model-modalities.mjs \
  --catalog <models.json> \
  --image-models <image-models.json> \
  --video-models <video-models.json>
```

脚本会在实时模型缺失、模态不一致、专用图片/视频模型未入快照或视频输出缺少专用契约时失败。历史条目只统计，不自动删除。

## 1. “全模态”的交付定义

模镜将“全模态”定义为三个互相独立的维度。任何能力只有同时写明这三个维度，才可以被称为“已支持”。

### 1.1 输入模态

| 输入模态 | 常见格式或载荷 | 本轮交付 |
|---|---|---|
| 文本 | TXT、Markdown、HTML、JSON、YAML、XML、代码、SQL、日志 | 审计现状与模块入口 |
| 图片 | JPEG/JPG、PNG、WebP、GIF、SVG、TIFF、HEIC/HEIF、BMP | 审计；不新增 GIF 动画处理 |
| 音频 | WAV、MP3、AAC、M4A、FLAC、OGG/Opus、WebM、WebRTC 媒体流 | 已交付 STT、TTS、Chat 音频附件、录音后转写、原生音频流、音乐生成和纯语音实时对话 |
| 视频 | MP4、MPEG、MOV、WebM、MKV、AVI | 已交付 MP4/MPEG/MOV/WebM 理解、Chat 单轮附件和异步生成闭环；MKV/AVI 仅审计 |
| 文档 | PDF、DOCX、PPTX、XLSX、CSV/TSV、EPUB、RTF、ODF | Chat、RAG、Data X 与 Workflow 已按任务边界接入；Agent 继续使用兼容文件上下文，统一资产 binding 迁移延期 |
| 结构化数据 | JSON、JSON Schema、表格、数据库查询结果、Parquet | 审计 Chat、RAG、Data X 分工 |
| 字幕与时间轴 | SRT、VTT、ASS/SSA、带时间戳的转录 JSON | 审计，归入音视频后处理 |
| 压缩包 | ZIP、7z、RAR、TAR.GZ | 只审计，待解压安全边界明确 |
| 空间与科学数据 | GeoJSON、GeoTIFF、LAS/LAZ、PLY/PCD、HDF5、NetCDF | 只审计 |
| 医学数据 | DICOM、NIfTI、FHIR JSON、HL7 文本 | 只审计，需独立隐私与安全设计 |
| 其他扩展 | 3D、点云、传感器时序、世界模型场景 | 只记录，不进入本轮实现 |

### 1.2 输出类型

输出不只等于模型目录中的 `output_modalities`。审计同时覆盖模型输出、平台转换产物和 Agent 事件：

- 文本、Markdown、代码块。
- JSON 与 JSON Schema 约束输出。
- 图片生成、编辑和局部重绘。
- 语音合成、音乐/通用音频生成、实时语音流。
- 视频生成。
- PDF、DOCX、PPTX、XLSX 等文件与报告。
- Embedding、分类标签、检测框、时间轴、字幕。
- 工具调用、工具结果、审批事件、工作流节点事件和 Agent 事件流。

### 1.3 能力层级

| 层级 | 定义 | 示例 |
|---|---|---|
| 模型原生支持 | 目标模型直接接收或生成该模态，平台只做协议适配 | 视觉模型直接接收 JPEG；TTS 模型直接返回 MP3 |
| 转换后支持 | 平台先解析、转码或提取，再交给模型 | PDF 提取文本；XLSX 转为带 Sheet 标记的文本；视频拆帧 |
| 组合支持 | 多个模型或模块组成一条明确流水线 | ASR → LLM → TTS 语音对话 |
| 降级支持 | 原目标不支持时，在用户知情的情况下转换或改路由 | 音频先转录为文本；视觉任务改路由至视觉模型 |
| 不支持 | 当前没有可验收的后端链路和 UI 入口 | 模型目录收录视频模型，但平台无法提交视频 |

“降级支持”必须显示发生了什么，不允许静默改变模态、模型或费用。

## 2. 平台支持与模块支持

### 2.1 状态词

| 状态 | 判定标准 |
|---|---|
| 已支持 | 至少有一个稳定模块同时具备 UI 入口、后端契约、错误处理和回归测试 |
| 部分支持 | 仅特定模块或特定格式可用，其他入口不得宣称可用 |
| 计划中 | 模型或底层网关具备能力，但模镜尚无完整入口 |
| 仅审计 | 本路线记录该能力，不进入当前开发与验收 |
| 不适用 | 该能力不应出现在此模块，例如 Embedding 不应进入 Chat |

模型目录中的“可调用”只表示连接、凭据和模型状态允许请求，不等于模镜已经提供对应交互。

### 2.2 模块边界矩阵

| 能力 | 模型目录 | Chat | RAG | Data X | Agent / 智能体 | 工作流 | 专用媒体入口 | 平台结论 |
|---|---|---|---|---|---|---|---|---|
| 文本对话与 Markdown | 已收录 | 已支持，原生 | 检索后组合 | 不适用 | 已支持 | 已支持 | 不适用 | 已支持 |
| 图片理解 | 已收录 | 已支持，模型原生 | 已支持，视觉转换/组合 | 不适用 | 文件能力按配置 | 节点能力按配置 | 不适用 | 已支持 |
| 图片生成 | 专用实时目录确认 | 独立图片生成/编辑工作区，不复用 Chat SSE | 不适用 | 不适用 | 按模型配置 | 按节点配置 | `/chat/:modelId?operation=generate_image` | 已支持，OpenRouter-first |
| GIF 动画 | 已收录为图片 | 当前压缩会静态化 | 当前按静态图片处理 | 不适用 | 未验证 | 未验证 | 无 | 仅静态首帧，不算动画支持 |
| PDF | 已收录为 file | 原生读取、本地提取及一次性视觉/OCR实现已接线；后两种外发模式默认关闭，待真实金丝雀 | 已支持，文本/视觉转换 | 不适用 | 文件能力按配置 | 文件节点按配置 | 无 | 一次性视觉/OCR使用显式目标、逐文件确认；不会隐式付费 |
| 音频转写 STT | 实时与本地档案确认 | Chat 媒体面板可上传或录音后转写并编辑 | 不适用 | 不适用 | Agent Runtime 可按配置调用 | 无 | 独立深链兼容保留 | 已支持，OpenRouter-first |
| 文字转语音 TTS | 实时与版本化档案确认 | 助手回答可朗读，自动朗读默认关闭 | 不适用 | 不适用 | Agent Runtime 可按配置调用 | 无 | 独立深链兼容保留 | 已支持，按已验证模型/声线开放 |
| 音频理解 | 实时与本地档案确认 | 已验证模型可直接理解；普通模型和 auto 显式先转写 | 不适用 | 不适用 | 按模型配置 | 无 | 无 | 部分支持，OpenRouter-first |
| 原生语音输出 | 实时与本地档案确认 | 已验证的 GPT Audio 模型可返回流式语音；失败保留文字 | 不适用 | 不适用 | 按模型配置 | 无 | 无 | 部分支持 |
| 音乐生成 | 实时与版本化档案确认 | 不进入朗读模型池 | 不适用 | 不适用 | 未形成通用能力 | 无 | 独立异步音乐工作区 | 已支持，Lyria Clip/Pro |
| 实时双向语音 | 2 个直接 OpenAI 档案 | 独立纯语音工作区，不进入普通消息流 | 不适用 | 不适用 | 不组合 Agent 工具 | 不适用 | `/chat/:modelId?operation=realtime_voice` | 已支持，需直接 OpenAI `audio+realtime` 连接 |
| 声音克隆 | 能力可识别 | 不开放上传或创建 | 不适用 | 不适用 | 不开放 | 不适用 | 仅安全占位 | 计划中，上游无法验证删除临时音色 |
| 视频理解 | 实时能力确认 | 本地附件可由当前模型直接理解或先生成辅助摘要 | 无拆帧流水线 | 不适用 | 未形成通用能力 | 无 | 文件或 HTTPS/YouTube URL | 已支持，OpenRouter-first |
| 视频生成 | 实时能力确认 | 独立异步工作区，不复用 Chat SSE | 不适用 | 不适用 | 未形成通用能力 | 无 | 文生视频、首尾帧、最多三参考图、受控高级参数 | 已支持，OpenRouter-first |
| XLSX | 归类为 file | 已支持本地结构化预览，确认后发送 | 已支持按 Sheet 语义检索 | 已支持专业结构化分析 | 本批未接入 | 通过固定工作流作用域的资产变量提取 | 不适用 | 已在 Chat、RAG、Data X、Workflow 按不同任务闭环 |
| CSV / TSV | 归类为 file | 已支持有界表格预览和来源行号 | 已支持语义检索 | 已支持 CSV | 文件能力按配置 | 文件节点按配置 | 不适用 | 已支持转换后输入 |
| JSON / JSONL / YAML / XML / HTML | 归类为 file | 已支持安全预检、结构保真或安全正文提取 | 已支持语义检索 | 不默认进入 | 文件能力按配置 | 文件节点按配置 | 不适用 | 已支持转换后输入 |
| SRT / VTT、源码、配置和日志 | 归类为 file | 已支持时间轴或行号来源预览 | 已支持语义检索 | 不适用 | 文件能力按配置 | 文件节点按配置 | 不适用 | 已支持转换后输入 |
| Parquet | 未作为聊天模态 | 不适用 | 不支持 | 已支持 | 文件能力按配置 | 文件节点按配置 | 不适用 | 部分支持 |
| Embedding | 已收录 | 不适用 | 已支持，专用配置 | 不适用 | 可作为检索依赖 | 可作为检索节点 | RAG 设置 | 已支持，入口仅在 RAG |
| Rerank | 已收录 | 不适用 | 已支持，专用 API/LLM | 不适用 | 可作为检索依赖 | 可作为检索节点 | RAG 设置 | 已支持，入口仅在 RAG |
| JSON Schema 约束输出 | 参数已收录 | 后端有兼容基础，缺少统一 UI | 流水线配置部分可用 | 不适用 | Agent 工具 schema 可用 | 节点 schema 可用 | 无 | 部分支持 |
| 工具调用与 Agent 事件 | 参数已收录 | 普通聊天不展示完整工具生命周期 | 不适用 | 不适用 | 已支持工具调用、审批和运行事件 | 已支持节点事件 | 运行诊断 | 部分支持 |
| 文件与报告生成 | 不完全由模型模态表达 | 无统一下载契约 | 不适用 | 可导出数据结果 | 文档工具按配置 | 工具节点按配置 | 无统一入口 | 部分支持 |

### 2.3 UI 入口规则

- `chat`：文本、图片、音频、本地视频及已验证的常用文件附件；文件必须先预览确认，不支持当前媒体模态的模型必须显式经过 STT 或视频理解辅助模型。
- `rag`：资料上传、Embedding、Rerank、引用检索和检索流水线。
- `datax`：CSV、XLSX、Parquet 的结构化分析，不承担知识库语义切片。
- `agents`：配置化媒体工具、工具调用、审批和 Agent 事件。
- `workflow`：节点编排和节点事件，不作为通用媒体播放器。
- `multimodal`：保留图片生成/编辑、STT/TTS 兼容深链、音乐/视频异步任务、视频 URL 分析和实时语音工作区；普通音视频附件统一从 Chat 媒体面板进入。
- `models`：保留 517 个 OpenRouter 快照条目，并单独展示 2 个直接 OpenAI Realtime 档案和 1 个 World Labs 档案；卡片只负责状态和入口，不直接承担推理。

## 3. 当前格式清单

### 3.1 文本、文档与结构化数据

| 格式 | Chat | RAG | Data X | 支持层级 | 后续动作 |
|---|---|---|---|---|---|
| TXT | 已支持附件和本地预览 | 已支持 | 不适用 | 转换后 | 保持 |
| MD / Markdown | 已支持附件和本地预览 | 已支持 | 不适用 | 转换后 | 保持 |
| PDF | 已支持原生读取或本地提取；一次性视觉与供应商 OCR 均通过真实金丝雀且默认关闭 | 已支持文本与视觉处理 | 不适用 | 原生/转换后 | 视觉理解与 OpenRouter mistral-ocr 分别受独立开关和真实金丝雀发布闸控制 |
| PNG / JPG / JPEG / WebP | 已支持图片输入 | 已支持视觉处理 | 不适用 | 模型原生/组合 | 保持 |
| XLSX | 已支持按 Sheet 本地预览 | 已支持按 Sheet 语义检索 | 已支持专业分析 | 转换后/专用 | Chat、RAG、Data X 与 Workflow 分流明确；Agent 统一资产迁移延期 |
| CSV / TSV | 已支持有界表格预览 | 已支持语义检索 | 已支持 CSV | 转换后 | 保持 Data X 专业分析分工 |
| Parquet | 不适用 | 未支持 | 已支持 | 转换后 | 保持 Data X 专用 |
| HTML / JSON / JSONL / YAML / XML | 已支持安全文件预览 | 已支持语义检索 | JSON 可经其他入口使用 | 转换后 | 保持深度、节点和主动内容门禁 |
| 常见源码、配置和日志 | 已支持并保留行号/语义布局 | 已支持语义检索 | 不适用 | 转换后 | 不做 NFKC 归一化 |
| DOCX / PPTX | 已支持隔离静态提取与预览 | 已支持隔离解析和来源引用 | 不适用 | 转换后 | Workflow 复用同一断网 sidecar；宏、ActiveX、OLE 与外部资源拒绝，图片仅占位且不自动调用视觉模型 |
| EPUB / RTF / ODT / ODS / ODP | 不支持 | 不支持 | 不适用 | 无 | 仅审计 |
| XLS / DOC / PPT | 不支持 | 不支持 | 不适用 | 无 | 需要隔离转换方案 |
| SRT / VTT | 已支持并保留时间轴 | 已支持语义检索 | 不适用 | 转换后 | ASS / SSA 继续延期 |
| ZIP / 7z / RAR / TAR.GZ | 不支持 | 不支持 | 不支持 | 无 | 完成路径穿越、炸弹和嵌套限制后再评估 |

### 3.2 图片

| 格式 | 当前行为 | 真实结论 |
|---|---|---|
| JPEG / JPG | 浏览器压缩后提交 | 已支持 |
| PNG | 浏览器压缩后提交，透明通道可能丢失 | 部分支持 |
| WebP | 浏览器解码并压缩后提交 | 已支持 |
| GIF | Canvas 压缩后只保留静态画面 | 不支持动画 |
| SVG | 未接受 | 仅审计；需防脚本与外部资源 |
| TIFF | 未接受 | 仅审计；需多页与高位深处理 |
| HEIC / HEIF | 未接受 | 仅审计；浏览器兼容性有限 |
| BMP | 未接受 | 仅审计；可转换但体积较大 |
| DICOM | 未接受 | 仅审计；需要隐私、窗宽窗位与多帧设计 |

### 3.3 音频与视频

| 格式 | STT | TTS 输出 | Chat 理解 | 视频任务 |
|---|---|---|---|---|
| WAV、MP3、AAC、M4A、FLAC、OGG、WebM | Chat 可上传或录音后转写；支持模型可直接理解部分格式 | 不适用 | 原生或 STT 组合支持 | 不适用 |
| MP3 | 可转写或直接理解 | MAI-Voice-2 朗读；已验证模型可原生流式输出 | Chat 内可播放，独立深链可下载 | 不适用 |
| PCM | 不适用 | 上游可能支持，首期不开放 | 不适用 | 不适用 |
| WebRTC 媒体流 | 不适用 | 模型原生双向流 | 仅实时语音工作区，不写入普通 Chat 历史 | 不适用 |
| MP4、MPEG、MOV、WebM | 不适用 | 不适用 | 模型原生理解，文件最大 20 MiB | 异步生成结果可播放与下载 |
| MKV、AVI | 不适用 | 不适用 | 未接受，需转码后支持 | 仅审计 |

## 4. 判定示例

### 示例 A：XLSX

“平台支持 XLSX”仍不等于每个模块采用同一种处理方式。

```text
格式：XLSX
Chat：本地只读提取，逐文件预览确认后作为非可信用户数据发送
RAG：按工作表与单元格范围保留来源，用于语义检索
Data X：保留 50 MiB、100 万行的专业分析边界，公式不执行
能力层级：Chat/RAG 为转换后支持；Data X 为专用分析
UI：上传时显式选择“与模型讨论”“加入资料库”或“用 Data X 分析”；切换模块需在目标页重新选择文件
限制：Chat/RAG 单文件 10 MiB、最多 50 个可见 Sheet、100,000 个非空单元格、每 Sheet 200 列
```

### 示例 B：Embedding 模型

```text
输出：Embedding
模型目录：已收录
RAG：已支持，用于向量化与检索
Chat：不适用，不展示“立即面试”
能力层级：模型原生支持
UI：RAG 的 Embedding 设置
```

### 示例 C：MP3

```text
输入：MP3
Chat：可上传音频或录音；默认先转成文字并由用户预览编辑
支持音频的显式模型：可在转写设置中选择直接理解
普通模型与 auto：必须显式选择 STT 辅助模型，不静默切换
能力层级：直接理解为模型原生；STT → LLM 为组合支持
UI：统一位于 Chat 媒体面板，独立 STT 深链仅保留兼容
```

### 示例 D：视频模型

```text
输入/输出：视频
模型目录：必须经实时能力目录确认
视频理解：支持 MP4/MPEG/MOV/WebM 文件，以及 HTTPS/YouTube URL
视频生成：独立异步任务，支持文生视频、首尾帧、最多三张参考图和受控供应商参数
平台状态：OpenRouter-first 已支持
UI：模型卡进入“分析视频”或“生成视频”专用工作区
说明：视频生成任务不会进入普通 Chat SSE；未知费用显示“以网关结算为准”
```

## 5. 本轮实现与明确不实现

### 实现

1. 目录保留输入与输出模态，并生成真实 operation。
2. 模型卡把 Chat、RAG 与待适配媒体任务分流。
3. 通用 STT 文件上传。
4. 通用 TTS MP3 生成。
5. 视频理解文件与 URL 输入。
6. Chat 音频上传、录音后转写、原生音频流和显式辅助 TTS。
7. Chat 本地视频附件的直接理解与辅助摘要。
8. 视频生成异步任务、状态恢复、首尾帧、多参考图、受控高级参数、播放器与下载代理。
9. Lyria Clip/Pro 音乐生成、幂等任务、临时播放器和下载。
10. 直接 OpenAI WebRTC 实时语音、语义 VAD、自然打断、静音、显式重连和 10 分钟硬上限。

实时翻译、语音/视频电话、SIP、自定义持久音色库、实时工具调用、音频 URL、Chat 视频 URL、Agent 统一文件资产 binding、跨模块免重选转交和其他尚未开放格式继续延期，必须重新建立独立验收路线。

### Recraft V4 Styles 图片契约

2026-08-26 对 OpenRouter 专用 Images 目录与四个端点逐项核对后，已适配 `recraft/recraft-v4-styles`、`recraft/recraft-v4-styles-pro`、`recraft/recraft-v4-styles-vector` 和 `recraft/recraft-v4-styles-pro-vector`：

- 四款均走 `POST /api/v1/images`，不进入 Chat SSE；输入为文本与 1–10 张风格参考图，单次输出 1–6 张图片，支持 12 种画幅比例。
- 每张参考图只接受 JPG、PNG 或 WebP，短边至少 256 像素；前后端都阻止缺少参考图的付费请求。
- Vector 两款只开放目录声明的 `output_format=svg`，返回内容继续经过完整 base64、MIME 与 SVG 文件签名校验。
- 费用由“每张输出图 + 每次请求一次风格创建”组成；风格创建费为 $0.005/请求，不按参考图数量重复计算。四款输出价依次为 $0.035、$0.10、$0.05、$0.12/张。
- 端点列出的 `style_id`、`style_match`、`controls` 和 `random_seed` 缺少公开类型约束，本轮不猜测 UI 或任意透传 JSON。
- 本轮只完成公开契约、模拟提交、输出校验和费用估算回归；未执行真实付费生成，不将其描述为人工成片验收。

### 通用 STT 后端契约

批次 B 新增：

```http
POST /api/multimodal/transcriptions
Content-Type: multipart/form-data
```

```text
model_id = openai/whisper-1
language = auto
file = sample.wav
```

成功响应示例：

```json
{
  "text": "这是识别出的文字。",
  "requested_model": "openai/whisper-1",
  "actual_model": "openai/whisper-1",
  "provider": "openrouter",
  "request_id": "decision_...",
  "usage": {
    "audio_seconds": 3.2,
    "input_tokens": null,
    "output_tokens": null,
    "total_tokens": null,
    "cost_usd": 0.0006,
    "cost_kind": "actual"
  }
}
```

- 接受 WAV、MP3、FLAC、M4A、OGG、WebM、AAC，最大 25 MiB。
- 首期只使用已启用的 OpenRouter 连接；没有连接时才读取后端 OpenRouter 环境密钥。
- 调用前通过 OpenRouter 转录模型目录验证 operation，不允许 `auto` 或普通 Chat 模型进入付费请求。
- 音频以 multipart 转发，不进行 base64 膨胀，不持久化文件。
- 审计只记录 tenant、operation、模型、连接、字节数、时长、费用和 request ID。
- 批次 B 只有后端 API；批次 C 已增加模型页自适应转录工作区，提供上传、替换、移除、语言选择、进度、取消、重试、预听和复制结果。

### 通用 TTS 契约

批次 D 新增：

```http
POST /api/multimodal/speech
Content-Type: application/json
```

```json
{
  "model_id": "microsoft/mai-voice-2",
  "input": "Welcome to ModelMirror.",
  "voice": "en-US-Harper:MAI-Voice-2",
  "response_format": "mp3",
  "speed": 1.0
}
```

- TTS 下拉框来自实时目录与版本化行为档案的交集；未通过模型、声线和格式验证的条目保持待适配。
- 输入不能为空且最多 4,000 个字符；速度限定为 `0.5–2.0`，输出格式按模型档案开放 MP3 或 WAV。
- 后端完整接收上游响应，并按能力档案校验 MP3 MIME/文件签名或 PCM 完整性；WAV 由后端补齐标准容器头。所有格式同时执行非空内容和 20 MiB 安全上限检查，校验通过后才返回浏览器。
- 成功响应为能力档案声明的 MP3 或 WAV 字节；脱敏响应头提供 request ID、实际模型、供应商、费用状态和输出字节数。
- 文字和音频不写入数据库；审计仅记录租户、operation、模型、连接、输入/输出字节数、状态和可用费用信息。
- 前端自适应语音生成工作区提供文字保留、已验证声线、语速、取消、重试、播放器和下载；替换结果或离开页面时释放 Blob URL。

### 音乐生成与实时语音

音乐生成使用独立任务，不进入普通 Chat SSE：

```http
POST   /api/multimodal/audio/jobs
GET    /api/multimodal/audio/jobs
GET    /api/multimodal/audio/jobs/{job_id}
GET    /api/multimodal/audio/jobs/{job_id}/content
DELETE /api/multimodal/audio/jobs/{job_id}
```

- 当前开放完成行为验证的 Lyria Clip/Pro；Pro 图片提示先按能力档案开放。
- `idempotency_key` 防止重复付费请求；后端完整校验音频后才标记成功。
- SQLite 不保存 Prompt、图片或音频正文；结果只在非持久化目录保留 30 分钟。
- 容器重建后任务元数据可保留，但临时音频正文不保证恢复。

实时语音使用独立 WebRTC 生命周期：

```http
POST   /api/multimodal/realtime/calls
DELETE /api/multimodal/realtime/calls/{session_id}
```

- 浏览器只把 SDP 交给模镜后端，永久 OpenAI Key 不进入前端。
- 音频在浏览器与 OpenAI 之间通过 WebRTC 传输，模镜不转发或保存 PCM。
- 默认 `gpt-realtime-2.1-mini + marin + semantic_vad`，可切换质量版和 Cedar。
- 单次最多 10 分钟，结束前 60 秒提示；网络中断只提供显式重新连接，不自动创建新付费会话。
- 首期只做纯语音，不组合 RAG、Skill、MCP、Agent、附件或 `/chat/auto`。
- 两张模型卡始终可进入工作区；未配置直接 OpenAI 连接时显示配置建议，不伪装为可调用。

### 视频理解与生成闭环

视频理解使用供应商无关的专用接口：

```http
GET  /api/multimodal/video/models
POST /api/multimodal/video/analysis
```

- 本地视频接受 MP4、MPEG、MOV、WebM，最大 20 MiB；请求内转换为 data URL，不写入磁盘或数据库。
- 网络来源只接受 HTTPS 与受支持的 YouTube URL；后端不下载目标 URL，避免形成 SSRF 下载代理。
- 提示词长度为 1–4,000 字符，提交前验证模型 operation 和实时交互状态。
- UI 支持本地预览、URL 输入、替换、移除、取消请求、重试和复制分析结果。

视频生成使用独立异步任务，不复用 `/api/chat` SSE：

```http
POST   /api/multimodal/video/jobs
GET    /api/multimodal/video/jobs
GET    /api/multimodal/video/jobs/{job_id}
POST   /api/multimodal/video/jobs/{job_id}/refresh
GET    /api/multimodal/video/jobs/{job_id}/content?index=0
DELETE /api/multimodal/video/jobs/{job_id}
```

- 支持文生视频、首帧、尾帧和最多三张参考图；图片只接受 JPEG/PNG/WebP，每张最大 10 MiB，参考图合计最大 30 MiB。
- 首尾帧以实时 `supported_frame_images` 为准；参考图仅开放完成本地契约审计的模型。
- `alibaba/wan-3.0` 按专用目录开放 2–30 秒、480p/720p/1080p、首帧、单张参考图、生成音频与 seed，并按分辨率视频秒估价。
- `heygen/avatar-iv` 不发送 `duration`，成片时长由文本脚本与上游决定；只开放已公开的文本脚本、单张人物参考图、分辨率和画幅字段。模型说明提到的外部音轨尚无公开请求字段，本地不猜测或透传。
- 高级参数必须同时存在于实时允许列表和本地有类型定义中，不接受供应商任意 JSON；能力目录过期时必须刷新后再提交。
- SQLite 只持久化 `tenant_id="local"`、模型、参数摘要、图片布尔值/数量、高级参数键名、上游任务 ID、状态、费用和错误码，不保存 Prompt、图片、高级参数值或视频正文。
- `idempotency_key` 在付费上游调用前原子占位，防止重复点击产生第二次任务。
- 运行中任务默认 30 秒轮询，短时错误按 30/60/120 秒退避；页面隐藏时暂停，恢复可见时立即刷新。
- “停止关注”只停止当前页面轮询；“移除记录”只删除本地元数据，均不伪装成取消上游生成。
- 成功内容经后端鉴权代理流式下载，前端不接触上游密钥、签名 URL 或轮询地址。
- 费用未知时不显示零成本；提交区提示异步等待、可能产生费用和 OpenRouter 视频生成不支持 ZDR。

运维开关写入后端环境文件 `server/.env`：

```text
MULTIMODAL_VIDEO_ANALYSIS_ENABLED=true
MULTIMODAL_VIDEO_GENERATION_ENABLED=true
MULTIMODAL_CHAT_AUDIO_ENABLED=true
MULTIMODAL_MICROPHONE_ENABLED=true
MULTIMODAL_STREAMING_AUDIO_ENABLED=true
MULTIMODAL_CHAT_VIDEO_ENABLED=true
MULTIMODAL_AUDIO_GENERATION_ENABLED=true
MULTIMODAL_REALTIME_VOICE_ENABLED=true
MULTIMODAL_VOICE_CLONING_ENABLED=false
```

任一开关设为 `false` 后，模型仍保留在目录，但对应入口显示“当前未启用”；文本、图片、STT、TTS、RAG、工作流和智能调度不受影响。

直接 OpenAI Realtime 还要求在设置页创建 `openai` 类型连接，地址必须为
`https://api.openai.com/v1`，用途范围为 `audio + realtime`。环境开关不能替代连接和密钥。
普通 OpenAI TTS 同样复用该连接的 `audio` scope；模型卡和朗读入口在缺少连接时显示“需配置”，连接就绪后无需另开实验白名单。
声音克隆开关保持关闭；本轮没有上传授权录音、创建音色或绕过删除安全门禁的接口。

### 只审计

- GIF 动画、SVG 主动内容、HEIC/TIFF 高级处理。
- 视频音轨单独识别、视频多轮原始媒体上下文。
- 实时翻译、语音/视频电话、电话/SIP 接入、实时工具调用和自定义持久音色库。
- Agent 的统一文件资产 binding、跨进程运行租约，以及跨模块免重选转交。
- 3D、点云、传感器、科学数据和医学影像。
- 压缩包、旧版 Office，以及 DOCX/PPTX 中无法由静态结构可靠还原的复杂版式与修订细节。

## 6. 文档验收标准

本文档只有满足以下条件才算完成：

1. 每个能力都同时标注格式、模块、能力层级、状态和 UI 入口。
2. “模型目录收录”“网关可调用”“模块可用”“平台已支持”不混用。
3. Chat、RAG、Data X、Agent、Workflow 和专用媒体入口均有清晰边界。
4. 已支持项能找到真实代码或测试证据；无法验证的项目只能标为“计划中”或“仅审计”。
5. GIF、视频、3D、医学影像等不会因目录标签被误写成当前平台能力。
6. 新增格式必须补充大小、数量、超时、安全、隐私和降级约束后才能进入实施。

### 6.1 文件格式 readiness 离线门禁

文件格式的机读审计证据保存在 `docs/file-readiness.json`。它只记录 Chat、RAG、Data X、智能体和工作流各自的真实状态，不作为后端运行时配置；一个模块支持某种格式，不代表平台其他模块自动支持。

已上线但安全护栏尚未完全闭环的能力使用 `ready + contract_verified`，与具备完整安全测试证据的 `ready + verified` 分开统计。计划或关闭的格式必须保留可理解的原因，不能用无说明的“待适配”代替。

Chat 扫描 PDF 与 JPEG/PNG/WebP 的“一次性视觉识别 / 供应商 OCR”入口已实现：当前文件面板展示明确连接、模型、页数、费用与隐私说明，服务端 revision 绑定文件哈希、作用域、页范围、提示哈希和付费确认，结果预览后才能选择发送或保存到资料库。两个功能开关默认关闭。视觉路径已使用公开合成单页样本，经 OpenRouter 精确调用 `google/gemini-3.5-flash-lite` 完成一次真实金丝雀；供应商 OCR 在两轮真实调用暴露并修复 annotation 文件包装、页归属及非 2xx `error.metadata.file_annotations` 接收问题后，再次使用同一公开样本完成最终真实金丝雀，页数为 1，无包装或内嵌图片，实际成本 `$0.0020246`。两条路径均为单次请求、无重试、无 fallback，且仍需显式开启各自功能开关。

Batch G 的生命周期边界如下：RAG 文档删除先进入不可检索 tombstone，再清理原件、活动/历史索引、流水线 snapshot、处理产物与视觉缓存；清理失败保持 `cleanup_pending` 并允许从资料库作用域恢复重试。删除单个文档或整个资料库时只解绑对应 RAG binding，其他模块仍引用的共享 blob 保留。整库删除在用户明确授权后采用持久 scope tombstone 与 asset ledger：先原子隔离资料库并停止新写入，再等待流水线停写、严格清理派生物和无引用资产；任何步骤失败都保留可刷新、可重试状态，不会提前报告删除完成。

Xpert 兼容文件上下文恢复时不自动勾选历史附件，发送只消费本轮显式选择；旧 DELETE 保留一版归档语义，永久删除使用独立 purge 入口并在活跃读取时返回冲突。该 claim 仅覆盖单进程读取阶段，统一 FileAssetService binding 与跨进程运行租约仍延期。

Workflow 使用 `workflow:{workflow_id}` 固定作用域、`assetIdVariable` 和真实上传/已有资产选择；`WORKFLOW_FILE_ASSETS_ENABLED=false` 与非 shadow/native 存储模式均 fail closed。旧 `sourcePathVariable` 只读兼容一版，不允许新建或与资产变量并存。

离线检查会重算统计、验证证据路径，并确认 RAG、Data X、智能体和 Workflow 的后端解析与前端上传入口均接入各自声明的文件能力契约；运行时能力值则由 `server/tests/test_file_assets.py` 与审计报告逐项对账：

```text
node scripts/check-file-readiness.mjs
```

## 7. 接口依据

- OpenRouter Multimodal Overview: <https://openrouter.ai/docs/guides/overview/multimodal/overview>
- OpenRouter Speech-to-Text: <https://openrouter.ai/docs/guides/overview/multimodal/stt>
- OpenRouter Text-to-Speech: <https://openrouter.ai/docs/guides/overview/multimodal/tts>
- OpenRouter Audio: <https://openrouter.ai/docs/guides/overview/multimodal/audio>
- OpenRouter PDFs: <https://openrouter.ai/docs/guides/overview/multimodal/pdfs>
- OpenRouter Video Understanding: <https://openrouter.ai/docs/guides/overview/multimodal/videos>
- OpenRouter Video Generation: <https://openrouter.ai/docs/guides/overview/multimodal/video-generation>
- OpenAI Realtime WebRTC: <https://developers.openai.com/api/docs/guides/realtime-webrtc>
