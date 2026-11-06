# 金庸大世界复刻

来源为用户已打开的原站卡片：
https://afengy.cash/zh/explore/installed/529c4fe0-dec0-4cbf-948d-5ed9341afd80

原请求中的链接与已打开卡片不一致；以页面标题和实际打开的金庸卡为准。

## 资源与入口

- `resources/system.txt`：从用户提供的《金庸提示词.docx》机械提取，1041 段、20552 个 Unicode 字符，保留空段落和原文，不作语义修改。
- `resources/original.html`：原卡 iframe 内容，按 LF 保存。
- `resources/MANIFEST.json`：文档、提示词、原 HTML、本地资源的 SHA-256 与提取方式。
- `public/index.html`：保留七步建档、15 部作品、五大融合区域、武学和命数逻辑。仅本地化背景、移除浏览器忽略的字体导入、增加明确的进入聊天按钮。
- `/rpg/jinyong`：故事馆内入口；`/rpg/jinyong/chat/:sessionId`：独立聊天。
- `/rpg-app/jinyong/`：原卡建档页；`/rpg-app/jinyong/chat/`：独立宿主。

「复制人物帖」仍只复制文本。「以此人物帖进入聊天」新建空会话并把人物帖放入可编辑草稿；点击发送才生成回复。打开或刷新页面不自动调用模型。

## 运行边界

`host.mjs` 使用独立 `jinyong` 身份、存档目录、预算和回执。新增卡片所需的传输快照见 `transport/README.md`；旧 Earth/God 及既有档案宿主的指纹文件保持不变。新卡宿主同时绑定自己的适配器和传输文件哈希。

默认离线。真实调用需显式设置 `RPG_JINYONG_CHAT_ENABLED=true`、正数 `RPG_JINYONG_CHAT_CALL_LIMIT` 以及既有受控服务地址/令牌；凭据不进入浏览器。已在独立金庸容器完成用户授权的真实调用和连续性复测，结果与限制见验收记录；未更新共享栈。

沿用档案卡插件：模型选择、分支、历史窗口、自动总结、记忆宫殿，以及配置启用的消息工具、聊天记录和场景插画。公用 Mod 按已有 Mod 面板装配；古代专用 Mod 不向本卡开放。自定义配置仍遵循既有档案卡兼容范围。

## 验证

在 `experiments/ai-rpg-engine` 目录运行 `node --test jinyong-card/card.test.mjs studio/card-entry.test.mjs plugins/mod-panel/mod-panel.test.mjs`。

验收记录与本地预览说明见仓库根 `docs/ai-rpg-experiment/JINYONG_CARD.md`。
