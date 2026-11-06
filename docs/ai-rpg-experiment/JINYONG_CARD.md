# 金庸大世界：复刻验收记录

日期：2026-11-06。基线：`6977d86c0ad13e4cd18a9a641f0aae64731443a4`。分支：`codex/rpg-jinyong-card`。

## 已实现

原站七步建档页面、原背景图、单一作品与融合宇宙、人物与武学、八抽三选命数、人物帖预览及复制。接入故事馆第七张卡、独立聊天、可编辑草稿、原有插件服务和公用 Mod。完整提示词机械提取并校验哈希，没有重写。

实际来源：https://afengy.cash/zh/explore/installed/529c4fe0-dec0-4cbf-948d-5ed9341afd80 。与用户所说已打开页面一致，原消息粘贴的另一 URL 不作为金庸资源来源。

## 验证证据

- DOCX SHA-256：`238355902cad8e5c82b7f86b6dce7582edff3487c832600e746782deca3df981`。
- 提示词 SHA-256：`343b7772770b489c4a0867f0deadf0294b2c5505c1b1a82508ddcf9d2cc81a98`。1041 段、20552 个 Unicode 字符。只抽取 XML，不复审内容。
- 原站/本地浏览器对照：天龙八部、雁门旧案、沈砚、20 岁样例，325 字人物帖逐字一致；融合中州、地方行走、心志坚定和镖路见闻样例，348 字逐字一致。
- 浏览器检查：八条命数可见，选满三条后第四条不增加；人物帖精确进入草稿，0 回合；手动发送得到明确标注的离线样例并保存为 1 回合，刷新后可恢复。故事馆显示七张卡，进入金庸卡后可跳转独立聊天地址。1440 像素桌面及 390 像素手机视口已检查，手机页宽与滚动宽均为 390，原卡横向标签导航保持原样。
- `npm.cmd run build --prefix client`：通过（保留已有的大包警告）。
- `npm.cmd run build --prefix experiments/ai-rpg-engine/card-replica -- --base=/rpg-app/earth/`：通过（保留已有动态/静态导入混用警告）。
- `cd client; npm.cmd test -- --run src/pages/RpgWorkspace.test.tsx src/pages/RpgFreeOpening.test.tsx --maxWorkers=1 --fileParallelism=false`：37 项通过。
- 最终镜像中 `node --test jinyong-card/card.test.mjs studio/card-entry.test.mjs plugins/mod-panel/mod-panel.test.mjs`：8 项通过，其中新卡 5 项。覆盖资源精确还原、两轮模拟受控传输、公用 Mod 装配、幂等、旧卡数据隔离、离线发起/恢复、传输规则约束、插画改写独立预算。
- Docker 离线测试使用 `--network none`；所有模型与插画改写回执均为测试模拟，不代表 Gemini 或生图真实可用。

## 已有失败

`studio/container-host.test.mjs:34` 预期旧 God profile 因目录版本变化返回 `RUNTIME_VERSION_MISMATCH`，实际已返回兼容（code 为 null）。同一失败在 `git archive HEAD` 导出的未修改基线上复现。此测试及相关旧 profile 未被修改，不将其记为通过。

## 边界与回退

新卡默认离线，真实调用限额默认为 0；首轮离线验收未调用真实模型。后续用户授权的真实验收见下文；未发布、未改共享容器或既有存档。自定义配置能力仍由既有宿主兼容范围决定。

部署时若需停用新卡，先关闭 `RPG_JINYONG_CHAT_ENABLED`；回退本任务分支的代码即可移除入口。新卡数据位于独立 `archive-chat/jinyong` 目录，不需迁移其他卡数据，不删除新卡存档。旧指纹模块保持字节不变。

## 本轮本地验收实例

- 页面：`http://127.0.0.1:18463/rpg/jinyong`；RPG 宿主只绑定 `127.0.0.1:18464`。
- 自有容器：`modelmirror-jinyong-client-final`、`modelmirror-jinyong-final`。镜像：`modelmirror-rpg:jinyong-review`。
- 数据：本工作树 `experiments/ai-rpg-engine/.rpg04-work/jinyong-preview-data`，未跟踪。
- 仅用于离线验收。停止上述两个具名容器即可关闭本轮预览，不删除数据，不操作共享栈。

预览环境曾在 Windows 绑定挂载读取前端文件时出现 ENOMEM；已把同一构建产物复制到容器内部运行，未修改应用代码或共享容器。

## 2026-11-06 授权真实调用验收

用户授权 1 轮中性、2 轮连续开局样例及插件调用。本地专用容器 `modelmirror-jinyong-live`，端口 18466，预算硬上限 7；不更改共享栈或其他卡存档。全程使用 `google/gemini-3.8-flash`，temperature=0.7、top_p=0.8、max_tokens=16384，无自动重试。

### 真实证据

- 原 7 次回执全部为网关 `rpg_scoped` 的 complete，actualModel 与 requestedModel 相同，retries=0；原文及 SSE 哈希逐项匹配。共 108159 tokens，未据此推算金额。
- 样例会话 `92d06a4e-1ddc-4942-bc20-55830d90d3a5`：2 回合完整落盘，2 条小总结，2 次记忆模型更新，保留 2 条有效记忆；processedThrough=2，无后台待处理/不确定操作。
- 第二轮真实请求只有 system、user 两条消息：原作者提示词前缀逐字保持；首轮小总结和两条命中记忆全文注入，已总结的首轮原始对话不再重复发送。lastRecall 对两条记忆均记录 selected=true；用户档案输入与实际出站内容相同。
- 浏览器刷新后显示 2 回合已保存；自动总结面板列出两条小总结；记忆宫殿显示已整理至第 2 回合、角色条目及更新后的场景条目。
- 此两轮未达到 20 条小结，因此没有实测大总结合并；没有调用图片模型，也未改变作者提示词。

| 调用 | 用途 | 网关 runId |
| --- | --- | --- |
| 1 | 中性剧情 | chatrun_e67ee1b8046745ca8e052d2f82fc0206 |
| 2 | 样例第一轮剧情 | chatrun_71439920c7c546059f9420f3d9f0502a |
| 3 | 第一轮小总结 | chatrun_b7db92d51fd14d5e950869c8e7957dda |
| 4 | 第一轮记忆更新 | chatrun_1c43efc43d034b47b9a7bca76aaed928 |
| 5 | 样例第二轮剧情 | chatrun_8a318dc1e9334d4f9c80660e4a2468a4 |
| 6 | 第二轮小总结 | chatrun_7ad93b46d3eb491284e208f8bed28e1a |
| 7 | 第二轮记忆更新 | chatrun_13cda3e3ded045b38fd02d35f6ca71c9 |

### 不通过项与限制

1. 首次中性请求的模型生成成功，但测试编排在它等待期间给另一会话安装插件，改变同一卡插件注册版本，触发 `SUMMARY_AUTHORIZATION_CHANGED`，会话操作 revoked/cancelled、0 回合。完整响应仍在 provider 证据中；没有强制回写或重放。该轮不计为聊天端到端通过。用户随后单独授权额外 1 次中性补验，使用另一限额为 1 的容器，结果另列。
2. 样例细节连续性未通过：同一天内，赵老栓年龄 52→53，花会倒计时 7 天→3 天；钱物、部分武学命名也发生漂移。两条首轮记忆和小总结确已注入，但未包含这些细节，第二轮重新生成状态栏时出现变化。由此只能确认插件真实执行、召回及装配有效，不能断言长期记忆准确或与原站生成效果相同。

私有请求、原文、SSE、插件结果和复核脚本均位于未跟踪目录 `experiments/ai-rpg-engine/.rpg04-work/jinyong-live-20261106/`。只读审计命令：`python -X utf8 experiments/ai-rpg-engine/.rpg04-work/jinyong-live-20261106/audit.py`。不得将这些真实数据目录作为源码提交。

查看已完成样例：`http://127.0.0.1:18466/rpg-app/jinyong/chat/?session=92d06a4e-1ddc-4942-bc20-55830d90d3a5`。原 7 次预算已用尽，reserved=0、remaining=0；查看、刷新不会调用模型。关闭自有容器即可停用验收实例，不删除数据。
### 追加中性补验结果

用户明确追加授权 1 次模型调用。使用独立容器 `modelmirror-jinyong-neutral-recheck`（18467），硬上限 1；期间不变更插件注册/授权。

- 新会话 `e2e68098-400e-4719-bd6c-994c515bc17a`：中性剧情 complete，成功保存 1 回合，浏览器可打开并显示「已保存」。
- 网关 runId：`chatrun_efbceb22ae24428d86fd553cc6767a80`；Gemini 3.8 Flash，原参数，retries=0，原文及 SSE 哈希匹配，22137 tokens。
- 本次补验只消耗 1 次，reserved=0、remaining=0。证据在未跟踪的 `experiments/ai-rpg-engine/.rpg04-work/jinyong-neutral-recheck-20261106/`。
- 合计实际调用 8 次、130296 tokens：4 次剧情（含首次未落盘的中性调用）、2 次小总结、2 次记忆更新。最终有 1 轮中性、2 轮样例成功保存；上述剧情细节漂移仍保留为未通过项。
- 中性补验地址：`http://127.0.0.1:18467/rpg-app/jinyong/chat/?session=e2e68098-400e-4719-bd6c-994c515bc17a`。

本次实测只补充验收文档和未跟踪证据，没有修改作者提示词或运行时代码，未 Commit/Push/PR/Merge。关闭 `modelmirror-jinyong-live` 和 `modelmirror-jinyong-neutral-recheck` 可停止本轮实例，保留各自数据即可恢复只读核对。

## 2026-11-06 年龄、倒计时与关键状态连续性修复

### 核对结论

问题不是插件未运行或未召回。首轮小总结的事件部分受短文本约束，生成结果未保留年龄、钱物、倒计时和部分武学；记忆宫殿的两条条目也遗漏了这些字段。自动总结 v2 随后从下一轮消息中移除了已总结的原始对话。通过聊天草稿发送的首轮人物帖未写入 `characterText`，因此也随首轮历史退出了上下文。

对已有真实第二轮请求做离线重建，旧规则生成的 messages 与已保存请求完全一致；其中缺少 `赵老栓│茶博士/老掌柜│男･52岁`、`洛阳牡丹花会开市│距今：七天`、`三十文钱｜自备碎钱` 和 `⚓ 轻功：踏虚步云引`。修复后的同一请求包含四项原文、完整开局、已有小总结及已召回记忆。该样例 system 内容由 21209 增至 24767 个 JavaScript 字符，不将字符数等同于 token 数。

### 本次变更

- `experiments/ai-rpg-engine/jinyong-card/continuity.mjs`：为金庸卡的 v2 上下文增加独立的原始状态资料。保留开局，以及 World、Self、NPCs、Bonds、Timeline 各自最近两次观察，附带来源轮次、请求 ID 和原文哈希；最近输出无法识别状态栏时保留该轮原文。摘要遗漏不会删除这些资料。
- `experiments/ai-rpg-engine/jinyong-card/host.mjs`：接入资料装配并绑定新文件指纹，精确兼容修复前宿主描述。每轮记录装配版本，旧回执按旧规则重建，新回合按新规则生成；不迁移或改写旧存档。
- `experiments/ai-rpg-engine/jinyong-card/continuity.test.mjs`：覆盖有损总结、稀疏输出、合法时间/钱物变化、冲突来源、旧回执、新旧回合混合、分支隔离、幂等、禁用插件恢复及指纹篡改拒绝。
- 本文档：记录原因、验收和边界。

作者 `resources/system.txt` SHA-256 仍为 `343b7772770b489c4a0867f0deadf0294b2c5505c1b1a82508ddcf9d2cc81a98`；只在宿主装配时追加独立资料，没有修改作者原提示词。共享插件及其指纹文件保持不变，本次不是其他卡片的通用修复。

### 验证

在自有 `--network none` 容器中运行：

```text
node --test jinyong-card/card.test.mjs jinyong-card/continuity.test.mjs studio/default-summary-v2.test.mjs studio/auto-summary-v2.test.mjs studio/memory-integration.test.mjs
```

44 项通过，0 失败、0 跳过；其中新增连续性测试 7 项。受控模拟测试验证组合插件每轮仍只需剧情、小总结、记忆更新三个阶段，没有新增模型阶段。上述检查不包含前文已记录的既有 container-host 测试失败。

另以已有真实存档和出站请求副本进行离线复核：旧运行时兼容、旧回执验证、旧请求精确重建、新请求关键状态补齐均通过；原存档哈希不变。既有 52/53 岁、七天/三日的矛盾来源均保留，没有自动猜测正确值。真实调用次数为 0。私有脚本和报告位于忽略目录 `.rpg04-work/jinyong-continuity-fix/`，不作为源码提交。

### 限制与回退

此修复保证资料进入金庸卡后续 v2 请求，不保证模型必然遵守，不能据离线测试宣称真实生成漂移已经消失。未重新调用 Gemini、未更新运行中的预览或共享容器、未 Commit/Push/PR。既有漂移输出仍然保留。

资料保留为每个状态区最近两次观察，不是无限期的完整状态账本。开局上限 20000 个 Unicode 字符，状态快照 JSON 上限 48000 个 Unicode 字符；超限明确拒绝装配，避免无声截断。原始资料增加上下文消耗，但不新增计费调用阶段。

尚未部署时，可仅撤回本节列出的宿主接入和新增模块。以后部署前应备份该卡独立数据目录；若新宿主已经生成带版本的新回合，不应将旧宿主直接接到该目录，应保留新存档，以部署前备份核对旧版本。无需更改其他卡或共享数据库。

## 2026-11-06 修复部署与真实复测

用户授权「部署复测」后，将上述已验证的两份运行文件打入 `modelmirror-rpg:jinyong-continuity-v1`，部署至专用 `modelmirror-jinyong-live`（127.0.0.1:18466）。容器内 host、continuity 文件哈希与工作树一致，作者提示词哈希未变。部署前已停止旧容器并冷备份数据；旧容器保留为 `modelmirror-jinyong-live-before-continuity`。没有改共享栈或其他卡片。原两份会话文件哈希仍一致，原样例的两轮旧回执仍可恢复。

本次创建独立会话 `a9698a6e-e5a0-4744-a19a-365fbc2f1a1b`，复用原始龙婉翩开局；第二轮依据本次生成的茶棚场景继续对话，不在输入中补写年龄、余额或倒计时答案。启用自动总结 v2 和记忆宫殿，待首轮后台任务全部完成后再发送第二轮。

### 调用与装配验收

- 新增 6 次 Gemini 3.8 Flash 调用：2 次剧情、2 次小总结、2 次记忆更新，全部 complete，retries=0；回执、原文和 SSE 哈希一致，共 87926 tokens，不据此推算金额。
- 2 轮剧情保存，2 条小总结，2 次记忆更新，processedThrough=2；两条首轮记忆在第二轮均 selected=true 且全文存在于实际请求。
- 第二轮实际请求保留完整原始开局，以及首轮 World、Self、NPCs、Bonds、Timeline 五个状态区原文和原文哈希；原作者提示词作为前缀不变。
- 自动总结和记忆宫殿仍会省略部分数值，关键状态由新增原文资料补足；不将此次结果描述为插件自身已实现完整状态记忆。
- 原账本 7 次，本轮增加 6 次，宿主总上限 13，最终 used=13、reserved=0、remaining=0。没有未知或进行中的请求，没有追加重试或图片调用。
- 聊天页面 HTTP 200，刷新读取两轮会话及插件状态正常。本轮没有重新进行浏览器视觉验收，也没有达到大总结合并门槛。

| 项目 | 第一轮 | 第二轮 | 结论 |
| --- | --- | --- | --- |
| 龙婉翩年龄 | 18 | 18 | 保持 |
| 陈平年龄 | 38 | 38 | 保持 |
| 赵老栓年龄 | 56 | 56 | 保持 |
| 银两 | 十二文 | 十二文 | 保持 |
| 武学名称 | 踏虚凌波步法、归元秘笈、太清仙法等 | 原名称保留 | 保持 |
| 金刀王家寿宴 | 约七日 | 约七日 | 保持 |
| 镖局与绿林摩擦 | 约两至三日 | 正在发生／数个时辰内蔓延 | 剧情提前触发，但未登记时间线偏移 |

**结论：部署与调用链路通过，本样例的关键状态保留有效，整体时间线一致性仍为部分通过。** 绿林哨探在第二轮抵达并与镖头冲突，剧情提供了事件发生依据，因此不是单纯随机改写倒计时；但原预计两至三日的事件提前发生，状态栏仍写「暂无时间线偏移」，这一记录缺口保留为未通过项。不能据两轮样例宣称长期漂移完全解决。

六次网关 runId 按顺序为：

1. `chatrun_b4dadd1f8938400594f1be4b50822391`
2. `chatrun_49b149709618472c8b33ef05d3039f72`
3. `chatrun_2ad354d47bd54eea94e4fb70dbda6517`
4. `chatrun_a83cb3465ae545deb62e2bc0d426a1cf`
5. `chatrun_89c56851e8df4d9d80da9b60d25ffba7`
6. `chatrun_50071653fae54d9c880d0bc4415f6cc9`

查看复测会话：http://127.0.0.1:18466/rpg-app/jinyong/chat/?session=a9698a6e-e5a0-4744-a19a-365fbc2f1a1b 。本轮额外额度已用尽，查看和恢复不会发送新请求。

私有验收脚本、报告及部署前备份在忽略目录 `.rpg04-work/jinyong-continuity-live-20261106/`；真实派发证据仍位于原专用数据目录的 slot-8 至 slot-13。只读审计命令：`python -X utf8 experiments/ai-rpg-engine/.rpg04-work/jinyong-continuity-live-20261106/audit.py`。本次只增加验收记录，没有再改修复代码，也未 Commit/Push/PR。回退时必须保留本次新存档和调用账本，不覆盖新回执或重置已消耗额度。


## 2026-11-06 PR 收尾与首页整合

在独立发布工作树中以 `main@3789ca737de9bc7d762b4cff630c273c3efac8e4` 为基线，保留已合并的蓝色大肥鱼、斗罗大陆与龙族大世界。首页将金庸、斗罗、龙族放入新世界区，蓝色大肥鱼单列为创作助手，其余六个世界继续保留；全部十张卡沿用各自入口、存档与插件身份。没有修改其他工作树或共享栈。

本次新增验收：

- `npm.cmd run build --prefix client`：通过。
- `npm.cmd test --prefix client -- --run src/pages/RpgPluginsPage.test.tsx src/pages/RpgWorkspace.test.tsx src/pages/RpgFreeOpening.test.tsx src/content/help-center/helpContent.test.ts --maxWorkers=1 --fileParallelism=false`：82 项通过。
- 隔离的无网络容器中运行金庸卡及连续性、蓝色大肥鱼、斗罗、龙族、卡入口、自动总结 v2、记忆集成、Mod 面板测试：61 项通过。原有金庸传输一致性断言只对齐新增卡片白名单，完整文件比较仍保留。
- 发布目录的金庸运行文件及作者资源与此前真实复测版本逐字节一致；README 和测试文件单独更新。作者提示词 SHA-256 不变。
- 未增加真实模型调用，未重新部署已验收的真实会话，未合并主分支。

浏览器预览使用本次独立容器和全新数据，真实调用关闭且无密钥。用户单独授权前端连接 bridge 后，仅通过 127.0.0.1:18473 验收；RPG 后端保留在专用 internal 网络。桌面 1440、平板 1000、手机 390 宽度检查完成，手机无横向溢出。斗罗、龙族、肥鱼及肥鱼独立编辑器入口可打开。按已写帮助在全新预览状态重放金庸七步建档，以成年虚构角色沈砚（20 岁）制作人物帖，带入 0 回合可编辑草稿；刷新保留完整档案且不自动发送。发送一次离线样例后为 1 回合，返回故事馆并进入同一存档，内容与回合数保留。截图与详细记录见 docs/help-center/evidence/jinyong-3789ca73/README.md。此次浏览器流程没有真实模型调用。
