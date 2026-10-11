# R2：两卡展示接入与真实原文回放

日期：2026-10-11。工作区 `C:/tmp/modelmirror-rpg-god-card`，分支 `codex/rpg-god-card`，基线 `e5a808a60b024635a1e13965558f15775c5af86a`。本批接续用户已确认的 R1；R1 的发现和历史 hash 不改写。

## 结果与范围

离线与回放候选通过本批已运行检查，等待用户可见验收。RPG Studio 的 God/Earth API 现在使用宿主指定的展示 profile；原始回复、作者文本、模型消息、历史及调用账本均不改变。God 首轮真实输出的用户意见仍仅为“内容质量差距不大，展示存在明显差异”，不外推为整个卡片验收完成。

- God：恢复宏观/微观原生复选控件、作者 0.6 秒翻面、三轨弹幕、折叠面板、固定图片及点击/空格展开吐槽。
- Earth：恢复状态面板、人物标签/网格、进度条颜色与流光、NPC/环境/社会/记忆面板样式，以及已核对的 `say` 角色着色规则。
- 样式限定在宿主创建的 `.card-output[data-profile]` 中；模型不能自行选择 profile。保留脚本、远端资源、任意 CSS、事件及危险命名空间的过滤边界。
- 图片只接受 R1 已登记的两个精确作者 URL，并映射为已冻结本地文件；未知图片仍不自动联网。实际 God 回复引用的是第二张作者图片，不替换成用户案例的另一张。
- 不执行模型提供的脚本或 CSS。任意新类名、脚本驱动组件、外部图片不会因为这次补齐自动获得执行权限。

本批代码：`card-replica/lib/card-presentation.mjs`、`src/card-presentation.css`、`src/chat.tsx`、`tests/card-presentation.test.mjs`；`god-card/lib/http.mjs`、`lib/ui.mjs`、`tests/ui-http.test.mjs`、`tools/presentation-preview.mjs`；`studio/server.mjs`。没有增加依赖。原有整个 God 竖切的脏 Diff 仍保留，不能把它全部归为 R2。

## 人工入口

打开 **http://127.0.0.1:18514/**，进入 God 或地球 OL。Earth 进入后点击“历史”，打开“林遥（虚构验收角色）”。God 链接直接打开“龙婉翩（婉儿）”。

此入口读取两个已保存真实会话的独立字节副本，并拒绝全部非 GET 请求；显示可用额度 0，不使用原账本。旧会话与当前运行版本不匹配的提示，以及插件状态不可用提示，是只读回放的边界，不通过改版本或迁移来绕过。原 18511/18512 调用入口、18505/18510 预览未替换。

已核实 God 原文件、Earth 原文件及回放副本完全一致。三个回复分别为 9533、13860、17972 个 Unicode 字符。完整源文件/历史/原文/展示 hash 见 `R2-REPLAY-EVIDENCE.json`；本批 Provider 派发 **0 次**，God 原授权仍为已用 2 / 上限 4。

需要重启本批预览时，先核对端口及进程命令行，仅使用本批自有实例，运行：

```powershell
node experiments/ai-rpg-engine/god-card/tools/presentation-preview.mjs C:/tmp/modelmirror-rpg-memory-palace/experiments/ai-rpg-engine/.rpg04-work/memory-palace-b5/data/earth/fb57eeb2-4c55-4eea-a677-c317fd977147.json
```

脚本默认从 `.rpg04-work/god-card/r2/dist` 读取本批隔离构建；副本已存在时必须与来源逐字节一致，否则拒绝启动。

## 验证证据

| 检查 | 结果与范围 |
| --- | --- |
| God + Earth + Studio + plugins 离线回归 | 339 项：338 通过，0 失败，1 跳过；跳过项需要未提供的 `RPG_B3_LEGACY_COPY`，不计通过 |
| 展示与 HTTP 聚焦测试 | 19/19 通过；包含上述回归中的测试，不重复累加为额外通过数 |
| 受影响前端 | 7 文件、63/63 通过；含 God、聊天折叠节点稳定性、模型选择、M1/M2/M3、插件市场 |
| 独立卡片生产构建及 TS | 通过；最后手机溢出修复后重新构建 |
| Studio 生产构建 | 通过；输出到隔离目录，未覆盖原 client/dist |
| 宿主实际 GET 展示 | 两卡均返回对应 profile；原文逐字相同，来源文件/副本相同；两个写请求均被 403 拒绝 |
| God 桌面/390px | 键盘 Space 切换正反面、展开图片；图片成功加载本地资源；三轨动画时长 20/24/17 秒，连续观察 transform 变化；功能区折叠开启、箭头 0.3 秒/内容 0.4 秒 |
| Earth 桌面/390px | 状态栏键盘 Enter 展开；桌面 3 列、390px 1 列；血量/饥饿/疲劳保留原始百分比与各自渐变；流光规则生效 |
| 两回合隔离/重取 | 第一回合展开后点击“恢复记录”，第一回合保持打开，第二回合保持关闭；React memo 节点稳定性亦有自动测试 |
| 390px 横向边界 | 最终两卡 document.scrollWidth=375（扣除滚动条），innerWidth=390；God 翻面开始及完成时均不溢出 |
| 资源/执行边界 | 展示图片指向本地；God CSP 禁止第三方图像/脚本；负向用例确认危险标签、事件、任意资源 URL 不进入展示 |

运行命令（仓库根目录，除已注明者）：

```powershell
node --test experiments/ai-rpg-engine/god-card/tests/*.test.mjs experiments/ai-rpg-engine/card-replica/tests/*.test.mjs experiments/ai-rpg-engine/studio/*.test.mjs experiments/ai-rpg-engine/plugins/*.test.mjs
node --test experiments/ai-rpg-engine/card-replica/tests/card-presentation.test.mjs experiments/ai-rpg-engine/god-card/tests/ui-http.test.mjs
# client 目录
npm.cmd exec vitest run src/pages/RpgGod.test.tsx src/pages/RpgChatState.test.ts src/pages/RpgHistoryWindow.test.tsx src/pages/RpgModelSelector.test.tsx src/pages/RpgRollingSummary.test.tsx src/pages/RpgMemoryPalace.test.tsx src/pages/RpgPluginsPage.test.tsx
npm.cmd run build -- --outDir ../experiments/ai-rpg-engine/.rpg04-work/god-card/r2/client-dist
# card-replica 目录
npm.cmd run build -- --outDir ../.rpg04-work/god-card/r2/dist
```

日志在 `.rpg04-work/god-card/r2-regression.log` 和 `.rpg04-work/god-card/r2/`。隔离 outDir 位于项目外的 Vite 提示不代表失败；未清理原有 dist。Git 以 `core.whitespace=cr-at-eol` 检查本批两个已跟踪文本文件通过，原工作区 CRLF 历史现象保留。

## 实测发现与修复

1. 初版只读回放继承离线默认额度，曾显示“100 次”。已在回放代理中覆盖为 0，并增加只读说明；这只改变回放状态响应，不修改真实账本。
2. God 手机翻面过程中曾短暂出现 scrollWidth=431。给翻面视窗加入 `overflow:clip` 后，翻面开始/结束均为 375；正文仍可在正反面独立纵向滚动。
3. 桌面与手机截图为官方浏览器工具捕获的原始 JPEG 字节，没有补画或编辑。工具直接写文件受到只读权限限制，经过已批准的本地临时截图归档表单保存；该辅助进程归档后关闭。桌面截图早于最后仅限制手机翻面溢出的 CSS 调整，God 手机截图在修复后采集。

截图：`R2-GOD-DESKTOP.jpg`、`R2-GOD-MOBILE.jpg`、`R2-EARTH-DESKTOP.jpg`、`R2-EARTH-MOBILE.jpg`。God 显示实际输出指定的图片变体；不能据此要求模型生成与原案例完全相同的正文。

## 未验证边界与回退

- 本批没有新增真实生成；回放证明展示兼容，不是新的模型内容质量验收。
- 未对所有作者模块做完整逐状态/逐帧对照；幕后采访、深层世界等未在本次真实回放中出现，不能宣称完整通过。
- `prefers-reduced-motion` 已提供静态弹幕与关闭过渡的降级规则，但当前浏览器工具不提供该偏好模拟，未实际切换 OS 偏好验收。暗色作者变体未接入，本批限定已对照的浅色展示。
- 未记录完整浏览器网络瀑布；资源限制证据来自实际 DOM、本地图片加载、宿主静态路由/CSP和负向测试。
- 独立旧 `card-replica/server.mjs` 离线入口仍保留其原展示器；本批接入的是 RPG Studio 的两卡入口，不宣称所有历史宿主均升级。

回退只撤回 R2 接入：God/Earth HTTP 展示恢复原 `safeHtml`，移除聊天 CSS 导入；保留 R1 转换器、资源、证据及全部会话。原产物未替换，可继续使用旧入口。停止本批18514之前必须核对 `.rpg04-work/god-card/r2/replay-receipt.json` 的 PID 与命令行，不按历史 PID 盲停。禁止重置整棵工作区或删除会话/账本。

本批未 Commit、Push、PR、Merge、共享部署或发布。下一门禁是用户对 R2 可见回放的验收。
