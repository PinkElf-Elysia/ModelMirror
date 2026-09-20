# 补充批次证据

## 已运行

1. `node --test experiments/ai-rpg-engine/studio/*.test.mjs experiments/ai-rpg-engine/plugins/*.test.mjs`：177 项，176 通过、0 失败、1 跳过。跳过的是 model-legacy-copy.test.mjs，未提供 RPG_B3_LEGACY_COPY 旧 B5 副本；没有读取或迁移原数据以凑通过。最终控制台记录在本工作区 experiments/ai-rpg-engine/.rpg04-work/rolling-summary-overflow/regression.txt。
2. 在 card-replica 运行 `npm.cmd run verify`：26 项测试通过，TypeScript 和 Vite 生产构建通过。
3. 新增 summary-compression.test.mjs 的 19 项测试包含：Unicode 阈值、3000 接受/3001 拒绝、原摘要截断不触发、空压缩拒绝、50 完整合成回合来源逐字核对、最多一次压缩、两次滚动分别溢出、失败保留旧摘要、预算不足零派发、余量释放、在途撤权/取消、未知结果恢复、两种落盘失败、后台等待、真实宿主装配和分支来源隔离。
4. `git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check`：通过。默认 diff --check 将原有 CRLF 中新增行的 CR 报为尾空格；显式识别 CRLF，其余空白规则仍启用，文件各自换行格式保留。

每个带 Provider 的新测试将实际 fetcher 请求对象写入忽略目录 rolling-summary-overflow/tests/f-*/capture.json，标记 fake-provider-host-wire。包括 source、messages、参数与会话请求标识；不含真实凭据。宿主来源与请求 hash 由运行期绑定，技术回执不进入消息。

## 历史失败保留

- 初轮 14 项测试为 13 通过、1 断言失败：空压缩在既有传输层先拒绝，实际错误为 SUMMARY_TASK_FAILED，而非预期的 SUMMARY_COMPRESSION_REJECTED。修正错误码断言后通过；暂停与无循环派发断言保留。
- 增加分支测试后先遇到测试夹具仍提交摘要插件 ID，修正后又发现夹具未连接正式分支宿主。改用实际 connectSummary 后，19 项全部通过。没有改变生产分支校验来迁就测试。
- 编辑过程中换行格式造成无关整文件差异；恢复各文件基线格式后重跑最终宿主回归。

## 未运行与阻断

- 内置浏览器打开 http://127.0.0.1:18475/ 返回 net::ERR_BLOCKED_BY_CLIENT。停止浏览器操作，未换端口/浏览器或使用其他控制方式绕过。尚无本批真实运行截图，桌面、390px 和键盘可视验收均未完成。HTML 原型可供用户自行打开查看，不当作 UI 已通过证据。
- 正式 UI 尚未修改，新增费用和压缩状态仅在原型中。父 Studio 前端未改，本批尚未运行其测试/生产构建，留给原型批准后的 UI 交付。
- 真实 Provider：本批 0 次，无新额度。没有验证真实二次压缩的忠实性与长程稳定性。先前正常滚动摘要不能作为超过阈值后二次压缩的证据。
- 上述 50 回合是最小合成会话，不是玩家长局，也没有第二张卡完整验收声明。

## 原型启动故障复查与恢复

2026-09-20 用户要求定位并修复后，经过授权的主机只读检查确认：18475 和 18477 均由 PID 1608（node.exe，rolling-summary-pr/preview.mjs）监听；该旧进程创建于此前启动失败之前。18475 根路径返回 JSON NOT_FOUND，不是本原型。先前沙箱内 Get-NetTCPConnection / Get-CimInstance 实际被拒绝访问，空结果不能作为端口空闲证据。原 Python 启动日志的 WinError 10013 保留，之前仅归因浏览器客户端的解释已被本次证据修正。

18477 的尝试在端口检查处停止，未启动或关闭任何进程。随后选择实查空闲的 18479，仍用同一 Python 静态服务、同一原型目录，绑定 127.0.0.1。PID 34644、HTTP 200、磁盘与响应字节 hash 一致。官方内置浏览器已打开 http://127.0.0.1:18479/，实际显示二次压缩原型，内联截图已查看。这证明新原型启动和浏览器显示恢复；不代替完整 UI 流程、390px 和用户批准。未归档截图文件。

启动回执：experiments/ai-rpg-engine/.rpg04-work/rolling-summary-overflow/preview-18479-receipt.json。旧 18473/18475/18477 实例保持运行，无防火墙修改、无 Provider 派发。正式 UI 接入仍等待用户原型门禁。

## 原型获批后的正式 UI 批次（2026-09-20）

前文“未接入 UI / 尚未批准”记录的是此前阶段，本节记录后续实查，不覆盖历史失败。

- 用户批准 18479 原型后实施：状态及新费用说明、压缩前后原文、未生效尝试、服务器验证后的一次再次压缩入口；摘要轮询同步刷新共用预算，保存/取消不生成。
- API 与新增压缩测试：23 通过（20 compression + 3 summary HTTP）。最终全部 studio/plugins 回归：178 项，177 通过、0 失败、1 跳过；旧副本跳过原因不变。final-host-tests.txt 保留控制台。
- 受影响前端五文件 44 通过（RpgRollingSummary、RpgPluginsPage、RpgHistoryWindow、RpgModelSelector、RpgChatState）；StudioBetaPanels 3 通过。card-replica verify：26 通过、TypeScript 和生产构建通过。父前端生产构建通过，保留原有大 chunk 警告。挂载构建使用 npm.cmd run build -- --base=/rpg-app/earth/。
- 正式浏览器真实操作：市场安装、会话单独授权、独立摘要模型选择与保存；14891 字符合成摘要触发一次压缩；失败暂停、显示再次压缩 1 次调用；主动再次压缩成功；原文查看、Esc 放弃修改、刷新恢复、第三回合续玩、卸载/重装不自动启用。390px 外层/375px iframe 的 scrollWidth=clientWidth，无横向溢出。
- 真实截图为官方 CUA 原生 JPEG：screenshots/desktop.jpg、failure.jpg、mobile.jpg。没有 PNG 交付声明。短暂运行中的压缩标签由组件测试覆盖，本次没有捕获该瞬间截图；完整键盘流程未穷举。
- ui-wire.jsonl 记录宿主真正发给可控假 Provider 的请求；ui-acceptance.json 包含逐字/顺序/hash 断言与用途序列。成功会话为 story,story,summary,compression,story；失败会话为 story,story,summary,compression,compression。种子共4次在内合成派发10次，真实派发0次。续玩仅用有效压缩摘要＋原第二回合＋当前输入，原始历史完整保留。
- 合成超长文本与压缩返回是故意构造的机制夹具，不作忠实性或长局质量评分。真实二次压缩仍未运行，额度为0；用户此前正常摘要通过不外推到本批。

本阶段故障记录：导入 ChatView 后 Vitest mock 工厂遇到初始化顺序错误，改为官方支持的异步 import；预算断言先匹配错误文案，随后测试 mock 复用了同一对象，与真实 JSON 返回不符，改为独立副本后44项通过。默认卡片构建的 /assets 路径不适合 Studio iframe，导致空白，重新按 /rpg-app/earth/ 构建并浏览器重放通过。截图接口最初仅接收PNG，但实际CUA返回JPEG，原样另存JPEG，没有伪造或转绘。帮助更新首次相对路径错误未写入文件，改为绝对路径后完成；保留文件原LF格式。

回退：优先停用新版插件，不删历史/摘要/账本。旧宿主兼容降级未用真实数据测试，禁止直接指向新目录。当前仅待用户验收本批；没有 Commit、Push、PR 或共享部署。

## 新授权合成长摘要：第1次真实调用（2026-09-20）

用户确认新额度最多2次（素材生成＋压缩），失败/未知计数、无自动重试，不生成多轮剧情；旧额度及会话未改。real-compression-v1/AUTHORIZATION.json 与 LEDGER.json 记录授权和使用。18458实时受控目录确认Gemini可用，无认证调用；服务凭据仅由进程在内存中载入，不输出或落盘。

第1份完整返回：google/gemini-3.8-flash，temperature=0.7，top_p=0.8，max_tokens=16384；HTTP200、finishReason=stop、10031个Unicode字符，满足超过10000的阈值。回执报告731输入token、10322输出token、11053总token，不是账单审计。原文sha256：8b790c2d396824ccf8b5f72d2287a8219642652f7b685948dff01a58d3b60e06。完整请求、原文、SSE和回执在real-compression-v1/dispatches/earth/slot-1，未补齐或改写。

该输出由独立虚构资料生成指令产生，并非正常M2摘要器从真实长局得到的摘要；正式作者文本和摘要指令未改。后续采用冻结compressionMessages及原参数测试压缩，不替代长局稳定性验证。已用1/2、剩余1次；素材人工审阅待办，压缩尚未派发。此前0次记录保留为历史阶段。

登记脚本两次本地失败（默认GBK解码、校验表达式语法）均在写入前终止，未影响模型输出；修正后完成。open_in_codex仅返回queued，不作为用户已查看原文的证据。

## 第2次真实调用：压缩完成

用户确认首份素材后，仅使用剩余1次Gemini压缩。10031→1900字符，stop、HTTP200，压缩原文hash为ac2070d908ab73bf29a41c71b018f046b73db528508e47b7dec6a63b48e30878。compression-wire-check.json验证实际消息逐字等于生产compressionMessages、完整来源、原参数/模型、请求和响应hash、2/2额度用尽。没有自动重试或新增剧情。

逐项内容核查见REAL-COMPRESSION-REVIEW.md：主要金额、承诺、误会澄清、冲突、未完成状态保留；家属病情说法的来源归属、口头转书面等细节有损失。来源本身已把未勘探密室扩写为勘探排除，不能错误归因为压缩幻觉。此次是单份合成摘要的真实压缩，不是正常长局来源或多次累积压缩验证。用户已批准素材，压缩输出人工审阅仍待办。全部原文、账本、旧阶段记录保留，未发布。

## 新增1次：保留压缩状态、追加经历后再压缩

用户明确选择本地追加素材、只授权1次压缩。原1900字符逐字保留，新添8811字符，组合10713字符。再次Gemini压缩得到1933字符，HTTP200/stop，1/1额度用完。实际消息、完整来源、旧前缀、原参数/模型、回执hash和字符上限均经wire-check.json验证。旧2/2账本没有挪用或重置。

REAL-REPEAT-REVIEW.md区分了主要状态正确更新与内容损失：两库范围被扩大为全馆；消防检查延期被改成复检未过；撤展交接时点提前；检验限定有所弱化。保留全部输入输出，没有修复原文或自动重试。本地新增资料和末尾重申当前状态有利于记忆，不能由此宣告长程稳定性。用户认可上一份“基本正常”，新1933字符输出仍待人工审阅。

## 初版人工接受与发布授权

2026-09-20 用户判断：“有偏差，但在初版可接受范围内，可以收尾后PR”。1933字符再压缩按初版接受，四项语义偏差完整保留；没有调优、修复模型原文或追加调用。三次补充额度全部用尽。先前待审阅记录是历史阶段，当前门禁为PR审查；不包含共享部署、自动合并或长期稳定性通过。

## 发布收尾复核

最新主线09e8a7d6已无冲突合入独立分支。177宿主/插件、26卡片、62前端/帮助测试通过；两处构建通过。正式新预览18487重放成功与失败恢复，10次均为合成派发，本次真实0次。帮助图片检查与主线同为3个旧残留失败；本轮图片及元数据问题修复后无新增失败。完整命令、失败历史、截图和回退见CLOSEOUT.md。
