# 龙族卡片本地验收

日期：2026-11-06。隔离分支 `codex/rpg-dragon-raja`；基线 `6977d86c0ad13e4cd18a9a641f0aae64731443a4`。

## 结果

- 作者 DOCX 的段落、空段、text run 顺序机械保留；system SHA-256 与清单一致。没有语义改写。
- `node dragon-raja-card/build.mjs --check`：通过。原始 HTML、两张素材及生成结果校验通过。
- 后端 72 项通过（Node 24.18.0）。从实验目录运行：
  - `node --test dragon-raja-card/dragon.test.mjs`：5 项，完整提示词、静态资源、开局不发送、两轮离线对话、重启恢复、合成网关两轮及公用 Mod 启停。
  - `node --test studio/archive-plugins.test.mjs studio/controlled-provider.test.mjs`：28 项，五卡八类插件、旧适配器兼容、受控回执、额度与不重放约束。
  - `node --test studio/memory-tasks.test.mjs plugins/custom-config/custom-config.test.mjs plugins/mod-panel/mod-panel.test.mjs`：39 项，记忆任务、自定义配置与 Mod。
- 前端 50 项通过。在 `client` 运行 `npx vitest run --configLoader runner --maxWorkers=1 --fileParallelism=false src/pages/RpgWorkspace.test.tsx src/pages/RpgFreeOpening.test.tsx src/pages/RpgPluginsPage.test.tsx`（实际分两次运行，38 + 12）。
- `client/npm run build`：通过，保留已有大 bundle 警告。
- `docker build -f experiments/ai-rpg-engine/studio/Dockerfile -t modelmirror-rpg-dragon-raja:review experiments/ai-rpg-engine`：通过，包含 UI 与 card-replica 类型检查和构建。未升级锁文件或依赖。
- 最终 `git diff --check`：通过。

## 实际界面检查

使用内置浏览器检查 1440 × 1000 与 390 × 844 视口；临时尺寸已恢复。原始图像在严格不透明来源沙箱下以内嵌方式正常显示。

已操作开场唤醒/跳过、六页切换、卡塞尔入学篇选择、原著楚子航写入档案、切回原创林澈、自定义引号和尖括号保留、言灵钥匙选择、天赋八抽与选择、带入可编辑草稿、两轮离线发送、容器重启后从故事馆恢复。窄屏卡片 body 宽度与 scrollWidth 均为 360，无横向溢出。独立聊天与底部输入框可用。

“复制档案”通过系统剪贴板粘贴至临时记事本核验，文本逐字一致。浏览器自动化的虚拟剪贴板为空，未将其当作产品复制失败；系统剪贴板验证为准。临时记事本测试标签已关闭。

预览：`http://127.0.0.1:19789/rpg/dragon-raja`，宿主端口 19788。均为本任务独立容器，不使用共享栈数据和凭据。桌面截图保存在忽略目录 `.rpg04-work/dragon-desktop.jpg`。

## 边界与回退

真实调用仅限下述用户追加授权的 7 次请求；未进行共享栈部署、Git 提交或 PR。无数据库迁移，现有宿主文件与旧存档指纹未改。回退本次入口/路由即可隐藏新卡，保留 `archive-chat/dragon-raja` 数据以备恢复。

## 追加授权的真实调用验收

2026-11-06，用户授权 1 轮中性开局、2 轮白羽绫音「东京黑月篇」开局及插件调用。经现有受控服务实时目录确认，使用 `google/gemini-3.8-flash`，参数 `temperature=0.7`、`top_p=0.8`、`max_tokens=16384`。单独启动宿主与存储目录，经真实聊天 API 创建和发送会话，没有修改共享栈、原作者 system 或用户样例。

| 请求 | 实际动作 | 结果 |
| --- | --- | --- |
| 1 | 林澈中性入学开局 | complete / stop |
| 2 | 白羽绫音随卡塞尔三人组赴日 | complete / stop |
| 3 | 样例第 1 轮自动小结 | complete / stop，186 字符 |
| 4 | 样例第 1 轮记忆宫殿更新 | complete / stop，发布 3 条记忆 |
| 5 | 样例第 2 轮讨论任务、暂不主动使用叙事人 | complete / stop |
| 6 | 样例第 2 轮自动小结 | complete / stop，151 字符 |
| 7 | 样例第 2 轮记忆宫殿更新 | complete / stop，更新既有 3 条记忆 |

- 7 份回执均确认实际模型为 Gemini 3.8 Flash，HTTP 200，`retries=0`；SSE 正常结束，原文与 SSE 哈希、模型选择和参数绑定均通过校验。回执用量合计输入 80,098、输出 30,943、总计 111,041 tokens；这不是费用结算数据。
- 中性轮未开启插件。样例会话安装并授权模型选择、历史窗口、自动总结 v2、记忆宫殿；每轮计划严格为剧情、小结、记忆更新 3 个阶段。
- 样例首次发送在本地被 `MEMORY_MODEL_REQUIRED` 拦下，原因是测试脚本未选择记忆模型。确认会话 0 轮、requests 与插件 operations 为空、没有产生第 2 次外部请求后，补齐同一 Gemini 模型配置继续。没有重发中性轮，也没有重试任何未知结果。
- 第 2 轮实际发出的 system 包含完整原作者 system、首轮小结和全部 3 条召回记忆；原始第一轮长回复已由小结替代（消息为 system + 当前 user）。最终 2 条小结、`processedThrough=2`、3 条记忆、召回 3 条、所有任务 complete，未留下待恢复或 blocked 状态。
- 剧情文本抽查：从成田机场接机延续到赴新宿车队，保留白羽绫音身份、外观和未知言灵设定，第 2 轮讨论任务并未主动施展「叙事人」。这是两轮样例观察，不代表长期叙事或原著事实全面验证。
- `node .rpg04-work/dragon-live-verify.mjs` 通过，验证上限 7、回执/SSE、原文保持、两条小结、记忆更新与实际上下文注入。`npm --prefix card-replica run build` 通过（已有混合静态/动态导入警告），`node dragon-raja-card/build.mjs --check` 与 `git diff --check` 通过。聊天 HTML 和其 3 个静态资源 HTTP 200。
- 实际证据位于忽略目录 `.rpg04-work/dragon-live-20261106/`：`verification.json`、逐请求 wire/receipt、会话与插件快照；脚本和原始叙事不进入提交。本批次已耗尽并关闭发送额度，临时本地宿主仅供查看结果。
- 真实结果预览：`http://127.0.0.1:19790/rpg-app/dragon-raja/chat/?session=64619417-8d16-432d-b8b0-07aa351d2047`。中性存档：`58597d10-7055-4764-837f-f23585be5f8c`。
- 本次只验证逐轮小结与记忆更新/召回，未达到 20 轮大总结合并条件，未调用配图/改写等额外模型。Computer Use 因无法可靠判定当前浏览器 URL 而中止，本轮真实回复的浏览器视觉验收未完成；前一阶段的离线界面检查不替代该项。

## 文件清单

- `client/src/content/help-center/articles/choose-rpg-model.md`
- `client/src/content/help-center/articles/play-rpg-cards.md`
- `client/src/content/help-center/articles/use-rpg-rolling-summary.md`
- `client/src/pages/ArchiveCardPage.tsx`
- `client/src/pages/RpgFreeOpening.test.tsx`
- `client/src/pages/RpgPage.tsx`
- `client/src/pages/RpgPluginsPage.test.tsx`
- `client/src/pages/RpgWorkspace.test.tsx`
- `experiments/ai-rpg-engine/card-replica/src/entry.tsx`
- `experiments/ai-rpg-engine/card-replica/src/presentation.ts`
- `experiments/ai-rpg-engine/card-replica/src/session-client.ts`
- `experiments/ai-rpg-engine/card-replica/src/xingjian.tsx`
- `experiments/ai-rpg-engine/plugins/custom-config/custom-config.test.mjs`
- `experiments/ai-rpg-engine/plugins/custom-config/host.mjs`
- `experiments/ai-rpg-engine/plugins/mod-panel/mod-panel.test.mjs`
- `experiments/ai-rpg-engine/studio/Dockerfile`
- `experiments/ai-rpg-engine/studio/archive-plugins.test.mjs`
- `experiments/ai-rpg-engine/studio/controlled-provider.mjs`
- `experiments/ai-rpg-engine/studio/illustration-rewrite.mjs`
- `experiments/ai-rpg-engine/studio/memory-budget.mjs`
- `experiments/ai-rpg-engine/studio/memory-task-provider.mjs`
- `experiments/ai-rpg-engine/studio/server.mjs`
- `experiments/ai-rpg-engine/dragon-raja-card/README.md`
- `experiments/ai-rpg-engine/dragon-raja-card/assembly.mjs`
- `experiments/ai-rpg-engine/dragon-raja-card/build.mjs`
- `experiments/ai-rpg-engine/dragon-raja-card/dragon.test.mjs`
- `experiments/ai-rpg-engine/dragon-raja-card/public/awakening.gif`
- `experiments/ai-rpg-engine/dragon-raja-card/public/background.jpg`
- `experiments/ai-rpg-engine/dragon-raja-card/public/index.html`
- `experiments/ai-rpg-engine/dragon-raja-card/resources/ASSETS.json`
- `experiments/ai-rpg-engine/dragon-raja-card/resources/MANIFEST.json`
- `experiments/ai-rpg-engine/dragon-raja-card/resources/original.html`
- `experiments/ai-rpg-engine/dragon-raja-card/resources/prompts.json`

## 首页收尾契约（2026-11-06）

用户已接受真实调用结果，并授权补齐故事馆布局、提交 PR。当前独立工作树 `C:/tmp/modelmirror-rpg-dragon-raja`，分支 `codex/rpg-dragon-raja`；发布前刷新 `origin/main` 后与 HEAD 同为 `6977d86c`。已有改动均为本卡装配，真实调用记录仍在忽略目录。

本批修改范围：`RpgPage.tsx`、`rpg-ui.css`、`RpgAuthoringEntry.test.tsx`、玩家帮助文章和本验收记录。首页新世界区由注册表筛选龙族/斗罗，未登记的卡不会产生空链接；已登记新卡桌面共享一行、窄屏纵向排列，旧世界与续玩保留。另一斗罗任务保有自己的工作树和 PR；不覆盖其文件。后续帮助截图、发布基线整合与装配文件属于同一可验收卡片目标，需要在同一 PR 保持入口、宿主、测试与文档一致，故完整交付超过 5 文件。

验证：前端入口/存档故障回归、目标卡后端测试、前端构建、桌面和窄屏可见操作、帮助重放、最终差异与敏感信息扫描。不新增生产依赖、数据库迁移或模型调用。回退只撤销本卡注册与展示；不触碰共享数据或旧卡存档。

## 发布收尾与双卡协调

用户已接受上述真实测试并授权提交 PR。斗罗 PR #445 的 bd1fb498 已整合；随后主线合并为 193ea780，两个提交文件树相同，已快进至最新主线，不需要依赖分支或改动另一工作树。原提示词、媒体和旧宿主指纹保持一致。

- 首页“探索新世界”同时展示斗罗/龙族，桌面等宽并排，窄屏纵向排列；原六个世界与续玩保留。完整可见验收和帮助重放见 `docs/help-center/evidence/dragon-193ea780/README.md`。
- Node 目标回归共 76 项通过；前端目标回归共 95 项通过。整合时旧断言仅计七卡，已改为八卡并补齐双方插件覆盖；未放宽断言。
- 两套前端构建、帮助截图检查、Compose 静态校验、生成 HTML 一致性通过。构建产物、真实调用记录、临时配置和存储不提交。
- 默认关闭的 RPG_DRAGON_CHAT_ENABLED / CALL_LIMIT 已接入 Compose，无新增依赖或数据库迁移。
- Git 发布仅包含龙族卡、必要公共注册/宿主接线、协调布局与帮助证据。超过五文件的原因是新卡需要同时覆盖资源、装配、聊天、插件、容器和用户教程，均属于同一可验收目标。
- 此前“未 Git 提交或 PR”记录描述当时阶段；本节追加用户授权的发布收尾，不把早期视觉限制或未测大总结合并改写为通过。没有追加真实调用、合并或共享栈部署。

## 肥鱼合并后的最终协调

用户澄清另一卡为蓝色大肥鱼。重新获取主线后确认 #444 在 #445 后合并，最终发布基线改为 c4630653。已合并肥鱼的注册、独立编辑器、存储桥接与聊天支持，并解决全部交集冲突；没有修改肥鱼运行时副本。首页改为“探索与创作”，斗罗、龙族与肥鱼宽屏三列，中等宽度换行，手机纵向排列；总计九张卡入口保留。此前双卡报告只是中间检查，最终证据见 `docs/help-center/evidence/dragon-c4630653/README.md`。

前端六个目标文件共 103 项通过；后端三卡和公共插件共 82 项通过（肥鱼三项等价校验因精确白名单增加龙族而更新，修正后四项全部重跑通过）。最新主线再次重放龙族档案、刷新草稿、离线发送、首页续玩，并打开肥鱼独立编辑器和斗罗觉醒台。截图在本基线重新捕获。

`git diff --cached --check` 报告冻结原 HTML 的两处既有行尾空格及生成文件对应两处，共四处；保持源哈希不删除原文空格，其他文件单独检查通过。该项如实保留为发布限制，不关闭或削弱检查。
