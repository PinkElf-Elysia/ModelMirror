# OpenRouter 2026-10-14 更新

## 开工契约

- 基线 `origin/main@ec79e120`，已含 #417；独立工作树 `C:/tmp/modelmirror-openrouter-oct14`，分支 `codex/openrouter-oct14-assembly-jev`，开工干净。
- 范围：Universal-3.6 Pro、Jev 1.14、同窗口漏项/别名、Batch、元数据、生命周期及市场侧栏漂移。按目录、专用契约、测试与文档分批；跨五个文件是保护刷新、运行白名单和前后端契约一致所必需。
- 不改默认模型、共享栈、凭据、依赖声明或持久化；不执行真实模型调用、提交、推送或 PR。本轮回退为撤销限定差异。
- 验收：四源双审计和 readiness、目录测试、STT/Decisions 专项与聊天回归、脚本测试、前端构建。
- 四源窗口：`C:/tmp/openrouter-oct14-assembly-window/manifest.json`，UTC 2026-10-15 02:22:22.698–02:22:27.004（Phoenix 2026-10-14 19:22）。669 条 = 588 非 Batch + 81 Batch；图片 57、视频 30，市场非 Batch ID 集完全一致。
- 初始审计因新工作树缺依赖失败，安装锁文件依赖后重跑。原基线 readiness 因已有到期时间经过而出现 live 586/585 不一致，保留 `before2` 的两份原始比较结果；这是本轮需修正的生命周期收据，不是无漂移。

## 契约证据

- https://openrouter.ai/assemblyai/universal-3-6-pro 及 endpoints：Sync 转写、最长 120 秒、16-bit WAV，基础每秒 $0.0000833333（约 $0.30/小时），提示转写另价。只开放现有基础转录，不新增提示、关键词、说话人或时间戳界面。
- https://openrouter.ai/typesafe/jev-1.14 及 endpoints：Decisions，$0.042/M 输入、零输出价格，目录 262144 上下文并标记图片输入。
- https://docs.typesafe.ai/models 当前文档仍声明文字输入，原生默认上下文与显式长上下文开关有区别。目录保留原始能力，工作台只接受文字；不据目录伪造图片或长上下文验证。
- https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request：复用 state/questions 的 noul、choice、score 契约。
- 补齐目录发现的 `~perplexity/pplx-decider-latest`；保留别名请求 ID，不写死目标。旧别名保留生命周期提示。

## 收口结果

- 本轮新增 3 个目录实体，新增 Batch 为 0；修正 `openai/gpt-5.6-sol:batch`、`openai/gpt-5.6-sol-pro:batch` 的基础与长上下文分段价格。两款 AssemblyAI 的基础价格均为约 $0.30/小时，并注册秒转小时审计保护，禁止当作 Token 价格。
- 24 项普通元数据：`inclusionai/ling-3.1-flash`、`~deepseek/deepseek-flash-latest`、`deepseek/deepseek-v4.1-flash`、`~z-ai/glm-flash-latest`、`z-ai/glm-5.3-flash`、`~z-ai/glm-latest`、`qwen/qwen3.8-27b`、`black-forest-labs/flux-3-video`、`moonshotai/kimi-k3`、`openai/gpt-5.6-sol-pro`、`openai/gpt-5.6-sol`、`z-ai/glm-5.2`、`~moonshotai/kimi-latest`、`qwen/qwen3.6-35b-a3b`、`z-ai/glm-5.1`、`google/gemma-4-26b-a4b-it`、`google/gemma-4-31b-it`、`minimax/minimax-m2.5`、`z-ai/glm-5`、`openai/gpt-oss-120b`、`qwen/qwen3-coder-30b-a3b-instruct`、`mistralai/mistral-small-3.2-24b-instruct`、`anthropic/claude-sonnet-4`、`google/gemma-3-4b-it`。专用保护项另人工修正 AssemblyAI 3.5 价格与 Jev latest 的上下文/输入声明。
- 市场侧栏修正 38 项结构记录并同步 287 项波动观察；不将榜单用作侧栏分类。完整前后证据分别位于 `C:/tmp/openrouter-oct14-assembly-before2/` 与 `C:/tmp/openrouter-oct14-assembly-final/`。
- `~perplexity/pplx-decider-27b-latest` 从源消失，保留可能不可用入口。`anthropic/claude-sonnet-4` 和 `unbiased/pareto-26.10-preview` 的明确到期时间已到，不删除历史记录。
- 最终全量审计 `clean`：716 本地 = 586 有效在售 + 103 可能不可用 + 27 过期保留；689 为保留入口分母，不是真实可调用证明。上游 588 个非 Batch 中有 2 个仍列出但明确过期；81 Batch 不计入模型总数。
- 新卡片沿用两个展示位加三列网格的第六完整行后分散规则（最小索引 `2 + 6 * 3`）；不修改旗舰或默认模型。新增测试最初错误地以 24 为最小索引，已按现有布局规则校正。
- 音频校验覆盖旧/新 AssemblyAI，拒绝非 WAV、非 16-bit PCM、空帧、截断与超过 120 秒的音频；legacy/managed 派发前和 managed 确切模型解析后均检查。其他转录模型不受影响。

## 验证

- `npx.cmd vitest run --configLoader runner --maxWorkers=1 --fileParallelism=false src/data/models.refresh.test.ts src/data/models.oct14.test.ts src/data/models.oct13.test.ts src/data/openrouterBatch.test.ts src/components/OpenRouterBatchWorkspace.test.tsx`：87 passed。
- 同参数运行 `src/content/help-center/helpContent.test.ts`：17 passed；前端合计 104。静态目录测试时间固定到证据窗口，保留精确价格、分类和生命周期断言。
- `python -m pytest server/tests/test_oct14_transcription.py server/tests/test_multimodal_stt.py server/tests/test_openrouter_decisions.py server/tests/test_multimodal_chat_foundation.py server/tests/test_openrouter_batch.py --basetemp C:/tmp/modelmirror-oct14-assembly-tests4 -q`：95 passed，4 条既有 FastAPI 生命周期弃用警告；解释器 `C:/tmp/modelmirror-gate-q-venv-4764406c/Scripts/python.exe`。新增测试夹具初始化曾失败，修正后同套重跑通过。
- `node --test scripts/update-openrouter-models.test.mjs scripts/openrouter-pricing-contracts.test.mjs .agents/skills/openrouter-update/scripts/inspect-json.test.mjs .agents/skills/openrouter-update/scripts/stable-signature.test.mjs`：24 passed。新增价格测试最初缺市场夹具，补齐后原命令重跑通过。
- `npm.cmd run build`：通过，保留现有大包警告。`git diff --check`：通过。没有依赖声明或锁文件变化。
- 初轮实现结束时尚未提交、推送或创建 PR；以下为用户授权发布后的收口证据。

## 2026-10-15 发布收口

- 刷新基线仍为 `ec79e120`，无提交分叉。用户已授权提交 PR；不合并、不部署、不执行真实模型调用。
- 二次四源窗口 UTC 2026-10-15 07:12:58.622–07:13:06.076：`C:/tmp/openrouter-oct15-assembly-pr-window/manifest.json`；模型 SHA-256 `52b35d89e4af9f9519a7d7ac6a67600a453b4efeba0312fee1f6a0dead259b43`。
- 实施期间新增 10 项结构元数据漂移：`~deepseek/deepseek-flash-latest`、`~google/gemini-pro-latest`、`~moonshotai/kimi-latest`、`~z-ai/glm-latest`、`cloudflare/clef-flash`、`deepseek/deepseek-v4.1-flash`、`google/gemma-4-26b-a4b-it`、`meta-llama/llama-3.2-3b-instruct`、`moonshotai/kimi-k3`、`stepfun/step-5-preview`；侧栏结构 2 项、波动观察 221 项。没有新增模型或 Batch 漏项，已分阶段同步。
- 最终 `C:/tmp/openrouter-oct15-assembly-pr-final/summary.json` 为 `clean`，数量口径不变；按冻结价格更新断言后原前端测试集重跑 104 passed，最终构建通过。
- 同基线帮助重放已完成，见 [记录](help-center/OPENROUTER_2026_10_15_PREVIEW.md)。未运行全量后端或真实供应商测试，不宣称用户人工验收。
- 回退为撤销本轮限定提交，不删除历史模型、凭据或持久化数据。
