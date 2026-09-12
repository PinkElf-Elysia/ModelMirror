# RPG05 检出与复验

本轮独立界面采用 RPG04 合并基线 81fc14f6e0dada2447a63c1e532ee55b1f914ded。RPG05_PUBLICATION.json 绑定可提交文件的 Git blob；原型原件、运行来源、原始模型回执和会话仍由各自私有冻结记录绑定。Git 字节检查不代替真实模型或人工质量验收。

## 本地启动

在 experiments/ai-rpg-engine 目录，使用 package.json 声明的 Node/npm 版本：

```powershell
npm.cmd ci --ignore-scripts
npm.cmd --prefix ui ci --ignore-scripts
npm.cmd run build
npm.cmd run rpg:ui
```

打开 http://127.0.0.1:18405/ 。该入口明确使用 mock，不会请求真实模型。数据写入该检出自己的 ui-host/.local/，避免复用正在运行的人工实例或共享服务。新建旅程依次填写角色、模式与世界、身份与天赋、核对开局；缺少兼容场景时先修正配置。普通输入为行动，/对话 和 /查询 明确区分对话与查询。纸飞机发送；生成中可停止。成功回复自动保存，重新生成失败保留旧回复，删除仅撤回最新回合，详情默认收起。

为保留人工冻结，原运行工作区的4个界面源码及1份历史说明仍保留末尾空行；只有待提交索引移除了这些空行，原始字节和hash另存。不要用 `git add -A` 覆盖已整理的索引。发布复验在独立检出中执行；原运行工作区这5项差异是刻意保留的运行字节，不能标为提交检出通过。

8个既有CRLF文件通过精确路径 `.gitattributes` 识别CR行尾，同时保留全部默认空白检查；不做text/eol转换或配置clean filter。

不要在正在保留的18409人工运行目录重装依赖或重建产物；它有独立的357项原始字节冻结。真实模型入口与5次用户专用额度按 RPG05_MANUAL_HANDOFF.md 使用；此说明不给检出者新的模型派发授权。

## 复验命令

```powershell
node scripts/verify-rpg05-published.mjs --check-only
node scripts/verify-rpg05-published.mjs
```

第一条只读检查HEAD、暂存区、工作区的发布文件集合、模式、Git blob、固定基线及旧冻结文件；第二条再运行旧216、RPG04非HTTP125、本轮宿主、UI、typecheck和build，日志写入本检出 .rpg04-work/。第一次运行前安装独立依赖。脚本接受固定基线的后续提交和detached PR检出；它不改原始 check-boundary-rpg05.mjs 或 verify:rpg05，它们继续保留开工HEAD和本机原型原件检查语义。

尚未提交时可额外传入 `--candidate` 检验工作区候选；回执明确记录 commitVerified=false，不能当作已提交版本通过。默认模式拒绝HEAD或暂存区差异，快照自身与研究MANIFEST也需已提交且无改动。

完整历史 Provider、独立 HTTP harness、人工质量及PNG归档单列。发布复验不联网调用Provider，HTTP套件未运行时明确标为未运行。Git行尾归一化与工作区原字节不同会单列，不得用于更改或重建现有人工运行冻结。当前门禁与授权以 RPG05_STATUS.json、RPG05_PR_GATE_REVIEW.md 为准；其他轮次和早期记录保留其历史时点。

## 人工与合并

用户已授权完善后的提交与PR；本次已补齐7张经用户授权无损裁切的实机PNG，并登记原图与裁切图hash，可用于草稿PR证据。人工验收前不得合并。此前PNG缺口与截图接口失败记录保留，不改写为Agent成功截图。模型STM累积、天赋效果及正文/信息栏一致性尚待实案确认，原失败和旧hash保留。新源码复验不能追认旧真实回合为当前修复版的Provider实测。

回退仅撤回本轮界面/宿主与获批结构化输出增量，保留会话和账本。正在保留的人工服务不得用旧二进制直接降级；其来源标记迁移后的恢复方式以人工交接说明为准。无自动合并、部署或发布。

## 实机截图的未遮挡局部

以下图片由用户提供的原图无损裁切而来。只删除包含桌面宠物、通知或悬浮控件的区域，不缩放、不补画、不改正文。7张裁切图均逐像素等于原图对应区域，并已目视检查；原图私下保留，原始hash、裁切坐标和当前hash见 [截图登记](RPG05_SCREENSHOTS.json)。图片只证明可见局部，不代表完整页面或用户质量验收。

| 页面 | 截图及可见范围 |
| --- | --- |
| 旅程列表局部 | [已有旅程的名称、回合数与更新时间。](screenshots/rpg05/2026-09-12/01-journeys.png) |
| 角色资料局部 | [姓名、性别、年龄、外貌、性格和XP字段。](screenshots/rpg05/2026-09-12/02-character.png) |
| 兼容世界选择 | [蛊真人与Minecraft两个兼容世界的选择项。](screenshots/rpg05/2026-09-12/03-world-mode.png) |
| 身份选择局部 | [身份分页与一个可选身份条目，当前尚未选择。](screenshots/rpg05/2026-09-12/04-identity.png) |
| 开局检查提示 | [配置未通过检查的提示、世界与配套开场摘要。](screenshots/rpg05/2026-09-12/05-setup-review.png) |
| 展开的信息栏 | [开局摘要中的身份、天赋与物资横向信息行。](screenshots/rpg05/2026-09-12/06-expanded-information.png) |
| 游玩正文片段 | [Minecraft回复中两段未遮挡的叙述与NPC对话。](screenshots/rpg05/2026-09-12/07-play.png) |
