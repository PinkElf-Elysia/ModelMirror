# OpenRouter 2026-10-13 更新

## 开工契约

- 基线 origin/main `2df7efba` (#415)，独立工作树 `C:/tmp/modelmirror-openrouter-oct13`，分支 `codex/openrouter-oct13-scaledown`，开始时干净。
- 范围：四个新模型、同窗口漂移、用户指定的前六排和旗舰替换。默认调用模型保持不变，不触碰共享栈、凭据、依赖或数据迁移。
- 用户已确认新增 ScaleDown 摘要/提取专用工作台与受限接口，首轮文字输入。Classify-1 复用 Decisions；图片/PDF 不宣称已适配。
- 分批修改目录、受限后端、工作台及测试；跨文件是同一契约的端到端适配所需。每阶段独立审计。回退为撤销本轮差异，无持久化迁移。
- 验收：目录双审计/readiness、模型排序与 Batch 回归、后端契约/白名单/非法输入/异常测试、工作台交互测试、前端构建。真实调用、预览、提交及 PR 需另行授权。

## 初始证据

- 冻结四源 manifest `C:/tmp/openrouter-oct13-window/manifest.json`；初始审计 `C:/tmp/openrouter-oct13-before/summary.json`。
- 源 586 非 Batch + 80 Batch，指定四项为全部漏项；11 项元数据漂移、10 个结构化市场记录，生命周期及 Batch 无漂移。
- ScaleDown 官方目录和 endpoints 均已核对。Summarize/Extract 使用非流式 Chat Completions；Extract 的 OpenRouter 参数路径为 `provider.options.scaledown.entities`，首轮只开放名称到描述字符串的有界映射，不透传任意 provider 参数。
- 参考：https://docs.scaledown.ai/api-reference/extract 、https://docs.scaledown.ai/api-reference/summarize 、OpenRouter 对应模型目录。

## 补充窗口与最终范围

- 用户追加六项：`~unbiased/pareto-latest`、`unbiased/pareto-26.9`、`heygen/heygen-voice-1`、`heygen/heygen-voice-1-turbo`、`liquid/lfm-2.5-24b-a2b`、`fish-audio/drama-3`，以及 Argon Batch；本轮合计十个目录实体和一个服务档位。
- 第二窗口：`C:/tmp/openrouter-oct13-window2/manifest.json`，UTC 2026-10-14 04:24:04.497 至 04:24:07.434（本地 Phoenix 10 月 13 日）。四源固定后，依次执行新增、Batch、元数据、生命周期和市场分类同步，每阶段复核。
- 最终证据：`C:/tmp/openrouter-oct13-window2-final/summary.json`，状态 `clean`。源 667 条 = 586 非 Batch + 81 Batch；本地 713 = 586 在售 + 102 可能不可用 + 25 过期保留，站内保留入口口径 688。Batch 不计入模型总数，不作为首页独立卡片。
- 第二窗口修正 22 个模型的结构化元数据、47 个原有市场结构记录；同步波动字段，但不以波动字段作为门禁。最终缺项、生命周期、价格、操作、能力、Batch 和市场分类差异均为零。此结论仅绑定上述冻结窗口，不代表之后上游不会变化。
- 六项源下架改为保留入口的可能不可用：`apodex/apodex-1.1-mini:free`、`inclusionai/ling-3.0-flash-fin`、`inclusionai/ling-3.0-flash-sante`、`inclusionai/ming-image-0.1-design`、`inclusionai/ming-image-0.1-design-layer`、`unbiased/pareto`。`unbiased/pareto-26.10-preview` 的 2026-10-15 到期日期仍是未来日期，未提前标记过期。

## 专用契约与兼容边界

- Summarize/Extract 使用 `/scaledown/:modelId` 和受限 `/api/scaledown`。文字上限 100000 字符、指令 4000 字符；提取字段 1–50 个。摘要最多 1000 输出 Token（采用官方接口上限，不照搬目录 4000）；提取最多 4000。非流式、不自动重试，拒绝图片/PDF、任意 provider 参数；响应不透传上游敏感元数据。普通 Chat 和原生实时路由排除三个 ScaleDown 模型，Classify 复用 Decisions。
- Argon 复用实时聊天和音频理解，Batch 复用现有文本异步工作台，提交基础模型 ID。Batch 输入/输出分别 $1/$5 每百万 Token；实时 $2/$10。
- HeyGen 两款复用语音工作台，10 条目录声线，首轮固定默认语速、4000 字符，不开放克隆/BYOK。OpenRouter PCM 按 44.1kHz 单声道校验后封装 WAV；不是直接调用 HeyGen 的 NDJSON 接口。价格分别 $15/$7.5 每百万字符。
- Drama 3 首轮只开放官方示例中的三条公开声线、单声线 MP3；不开放多说话人或克隆。价格 $2/百万 UTF-8 字节，前端按编码后字节数预估，不能按字符或 Token 估算。
- 三款新语音均为契约适配、真实行为待人工验收；没有发送付费调用，不能据模拟测试宣称 Provider 验证通过。语音页面沿用既有布局，去除“已验证声线/认证格式”这种超过证据的笼统文案。
- 新语音价格单位加入同步保护和回归测试，避免后续通用快照刷新覆盖字符/字节单位。现有审计 JSON 的 `audio_hour_pricing_overlay_mismatches` 字段为兼容保留名称，其检查范围现包含这些单位价格。
- Pareto latest 保留别名请求 ID，不把当下解析目标 26.9 写死为调用 ID。
- 首页 Opus 5.5 取代 Fable 5.1 旗舰；Argon、GPT 6.1 Sol、Sonnet 5.5、GPT 6 Luna、Haiku 5.5 及普通 Fable 均在前六排。其他新增项散布在前五排之后；应用默认调用模型未改。

契约来源：

- https://docs.scaledown.ai/api-reference/summarize
- https://docs.scaledown.ai/api-reference/extract
- https://openrouter.ai/heygen/heygen-voice-1
- https://openrouter.ai/heygen/heygen-voice-1-turbo
- https://openrouter.ai/fish-audio/drama-3
- https://docs.fish.audio/features/text-to-speech/drama-3
- https://openrouter.ai/docs/guides/overview/multimodal/tts

## 验证与交付边界

- 后端专项：`test_oct13_speech`、`test_multimodal_tts`、`test_scaledown`、`test_openrouter_decisions`、`test_multimodal_chat_foundation`、`test_multimodal_image`、`test_native_router_chat`、`test_model_router_routing`、`test_multimodal_chat_audio`、`test_multimodal_chat_video`、`test_openrouter_batch`：193 passed，4 条现有 FastAPI 生命周期弃用警告。解释器 `C:/tmp/modelmirror-gate-q-venv-4764406c/Scripts/python.exe`，pytest 使用独立 `--basetemp C:/tmp/modelmirror-oct13-tests-4`。
- 前端 `vitest run --configLoader runner --maxWorkers=1 --fileParallelism=false`，五个文件 `models.refresh`、`models.oct13`、`ScaleDownPage`、`models.oct12`、`AudioManagedParameterContract`：96 passed。
- Node 同步/价格/技能 JSON/签名测试：23 passed。
- `npm.cmd run build`：通过，保留现有大包提示。没有依赖或锁文件变更。
- 未运行全量后端、真实 Provider 调用及浏览器人工验收；没有启动/替换旧预览、共享栈部署、提交、推送或 PR。后续 PR 前应刷新基线并按仓库要求完成帮助中心及发布检查。
- 回退：撤销本轮限定差异；不涉及数据库迁移、凭据或持久卷，既有默认模型和共享运行环境不变。

## 2026-10-14 发布前收口

- 用户授权独立预览、帮助验收、漂移修正及提交 PR。独立预览和教程重放证据见 `docs/help-center/openrouter-oct13-preview.md`；本节替代前述“未启动预览”的阶段状态。没有真实付费调用，未运行全量后端，未部署共享栈。
- 发布窗口 `C:/tmp/openrouter-oct14-release-window/manifest.json`：UTC 2026-10-14 09:49:38.411–09:49:42.378（Phoenix 02:49）。此前窗口间新增的 4 项元数据/3 项市场变化已先收口；恢复后新窗口又发现 10 项元数据、3 项结构市场变化和 234 项波动市场观察，没有新增或 Batch 漏项。
- 本次 10 项：`~deepseek/deepseek-flash-latest`、`~deepseek/deepseek-pro-latest`、`~moonshotai/kimi-latest`、`~z-ai/glm-flash-latest`、`~z-ai/glm-latest`、`deepseek/deepseek-v4.1-flash`、`deepseek/deepseek-v4-pro-0813`、`google/gemma-4-26b-a4b-it`、`moonshotai/kimi-k3`、`z-ai/glm-5.3-flash`。结构市场：`~moonshotai/kimi-latest`、`moonshotai/kimi-k3`、`z-ai/glm-5.2`。
- 已按冻结源分别执行元数据与市场同步并复核，最终 `C:/tmp/openrouter-oct14-release-clean/summary.json` 为 clean，所有比较差异归零。数量仍为 713/586/102/25，81 个不计数 Batch，688 为保留入口口径而非真实可调用证明。
- 发布窗口最终重跑：后端 193 passed（独立 basetemp `C:/tmp/modelmirror-oct14-pr-tests`）；前端八个文件合计 119 passed（含帮助与 Batch 23 项）；同步脚本 23 passed；前端构建通过（现有大包提示）。动态费率快照断言随各冻结源调整，并在发布前重跑；不删除能力或门禁测试。
