# E5 原型验收收尾与发布边界

2026-11-14。用户已通过离线及最终真实原型验收，明确授权收尾整理文档、提交PR并向主会话说明对接边界。本次收尾不增加业务补丁或真实调用，不合并、正式执行或部署。此文件是当前状态入口；其他记录保留各自当时的授权、失败和待验收状态，不以最终通过抹去历史。

## 交付和发布范围

工作树C:/tmp/modelmirror-rpg-context-plans-e5，分支codex/rpg-context-plans-e5，冻结基线bfc3d748438637215530f66a50cdd4e4d915f852。仅新增experiments/ai-rpg-engine/feature-exploration/context-plans-e5/**、本目录E5文档及E5-evidence说明截图；公共核心、E4及E1–E3合同、父项目、共享配置、角色工作树保持原样。没有依赖、迁移或正式导航变化。

E5在E4只读来源和单步校验上提供最多三个有序意图步骤、每步一个原文条件、歧义澄清、稳定步骤ID、连续修订、删除/重排后依赖确认、只读保存导出。普通交互页是固定样例模拟；受控Gemini只在另行授权的任务脚本中运行，不接正式聊天或执行入口。源数据是独立成年修仙/灯塔R5快照，不是完整成熟卡片的共享会话。

本PR包含前述连续分批实现与历次验证脚本，文件较多源于独立冻结的条件、字段及引文补测入口，收尾不做去重重构，以免改写证据。运行目录、原始回执、凭据、日志、node_modules和Python缓存不提交；公开文档保留脱敏结论、完整历史索引与指纹。已有失败回复的回归夹具为中性测试文本，无真实用户材料。

## 历史结果：失败与修复均保留

| 阶段 | 输入/输出token | SSE实报USD | 结果与记录 |
|---|---:|---:|---|
| 初次两步提案 | 472 / 598 | 0.00259650 | 模型将单步守卫误判为分支流程，未形成计划；[E5-REAL-RESULTS.md](E5-REAL-RESULTS.md) |
| 单步条件边界补测 | 723 / 1518 | 0.00623475 | actionId/itemId混入提案槽位，原E4_CHANGES整批拒绝；[E5-CONDITION-REAL-RESULTS.md](E5-CONDITION-REAL-RESULTS.md) |
| 提案字段补测 | 1065 / 1195 | 0.00528000 | 原始字段与两步条件通过；服药仅只读预检；[E5-SLOT-REAL-RESULTS.md](E5-SLOT-REAL-RESULTS.md) |
| 连续修订补测 | 1320 / 914 | 0.00441750 | revise缺少操作级quote，E5_EVIDENCE整批拒绝；[E5-REVISION-REAL-RESULTS.md](E5-REVISION-REAL-RESULTS.md) |
| 操作级引文补测 | 1534 / 2720 | 0.01135050 | 三层quote齐全，连续修订通过原校验，用户验收通过；[E5-QUOTE-REAL-RESULTS.md](E5-QUOTE-REAL-RESULTS.md) |

历史真实请求共5次，供应商实报合计$0.02987925；各次均另行授权、没有模型自动重试。本次收尾新增真实调用0次。推理token已包含在输出内，不重复计算。

修复限于E5提示合同澄清：单步守卫与执行分支分开 → 提案槽位与规则参数分开 → 操作级/槽位/条件quote分别必需。既有parse/apply/E4校验没有放宽，错误回复没有自动改名或补写。文档链为E5-CONDITION-BOUNDARY、E5-SLOT-BOUNDARY、E5-OPERATION-QUOTE-BOUNDARY。

最后真实样例复用原已保存两步计划：撤回step-1服药，保留step-2赠送ID及medicine/zhou，数量改为2；“他已经答应了。”只记录为player-claim。saved清空，旧服药预检清除，orderReview=true，待用户确认依赖；实际丹药仍5，世界记录指纹不变。此结果不代表赠送已执行或周禾确已同意。

一次工具进程在环境准备阶段中断，核对无请求、预算和回执工件且数据库只含早于启动的继承调用后，归档为派发前0调用。先清理临时密文再继续首个请求，不是重试未知调用。全部正常/中断临时凭据均已清理。另有临时离线指纹脚本Windows路径分隔符比较误报，经路径表示统一后复核，未改业务校验；记录见E5-OPERATION-QUOTE-BOUNDARY。

## 验证及可复现范围

已验证：E5/E4 Node62项通过，普通离线浏览器14条流程，最终只读证据页7项；新增quote协议Python3项（之前四组协议12项单列于历史记录）。桌面与窄屏均无脚本错误/横向页面溢出/意外外部请求。319个冻结文件核对在最后实测前后不变。

收尾重新执行可移植Node60/60、五组Python协议15/15和独立模拟浏览器14条流程均通过，证据目录为.rpg03-work/e5-validation/browser-HG7nKB。本轮未重新真实调用。

Node62项中的real/revision-case.test.mjs两项依赖本机未跟踪的已保存Gemini证据，不能宣称干净克隆无需材料也可运行。该文件用于历史实测回放，不是通用CI测试。普通离线入口与其他60项可用仓库自身中性夹具运行。最小可移植检查（仓库根，按engine锁文件已有依赖）：

```powershell
$e5Tests = @(Get-ChildItem experiments/ai-rpg-engine/feature-exploration/context-plans-e5 -Recurse -Filter *.test.mjs | Where-Object { $_.Name -ne 'revision-case.test.mjs' } | ForEach-Object { $_.FullName })
$e4Tests = @(Get-ChildItem experiments/ai-rpg-engine/feature-exploration/context-actions-e4 -Recurse -Filter *.test.mjs | ForEach-Object { $_.FullName })
node --test @e5Tests @e4Tests
node --check experiments/ai-rpg-engine/feature-exploration/context-plans-e5/public/app.mjs
node experiments/ai-rpg-engine/feature-exploration/context-plans-e5/browser-check.mjs C:/tmp/mm-r3a-pw/node_modules/playwright
```

Playwright路径为本机已有依赖，其他环境传自己的安装路径。实测launch脚本是历史受控任务入口，绑定本地选定连接和冻结样本；不能因获得代码就重放计费请求。新调用、路由及凭据访问仍需单独授权。

## 使用与帮助范围

离线交互：http://127.0.0.1:18916/ ；最终Gemini只读验收：http://127.0.0.1:18931/ 。操作说明和截图见[E5-ACCEPTANCE.md](E5-ACCEPTANCE.md)。旧失败页面18919/18922/18928与首次成功18925保留，原始材料留在本任务.rpg03-work。

启动普通原型：`node experiments/ai-rpg-engine/feature-exploration/context-plans-e5/server.mjs --offline-preview --port 18916`。默认关闭，端口占用失败；不要停止他人服务。普通原型可在新机器按仓库夹具重建；真实证据页还需要本地未跟踪材料，不能把其localhost地址当作公开托管。

Help Center Impact：影响独立原型的审阅操作，未进入正式产品。本次用户明确限定E5、禁止部署或扩展公共核心，故帮助和截图随E5实验文档交付，不改client帮助中心或正式故事馆。保留冻结实验基线与人工验收，不宣称重建/验证了最新正式站。该范围说明不是对正式产品帮助门禁的通用豁免；未来正式接入需另行完成产品帮助与集成验证。

## 主会话对接边界

发布前fetch核对origin/main=d61dbd84a32793b454420bad715cc2c4e59410f8（R7 PR483已合并）；新增主要在agent-core/tasks与其文档，E5/E4复用路径无重叠。不自动rebase冻结样本。只读查看主会话“agent化框架链路”：R8已进入装配受控接入/模拟派发阶段，不把该工作树视为已合并或替代E5基线。

- 探索线拥有输入解释、原文引文、槽位/步骤修订、澄清、只读预检和计划界面。
- 主线拥有会话/分支绑定、上下文与工具权限、模型任务/统一预算、规则执行、状态提交和正式采用。
- `modelmirror.rpg.e5-plan/1`仅只读计划；导出`not_executed`、`requiresExecutionValidation=true`。`saved.meaning=save-plan-only`不是执行授权；after不是可直接派发的调度依赖；预检不是结算；player-claim不是世界事实或角色记忆。
- 未来接收器须重读当前状态/版本/规则、核对权限与原文、完成条件/依赖确认，并按主线规则逐步接收。不得批量执行整份计划或将赠送偷换为服药。
- 本轮未实现E5→R7/R8接收器，不承诺角色认知隔离或共享记忆。不阻塞R8，也不请求主线在本轮增加实现。

## 剩余限制与回退

固定样例及小规模真实补测不能证明任意自由输入、主动追问、多轮稳定性或费用下降。最多三步、每步一个条件；不支持分支树/循环/并行、世界预测、整链资源预算、NPC响应或正式执行。读取仍限独立精确绑定、无Mod配置的R5来源。

回退只关闭已核对属于本任务的独立预览进程，保留草稿/回执。没有共享数据迁移或世界交易需要撤销。发布仅提交PR，不自动合并、部署或追加真实调用。
