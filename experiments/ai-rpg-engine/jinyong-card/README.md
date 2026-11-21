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

## 定稿世界书

十五部作品沿用作者建档页的 45 个阶段，融合模式按区域检索。固定提示词为 14,659 字，本次《射雕英雄传》开局的分类中文触发库为 286 字。人物、门派、地点、武学与剧情条目保留原文坐标，同名标作品。目录位于原稿第 773 行之前，原系统词条目在原位装入；建档表单的单作品资料补在第 845 行之后，融合资料补在第 1041 行之后。

| 离线场景 | 实际请求核对 |
| --- | --- |
| 《射雕英雄传》前期，大漠集市开局 | 命中作品、阶段「大漠少年初入江湖」和「骑射本领」。系统词 15,008 字。 |
| 重启后向丐帮弟子打听郭靖和打狗棒法 | 只新增《射雕》的郭靖、丐帮和打狗棒法资料。系统词 15,041 字。 |
| 相同查阅语句，分别选择《射雕》前期、《神雕》前期、《神雕》后期 | 前两组均未装入原稿第 847 行的断臂后剧情，第三组命中该段。 |
| 旧宿主建档并完成首轮，由新宿主打开同一存档续玩 | 首轮历史逐项一致；两轮完整消息与已审核旧档证据的散列一致，两轮系统词均与冻结原稿一致。 |

两轮输入、命中条目、三组对照和旧档散列见[离线验收证据](./worldbook-acceptance.json)。以下命令在 `experiments/ai-rpg-engine` 中运行；第二条命令的参数指向已安装项目依赖的拆分前基线模块目录。回放仅使用临时存档和本地离线宿主。

~~~sh
node --test jinyong-card/worldbook.test.mjs jinyong-card/continuity.test.mjs studio/speech-compatibility.test.mjs jinyong-card/card.test.mjs
node jinyong-card/worldbook-acceptance.mjs "<基线工作树>/experiments/ai-rpg-engine"
~~~

上述测试合计 24 项通过，1 项既有失败。测试 `new-card transport retains the reviewed budget and receipt logic` 对比金庸卡的历史传输快照与共享实现，在 `controlled-provider.mjs` 断言失败。已在 `main@429db774` 复现，六份被比较的传输文件与基线逐字节一致。跨版本回放另行通过，真实模型调用为零。
