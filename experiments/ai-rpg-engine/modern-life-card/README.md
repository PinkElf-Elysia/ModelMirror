# 现代超真实人生

故事馆入口为 `/rpg/modern-life`，卡片聊天入口为 `/rpg-app/modern-life/chat/`。系统提示词、预算、浏览器草稿及服务端会话均使用本卡自己的标识。

## 来源与适配

参考卡为[现代超真实人生！【重制版】](https://afengy.cash/zh/explore/installed/f3bb33a9-d794-4bfe-ac36-fca3fcd1af76)。原站公开建卡器保存在 `resources/original.html`。加载和跳过动画、五步导航、世界设定、主角标签参考、NPC 列表和实时预览均使用原作者的界面与逻辑。

`resources/system.txt` 由授权文档《现代002提示词.docx》机械提取。依 Word 正文段落顺序拼接文本节点，保留制表符、段内换行及空段，段落以 LF 分隔。文档、提取正文、原界面和封面的 SHA-256 见 `resources/source.json`。提示词仅由服务端加载。

`view.mjs` 对原 HTML 作锚点校验，替换展示名与本地字体，接入沙箱内的删除确认和聊天入口。最终预览可以复制，也可以带入新会话作为可编辑草稿。玩家手动发送后才生成第一回合。两种世界模式、NPC 的跳过和多人物编辑沿用原版。

封面取自原卡。Playfair Display 保留官方字库，Noto Serif SC 和 Noto Sans SC 使用涵盖界面文案的本地可变字体子集。玩家自行输入的其他字符由系统字体回退。三款字体来自 [Google Fonts](https://github.com/google/fonts/tree/main/ofl)，许可证随附于 `resources/licenses/`。

## 真实模型验收

Gemini 3.8 Flash 的三轮开局、对话与重启读档均已保存。三处实测偏差与逐轮用量见[验收报告](LIVE_ACCEPTANCE_2026-11-21.md)。

## 本地验证

在 `experiments/ai-rpg-engine` 目录运行：

```powershell
node --test modern-life-card/card.test.mjs
npm.cmd --prefix card-replica test
npm.cmd --prefix card-replica run build -- --base=/rpg-app/earth/
node modern-life-card/preview.mjs
```

Windows 独立预览地址为 `http://127.0.0.1:18986/rpg-app/modern-life/chat/`。预览将数据写入本工作树被忽略的 `.rpg04-work/modern-life-preview/`。正常容器入口由 Studio 装配，真实模型启用开关为 `RPG_MODERN_LIFE_CHAT_ENABLED`，额度为 `RPG_MODERN_LIFE_CHAT_CALL_LIMIT`，默认分别为 false 和 0。

回退时撤回新卡目录及故事馆、聊天路由、Studio 和 Compose 中对应的注册项。本次接入无需数据迁移。保留 `archive-chat/modern-life/` 可供恢复本卡存档。
