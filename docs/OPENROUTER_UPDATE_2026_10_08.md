# OpenRouter 更新与漂移收口：2026-10-08

## 范围与证据

- 基底 `origin/main`：`70a01640`；分支 `codex/openrouter-oct08-audio`，独立工作树，未修改共享栈。
- 完整四源窗口：2026-10-08 07:20:05–07:20:42 UTC。
- 外部来源与 SHA-256 清单：`C:/tmp/openrouter-oct08-retry/manifest.json`。
- 前审计：`C:/tmp/openrouter-oct08-baseline-audit/summary.json`；最终审计：`C:/tmp/openrouter-oct08-closeout-audit/summary.json`。
- 首次抓取市场源中断，未作为完整证据使用；以上重试四源完整且 ID 集合一致。

## 变更

新增 14 个目录实体：ElevenLabs 九款 TTS（Flash v2/v2.5、Turbo v2/v2.5、Multilingual v2、v3、v3 Conversational、v4、v4 Turbo），Scribe v2/Medical，Perplexity Decider v1.1 27B、Solar Decide Flash、Claude Haiku 5.5。沿用第六行后稳定散列位置，不改旗舰或默认模型。

- TTS 复用 `/audio/speech`、21 个预设音色、MP3 二进制输出和本地 4000 字符限制。支持语速的三款只允许 0.7–1.2，其余不发送 speed。按 Unicode 码点计费并展示估算，不支持克隆音色或多角色。
- STT 复用 `/audio/transcriptions` JSON 原始 Base64、25 MiB 限制、文本结果；按秒目录价转换每小时费用并登记保护规则。未开放说话人分离、时间戳或医疗合规承诺。
- ElevenLabs 目录价已含启动优惠，不重复折扣；官方优惠截止 2026-10-19 15:00 UTC。静态费率须随后续目录刷新，不代表未来价格承诺。
- Decisions 使用专用文本状态与 choice/score 契约，最多 50 个问题，不冒充普通聊天。
- 增加 Haiku 5.5 Batch 档位，仅对话页设置入口，不另加卡片或快照计数。
- 修复 29 个既有模型的结构化漂移（22 项价格、7 项参数、2 项到期日期、1 项上下文，同模型可多字段）；同步市场结构与动态指标。旧 `perplexity/pplx-decider-v1-27b` 保留入口并标记可能不可用。
- 保留 UTC 星期与全天价格条件：修复 DeepSeek V4 Pro 0813 周末全天优惠被解析器遗漏的问题，新增解析与选择回归测试。

官方契约参考：[TTS](https://openrouter.ai/docs/guides/overview/multimodal/tts)、[音频](https://openrouter.ai/docs/guides/overview/multimodal/audio)、[ElevenLabs 发布说明](https://openrouter.ai/blog/announcements/elevenlabs-on-openrouter/)。

## 最终口径

上游 665 条 = 587 非 Batch + 78 Batch。市场 587 个基础 ID 与上游非 Batch 精确一致。图片源 59、视频源 30。上游仍列有两条已过期记录，故本地 live 不等于非 Batch 总数。

本地快照 694 = live 585 + uncertain 95 + expired 14；站内分母 680。78 个 Batch 不计入快照。此计数不是配置可调用率或真实调用证据。

冻结窗口内缺失、陈旧/孤立 Batch、元数据、生命周期、任务、计价与市场结构漂移均为零；动态市场观察也已同步。历史保留记录和显式专用契约例外不是漏项。

## 验证与边界

- 前端快照、时间价格、Batch 和音频界面六文件：108 passed。
- 帮助内容结构：15 passed。
- 后端 TTS/STT/Decisions 87 passed；新增 ElevenLabs 契约与拒绝非法语速 16 passed；Batch 18 passed。
- 更新器/计费保护/证据集合/签名四组 Node 测试：22 passed。
- 前端生产构建通过，有既有大包体积警告；后端测试有 FastAPI 生命周期弃用警告。
- 未执行全量后端、真实模型付费调用或部署。语音契约仍为 manual_required，配置与资格认证门禁不变。
- 用户授权发布后已补充独立前端可见预览及帮助截图；详细边界见 `docs/help-center/openrouter-oct08-preview.md`。
- PR 前第二窗口：07:50:30–07:50:46 UTC，清单位于 `C:/tmp/openrouter-oct08-pr-window/manifest.json`。发现 4 项供应商变化及 96 项动态指标变化，已同步；`C:/tmp/openrouter-oct08-pr-clean/summary.json` 再审计 clean，模型与 Batch 数量不变。

回退：仅撤回本轮目录、适配、解析与帮助变更；不删历史模型、数据、配置或认证记录。
