# 龙族大世界

原卡六页建卡界面与作者提示词的隔离装配。卡片 ID：`dragon-raja`。

## 入口与使用

- 故事馆：`/rpg` → 龙族大世界。
- 建卡与会话：`/rpg/dragon-raja`；独立聊天：`/rpg/dragon-raja/chat/:sessionId`。
- RPG 宿主直接预览：`/rpg-app/dragon-raja/chat/`；原卡面板：`/rpg-app/dragon-raja/`。
- 六页保留序章、时间线、125 位原著角色、言灵和武装、自创条目、八抽三天赋与档案复制。新增“带入聊天”，只创建空会话并保存可编辑草稿，发送前无模型请求。
- 复用模型选择、分支、历史窗口、自动总结 2.0、记忆宫殿、消息工具、记录导入导出、插画，以及自定义配置与公用 Mod。安装与授权仍按卡片、会话隔离。

## 原文和素材

龙族卡从 `resources/prompts.json` 读取固定提示词，正文原样采用审核稿《01-固定提示词.txt》。装配时按 `resources/MANIFEST.json` 校验 SHA-256。世界书条目的行号和片段继续引用 `resources/worldbook-source.json`，其中保存《龙族提示词.docx》的原始提取文本。

前端按用户实际超链接及已打开页面 `https://afengy.cash/zh/explore/installed/5892055f-8249-4ea2-9dda-9bf86b8806e5` 保存；其标题为“龙族大世界”。用户链接显示文字中的另一 ID 未作为来源。`resources/original.html` 保留原始 iframe 源码；`resources/ASSETS.json` 记录两张原始素材的 URL 与哈希。

`build.mjs` 从不可变原始 HTML 生成 `public/index.html`：原图内嵌供不透明来源的沙箱离线使用；移除原来位于规则后的无效在线字体导入；接入草稿消息桥；修复原著角色字段、标签双重切换和自定义内容重绘转义；补充键盘操作、焦点与减少动画偏好。修改不进入作者提示词。

```powershell
node experiments/ai-rpg-engine/dragon-raja-card/build.mjs
node experiments/ai-rpg-engine/dragon-raja-card/build.mjs --check
node --test experiments/ai-rpg-engine/dragon-raja-card/dragon.test.mjs experiments/ai-rpg-engine/studio/archive-plugins.test.mjs
```

## 运行与验证边界

默认是离线预览。真实模型需在现有受控网关环境显式配置 `RPG_DRAGON_CHAT_ENABLED=true`、`RPG_DRAGON_CHAT_CALL_LIMIT`、`RPG_CONTROL_URL` 与 `RPG_S2S_TOKEN`；沿用受控模型选择、回执核验、独立额度与未知结果不重放机制。不得将凭据放入前端。

离线测试涵盖哈希、可重复构建、两轮会话与重启恢复、跨卡隔离。2026-11-06 经用户追加授权，使用 Gemini 3.8 Flash 完成 1 轮中性开局、2 轮指定样例，及样例每轮的自动小结和记忆宫殿更新，共 7 次真实请求；原始 system 与指定开局文本保持不变。实际回执、上下文注入与验证边界见 `ACCEPTANCE.md`。原图在严格沙箱内显示，未添加同源脚本权限。

存档目录为 `archive-chat/dragon-raja/{offline,real}`。现有卡片的宿主和运行指纹保持不变，无数据迁移。回退时撤销本卡入口与路由改动，并保留该目录以备恢复；不删除任何已有卡片存档。
