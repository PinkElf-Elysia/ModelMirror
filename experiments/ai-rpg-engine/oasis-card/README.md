# OASIS！头号玩家！（游戏人生）

故事馆入口为 `/rpg/oasis`。[原卡](https://afengy.cash/zh/explore/installed/e043b6b9-8430-4ed1-9035-dd6b4eb71cf1) 的开场 HTML 保存在 `resources/original.html`。五个页签、折叠说明、双身份表单、实时预览和复制弹窗沿用作者实现。作品详情页的 400 × 300 PNG 作为故事馆封面。Orbitron 和 Noto Sans SC 在本地供应，后者为覆盖原卡文案的可变字体子集。两款字体的 OFL 许可证位于 `resources/licenses/`。

`resources/system.txt` 按作者 Word 文档的段落顺序保存正文，段间使用 LF，保留空段。875 段，共 15,548 个 Unicode 字符。来源和 SHA-256 记录见 `resources/source.json`。宿主在装配时验证正文摘要。角色配置页的「带入聊天」将预览写入当前浏览器的新会话草稿。玩家可继续编辑，点击「发送」才开始剧情。

## 本机验证

在 RPG 模块根目录运行卡片测试和聊天前端构建。在仓库的 `client` 目录执行 `npm.cmd run build` 生成外层故事馆。两个前端构建完成后可启动独立预览。

```powershell
node --test oasis-card/card.test.mjs
npm.cmd --prefix card-replica run build -- --base=/rpg-app/earth/
node oasis-card/preview.mjs
```

[离线预览](http://127.0.0.1:18995/rpg/oasis) 使用本机端口 18995 和 18996。存档位于本工作树的 `.rpg04-work/oasis-preview/`。容器中本卡使用 `RPG_OASIS_CHAT_ENABLED` 和 `RPG_OASIS_CHAT_CALL_LIMIT`，默认关闭真实调用且额度为零。

2026-11-21 离线验收覆盖五个页签、桌面及 390 × 844 视口、原封面、角色预览、复制弹窗和刷新恢复零回合草稿。两轮模拟网关请求逐项核对了系统正文、玩家输入和历史。若撤回本卡，可撤回本卡目录及对应故事馆、路由、宿主和 Compose 注册，保留 `archive-chat/oasis/` 存档。接入无需数据库迁移。

## Gemini 3.8 Flash 两轮实测

**验收通过（2026-11-21）。** 用户已复核两轮真实剧情，接受下文记录的三处偏差。后续沿用本次实测的作者提示词原文。

2026-11-21 从角色配置页填写“霓虹拾荒者／林岚”，带入聊天后核对了草稿，再由玩家侧发送。会话 `1fe0b7a1-6795-48c9-a62d-c34104a2008a`。每轮均由 `google/gemini-3.8-flash` 正常停止，重试 0 次。第一轮用量为 9,582 输入、6,616 输出，第二轮为 14,617 输入、4,853 输出。两轮系统正文与作者文本逐字相同。第二轮完整带入第一轮的玩家输入和模型原始回复。两轮 SSE 文本均与磁盘存档和会话回读结果相同。

开局在比波普巷遇到磁带客。续轮问曲名和播放器来历后，对方自报莫里森，ID 为“废铁磁带”。林岚留在长椅旁，余额维持 1,000 C，好感从 22 升到 35。正文将衣袖的“补丁”误写为“布丁”，并把《Space Lion》与第 24 集的告别场景混用。开局另有模型代玩家决定穿过广场、寻找乐声的叙述。

本机验收材料位于 `.rpg04-work/oasis-real-20261121-a/`，其中 `turn-1-story.txt`、`turn-2-story.txt` 为供阅读的剧情正文，`verified-1.json`、`verified-2.json` 为校验记录。各轮请求、原始回复、SSE 和回执位于 `archive-chat/oasis/real/dispatches/oasis/slot-{1,2}/`。
