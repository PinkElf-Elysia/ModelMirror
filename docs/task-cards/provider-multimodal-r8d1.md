# 任务卡：R8D.1 音频 Provider 协议资格补充轮

## 目标与边界

- 基线为已更新到最新主线的 R8D PR #349 头 `c6739a12`（基于 `origin/main@436d2453`）；R8D.1 使用独立堆叠分支，不能污染或合并 R8D。
- 用户于 2026-09-07 锁定最终收尾标准：`chat_audio_input`、`chat_audio_output`、`audio_generation` 三者必须全部完成真实纳管；若仍有任一未纳管，必须给出同时由官方合同和可复核实测支持的、不可绕过且不可修复的硬阻断。能力声明、单次 2xx、临时 4xx/5xx、缺少文档或尚未尝试替代 Adapter 均不满足该例外标准。
- 只处理 `chat_audio_input`、`chat_audio_output` 与 `audio_generation_stream` 的精确上游协议资格。
- 保持 R8D 的单 POST、派发后不回退、精确模型、脱敏 Receipt、默认关闭和 `legacy` 回滚边界。
- 不迁移 R8E/R8F，不修改普通 Chat、Catalog 数量或提示词选择器口径，不新增生产依赖。
- 在新的真实调用获逐次授权前，只允许公开目录、官方文档、既有脱敏证据和本地 Mock 验证。

## 已证实的协议差异

- OpenRouter Chat Audio 要求 `stream=true`，音频输出位于 SSE `choices[0].delta.audio`。
- OpenAI 当前 `gpt-audio-mini` 模型合同明确不支持 Streaming；它不能证明现有流式 Chat Audio 形态。
- OpenAI 当前 `gpt-audio` 支持 Streaming，且仍是现有 `openrouter_chat_audio_v1` 的候选，不在本批自动选定。
- OpenRouter 将 Lyria 3 Clip 暴露为 Chat Completions 流式模型，但未公开承诺 R8D 假定的精确音频 delta、终止和 MP3 组合。
- Google 原生 Lyria 使用 Interactions/Generate Content 的一次性 JSON 音频块；该协议不等于现有 OpenRouter SSE Adapter。

## 阻断性质

- 不存在“音频能力整体永远无法纳入控制面”的证据。
- `gpt-audio-mini + 流式 Chat Audio` 是当前上游合同的硬阻断；继续修改 Prompt 不能解决。
- `Voxtral + 当前 OpenRouter Chat Audio 请求` 是已由真实 HTTP 400 证实的精确组合阻断；在上游说明或合同变化前保持 NO-GO。
- `Lyria + 当前 OpenRouter SSE Adapter` 尚属协议和可用性未证实，不可当作永久不支持，也不能在 5xx 后宣称可用。
- 以上阻断只让对应 Binding 失败关闭；不会削弱控制面管理其他已认证模型或后续增加新 Adapter 的能力。

## 首批修复

- 从静态音频目录撤销 `gpt-audio-mini` 的流式 Chat Audio 已验证状态，但保留模型可发现性。
- 对 `gpt-audio-mini + openrouter_chat_audio_v1` 的输入和输出认证在 Provider POST 前返回稳定阻断码。
- 不删除历史失败记录，不把现有失败升级为通过，不改变 `gpt-audio` 或 Lyria 的状态。

## 后续需用户确认的产品选择

1. Chat Audio 是否优先用 `openai/gpt-audio` 继续现有流式 Adapter 资格；这需要输入、输出各一次独立付费认证。
2. 是否仍必须支持 `gpt-audio-mini`。若必须，需要新增独立 unary Adapter，并接受上游非流式、下游缓冲后再发 SSE 的体验差异。
3. 音乐生成继续等待 OpenRouter Lyria 稳定并取得精确响应证据，还是新增 Google 原生 Interactions Adapter；后者会扩大 Provider 类型、凭据和协议范围。

## 已确认的补充轮路线

- 用户于 2026-09-04 确认采用最小范围路线。
- Chat Audio Input 与 Chat Audio Output 只以 `openai/gpt-audio` 继续现有流式 Adapter 资格。
- `openai/gpt-audio-mini` 暂不新增 unary Adapter，并继续在付费 POST 前失败关闭。
- Audio Generation 保留 OpenRouter Lyria 路径，等待精确协议与稳定性证据；本轮不新增 Google 直连 Adapter。
- 上述确认不构成真实额度授权；输入、输出及后续 Lyria 复测仍需分别授权。

## 门禁

- 不兼容模型在目录和认证两层都不得宣称流式可用，且认证不得产生 Provider POST。
- 新 Adapter 必须有独立契约版本、精确模型/格式/终止测试和真实资格，不能放宽 R8D v1。
- 真实资格通过前，三个入口继续保持 `legacy`、无 Binding、无用户 Smoke。
- 每个真实认证单独列出最大 POST 数并等待授权；一次授权不覆盖重跑或其他形态。

## `openai/gpt-audio` 真实资格结果

- 用户授权建立独立 R8D.1 预览并执行输入、输出各一次真实认证；共使用两个 Provider POST，未重试。
- Chat Audio Input 的 Catalog、HTTP 2xx、精确模型、非空内容、安全终止、`stop` 与 `[DONE]` 均通过；固定 WAV 的返回文本未严格归一化为 `okay`/`ok`，最终为 `provider_multimodal_chat_audio_input_content_mismatch`。
- Chat Audio Output 在消费正文前返回确定的 4xx，最终为 `provider_workload_http_error`；未观察到实际模型、音频或终止帧。
- 两条 session 均为 `provider_dispatch_state=confirmed`；服务日志中认证 API POST 恰为 2，无异常和敏感信息模式命中。
- 上述证据推翻了静态目录中 `openai/gpt-audio` 的 `behavior_verified` 声明；模型仍可发现且保留上游 Streaming 能力声明，但在新的精确资格通过前不再显示为 ready Chat Audio。
- 本次授权已耗尽，不覆盖格式诊断、响应正文观察、重跑或用户入口 Smoke。

## 新的决策门禁

1. Chat Audio Input 保持严格转写等值门禁，或另行批准新的音频理解认证语义；不得仅因流完整就将其标为通过。
2. Chat Audio Output 保持 MP3 合同失败关闭，或另行批准 WAV 参数合同、前端兼容检查及一次付费认证；OpenRouter 文档明确提示具体格式支持随模型和 Provider 变化。
3. 在上述决策与真实资格完成前，R8D.1 不创建 Binding、不激活 Policy、不执行用户 Smoke。

## 第二轮真实资格与证伪结果（2026-09-05）

- 用户批准 Input v3 与 Output WAV v2 各一次真实认证；两次额度均已使用，未重试、未回退，也未调用第二 IP、连接或模型。
- Input v3 认证 `workcert_6e439ed54fb944a1b02a5203da336c6d`：Catalog、HTTP 2xx、精确实际模型、非空文本、完整 SSE、`stop` 与 `[DONE]` 均通过；仅严格语义检查失败，错误为 `provider_multimodal_chat_audio_input_semantics_mismatch`。认证耗时约 1.81 秒，记录到 92 个总 Token。
- Input v3 的返回正文按设计未持久化，因此不能从脱敏 Receipt 判断它是 `ACKNOWLEDGMENT`、带标点/包装的标签还是 `OK`；不得凭猜测归因于模型不理解音频。
- Output WAV v2 认证 `workcert_e48dcdff261a4bfe9a20c6a26a5dde0a`：Catalog 通过，但 Provider 在约 0.81 秒后返回确定性 4xx；没有观察到实际模型、音频 delta 或终止帧。旧实现把 400/402/413/422 合并为 `provider_workload_http_error`，且未为 Output 保留有界错误子类型，所以现有证据不足以宣称 WAV 不受支持。
- 官方公开 Endpoint 元数据显示 `openai/gpt-audio` 仍声明 text/audio 输入与输出、Streaming 和至多 16384 completion tokens；因此当前结论是“现有精确请求组合未通过”，不是不可修复的上游硬阻断。
- 两次授权已经耗尽；下述离线修复不构成新的付费调用授权。

## 第二轮后的手术刀修复

- Chat Audio Input 契约升级到 `modelmirror-provider-chat-audio-input-parameters-v4`，输出标签缩短为 `ACK`，并仅接受有界同义值：`ACK`、英美拼写的 acknowledgement、`OK`、`okay`，以及单一标点、引号或三行代码围栏包装。解释性文本、多标签和 `OTHER` 继续失败关闭。
- Chat Audio Output 仍为 WAV v2，但认证 `max_tokens` 从 32 对齐到实际 Chat 数据面的 2048；这只改变已确认的请求差异，不放宽音频 Magic、完整 WAV、实际模型或终止门禁。
- OpenRouter Chat Audio 的确定性 400、402、413、422 现在分别映射为稳定错误码；Input 和 Output 都只读取有大小、内容类型、JSON 形状和 allowlist 限制的 `error_type`，不保存或返回上游正文。
- 离线聚焦反证测试：`34 passed`；覆盖允许/拒绝语义、错误正文大小/读取失败/超时/取消、精确 4xx 和单 POST。
- 完整 R8D 资格测试：`116 passed`。
- Chat Audio 数据面与多模态基础回归：`54 passed`，仅有 4 条既有 FastAPI lifespan 弃用警告。
- Python 语法检查通过，`git diff --check` 通过。
- `openai/gpt-audio` 仍保持 `manual_required` 且无 Chat Audio mode；尚未恢复目录资格、创建 Binding、激活 Policy 或执行用户 Smoke。

## 下一道真实门禁

1. 只有在新的逐次额度授权后，才可重建 v4/Output-2048 预览并分别执行 Input、Output 各一次认证；最多两个 Provider POST，任何失败均不得自动重跑。
2. 两个形态都必须通过后，才能恢复对应精确资格并考虑 Binding；单边通过不能继承到另一形态。
3. 若 Output 仍返回确定性 4xx，先使用新错误码与有界子类型定位请求合同；不得通过增加重试、第二 Provider 或放宽完整 WAV 门禁绕过。
4. 只有资格、Binding、Policy 和用户入口真实 Smoke 均通过，R8D.1 才可宣称纳入统一默认控制面。

## v4 隔离预览

- 预览地址：`http://127.0.0.1:15155/settings?section=providers`；后端地址：`http://127.0.0.1:18155`。
- 当前 server 镜像：`modelmirror-provider-multimodal-r8d1-server:chat-audio-v4-wav-v2-2048-20260905`。
- 上一版 server 容器已停止并保留为 `modelmirror-provider-multimodal-r8d1-server-preview-backup-v3-20260905-004401`；更早备份和 client 容器未删除。
- Router 持久化目录继续使用 `C:\tmp\modelmirror-provider-multimodal-r8d1-preview-data`，未清空、迁移或重写。
- server 与 client 代理的 `/api/health` 均返回 200；容器内确认加载 Input v4 和 Output WAV v2，启动日志无迁移或配置错误。
- 预览专用配对密钥已轮换，浏览器刷新后正确回到锁定状态；密钥值未写入任务卡或日志。
- 只读预检确认两个 Chat Audio 入口仍为 `legacy`、`available=false`、`provider_workload_policy_not_active`，不会在当前状态误发 Provider POST。

## v4 / Output-2048 真实复测（2026-09-05）

- 用户重新配对并授权 Input v4、Output WAV v2+2048 各一次；服务端共收到两次认证 API 请求，两次均返回 200 认证摘要，未重试、未回退，也没有第三次认证请求。
- Input v4 认证 `workcert_a840ffae14c34903bded5252b3a50ad9`：精确模型 `openai/gpt-audio`，Catalog、HTTP 2xx、非空文本、完整 SSE、实际模型、`stop` 与 `[DONE]` 均通过；`audio_semantics_matches_fixture=false`，最终错误仍为 `provider_multimodal_chat_audio_input_semantics_mismatch`。TTFT 约 1.35 秒，E2E 约 1.37 秒，总 Token 84。
- Input v4 已覆盖 `ACK`、英美拼写、`OK`、`okay`、单一点号、单引号/双引号/反引号和三行代码围栏。复测仍失败，使这些已知包装导致的本地假阴性不再是可靠根因；由于正文按安全设计立即丢弃，现有证据不能继续猜测模型返回了什么。
- Output WAV v2+2048 认证 `workcert_b6b1bebafcd447e0a29238ba7324a9ca`：Catalog 通过，但在约 0.81 秒后确定性返回 `provider_workload_http_400`；没有实际模型、音频 delta 或终止帧，有界 `error_type` 也未出现。将 `max_tokens` 从 32 调整到 2048 未改变结果，因此已排除“输出 Token 上限过小”这一假设。
- 日志审计：认证 API 请求恰为 2，Traceback/Error 为 0，配对密钥和 API Key 模式命中为 0。
- 持久化目录扫描未发现固定 Prompt、WAV Base64 标记或模型正文；两条记录均为 `provider_dispatch_state=confirmed`、`retry_allowed=false`。
- 本次两次真实额度授权已经耗尽。两个形态仍不具备 Binding/激活资格，目录必须继续保持 `manual_required`。

## v4 复测后的可靠推进边界

1. Input 不再继续凭猜测扩大同义词表。下一步如仍要攻关，应先增加不含正文的有界诊断特征（例如长度桶、是否只出现一个批准语义词、是否存在多标签），经离线隐私审查后再单独授权一次诊断认证；诊断不能自动授予资格。
2. Output 的 MP3、WAV 和 32/2048 Token 组合均已得到确定性 4xx。下一步优先从 OpenRouter 失败请求日志或可引用的上游错误类别取得证据；在没有新证据前不再通过排列参数进行付费试错。
3. 若上游无法提供可执行错误信息，则 `openai/gpt-audio + openrouter_chat_audio_v1` 的 Input/Output 在本版本保持 NO-GO；这不影响控制面继续纳管已通过独立资格的 STT、TTS 或后续其他 Adapter。
4. 不允许把“HTTP/SSE 基础链路通过”降格为 Input 资格，也不允许把公开能力声明当作 Output 真实资格。

## OpenRouter 上游日志核对（2026-09-05）

- OpenRouter Logs 的成功 Generation 明确对应 Input v4：`openai/gpt-audio` 最终 Provider 为 OpenAI，Provider 状态 200、单次尝试，输入/输出 Token 为 69/15，finish reason 为 `stop`。这与本地 Receipt 的 84 个总 Token、完整 SSE 和安全终止一致，进一步把失败边界收窄到本地语义判定，而非网络、路由、模型或流终止。
- 该 Generation 的 Provider Responses 只显示 OpenAI 200；OpenRouter 当前未启用 I/O logging，历史调用正文不可补录。为保持隐私边界，本轮没有启用 I/O logging，也没有读取 Prompt 或模型正文。
- OpenRouter Upstream Requests 中存在唯一对应 Output WAV v2+2048 的失败记录：`openai/gpt-audio`、最终 Provider OpenAI、HTTP 400、Attempts=1、延迟 216 ms。它没有进入成功 Generation 列表，证明本地单 POST/无回退行为与上游记录一致。
- 使用该失败记录的 Generation ID 查询官方 `GET /api/v1/generation` 返回 404 `Generation ... not found`；官方公开接口和当前 UI 均未提供参数级错误正文。因此目前可证实的是“OpenAI 在一次上游尝试中确定性拒绝了请求”，不能从现有证据继续归因到 `modalities`、voice、format、temperature 或 Token 上限中的某一字段。
- 当前公开 Endpoint 元数据仍声明 `text+audio -> text+audio`、Streaming、`max_completion_tokens=16384`，且明确列出 `max_tokens` 与 `temperature` 为支持参数；OpenRouter Audio 文档的输出示例也使用 `modalities=[text,audio]`、`voice=alloy`、`format=wav` 和 `stream=true`。因此现有请求没有可由公开合同直接指出的非法字段，400 更像是未公开的 Provider 约束或公开能力与实际端点的漂移，但两者在取得参数级错误前都只是候选解释。
- 结论不变：Input/Output 均继续 NO-GO；在 OpenRouter 提供可执行错误类型、支持工单给出参数级原因，或用户另行批准有证据约束的诊断调用前，不做参数排列式付费试错。

## 最新主线重放审计（2026-09-07）

- Fetch 后最新主线为 `origin/main@436d2453`；R8D PR #349 仍停在 `f5baebf6`，共同祖先为 `06ef51ae`，GitHub 将该 PR 标记为冲突状态。
- 主线与 R8D 的文件交叉只有 `server/main.py` 和 `server/model_router/multimodal_gateway.py`。只读三方合并证明，唯一文本冲突是 `server/main.py` 顶部的 `copy`/`codecs` 导入，两项都必须保留；Gateway 可自动合并。
- 在独立临时 Worktree 中组合最新主线、R8D 和当前 R8D.1 未提交增量后，Python 语法检查与 `git diff --check` 通过。
- 组合树的 R8D 音频回归为 `149 passed`；主线新增视觉、Workflow 与评测交叉回归为 `200 passed`；加入 R8D.1 后的资格与数据面回归为 `213 passed`。只有既有 FastAPI lifespan、PyPDF2 弃用警告。
- 组合树前端定向测试为 `23 passed`，App/Node TypeScript 无增量类型检查通过，Vite production build 通过；大 Chunk 提示保持为既有非阻塞警告。
- 本次恢复只重启原 R8D.1 server/client，继续挂载原 Router 持久化目录；前后端健康检查均为 200。没有执行 Provider POST、认证重跑、Binding、Policy 激活、提交、Push 或 PR 更新。
- 用户随后授权更新。R8D 已手术刀式 rebase 到 `origin/main@436d2453`，新头为 `c6739a12`；使用精确 `force-with-lease` 更新 PR #349 后，GitHub 的文本冲突已解除，CI 进入运行状态。
- 正式 rebased R8D 分支复跑音频回归 `149 passed`、前端定向回归 `42 passed`、App/Node 类型检查和 production build；视觉交叉长套件首次因 PDF worker 10 秒资源限制得到 `1 failed, 199 passed`，同一 SHA 单测复现和完整 `test_rag_vision.py` 复跑分别为 `1 passed`、`16 passed`，分类为环境超时而非 R8D 回归。
- R8D.1 在保留 `stash@{0}`（对象 `5694048b`）的情况下迁移到 `c6739a12`，迁移前后 tracked patch-id 与两个 untracked 文件 SHA-256 完全一致；备份在完整验证前不删除。

## 严格终止与 Output PCM16 v3（离线阶段）

- Runtime 与认证统一要求精确模型、单 choice/index 0、`stop`、`[DONE]` 和正常 EOF；只允许一次带有效非负 usage、相同 generation ID 且不含语义内容的终止重放。错误终止、终止后 transcript/tool call、不同 generation ID 和非法 usage 均失败关闭。
- Output v3 将用户侧 `wav` 与上游 `pcm16` 分离；旧 WAV v2 资格自动 stale。服务端只有在完整终止和原始 PCM16 通过有界、偶数字节与容器魔数检查后，才可按单声道、16-bit little-endian、24 kHz 封装为 WAV。
- Chat Completions 的公开合同确认 `pcm16` 是合法输出格式，但未公开精确采样率。24 kHz 在本轮被明确记录为 `empirical_route_playback_required_v1` 候选参数，而不是借用 Realtime 文档冒充 Chat 证明；真实认证与浏览器人工播放通过前不得宣称 Output 已纳管。
- 当前阶段没有 Provider POST。PCM 包装通过只证明本地传输与交付合同，不证明上游真实音频参数或听感。

## v5 / PCM16 v3 / Lyria v2 离线收口（2026-09-08）

- Chat Audio Input 使用独立的 3.598 秒、8 kHz、单声道 PCM16 WAV 合成素材；SHA-256 为 `2608cf8502a810d71e9c9ec5801c0dcb5992c4b6988a959a5ba5cc18c2126006`。它不再复用 R8C STT 的短音频。
- Input v5 要求模型只返回严格 JSON 的数量与颜色事实；Prompt 不包含答案值，解析拒绝重复键、Markdown、解释文本、额外字段和类型混淆。认证 Profile 绑定素材、清单、Prompt、评分合同和人工听审 Receipt。
- 人工听审已由用户针对 SHA-256 `2608cf8502a810d71e9c9ec5801c0dcb5992c4b6988a959a5ba5cc18c2126006` 的固定 WAV 完成；用户逐字确认听到 `The card shows three blue circles.`，与固定事实完全一致。Input v5 状态已改为 `approved`；这只批准该不可变素材进入认证，不构成 Provider 认证、用户入口 Smoke 或其他音频形态资格。
- Output v3 将上游 `pcm16` 与用户侧 WAV 交付分离；只在精确模型、完整 SSE、`stop`、`[DONE]`、正常 EOF 和有界 PCM16 都通过后，按 24 kHz、单声道、16-bit little-endian 封装 WAV。真实认证及浏览器听感验收前仍不能宣称已纳管。
- Audio Generation v2 让认证和真实任务共用同一请求构造器，并统一固定 Request Contract；认证不再使用运行时不存在的 `temperature` 或 `max_tokens`。认证资格现在同样要求安全终止证据。
- Lyria 认证与运行时均拒绝 generation ID 漂移、多 choice、非零 index、冲突终止、错误终止、终止后正文/音频、文本或工具载荷、重复 JSON 键和不完整 MP3；只允许一次结构有效且一致的 usage-only 终止帧。
- Managed Lyria 增加 300 秒总时限；响应关闭另有 1 秒独立上限，清理协程即使忽略取消也不能让任务永久停留在 running/dispatched。
- PCM16 检查不再凭单个 `0xFFFF` 合法负采样误判 MP3；只有连续两个结构合理的 Layer III 帧头才触发裸 MP3 拒绝。
- Astra Max 独立只读证伪审查未发现 P0/P1，指出的三个 P2（PCM16 假阴性、无界关闭、重复 JSON 键覆盖）均已手术刀式修复并补回归。
- 离线门禁：R8D 认证、R8D Chat、多模态 Chat Foundation 与 Audio Jobs 共 `261 passed`；旧 R8C STT 素材隔离检查另为 `1 passed`。只有既有 FastAPI lifespan 弃用警告。
- 本阶段未执行 Provider POST、未重建预览、未创建 Binding、未激活 Policy、未执行用户入口 Smoke，也未 Commit、Push 或创建 PR。

## 当前三形态门禁

| 形态 | 离线合同 | 仍需真实证据 | 当前结论 |
|---|---|---|---|
| `chat_audio_input` | v5 独立素材、严格事实评分和人工听审已完成 | 一次用户入口 Smoke | 真实认证、精确 Binding 与 `managed_required` 激活已完成；尚未完成用户入口验收 |
| `chat_audio_output` | PCM16 v3 严格流与 WAV 交付已完成 | 一次认证；实际采样/听感确认；Binding/Policy；一次用户入口 Smoke | 尚未纳管，未证明硬阻断 |
| `audio_generation` | Lyria v2 请求同构、严格流、完整 MP3 和有界超时已完成 | 一次认证；Binding/Policy；一次异步任务 Smoke | 尚未纳管，未证明硬阻断 |

三形态仍须分别取得逐次额度授权。任何一次失败都只能证明该精确模型、Adapter、请求与当时上游状态的组合未通过；只有官方合同与可复现实测共同证明不可绕过且不可修复时，才可使用用户批准的硬阻断例外收尾。

## Deadline、取消与跨轮回归收口（2026-09-08）

- Managed Chat Audio 的总时限改为单一绝对 deadline；每次读取只在 `anext()` 外围进入 timeout context，timeout context 不再跨越 async-generator 的 `yield`。这消除了内部 deadline 取消被误归类为客户端取消的根因。
- deadline 在消费者处理后及同步 EOF 后再次检查；已越界的缓冲成功、晚 EOF 或晚终止不得被接受为成功。此语义约束 Provider 流的可接受结果，不承诺在 deadline 到点时抢占同步数据库提交或客户端发送。
- 真实 ASGI disconnect 在等待上游分片时会记录取消 Receipt、关闭上游响应和客户端，并且不再发送 `message_end` 或 `[DONE]`；同一逻辑调用仍只有一个 Provider POST。
- 超时、慢消费者、晚 EOF、断连及资源关闭的聚焦回归通过；R8D Audio Jobs、Foundation、认证与 Chat 四文件最终为 `329 passed`。
- 初次跨轮回归发现 11 个 Flag-off legacy Chat Audio 失败。根因是把 Managed 真实资格结论错误写入了共享静态音频目录；现已将 `audio_catalog.py` 及其旧目录断言精确恢复到 `HEAD@c6739a12`。
- 恢复静态目录只保持既有 legacy 兼容，不授予任何 Managed 资格。Managed 路径仍要求精确 Policy、Binding、当前参数合同、真实认证、连接指纹和出口授权。
- 恢复后跨轮回归为 `240 passed, 1 failed`。唯一失败是 R8C metadata 恢复测试使用 10ms 单次预算，而本机 Windows ProactorEventLoop 时钟粒度为 15.625ms；相同失败已在同 SHA 的干净基线 Worktree 复现，且相关生产方法与测试 AST 均未被本批修改。该项分类为基线时序夹具缺陷，不修改生产重试或失败关闭语义。
- 前端定向测试为 `23 passed`，TypeScript typecheck 与 production build 通过。前端全量测试为 `985 passed, 2 failed`；同 SHA 干净基线复现相同两个模型快照口径失败（507/509 与过期模型 8/6），分类为基线 Catalog 快照漂移。
- Python 语法检查与 `git diff --check` 通过；截至本节记录时尚未重建新预览、执行新的 Provider POST、创建 Binding、激活 Policy、执行用户入口 Smoke、Commit、Push 或创建 R8D.1 PR。

## Audio Generation 认证/运行同构收口（2026-09-08）

- `audio_generation_stream` 的认证与运行端现在共享 `15/180/180/180` 秒的 connect/read/write/pool timeout、300 秒总 deadline、2 MiB 单事件上限、约 37 MiB SSE 总量上限，以及 1 KiB～25 MiB 的解码音频边界。
- 两侧都在累计 Base64 时执行编码长度上限，并在成功前执行相同的完整 Layer III MP3 结构校验；已封闭“认证接受、运行拒绝”的媒体尺寸和结构差异。
- Lyria 允许上游在音频之外返回有界的伴随文本，但只丢弃、不持久化，不能替代音频或安全终止证据；超过 256 KiB 仍失败关闭。
- Managed 运行路径现在只接受 HTTP 2xx；3xx 不跟随且直接拒绝。Legacy 路径的既有状态处理和 timeout 数值保持不变。
- Astra 独立证伪先后发现并推动修复了 Managed 3xx、认证/运行预算漂移、伴随文本合同、认证 read timeout 和媒体上下界五项问题；最终复审未发现新增 P0/P1/P2。
- 聚焦回归为 `238 passed`；四个 R8D 核心测试文件使用独立可写 `--basetemp` 后为 `334 passed`；跨 R8B/R8C/R5 回归为 `240 passed, 1 failed`，唯一失败仍是已在同 SHA 干净基线复现的 Windows 10 ms 时钟粒度夹具。
- 8 个相关 Python 文件经内存编译通过，`git diff --check` 通过。普通 `py_compile` 写入既有只读 `__pycache__` 时遇到 ACL 拒绝，未将该环境问题误报为代码失败。
- 当前 Docker 中旧 v4/WAV v2 预览和历史备份仍存在，Router 数据目录仍为 `C:\tmp\modelmirror-provider-multimodal-r8d1-preview-data`；尚未用本节代码重建，也未执行新的 Provider POST。
- 请求构造仍只包含已经确证的字段，不凭通用音频文档猜测 Lyria 的 voice、format 或 modalities；真实协议资格仍需新预览上的独立认证和异步任务 Smoke。

## v5 / PCM16 v3 / Lyria v2 新预览（2026-09-08）

- 新 server 镜像为 `modelmirror-provider-multimodal-r8d1-server:audio-contract-v5-pcm16-v3-lyria-v2-20260908`；新 client 镜像为 `modelmirror-provider-multimodal-r8d1-client:audio-contract-v5-pcm16-v3-20260908`。
- 当前预览继续使用 `http://127.0.0.1:15155` 和后端 `http://127.0.0.1:18155`；前后端 `/api/health` 均返回 200。
- 被替换的 server/client 分别保留为 `modelmirror-provider-multimodal-r8d1-server-preview-backup-v4-20260908-064522` 和 `modelmirror-provider-multimodal-r8d1-client-preview-backup-v4-20260908-064612`，未删除历史备份。
- Router 仍挂载 `C:\tmp\modelmirror-provider-multimodal-r8d1-preview-data`；现有 `router.sqlite3`、主密钥文件和两份数据库备份均保留，未清空或迁移。
- 预览专用管理员配对密钥已轮换；任务卡、日志和命令输出均未记录密钥值。
- 容器内确认加载 Input v5、Output PCM16 v3、Audio Generation 参数 v2、Lyria 请求合同 v2，以及 300 秒/25 MiB 预算。
- 三个公开入口均仍显示 Feature Flag 开启但 Policy 为 `legacy`，并以 `provider_workload_policy_not_active` 在派发前阻断；重建未创建资格、Binding、激活 Policy 或产生 Provider POST。
- 首次替换脚本因错误继承容器内 `PATH` 而在停止容器前失败；只读复核确认旧预览未受影响。重试改为只继承明确应用变量并固定 Docker 可执行路径，随后完成可逆替换。

## Audio Generation Runtime Receipt 指标收口（2026-09-08）

- 严格音频生成解析器现在只在首个非空音频 delta 到达时计算 TTFT；伴随文本、终止重放和完整 MP3 校验完成时间均不能冒充首音频时间。
- 去除了 `httpx.Response.aiter_bytes(chunk_size=64 * 1024)` 的固定分块：HTTPX 会通过 ByteChunker 合并小块，可能把首音频时间推迟到 64 KiB 或 EOF；改为无参数 `aiter_bytes()`，并用受控时钟证明首音频在 125 ms、EOF 在 5000 ms 时仍记录 125 ms。
- 只有已经通过 `R8DAudioSseContract` 完整数值、非负范围和总数一致性校验的 usage 才进入 Runtime Receipt；上游缺失的指标继续保存为 `None`，不估算、不填零。
- 音频生成结果会把 TTFT 和 prompt/completion/total token 传给既有 `dispatch.complete()`；完整 MP3 写入后先原子完成真实 SQLite Receipt，再更新任务元数据。首次或最终任务元数据写入失败时保留已确认 Receipt 与完整输出，由 `get/list/recover_interrupted` 对账恢复，不误报 Transport uncertain，也不重新 POST。
- 旧测试替身或兼容调用方没有 `started_at` 时安全降级为无 TTFT，不影响真实 Dispatch 路径，也不为缺失数据制造时间值。
- 新增真实 Repository 集成故障注入：首个 Provider 结果元数据写入失败后，SQLite Call 仍为 `passed`，保留实际模型、TTFT 和 `2/3/5` usage；随后读取恢复成功，认证与 Runtime 合计仍只有两个预期 POST。
- 完整 Audio Jobs 套件为 `65 passed`；认证、R8D Chat、Foundation 与 Audio Jobs 四文件核心组合为 `337 passed`，仅有 4 条既有 FastAPI lifespan 弃用警告；`git diff --check` 待最终统一复核。
- 两个 Astra Max 独立审计在复核上述修复后未发现新的 P0/P1/P2；人工固定素材听审随后已通过，剩余门禁是最终镜像重建以及逐形态真实认证与用户 Smoke，不是离线解析或 Receipt 数据完整性缺口。
- 本节只修复本地可观测性和恢复语义，不改变三形态资格结论；未执行 Provider POST、未创建 Binding、未激活 Policy，也未 Commit、Push 或更新 PR。随后已在人类听审确认后按下一节记录完成可逆预览重建。

## Input v5 人工听审批准（2026-09-08）

- 用户对 57,610-byte 固定 WAV 完成人工听审并逐字回报 `The card shows three blue circles.`；复核 SHA-256 为 `2608CF8502A810D71E9C9EC5801C0DCB5992C4B6988A959A5BA5CC18C2126006`，与源码清单一致。
- `CHAT_AUDIO_INPUT_FIXTURE_HUMAN_AUDIT_STATUS` 已由 `pending` 改为 `approved`。批准只绑定当前 fixture revision、音频摘要、Prompt 合同和评分合同；任一项变化都必须升级认证 Profile 并重新人工听审。
- 人工听审批准当时尚未发起 Input v5 Provider POST，也未创建资格、Binding 或激活 Policy；人工听审本身不能替代一次真实认证和一次用户入口 Smoke。后续真实认证结果见下节。

## 听审后最终预览重建（2026-09-08）

- 人工批准状态更新后，认证、R8D Chat、Foundation 与 Audio Jobs 四文件核心组合再次得到 `337 passed`，只有 4 条既有 FastAPI lifespan 弃用警告。
- 新 server 镜像为 `modelmirror-provider-multimodal-r8d1-server:audio-contract-v5-pcm16-v3-lyria-v2-receipts-approved-20260908`；容器内确认 fixture 状态为 `approved`、SHA-256 与源码一致，并加载 TTFT 与三项 token Receipt 字段。
- 当前 server 仍使用 `modelmirror-provider-multimodal-r8d1-server-preview`、`127.0.0.1:18155`、原独立网络和 `C:\tmp\modelmirror-provider-multimodal-r8d1-preview-data`。前后端 `/api/health` 均为 `ok`，启动日志无 Traceback、ERROR、CRITICAL 或敏感字面量命中。
- 被替换的上一版 server 完整保留为 `modelmirror-provider-multimodal-r8d1-server-preview-backup-v5-approved-20260908-194121`；更早备份、Router SQLite、主密钥和数据库备份均未删除。
- 预览专用管理员配对密钥已再次随机轮换且未写入源码、任务卡或工具输出；管理员会话显示已配置但尚未配对。
- 三入口 Feature Flag 均开启，但 Policy 仍为 `legacy`，公开状态均为 `available=false`、`blocks_before_dispatch=true`、`provider_workload_policy_not_active`。重建没有创建认证、Binding、激活 Policy 或发起 Provider POST。

## Input v5 真实资格通过（2026-09-08）

- 用户配对后单独授权一次 Chat Audio Input v5 认证。首次表单误选 `openai/gpt-audio-mini`，代码依据已锁定的非流式上游合同在任何 Provider POST 和认证记录创建前返回 `provider_multimodal_upstream_streaming_unsupported`；该预检不消耗付费调用。
- 随后按补充轮已确认路线更正为 `openai/gpt-audio`，使用 `conn_db4514e04d5c4da1b86d2e05f54e704a` 与 `openrouter_chat_audio_v1` 发出唯一一次真实 Provider POST；未重试、未回退、未调用第二 IP、连接或模型。
- 认证 `workcert_2badc7582c4f440fa9686e98f65fca5a` 为 `passed`：请求与实际模型一致，Catalog、HTTP 2xx、固定素材人工批准、WAV 格式、非空内容、严格数量/颜色事实、完整 SSE、`stop`、`[DONE]` 和安全终止全部通过，warnings 为空。
- 脱敏指标：TTFT 约 `2006.09 ms`，E2E 约 `2049.30 ms`，prompt/completion/total tokens 为 `101/16/117`。Session 为 `post_dispatched=1`、`provider_dispatch_state=confirmed`、`status=passed`。
- Server 访问日志中本轮认证写请求为两条：错误模型发送前阻断 `409` 一条，真实认证 `200` 一条；SQLite 新增的精确 Input v5 认证记录只有一条。日志无 Traceback/ERROR/CRITICAL。
- 扫描 Router SQLite、WAL、SHM 和两份备份共 5 个文件，固定听审短句、认证 Prompt、WAV Base64 头和 OpenRouter Key 前缀命中均为 0；日志相同敏感标记命中为 0。
- 该结果只授予 `chat_audio_input + openai/gpt-audio + openrouter_chat_audio_v1 + 当前连接指纹` 的资格。认证完成当时 Policy 仍为 `legacy`，尚未创建精确 Binding、激活 `managed_required` 或执行用户入口 Smoke；后续激活结果见下节。

## Input v5 精确 Binding 与 Policy 激活（2026-09-08）

- 经用户单独批准，在预览设置页为 `chat_audio_input` 创建了唯一精确 Binding：`openai/gpt-audio`、`conn_db4514e04d5c4da1b86d2e05f54e704a`、`openrouter_chat_audio_v1`，并关联通过的认证 `workcert_2badc7582c4f440fa9686e98f65fca5a`。
- 管理员确认当前无未解决 P0/P1 且接受 fail-closed 后，Policy 已由 `legacy` 原子激活为 `managed_required`，revision 为 `2`，人工批准有效，本地降级保持 `none`。
- 公共状态接口返回 `feature_enabled=true`、`status=managed_required`、`available=true`、`blocks_before_dispatch=false`、`reason_code=provider_workload_available`，并只披露认证格式 `wav`。
- SQLite 只读核对确认 Policy、Binding、认证、连接指纹和资格指纹关联一致；当前该入口的 Workload Run/Call 均为 `0`。
- 激活窗口访问日志只有 Policy `PUT`、`activate` 与只读查询；没有认证或模型数据面请求。因此本步骤没有产生 Provider POST、重试、回退或额度消耗。
- 当前剩余门禁仅为一次单独授权的用户入口真实 Smoke；在取得该授权前，不将 Input 宣称为完成端到端纳管。

## Input v5 用户入口真实 Smoke（2026-09-08）

- 经用户单独授权，在 `chat_audio_input` 的普通 Chat 用户入口上传固定 WAV，并只点击一次发送。模型正确回答音频包含 `3` 个蓝色圆形；前端显示“已纳管 · 1 次 Provider 调用”。
- SQLite 新增且仅新增一个 Workload Run 与一个 Workload Call；Call 精确绑定 `openai/gpt-audio`、当前 OpenRouter Managed Connection、Input v5 认证和 `openrouter_chat_audio_v1`，`provider_dispatch_state=confirmed`、`status=passed`，usage 为 `62/13/75`。
- Server 日志只有一次 `/api/chat` 和一次 `managed_multimodal_upstream_send`；没有第二次 Provider 调用、远程 fallback 或浏览器控制台错误。页面加载时三个可选文件服务状态接口返回 `503`，属于未配置的独立预览能力，不影响本次音频调用。
- 扫描 Router SQLite、WAL、SHM、备份及 Server 日志，用户问题、模型回答、固定音频、听审短句与 OpenRouter Key 标记命中均为 `0`。
- 严格证伪发现 P2 可观测性缺陷：本次 Call 的 TTFT 为约 `1581.58 ms`，E2E 为约 `1569.71 ms`，违反 `TTFT <= E2E`。根因是流证据从请求级 `chat_request_started_at` 计时，而 Dispatch E2E 从稍后的实际派发时刻计时，两者起点不同。
- 因此本次真实功能 Smoke 可判通过，但该次时延不可作为性能证据，R8D.1 总门禁尚未完成。下一步只需让 TTFT 与 E2E 共享实际 Dispatch 起点，并用虚拟时钟离线回归证明精确区间；不得裁剪数值、改写历史 SQLite 或借修复再次调用 Provider。

## 模型主导权过渡待办

## Audio Generation OpenRouter 终止合同 v3（2026-09-12）

- 用户单独授权的一次 `google/lyria-3-clip-preview + audio_generation_stream + openrouter_audio_generation_stream_v1` 真实认证只派发一个 Provider POST；未重试、未回退，也未切换第二 IP、连接、模型或 Adapter。认证 `workcert_60a516df19a44bb483fbf64e218dd86f` 与 Session `mmcertsession_f7b288a0dec248c99ee023b0274b79ed` 记录为失败，固定诊断为 `native_finish_mismatch`：HTTP、Catalog、精确实际模型和一段音频均已观察到，但安全终止未通过。
- 官方 OpenRouter 合同明确区分标准化 `finish_reason` 与供应商原始 `native_finish_reason`，两者不保证相等；流末还可能出现一个无正文、重复终止原因并携带 usage 的 accounting frame。因此此前要求 raw 与 normalized 必须相等、并拒绝该唯一 accounting replay，属于 ModelMirror 本地兼容缺陷，不是上游不可绕过的硬阻断。
- 手术刀式修复只作用于 `audio_generation_stream`：仍要求标准化 `finish_reason=stop`、`[DONE]`、正常 EOF、精确模型、严格 Base64 和完整 MP3；raw 原因只能是最长 128 字符的字符串，不能单独证明成功。Input v5 与 Output v4 继续要求 raw 与 normalized 一致。
- Generation 只接受一次内容为空、无音频、无工具、身份一致且 usage 完整的终止 accounting replay。正文、音频、工具、第二次 replay、模型或生成 ID 漂移、缺失/非法 usage、缺 `[DONE]`、非字符串 raw 和 129 字符 raw 均失败关闭；测试同时证明序列化状态不保留攻击载荷。
- Generation 参数合同由 `modelmirror-provider-audio-generation-parameters-v2` 升级为 `v3`。旧 passed 证据保留但派生为 stale；Binding 进入无效、Policy 进入 `degraded_required`，下一次运行在派发前阻断并保持零新增 POST。Input v5、Output v4 和请求 Payload 合同均未升级。
- 最窄证伪矩阵为 `17 passed`；R8D 认证、R8D Chat 与 Audio Jobs 三套受影响回归为 `350 passed`，仅有 4 条既有 FastAPI lifespan 弃用警告；`git diff --check` 通过。Astra 独立只读复核未发现 P0/P1/P2。
- 新 Server 镜像为 `modelmirror-provider-multimodal-r8d1-server:audio-generation-terminal-v3-20260912`；旧容器保留为 `modelmirror-provider-multimodal-r8d1-server-preview-backup-before-terminal-v3-20260912-045533`，原 Router 数据目录继续挂载。切换前后均为 23 条认证、23 条多模态 Session 和 2 条 Workload Call，上一条 Lyria 失败证据仍存在；前后端健康检查均为 200，启动日志错误级与密钥标记命中为 0。
- 本次重建没有发起 Provider POST。Mock 与独立审查不能证明 Lyria 当前真实资格；管理员重新配对后仍须单独完成一次 Lyria Clip v3 认证。该门禁随后已按下一节完成。

## Lyria Clip Audio Generation v3 真实资格通过（2026-09-12）

- 用户重新配对后明确授权一次 `google/lyria-3-clip-preview + audio_generation_stream + openrouter_audio_generation_stream_v1` 真实认证。浏览器首次确认点击发生控制通道超时，但弹窗仍在且 SQLite 保持 23/23/2，证明未创建认证或派发；仅在这一结果被确认后才使用当前弹窗重试一次，没有盲目双击。
- 认证 `workcert_be60c1eeea6e4440ab69d5d76b1c157d` 与 Session `mmcertsession_b3192761ffee46d4897d0204144107d2` 均为 `passed`；Session 为 `post_dispatched=1`、`provider_dispatch_state=confirmed`、`poll_count=0`，且无错误码。
- 资格精确绑定 Generation v3 参数合同 `modelmirror-provider-audio-generation-parameters-v3` 与请求合同 `openrouter-audio-generation-chat-stream-v2`。请求和实际模型均为 `google/lyria-3-clip-preview`，Adapter 与协议版本分别为 `openrouter_audio_generation_stream_v1` 和 `modelmirror-provider-multimodal-v1`。
- Catalog、HTTP 2xx、非空音频、精确实际模型、完整 MP3、完整响应、标准化 `finish_reason=stop`、`[DONE]` 与安全终止全部通过；共接受 5 个 SSE 事件和 1 个音频片段，warnings 为空。脱敏指标为 TTFT 约 `10372.06 ms`、E2E 约 `10397.74 ms`，usage 为 `10/4/14`。
- SQLite 认证与 Session 均由 23 增至 24，Workload Call 保持 2；认证窗口内只有一条管理认证 POST 且返回 200，ModelMirror Session 只记录一次已派发 Provider POST，retry/fallback 命中为 0。该证据不外推为 newAPI 或 OpenRouter 内部最终供应商只调用一次。
- Server 日志无 Traceback、ERROR 或 CRITICAL；日志和 Router 存储共扫描 8 个文件，固定认证 Prompt、OpenRouter Key 前缀、Authorization 与 Bearer 明文命中均为 0。
- Lyria Clip 已取得当前连接指纹、精确模型、Adapter 和 Generation v3 合同下的真实资格，但尚未创建 Binding、激活 `managed_required` 或执行 Audio Job 用户 Smoke及 MP3 人工播放确认。因此当前结论是“资格通过”，不是“端到端已纳管”；Lyria Pro 仍需独立资格，不能继承 Clip 结果。

## Lyria Clip Audio Generation v3 Binding 与 Policy 激活（2026-09-12）

- 经用户单独授权，在设置页为 `audio_generation` 创建唯一精确 Binding：`google/lyria-3-clip-preview`、`audio_generation_stream`、`conn_db4514e04d5c4da1b86d2e05f54e704a`、`openrouter_audio_generation_stream_v1`，并关联当前 Generation v3 资格 `workcert_be60c1eeea6e4440ab69d5d76b1c157d`。
- Binding 保存后 Policy revision 由 `0` 增至 `1`；管理员确认当前无未解决 P0/P1 且接受 fail-closed 后，Policy 原子激活为 `managed_required`、revision `2`，人工批准与当前 Policy 指纹一致，本地降级保持 `none`。
- 公共状态接口返回 `feature_enabled=true`、`status=managed_required`、`available=true`、`blocks_before_dispatch=false`、`reason_code=provider_workload_available`，只公开认证输出格式 `mp3` 与 `supports_image_prompt=false`，未公开连接或资格 ID。
- SQLite 只读核对确认 Binding、资格、连接、Adapter 与协议版本精确匹配；Workload Certification、Multimodal Certification Session 与 Workload Call 总数保持 `24/24/2`，本步骤没有创建新认证或运行调用。
- 激活时间窗口内只有一次 Policy `activate` 写请求及后续只读查询；没有认证、`/api/audio/jobs`、`/api/chat` 或模型数据面请求，因此没有产生 Provider POST、重试、回退或额度消耗。
- Lyria Clip 当前已完成真实资格、精确 Binding 与 `managed_required` 激活；仍缺一次单独授权的 Audio Job 用户 Smoke 和 MP3 人工播放确认，完成前不宣称端到端纳管。Lyria Pro 仍需独立资格和 Binding，不能继承 Clip 结果。

## Lyria Clip Audio Generation v3 用户入口真实 Smoke（2026-09-12）

- 经用户单独授权，在 `google/lyria-3-clip-preview` 音乐生成页提交一次无敏感合成描述；不带图片，只点击一次提交，未重试、未回退，也未切换第二 IP、连接、模型或 Adapter。
- 任务 `audio_3fcae8b7537b47d9b698100c78d82039` 为 `succeeded`，`provider_dispatch_state=confirmed`、`post_dispatched=1`、`provider_terminal_status=passed`；请求和实际模型均为 `google/lyria-3-clip-preview`，产生 701,351 bytes MP3，Provider 报告成本为 `$0.0400`。
- 新增且仅新增一个 Workload Run `workrun_ea575b9065494086a3a8b1766aae095a8ab16985e3568bf481fb79264777363f`、一个 Workload Call `workcall_60d9e4ba8f464554b721aa483c0742f0` 和一个 Audio Job；认证与 Session 总数保持 `24/24`。Call 精确关联当前 OpenRouter Managed Connection、`openrouter_audio_generation_stream_v1` 与资格 `workcert_be60c1eeea6e4440ab69d5d76b1c157d`，`call_sequence=1`、`dispatched=1`、`status=passed`。
- 脱敏指标为 TTFT 约 `12757.97 ms`、E2E 约 `12764.00 ms`，prompt/completion/total tokens 为 `23/4/27`。Server 访问日志只有一条 `POST /api/multimodal/audio/jobs`，随后只有状态与内容 GET，没有第二次付费提交。
- 内容端点返回 `200`、`Content-Type: audio/mpeg`、`Content-Length: 701351`，文件头以 `ID3` 开始。Router 存储扫描 8 个文件，合成描述标记、`Authorization: Bearer` 与该 MP3 特征头命中均为 `0`；浏览器 Console warning/error 为 `0`。
- 页面显示“已完成”、“已纳管 · 1 次 Provider 调用”、MP3 播放器和下载链接。内置预览器首次点击播放时标签页崩溃；已恢复同一完成任务页，未重新提交。用户随后在恢复页面完成人工试听并确认音乐可正常听到；因此技术 Smoke 与 MP3 听感门禁均通过。
- `audio_generation + google/lyria-3-clip-preview + 当前 OpenRouter Managed Connection + openrouter_audio_generation_stream_v1 + Generation v3` 现已完成真实资格、精确 Binding、`managed_required`、用户入口单 POST、Receipt、MP3 交付和人工听感的端到端闭环。
- 本次结论仅适用于 `google/lyria-3-clip-preview + audio_generation_stream + openrouter_audio_generation_stream_v1 + 当前连接指纹`。Lyria Pro 仍未被本次 Smoke 覆盖，不得继承 Clip 的资格、Binding 或验收结论。

## Lyria Pro Audio Generation v3 真实资格与图片提示阻断（2026-09-12）

- 用户单独授权一次 `google/lyria-3-pro-preview + audio_generation_stream + openrouter_audio_generation_stream_v1` 真实付费认证。认证前确认当前连接为 `conn_db4514e04d5c4da1b86d2e05f54e704a`，该连接启用、健康、具备 `audio` scope，并在当前 Inventory 中包含精确 Pro 模型。
- 只点击一次“确认并运行”。认证 `workcert_8b2c10e39adf411b8edb158ff4c6b570` 与 Session `mmcertsession_76e52048ab874979b0ffef24480d2827` 均为 `passed`；Session 为 `post_dispatched=1`、`provider_dispatch_state=confirmed`、`poll_count=0`，未重试、未回退，也未切换第二 IP、连接、模型或 Adapter。
- 请求和实际模型均为 `google/lyria-3-pro-preview`，Generation v3 参数合同、`openrouter-audio-generation-chat-stream-v2` 请求合同、Catalog、HTTP 2xx、非空音频、完整 MP3、完整响应与安全终止均通过，warnings 为空。脱敏指标为 TTFT 约 `20430.52 ms`、E2E 约 `20472.42 ms`，prompt/completion/total tokens 为 `10/8/18`。
- Workload Certification 与 Multimodal Certification Session 总数各由 `24` 增至 `25`，Workload Run/Call 保持 `3/3`。认证时间窗只有一条管理认证 POST 并返回 `200`，随后只有读取请求。Router 存储扫描 8 个文件，固定认证 Prompt、Authorization Header、OpenRouter Key 前缀和 MP3 头命中均为 `0`。
- 严格反向审计同时确认一项未闭环能力：静态音频产品合同对 Clip 与 Pro 都声明 `supports_image_prompt=true`，音乐页也对两者提供图片提示入口；但当前 Generation v3 认证只发送文本提示，Profile 固定为 `supports_image_prompt=false`。Managed Runtime 会在 Provider POST 前以 `provider_multimodal_audio_image_prompt_unsupported` 拒绝图片提示。
- 因此 Pro 当前只取得“无图片的文本音乐生成”真实资格，尚未创建 Binding、未更新或重新激活 Policy，也未执行用户 Smoke。在现有图片输入承诺未被真实认证覆盖前，不将 Pro 宣称为完整纳管，也不用静态 Catalog 声明把资格擅自升级为图片可用。

## Input v5 Receipt 计时修复与剩余模型矩阵（2026-09-08）

- 已定位并手术刀式修复 Input 用户 Smoke 中 `TTFT > E2E`：流证据与 Dispatch 完成记录此前使用不同起点；现在统一使用实际 Managed Dispatch 的 `started_at`，缺少 Dispatch 时才兼容回退到请求起点。
- 新增虚拟时钟回归固定证明：请求开始 `100.000s`、Dispatch 开始 `100.200s`、首文本 `100.300s`、完成 `100.350s` 时，TTFT 为约 `100ms`、E2E 为约 `150ms`，并保持单次 POST、精确实际模型及 `TTFT <= E2E`。
- R8D Chat、R8D 认证、Audio Jobs 与 R8C 受影响组合为 `406 passed`，仅有既有 FastAPI lifespan 弃用警告；`git diff --check` 通过。该离线修复不修改历史 SQLite，也不需要重新消耗一次 Input Provider 调用。
- 修复后的 server 镜像为 `modelmirror-provider-multimodal-r8d1-server:audio-contract-v5-pcm16-v3-lyria-v2-receipts-metrics-v2-20260908`；原容器保留为 `modelmirror-provider-multimodal-r8d1-server-preview-backup-before-metrics-v2-20260908-204802`，Router 数据目录未变。容器与 Worktree 的 `server/main.py` SHA-256 均为 `41a1570a168c9ce934f8a9d9f4bff209a31bcd8af4c11942ab4a7739a833e3a5`。
- 当前公开状态再次确认：`chat_audio_input + openai/gpt-audio` 为 `managed_required/available`；`chat_audio_output` 与 `audio_generation` 仍为 `legacy/provider_workload_policy_not_active`。重建未新增认证、Binding、Receipt 或 Provider POST。
- 在不收缩现有用户界面 ready 承诺的前提下，本批最小完整矩阵是四个精确组合：`gpt-audio` Input、`gpt-audio` Output、Lyria Clip Generation、Lyria Pro Generation。Clip 与 Pro 均在当前音乐工作台中可选择和提交，不能互相继承资格。
- `gpt-audio-mini` 的当前 OpenRouter 流式 Chat Audio 合同已在发送前明确阻断；本批不新增 unary Adapter。newAPI 与官方 OpenAI 直连也没有覆盖这三形态的当前 R8D Adapter，因此不属于本批必要重复组合。
- Output 的最小成功路径仍是一次 PCM16 v3 认证、精确 Binding/激活，以及另一次用户 Chat Smoke 与 WAV 人工播放确认；Clip 与 Pro 各自同样需要一次 Lyria v2 认证、Binding，以及一次 AudioJob Smoke 与 MP3 人工播放确认。每个真实 POST 分别授权，失败即停，不重试或切换目标。

## Chat Audio Output 首次真实认证与预算一致性修复（2026-09-08）

- 用户单独授权一次 `openai/gpt-audio + chat_audio_output + openrouter_chat_audio_v1` 认证。认证只发出一个 Provider POST，未重试、未回退，也未调用第二 IP、连接或模型。
- 认证 `workcert_98ecddaf113d42afb54884315e74f729` 与 Session `mmcertsession_f6444a7f4ebb46618501214b274002fb` 均记录为 `failed`；`post_dispatched=1`、`provider_dispatch_state=confirmed`，稳定错误码为 `provider_workload_stream_too_large`。脱敏指标为 TTFT 约 `1863.15 ms`、E2E 约 `11313.27 ms`。
- 根因是本地认证仍误用普通文本的 4 MiB 总流上限，而 Chat Audio Output 运行时允许 40 MiB SSE、最终 PCM16 最多 25 MiB；该失败不能证明上游或模型不可纳管。
- 手术刀式修复将 Chat Audio Output 的认证与运行总流、单事件、累计 Base64、累计交付文本和最终 PCM16 上限统一为共享预算。运行端的流式预检查为最多两个 CRLF 分隔符保留边界余量，最终严格判定仍由同一字节 Framer 执行。
- 新增认证与运行配对回归，覆盖普通文本 4 MiB 旧上限不再误拒绝、40 MiB 总流边界、分片 Base64、累计文本，以及 LF/CRLF 的单事件精确上限与超限一字节失败。
- 针对性边界测试为 `10 passed`；R8D Chat、R8D 认证、Audio Jobs 与 R8C 完整受影响组合为 `416 passed`，只有 4 条既有 FastAPI lifespan 弃用警告；`git diff --check` 通过。
- 经用户独立授权，修复后的 Server 已构建并切换为 `modelmirror-provider-multimodal-r8d1-server:audio-contract-v5-pcm16-v3-lyria-v2-receipts-metrics-v3-budgets-20260908`。旧容器保留为 `modelmirror-provider-multimodal-r8d1-server-preview-backup-before-budgets-v3-20260912-021936`。
- 切换前使用 SQLite Backup API 创建 `router.sqlite3.backup-r8d1-budgets-v3-20260908-215637`；Router 数据目录仍为 `C:\tmp\modelmirror-provider-multimodal-r8d1-preview-data`，既有 19 条认证、19 条 Session、1 条 Binding 与 1 组 Run/Call 均保留。
- 新容器关键源码 SHA-256 与 Worktree 一致：`server/main.py` 为 `72aba3fa907fb7277999a4ded28c1db7a9a18dddb07b11b1947dba348cdda8e1`，`multimodal_control.py` 为 `ea0cedf5f8d90c402d938197a60cc5ca9673b11b0b3c676f734f83bd0ef8e31c`；后端及前端代理健康检查均返回 `ok`，启动日志无错误级异常命中。
- 预览专用配对密钥已轮换且未输出；管理员状态为已配置、未认证。Output 仍未认证、未创建 Binding、未激活，也未纳管；新的付费认证须在用户重新配对后另行授权。

## Chat Audio Output 预算修复后真实认证（2026-09-12）

- 用户重新配对后明确授权一次 `openai/gpt-audio + chat_audio_output + openrouter_chat_audio_v1` 真实认证。表单目标连接、执行形态、精确模型和 Adapter 均在最终确认前重新核对；只点击一次“确认并运行”，未重试、未回退，也未切换第二 IP、连接、模型或 Adapter。
- SQLite 的 Workload Certification 与 Multimodal Certification Session 均由 `19` 增至 `20`，Workload Call 保持 `1`；Server 访问日志只有一条本轮认证 POST。Session `mmcertsession_041dc40204e741d692e2155aa1943527` 为 `post_dispatched=1`、`provider_dispatch_state=confirmed`。
- 认证 `workcert_f4352c9e40bc4d72be8037c2133aed58` 失败，稳定错误码为 `provider_workload_missing_terminal`。精确实际模型、HTTP 2xx、非空内容、完整响应、PCM16 传输、WAV 交付格式和 `[DONE]` 均已验证；上游没有提供当前 v3 契约要求的 `finish_reason=stop`，usage 未提供。脱敏 TTFT 约 `1951.64 ms`，E2E 约 `2849.54 ms`。
- 该结果证明此前预算误拒绝已消除，也证明当前 OpenRouter 路由的一次真实响应与 Output v3 的严格 `stop + [DONE] + 正常 EOF` 终止合同不兼容；它不证明解析器遗漏了合法 `stop`，也不足以证明上游存在不可修复硬阻断。
- Astra 独立只读审计确认认证与 Runtime 当前都一致要求 `finish_reason=stop` 和 `[DONE]`。有效 PCM16 再封装成结构完整 WAV 不能单独证明模型自然结束，因此本轮继续失败关闭，不伪造终止、不放宽断言、不再次付费试错。
- 若决定接受 OpenRouter 的 DONE-only Chat Audio Output，必须作为独立产品与安全决策升级 Output 认证契约版本，使旧资格 stale，并同步修改认证和 Runtime 后重新完成一次认证与一次用户 Smoke；在该决策获批前，Output 仍未纳管。

## Chat Audio Output DONE-only v4 离线收口（2026-09-12）

- 用户明确批准升级 Chat Audio Output 认证契约：仅当精确实际模型、完整且有效的 PCM16、无上游错误、正常 EOF 与 `[DONE]` 全部成立时，允许 `finish_reason` 缺失或为 JSON `null`；显式空字符串、非 `stop` 终止、异常 EOF、取消或不完整音频仍失败关闭。
- Output 参数合同已升级为 `modelmirror-provider-chat-audio-output-parameters-v4`；旧 v3 认证因 Profile 版本变化自动 stale。该兼容只作用于 `chat_audio_output`，未放宽 Chat Audio Input 或 Audio Generation 的终止合同。
- 认证和 Runtime 共用 `R8DAudioSseContract.safe_terminal_observed`。接受 DONE-only 时只记录脱敏警告 `finish_reason_missing_accepted`，不伪造 `finish_reason=stop`，也不把缺失 usage 填零。
- Astra 独立只读证伪发现：显式空 `finish_reason` 曾可被后续 `stop` 覆盖。现已手术刀式修复为空值粘性拒绝，并补测 `空值 -> stop -> 合法 usage replay -> [DONE]`；认证和 Runtime 均失败、均只派发一次 POST，且不交付成功音频。
- 断网容器离线验证：v4 定向组合 `10 passed`；完整 R8D 认证与 Chat 套件 `268 passed`；R8C、Audio Jobs 与既有 Chat Audio 交叉回归 `170 passed`。仅有 4 条既有 FastAPI lifespan 弃用警告。
- 本阶段未重建预览、未调用真实 Provider、未创建新 Binding、未激活 Output Policy，也未 Commit、Push 或更新 PR。此前 v3 失败认证只保留为历史证据，不能授予 v4 资格。
- Output 仍未纳管。下一门禁依次为：保留现有数据重建 v4 预览并轮换预览配对密钥；重新配对；单独授权一次 v4 真实认证；通过后再分别授权 Binding/Policy 与一次用户入口付费 Smoke，并人工确认 WAV 可播放和内容可听。

## Chat Audio Output v4 可逆预览重建（2026-09-12）

- 经用户独立授权，新 Server 镜像构建为 `modelmirror-provider-multimodal-r8d1-server:audio-output-v4-done-only-20260912`；断网临时容器确认 Output 参数合同为 `modelmirror-provider-chat-audio-output-parameters-v4`，并验证 Output DONE-only 接受、Output 空值后 stop 拒绝、Input DONE-only 拒绝。
- 镜像内 `multimodal_control.py` 与 `workload_control.py` SHA-256 分别为 `0778ca92cb0d4dc1b0ee7a12b13b383aadbfa2a5e04b9626a3e60f93d1a21459`、`6cbac9146d061b06086044da98d9493a8bf46fcf4fb90e87300c82ffe814a6fd`，与 Worktree 完全一致。
- 切换前使用 SQLite Backup API 创建 `/app/model_router/storage/router.sqlite3.backup-r8d1-output-v4-20260912-101133`。Router 数据目录继续使用 `C:\tmp\modelmirror-provider-multimodal-r8d1-preview-data`；切换前后均为 20 条 Workload Certification、20 条 Multimodal Session、1 条 Binding、1 组 Run/Call。
- 原 Server 容器完整保留为 `modelmirror-provider-multimodal-r8d1-server-preview-backup-before-output-v4-20260912-101215`；新容器继续使用原名称、独立网络及 `127.0.0.1:18155`，客户端仍使用 `127.0.0.1:15155`。前后端健康检查均为 `ok`，启动日志错误级匹配为 0。
- 预览管理员配对密钥已使用 32-byte 随机值轮换，容器内长度为 64 个十六进制字符；密钥值未写入源码、任务卡或工具输出。管理员状态为已配置、未认证。
- 重启后 Input 显示 `degraded_required`，只读证据确认原因是 2026-09-09 的真实认证超过默认 24 小时资格有效期，阻塞码为 `provider_workload_certification_expired`；Policy、Binding、连接指纹和批准记录均保留。这是时间门禁按设计生效，不是 Output v4 对 Input 的资格外溢。
- Output 保持 `legacy/provider_workload_policy_not_active`，尚无 v4 资格。本次重建未产生 Provider POST、未创建认证或 Binding、未激活 Policy，也未 Commit、Push 或更新 PR。

## Chat Audio Output v4 真实资格通过（2026-09-12）

- 用户在最终动作时确认一次 `openai/gpt-audio + chat_audio_output + openrouter_chat_audio_v1` 付费认证；页面只点击一次“确认并运行”，未重试、未回退，也未改用第二 IP、连接、模型或 Adapter。
- 认证 `workcert_1f8fc0d90c2f4006a2cf16077e792029` 与 Session `mmcertsession_ac0e793337644a84a04f487cacbe75bf` 均为 `passed`；Session 为 `post_dispatched=1`、`provider_dispatch_state=confirmed`、`poll_count=0`，且 `error_code` 为空。
- 资格精确绑定 Output v4 参数合同 `modelmirror-provider-chat-audio-output-parameters-v4`：请求与实际模型均为 `openai/gpt-audio`，上游 PCM16 为 24 kHz、单声道、16-bit little-endian，交付格式为 WAV，声线为 `alloy`。
- Catalog、HTTP 2xx、非空内容、精确实际模型、PCM16、WAV、完整响应、正常 EOF、`[DONE]` 与安全终止均通过。上游未提供 `finish_reason=stop`，因此保留脱敏警告 `finish_reason_missing_accepted`，没有伪造 stop；认证状态仍按已批准的 v4 DONE-only 合同判定为通过。
- 脱敏指标：TTFT 约 `1785.15 ms`，E2E 约 `2954.13 ms`，prompt/completion/total tokens 为 `10/152/162`。
- SQLite 中 Workload Certification 与 Multimodal Certification Session 均由 `20` 增至 `21`，Binding 保持 `1`，Workload Run/Call 保持 `1/1`；Server 访问日志在认证窗口内只有一条认证写请求并返回 `200`。新 Session、零重试合同与离线单 POST 回归共同证明本次只派发一次认证调用。
- 扫描当前 Router SQLite、WAL、SHM 与四份数据库备份共 7 个文件，固定认证提示词、Input 人工听审短句、WAV Base64 头和 OpenRouter Key 前缀命中均为 0；认证窗口日志也未出现 Prompt、音频、模型正文、Key 或错误级异常。
- Output 已取得当前连接指纹、精确模型、Adapter 与 v4 参数合同下的真实资格，但仍为 `legacy/provider_workload_policy_not_active`：尚未创建 Output Binding、激活 `managed_required`，也未执行用户入口付费 Smoke 或人工确认 WAV 可播放。因此“资格通过”不等于“端到端已纳管”。

## Chat Audio Output v4 Binding 与 Policy 激活（2026-09-12）

- 经用户单独授权，在设置页为 `chat_audio_output` 创建唯一精确 Binding：`openai/gpt-audio`、`conn_db4514e04d5c4da1b86d2e05f54e704a`、`openrouter_chat_audio_v1`，并关联当前 v4 资格 `workcert_1f8fc0d90c2f4006a2cf16077e792029`。
- Binding 保存后 Policy revision 由 `0` 增至 `1`；管理员确认当前无未解决 P0/P1 且接受 fail-closed 后，Policy 原子激活为 `managed_required`、revision `2`，人工批准有效，本地降级保持 `none`。
- SQLite 只读核对确认 Binding、资格和连接指纹一致，批准记录与当前 Policy 指纹一致且未撤销；请求模型、执行形态、Adapter 与协议版本均与通过的资格精确匹配。
- 公共状态现为 `feature_enabled=true`、`status=managed_required`、`available=true`、`blocks_before_dispatch=false`、`reason_code=provider_workload_available`，只公开声线 `alloy` 与交付格式 `wav`，未公开连接或资格 ID。
- 激活步骤的访问日志只有 Policy `PUT`、`activate` 与只读查询，没有认证、`/api/chat` 或其他模型数据面请求；因此本步骤没有产生 Provider POST 或额度消耗。
- Output 当前已完成真实资格、精确 Binding 与 `managed_required` 激活；仍缺一次单独授权的用户 Chat Smoke 和人工 WAV 播放确认，完成前不宣称端到端验收闭环。

## Chat Audio Output v4 用户入口真实 Smoke（2026-09-12）

- 用户在最终动作时确认一次 `openai/gpt-audio` 用户 Chat Smoke。页面启用原生语音回答，声线为 `alloy`，固定合成输入为 `Please say exactly: The blue circle is ready.`；只点击一次“发送”，没有重试、回退或第二个目标。
- 前端返回精确文本 `The blue circle is ready.`、可播放的原生音频控件，以及“Chat 音频输出控制面：已纳管 · 1 次 Provider 调用”；执行片段显示 `通过 · 1 次 · openai/gpt-audio`。浏览器 Console 的 warning/error 记录为 0。
- SQLite 的 Workload Run/Call 均由 `1` 增至 `2`。本次 Run `workrun_62100edb4b51550a90103ecbb4ad7b0fbe4fc7c6ed772ad4fa4d0490aa882439` 与 Call `workcall_66076c2c075a42d9b90c420d40025594` 均为 `passed`；Call 为 `dispatched=1`、`provider_dispatch_state=confirmed`、`call_sequence=1`，请求与实际模型均为 `openai/gpt-audio`，并精确关联当前连接、`openrouter_chat_audio_v1` 与 v4 资格。
- 脱敏指标为 TTFT 约 `1586.99 ms`、E2E 约 `2760.84 ms`，prompt/completion/total tokens 为 `17/53/70`。Server 日志计数为 `managed_multimodal_upstream_send=1`、`POST /api/chat=1`、retry/fallback=0、ERROR/Traceback=0。
- 当前 Router SQLite 对固定输入、返回文本、OpenRouter Key 前缀和 Authorization Header 的原始字节扫描命中均为 0；控制面没有保存用户文本、模型正文或凭据。
- 用户随后人工确认实际听到 `The blue circle is ready.`。因此 `chat_audio_output + openai/gpt-audio + 当前 OpenRouter Managed Connection + openrouter_chat_audio_v1 + Output v4` 已完成真实资格、精确 Binding、`managed_required`、用户入口单 POST、Receipt 与 WAV 听感的端到端闭环。
- 该结论只授予上述精确组合，不外推到 `openai/gpt-audio-mini`、其他模型、Adapter、声线或音频形态。页面初始化期间观察到的文件分析/输出辅助接口 503 未参与本次音频请求，不作为 R8D.1 Output 阻断。

## Audio Generation v4 图片提示合同离线收口（2026-09-12）

- 根因已定位为两处独立的静默丢参：v3 资格请求没有携带图片；Managed Runtime 又分别在任务创建与 Transport 调用处把已经校验的 `image_data_url` 覆盖为 `None`。因此，原先仅声明 `supports_image_prompt=false` 不能代表完整的图片提示能力，也不能通过简单翻转布尔值解决。
- Audio Generation 参数合同升级为 `modelmirror-provider-audio-generation-parameters-v4`。资格请求复用与 Runtime 相同的 payload builder，并携带仓库内固定 2x2 PNG；Runtime 将已校验的图片 data URL 仅在内存任务与一次 Provider POST 之间传递，不写入任务、Receipt、SQLite 或日志。
- v4 Profile 只保存固定素材 ID、MIME、SHA-256、宽高和 `supports_image_prompt=true`；资格证据新增 `image_prompt_request_verified=true`。派发前会严格核对文本 part 与固定图片 part，素材或结构不匹配即以 `provider_multimodal_audio_image_fixture_invalid` 失败关闭且产生零 Provider POST。
- 旧 v3 Audio Generation 资格会因参数合同版本变化自动 stale；迁移不重写旧记录、不改变 Binding/Policy，也不自动授予 v4 资格。
- MockTransport 端到端回归分别覆盖 `google/lyria-3-clip-preview` 与 `google/lyria-3-pro-preview`：资格 POST 和用户任务 POST 都精确包含图片，任务幂等重放不增加 POST；持久化记录扫描未发现 Prompt、图片 data URL、Base64、模型正文或凭据。
- 固定 PNG 回归同时验证签名、IHDR、由字节派生的宽高、SHA-256、每个 chunk CRC、IHDR/IEND 顺序和尺寸上限，避免测试仅自证常量一致。
- 本机 Python 缺少 `pytest`，属于环境阻断；语法编译通过。使用断网、只读源码挂载和临时存储目录的容器完成验证：Audio Generation v4 定向用例 `9 passed`；两份完整 Audio 测试文件 `268 passed`；扩展 Workload/Multimodal/R8C/Chat Audio 回归 `237 passed`，共 `505` 个不重复测试通过，仅有 4 条既有 FastAPI lifespan 弃用警告。
- 扩展回归首次失败被分离为 55 个只读挂载下 Router SQLite 路径未重定向的环境失败，以及 1 个仍断言 v3 `supports_image_prompt=false` 的陈旧测试；配置临时存储并手术刀式更新该断言后，原组合全部通过。
- 当前仅完成离线实现与证伪门禁：预览器仍运行 v3 镜像，本阶段没有重建、真实 Provider POST、付费调用、Binding/Policy 变更、Commit、Push 或 PR。
- v4 真实验收只能证明同一请求中的固定图片与文本被目标 Adapter 接受，并返回结构有效的 MP3；它不能单独证明图片对音乐内容产生了可感知的语义影响。
- 剩余门禁依次为：单独授权保留旧容器与数据重建 v4 预览；分别授权 Clip v4 与 Pro v4 各一次真实图片资格（每个精确模型独立取证）；资格通过后再单独授权至少一次图片提示用户 Smoke。任何一次批准均不覆盖重跑或另一模型。

## Audio Generation v4 可逆预览重建（2026-09-12）

- 经用户独立授权，新 Server 镜像构建为 `modelmirror-provider-multimodal-r8d1-server:audio-generation-image-v4-20260912`。断网临时容器确认 Audio Generation 参数合同为 `modelmirror-provider-audio-generation-parameters-v4`，固定图片素材尺寸为 2x2。
- 镜像内 `workload_control.py` SHA-256 为 `a62dd9ebd5049419fb4166d9de83bb3c2dd568712bac72ee7051b1adeda8ad24`，`audio_jobs.py` 为 `139fe1dbaff653bf0a09c14a7b005a85759c79a7fbff86ca28c1dc32ca9ad06c`，均与 Worktree 完全一致。
- 切换前使用 SQLite Backup API 创建 `/app/model_router/storage/router.sqlite3.backup-r8d1-audio-generation-v4-20260912-132703`，大小为 1,482,752 bytes，`PRAGMA integrity_check=ok`。
- Router 数据目录继续使用 `C:\tmp\modelmirror-provider-multimodal-r8d1-preview-data`。切换前后均为 25 条 Workload Certification、25 条 Multimodal Certification Session、3 条 Binding、3 条 Workload Run 和 3 条 Workload Call，未清空、迁移或重写数据。
- 原 v3 Server 完整保留为 `modelmirror-provider-multimodal-r8d1-server-preview-backup-before-audio-generation-v4-20260912-062738`；新容器继续使用原名称、独立网络、`127.0.0.1:18155`、原持久化挂载，Client 继续使用 `127.0.0.1:15155`。
- 前后端 `/api/health` 均返回 `ok`；管理员状态为已配置、未认证；新 Server 启动日志中 Traceback、ERROR、CRITICAL、OpenRouter Key 前缀和配对密钥变量字面量命中均为 0。
- 预览专用配对密钥已轮换为 32-byte 随机值，容器内长度为 64 个十六进制字符；密钥值未进入源码、任务卡或工具输出。
- 公共状态证明 Clip 与 Pro 均未取得当前 v4 图片资格，且 Policy 尚未激活，因此保持派发前阻断。旧 v3 证据没有被当作 v4 资格，也没有自动改变 Binding 或 Policy。
- 本次重建未发起 Provider POST、未产生额度消耗、未创建认证、未修改 Binding/Policy，也未 Commit、Push 或更新 PR。下一步必须重新配对，并分别取得用户对 Clip v4、Pro v4 各一次真实资格调用的授权。

## Clip v4 图片资格真实认证失败（2026-09-12）

- 用户重新配对并在最终确认弹窗前单独授权一次 `google/lyria-3-clip-preview + audio_generation_stream + openrouter_audio_generation_stream_v1` 真实资格调用。表单精确绑定历史 Lyria 所用连接 `conn_db4514e04d5c4da1b86d2e05f54e704a`；只点击一次“确认并运行”，未重试、回退或切换第二目标。
- 资格 `workcert_1a7c720ccf4a4796a13231fb5f68103e` 与 Session `mmcertsession_c4217985d2424ec4b5cb122e7463d823` 均为 `failed`。Session 为 `post_dispatched=1`、`provider_dispatch_state=confirmed`、`poll_count=0`，稳定错误为 `provider_multimodal_audio_stream_empty`。
- v4 图片请求门禁已通过：`image_prompt_request_verified=true`，Profile 保存固定素材 ID、SHA-256、PNG、2x2 与 `supports_image_prompt=true`；请求和实际模型均为 `google/lyria-3-clip-preview`。
- HTTP 2xx、完整响应、精确模型、正常 EOF、`finish_reason=stop` 与 `[DONE]` 均通过；共接受 4 个合法 SSE 事件，但音频片段数为 0、`content_observed=false`，因此正确地失败关闭。脱敏指标为 E2E 约 `11199.60 ms`，prompt/completion/total tokens 为 `1458/4/1462`，没有音频 TTFT。
- SQLite 中 Workload Certification 与 Multimodal Session 各由 25 增至 26，Binding、Run、Call 保持 `3/3/3`；认证窗口只有一条认证 API POST，retry/fallback、错误栈与秘密标记均为 0。Router SQLite/WAL/SHM 对固定 Prompt、图片 data URL、图片 Base64 与 OpenRouter Key 前缀的扫描命中均为 0；浏览器 warning/error 为 0。
- OpenRouter 当前官方图片输入合同要求在 Chat Completions 的 `messages[].content` 中先放文本、再放 `image_url`，并支持 Base64 data URL；Lyria Clip 官方模型页声明图片输入与音频输出。本次 v4 payload 与该结构一致，因此没有证据把失败归因于本地图片序列化或再次丢参。
- 当前证据只能证明上游以成功 HTTP 和正常 SSE 终止返回了零音频；不能区分“2x2 无语义素材未触发音乐生成”和“上游当前路由产生文本式结果”。在未增加仅保存布尔/计数的 text-only 诊断、并换用仍无敏感信息但具有明确视觉语义的固定图片前，不应再次付费试错，也不应开始 Pro v4 认证。
- UI 当前把失败记录中的 `image_prompt_request_verified=true` 显示为“图片提示：已认证”，容易被误读为能力认证通过；应改为“图片请求：已携带/已验证”，且只有整体资格 `passed` 才能显示能力已认证。
- Clip 未获得 v4 资格，不创建 Binding、不激活 Policy；Pro 也仍未认证。本次失败不影响已闭环的 Chat Audio Output，也不证明 Lyria 图片提示存在不可绕过的上游硬阻断。

## Audio Generation v5 诊断与固定素材离线收口（2026-09-12）

- v4 真实失败已证明图片确实进入唯一一次 Provider POST，但无法区分“文本式正常响应”与“完全空流”。本次没有放宽成功条件，而是为 Audio Generation 增加有界诊断：只保存是否观察到 SSE 文本和最多 65,535 的字符计数，绝不保存文本正文。
- 正常结束但只返回文本、没有任何音频片段时，稳定错误由笼统的 `provider_multimodal_audio_stream_empty` 细分为 `provider_multimodal_audio_text_only`；真正无文本、无音频仍保留原错误。无论哪种情况，均继续失败关闭且不得重试或回退。
- Audio Generation 参数合同升级为 `modelmirror-provider-audio-generation-parameters-v5`，旧 v4 资格自动 stale。Profile 新增固定认证 Prompt 的 SHA-256，并继续绑定请求合同、图片素材 ID、图片 SHA-256、MIME 和精确尺寸，避免后续静默更换认证输入。
- 原 2x2 近乎无语义图片替换为固定、无敏感信息的 96x64 PNG（420 bytes）：可辨识的音乐符号、日月、山水构图。素材 ID 为 `r8d-audio-generation-image-v2`，SHA-256 为 `422dec23a06d4dea51be6ce13039db1261372e2c56e6403fbf80631f571e4856`；认证 Prompt SHA-256 为 `a2ad68b865b1f59afaa27788517d7ae56abcc76fe87d8e10096684ed92f60be5`。
- 设置页不再把失败记录中的图片请求证据显示成“图片提示：已认证”。新文案区分“图片认证请求已携带固定素材”和整体资格状态；若观察到文本式 SSE，只显示字符数并明确“仅记录计数”。
- 离线门禁：v5 定向回归 `4 passed`；完整 R8D 认证与 Audio Jobs `269 passed`；相邻 Workload Control 与 Chat Audio `127 passed`；R8C、STT、TTS 与既有 Multimodal Chat Audio `170 passed`，共 `566` 个不重复后端测试通过。设置组件 `19 passed`、Server Header `1 passed`；标准 TypeScript typecheck 与 production build 通过；`git diff --check` 和 Python 语法编译通过。相邻后端测试仅有既有 FastAPI lifespan 弃用警告。
- 前端全量 Vitest 为 `984 passed / 3 failed`。三个失败全部位于未修改的 `client/src/data/models.refresh.test.ts`：静态快照实际为 506 live / 9 expired，而测试仍断言 509 / 6，并将 `nex-agi/nex-n2-mini`、`nex-agi/nex-n2-pro`、`z-ai/glm-4.7-flash` 视为 live。该测试、`models.ts` 与其唯一另一导入 `modelOptions.ts` 均与当前 `HEAD@c6739a12ce2abf9873e39efcc66f0e0a68ef13c0` 字节无 Diff，因此分类为本补充补丁之前的静态 Catalog 基线漂移；未修改口径或顺手修复。
- 标准 typecheck 首次因独立 Worktree 内两个旧 `tsbuildinfo` 文件权限失败；仅删除并重新生成这两个可再生缓存后，原命令通过。该失败未通过替代性绿测掩盖。
- 本阶段没有重建预览、没有再次调用 Provider、没有修改 Connection、Binding 或 Policy，也没有 Commit、Push 或 PR。v4 失败记录仍保留为历史证据，不能授予 v5 资格。
- 下一门禁是单独授权保留现有容器和数据重建 v5 预览并轮换预览配对密钥；重新配对后，再对精确 Clip v5 发起一次独立授权的付费认证。该授权不覆盖失败重跑、Pro、Binding/Policy 或用户 Smoke。

## Audio Generation v5 可逆预览重建（2026-09-12）

- 经用户独立授权，新 Server 镜像构建为 `modelmirror-provider-multimodal-r8d1-server:audio-generation-v5-diagnostic-image-v2-20260912`。断网临时容器确认参数合同为 `modelmirror-provider-audio-generation-parameters-v5`，固定素材为 `r8d-audio-generation-image-v2`、96x64 PNG、420 bytes。
- 镜像内固定图片 SHA-256 为 `422dec23a06d4dea51be6ce13039db1261372e2c56e6403fbf80631f571e4856`，固定 Prompt SHA-256 为 `a2ad68b865b1f59afaa27788517d7ae56abcc76fe87d8e10096684ed92f60be5`，均与 Worktree 源码一致。
- 切换前使用 SQLite Backup API 创建 `/app/model_router/storage/router.sqlite3.backup-r8d1-audio-generation-v5-20260912-072035`，大小为 1,486,848 bytes，`PRAGMA integrity_check=ok`。
- Router 数据目录继续使用 `C:\tmp\modelmirror-provider-multimodal-r8d1-preview-data`。切换前后均为 26 条 Workload Certification、26 条 Multimodal Certification Session、3 条 Binding、3 条 Workload Run 和 3 条 Workload Call，未清空、迁移或重写数据。
- 原 v4 Server 完整保留为 `modelmirror-provider-multimodal-r8d1-server-preview-backup-before-audio-generation-v5-20260912-072125` 且处于停止状态；新容器继续使用原名称、独立网络、`127.0.0.1:18155`、原持久化挂载，Client 继续使用 `127.0.0.1:15155`。
- 前后端 `/api/health` 均返回 `ok`；Live Server 报告 v5 参数合同。旧 v4 失败资格仍原样保存，并由当前代码派生为 `provider_multimodal_audio_parameter_contract_stale`，没有被错误授予 v5 资格。
- 预览专用配对密钥已轮换为 32-byte 随机值，容器内长度为 64 个十六进制字符；密钥值未进入源码、任务卡或工具输出。管理员状态为已配置、未认证。
- 新 Server 启动日志共 9 行；Traceback、CRITICAL、Exception、错误级日志、配对密钥变量名及其值命中均为 0。
- 本次重建未发起 Provider POST、未产生额度消耗、未创建认证、未修改 Binding/Policy，也未 Commit、Push 或更新 PR。下一步必须重新配对，并单独授权一次精确 `google/lyria-3-clip-preview + audio_generation_stream + openrouter_audio_generation_stream_v1 + v5` 真实付费认证；本次重建授权不覆盖该调用。

## Audio Generation v5 Clip 真实资格通过（2026-09-12）

- 用户重新配对，并在最终确认弹窗前单独授权一次 `google/lyria-3-clip-preview + audio_generation_stream + openrouter_audio_generation_stream_v1 + v5` 真实付费认证。只点击一次“确认并运行”，没有重试、回退、第二 IP、第二连接、第二模型或第二 Adapter。
- 认证 `workcert_0053b19ffb8b4096ac29a37efcf5348a` 与 Session `mmcertsession_26b4f8db9f394f59821f7ea3e9c97da6` 均为 `passed`；Session 为 `post_dispatched=1`、`provider_dispatch_state=confirmed`、`poll_count=0`，且错误码为空。
- 请求和实际模型均为 `google/lyria-3-clip-preview`。资格精确绑定参数合同 `modelmirror-provider-audio-generation-parameters-v5`、请求合同 `openrouter-audio-generation-chat-stream-v2`、固定素材 `r8d-audio-generation-image-v2`、96x64 PNG、图片 SHA-256 `422dec23a06d4dea51be6ce13039db1261372e2c56e6403fbf80631f571e4856` 与 Prompt SHA-256 `a2ad68b865b1f59afaa27788517d7ae56abcc76fe87d8e10096684ed92f60be5`。
- Catalog、HTTP 2xx、精确实际模型、固定图片请求、非空内容、媒体格式、完整响应、`finish_reason=stop`、`[DONE]` 与安全终止全部通过。接受 5 个合法 SSE 事件，其中 1 个音频片段，并观察到 14 个 SSE 文本字符；只保存布尔值和计数，不保存文本正文。warnings 为空。
- 脱敏指标：TTFT 约 `13202.08 ms`，E2E 约 `13221.64 ms`，prompt/completion/total tokens 为 `1466/4/1470`。
- SQLite 中 Workload Certification 与 Multimodal Certification Session 均由 `26` 增至 `27`；Binding、Workload Run 和 Workload Call 保持 `3/3/3`。认证窗口只有一条管理认证 POST；单一 Session 的已派发证据、零重试合同和离线单 POST 回归共同证明 ModelMirror 仅派发一次认证调用。该证据不推断 newAPI 或 OpenRouter 内部的最终供应商调用次数。
- 当前 Server 日志中认证 API POST 为 1，Audio Job POST 与 Chat POST 均为 0，retry/fallback、Traceback 和错误级日志命中均为 0。Router 存储共扫描 10 个文件，固定 Prompt、图片 data URL、图片 Base64 前缀、Authorization Header 和 OpenRouter Key 前缀命中均为 0。
- Router SQLite 再次执行 `PRAGMA integrity_check` 返回 `ok`，当前计数为 27 条资格、27 条 Session、3 条 Binding、3 条 Run、3 条 Call。
- 本次只取得当前连接指纹下 Clip v5 的真实资格。既有 `audio_generation` Binding 仍关联旧 v3 资格，Policy 仍为原 `managed_required` revision 2；因此公开状态按设计为 `degraded_required`、`available=false`、`blocks_before_dispatch=true`。本阶段没有更新 Binding、重新批准/激活 Policy 或执行用户 Smoke。

## Audio Generation v5 Client 预览漂移修复（2026-09-12）

- 真实资格完成后发现 Live Client 仍运行旧镜像 `modelmirror-provider-multimodal-r8d1-client:audio-contract-v5-pcm16-v3-20260908`，把失败记录中的图片请求证据误显示为“图片提示：已认证”。这是预览部署漂移，不是后端资格结果错误。
- 当前 Worktree 构建的新镜像为 `modelmirror-provider-multimodal-r8d1-client:audio-generation-v5-diagnostic-ui-20260912`；production build 通过，只有既有 Vite chunk-size 警告。产物包含“图片认证请求”“已携带固定素材”“SSE 文本响应”，且不再包含旧文案“图片提示：已认证”。
- 旧 Client 完整保留为停止容器 `modelmirror-provider-multimodal-r8d1-client-preview-backup-before-audio-generation-v5-ui-20260912-073848`；新 Client 继续使用原名称与 `127.0.0.1:15155`。新旧容器环境变量集合完全一致。
- 前端代理与后端 `/api/health` 均返回 `ok`；新 Client 处于运行状态，日志错误标记为 0。页面重载后保持管理员会话，并正确显示本次资格为 `passed`、MP3、固定图片素材已携带及 SSE 文本仅记录 14 字符计数。
- Client 重建和切换没有发起认证、Chat 或 Audio Job，也没有修改 Router 数据、Binding 或 Policy，不产生模型费用。下一门禁仍是单独授权的 Binding/Policy 更新；其后用户入口付费 Smoke 还需再次独立授权。

## Audio Generation v5 Binding 与 Policy 重新激活（2026-09-12）

- 经用户单独授权，在设置页为 `audio_generation` 原子保存唯一精确 Binding：`google/lyria-3-clip-preview + conn_db4514e04d5c4da1b86d2e05f54e704a + audio_generation_stream + openrouter_audio_generation_stream_v1 + modelmirror-provider-multimodal-v1`，并关联当前 v5 资格 `workcert_0053b19ffb8b4096ac29a37efcf5348a`。
- Binding 保存后 Policy revision 由 `2` 增至 `3`，旧批准保持失效。管理员随后确认当前无未解决 P0/P1 并接受 fail-closed，Policy 原子重新激活为 `managed_required`、revision `4`；本地降级保持 `none`。
- SQLite 只读核对确认 Binding、资格、连接、Adapter 和协议版本精确一致；最新批准记录的 Policy 指纹与当前 Policy 指纹均为 `b52df5319f2c85edc37e6671e8881f55b9e31195939c6799f99be92e709ea4d9`，批准未撤销，两项人工确认均为真。
- 公共状态现为 `feature_enabled=true`、`status=managed_required`、`available=true`、`blocks_before_dispatch=false`、`reason_code=provider_workload_available`，并只公开 `mp3` 与 `supports_image_prompt=true`，没有公开内部连接或资格 ID。
- 最近服务日志记录 1 次 Policy PUT 和 1 次激活 POST；认证 POST、Audio Job POST、Chat POST、Managed upstream send、retry、fallback 及错误级日志均为 0。因此本步骤没有发起 Provider 调用或产生模型费用。
- Audio Generation v5 现已完成真实资格、精确 Binding 和 `managed_required` 激活；仍缺一次单独授权的图片提示用户 Smoke、MP3 完整性核验及人工听感确认，完成前不宣称端到端纳管闭环。

## Audio Generation v5 图片提示用户 Smoke（2026-09-12）

- 用户单独授权一次付费用户 Smoke。页面保持精确模型 `google/lyria-3-clip-preview`，使用仓库内无敏感信息的 `client/public/logo.png` 作为图片提示，并提交一条描述蓝紫渐变、明快电子器乐与清晰收束的固定英文描述；只点击一次提交，没有重试或第二个目标。
- 任务 `audio_d2c2a5eaf9f941c1a2a20a817b71711d` 成功完成，API 返回 `execution_mode=managed`、`provider_dispatch_state=confirmed`、`parameters.has_image=true`、请求与实际模型一致、实际结算成本 `$0.04`，输出为 `733645` bytes。
- Workload Run `workrun_eacc695009a6ee21cd011a4b0dccd1fe9bda550bec9b5078ee7ad532f85b23f7` 与 Call `workcall_5a38eeb3bae64db4a686d08f93f8ffc2` 均为 `passed`。Call 为 `call_sequence=1`、`dispatched=1`、`provider_dispatch_state=confirmed`，精确关联当前 v5 资格、OpenRouter 连接、`openrouter_audio_generation_stream_v1` 和 `modelmirror-provider-multimodal-v1`；Run/Call 总数均由 `3` 增至 `4`。
- Receipt 显示一个逻辑调用、一个成功 Call，prompt/completion/total tokens 为 `1482/4/1486`。TTFT 约 `96794.97 ms`、E2E 约 `96799.69 ms`；上游接近结束时才交付首个音频片段，但没有超时、重试、回退或第二 Provider。
- 内容端点返回 HTTP `200`、`Content-Type: audio/mpeg`、长度 `733645`，文件头为 `ID3`，SHA-256 为 `5112e379e1e7b1578b1a75333859f954a6c9c64f22792f6a1de35c0bafc6b315`。页面显示 `716 KiB`、约 30 秒播放器、下载入口与“已纳管 · 1 次 Provider 调用”。
- 最近服务日志只有 1 次 Audio Job POST，Chat POST 与认证 POST 均为 0，retry、fallback、Traceback 与错误级日志均为 0。Router 持久化目录扫描 10 个文件，固定描述、图片 Base64 前缀、音频 Base64 前缀、`logo.png`、Authorization Header 与 OpenRouter Key 前缀命中均为 0；浏览器 Console warning/error 为 0。
- 浏览器播放器已真实进入播放状态，观测到 `paused=false`、播放进度约 `12.32/30.30` 秒。用户随后确认音乐可以正常听到；因此技术 Smoke、单 POST、Receipt、MP3 完整性、隐私与人工听感门禁均通过。

## Input v5 资格续期与 Policy 恢复（2026-09-12）

- 既有 Input v5 资格因 24 小时有效期到期而进入 `degraded_required`，没有自动回退 Legacy。用户授权后，对精确组合 `chat_audio_input + openai/gpt-audio + openrouter_chat_audio_v1` 发起一次重新认证，未重试或切换目标。
- 新资格 `workcert_e00986d97ca049a3b164ad05411827b2` 与 Session `mmcertsession_585bb25766df42008226b7ec29e28a76` 均为 `passed`；Session 为 `post_dispatched=1`、`provider_dispatch_state=confirmed`，且错误码为空。TTFT 约 `2962 ms`、E2E 约 `2973 ms`、总 Token 为 `117`。
- Binding 已重新关联当前资格，管理员再次确认无 P0/P1 并接受 fail-closed；Policy 为 `managed_required` revision `4`，人工批准有效。公开状态为 `available=true`、`blocks_before_dispatch=false`，只公开已认证输入格式 `wav`。
- 资格续期没有改变 Input v5 素材、评分合同、Adapter 或用户入口协议；2026-09-08 已通过的真实用户 Smoke 仍作为同一精确组合的数据面证据。本步骤只恢复时间资格，不把结果外推到 `gpt-audio-mini` 或其他音频形态。

## Lyria Pro v5 大 SSE 事件根因与手术刀修复（2026-09-12）

- 首次 Pro v5 图片资格 `workcert_78c816f4de5b4c4eb6e418940e4bca8b` 与 Session `mmcertsession_8195b7ed310d4c9290bf429fcb1c7d42` 失败，稳定错误为 `provider_workload_sse_event_too_large`。该调用只派发一次 POST；Catalog、HTTP 2xx、图片请求和精确实际模型均已通过，但下一事件在观察音频前被本地大小门禁拒绝。
- 根因是本地预算不一致：最终媒体上限为 `25 MiB`，累计编码与总 SSE 上限约为 `35/39 MiB`，单事件却仍沿用 `2 MiB`。Pro 将完整、有界的音频 Base64 放在单个 SSE 事件中，因而被本地假阴性拒绝；Clip 的较小事件没有暴露该缺陷。
- 修复只调整 Audio Generation 严格 Framer 的单事件上限，使其与既有总 SSE 上限一致；最终解码媒体仍严格限制为 `25 MiB`，累计流、Base64、MP3 完整性、精确模型、终止、单 POST 和无回退门禁均未放宽。
- 新增回归证明：超过旧 `2 MiB` 但位于当前总预算内的分片事件可被接受，超过当前总预算的事件仍失败关闭；预算不变量同时要求单事件上限覆盖最大合法编码媒体。最窄用例 `4 passed`，R8D 认证、Audio Jobs、Chat Audio 与 Workload Control 组合为 `397 passed`，仅有既有 FastAPI lifespan 弃用警告。
- 新 Server 镜像为 `modelmirror-provider-multimodal-r8d1-server:audio-generation-pro-large-event-v6-20260912`。切换前用 SQLite Backup API 创建 `C:\tmp\modelmirror-provider-multimodal-r8d1-preview-data\router.sqlite3.backup-r8d1-audio-generation-pro-v6-20260912-083241`，完整性为 `ok`；旧 v5 容器保留为 `modelmirror-provider-multimodal-r8d1-server-preview-backup-before-pro-large-event-v6-20260912-0833`。
- 新容器继续使用原端口、网络和 Router 持久化目录，前后端健康检查均为 `ok`。断网镜像核对确认单事件与总 SSE 上限均为 `39146853` bytes，且覆盖最大合法编码媒体；重建没有发起 Provider POST或删除数据。

## Lyria Pro v5 资格、双模型 Binding 与真实 Smoke（2026-09-12）

- 修复后按用户批准对 `google/lyria-3-pro-preview + audio_generation_stream + openrouter_audio_generation_stream_v1 + v5` 发起唯一一次重新认证。资格 `workcert_383d76faf3c84acabac5d9e6a3ee9bfa` 为 `passed`，对应 Session 为 `post_dispatched=1`、`provider_dispatch_state=confirmed`、`poll_count=0`，且错误码为空。
- 认证请求与实际模型一致，图片请求、HTTP 2xx、媒体格式、非空音频、完整响应、`finish_reason=stop`、`[DONE]` 和安全终止均通过；接受 5 个 SSE 事件和 1 个音频片段。TTFT 约 `42732 ms`、E2E 约 `42750 ms`、总 Token 为 `1476`。
- `audio_generation` Policy 原子保留 Clip Binding 并新增 Pro Binding，两者分别关联自己的当前 v5 资格。保存后旧批准按设计失效；重新人工批准后 Policy 为 `managed_required` revision `6`、`approval_valid=true`。Clip 与 Pro 的公开状态均为 `available=true`、`blocks_before_dispatch=false`、`supports_image_prompt=true`。
- 用户批准的 Pro 图片提示 Smoke 通过等价用户数据面 API 执行：任务 `audio_24f45042d3a1480fbc2a3ca30d121fd8` 使用仓库内非敏感 `client/public/logo.png`，只提交一次，没有重试或第二目标。任务为 `succeeded`，请求与实际模型均为 Pro，`execution_mode=managed`、`provider_dispatch_state=confirmed`、`parameters.has_image=true`，Provider 报告成本 `$0.08`。
- Workload Run `workrun_dd098465328cc22cbaf72ae73729e179a9d23e36d4ebb419b6786885afc131b6` 与唯一 Call `workcall_d56f6b78c3a84105a3638f4a95173839` 均为 `passed`。Call 为 `call_sequence=1`、`dispatched=1`，精确关联 Pro v5 资格、当前连接、Adapter 和协议版本；TTFT 约 `92585 ms`、E2E 约 `92617 ms`，usage 为 `1475/10/1485`。
- 内容端点返回完整 MP3：`4147953` bytes，文件头为 `ID3`，SHA-256 为 `3332d0070980467382d806d28fab9356314159f2e3dab1be8d8cc57d9483edfa`，本地完整性检查为 `true`。Server 日志无 ERROR、Traceback 或 Exception。
- 当前 SQLite 为 30 条 Workload Certification、30 条 Multimodal Certification Session、4 条 Binding、5 条 Workload Run、5 条 Workload Call和 3 条 Audio Job，`PRAGMA integrity_check=ok`。控制面只保存脱敏任务、模型、调用、指标和状态证据；未输出 Prompt、图片、音频正文或凭据。
- 用户已在任务 `audio_24f45042d3a1480fbc2a3ca30d121fd8` 的结果页完成人工试听并确认可正常听到音乐。至此 Pro 的资格、精确 Binding、`managed_required`、图片提示、单 POST、Receipt、完整 MP3 交付与人工听感门禁均已通过。

## R8D.1 当前三形态结论（2026-09-12）

- `chat_audio_input + openai/gpt-audio`：当前资格、精确 Binding、`managed_required` 与真实用户 Smoke 均通过。
- `chat_audio_output + openai/gpt-audio`：当前资格、精确 Binding、`managed_required`、真实用户 Smoke 与人工 WAV 听感均通过。
- `audio_generation + google/lyria-3-clip-preview`：当前 v5 图片资格、精确 Binding、`managed_required`、真实用户 Smoke、完整 MP3 与人工听感均通过。
- `audio_generation + google/lyria-3-pro-preview`：当前 v5 图片资格、精确 Binding、`managed_required`、真实用户 Smoke、完整 MP3 与人工听感均通过。
- `openai/gpt-audio-mini + 流式 Chat Audio` 仍因官方不支持 Streaming 而在派发前失败关闭；它不是本补充轮选定的必要模型，不影响上述三种执行形态通过各自精确模型完成纳管。

因此，R8D.1 选定的三种执行形态已全部完成端到端纳管；结论严格绑定上述精确模型、Adapter、当前连接指纹与合同版本，不外推到其他模型或未认证组合。

## 提交前最终门禁（2026-09-12）

- 在断网、只读源码挂载和临时 Router、MCP、Agent、RAG 存储目录下复跑受影响后端组合，结果为 `399 passed, 4 warnings`；警告仅为既有 FastAPI lifespan 弃用提示。最初三次收集失败均由只读隔离命令未重定向运行时 SQLite 目录导致，未进入测试断言，也未修改源码或产生 Provider 调用。
- 前端精确回归为 `35 passed`，独立 Header 检查为 `1 passed`；原始 TypeScript typecheck 在删除当前 Worktree 内两个不可写、可再生的 `tsbuildinfo` 缓存后通过，production build 通过并转换 `3176` 个模块，仅有既有大 chunk 警告。
- 前端全量测试按“失败即停”在 `models.refresh.test.ts` 的 3 项动态 Catalog 断言处中止：其中 2 项与父 PR #349 的既有 Catalog 快照漂移一致；第 3 项源于 `z-ai/glm-4.7-flash` 已于 `2026-09-10 UTC` 到期而旧断言仍要求 `live`。R8D.1 未修改模型目录、市场快照或该测试文件，三项均分类为父基线/时间漂移，不通过本音频补充轮顺手修复。
- 父 PR #349 当前仍为 `OPEN`，Head 为 `c6739a12`，R8D.1 精确基于该提交；因此本轮发布为以 `codex/provider-multimodal-audio-r8d` 为 Base 的堆叠 PR，不把尚未合并的父变更重复提交到 `main`。
- 提交前收尾阶段没有执行认证、用户数据面或其他 Provider POST；先前获授权的真实资格与 Smoke 证据保持不变。

- R8D.1 继续由当前 Sol 主智能体完成，不在批次中途切换模型。
- R8D.1 完成离线门禁、独立预览、获授权的真实认证、证据审计和收尾后，生成一份可直接交给 Astra 的过渡提示词。
- 交接材料必须包含：基线与分支、已实现 Diff、验证命令与结果、真实调用次数和 Receipt、已知阻断、未决产品选择、R8D PR 状态、回滚方式及下一批边界。
- 后续采用 Astra 主导：负责跨批次规划、异常判断、浏览器关键操作链、证据整合和最终验收。
- 后续 Sol 子智能体只承接边界明确、文件范围不重叠、结果可独立验证的实现、测试或资料整理；不得同时操作 Astra 正在控制的同一浏览器页面。
- 主智能体负责复核子智能体产物、解决冲突并执行最终门禁；委托不扩大付费调用、外部写入、提交、PR 或部署授权。

## Help Center Impact

- 当前补充轮保持共享 legacy 音频目录不变；新的证据只约束 Managed 资格、参数合同、交付和失败关闭，不能用静态目录声明替代真实认证。
- 若后续选择新的模型或 Adapter，将同步更新正式帮助文章、预览截图和实操证据后才能创建 R8D.1 PR。

## 官方证据

- OpenRouter Audio：https://openrouter.ai/docs/guides/overview/multimodal/audio
- OpenAI GPT-Audio Mini：https://developers.openai.com/api/docs/models/gpt-audio-mini
- OpenAI GPT-Audio：https://developers.openai.com/api/docs/models/gpt-audio
- OpenRouter Lyria 3 Clip：https://openrouter.ai/google/lyria-3-clip-preview/pricing
- Google Lyria Music Generation：https://ai.google.dev/gemini-api/docs/music-generation
