# 2026-10-20 模型与漂移更新

## 发布补充（覆盖下文历史待授权状态）

- 用户明确授权提交 PR，并批准本轮独立预览重放与截图门禁豁免。未执行预览重放或归档新截图，不代表真实供应商调用已验收。
- 发布前四来源窗口 UTC 2026-10-20 14:03:45–14:03:50，manifest：`C:/tmp/openrouter-oct20-pr-window/manifest.json`。新增一项 `moonshotai/kimi-k2.6` 市场结构化变化及 29 项易变观测变化，已同步市场快照。
- 最终 `C:/tmp/openrouter-oct20-pr-final/summary.json` 为完整 clean。随后模型、Batch、新契约与帮助四组专项 102 passed；基线仍为 `ba2e5022`，无分叉。
- 不执行合并、部署、凭据变更或真实模型调用。本次豁免仅适用于本轮，不修改仓库长期规则。

- 基线 `origin/main@ba2e5022`（已含 PR #420、#421），隔离分支 `codex/openrouter-oct20-minimax`。只改目录、计价覆盖、测试及帮助；不改默认模型、凭据、部署或公共 API。
- 四来源冻结窗口 UTC 2026-10-20 13:54:49–13:54:53；带哈希 manifest 为 `C:/tmp/openrouter-oct20-window/manifest.json`。普通目录 683 条 = 603 非 Batch + 80 Batch，图片 61、视频 30；市场非 Batch ID 集合精确一致。
- 补齐 `minimax/minimax-m3.1-flash:free`、`inception/mercury-3`、`unbiased/pareto-26.10`，全部通过既有机制散布到第六排后。MiniMax 为文字/图片/视频理解，免费、100 万上下文、必需推理；Mercury 为文字，Pareto 为图文理解。复用现有聊天/视频理解入口，不引入第二套协议，不宣称真实供应商可用。
- 修改前独立目录报告有 49 项元数据变化，缺少上述三项；移除上游已下架 `google/gemini-2.5-flash:batch` 和 `google/gemini-2.5-pro:batch` 服务档位，基础模型仍保留。缺失 `deepseek/deepseek-r1`、`microsoft/wizardlm-2-8x22b`、`sao10k/l3.3-euryale-70b` 改为可能不可用而非删除。
- 上游更新 Gemini 2.5 系列到期元数据，按新目录保留，未以旧到期日推定删除。同步别名上下文/价格/参数、结构化市场分类及时间窗；旧 V4.1 Flash 与 V4 Pro 0813 当前不再有时间窗，1017 保留原有时间窗。
- ElevenLabs 首发优惠结束：九款 TTS 保留字符计价，显式价格覆盖同步到 $40/$80 每百万字符；两款 Scribe 从约 $0.11 更新为 $0.22/音频小时。更新过时优惠说明，未改变音色或上传契约。
- 初始审计因跨日期后的旧 readiness 生命周期计数失败，证据保留于 `C:/tmp/openrouter-oct20-before/`，不能称为完整基线审计。修正后 `C:/tmp/openrouter-oct20-after/summary.json` 完整 clean，缺项、元数据、Batch、生命周期及结构化分类漂移为零。
- 最终 737 快照 = 603 live + 106 uncertain + 28 expired；709 为站内适配分母，不是真实可调用证据；80 Batch 不计快照。
- 超过五文件是因为模型数据、市场分类、计数回执、精确断言及过期价格帮助必须一致；无无关依赖升级。锁文件安装报告既有 17 项依赖风险，本轮未自动升级依赖。
- 回退为撤销本轮局部差异，无数据库迁移。未提交、推送、PR、部署或付费调用；本轮未执行界面人工验收，上一轮截图豁免不沿用。

## 验证结果

- 前端 `vitest run`：models.refresh、models.oct15、models.oct16、models.oct20、openrouterBatch 五组 119 passed；帮助内容 18 passed。旧精确价格、分类和下架 Batch 断言按同一冻结窗口更新，原失败套件已重跑通过。
- Python 专项：test_multimodal_chat_foundation、test_multimodal_chat_video、test_multimodal_video_analysis、test_elevenlabs_contracts、test_openrouter_batch 共 79 passed，4 个既有 FastAPI 生命周期弃用警告。
- Node 更新、价格和审计工具四组共 24 passed。
- `npm.cmd run build` 通过，3208 模块，保留既有大 chunk 警告。未运行全量后端。
