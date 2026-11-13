# 西幻大世界 · 艾尔德兰

入口：`/rpg/elderland`；角色配置：`/rpg-app/elderland/?chat=1`；独立聊天宿主：`/rpg-app/elderland/chat/`。

参考作品为用户链接实际目标 `9861186f-8af4-411b-b771-07e45ee29386`。由该页面公开 GET 详情中的 `apps.description` 保存原始 HTML，`apps.summary` 保存完整作品介绍；没有使用链接显示文字中的另一张卡片 ID。

## 原文与素材

- `resources/original.html`：原版完整前端，未经改写；94741 个 Unicode 字符。
- `resources/prompts.json`：授权《西幻提示词.docx》原文，2843 段、66964 个 Unicode 字符。按 Word 文档顺序拼接段落，保留段内换行、制表符及空段，不 trim、不修订、不添加提示词。
- `resources/summary.json`：原版完整作品介绍，在会话导航的“作品详情”查看。
- `resources/MANIFEST.json`：DOCX、document.xml、system、HTML 和介绍文本的 SHA-256。
- `resources/ASSETS.json`：11 个原图 URL、文件名、媒体类型、大小和 SHA-256。
- `public/`：两张背景、九张地图和可复现生成的 `index.html`。图片不压缩、不裁剪、不重绘。

## 适配

`build.mjs` 在保留作者文案、十三页签、地区/NPC/出生地数据、表单、传承池、布局和动画的基础上，只作以下适配：

1. 将图片地址替换为本地明确列举的静态资源；不将约 30 MB 图片重复内联进 HTML。
2. 去掉原版放在 CSS 规则之后、浏览器本来就会忽略的 Google Fonts `@import`，保持原先实际使用的回退字体。
3. 添加“带入聊天”按钮，只创建会话和草稿，不自动发送。桥接沿用 opaque-origin sandbox、`event.source` 和卡片类型检查。
4. 开场支持键盘、二次点击跳过和减少动效偏好；补充控件标签与地图替代文字。原开场睁眼、模糊消退、渐隐、页签进入、卡片悬浮、传承抽取动效保留。
5. 用户自定义能力仅在 HTML 展示位置转义，角色预览仍保留原始文本。修复跨区域返回已渲染出生地时可能沿用另一个国家的问题，并显式传递能力点击事件。
6. 保留作者“复制角色卡”，新增超长草稿提示以匹配宿主 20000 字开局边界。

`assembly.mjs` 校验提示词哈希并按 system → 完整历史 → 当前用户输入装配。提示词只在服务端使用，不作为静态资源暴露。

## 验证

在仓库根运行：

```powershell
node experiments/ai-rpg-engine/elderland-card/build.mjs --check
node --test experiments/ai-rpg-engine/elderland-card/*.test.mjs experiments/ai-rpg-engine/studio/archive-plugins.test.mjs
npm.cmd --prefix experiments/ai-rpg-engine/card-replica run build -- --base=/rpg-app/earth/
npm.cmd --prefix client run test -- --run src/pages/RpgFreeOpening.test.tsx src/pages/RpgWorkspace.test.tsx src/pages/RpgAuthoringEntry.test.tsx src/pages/RpgPluginsPage.test.tsx
npm.cmd --prefix client run build
```

DOM 测试复用 `client` 已有 jsdom 开发依赖；没有新增生产依赖。详细结果见仓库根 `docs/ai-rpg-experiment/ELDERLAND_CARD.md`。

## 运行边界与回退

默认仅离线。实际模型通道使用独立 `RPG_ELDERLAND_CHAT_ENABLED=true`、`RPG_ELDERLAND_CHAT_CALL_LIMIT` 和已有受控服务配置；默认不会自动开启；用户授权的独立本机验收完成5次真实调用（4次成功、1次过滤），详见验收记录，未部署。聊天、摘要、记忆、导入导出与插画沿用既有插件授权机制。

所有新会话位于独立 `archive-chat/elderland/{offline,real}` 目录。未迁移旧数据，未修改共享存储或现有会话指纹适配器。回退时撤去本次卡片注册和新增目录即可；保留西幻会话目录供恢复，不需要回滚其他卡片数据。
