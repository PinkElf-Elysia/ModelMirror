# 斗罗大陆 · 魂师觉醒台

原站卡片：<https://afengy.cash/zh/explore/installed/5dab2efe-0bf7-4790-8087-c8e7a06a3d54>。

本目录提供原卡开局界面、本地媒体和作者提示词装配。主入口为 `/rpg/douluo`，独立聊天为 `/rpg/douluo/chat/:sessionId`；内部宿主为 `/rpg-app/douluo/chat/`。新卡使用独立 `douluo` 存档空间，复用公共聊天、记忆插件、自定义配置和 Mod 面板。

## 来源与保持范围

- `resources/original.html`：从用户打开的原站内嵌卡片取得的完整 HTML，统一 LF 换行，保留所有文案、数据和动画实现。
- `resources/prompts.json`：用户提供的《斗罗提示词.docx》按 OOXML 正文段落顺序机械提取，保留空段、制表与段内换行，不改写提示词。
- `resources/MANIFEST.json`：源 DOCX、提取文本和原始 HTML 的 SHA-256；装配时验证提示词摘要。
- `resources/assets.json`：逐项记录来源 URL、大小、媒体类型和 SHA-256。46 张人物图、5 首原音乐、5 段原视频及 6 个字体文件均已本地化，无需音乐替代文件。
- `public/index.html` 是 `build.mjs` 的可重复生成产物。不要直接编辑它；修改适配脚本后重新构建。

保留开场雷电/昊天锤/碎屏、蓄力觉醒、武魂弹层、等级与魂环、魂技增删、魂骨择取与自定义、时间线、角色档案/额外角色、功能开关、开场白、实时投影、世界设定、46 人图鉴与翻面详情、地图热点和二级文案、降低消耗说明、自定义配置说明、音乐切歌/视频/弹幕/星尘开关。

适配仅包括媒体本地化、宿主聊天入口、键盘焦点样式与减少动态效果样式，以及修复原卡 `BG_MUSIC_URL` 未定义和关音乐后切歌仍播放的问题。原卡“复制”继续仅复制档案；新增“以此档案进入聊天”将档案放入可编辑输入框，创建存档不调用模型。

## 验收命令

从仓库根目录运行：

```powershell
node experiments/ai-rpg-engine/douluo-card/build.mjs
node --test experiments/ai-rpg-engine/douluo-card/douluo.test.mjs experiments/ai-rpg-engine/studio/card-entry.test.mjs experiments/ai-rpg-engine/studio/free-opening.test.mjs experiments/ai-rpg-engine/plugins/custom-config/custom-config.test.mjs experiments/ai-rpg-engine/plugins/mod-panel/mod-panel.test.mjs
npm.cmd run build --prefix experiments/ai-rpg-engine/card-replica
npm.cmd run build --prefix client
```

从 `client` 目录运行：

```powershell
npm.cmd exec -- vitest run src/pages/RpgWorkspace.test.tsx src/pages/RpgFreeOpening.test.tsx
```

独立离线宿主（已安装 RPG 包与 card-replica 依赖且完成构建）：

```powershell
node experiments/ai-rpg-engine/studio/server.mjs
```

访问 `http://127.0.0.1:18420/rpg-app/douluo/chat/`。完整故事馆导航由主前端提供。容器构建已包含本目录；真实路由仍需显式配置 `RPG_DOULUO_CHAT_ENABLED=true`、`RPG_DOULUO_CHAT_CALL_LIMIT` 及既有受控网关认证。Compose 默认关闭新卡真实调用，未修改共享栈运行状态。

## 验收记录（2026-11-06）

自动化验证覆盖：提示词/媒体完整性、完整历史装配、沙箱/CSP、音视频 Range 请求、null-origin 公共媒体与受保护 API 的区分、真实传输适配器的两轮 mock、公共 Mod 注入、专用 Mod 不兼容拒绝、重复请求不重放和各卡存档隔离。另以既有前端测试覆盖新卡开局草稿、发送与存档路由。

内置预览器人工操作覆盖：开场破幕；长按觉醒及结果弹层；百年魂环；增加魂技；头骨选择；角色姓名/年龄/开场白汇总；人物图鉴目录跳转、千仞雪图片和详情翻面；天斗城热点及详情；原音乐和视频加载播放；档案进入独立聊天；离线发送、刷新恢复、公用 Mod 获取与启用；390px 窄屏和桌面布局。完整页面的二级文案由原始 HTML 与构建校验保留，不将抽样操作声明为每个动画阶段的逐帧验收。

已开展授权的 Gemini 真实验收，结果见 `live-acceptance.json`：中性轮传输完成，但选项出现成人向分支，未通过中性内容验收；14 岁样例在用户确认的非性化会话约束下仍被上游以 `PROHIBITED_CONTENT` 拒绝，没有正文。保留宿主 unknown 状态和回执，不重发。第二轮样例及总结/记忆模型调用未执行，插件启用不等于真实调用通过。总共发出 2 次请求，0 次重试；原始输出和运行目录留在未跟踪的 `.rpg04-work/douluo/live-20261106/`。未部署共享栈或进行生产验收。发布素材时应保留现有来源与授权范围，不推定本地复刻授权等于额外公开发布授权。

后续用户将样例年龄改为 18 岁并授权重新调用，独立批次结果见 `live-adult-acceptance.json`。在保留非性化会话约束、作者提示词不变的条件下，2 轮剧情、2 次自动总结 v2 和 2 次记忆宫殿更新全部完成，共 6 次 Gemini 请求、0 次重试。第二轮请求实际带入首轮小总结及召回的两条记忆，未重复携带已总结的首轮原始对话；最终存有两条小总结，记忆处理至第 2 轮，食宿事件条目更新为版本 2。正文与全部行动选项人工核对为非性化救助、问询与学院杂务剧情。六份正文/SSE 的 SHA-256 与回执一致。

本次仅通过上述有限范围：第二条小总结把时间写成“未知”，角色记忆仍保留 2644 年；未触发大总结合并、重新生成或长对话路径。此前中性轮内容未通过的结论及 14 岁样例拒绝证据均保留，不将本次成功归因为已证明年龄是拒绝原因。新批次原始记录位于未跟踪的 `.rpg04-work/douluo/live-adult-20261106/`。

用户已在只读离线回放中验收两轮成年样例，并授权本卡收尾提交 PR。故事馆新增斗罗“新世界”入口，七卡入口与续玩区域保留；桌面和 390px 布局、开局档案进入独立聊天及帮助教程重放记录见 `docs/help-center/evidence/douluo-6977d86c/README.md`。本次收尾未追加模型调用。

## 隔离与回退

卡片运行在 `allow-scripts` 的不透明源沙箱内，禁用网络连接；仅显式允许的静态媒体接受跨源 GET，聊天 API 继续执行来源校验。父页面验证消息来源窗口、`null` 源和卡片消息类型。源提示词不通过静态路由暴露。

停止使用时关闭新卡真实路由，移除故事馆注册即可；不删除或迁移存档。已有其他卡片目录、存档和共享数据不变。
