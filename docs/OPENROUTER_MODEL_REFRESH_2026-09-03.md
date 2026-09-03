# Gemini 3.8 Flash 与 Muse Spark 1.3 定向适配

## 范围与基线

- 基线：`origin/main@07cbd6d1`（已合并 PR #350）；独立工作树 `codex/openrouter-gemini38-flash-20260902`，开工时无未提交修改。
- 仅增加三个模型与 Gemini Batch 档位；旧模型、旧 Batch、生命周期判断、默认模型与旗舰展位均不调整，不宣称已完成全目录漂移复核。
- 共 13 个文件：静态快照、市场元数据、音频契约、前后端回归、readiness、本文、帮助正文与索引及其基线回归、预览证据及两张截图。目录到入口及证据口径必须同步，新增费用与数据边界还需在同一 PR 完成帮助门禁，故不能安全只补卡片。
- 不改变公共 API、持久化结构、依赖、凭据或共享栈部署环境；不调用付费模型。用户于 2026-09-03 追加授权完成后提交 PR。

## 核对结果

数据于 2026-09-03 从公开模型 API、端点 API、模型市场与官方说明取得。以下为美元／百万 Token：

| 模型 | 输入 | 输出 | 上下文 |
| --- | ---: | ---: | ---: |
| `google/gemini-3.8-flash` | 0.75 | 3.75 | 1,048,576 |
| `google/gemini-3.8-flash:batch` | 0.375 | 1.875 | 1,048,576 |
| `meta/muse-spark-1.3` | 1.25 | 4.25 | 1,048,576 |
| `meta/muse-spark-1.3-contributor` | 0.10 | 0.20 | 1,048,576 |

三个实时模型均声明文本、图片、视频、文件及音频输入，输出为文本；它们不是媒体生成模型。复用现有 Chat、图片理解、文件处理和视频理解入口。Gemini 音频复用现有常用格式档案；Muse 音频复用已存在的 WAV 输入协议。音频登记为 `ready + contract_verified`，仍受现有音频开关和连接条件控制，不冒充真实调用已验证。

两款 Muse 的官方页面均提示音频理解未完整支持，带音频请求的回答质量可能下降；模型卡与音频契约保留中文提示。Contributor 的输入、输出可能用于改进 Meta 产品，模型卡明确提示不要提交敏感材料。市场快照不把两款 Muse 标为零数据保留。

Gemini Batch 仅作为基础模型的 `serving_variants`，只在对话页设置中提供入口；主页仅显示支持批处理，不重复计数或新增卡片。Batch 请求使用基础 ID，经异步 Batch API，限定文本，保留 24 小时完成窗口和 30 天数据保留提示。虽然上游 Batch 目录复制多模态架构，本地服务档位仍覆盖为纯文本。

三个新卡片按既有稳定散列规则放在前六排之后。更新后为 581 个计数快照（502 live、73 uncertain、6 expired），575 个非过期快照，另有 71 个不计数 Batch 档位；这些是目录与适配口径，不等于真实调用成功数量。

## 验证与回退

- 验收：新增 ID／价格／能力／市场字段一致；位置后置；Batch 不形成实体、不接受多模态；音频只输出文本并遵守现有开关。
- 前端专项：`npm.cmd test -- --run src/data/models.refresh.test.ts src/data/openrouterBatch.test.ts src/components/OpenRouterBatchWorkspace.test.tsx src/pages/ChatPage.layout.test.ts src/pages/ModelListPage.layout.test.tsx src/components/filters/FilterPanel.test.tsx`，通过，6 文件 / 70 用例。
- 前端全量：`npm.cmd run test:run`，通过，131 文件 / 914 用例，另有 1 条服务器头部测试。首次使用并行默认命令且与构建同时运行时，未改动的 Skill / Agent 测试出现 6 条超时；未调整断言或时限，按仓库单工作进程命令重跑全量通过。
- 前端生产构建：`npm.cmd run build`，通过；保留既有大 chunk 提醒。
- 帮助中心截图：`npm.cmd run verify:help-images`，通过；13 篇已注册文章的引用、真实 PNG、750–1000px 尺寸、≤250KB 体积、替代文本和基线目录均合规。截图经人工复核可读。
- 后端：`python -m pytest server/tests/test_multimodal_chat_foundation.py server/tests/test_multimodal_chat_audio.py server/tests/test_multimodal_chat_video.py server/tests/test_openrouter_batch.py server/tests/test_managed_openrouter_batch.py -q -p no:cacheprovider`，通过，59 用例 / 4 条既有 FastAPI 弃用提醒。在无网络临时容器中复制只读源码后执行；直接只读挂载首次因既有 WorldStore 初始化写目录而收集失败，第二次暴露一处遗漏的旧版本断言，修正后同组测试全过。全量后端未运行。
- 离线：`node scripts/check-multimodal-readiness.mjs`、定向源数据比对、`git diff --check`，通过。三个新增快照及一个 Batch 元数据差异为零；578 条旧快照、70 个旧 Batch 和 499 条旧市场记录原样保留。
- 浏览器：独立前端 5200 / 无密钥临时后端 8096，三个实时入口、Muse 提示、Gemini 设置到文本 Batch 工作区已核验；帮助与重放记录见 [实操证据](help-center/evidence/gemini38-muse13-07cbd6d1.md)。
- 真实 Provider 调用、计费、上传与 Batch 提交：未运行；不读取密钥，不改共享栈。
- 回退：撤销本轮三个快照、Batch 附件、音频档案、市场字段和相应计数；无数据库或数据恢复步骤。

来源：[模型目录](https://openrouter.ai/api/v1/models?output_modalities=all)、[Gemini 3.8 Flash](https://openrouter.ai/google/gemini-3.8-flash)、[Muse Spark 1.3](https://openrouter.ai/meta/muse-spark-1.3)、[Contributor](https://openrouter.ai/meta/muse-spark-1.3-contributor)、[音频输入](https://openrouter.ai/docs/guides/overview/multimodal/audio)、[Batch API](https://openrouter.ai/docs/batch-quickstart)。
