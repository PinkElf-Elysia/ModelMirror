# OpenRouter 模型快照补充与未适配审计（2026-08-16）

## 本轮补充

- `dots-studio/dots-3-note-preview:free`
  - 512,000 token 上下文。
  - 文本、图片输入，文本输出；进入现有聊天与图片理解链路。
  - 当前目录价格为免费，提供端点为 AtlasCloud。
- `qwen/qwen3.8-27b`
  - 262,144 token 上下文。
  - 文本、图片、视频输入，文本输出；进入现有聊天、图片理解和视频理解链路。
  - 当前基础价格为输入 0.45 美元、输出 3.20 美元 / 百万 token。

两条记录均使用 OpenRouter 的 `/api/v1/chat/completions` 兼容契约，不需要新增图片、音频或视频生成端点。卡片沿用确定性散列，放在前六个完整展示行之后。

## UTC 分时价格

本轮补齐 `deepseek/deepseek-v4-pro-0813` 与 `deepseek/deepseek-v4-pro` 的完整 UTC 分时价格表，共 2 个模型、8 条时间窗规则：

- `utc_start` 为包含边界，`utc_end` 为不包含边界，均按 HHMM UTC 解释。
- 结束时间不晚于开始时间时跨越午夜，例如 `1000 → 0100` 显示为“10:00–次日 01:00”。
- 顶层输入/输出价格保留抓取时刻生效的档位；界面根据完整窗口表和访问时的 UTC 时间动态选择当前档位，不把快照顶层值误当成恒定基础价。
- 模型卡片以可展开区块展示完整时间表；对话页设置直接展示各时段及人民币每百万 token 估算。
- 生成与审计脚本分别保留 token 阈值价格和 UTC 时间窗价格，避免两种 `pricing.overrides` 条件互相覆盖。

## 快照口径

- 本地模型快照：545。
- 明确可用且未过期：484。
- 可能不可用但保留入口：55。
- 明确过期：6。
- Batch 服务档位：63，不计入模型快照数量。
- 站内保留的 OpenRouter 入口：539（明确可用与可能不可用之和）。

`deepseek/deepseek-v3.1-terminus` 仍出现在上游目录中，但其结构化 `expiration_date` 已到期，因此本地按既定规则转为明确过期并停止调用。

## 全量未适配审计

本轮以同一次下载的实时数据进行可复现对照：

完整结构化结果保存在 `docs/openrouter-classification-audit-2026-08-16.json`。

- 通用目录：549 条，其中 63 条 Batch、486 条非 Batch、475 个 canonical 实体。
- 图片生成目录：43 条，本地缺失 0。
- 视频生成目录：23 条，本地缺失 0。
- Embeddings 目录：32 条，本地缺失 0。
- 已纳入本地的 485 条当前上游记录：操作分类错配 0、任务能力错配 0、侧边栏市场快照错配 0。

仍有以下未适配项，本轮只记录、不擅自扩展模型快照范围：

1. `z-ai/glm-5.2:free`：当前通用目录中存在，但本地没有独立快照；它是文本到文本的免费推理模型。

除该未纳入模型外，本轮结构化审计中的已收录模型元数据错配、Batch 元数据错配、专用图片/视频目录缺失和生命周期状态错配均为 0。UTC 时间窗价格的上游/本地模型数均为 2，规则数均为 8，错配为 0。

此外，`openrouter/auto` 与 `openrouter/auto-beta` 在通用目录继承了图片输出声明，但不在专用 Images API 目录中；本地继续把它们作为路由器而不是独立图片生成模型，这不是本轮新增缺口。

## 数据来源与验证边界

- `https://openrouter.ai/api/v1/models?output_modalities=all&sort=newest&offset=0&limit=1000`
- `https://openrouter.ai/api/v1/images/models`
- `https://openrouter.ai/api/v1/videos/models`
- `https://openrouter.ai/api/v1/embeddings/models?offset=0&limit=1000`
- `https://openrouter.ai/api/frontend/v1/models/find?active=true&fmt=cards`
- 两个新增模型的 `/api/v1/models/{id}/endpoints` 记录。

本轮验证目录与端点契约，没有发送真实计费推理请求；“端点存在”不等于完成真实供应商行为验收。
