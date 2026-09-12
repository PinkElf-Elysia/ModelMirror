# RPG05 PR 前收口审阅

## 当前草稿PR门禁（2026-09-12，PNG已补齐）

7张用户提供的实机PNG已按用户明确选择做常规无损裁切，排除桌面宠物、通知和浮层；逐像素验证与原图对应区域相等，并逐张目视检查。仅裁切图进入文档，原图私下保留；原图/裁切hash、坐标、尺寸和局部覆盖见 [RPG05_SCREENSHOTS.json](RPG05_SCREENSHOTS.json)，查看入口见 [RPG05_CHECKOUT.md](RPG05_CHECKOUT.md)。此前截图工具拒绝与失败仍是历史证据，没有绕过，也没有AI补画。PNG缺口关闭，图片不代表完整页面或人工内容质量通过。

工程聚合520项、typecheck/build及独立代码复核保持通过。本回合新建主线隔离副本，检查origin/main d246527d5554b16594d390172cfa38876f998ccb（基线后13提交）：只有server/main.py同文件交叉，两文件增量三方应用无冲突，结构化输出、稳定文本聊天和主线音频共143项离线ASGI/fake上游回归通过，5条既有框架/OpenAPI警告。该143项与原父仓59有重合，不重复相加；没有将主线代码写入18409运行目录。

当前106个交付变更路径、352项Git文件快照；5项仅索引删除EOF空行的运行原字节继续保留。初始门禁与旧检查点不改，交付后须用默认发布检查器验证实际HEAD/index/工作区，而非用候选回执冒充。用户已授权完善后的Commit/Push/PR；此记录是执行提交前检查点，实际发布动作以随后GitHub草稿PR和机器状态回执为准。

357项运行冻结、120份既有会话/检查点、自动26笔和7份政策均未变；人工用量最新0/5，仅供用户，预览与路由保留。本回合Provider0次，不重试、不扩额。草稿PR可提交；完整验收仍为in_progress。STM累积未通过、天赋宝藏兑现未证实、正文/面板及玩家体验待用户实案验收；人工验收前不得合并。

回退只撤回RPG05增量及已批准的结构化输出接入，保留会话、账本、失败与截图原件；人工服务不可直接回到旧二进制/旧政策。此处不授予Merge、Deploy、Release或Publish。

本回合证据：.rpg04-work/rpg05-png-closeout-20260912 内原图回执、裁切坐标和逐像素验证、upstream-compatibility.json、upstream-compatibility-tests.log；原完整520与HTTP8/父仓59回执继续各自保留。

## PNG补齐前检查点（历史原文）

工程修复、冻结来源保留和候选复验已完成；PNG归档仍是PR创建前的阻断项。用户允许完善后的Commit/Push/PR，明确人工验收前不得合并；先前允许PNG随草稿PR后补的答复已撤回。本轮没有交付提交、推送或PR，既有失败、hash和私有账本保留。

新增检出复验入口 `scripts/verify-rpg05-published.mjs`、检出说明 `RPG05_CHECKOUT.md` 与 Git 文件快照 `RPG05_PUBLICATION.json`。原开工检查器与运行源码不变。当前344项交付快照以Git blob绑定；默认入口必须同时匹配HEAD、index和工作区，并分别检查四路Git差异。未提交候选必须显式使用 `--candidate`，回执 `commitVerified=false`。快照和研究MANIFEST为避免循环hash而排除出快照，但默认仍要求二者已提交且无差异。

隔离候选实际执行旧216+RPG04非HTTP125+宿主176+UI3，共520项，typecheck/build通过。初稿检查器未能证明HEAD绑定，独立审阅发现后已修复；原520回执只作未提交候选证据，未改写为提交证据。最终新增入口通过候选正例、未提交HEAD拒绝和文件漂移拒绝；实际交付commit生成后还必须运行默认模式。独立最后复核未留代码阻断，未重复执行测试。已核验152项锁定依赖与许可来源，凭据扫描仅命中Markdown拒绝测试中的example.org占位URL，真实凭据发现0。

最新origin/main为6c1288e7e07b6a1ff7c3b82a8826b677950dd727，比固定基线多8个提交，与本轮路径无交叉；没有重放运行源码或触碰主检出。此次未新增Provider派发；357项运行冻结、原26笔自动账本与用户专用5次入口继续保留。

PNG：本回合受支持的截图接口先报零宽视口，官方视口调整后原生及完整页面截图仍超时，未生成PNG。此前数据页面导出的安全策略拒绝保持记录，不绕过；需要受支持的截图文件实际落盘后核验并登记，才能创建PR。STM、天赋及信息一致性仍按下文列为质量待验项，不因PR授权变为通过。

暂存检查另发现8个既有CRLF文件及5个EOF空行问题，初次默认检查失败记录原样保留。新增精确路径 `.gitattributes` 仅识别CR行尾，保留blank-at-eol、blank-at-eof、space-before-tab，不做text/eol转换或clean filter。正反例确认合法CRLF通过、尾随空格和新增EOF空行仍拒绝。4个UI文件及1份历史说明仅在提交索引移除末尾空行，原运行字节、hash和旧文本保留；分5文件和1文件批次处理。整理后默认暂存检查、隔离typecheck/build通过，JS/CSS/HTML与保留的人工运行版逐字节相同。原工作区刻意保留这5项未暂存的旧运行字节；不得用git add -A覆盖发布索引。发布复验必须在独立检出进行，不能把该运行工作区说成已提交且干净。

本回合私有证据：`.rpg04-work/rpg05-pr-publish-20260912/` 内 published-verification.log、verifier-checks.json、packaging-checks.json、review-findings.json、png-capture-attempts.json、before-metadata。仅新增验证工具和说明；没有重建正在运行的18409实例。以下保留上一工程切换检查点，当前授权以上文及机器状态为准。

## 上一工程切换检查点（历史原文）


本次工程收口已完成并切换到修复版 closeout-1。实际工作区聚合 520 项、typecheck/build、来源/账本/会话保留检查通过，独立代码审阅未留阻断发现。预览与真实调用入口继续保留。

仍未达到排除用户人工验收后的完整 PR 门禁：STM 累积未通过、天赋宝藏效果未证实，独立 PNG 归档尚未完成。本次没有新 Provider 实测，不能将离线修复算作这些质量缺口已关闭。

## 已修复与验证

| 问题 | 当前处理 | 验证 |
|---|---|---|
| P1 未决派发误记普通失败 | 可能已派发且没有可信终局时记 unknown，阻止下一次预约；用户主动取消保持 cancelled | 真实适配器的替换 HTTP、重启后预算守卫测试；0 Provider |
| P2 明确拒绝锁住输入 | 仅释放明确受理前拒绝；版本冲突还需确认该编号不存在，网络未决保留原编号 | 实际 mock UI：拒绝、原样保留、刷新、修改、正式保存 1 回合 |
| P2 模型自行存档 | 提交前拒绝 state.rpg04.memory.saves，保留完整失败候选与原正式历史 | 失败候选与父检查点字节不变；查询规则与其他状态接受回归 |
| 切换时的并发窗口 | 路由资格 → 同一 runtime 独占 owner → 追加来源绑定 → 监听 | 迁移及原手动集成 36 项；独立复核启动顺序与失败恢复 |

变更为 12 个源码/测试文件和 1 份操作说明，分 2、2、3、5、1 文件批次应用。RPG04 提示词、RPG05 已批准的两句语义补充、卡片规则、旧合同、旧测试常量和依赖均未修改。存档限制仅针对现有卡片字段，不建设通用存档、STM/LTM 或玩法引擎。

保留的限制：HTTP 非200即使附合法收据，没有验证过的 SSE DONE 仍可能记 unknown；提前退出解析的 length/refusal 也可能如此。这是保守阻断，不能宣称所有明确 HTTP 拒绝均可继续。

## 验证分栏

| 范围 | 当前证据 | 边界 |
|---|---|---|
| offline/mock | 实际 verify:rpg05：旧216 + RPG04非HTTP125 + 宿主176 + UI3 = 520；typecheck/build通过 | 0 Provider；资格/取消/失败测试使用替换 HTTP |
| Agent UI | 候选实际拒绝恢复闭环通过；切换后预览刷新、原旅程列表恢复，服务脚本 hash 与新构建一致 | 无新真实发送；不是用户质量或体验接受 |
| independent | 原三项修复和来源切换共两次复核通过；启动顺序 P1 已修复 | 原适配器9项、内存传输11项、错误分类11项等单列，不与主测试重复相加；迁移最终复核只读代码/测试 |
| HTTP / 父仓 | 原 fake HTTP8 与父仓59历史证据分别保留 | 未重跑；独立 Agent 未直接读取受沙箱限制的原HTTP私有回执，主 Agent 前一只读回合已核验 |
| real | 原 reviewed v2 的Gu3+Minecraft3、格式资格和客户端取消证据保留 | 仅旧冻结运行版的实测；不宣称新增修复已完成Provider复测 |
| manual | 原型已批准；人工额度仍0/5，用户内容质量和体验待验收 | 五次仅供用户，不由Agent自动使用 |

完整聚合回执 `.rpg04-work/rpg05-verify-1789228974648/receipt.json`，SHA256 `51f51e38e0649d6c925161853b59b6d1436125b4673a128eb8551ee589cf9932`。此前在无独立Git根的候选复制目录运行同一聚合，因 FREEZE_SET_COUNT 停止；失败回执保留，随后在正确工作区原命令完整通过，没有改旧门禁常量。

## 运行来源与数据保留

基线 81fc14f6e0dada2447a63c1e532ee55b1f914ded，分支 codex/ai-rpg-rpg05-ui。当前入口 http://127.0.0.1:18409/ ，自有宿主PID56980。路由PID32576及隔离newAPI保持运行，临时mock18410/PID48304和其标签已关闭。

当前人工冻结 `d8814b5b3a1f43fbc6ba0aaf837e247d95027a0e2037cb54245caa15e2f1c648`，来源357项。旧人工冻结 `a2102b6f1321e906bb8816e84fce7d3e280ac7b8e32b58a0ab9c24ef85d9bec6` 原字节保留；原 manual-policy revision1/SHA `6ce2a317f721161e044f74dbe47ef6ac10229843def7cdf427f7cffaecf0da79` 未改。同一账本只追加 manual-source-binding.closeout-1，SHA `e9fac293b4cad76ad83f18e6c351498ef682c8622cbbdfc6f2d8559f4aae3a7e`。自动26笔及7份政策一致，人工记录0/5；本次Agent Provider0次，无扩额、重置、重试或新账本。

切换前后120份会话/检查点文件hash一致。新宿主服务脚本与实际构建hash一致，来源守卫通过；预览已刷新。当前可变收口元数据仅 STATUS、REVIEWED_RETEST、THIRD_PARTY 与本报告四份，代码、构建、操作说明、规则及资源继续绑定。

旧二进制本身不认识新来源标记，不能声称标记可以独立阻止任意旧程序降级。受控切换已先停旧入口并确认无未决，再取得同一路径独占owner。绑定或监听失败释放owner；如果绑定已经写入，只按同一closeout-1恢复，不还原旧政策、旧二进制或账本。需要撤回修复时，保留证据与用量，另行准备经过验证的新来源版本；不直接逆向补丁降级运行。

## 仍待关闭的质量与证据问题

- STM：第24笔实际请求包含第23笔旧摘要和追加规则，模型仍只返回新的S1。原始响应已缺少旧条目，转换没有额外删条；shortText整体替换被提交。完整回合和旧检查点未丢失；本轮没有引入卡片记忆语义引擎或新提示词修复。
- 天赋：自动钓鱼机的必得宝藏效果只表现为旧皮革袋与未知硬物，未证实兑现。正文/信息栏、NPC知情及资源等级差异仍需真实案例内容审阅。
- 六回合正文为1007、1207、1564、1191、1210、1580非空白字符。用户已批准800–1200作为质量参考，偏差保留，不因此单独停批，也不放弃其他要求。
- 原生CUA截图在任务中展示，独立PNG文件尚未归档。浏览器安全策略拒绝了本次通过本地数据页面导出截图的操作，已停止该方法，没有绕过。手机整体优化依照用户批准延期。

## 证据与发布

本次证据根 `.rpg04-work/rpg05-pr-closeout-20260912T150920Z`：cutover-applied.json、cutover-preflight.json、cutover-verification.json、cutover-owner.json、cleanup.json、两份 independent 审阅、各候选和实际聚合日志。已应用补丁 `.rpg04-work/rpg05-pr-closeout-20260912T150920Z/cutover-fixes-applied.patch`，SHA `48779fef54d2311feade30a6829d2b755615824a62c41bd6b6efe09809ef0454`。原8文件候选补丁和原始失败记录仍保留。

当前机器状态以 RPG05_STATUS.json 与本报告为准。RPG05_ACCEPTANCE.md、TARGETED_ACCEPTANCE及旧格式审阅保留各自版本/检查点含义，旧“当前”不代替本次状态。旧状态、报告、登记、MANIFEST和源码字节在before-metadata、before-activated-metadata、before-source中留存。

未执行Commit、Push、PR、Merge、Deploy、Release或Publish。用户人工合并要求不变。
