# 2026-10-15 OpenRouter 九模型更新

## 发布授权及门禁补充（覆盖下文历史暂停状态）

- 用户重新授权提交 PR，并明确批准本轮截图归档门禁豁免；仅豁免截图，不豁免测试、不宣称真实调用通过。
- 发布前基线仍为 `ae2493e2`，与刷新后的 `origin/main` 无分叉。四来源窗口为 UTC 2026-10-17 14:49:15–14:49:21，manifest：`C:/tmp/openrouter-oct17-publish-window/manifest.json`。
- `C:/tmp/openrouter-oct17-publish-audit/summary.json` 为 complete/clean：缺项、Batch、元数据及市场结构化漂移为零；59 个模型出现易变评分/工具观测变化，按既定策略单列、不作为发布阻塞，不声称所有观测值完全一致。
- 独立预览 5304 从本轮工作树重新启动；按教程重放市场搜索 Span-02、进入决策页、填入公开文字、确认按钮与估算变化、清空恢复禁用、搜索 K3 Edit 并确认参考图/未知费用/禁用提示、清空恢复列表。没有上传或提交请求。截图写入返回 EPERM，用户批准豁免归档。
- 前端 141、后端专项 94、脚本 24 项通过及构建成功的证据保持有效；最终文档变更另跑帮助内容测试。真实图片调用、完整后端套件和部署均未执行。

## 10 月 17 日扩展收尾（当前结论）

后文保留历史窗口结果；本节覆盖当前数量、验证状态与展示要求。

- 追加八项：`elorian/glimpse`、`qwen/qwen-image-2.1-turbo`、`qwen/qwen-image-2.1-pro`、`perplexity/pplx-decider-flash-v1.2`、`perplexity/pplx-decider-v1.2`、`xingchen-agi/xing4.0-29b-a4b:free`、`~perplexity/pplx-decider-flash-latest`、`reflection/beam-1.0-501b-a23b`。普通卡片继续在第六排后。
- 两款 Qwen 图片模型使用专用 Images 契约：1K/2K、13 种宽高比、1–6 张输出、0–4 张参考图及 seed；每张分别 $0.0184 / $0.046。不因通用 Token 价格为零标免费，不开放未声明参数。
- 两款 Decider 和 Flash 别名加入受限 Decisions 白名单，保持文字 state 和最多 50 个问题；不将目录中的图片能力宣称为已适配。
- HeyGen Voice 1 Turbo 字符单价由 $7.5 更新为 $4 / 百万字符，仍按字符计价。新增 `qwen/qwen3.7-plus:batch`，同步旧 V4.1 Flash Batch 元数据；Batch 不新增独立卡片、不计快照。
- `perplexity/pplx-decider-v1.1` 依据明确到期日 2026-10-17 保留为已过期记录，未删除。
- 再追加 `deepseek/deepseek-v4.1-flash-1017`：文字/图片理解、1,048,576 上下文、393,216 最大输出，以及六段 UTC 时间窗价格。替换首页原 V4.1 Flash 展位（索引 3，位于前六排）；原版移到第六排后。仅调整首页，工作流和 Agent 等默认模型保持不变；上游没有新版 Batch，不推断添加。

### 窗口与最终计数

- 八项扩展窗口 manifest：`C:/tmp/openrouter-oct16-expanded-window/manifest.json`，UTC `2026-10-17T04:01:10.0705885Z`–`04:01:27.8453757Z`。修改前缺少 8 项、21 项元数据漂移、1 项 Batch 缺失、1 项 Batch 元数据漂移、41 项市场结构化漂移；已分阶段修正。
- 当前权威窗口 manifest（含四来源哈希）：`C:/tmp/openrouter-oct17-flash1017-window/manifest.json`，UTC `2026-10-17T14:37:54.3720562Z`–`14:38:01.2228199Z`。本窗口新增 1017、移除上游已过期 Decider V1.1，非 Batch 总数不变。
- 本窗口修改前 8 项元数据漂移：`~deepseek/deepseek-flash-latest`、`~deepseek/deepseek-pro-latest`、`~deepseek/deepseek-v4-flash-latest`、`~moonshotai/kimi-latest`、`~z-ai/glm-flash-latest`、`~z-ai/glm-latest`、`deepseek/deepseek-v4-flash-0731`、`deepseek/deepseek-v4.1-flash`；另有 15 项市场结构化漂移，均已修正。
- 最终审计 `C:/tmp/openrouter-oct17-flash1017-final/summary.json` 为完整 `clean`，三个子审计退出均为 0。缺项、元数据、Batch、生命周期、操作能力、价格口径及市场结构化/易变观测差异均为 0；两个 auto 条目缺少专用图片目录的既有提示不等于新增可用能力。
- 上游 685 条 = **603 非 Batch + 82 Batch**；专用图片 61、视频 30，市场 603 个唯一基础 ID 与非 Batch 集合精确一致。
- 本地 **734 = 603 live + 103 uncertain + 28 expired**；站内适配分母 **706**，不是实测可调用数量。82 个 Batch 档位不计入快照。

### 最终验证与剩余边界

- 前端六组专项（模型、排序、Batch、图片费用、帮助中心）：**141 passed**。
- 后端图片契约、多模态图片、Decisions、多模态聊天和 Batch：**94 passed**，4 个既有生命周期弃用警告。
- 更新脚本与价格/审计工具：**24 passed**。
- `npm.cmd run build` 成功，3208 个模块；保留现有大 chunk 警告。旧价格精确断言逐项按冻结来源修正，未放宽校验。
- 未运行全量后端套件、付费 Provider 请求或完整图片人工验收；截图归档和帮助教程完整重放仍未完成。以上结果仅证明目录与契约适配、自动化验证，不证明真实供应商可用。
- PR 按用户指令暂停。重新获得发布授权后仍需完成预览门禁，并重新冻结发布前窗口；本报告不宣称未来实时目录持续无漂移。

## 范围与基线

- 基线：`origin/main@ae2493e2`（包含 PR #418、#419）。隔离分支 `codex/openrouter-oct15-muse-klein`。
- 新增：meta/muse-spark-open、black-forest-labs/flux-3-image-klein、nvidia/nemotron-3.5-super-vl 及 :free、respan/span-02、liquid/d1-ultra、krea/k3-edit、moondream/moondream3-preview、moondream/moondream3.1-9b-a2b。
- 保持旗舰与默认模型不变；九个普通卡片按既有确定性散布机制放在第六排后。
- 范围为模型数据、专用契约注册、对应测试和审计说明；不修改部署、凭据、持久化、依赖或公共 API 结构。由于目录、后端白名单、测试与计数必须同步，整体超过五文件，按新增、Batch、元数据、生命周期和分类分批验证。
- 回退：撤销本轮局部差异即可；无数据库迁移。用户随后明确暂停 PR，当前不提交、不推送、不创建 PR；部署或真实模型调用不在授权范围。

## 冻结来源

窗口为 UTC `2026-10-16T03:40:54.3524987Z` 至 `2026-10-16T03:40:57.9008506Z`（Phoenix 10 月 15 日晚）。

- Manifest：`C:/tmp/openrouter-oct15-muse-window/manifest.json`。
- Models SHA256：`348aff68c644351eb83f1d4a9ea16dcf7355f85bebf37a40a4eaf16ab95afb60`。
- Images SHA256：`d0baaa86b468947aa561e4fa245c4cb3931a180817c96e6fb60e1c49a8b80e51`。
- Videos SHA256：`927e50fd39c47dcb1da7c2d2b258d29ec013161907e0f785cb7c7691bc6b4351`。
- Market SHA256：`ccd677f8aa5518076ef1aaf8346e49aa50d1750d2b6e9317bb4bccd0d1152af2`。
- 四来源覆盖精确一致：676 条目录 = 595 非 Batch + 81 Batch；图片 59，视频 30。

## 调用契约

- Muse Spark Open、Nemotron VL 付费/免费和两款 Moondream：复用现有多模态聊天/视觉理解。目录能力不等同真实 Provider 验证；不扩展原音频/视频上传能力。
- Span-02、d1-ultra：加入后端 Decisions 白名单，使用 `/api/alpha/decisions`，不进入普通聊天。保留现有文字 state、最多 50 个 noul/choice/score 问题及大小限制；图片状态未开放。
- [FLUX 3 Image Klein](https://openrouter.ai/black-forest-labs/flux-3-image-klein)：同步 Images API、单次 1 张、0–10 张参考图；五档分辨率，16 种宽高比。专用端点报价按 768/1K/1.5K/2K/4K 为 $0.008/$0.009/$0.011/$0.014/$0.036 每张。现有动态目录和费用组件读取端点价，未增加第二套请求实现。
- [K3 Edit](https://openrouter.ai/krea/k3-edit)：同步 Images API、必须 1–4 张参考图、1K/2K、7 种宽高比和 seed，单次 1 张。现有参考图最小/最大数量校验直接适用。未开放 provider passthrough 参数。
- K3 Edit 端点 `pricing=[]`，模型页 $0.03 起而通用目录 `image=0.02`；不据此生成精确估价或标免费。界面保留费用未知状态，卡片说明冲突，等待上游结构化报价。
- 图片参考图使用 `input_references[].image_url.url`；既有 JPG/PNG/WebP、安全下载和完整响应处理不变。Klein 不沿用通用目录 seed，因为专用目录未声明该参数。

## 漂移与计数

- 修改前：缺少上述 9 个模型；18 个普通模型元数据漂移（17 价格、1 上下文）；`z-ai/glm-5.3-flash:batch` 的价格与参数漂移；42 个市场结构化漂移模型。未增加或删除 Batch 档位。
- 普通元数据涉及：~deepseek/deepseek-flash-latest、~deepseek/deepseek-v4-flash-latest、~moonshotai/kimi-latest、~z-ai/glm-flash-latest、~z-ai/glm-latest、cloudflare/clef-flash、deepseek/deepseek-chat、deepseek/deepseek-v4-flash-0731、deepseek/deepseek-v4.1-flash、google/gemini-3.8-flash、google/gemma-4-26b-a4b-it、meta-llama/llama-3.2-3b-instruct、mistralai/mistral-small-3.2-24b-instruct、moonshotai/kimi-k3、qwen/qwen3-next-80b-a3b-instruct、qwen/qwen3.6-27b、z-ai/glm-5.2、z-ai/glm-5.3-flash。
- 最终审计：`C:/tmp/openrouter-oct15-muse-final/summary.json`，clean；缺项、Batch、结构化元数据和市场分类漂移均清零。易变评分/工具调用观测与结构化漂移分开，不作为调用能力证据。
- 本地 725 = 595 live + 103 uncertain + 27 expired；698 为现行站内适配分母，**不是真实调用已验证数量**。81 个 Batch 档位不计入快照。

## 验证边界

- 前置第一次审计缺少新工作树 node_modules，按锁文件 npm ci 后重跑；新增时 readiness 计数未同步引起失败，已修正并通过。
- 旧价格/分类精确断言按冻结来源逐项更新，不关闭或放宽测试。
- 后端图片、决策、多模态聊天与 Batch 专项：89 passed，4 个既有 FastAPI 生命周期弃用警告。
- 前端模型、Batch、图片费用专项 108 passed；帮助中心 17 passed；更新脚本 24 passed。`npm.cmd run build` 通过，保留既有大 chunk 警告；`git diff --check` 通过。
- 未执行真实模型调用、完整后端套件或部署。独立前端预览为 5304，未复用上一轮 5303 或共享栈。

## 发布前二次窗口

- UTC `2026-10-16T08:41:00.0149060Z` 至 `2026-10-16T08:41:02.9150230Z`，manifest 为 `C:/tmp/openrouter-oct16-muse-pr-window/manifest.json`。
- Models SHA256：`551cb23b52690af71940ab00017089176ca16a9110f38607433c15a8da8c223f`；Images：`d0baaa86b468947aa561e4fa245c4cb3931a180817c96e6fb60e1c49a8b80e51`；Videos：`bce15454f4874876606e9850a2f2972dba8ddb9c04593b92ea442141dacc4281`；Market：`1e1bf5641dd48724f9218c142669340702d8996fb43f3edfb82b1756354d42e7`。
- 数量保持不变，无新增或 Batch 缺项。新增 13 项元数据变化和 4 项市场结构化变化，已告知用户并同步到本窗口。
- 元数据：~deepseek/deepseek-flash-latest、~deepseek/deepseek-pro-latest、~deepseek/deepseek-v4-flash-latest、~moonshotai/kimi-latest、~z-ai/glm-flash-latest、deepseek/deepseek-v4-flash-0731、deepseek/deepseek-v4.1-flash、google/gemma-4-26b-a4b-it、moonshotai/kimi-k3、nousresearch/hermes-4-405b、qwen/qwen3-embedding-8b、z-ai/glm-5.2、z-ai/glm-5.3-flash。
- 市场结构：~deepseek/deepseek-pro-latest、deepseek/deepseek-v4-pro、deepseek/deepseek-v4-pro-0813、meta/muse-spark-open。
- `C:/tmp/openrouter-oct16-muse-pr-final/summary.json` 为 complete/clean，审计退出 0。这是冻结窗口结论，不宣称之后的实时目录仍无漂移。

## 预览与发布门禁

- 新增帮助文章 `review-vision-image-decisions`、目录入口及测试。通过可见界面搜索 Span-02、进入决策页、填入公开示例文字、观察按钮启用和 Token 估算变化、清空后恢复禁用；未提交。按新教程再次重放前三步成功。
- K3 Edit 卡片可见 1–4 张参考图、价格不一致提醒和按媒体计费；本地前端没有可用后端目录，图片按钮禁用。代码确认按钮依赖 `confirmedImageOperations`，动态目录的 `resolve_target` 要求配置连接。没有绕过能力门禁、复制凭据或伪造可用状态。
- 截图已在浏览器内捕获，但浏览器运行环境禁止写入目标目录（EPERM），也禁止访问本地导出端口（EACCES）；尚未归档截图。不能将此记作完整帮助中心验收。
- 依照 AGENTS §2.6，当前尚不创建 PR。需完成图片预览连接检查、截图归档与教程完整重放，再刷新发布前窗口、审查最终差异并提交。没有 commit、push、PR、merge 或部署。
