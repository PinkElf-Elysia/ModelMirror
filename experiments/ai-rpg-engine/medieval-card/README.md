# 中世纪真实人生卡片

入口：`/rpg/medieval`；独立聊天：`/rpg-app/medieval/chat/`；原卡构建器：`/rpg-app/medieval/`。

## 来源与保真

用户提供完整作者授权，系统提示词来自 `中世纪提示词.docx`，仅按 DOCX 正文段落顺序机械提取为 UTF-8/LF：1071 段、29062 个 Unicode 字符。没有进行语义复核、改写、补充规则或追加其他卡片的输出后缀。

界面以用户链接实际 href 对应的 [原卡](https://afengy.cash/zh/explore/installed/1389495e-063a-470d-b625-042758642711) 为准。`resources/original.html` 保存原始构建器；`resources/MANIFEST.json` 记录文档、系统文本、原界面、图片与字体的 SHA-256 和来源。

六个页签、4—15 世纪的时代资料及折叠文案、时代联动、职业搜索、技艺等级、天赋抽取与最多选三项、角色预览复制，均保留原 HTML 和原第一段脚本。背景图使用原图，开场眼睑、模糊渐清、淡出和天赋抽取动效保留原参数。测试逐字比较原脚本、原样式和正文结构，仅排除明确的本地适配。

## 本地适配

- 两张背景图片与三组字体全部本地化。字体使用原字体族、字重和样式，子集涵盖原卡文案和新增按钮；用户自填的额外字符由系统字体回退。字体许可证随资源保存。
- 增加“以此角色进入聊天”，通过来源窗口校验的消息桥接送入现有聊天草稿；创建草稿不调用模型，首次发送才提交用户可编辑的内容。
- 补充键盘操作、表单标签、折叠状态、焦点样式和减少动态效果偏好。
- 原卡运行于不含同源权限的 sandbox，禁止连接外部服务。只有明确列出的图片、字体、CSS 可供不透明框架加载；系统提示词不通过静态路由暴露。
- 新卡独立目录、预算、传输快照与运行时指纹；接入既有会话、记忆、分支、Mod、导入导出及插画机制，不改写旧卡存档。

## 运行与控制

默认离线演示。已有 Studio 启动器识别 `RPG_MEDIEVAL_CHAT_ENABLED=true`、`RPG_MEDIEVAL_CHAT_CALL_LIMIT`，并沿用 `RPG_CONTROL_URL`、`RPG_S2S_TOKEN`。启用真实调用仍须有对应授权和预算；初次复刻阶段没有执行真实模型/图片调用；后续经用户授权完成三轮真实剧情验收，见下方记录。

容器构建已加入本卡 COPY；尚未合并或部署。本地验收使用独立数据目录，不与现有运行环境共用存档。

## 验证（2026-11-13）

在模块目录运行：

```powershell
node --test medieval-card/card.test.mjs
node --test --test-name-pattern="^medieval" studio/archive-plugins.test.mjs
npm.cmd --prefix card-replica test
npm.cmd --prefix card-replica run build
```

分别通过 6 项卡片测试、1 项八插件集成测试、80 项现有卡片前端测试，以及卡片前端类型检查和构建。新增测试覆盖原文与资源哈希、样式/脚本保真、公开资源边界、两轮模拟请求、幂等、Mod、会话隔离、重启恢复和插画改写预算。

主站 `client` 目录：

```powershell
npx.cmd vitest run --configLoader runner --maxWorkers=1 --fileParallelism=false src/pages/RpgAuthoringEntry.test.tsx src/pages/RpgWorkspace.test.tsx src/pages/RpgFreeOpening.test.tsx
npm.cmd run build
```

75 项通过，生产构建通过。现有大体积 chunk 警告保留。

扩展服务端回归：

```powershell
node --test --test-concurrency=4 medieval-card/card.test.mjs jinyong-card/card.test.mjs studio/card-entry.test.mjs studio/archive-plugins.test.mjs studio/free-opening.test.mjs studio/illustrations.test.mjs studio/output-edits.test.mjs plugins/mod-panel/mod-panel.test.mjs
```

首次运行 38/39 通过（当时新增卡片测试为 5 项、插件矩阵尚未加入 medieval）。唯一失败是既有 `jinyong-card/card.test.mjs:88` 的传输快照等同检查：共享 `studio/memory-budget.mjs` 已新增 comparison 能力，金庸快照没有该扩展。用 `git show HEAD:` 直接核对基线同样存在差异；本任务未修改这些文件或削弱测试。之后新增的第六项卡片测试和 medieval 八插件用例独立通过。

浏览器人工核验：桌面、390×844 窄屏；开场与六页签；14 世纪折叠内容及 1337 事件联动；角色字段、铁匠职业搜索、锻造精通；八项天赋抽取、最多三项；预览生成；角色草稿交接、离线发送与历史读取；主站故事馆新卡和会话深链接。初次复刻阶段未进行真实模型叙事质量验收。后续真实调用、存档恢复及已观察到的模型偏差见 [真实验收记录](LIVE_ACCEPTANCE_2026-11-13.md)。

## 文件范围与回退

新增本目录；修改主站 RPG 入口及其测试、`card-replica/src/{entry,presentation,session-client,xingjian}`、Studio 路由/插画选择器/容器 COPY/测试，以及自定义配置路由白名单。

无数据库迁移、无新增依赖。回退时撤回本卡目录及这些注册差异即可；保留本卡会话数据目录供恢复，不删除或更改其他卡片数据。初次复刻及真实验收基线为 `b4080f2c`。提交阶段已整合到最新主线 `4627d73d3dbcfab25be6c5df218633999b0d8f54`，分支 `codex/rpg-medieval-pr`，保留统一故事馆、AI 娘及本地发布入口。最新验证、帮助截图与既有失败见 [PR 验收证据](../../../docs/help-center/evidence/medieval-4627d73d.md)。
