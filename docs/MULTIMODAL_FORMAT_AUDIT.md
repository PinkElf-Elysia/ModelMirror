# 模镜全模态与常用格式缺口审计

> 审计基线：`origin/main` at `b622d3f`，模型目录快照 2026-07-28，共 493 个模型。
> 本文描述的是当前真实能力和分阶段交付边界，不代表一次性承诺支持所有格式。

## 1. “全模态”的交付定义

模镜将“全模态”定义为三个互相独立的维度。任何能力只有同时写明这三个维度，才可以被称为“已支持”。

### 1.1 输入模态

| 输入模态 | 常见格式或载荷 | 本轮交付 |
|---|---|---|
| 文本 | TXT、Markdown、HTML、JSON、YAML、XML、代码、SQL、日志 | 审计现状与模块入口 |
| 图片 | JPEG/JPG、PNG、WebP、GIF、SVG、TIFF、HEIC/HEIF、BMP | 审计；不新增 GIF 动画处理 |
| 音频 | WAV、MP3、AAC、M4A、FLAC、OGG/Opus、WebM | 先交付通用 STT，再交付 TTS |
| 视频 | MP4、MOV、WebM、MKV、AVI、MPEG | 只审计，音频闭环后实施 |
| 文档 | PDF、DOCX、PPTX、XLSX、CSV/TSV、EPUB、RTF、ODF | 只审计；RAG XLSX 延后到专项路线审计 |
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
- 语音合成、通用音频生成。
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

| 能力 | 模型目录 | Chat | RAG | Data X | Xpert / Agent | 工作流 | 专用媒体入口 | 平台结论 |
|---|---|---|---|---|---|---|---|---|
| 文本对话与 Markdown | 已收录 | 已支持，原生 | 检索后组合 | 不适用 | 已支持 | 已支持 | 不适用 | 已支持 |
| 图片理解 | 已收录 | 已支持，模型原生 | 已支持，视觉转换/组合 | 不适用 | 文件能力按配置 | 节点能力按配置 | 不适用 | 已支持 |
| 图片生成 | 已收录 | 已支持，模型原生 | 不适用 | 不适用 | 按模型配置 | 按节点配置 | 无 | 部分支持 |
| GIF 动画 | 已收录为图片 | 当前压缩会静态化 | 当前按静态图片处理 | 不适用 | 未验证 | 未验证 | 无 | 仅静态首帧，不算动画支持 |
| PDF | 已收录为 file | 无附件契约 | 已支持，文本/视觉转换 | 不适用 | 文件能力按配置 | 文件节点按配置 | 无 | 部分支持 |
| 音频转写 STT | 已收录 | 自适应转录工作区 | 不适用 | 不适用 | 已有按 Xpert 版本配置的入口 | 无 | 本轮新增 | 通用入口已实现，待人工验收 |
| 文字转语音 TTS | 已收录 | 自适应语音生成工作区 | 不适用 | 不适用 | 已有按 Xpert 版本配置的入口 | 无 | 本轮新增 | MAI-Voice-2 通用入口已实现，待人工验收 |
| 音频理解 | 已收录 | 无 `input_audio` 契约 | 不适用 | 不适用 | 未形成通用能力 | 无 | 无 | 计划中 |
| 通用音频生成 | 已收录 | 无音频增量解析 | 不适用 | 不适用 | 未形成通用能力 | 无 | 无 | 计划中 |
| 视频理解 | 已收录 | 无 `video_url` 契约 | 无拆帧流水线 | 不适用 | 未形成通用能力 | 无 | 无 | 计划中 |
| 视频生成 | 已收录 | 不适合 Chat SSE | 不适用 | 不适用 | 未形成通用能力 | 无 | 无异步任务中心 | 计划中 |
| XLSX | 归类为 file | 不支持 | 未支持，待专项审计 | 已支持，结构化解析 | 文件能力按配置 | 文件节点按配置 | 不适用 | 部分支持，仅 Data X 等特定模块 |
| CSV / TSV | 归类为 file | 不支持 | 尚未纳入资料库 | 已支持 CSV | 文件能力按配置 | 文件节点按配置 | 不适用 | 部分支持 |
| Parquet | 未作为聊天模态 | 不适用 | 不支持 | 已支持 | 文件能力按配置 | 文件节点按配置 | 不适用 | 部分支持 |
| Embedding | 已收录 | 不适用 | 已支持，专用配置 | 不适用 | 可作为检索依赖 | 可作为检索节点 | RAG 设置 | 已支持，入口仅在 RAG |
| Rerank | 已收录 | 不适用 | 已支持，专用 API/LLM | 不适用 | 可作为检索依赖 | 可作为检索节点 | RAG 设置 | 已支持，入口仅在 RAG |
| JSON Schema 约束输出 | 参数已收录 | 后端有兼容基础，缺少统一 UI | 流水线配置部分可用 | 不适用 | Agent 工具 schema 可用 | 节点 schema 可用 | 无 | 部分支持 |
| 工具调用与 Agent 事件 | 参数已收录 | 普通聊天不展示完整工具生命周期 | 不适用 | 不适用 | 已支持工具调用、审批和运行事件 | 已支持节点事件 | 运行诊断 | 部分支持 |
| 文件与报告生成 | 不完全由模型模态表达 | 无统一下载契约 | 不适用 | 可导出数据结果 | 文档工具按配置 | 工具节点按配置 | 无统一入口 | 部分支持 |

### 2.3 UI 入口规则

- `chat`：文本、图片理解、当前已适配的图片生成。
- `rag`：资料上传、Embedding、Rerank、引用检索和检索流水线。
- `datax`：CSV、XLSX、Parquet 的结构化分析，不承担知识库语义切片。
- `xpert / agents`：配置化 STT/TTS、工具调用、审批和 Agent 事件。
- `workflow`：节点编排和节点事件，不作为通用媒体播放器。
- `multimodal`：通用 STT、TTS 及后续音视频任务入口；首期仍复用模型详情到 ChatPage 的自适应布局，不新增顶级导航。
- `models`：只展示真实能力、适配状态和正确入口，不直接承担推理。

## 3. 当前格式清单

### 3.1 文本、文档与结构化数据

| 格式 | Chat | RAG | Data X | 支持层级 | 后续动作 |
|---|---|---|---|---|---|
| TXT | 文本粘贴，不支持附件 | 已支持 | 不适用 | 转换后 | 保持 |
| MD / Markdown | 文本粘贴，不支持附件 | 已支持 | 不适用 | 转换后 | 保持 |
| PDF | 不支持附件 | 已支持文本与视觉处理 | 不适用 | 转换后 | 后续补 Chat `file` |
| PNG / JPG / JPEG / WebP | 已支持图片输入 | 已支持视觉处理 | 不适用 | 模型原生/组合 | 保持 |
| XLSX | 不支持 | 未支持 | 已支持 | 转换后 | 下一轮先审计解析路线、资源边界与 RAG 入口 |
| CSV | 不支持 | 未支持 | 已支持 | 转换后 | 与 RAG 常用表格格式一起重新排序 |
| Parquet | 不适用 | 未支持 | 已支持 | 转换后 | 保持 Data X 专用 |
| HTML / JSON / YAML / XML | 文本粘贴 | 未作为文件支持 | JSON 可经其他入口使用 | 转换后 | 第二轮常用文本文件 |
| DOCX / PPTX | 不支持 | 不支持 | 不适用 | 无 | 依赖与版式保真审计后实施 |
| EPUB / RTF / ODT / ODS / ODP | 不支持 | 不支持 | 不适用 | 无 | 仅审计 |
| XLS / DOC / PPT | 不支持 | 不支持 | 不适用 | 无 | 需要隔离转换方案 |
| SRT / VTT / ASS | 不支持附件 | 不支持 | 不适用 | 无 | 音视频后处理阶段 |
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
| WAV、MP3、AAC、M4A、FLAC、OGG、WebM | Xpert 按配置部分可用；本轮补通用入口 | 不适用 | 计划中 | 不适用 |
| MP3 | 不适用 | Xpert 按配置部分可用；MAI-Voice-2 通用入口已实现 | 专用工作区可播放和下载 | 不适用 |
| PCM | 不适用 | 上游可能支持，首期不开放 | 不适用 | 不适用 |
| MP4、MOV、WebM | 不适用 | 不适用 | 计划中 | 计划中 |
| MKV、AVI、MPEG | 不适用 | 不适用 | 需转码后支持 | 仅审计 |

## 4. 判定示例

### 示例 A：XLSX

“Data X 支持 XLSX”不等于“平台所有模块支持 XLSX”。

```text
格式：XLSX
Data X：已支持，结构化解析
RAG：未支持；下一轮先审计解析保真、资源边界与独立入口
Chat：不支持附件
能力层级：Data X 为转换后支持；RAG 尚未形成可验收链路
UI：当前仅 Data X；未来若进入 RAG，也不进入普通 Chat
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
Xpert：按发布版本配置可进行 STT
通用模型页：本轮补齐 STT 入口
Chat 音频理解：尚未支持
能力层级：STT 为模型原生；语音对话为 ASR + LLM + TTS 组合支持
UI：先进入“音频转文字”，不得伪装成普通文本聊天
```

### 示例 D：视频模型

```text
输入/输出：视频
模型目录：已收录
Chat：没有上传、轮询或播放器契约
平台状态：计划中
UI：当前显示“视频理解/视频生成 · 待适配”
说明：视频生成未来进入异步任务中心，不复用 Chat SSE
```

## 5. 本轮实现与明确不实现

### 实现

1. 目录保留输入与输出模态，并生成真实 operation。
2. 模型卡把 Chat、RAG 与待适配媒体任务分流。
3. 通用 STT 文件上传。
4. 通用 TTS MP3 生成。

本轮在批次 D（TTS）验收后结束。RAG XLSX 与音频批次的用户任务和前端入口割裂，已整体延期；下一轮必须先单独审计解析方式、资源上限、语义检索定位，以及是否只在 `/rag` 提供入口，再决定实现。

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

- 首期只开放完成行为测试的 `microsoft/mai-voice-2` 与 Harper 声线；其他 TTS 模型继续标记为待适配。
- 输入不能为空且最多 4,000 个字符；速度限定为 `0.5–2.0`，输出仅允许 MP3。
- 后端完整接收上游响应，并同时校验 `audio/mpeg`、MP3 文件签名、非空内容和 20 MiB 安全上限，校验通过后才返回浏览器。
- 成功响应为原始 MP3 字节；脱敏响应头提供 request ID、实际模型、供应商、费用状态和输出字节数。
- 文字和音频不写入数据库；审计仅记录租户、operation、模型、连接、输入/输出字节数、状态和可用费用信息。
- 前端自适应语音生成工作区提供文字保留、已验证声线、语速、取消、重试、播放器和下载；替换结果或离开页面时释放 Blob URL。

### 只审计

- GIF 动画、SVG 主动内容、HEIC/TIFF 高级处理。
- 视频输入、视频生成、实时语音。
- RAG XLSX、CSV 等表格资料解析及其前端入口。
- 3D、点云、传感器、科学数据和医学影像。
- 压缩包、旧版 Office 和复杂办公版式保真。

## 6. 文档验收标准

本文档只有满足以下条件才算完成：

1. 每个能力都同时标注格式、模块、能力层级、状态和 UI 入口。
2. “模型目录收录”“网关可调用”“模块可用”“平台已支持”不混用。
3. Chat、RAG、Data X、Xpert/Agent、Workflow 和专用媒体入口均有清晰边界。
4. 已支持项能找到真实代码或测试证据；无法验证的项目只能标为“计划中”或“仅审计”。
5. GIF、视频、3D、医学影像等不会因目录标签被误写成当前平台能力。
6. 新增格式必须补充大小、数量、超时、安全、隐私和降级约束后才能进入实施。

## 7. 接口依据

- OpenRouter Multimodal Overview: <https://openrouter.ai/docs/guides/overview/multimodal/overview>
- OpenRouter Speech-to-Text: <https://openrouter.ai/docs/guides/overview/multimodal/stt>
- OpenRouter Text-to-Speech: <https://openrouter.ai/docs/guides/overview/multimodal/tts>
- OpenRouter Audio: <https://openrouter.ai/docs/guides/overview/multimodal/audio>
- OpenRouter PDFs: <https://openrouter.ai/docs/guides/overview/multimodal/pdfs>
- OpenRouter Video Understanding: <https://openrouter.ai/docs/guides/overview/multimodal/videos>
- OpenRouter Video Generation: <https://openrouter.ai/docs/guides/overview/multimodal/video-generation>
