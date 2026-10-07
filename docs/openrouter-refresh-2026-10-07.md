# OpenRouter 模型更新与漂移核对（2026-10-07）

基线：`origin/main` `2c82d8a53c52fdec507ec5f81ffec5ebc577c750`。
分支：`codex/openrouter-oct07-refresh`。未部署共享栈，未执行真实付费调用。

## 来源与范围

四源冻结窗口为 2026-10-07 05:12:01–05:12:10（UTC-07:00）：
全模态 models、专用 images/models、videos/models，以及模型页侧栏 market。
证据与 SHA-256 清单位于 `C:/tmp/openrouter-oct07-before-019fff67/manifest.json`。
最终审计位于 `C:/tmp/openrouter-oct07-final-audit/summary.json`，状态 `clean`。

新增用户指定的 GPT-6 Luna Decisions、Grok Imagine Video 1.5 Lite、Nano Banana 2.1、
Mistral Large 4、Hy Image 3.5 Preview；同时补齐目录缺失的 Perplexity Decider V1 27B、
Cloudflare Clef、Clef Flash。8 个条目均进入第六排之后的刷新区。

## 契约与验证边界

- Decisions 使用 `/api/alpha/decisions`，不进入普通聊天。Luna 允许最多 200 个问题；其他模型维持站内 50 个问题限制。当前工作台仅开放文字状态；图片状态尚未适配。Cloudflare 目录为 65K，但上游说明当前端点可能截断到约 2K Token。
- Grok Lite 复用异步视频提交和轮询：1–15 秒、480p/720p/1080p、首帧、7 种画幅；费用分别为 $0.02/$0.03/$0.14 每秒，首帧 $0.01。保持人工验证门禁，不写入已实测模型名单。
- Nano Banana 2.1 复用专用 Images API：1K/2K/4K、14 种画幅、最多 14 张参考图、一次 1 张输出。专用端点图片输出为 $30/M Token。
- Hy Image 3.5 Preview：1K/1.5K/2K/4K、13 种画幅、最多 20 张参考图、一次 1 张输出、seed/size；图片输出 $1.60/M Token。
- 两个图片端点均非流式，图片 Token 数无法在生成前准确预测，不伪造固定每图费用。图片、视频与决策模型仍需真实调用验收。
- Mistral Large 4 复用现有文字/视觉聊天，524288 上下文，保留工具与推理参数。

## 漂移与计数

已处理 29 条普通模型结构化元数据漂移、3 条可能不可用状态漂移和模型页侧栏元数据。
`kwaipilot/kat-coder-pro-v2.5`、`openai/sora-2-pro`、`qwen/qwen3.8-27b:free` 保留入口及警示。
Space Bunny Alpha 的既有到期时间已生效，修正随时间失效的计数回执。

最终 680 个保留快照 = 574 在架 + 94 可能不可用 + 12 过期；站内未过期口径为 668。
77 个 Batch 档位无新增或漂移，继续不计入模型数量。574 个非 Batch 来源 ID 与市场 ID 完全一致；
缺失模型、元数据、生命周期、Batch、操作能力、侧栏分类可执行漂移均为零。
该结论绑定上述时间窗，不表示 668 个模型均完成真实调用验收。

## 验证

- 前端快照、图片与视频费用专项：97 passed。
- Decisions、图片目录/生成、视频目录/任务后端专项：76 passed；均使用模拟传输。
- 生成器与价格覆盖保护：15 passed。
- 四源最终审计：modalities/classifications/readiness 均退出 0，状态 clean。
- 后端测试使用已有 `modelmirror-gate-q-venv-4764406c`，专属 `--basetemp` 避免系统临时目录权限问题。
- 首次前端构建因既有 card-replica 子模块未安装依赖失败，按 CI 的锁文件安装后重跑通过，保留大 chunk 警告；未修改依赖清单或锁文件。
- `git diff --check` 与 `server/main.py` 语法检查通过。
- 后续发布阶段完成独立前端入口检查，详见 `help-center/openrouter-oct07-preview.md`；未进行真实提供商调用或部署。

## 发布前再次复核

重新冻结四源窗口：2026-10-07T05:45:17.7210887-07:00 至 05:45:27.3663306-07:00。574 个非 Batch 与市场 ID 仍完全一致，无模型或 Batch 漏项。此时上游再次发生变化：`~deepseek/deepseek-pro-latest` 与 `~z-ai/glm-flash-latest` 价格漂移，以及侧栏 providers/regions 各 2 项和 129 条工具成功率观测变化。发布前审计状态为 drift，而非 clean；新增变化保留为后续增量审计项，没有将旧时间窗结论套用于新窗口。证据位于本地 `C:/tmp/openrouter-oct07-prepr-audit/summary.json`。

回退：按本分支审阅后的文件差异回退目录、分类快照及 Decisions 白名单/限额；无数据迁移或运行环境变更。
