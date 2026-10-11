## 当前：整合候选与 PR 提交（2026-10-11）

用户已明确授权最终收尾和 PR 提交。发布候选位于 `codex/rpg-god-integration`，基线 `7eaaa305cface49ca3eedba6f47a7315f641e584`；原工作区与18512真实验收会话保持不动。本授权不包含合并、共享部署或新增真实调用。

最新主线整合、控制面兼容测试及全新离线教程重放已完成：Node 358通过/1跳过，前端173通过，后端91通过，两处构建通过。长路径Windows落盘EPERM在短路径同源码回归未复现；具体环境机制仍未确定。最新390px、旧数据副本降级、所有插件真实组合及整仓/远端CI不在已通过范围。

离线整合入口：http://127.0.0.1:18520/rpg/god 。当前真实账本仍13/13、剩余0；离线实例不加载真实凭据。详见 [整合报告](../../../docs/ai-rpg-experiment/god-card/INTEGRATION-REPLAY.md) 与 [机器可读验证](../../../docs/ai-rpg-experiment/god-card/INTEGRATION-VERIFICATION.json)。下方所有旧额度、入口与授权文字均为历史记录。

## 历史：PR 前收尾（2026-10-11）

用户已表示“通过，可开始 PR 前收尾”。本轮不含 Commit、Push 或 PR 授权。当前工作区为 codex/rpg-god-card，基线 e5a808a6；保持原18512入口、会话与全部账本，未迁移旧会话。

最新账本13/13、物理尝试16、保留明确豁免3，无剩余额度。最后一次仅剧情，记忆宫殿停用且4条记忆保留；Gemini以content_filter结束，部分原文保留而不写入完整历史。人工接受不改写这个失败事实。当前作者system不变，已批准用户后置词每轮追加，参数0.70/0.80/32768。

R5修复覆盖作者剧情辅助注释块、普通文本换行、17张固定吐槽图片路由及预算预留误报。独立预览的公开HTML顶层导航在边缘提供，会话和写API继续沿用来源校验。详细证据见R5-RETEST.json；下方额度及模式均为当时历史快照。

## 历史：用户补充后置词，本卡7/9（2026-10-11）

http://127.0.0.1:18512/rpg-app/god/?session=fef7a176-90d8-47bf-978f-ab5f58d5b995 。首轮与续轮当前user末尾追加《输出要求.docx》正文，界面隐藏；作者system完全不动。参数保持0.70/0.80/32768，M3开启，剩2次，全部由用户手动派发。92项离线回归通过，篇幅效果待实测。详见[本批记录](../../../docs/ai-rpg-experiment/god-card/R4-OUTPUT-SUFFIX.md)。

## 历史：God输出上限32768，本卡5/7（2026-10-11）

http://127.0.0.1:18512/rpg-app/god/?session=f63f9b55-eb2e-4e77-848e-fe35240ec7d3 。同角色新会话，Gemini剧情及记忆宫殿均32768，temperature=0.70/top_p=0.80不变，剩2次，仅用户手动发送。旧会话保留。上一轮5817输出tokens正常stop，提高上限不保证篇幅增长。完整验证及回退见[参数准备记录](../../../docs/ai-rpg-experiment/god-card/R3-LENGTH-32768.md)。

## 历史：控制面上限 100，本卡 3/5（2026-10-11）

已修复派发前拒绝的回执与提示，并按用户明确授权豁免 slot-5；本卡已用 3/5、剩余 2 次，控制面累计上限独立为 100。新手动入口：<http://127.0.0.1:18512/rpg-app/god/?session=760cc57c-6a47-4bc1-85ba-f8ed4fce0868>。剧情及记忆均为 Gemini，宫殿已启用，只有玩家主动发送才调用。旧会话、失败证据和账本保留，没有迁移。共享 server 发生本次已授权的定点替换；回退容器、配置保留、验证和今后 Compose 重建注意事项见 [额度修复记录](../../../docs/ai-rpg-experiment/god-card/R3-QUOTA-REPAIR.md)。

## R3 终止提示修复与手动测试（历史，2026-10-11）

最近一次手动剧情已确认由 Provider 内容过滤终止，原文与回执保留，记忆整理没有派发；旧会话已从未知收束为明确失败，没有迁移运行版本。提示修复后已准备新手动会话：<http://127.0.0.1:18512/rpg-app/god/?session=88900dd0-e723-41cd-ad6f-284b4197660f>。用户新增一次授权后为已用 3/5、剩余 2 次。仅由用户点击发送；本批 Agent 派发 0 次。58 项后端、19 项前端测试及卡片类型检查/构建通过。详见 [修复记录](../../../docs/ai-rpg-experiment/god-card/R3-TERMINAL-REPAIR.md)。下方保留各阶段历史。

## R3 控制面修复与额度更正（历史，2026-10-11）

当前容器已修复 OpenRouter Fake-IP 解析；无凭据 HTTPS HEAD 返回 200，出口防护保持。用户明确豁免第3次控制面失败，运行状态为已用2/4、剩余2、历史尝试3、豁免1；原失败文件与未知操作保留。修复未生成剧情或记忆。该解析覆盖限当前容器，重建后须重新核验。详情及回退见 `docs/ai-rpg-experiment/god-card/R3-CONTROL-REPAIR.md`；以下 R3 阻断内容保留为历史记录。

## R3 当前真实验证状态（2026-10-11）

R2 已获用户接受。新 God 会话已安装并授权记忆宫殿，剧情与记忆均选择 Gemini；首个剧情请求在控制面被 `provider_address_blocked` 拒绝，本地保留 unknown，记忆整理未派发。账本已计数 3/4，剩余 1，预留 0；不自动重试，未验证插件内容泛化。详见 `docs/ai-rpg-experiment/god-card/R3-PLUGIN-REAL.md` 与 `R3-RESULT.json`。18512 继续保留，18514 原验收入口不变。

# 上帝模拟器／神临凡尘

## 当前展示验收：R2

R2 已接入 God/Earth 的受控展示转换与隔离作者 CSS，恢复翻面、弹幕、固定图片展开和状态栏。独立只读入口：http://127.0.0.1:18514/ 。使用现有真实输出的副本，不新增模型调用，旧验收入口保持不变；本批等待用户可见验收。

完整结果、截图、边界及回退步骤见 [R2-PRESENTATION](../../../docs/ai-rpg-experiment/god-card/R2-PRESENTATION.md)，当前文件及隔离构建 hash 见同目录 `R2-DELIVERY.json`。旧会话在新宿主显示版本不兼容，仅用于回放，不自动追认版本或迁移。God 已有真实派发 2/4（首份内容过滤、第二份完整），本批新增 0 次。

## B4 运行说明（保留历史）

作者官方system按原文冻结，SHA256：80e021b3f1421ed0d32ec7692d28c0445915ec21353a691b9ba07b5f83b8f59e。B3.5作者配置与共享五插件离线UI已获人工通过。

当前B4提供独立正式运行版本 god-plugins-real-v1，通过 God 专属 modelPolicy 复用受控传输、会话、插件与预算。配置与系统消息来自服务器；前端仅提交setup及mode。首轮只有原system和完整天书，续轮保留原始历史与玩家输入，无Earth前后置词。

- 旧离线入口18510保留；独立正式入口18512。
- 真实Gemini固定 temperature0.70、top_p0.80、max_tokens16384。
- God账本独立4次，已用2次，剩2次。首次为Provider内容过滤终止；第二次生成完整回合。保留两次原始文本、SSE、回执，技术完成不能视为质量验收通过。
- 启动不派发。God正式服务须显式传godControl，仅在内存中沿用服务S2S配置，不接收客户端模型地址或凭据。默认godPlugins仍走离线；不支持静默迁移旧版本会话。
- 不自动认证、降级、重试；不自动消耗剩余额度。插件安装和逐会话授权分离，首轮实测全部关闭。

验证命令：
~~~powershell
node --test experiments/ai-rpg-engine/god-card/tests/*.test.mjs
~~~

正式工具start-real.mjs只启动本轮自有预览；real-first.mjs的prepare/dispatch有一次性操作记录。未知结果不得换ID重放或删除invocation文件。重启前确认进程归属并正常关闭插件存储；异常遗留锁必须先核对旧进程已停止，再归档锁，不清空数据目录。

查看 docs/ai-rpg-experiment/god-card/B4-ACCEPTANCE.md、B4-FIRST-RECEIPT.json。原文和完整回执保存在忽略目录 .rpg04-work/god-card/b4-real/data/god/real-dispatches/god/slot-1/。
没有Commit/Push/PR/共享部署授权。

## 早期批次记录（保留历史，不代表当前状态）

# 上帝模拟器／神临凡尘

作者官方系统提示词已机械冻结。B1 作者开局界面与 B2 离线宿主已获用户验收；当前 B3.1 提供分支存档的离线兼容候选，聊天 UI 和其他插件仍待后续批次。

```powershell
node experiments/ai-rpg-engine/god-card/preview.mjs 18505
node --test experiments/ai-rpg-engine/god-card/tests/*.test.mjs
```

运行前检查端口，不替换其他实例。18505 仅作者界面预览；填写后可在“天书”复制原始开局文本，草稿保存在该 origin。此页面尚未连入聊天 UI。

Studio 启用 God API 使用可信启动选项 godCard:true；分支需额外 godBranches:true（环境开关分别为 RPG_GOD_CARD_ENABLED / RPG_GOD_BRANCHES_ENABLED）。在新独立数据目录启动；God 当前仅 offline，正式模型/参数未配置，额度0。不要复用旧验收目录或旧调用账本。

API：/rpg-app/god/api/sessions，创建只收 setup 和 mode:offline；首轮发送须等于服务器生成的 characterText。续轮原输入，不携带地球卡前后置词。
分支：POST sessions/:id/branches，GET sessions/:id/plugins/rpg.branch-save/nodes；复用全局插件市场，先安装本宿主审核的1.1.0制品，再逐会话授权。新路线授权默认关闭，创建不调用模型。模型选择、M1/M2/M3 本子批次仍明确不兼容。

新增跨卡制品只在显式启用 God 分支的宿主目录出现，原插件包不变；现有1.0.0安装不能自动升级。停用/卸载保留全部原始会话和分支，源码版本不匹配的旧会话只读，不自动迁移。

资源位于 resources，UI与复制格式位于 public 和 lib/setup.mjs，装配位于 lib/assembly.mjs，宿主适配位于 lib/adapter.mjs 与 lib/http.mjs。详见 docs/ai-rpg-experiment/god-card/B3.1-BRANCH-ACCEPTANCE.md。
