# 特殊 CG 与剧情回顾

本批从主线 `6aa5924d` 开始，在创作模块的 CG 设置和实际对话内增加收藏。素材、角色和剧情均使用本地原创中性样例。原站作品和截图仅作为界面观察材料。

## 作者和玩家的操作

原站作者页实操确认，常规 CG 的「设为特殊CG」会展开 CG 名称、默认关闭的「未解锁时显示名称」和默认关闭的「解锁提示」。打开提示开关后出现独立文字输入框。页面展示特殊 CG 个数，上限为 20。玩家页实查确认作品标题、解锁进度、分类与「神秘CG」卡背。另在原站实际打开同一画廊中的纯文字提示、大图与剧情回顾。其进度为 1/2，回顾中可滚动阅读对话，用户提供了触发后的一问一答参考。原站测试作品关闭自动保存，用于核验的临时行重新载入后恢复为 0/20。

## 本地规则及回退

- 以固定作品版本、当前路线中已完成回合的宿主选图记录为依据。素材别名、HTML 类名和关键词由作者定义。单纯展示立绘或在编辑器回放样例不会产生收藏记录。
- 同一特殊 CG 重复出现，回顾保留第一次实际携带该图的完整回合。分支只继承分支点之前的回合。跨独立会话共享与原站内部去重规则未经确认。
- 旧配置没有特殊 CG 字段时保持原样。编辑状态和运行时分开，原站实测，已标记的条目切到概率模式仍计入 20 项上限，切回后标记保留。对保留标记的概率条目，本地收藏其首次实际展示的选图；原站概率命中后的收藏细则未做生成实测。图片和原文随作品版本与会话保存，不写入模型上下文。
- 使用原创「雾港灯塔」作品和离线固定回复，从空会话的 0/2 走到 1/2，另一张仍锁定。桌面和 390px 均实点线索、查看、回顾。实测手机画廊内容宽 365px，页面宽 390px；Esc 返回对应卡片按钮。聊天插图可点击放大。自动化覆盖 20 项上限、作品包跨目录导入、同回合重放、分支隔离、未命中与原操作恢复。
- 回退可先停用作品内的特殊标记并发布新版本。已有作品包、历史版本、会话、原文和账本保留；旧宿主读取带新字段的包需在副本上验证。


## 本批验证记录

实现分为四个可审阅部分。

1. 作者配置与旧数据兼容。contracts.mjs、cg-ui.mjs、cg-ui.css、cg.mjs、cg.test.mjs。
2. 已落盘回合与路线投影。turn-display.mjs、turn-playback.mjs、turn-playback.test.mjs。
3. 玩家界面与隔离展示。special-cg-ui.mjs、playback-ui.mjs、playback-ui.css、presentation.mjs、server.mjs。
4. 帮助、合同测试和调用来源审计。browser.mjs、creator-guide.html、special-cg.test.mjs、本文及 docs/audits/provider-coverage.json。审计表只更新四个已登记模块的内容指纹。

自动化在独立工作树执行。npm.cmd run test:authoring 为 853/853。随后微调提示弹窗、放大图标和导入导出测试，最终六个受影响测试文件再次运行 85/85。调用来源审计为 82 项入口、979 项候选，现有审计测试 26/26。RPG UI 与主前端各自执行 npm.cmd run build 成功，主前端仍提示大于 4500 kB 的分块。

从 experiments/ai-rpg-engine 运行最终专项。

    node --test --test-concurrency=1 authoring/special-cg.test.mjs authoring/cg.test.mjs authoring/cg-ui.test.mjs authoring/turn-playback.test.mjs authoring/presentation.test.mjs authoring/turn-display.test.mjs

从仓库根目录运行来源审计。

    python -B scripts/check_provider_coverage.py
    python -B -m unittest discover -s server/tests -p test_provider_coverage.py

浏览器使用 fixture/story 固定回复，实际点击作者开关、模式切换、保存，并在重新打开的页面确认字段、解锁状态和提示。验收素材和截图只保存在忽略目录 .rpg04-work/special-cg-dev/，仓库新增文件均为文本。源站图片、截图与作者角色内容未导入。图片、原始回合及制作的作品包只在本地使用。

真实供应商生成和人工验收不在上述通过项中。变量面板随后单独完成。模型参数扩展、并行对照继续按用户指定顺序推进。
