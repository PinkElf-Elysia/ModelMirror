# OpenRouter 2026-10-12 更新

## 任务范围

- 独立工作树 `C:/tmp/modelmirror-openrouter-oct12`，分支 `codex/openrouter-oct12-refresh`，最新主线基底 `262f7caf`（#412），开工时干净。
- 更新三款指定模型及同窗口目录漂移，不修改默认模型、旗舰位、API 协议、部署、凭据或数据库。
- 分批修改目录、决策与音频注册、测试和审计文档。整轮超过五个文件是为了保持目录、执行白名单、能力注册及回归证据一致；没有扩大到新执行框架。
- 验收：目录双审计/readiness clean，模型与 Batch 前端测试，Decisions/多模态聊天后端测试，文件聊天/视频目录回归，Node 更新器测试，前端构建。

## 冻结窗口与审计

- 四源 manifest：`C:/tmp/openrouter-oct12-window/manifest.json`，Phoenix 2026-10-12 20:49:47–20:49:50（UTC 2026-10-13 03:49:47–03:49:50）。hash 见 manifest。
- 最初 `openrouter-oct12-before` 因新工作树缺少 Vite 依赖而不完整。按锁文件 npm ci 后，`openrouter-oct12-before-ready/summary.json` 为有效基线。
- 漏项恰为指定三款。23 个元数据记录漂移：22 价格、1 参数（Tencent Hy4）。完整 ID 列表在外部 baseline summary。
- 市场 303 个记录变化：63 个涉及结构、298 个涉及易变观测（58 个重叠）；结构字段涉及 regions、providers、categories、discounted、zero_data_retention。
- 分阶段证据 `openrouter-oct12-added`、`openrouter-oct12-metadata`、`openrouter-oct12-final`。最终同窗口 clean；Batch、生命周期无新增漂移，无需修改。
- 源 662 = 582 非 Batch + 80 Batch；市场 582 唯一基础 ID 与非 Batch 集合一致。
- 本地 703 = 582 live + 96 uncertain + 25 expired，适配分母 678；80 Batch 档位不计数。适配分母不是供应商真实调用成功数。

## 模型契约

- [Muse Wonder](https://openrouter.ai/meta/muse-wonder)：文字、图片、音频、视频、PDF 输入，文本输出；1,048,576 上下文，$4/$20 每百万输入/输出 Token。复用聊天、PDF 和视频理解路径，注册音频 input_audio/raw Base64 输入，本站保守限定 WAV。缓存读取 $0.20/M、原生搜索 $2.50/千次在备注说明，不额外开启原生搜索。不是 Contributor 档位，不复制其数据训练告知或 Spark 音频质量告警。
- [Drex v2.0](https://openrouter.ai/nace-ai/drex-v2.0)：131,072 上下文，输入 $0.06/M，输出 $0；复用专用 Decisions API，不走普通聊天。保留 Drex v1.5，不自动替换。
- [Perplexity Decider Latest](https://openrouter.ai/~perplexity/pplx-decider-27b-latest)：当前 alias_target 为 pplx-decider-v1.1-27b，保留浮动别名 ID 发送请求；262,144 上下文，输入 $0.02/M、输出 $0。别名 endpoints 当前为空，不据此宣称配置可调用。
- 两个决策入口只接受文字 state、最多 50 个 noul/choice/score 问题；沿用现有校验和费用组件。Perplexity 上游图片能力不等于本站已支持，备注显式标明未适配。
- 核对了各模型页面、canonical endpoints、[Decisions 文档](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request)和[音频输入文档](https://openrouter.ai/docs/guides/overview/multimodal/audio)。仅公开 GET，无模型调用。
- 三卡按现有第六排后稳定分散机制放置。默认模型及旗舰位不变。

## 验证与边界

- Decisions 和多模态聊天：52 passed，4 条既有 FastAPI 弃用警告。
- 文件聊天、视频目录回归：49 passed；前端 models.refresh、models.oct10、models.oct12、openrouterBatch、OpenRouterBatchWorkspace：87 passed。
- Node 更新器、价格契约、inspect-json、stable-signature：22 passed。
- 前端生产构建通过，保留既有大包警告。
- 目录断言逐项按冻结价格和市场源更新；保留类型、能力与排序断言，不跳过测试。首窗口 V4.1 Flash 有六段 UTC 时窗价格；PR 前窗口撤下这些时窗，现已按源同步为空并复测 87/87 通过。
- PR 前冻结 `C:/tmp/openrouter-oct12-pr-window/manifest.json`：Phoenix 2026-10-12 23:50:51–23:50:55。新增五项元数据漂移（四价格、一上下文）及 V4 Flash 的 providers 结构漂移，已告知用户并同步；195 项易变工具成功率观测一并刷新。同窗口 `openrouter-oct12-pr-clean/summary.json` clean，计数不变。
- 2026-10-13 用户在 5301 独立预览人工验收后明确“确认正常”。内置浏览器补查 Wonder 附件菜单与两个决策页；帮助新增 `review-wonder-decisions` 与同基线截图，保持旧文章及其基线不变。按帮助重新打开空白页面复查标题、门禁及价格通过。原 Windows 浏览器控制无法识别 URL 的失败不算验收证据。
- 帮助截图检查通过；帮助加模型专项 95 passed；此前模型/Batch 专项 87 passed（其中模型测试重叠，不相加计数）。最终前端构建通过，完整同窗口目录审计 `openrouter-oct12-publish-clean` exit 0。
- 未执行付费调用、配置复制或共享栈操作。预览没有可调用后端；音视频、文件实际解析、决策结果、供应商连通性和真实计费未验证。
- 回退：仅撤销本轮目录、注册、白名单及测试文档差异；没有持久化迁移。
