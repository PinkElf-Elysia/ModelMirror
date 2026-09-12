# 任务卡：R8D Chat Audio 与音频生成 Provider 控制面

## 范围

- 原实施基线：`origin/main@62b60cb9a78186515852e0b0fdb5fbcb3e1889f6`，已包含合并后的 R8C。
- 全量验证基线：`origin/main@cbb50f1095a51f2c32958ab4f7dd4e34dadfc2c2`。
- 最终 rebase 基线：`origin/main@0ad5aa9f7e849e1874999f0a25471d331285b3f3`。
- 接管 `chat_audio_input`、`chat_audio_output` 与 `audio_generation`。
- 执行形态分别为 `chat_audio_input`、`chat_audio_output` 与 `audio_generation_stream`。
- 只统一精确 Binding、Adapter、资格、安全出口、一次派发、Receipt、重启恢复和失败边界。
- Chat SSE 与异步音频任务继续保持各自协议；不迁移 STT/TTS、视频、Realtime、Voice Cloning 或 R9。

## 安全边界

- Feature Flag 默认关闭；Policy 为 `legacy` 时原路径保持不变。
- 音频输入、原生音频输出和音频生成必须分别认证，资格不得互相继承。
- 当前计划没有定义组合执行形态；Managed 请求同时要求音频输入和原生音频输出时在 POST 前阻断。
- Managed 调用只允许一个已批准 IP、一个连接、一个模型、一个 Adapter 和一个 Provider POST。
- POST 派发后不得切换 Provider、回退 legacy 或自动重放；中断、超时或重启后的未知结果记为
  `uncertain`，同一逻辑键不得再次派发。
- Chat Audio 只有在观察到精确实际模型、形态所需内容和安全终止后才成功。纯音频输出是合法结果，
  但纯文本不能冒充音频输出成功。
- 音频生成只有在完整 SSE 终止、精确实际模型和完整 MP3 结构均验证通过后才成功；部分音频不得发布。
- 控制面、Receipt、SQLite、日志和管理 API 不保存用户音频、Prompt、模型音频、转录、凭据或完整错误体。

## 验收

- 固定合成 WAV 的 Chat Audio Input、固定短文本的 Chat Audio Output 和固定短 Prompt 的音频生成
  资格分别通过，并且每个形态最多一个付费 POST。
- Chat Audio 的文本/音频 delta、Route Receipt、`message_end` 与 `[DONE]` 顺序保持兼容；缺少实际模型、
  缺少所需内容、非法 SSE、空流、断流、取消和超时均不产生成功 Receipt。
- 音频任务的幂等键最多产生一个 POST；删除内容、页面重复提交和 Server 重启不能擦除派发证据或重放。
- 已派发任务重启后保持 `provider_dispatch_state=uncertain`、`retry_allowed=false`；未派发任务可确定失败。
- Receipt、任务、精确 Binding、实际模型、Adapter 和真实 POST 数一致，且 R5—R8C 与 legacy 路径无回归。
- 独立预览、真实资格与用户入口 Smoke 分别授权；本任务卡及 PR 授权不批准新的付费调用。

## 回滚

- 显式停用受影响 Policy，关闭 R8D Feature Flag 并重启，恢复 legacy 路径。
- 保留 v18 表、资格、Receipt、任务幂等证据和临时媒体生命周期记录；不得删除 Router SQLite、
  Provider 凭据、newAPI 数据或已派发任务证据。

## Help Center Impact

- 影响用户体验：是。Settings 增加三个执行形态的资格与 Binding；Chat Audio 和音频生成在 Managed
  模式下新增派发前阻断、脱敏 Receipt 与不确定结果提示。
- 受影响入口：`/settings?section=providers`、`/settings?section=routing`、支持 Chat Audio 的聊天页与
  音频生成工作区。
- 最新主线的“功能暂不可用时怎么办”保持面向普通用户的通用指南，不加入 R8D 运维细节；具体状态、
  阻断原因和 Receipt 由 Settings 与用户入口就地说明，架构和恢复边界由本任务卡及部署文档承载。

## 当前验收状态

- 全量验证基线上的六个受影响后端文件为 `148 passed, 4 warnings`，R8B/R8C 回归为
  `104 passed, 4 warnings`，主线交叉语义测试为 `154 passed, 4 warnings`；取消、SSE 注入、
  HTTP 错误体有界处理、写盘竞态和 `delivery_pending` 序列化阻塞均已补测。
- 全量验证基线的全量后端为 `5499 passed, 20 failed, 29 skipped`；20 个失败已在同 SHA detached
  基线精确复现，均来自测试镜像缺少 Worker 构建产物或 TypeScript loader 依赖，未发现 R8D 新增失败。
- 前端全量 `893 passed`、Header `1 passed`、production build 通过。标准 typecheck 受 Windows
  `TS5033/EPERM` 增量缓存写入阻塞，app/node 两套非增量等价检查通过。Core、newAPI、Overlay
  三套 Compose 配置验证通过。
- 最终 rebase 后，R8D 与 R8B/R8C 后端专项合并运行 `252 passed, 4 warnings`，Workflow retry
  交叉专项 `99 passed, 4 warnings`，前端 R8D 与 Workflow retry 合并专项 `94 passed`；标准
  typecheck 仍仅出现同一 `TS5033/EPERM`，两套非增量等价检查和 production build 均通过。
- 隔离预览镜像构建、server/client liveness、管理会话配对、三个 Settings 页签、Marble 独立
  可用性和本地音频目录只读探针通过。R8D 三个入口保持 `legacy` 且无 Binding，配对后仍为
  0 连接、0认证、0运行和 0 Provider Call；预览验收不等于真实 Provider 资格。
- 已经分别获得即时授权并对 `chat_audio_input`、`chat_audio_output` 和
  `audio_generation_stream` 执行真实认证，三者均未通过；用户入口 Smoke 没有执行。
  不得据此宣称 R8D 已生产启用或 Provider 已通过真实验收。
- 所有 R8D Feature Flag 继续保持默认关闭，合并 PR 不等于启用 `managed_required`。

## 2026-09-04 受限收尾决定

- 用户明确批准以“控制面实现可交付，Chat Audio Input Provider 资格 NO-GO”收尾。该批准
  只调整本批次的交付分类，不将未通过的真实资格改写为成功，也不批准生产启用。
- `mistralai/voxtral-small-24b-2507` 在 OpenRouter 路径稳定返回 HTTP 400；
  `openai/gpt-audio-mini` 在 v2 中通过传输、精确模型、非空内容和安全终止，但仍不满足
  严格转写合同。两条路径均保留为脱敏负面资格证据，不再通过放宽匹配、反复调整
  Prompt 或试探 wire 追求绿灯。
- `chat_audio_input` 不创建 Binding，Policy 保持 `legacy`，仓库 Feature Flag 默认保持关闭。
  预览容器虽为认证验收注入了开关，但 SQLite 中无 R8D Policy 和 Binding，不构成
  Managed 激活或生产就绪。
- `chat_audio_output` 和 `audio_generation_stream` 的第一次真实资格已分别以确定 4xx 和 5xx 失败；
  未进入用户入口 Smoke。是否将“受限收尾”扩展到这两个形态，或保持原验收门禁并阻塞
  本批次，属于尚未授权的产品交付决策。任何新模型、Adapter 或重试仍需新的即时授权。
- 若后续需恢复 Chat Audio Input 资格，应以 R8D.1 独立资格任务处理 OpenRouter 工单结果或
  新的精确模型/Adapter，不回改本批次的负面证据。

## 2026-09-02 音频输入认证最小修复

- 修复基线为 `c1ba765e111429ee19d60a26eb432b63ff9aeb38`；保留当前独立工作树和历史认证，
  不迁移数据库、不增加依赖、不修改普通 Chat、R8C、Provider 配置或用户入口协议。
- 历史真实尝试依次为 HTTP 401、HTTP 403、HTTP 200 后内容不匹配。最后一次报告输出
  32 tokens，恰好达到原有上限，但未保存终止原因，不能把截断推测写成已确认根因。
- Chat Audio 认证把 `Okay` 与 `OK` 视为同一词的有限等价拼写；重复、解释性句子和其他
  非空回答仍拒绝。固定 WAV、提示词、32-token 预算、单 POST 与零重试均不改变。
- 认证显式拒绝 `error`、`content_filter`、`length`、工具调用或未知终止原因；后来的
  `stop` / `[DONE]` 不能清除先前错误。音频生成仍要求 `[DONE]`、`stop` 和完整 MP3。
  缺少终止信号不能被转写不匹配掩盖；无显式错误时仍保留音频格式校验的旧错误优先级。
- SSE 只接受缺省索引或整数 `index=0`、且带对象型 `delta` 的唯一 choice；拒绝显式 null/非法
  `choices`、多 choice、非零/非法索引和任意非 null 顶层 `error`，防止畸形事件或跨 choice
  拼接被误判为成功。请求字段保持既有兼容口径，不依赖新增 `n` 参数约束 Provider。
- 既有 `checks_json` 加法保存安全终止验证、DONE、白名单终止类别和转写匹配布尔值。
  管理 API 加法返回这些字段；不保存转写、未知终止字符串、媒体或上游错误正文。
  老记录未采集的观察字段返回空值，安全终止验证缺失时为 false，不回填历史证据。
- 旧 Chat Audio 通过记录缺少 `safe_terminal_verified` 时，摘要和 Binding 资格按
  `provider_multimodal_audio_evidence_incomplete` 失效；历史记录保留，不能自动重新认证。
  其他形态不新增此资格要求。回滚代码前应保持受影响 Chat Audio Policy 停用。
- 回归先在旧实现复现失败，再在断网容器中使用 Mock Transport 和临时 SQLite 验证；
  源码只读挂载，不挂载预览数据，不访问真实 Provider。其后的付费重测与预览恢复另行授权。
- 独立证伪审查复现并修复了“标量/数组/假值顶层错误被忽略”和“多 choice 拼成 `OK`”两条
  假阳性路径；早退响应关闭使用未消费的可追踪异步流证明，不再依赖预先关闭的响应对象。
- 冗余参数矩阵收敛后，最终 R8D 文件为 `62 passed`，R8D/R8C/R8B 及统一多模态、Workload
  控制面回归为 `242 passed, 4 warnings`；Python 语法、三套 Compose、前端 typecheck 和
  production build 通过。前端全量仅有 2 个随 UTC 日期过期的 Catalog 快照断言，且 `client/`
  相对 HEAD 无 Diff，归类为时间型基线漂移，不属于本修复。
- 修复期间的后端全量为 `5773 passed, 20 failed, 29 skipped`；20 个失败全部位于未改动的
  Agency Worker 构建产物和 TypeScript loader 环境路径，与既有基线失败类别一致。全量后新增的
  严格解析修复已由上述最终 `242 passed` 相邻回归覆盖。
- 2026-09-03 使用原预览持久化数据和热更新后的两份生产文件，对同一 OpenRouter 连接及
  `openai/gpt-audio-mini` 发起一次获批的 `chat_audio_input` 认证。认证
  `workcert_ca6b9784a0434c68b5c3642faca122d2` 观察到 HTTP 成功、非空内容和匹配的实际模型，
  随后明确观察到 `finish_reason=length`，稳定失败为 `provider_workload_output_truncated`；
  `safe_terminal_verified=false`，没有重试。该证据确认 32-token 输出上限是当前阻塞项，新的
  终止诊断生效。用户随后批准仅把 `chat_audio_input` 认证预算提高到 64；其他 R8D 形态继续
  使用 32。修改后的 R8D 完整专项为 `62 passed`，Chat 数据面兼容回归为
  `28 passed, 4 warnings`；隔离预览热更新文件哈希一致且 server/client 均返回 HTTP 200。
  新的真实付费复测仍需单独授权。
- 最近预览日志的凭据、Bearer、固定 Prompt 和 WAV Base64 模式扫描为 0 命中。一次错误 CSRF
  请求在进入认证服务前被拒绝；它没有触发 Provider 调用。只读历史核对曾因 SQLite WAL 行为触发
  checkpoint 并合并 `-wal/-shm`，逻辑记录仍可读取，但原字节级 WAL 拓扑已改变并在此如实记录。
- 2026-09-03 再次获得一次独立额度授权后，认证
  `workcert_b4adf13beedf4a649d15a9f55fcd464f` 只产生一个 Provider POST 且没有重试。64-token
  预算已消除截断：实际模型匹配、观察到非空内容与 `finish_reason=stop`，usage 为 30/32/62；但
  随后的合法 OpenRouter 终止 usage 帧被解析器误判为 `provider_workload_invalid_sse`，因此该次认证
  仍为失败，不能作为资格证据。
- OpenRouter 官方 Streaming 文档确认：Chat Completions 会在普通 `stop` 后、`[DONE]` 前发送一次
  非空单 choice 的 final usage chunk；该 choice 使用空文本 delta 并重复 `finish_reason=stop`。
  修复仅对 `chat_audio_input` / `chat_audio_output` 放行这一种 accounting frame：唯一 choice、严格
  整数 `index=0`、空 content、可选 assistant role、相同 generation、三项非负整数 usage 且总量一致。
  第二次 replay、正文、音频、工具调用、非法计数、generation 漂移、缺少 `[DONE]` 或 replay 后任何
  JSON 均失败关闭；usage 只有在 `[DONE]` 完成后才进入证据。`audio_generation_stream` 不采用该例外，
  Lyria/音乐生成终止契约保持不变。
- 最终离线门禁为：终止状态机定向 `23 passed`、完整 R8D `75 passed`、相邻 R8B/R8C/R8D Chat、
  多模态与 Workload 回归 `231 passed, 4 warnings`；`git diff --check` 通过。两名独立只读审查者分别
  检查生产白名单和测试夹具，未发现离线实现阻塞项。
- 2026-09-04 经独立授权重启预览后，server/client 健康检查均为 HTTP 200，容器内生产文件哈希与
  工作树一致。认证 `workcert_beb7d7336856447b93b1c7370a39f2a4` 只产生一个 Provider POST，
  没有重试或备用 Provider 调用。真实流现已通过安全终止校验：实际模型匹配，观察到非空内容、
  `finish_reason=stop`、final usage replay 和 `[DONE]`，usage 为 30/32/62；这证明 OpenRouter
  accounting-frame 兼容修复有效。认证最终仍因固定 WAV 转写未严格匹配 `okay`/`ok` 而以
  `provider_multimodal_chat_audio_input_content_mismatch` 失败；模型正文按设计未保存，因此当前证据
  不能区分固定素材与模型转写变体。日志窗口确认出站 Provider POST 恰为 1，未命中 Key、Bearer、
  配对密钥、CSRF、Cookie、固定 Prompt、WAV 或原始 SSE。R8D 真实资格门禁仍未通过；未授权再次调用。
- 2026-09-04 用户人工试听并确认固定 WAV 清楚表达 `Okay` 后，另行授权改用音频输入模型。
  无费用预检确认 `mistralai/voxtral-small-24b-2507` 在选定 OpenRouter 连接的完整 Inventory 中为
  active，连接 online 且具有 `chat` / `audio` scope。认证
  `workcert_de822aab4f6948b291c533053572e9f6` 随后只发出一个 Provider POST，并由 OpenRouter
  返回 HTTP 400；稳定结果为 `provider_workload_http_error`、`provider_dispatch_state=confirmed`、
  `retry_allowed=false`。日志窗口只出现一次已脱敏的 Provider POST，没有重试或备用 Provider；
  未记录上游错误正文，因而不能无证据断言具体 400 原因。OpenRouter 当前公开文档确认本地请求使用的
  `/chat/completions`、base64 `input_audio` 与 WAV 格式属于公开合同，但目录中的 audio modality 不等于
  该精确模型和上游 endpoint 已通过 R8D 流式资格。该模型组合按失败处理，不放宽认证合同，也不消耗
  第二次付费调用；R8D 的真实 `chat_audio_input` 门禁仍未通过。
- OpenRouter 的只读 Upstream Requests 记录进一步确认：上述一次 ModelMirror POST 在 OpenRouter
  内部产生两次 Mistral 尝试，两次均为 HTTP 400；这是数据面内部路由，不是 ModelMirror 的第二
  Provider 或派发后回退。该失败记录的 Generation metadata GET 返回 404，现有脱敏证据仍无法恢复
  精确上游校验正文，因而不能把单一原因描述为已经证实。
- 官方 OpenRouter 音频输入合同与本地 JSON wire 一致，但没有要求 16 kHz，也没有证明该 Voxtral
  endpoint 的 `input_audio + stream=true` 组合。原固定素材为 PCM16 单声道 8 kHz、0.4255 秒；因此曾
  仅把素材替换为 16 kHz、1 秒，作为单变量诊断实验，而不是已经证实的合同修复。实验保持 Prompt、
  模型、Adapter、`stream=true`、64-token 预算和单 POST 语义不变。
- 诊断代码曾验证完整 OpenRouter part、JSON/SSE headers、16-bit、单声道、16 kHz、1 秒与一次 POST；
  当时完整 R8D 为 `75 passed`、R8C 为 `91 passed`、Chat Audio 用户路径为
  `14 passed, 4 warnings`。这些绿测只证明诊断变量按预期生效，不证明真实上游兼容。
- 2026-09-04 经单独授权后，使用当前工作树重建 `18154` 隔离 server 预览；server/client 均返回
  HTTP 200，`multimodal_control.py`、`workload_control.py` 与 `schemas.py` 的容器内 SHA-256 均与
  工作树一致。随后只对同一 OpenRouter 连接、`mistralai/voxtral-small-24b-2507`、
  `openrouter_chat_audio_v1` 和 `stream=true` 发起一次 `chat_audio_input` 认证，仅将固定素材替换为
  16 kHz、1 秒版本。认证 `workcert_f0ec8ab152c44545aa2b62cb8bab9c01` 仍以 HTTP 400 失败，稳定结果为
  `provider_workload_http_error`、`provider_dispatch_state=confirmed`、`retry_allowed=false`。
  OpenRouter Upstream Requests 将该次 ModelMirror 请求记录为单一请求
  `gen-1788504451-8iirnY2LNNzAE7X3S3q3`，其数据面内部对 Mistral 进行了两次均为 400 的尝试，
  总延迟 940 ms；这不构成 ModelMirror 的二次 POST 或派发后回退。该单变量结果已推翻“原 8 kHz
  素材是唯一 400 根因”的假设，但上游界面仍未提供具体校验正文，不能继续猜测是流式组合还是其他
  模型端约束。未执行第二次 Provider 调用；该精确模型/Adapter 的 R8D 流式资格继续失败关闭。
- 因 16 kHz 素材及其资格 profile 未修复真实失败，且公开合同并不要求该规格，收口时已回退这项
  实验性代码并恢复共用固定素材；保留它只会无证据改变资格语义并使历史证据失效。此前的 64-token
  预算、安全终止、accounting frame 和脱敏防护不受影响。后续若继续诊断，必须另行授权并保持单变量；
  非流式成功也不能升级当前流式执行形态的资格。
- 回退后使用断网、只读源码和临时 SQLite 目录复跑：完整 R8D `75 passed`，相邻 R8C 与 Chat Audio
  `105 passed, 4 warnings`，`git diff --check` 通过。未执行新的 Provider 调用。当前 `18154` 预览仍是
  已完成该次诊断的 16 kHz 镜像；在下一次明确重建前，不把它当作回退后源码的运行证据。
- 2026-09-04 经再次单独授权，保留上述 16 kHz 诊断镜像、连接、模型、Adapter、Prompt、温度与
  64-token 预算，以一次性无持久化 runner 仅把请求模式改为 `stream=false`（并使用对应 JSON Accept）。
  runner 复用 DNS/SSRF pinning，设置 HTTP Transport `retries=0`，在第二个 POST 前硬阻断，不调用正式
  认证 API，也不写入资格。结果仍为 HTTP 400，`post_count=1`、响应完整且确定，generation ID 为
  `gen-1788506316-bZNfb2obl85QwCbWFour`；只读 generation metadata GET 返回 404，未获得脱敏错误类型。
  OpenRouter Upstream Requests 只显示这一条顶层请求，内部两次 Mistral 尝试均为 400，总延迟约
  1.1 秒。由此 `stream=true` 也被排除为该 400 的唯一根因；16 kHz 与非流式两项诊断均不支持继续
  猜测修改通用 wire。该精确模型/Adapter 组合继续失败关闭，未更新认证、Binding 或 Policy，且未
  发起第二个模型 POST。
- 后续只处理“确定 HTTP 400 被折叠后缺少安全次级分类”的可观测性阻塞。依据
  [OpenRouter Errors and Debugging](https://github.com/OpenRouterTeam/docs/blob/main/api_reference/errors-and-debugging.mdx)
  当前公开的 `error.metadata.error_type` 枚举，并经两路独立只读审查后，新增仅适用于
  `openrouter + chat_audio_input + openrouter_chat_audio_v1 + HTTP 400` 的有界解析：只接受未压缩 JSON、
  严格 UTF-8、无重复键、与 HTTP 状态一致的整数 `error.code`，响应正文最多 16 KiB、读取最多 1 秒。
  只有精确命中编译期白名单的 `error_type` 才映射为固定 `warning_code`；主错误仍保持
  `provider_workload_http_error`，不改变 failed/confirmed、`retry_allowed=false`、资格或路由语义。
  `message`、`provider_code`、未知字段、响应正文及 body generation ID 均不保存、不输出、不记录；
  非 JSON、压缩、畸形、超限、超时或读取失败均退回原稳定 HTTP 分类，外层仍关闭响应。未修改
  Repository、Schema、API 或 UI，也未发起新的 Provider 调用。
- 该收口的离线证伪结果：错误 envelope 专项 `22 passed`，完整 R8D `98 passed`，相邻 R8C 与
  Chat Audio `119 passed, 4 warnings`，R8B—R8D 与统一 Workload 合并回归
  `273 passed, 4 warnings`。覆盖单 POST、幂等重放、16 KiB 边界、伪造及 5000 位纯数字
  Content-Length、压缩/非 JSON、非法 UTF-8/JSON、重复键、类型混淆、未知 subtype、读取失败/超时、
  诊断读取期间取消仍保留确定 HTTP 400、跨形态和跨状态隔离，以及 API/SQLite/log 脱敏。上述证据只
  证明诊断护栏，不改变 Voxtral 真实资格仍被 HTTP 400 阻断的结论。
- 在等待 OpenRouter 对 Voxtral HTTP 400 的上游诊断期间，用户批准独立验证
  `openai/gpt-audio-mini`。该模型此前已通过 HTTP、实际模型、内容与安全终止检查，仅因输出未严格
  等于固定 WAV 的 `okay`/`ok` 而失败。为避免放宽门禁或在 Prompt 中泄露答案，
  `chat_audio_input` 使用新的 `modelmirror-provider-chat-audio-input-parameters-v2`：提示词明确要求只
  转写音频中的单个词，禁止标点、标签、解释、翻译和回答语义；严格归一化匹配保持不变。旧 v1
  `chat_audio_input` 资格因此 stale，但 `chat_audio_output` 与 `audio_generation_stream` 继续使用原 v1，
  不被无关失效。此变更尚未产生 Provider 调用；完成离线门禁和预览重建后，真实认证仍需单独额度授权。
- v2 离线门禁已完成：定向契约与失效测试 `12 passed`，完整 R8D `98 passed`，R8C 与两套 Chat
  Audio 相邻回归 `133 passed, 4 warnings`，R8B—R8D 与统一 Workload 宽回归
  `250 passed, 4 warnings`；`git diff --check` 通过。测试均在断网容器、只读源码和 `/tmp` 临时存储中
  完成，未访问预览数据或 Provider。v2 镜像已构建，但运行时切换未获独立授权，因此旧 server 已原样
  恢复并确认 server health 与 client Settings 均为 HTTP 200；尚未执行 v2 真实认证。
- 随后用户明确批准保留旧容器和数据、维持现有音频开关、轮换预览配对密钥并切换 v2。新 server
  使用镜像 `modelmirror-provider-multimodal-r8d-server:chat-audio-input-v2-20260904-1`，原 server 以
  `modelmirror-provider-multimodal-r8d-server-preview-pre-chat-audio-v2-20260904` 保留；Router SQLite
  备份 `router.sqlite3.backup-20260904-chat-audio-input-v2` 的完整性为 `ok`。server health 与 client
  Settings 均通过，三个关键生产文件的 Worktree/容器 SHA-256 一致。运行时契约为
  `modelmirror-provider-chat-audio-input-parameters-v2`，Prompt 不包含预期答案；历史 8 条 input 认证
  的最新记录仍为 `2026-09-04T06:47:31.062110+00:00`，其 v1 profile 明确派生为
  `provider_multimodal_audio_parameter_contract_stale`。重建未新增认证或 Provider POST；真实 v2
  `gpt-audio-mini` 认证仍需一次独立额度授权。
- 用户完成新配对并单独授权后，对 `openai/gpt-audio-mini` 执行了一次 v2
  `chat_audio_input` 真实认证，未重试。认证数从 8 增至 9，新记录
  `workcert_082118673dce4b66b767d78ff518f1ff` 的 profile 确认使用
  `modelmirror-provider-chat-audio-input-parameters-v2`，session 为 `post_dispatched=1` 且
  `provider_dispatch_state=confirmed`。HTTP 2xx、Catalog/精确模型、非空内容、`stop`、`[DONE]`、完整响应和
  安全终止均通过，实际模型为 `openai/gpt-audio-mini`；但 `transcript_matches_fixture=false`，
  最终仍以 `provider_multimodal_chat_audio_input_content_mismatch` 失败。模型正文按设计未保存，
  因此当前证据只能确定 v2 已生效且上游返回了不符合严格 `okay`/`ok` 合同的文本，
  不能推断具体转写内容。同时段容器日志只有一次认证 API POST、两次后续读取，无服务端异常。
  该精确模型/Adapter 组合仍未取得 Chat Audio Input 资格；本次授权不覆盖再次付费调用。
- 在受限收尾决定后，用户又单独授权对 `openai/gpt-audio-mini` 执行一次
  `chat_audio_output` 真实认证。认证 `workcert_7f647f702d2649cdbb43b406dc27d2ce`
  只产生一次认证 API POST 和一个已派发 Provider Call，无重试；session 为
  `post_dispatched=1`、`provider_dispatch_state=confirmed`。Catalog 与精确模型前置检查通过，
  但上游在内容消费前返回确定的非 2xx 4xx 响应，因此稳定记录为
  `provider_workload_http_error`；未观察到实际模型、音频内容或终止帧。服务端日志无异常，
  API、SQLite 和日志未保存上游错误正文。该模型/Adapter 的 Chat Audio Output 资格未通过，
  本次授权未执行 Lyria 认证或用户入口 Smoke。
- 用户随后又单独授权对 `google/lyria-3-clip-preview` 执行一次
  `audio_generation_stream` 真实认证。认证 `workcert_eb769394dfa24f4e94da2bb26f64cde7`
  仅产生一次认证 API POST 和一个已派发 Provider Call，无重试；session 为
  `post_dispatched=1`、`provider_dispatch_state=confirmed`。Catalog 和精确模型前置检查通过，
  但上游在内容消费前返回确定 5xx，稳定记录为 `provider_workload_http_5xx`；
  未观察到实际模型、MP3 内容或终止帧。服务端日志无异常，API、SQLite 和日志未保存
  上游错误正文。该模型/Adapter 的 Audio Generation 资格未通过，本次授权未执行
  用户入口 Smoke。
