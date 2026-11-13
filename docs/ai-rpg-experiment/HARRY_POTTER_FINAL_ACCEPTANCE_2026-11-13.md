# 哈利波特最终验收与交付

基线：`origin/main@a237f26f`。工作树：`C:/tmp/mm-rpg-harry-potter-pr`，分支 `codex/rpg-harry-potter-pr`。原工作树及真实验收存档保留。

授权：完成前端复刻、恢复与插件最终验收；不修改提示词、不新增真实调用、不部署。全部收尾后提交本任务文件并创建 PR，不合并。

整合范围：将原隔离工作树的卡包、入口、恢复和插图修复迁入最新主线。17 个注册文件与统一故事馆、中世纪、AI 娘发生文本冲突，需保持跨层路由/卡片/插件一致，不能把半完成的注册当作独立可验收交付；本批整体解决后统一构建和回归。保留主线布局与所有已合入世界，不回退故事馆。无数据迁移、新依赖或共享服务修改。

验收：原卡样式与折叠文案对照、桌面/手机/动效；冻结哈希；新基线独立预览按帮助步骤回放；离线会话强杀恢复、旧四轮复制件兼容性；插件授权/隔离/导入导出/插图幂等回归；主前端与聊天构建；隔离无网络容器冒烟；最终差异和凭据扫描。真实模型和真实插图服务本轮不调用。

回退：撤销本卡入口及宿主接线，保留独立存档与证据；不清空旧数据。验收失败先修复或记录证据边界，不降低断言。

## 已完成交付

| 项目 | 结果与证据 |
| --- | --- |
| 作者原文与素材 | 提示词系统 SHA-256 `10859780a0de93d01a2be111d31abf1fc3487778d6df9ae7d20bfe992e861f55`；DOCX、原 HTML、背景、动画、字体与冻结记录一致。未重新改写提示词。 |
| 前端 | 六页签、13 作品、274 原著人物、12 能力分类、双命运池；本轮 CUA 原站和新版独立预览对照、桌面/手机、动画和折叠核对通过。详细范围见帮助证据。 |
| 草稿/存档 | 1306 字草稿刷新保留；离线一回合保存后通过故事馆恢复。强杀恢复测试通过。 |
| 原四轮复制件 | 最后一次 `verify-replay.mjs final-post-lock-replay`：四回合兼容、强杀前后 API 完全一致、36 份 dispatch 文件和源 session 字节不变；8 个冻结文件不变，真实调用 0、凭据读取 0。每回合 9 个面板默认关闭。 |
| 插件 | HP 和七个其他档案世界的八插件授权隔离、导入导出、改写与插图回执假服务集成通过；共享分支、模型选择、历史窗口、总结、记忆 HTTP 生命周期回归通过。 |
| 插图 unknown | 瞬时文件发布锁错误有界重试同一 rename；永久失败保留 unknown 且不重复生成。5 条故障注入回归全部通过。旧偶发错误的具体 errno 未采集，不能声称历史每次原因完全相同。 |
| Windows 关闭兼容 | 扩展分支回归发现显式 plugins.close() 后内核租约未释放，且损坏注册表的只读降级被阻断；已修复，保留并通过原有分支之分支恢复测试，无降低断言。 |
| 帮助中心 | 新指南、故事馆指南链接、最新基线截图与实操记录同批交付；`docs/help-center/evidence/harry-potter-a237f26f.md`。 |

## 实际验收命令

在 `experiments/ai-rpg-engine`：

```text
node --test studio/illustration-persistence.test.mjs studio/illustrations.test.mjs studio/archive-plugins.test.mjs studio/local-owner.test.mjs studio/card-entry.test.mjs studio/session-lifecycle.test.mjs studio/branches-http.test.mjs studio/model-http.test.mjs studio/history-http.test.mjs studio/summary-http.test.mjs studio/memory-http.test.mjs harry-potter-card/harry.test.mjs card-replica/tests/harry-potter-presentation.test.mjs plugins/custom-config/custom-config.test.mjs
```

通过：64/64。模型、总结、记忆、改写和图像服务均为本地桩，不代表追加真实验收。

在 `client`：

```text
npx.cmd vitest run --maxWorkers=1 src/pages/HarryPotterCard.test.ts src/pages/HarryPotterOutput.test.ts src/pages/RpgWorkspace.test.tsx src/pages/RpgPluginsPage.test.tsx src/pages/RpgFreeOpening.test.tsx src/pages/HelpCenterPage.test.tsx
```

通过：143/143。首次与两个构建并行时，HP jsdom beforeEach 超时；串行单 worker 复跑通过，未修改超时或断言。全局 `helpContent.test.ts` 为 17/18，通过 `git show HEAD` 确认失败来自主线 `manage-rpg-history` 的图片基线（fbe65f12/regen.png 对应文章元数据 479cd636），保持原样并在 PR 披露。

其他检查：主前端 `npm.cmd run build`、聊天前端 `npm.cmd run build`、`node harry-potter-card/build.mjs --check`；独立 Docker 镜像构建、无网络/非 root/只读根目录/无 capabilities 冒烟、空 env Compose 配置校验。最终结果在下方追加。

## 保留问题与边界

1. 原四次 Gemini 3.8 Flash 结果中的自行补写来历、首次自动行动与时间线不一致保留原记录；提示词不动，本轮零追加真实调用。不能据此承诺长篇一致性。
2. 原卡字体和图片内联后 HTML 约 15.55 MB，首次载入仍较大；本轮保真优先，未替换原素材或改动字体。
3. 建卡表单尚不自动保存；带入聊天后的浏览器草稿和服务端完成回合分别持久化。
4. Windows 只有本版本完整建立的拥有者证据可自动回收；旧锁或启动/关闭中断的不完整证据保持拒绝，需人工核对，不自动删除。
5. 插图真实供应商、长篇玩法和所有角色组合未追加验收；八插件回归使用桩服务。真正未知任务继续禁止自动重放。
6. 全局帮助截图存量问题不属于本卡，不修改无关文章、资产或测试；构建存在既有较大 chunk 提示。

## 交付文件清单

- `experiments/ai-rpg-engine/harry-potter-card/`：原文资源、原 UI 与授权素材、本地构建适配、冻结宿主与测试。
- `studio/server.mjs`、`local-owner*`、`illustrations.mjs` 与回归：入口、恢复、插图发布。
- `card-replica/lib/harry-potter-presentation.mjs` 与共享聊天接线：安全折叠显示，原文与历史不变。
- `client/src/pages/ArchiveCardPage.tsx`、RPG 回归、帮助内容与截图：统一故事馆与聊天入口。
- `docker-compose.rpg.yml`、`studio/Dockerfile`：默认关闭真实调用的独立服务接线。
- 本报告及 CARD、REAL_ACCEPTANCE、RECOVERY_DISPLAY_FIX、ILLUSTRATION_UNKNOWN_FIX：实现、历史证据、限制与回退。

不提交本地测试数据、旧真实存档、凭据、构建目录或日志。只创建 PR，不合并或部署；若回退，撤销接线和修复代码，保留所有存档与审计证据，不补发调用。

## 最终检查结果

- 通过：主前端最终构建；聊天前端本地构建与 Docker 内构建；作者卡 `build.mjs --check`；Compose 空 env 配置检查；最终镜像无网络安全冒烟。最终镜像 config digest 为 `23ea5a3710605dc9ced939e02d375c4727e928079d358478d7de6d95d4c9a980`（本地验收镜像，未发布）。
- 失败（主线既有）：`npm.cmd run verify:help-images` 12 处。已从 HEAD 原样提取帮助源码、图片与检查脚本到临时目录运行，基线与本分支的失败列表逐条一致：已有 JPG 格式不满足 PNG 校验、旧截图宽度、孤立图片。HP 新截图 143973 字节、1000×563、PNG、引用/基线/替代文本全部通过。
- 失败（主线既有）：帮助内容契约最终仍 17/18，唯一失败为上述 regen.png 基线不一致；未修改或跳过原断言。
- 构建提示：两个旧子前端 npm ci 均报告 2 个 high 依赖问题；锁文件未修改，本轮未展开依赖漏洞修复。大 chunk 与动态/静态混合导入提示也保留。
- 未运行：真实模型、真实图像服务、生产部署、仓库全量 Python 后端和全量前端；本任务改动位于独立 RPG Node 宿主与相关前端。
- 当前 `origin/main` 仍为 `a237f26f`，没有提交前基线漂移。

暂存审核：223 个文件（含 164 个字体分段），无测试数据/凭据/构建目录；逐文件暂存字节与工作树相同，凭据特征扫描零命中。普通 `git diff --cached --check` 对原素材和生成副本中的 CRLF、许可证原始行尾空白报错；为保留作者资源哈希而不格式化这些字节。工程代码与文档范围的差异检查通过；原素材由冻结哈希与生成一致性测试验证，不隐藏全局 whitespace 失败。

## PR #460 主线冲突同步

用户要求解决合并冲突；从干净的 c5b5ab0d 合入 origin/main@806d8e6e，包含西幻 #459 和故事馆封面 #461。11 个冲突集中在共享注册与路由，必须作为一致的跨层接线一起解决。保留双方卡片、插件和帮助文章，保留主线封面/品牌/西幻作品详情；补齐新 libraryCopy 的哈利波特文案和 14 卡计数。提示词、卡片原 HTML、冻结宿主和现有真实存档不改。相关 Node/Vitest、两处构建及冻结字节检查通过后提交合并并推送同一 PR；不部署、不合入 main、不追加真实调用。

合并回归结果：

- `node --test` 运行 illustration-persistence、illustrations、archive-plugins、local-owner、card-entry、branches-http、harry、harry-potter-presentation、custom-config，以及 elderland/elderland 和 elderland/control：最终 43/43 通过，含双方八插件与受控假服务。
- Vitest 单 worker 运行 HarryPotterCard、HarryPotterOutput、RpgWorkspace、RpgPluginsPage、RpgFreeOpening、RpgLibrary、RpgAuthoringEntry、HelpCenterPage：首次 172/177，修复自由开局 API 路由和新主线文案/数量断言后，失败所涉四文件 74/74 通过；其他四文件 103/103 已通过。没有删除、跳过或放宽测试。
- 聊天前端与主前端 `npm.cmd run build` 通过。主线既有帮助图片契约失败与构建告警仍按上文记录，本轮不扩展处理。
- `verify-replay.mjs merge-806d8e6e-replay` 通过：旧四轮复制件强杀恢复相同，36 份 dispatch 和 8 个冻结文件不变；真实调用 0、凭据读取 0。
- 哈利波特整个卡包相对 c5b5ab0d 无差异，作者提示词未改；新增差异仅为双方接线和主线故事馆展示适配。保留原预览证据，不将本次自动回归冒充新的人工界面全验收。
