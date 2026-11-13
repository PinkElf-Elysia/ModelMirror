# 西幻卡片复刻验收（2026-11-13）

首阶段实现与真实调用基线：`b4080f2c`，独立工作区 `C:/tmp/mm-rpg-elderland`（保留原存档与证据）。PR 整理基线：`4627d73d3dbcfab25be6c5df218633999b0d8f54`，独立工作区 `C:/tmp/mm-rpg-elderland-pr`，分支 `codex/rpg-elderland-pr`。用户授权提交并创建 PR，暂不合并或部署。下文分阶段记录，不将旧基线的真实调用表述为主线重新在线验收。

## 还原来源

[用户指定的西幻大世界](https://afengy.cash/zh/explore/installed/9861186f-8af4-411b-b771-07e45ee29386)。使用页面脚本明确调用的公开只读详情接口取得 HTML 与作品介绍；图片沿原 HTML 的 URL 获取。未读取账号凭据，没有在参考站发送消息或调用模型。

提示词来自用户授权的《西幻提示词.docx》。OOXML 逐段提取与 python-docx 独立逐段提取结果完全一致，2843 段、66964 个字符；system SHA-256：`bbb79d8bb999d065b6a33e231f58e459e5b9bdabc1493ad382c9310131e0f75c`。原文不修订。详细资产与来源哈希见 `experiments/ai-rpg-engine/elderland-card/resources/`。

## 已通过

| 检查 | 结果 |
| --- | --- |
| 卡片专项与存档插件测试 | 14/14，通过；包含原文/11 图哈希、原文案比较、脚本语法、十三页签、八区域、跨区出生地、角色、能力转义、传承上限、减少动效键盘入口、两轮离线会话与服务重启、跨卡隔离、受控 mock 装配和八类插件 |
| 四组客户端 RPG 回归 | 90/90，通过；故事馆入口、自由开局、草稿、路由与插件 |
| 共享聊天前端生产构建 | 通过；仅保留既有动态/静态导入混用提示 |
| 模镜前端生产构建 | 通过；主应用已有大 chunk 提示 |
| `build.mjs --check` | 通过，生成 HTML 与适配器一致 |
| `git diff --check` | 通过 |

## 浏览器核对

独占 loopback 服务：前端 `http://127.0.0.1:18541/rpg/elderland`，RPG 宿主 `127.0.0.1:18441`，数据位于该工作区 `.rpg04-work/elderland-preview`，未使用共享运行实例。

- 桌面核对羊皮纸、金边、标题、蓝色页签、原背景、地图；九张地图均 `complete=true` 且 `naturalWidth>0`，尺寸与原图一致。
- 实际观察开场上下眼睑展开、背景从模糊变清晰、主界面出现，以及传承按钮摇动与抽取结果。原动画 CSS 在适配后保留。
- 选择圣光王国→辉耀城；填入林澈、28岁、目标；选择基础剑术和三项传承；角色预览内容完整。
- “带入聊天”进入独立西幻会话，557 字角色卡为未发送草稿、0 回合；刷新草稿恢复。明确的离线发送后保存 1 回合并显示离线样例。
- 390×844 视口检查：卡片内容区域 `scrollWidth=clientWidth=360`，无横向溢出，地图按宽度缩放，页签可横向滚动。临时视口检查后恢复默认尺寸。
- “作品详情”显示原版完整介绍，非摘要改写。检查到的浏览器 error/warn 日志为空。

## 差异与验证边界

增加本地聊天入口、原介绍弹窗入口、键盘/减少动效支持、用户输入转义和跨区域出生地绑定修复；作者提示词、地图与背景字节不改。去掉无效的迟置字体 import，实际字体回退行为保持。

以上为首阶段离线/UI 与 mock 集成验收；后续真实模型验收见下一节。实际图片生成、Docker 镜像部署未执行。地图全图合计约 30 MB，保留原分辨率以满足精确还原要求；本地静态加载不依赖原图床。

回退：撤去本任务新增卡片目录、故事馆/聊天注册及宿主精确 allowlist 项；保留 `archive-chat/elderland` 会话数据。没有数据库迁移或旧会话改写。


## 真实模型验收（2026-11-13）

用户明确授权验证角色卡开局、连续对话和存档恢复，提示词原文不改。冻结 3 次真实故事请求；模型 `google/gemini-3.8-flash`，temperature 0.7、top_p 0.8、max_tokens 16384。实际回执三次均确认该模型、retries=0。未调用摘要、记忆或图片模型插件，未修改共享后端配置。

**结论：开局、第一轮续聊和存档数据恢复通过；重启后完整续聊未通过，第三次请求被提供方 content_filter 截断。** 未重放请求、扩大额度或修改提示词。

| 阶段 | 实际证据 | 结论 |
| --- | --- | --- |
| 角色卡开局 | 原 UI 生成的 557 字林澈角色卡；2 条消息（system、user）；HTTP 200，stop；1 回合保存 | 成功生成辉耀城公会场景，姓名、28岁、人类、战士、1环、基础剑术、三项传承、测绘目标均保留 |
| 连续对话 | 4 条消息；完整携带开局原始输入/输出；HTTP 200，stop；2 回合保存 | 延续艾尔莎和三里岗委托，资金仍为 0金12银45铜，遵守不接单、不购物、不离开公会 |
| 进程终止与恢复 | 原 PID 53728 退出；首次重启触发 PLUGIN_OWNER_RECOVERY_REQUIRED；使用现有 acquirePluginOwner({recoverDeadOwner:true}) 核实死亡、归档两个 owner.lock，恢复 PID 39496 | 恢复前后会话 JSON 逐字节一致；非无人工步骤的自动恢复 |
| 恢复后续聊 | 6 条消息；前两轮完整历史与原文逐项相同；真实请求已经发送；上游 finish_reason=content_filter，本地 HTTP 502 / rpg_incomplete_output，部分输出 5450 字符 | 不完整输出未保存为第三回合；完整续聊验收未通过 |
| 浏览器刷新 | 本机前端显示原会话两轮“已保存”、冻结模型与“可用0次” | 保存结果与后端一致；过滤结果未污染历史 |

第三次输入没有重述具体报酬、地名与距离。其部分正文正确复述每日5银/三天15银、狼尾8银、脊骨皮凭证、晨星大道两里半→双岔柳向北半里→三里岗、松针斜坡等事实。这只能证明部分历史连续性，不能当作第三轮成功。

### 记录的偏差与边界

- 开局模型补写了角色卡中留空的外貌、性格和兴趣，并代写部分动作/内心；没有为了通过验收修改作者提示词。
- 第二轮存在“推了推鼻梁上并不存在的镜架”等文案瑕疵；不能把基本连续性通过等同于剧情质量完全合格。
- 第三次提供方内容过滤，具体触发内容无法由回执确定。既有任务调度层将失败显示为 SUMMARY_TASK_FAILED，但该请求的 purpose 确为 story，没有额外摘要调用；这条通用错误描述不足以直接解释实际原因。
- 原 DOCX SHA 与三次在线 system SHA 均复核不变；实际消息数为 2/4/6，所有历史内容逐项一致。
- 前两次已知用量：prompt_tokens 108860、completion_tokens 10350、total_tokens 119210。第三次 usage=null，不能报告完整总用量或费用。
- 本轮只补充验收文档与忽略目录中的证据脚本/快照，没有改动运行实现、提示词、资产、数据库或生产部署。既有离线回归结果仍见前文；本轮执行证据断言与 git diff --check，不重复无关构建。

### 本地证据与复查

会话：`42229e63-079a-45c3-87bd-d608415ead98`。

只读复查入口：[真实验收存档](http://127.0.0.1:18542/rpg/elderland/chat/42229e63-079a-45c3-87bd-d608415ead98)。服务仅绑定 loopback，3次额度已耗尽，未调整额度。

证据目录（Git 忽略，不提交模型全文）：`experiments/ai-rpg-engine/.rpg04-work/elderland-live-20261113/`。

- `plan.json`、`input-1.txt` 至 `input-3.txt`：冻结模型、预算和实际输入。
- `wire-*.json`、`data/archive-chat/elderland/real/dispatches/elderland/slot-{1,2,3}/`：消息哈希、请求、响应与回执。
- `before-restart.json`、`after-restart.json`、`final-snapshot.json`：重启前后一致及失败后两轮存档不变。
- `verification.json`：脱敏核验汇总；`python experiments/ai-rpg-engine/.rpg04-work/elderland-live-20261113/verify.py` 已通过，包含原 DOCX/系统哈希、每轮完整历史、回执/响应哈希、stop/content_filter 与旧锁归档检查。

本次无发布可回退；保留原始两轮存档和第三次失败证据，不删除历史或恢复调用额度。


## 换模后的恢复续聊验收（2026-11-13，追加授权）

用户要求更换可用模型继续验收、保留原提示词和现有存档/失败记录。本轮通过受控目录确认 `google/gemini-4-argon` 可用，再通过原会话的 `rpg.model-selector` 正式安装、启用与选择接口切换；没有修改会话 JSON 来伪造选择，没有修改模型适配器或运行提示词。

**本轮恢复与续聊链路验收通过：新增两次真实请求均 HTTP 200、finish_reason=stop、retries=0，实际模型均为 Gemini 4 Argon，原会话现在有四个已保存回合。** 这是有边界的两轮连续性验收，不代表长程剧情不会漂移。

### 原始记录保留

- 仍使用会话 `42229e63-079a-45c3-87bd-d608415ead98` 与同一存储目录。总额度从原3次追加至5次；旧3次全部计入，不退款、不删除、不重放，新增请求使用独立 operationId。
- 新批次冻结计划及完整32文件备份在 `experiments/ai-rpg-engine/.rpg04-work/elderland-live-20261113/argon-followup/`，旧 `plan.json`、旧快照和旧核验报告保留原样。
- 原两轮完整记录与原失败状态逐项相同；旧3个调用槽及 no-replay 请求记录共24文件哈希不变。第三次 Flash 的 content_filter 失败仍留在 slot-3；其残缺输出没有进入故事历史，也没有冒充已完成回合。
- 两次新增出站消息分别为6条、8条；包含原 system 与全部已完成的 user/assistant 原文，逐项完全相同。原 DOCX 与 system SHA 均保持前述值。

### 连续性核对

| 核验内容 | 结果 |
| --- | --- |
| 委托报酬 | 正确延续每天5银、三天15银；额外狼尾8银与半袋面粉；未强制击杀 |
| 路线与危险区域 | 正确延续晨星大道东支线两里半→双岔柳向北半里→三里岗；松针斜坡与黑松森林边缘、灌木沟禁入提醒 |
| 新旧事实跨轮延续 | 首轮新增小巴顿、老托马斯侄子、东城门内侧草料栓马桩、半个时辰后返程；第二轮在不提供这些答案的输入下正确复述，接单凭回执免费/不接单2-3铜的条件一致 |
| 钱袋及行动边界 | 始终0金12银45铜、1环纯战士；没有支付车资、签约或出城；备忘页保持在存档内 |
| 最早角色卡能力限制 | 正确指出毒素抗性不等于免疫、回声定位10米且完全静默失效、快速学习理解原理不等于立刻掌握或施法；没有获得新能力 |
| 第二次宿主重启 | 首轮 Argon 保存后正常关闭 PID 38032，再启动 PID 71620；无需死锁恢复，前后快照逐字节一致，Argon选择、前三轮和旧失败状态均恢复；之后第四回合正常生成 |

模型仍有细节扩写：把原先“一小块脊骨皮”具体化为“至少两寸”、半袋面粉具体化为约二十磅；后续也补写车夫年龄等。核心已知事实没有被替换，但不能宣称完全零扩写或逐字复述。本轮保留原输出与原提示词，不为通过验收修订文本。

### 调用、核验与边界

| 新增槽位 | 保存回合 | 消息数 | requestId | 用量（input/output/total） |
| --- | --- | --- | --- | --- |
| slot-4 | 3 | 6 | `94ea03e5f4fa74a53c94e6a6683563b49e2647ae5a4ae2d7b5cfa449d2689dd6` | 60003 / 5245 / 65248 |
| slot-5 | 4 | 8 | `b7b37e800b4e9d050cac5a2ee88afeb5267c27f5af33b8314f7e1550c0af74d2` | 65375 / 5671 / 71046 |

本轮合计136294 tokens；此数仅包括新增两次，不包含前批失败调用的未知用量。累计5次请求、4个成功故事回合、1次保留的过滤失败；剩余额度0。

执行 `python experiments/ai-rpg-engine/.rpg04-work/elderland-live-20261113/argon-followup/verify.py` 通过：完整历史、原提示词、真实模型回执、响应/SSE哈希、32文件备份、24旧证据文件、旧失败状态及重启快照全部核对。机器可读结果见同目录 `verification.json`，最终存档见 `final.json`。本轮仅追加验收文档与忽略目录内的脚本、备份、证据，没有修改生产实现；`git diff --check` 通过，无提交/推送/部署。

恢复方案：本轮前的完整数据副本保留于 `argon-followup/backup-data/`，仅在停止本次宿主并另行确认时用于恢复；不要覆盖现有四轮存档或清理旧失败账本。当前正常读取无需回退。

浏览器最终刷新复核：原会话显示4回合，当前选择Gemini 4 Argon；第1、2回合仍标记Flash，第3、4回合标记Argon，全部“已保存”。页面剩余额度0，不再显示待确认的进行中任务。


## PR 整理与最终检查（2026-11-13）

基于最新主线 `4627d73d` 整合，保留统一故事馆与 AI 娘入口；西幻新增为第12部内置作品。仅注册新卡、静态资源、受控路由和插件 allowlist，未修改核心会话指纹、公共聊天协议或持久化结构。原提示词和11张原始图片与首阶段逐字节相同。超过5个文件是同一卡片贯穿入口、宿主、插件与验收的必要集成，不包含其他任务改动。

| 检查 | 状态 | 实际命令与摘要 |
| --- | --- | --- |
| 暂存区空白检查 | 失败 | `git diff --cached --check` 仅剩原HTML第1639行及其生成副本第1647行的作者尾随空格；为保持原始HTML哈希与可复现生成而保留，其他新增行已修正 |
| 生成文件一致性 | 通过 | `node experiments/ai-rpg-engine/elderland-card/build.mjs --check` |
| 西幻资源/DOM/离线重启/受控请求 | 通过 | `node --test experiments/ai-rpg-engine/elderland-card/elderland.test.mjs experiments/ai-rpg-engine/elderland-card/control.test.mjs`，6/6 |
| 所有归档卡插件 | 通过 | `node --test experiments/ai-rpg-engine/studio/archive-plugins.test.mjs`，8/8；首次并行混跑中行间插画轮询失败，单项及整文件复测通过，未改断言或生产实现 |
| RPG 前端 | 通过 | `npx vitest run --configLoader runner --maxWorkers=1 --fileParallelism=false src/pages/RpgAuthoringEntry.test.tsx src/pages/RpgFreeOpening.test.tsx src/pages/RpgLibrary.test.tsx src/pages/RpgPluginsPage.test.tsx src/pages/RpgWorkspace.test.tsx`（client目录），102/102 |
| 类型与构建 | 通过 | client `npm.cmd run typecheck`、`npm.cmd run build`；card-replica `npm.cmd run build -- --base=/rpg-app/earth/`；保留既有chunk/import提示 |
| Compose 与镜像 | 通过 | `docker compose -p elderland-pr-check config -q`；engine目录 `docker build -t modelmirror-elderland-pr-check -f studio/Dockerfile .` |
| 镜像安全冒烟 | 通过 | 无外网、只读rootfs、非root UID1000、cap-drop ALL/no-new-privileges、仅临时data/tmp；卡片及聊天入口200、nosniff，非信任Origin写入403，无真实模型调用 |
| 帮助全量内容检查 | 失败 | `npx vitest run --configLoader runner src/content/help-center/helpContent.test.ts`，17/18；旧manage-rpg-history截图引用fbe65f12，与元数据479cd636不符 |
| 帮助全量图片检查 | 失败 | `node scripts/verify-help-images.mjs`，12处旧JPG/尺寸/残留素材问题；未改动的4627d73d基线独立运行复现完全相同12处，西幻新增PNG1000×841、122709字节无新增失败 |
| 真实证据保留复核 | 通过 | 原工作区再次运行 `argon-followup/verify.py`：4回合、24旧证据、32备份、旧过滤失败、system与完整历史均不变；本次没有新增计费调用 |
| 最新基线帮助实操 | 通过 | 见 `docs/help-center/ELDERLAND_2026-11-13.md`；独立预览重放角色卡→草稿→刷新，不使用原真实库 |

帮助校验的既有失败不被隐藏或削弱，本次仅证明新增指南无额外失败。全量前后端回归、真实图片模型、主线合并后的真实模型调用与生产部署未运行。依赖锁文件无修改；Docker依赖安装报告2项既有高危告警，本次没有执行无关依赖升级。

提交只含源码、授权资源、测试、帮助与验收报告。原始模型请求/响应、凭据、存档和失败账本留在原工作区 Git 忽略目录，不随 PR 上传。回退只撤去西幻接入提交，保留 `archive-chat/elderland` 全部数据；无需数据库回滚。


## PR #459 主线冲突解决（2026-11-13）

同步主线 `a237f26f`（中世纪卡片 PR #458 已合入），通过普通 merge 保留双方历史，不重写已推送提交。13个冲突文件、20个冲突块均保留西幻和中世纪注册；中世纪的专用宿主、模型预算与插画改写适配器保留。故事馆为13部内置作品，含1部测试发布作品时为14项；插件读取次数对应26次。新增双方卡片嵌套聊天静态资源回归覆盖。

- **通过**：`node --test --test-concurrency=1 experiments/ai-rpg-engine/elderland-card/elderland.test.mjs experiments/ai-rpg-engine/elderland-card/control.test.mjs experiments/ai-rpg-engine/medieval-card/card.test.mjs experiments/ai-rpg-engine/studio/archive-plugins.test.mjs experiments/ai-rpg-engine/studio/card-entry.test.mjs`，22/22。
- **通过**：前述5个RPG前端测试文件，106/106；与帮助文件一起运行合计123通过、1失败。帮助中心17/18，仍为既有manage-rpg-history截图基线错误，无新增失败。
- **通过**：client `npm.cmd run typecheck`、`npm.cmd run build`；card-replica `npm.cmd run build -- --base=/rpg-app/earth/`；`node elderland-card/build.mjs --check`。
- **通过**：`docker compose -p elderland-merge-check config -q`、新镜像构建；无外网、只读文件系统、UID1000的独立容器内，两张卡的角色页/聊天页均200，非信任Origin写入均403；容器随后移除，没有部署共享环境。
- **通过**：`git ls-files -u`为空；本次接入源码相对主线的空白检查无新增问题。双方卡片目录分别与`c9cac02b`和`origin/main`逐字节一致；继承的作者提示词/HTML/字体许可空白未修订。
- **通过**：PR相对最新主线共50个任务文件（新增西幻静态入口测试覆盖），无medieval-card目录差异、运行数据或凭据模式命中。

本次仅做冲突兼容与回归，不改变西幻建档步骤、原提示词、地图、背景、文案或动效；帮助截图仍对应明确标注的首轮预览基线，不冒充本轮重拍。真实存档与失败记录留在原独立工作区，没有重新调用模型、迁移存档或修改额度。原有模型扩写、帮助全量失败及未部署边界仍适用。回退本轮同步应保留所有作者目录及运行数据，勿对merge提交直接执行未区分父分支的回退。
