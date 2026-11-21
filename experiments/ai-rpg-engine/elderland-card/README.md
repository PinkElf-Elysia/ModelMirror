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

## 世界书与分类触发库

新档由 `archive-host.mjs` 调用 `worldbook.mjs` 装配。常驻部分为 18,773 个 Unicode 字符，保留扮演框架、通用规则和世界观总览。固定正文与复核版《西幻固定提示词》逐字一致，仅略去冻结原文开头的分隔线和空行（第 1–2 行）。`worldbook-sources.mjs` 从冻结的提示词和出生地前端取材，覆盖八个区域、813 个条目。来自提示词的段落按原行位插入，原生前端独有的命中片段插在对应区域总览前。

名称库按区域、地点、势力、人物、任务列中文规范名，跨区同名加区域。八个区域名常驻，其他名称随所在地和主动询问的区域装入。别名、英文名和额外触发写法保存在世界书配置中。出生地表单的“北境·永冬荒原”对应“霜寒领”总览，荒原地点详目单独触发。

### 冬堡城离线验收

三轮从原生前端生成的出生地档案起步，复用同一份临时新档，每轮间重启宿主。通过聊天入口记录的完整消息与审核稿逐条一致。

| 回合 | 正文命中 | 条目数 | 系统提示词字符数 | 所在地 |
| --- | --- | ---: | ---: | --- |
| 冬堡城开局 | 北境总览、冬堡城原提示词及出生地资料 | 3 | 20,219 | 霜寒领·冬堡城 |
| 去冒险者公会问任务 | 冬堡分部、会长弗蕾雅·银弓及六类本地任务 | 12 | 20,427 | 霜寒领·冬堡城 |
| 向公会会长打听风暴群岛 | 跨区只命中西境总览，西境任务和地标详目均未装入 | 4 | 21,456 | 霜寒领·冬堡城 |

前两轮均只命中北境或共通资料。另核对了 81 个原生出生地、1,073 组条目触发写法，以及提及、否定、出行计划、问路、英文别名、指代追问、结束话题、实际抵达八项对照。

旧档样本由拆分前提交 `ee55d57fb019190e0fca0ca0977e1ceef00c0ee1` 的宿主创建并发送首轮，再由新宿主从同一临时目录读取和续玩。运行时绑定、首轮内容均一致，两轮系统提示词的 SHA-256 均为 `bbb79d8bb999d065b6a33e231f58e459e5b9bdabc1493ad382c9310131e0f75c`。本轮真实模型调用为 0。

[定稿离线验收记录](./worldbook-acceptance.json)包含各轮命中条目及其来源、所在地、查阅区域、消息摘要和旧档对照。已在合入前复跑并核对全部三轮的消息数组。请求封装包含每次回放生成的标识，比较两次运行请使用 `messagesSha256`。

复现时在本模块和客户端安装锁定依赖，并准备已安装本模块依赖的可信拆分前工作树。从仓库根执行下列命令，第一个参数指向旧工作树的引擎目录，第二个参数为尚不存在的导出目录。导出包含固定提示词、三份名称库视图、世界书、三轮完整请求及验收记录。

```powershell
node experiments/ai-rpg-engine/elderland-card/worldbook-acceptance.mjs <拆分前引擎目录> <新导出目录>
```

回退本次变更时保留存档目录。带世界书绑定的新档须使用匹配的宿主版本续玩；旧绑定继续使用冻结的整份提示词。

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
