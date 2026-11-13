# C1-A 多模型对比协调器核心

## 目标与范围

本批完成公共上下文冻结、两层整批预留、两路并发、候选持久化、唯一采用及中断恢复的共享核心，**仅通过离线验收**。正式控制面 HTTP 批次预留、创作与六卡宿主适配器仍待下一子批接入；本批不等于整个 C1 或双端功能完成。

- 基线：`d9f20fe691e8ddef8623d779a25a9359717f2e8d`；分支 `codex/rpg-comparison-coordinator`。
- 工作树：`C:/tmp/modelmirror-rpg-comparison-coordinator`，继承用户已通过的 P1–P2-C5。继承清单与初始 hash 位于 `C:/tmp/modelmirror-rpg-c1-evidence/inherited-source.json`。
- 保留 18662、18664、18666、18668；不修改共享栈、旧会话、资源和账本，不认证、不调用真实模型、不提交/推送/PR。
- 已核对 joint-session、joint-plan、joint-budget、controlled-provider、session-storage 和 P0 合同。现有正式控制面逐请求计数，原顺序账本不能直接充当整批并行预留。
- 验收方式在实现前确定：持久离线宿主、两个独立账本、受控假 Provider、故障注入及真实子进程退出恢复。无 UI 新增，也没有新增二级交互。

## 五个新增文件

| 文件（相对仓库根目录） | 职责 |
| --- | --- |
| `experiments/ai-rpg-engine/authoring/comparison-contract.mjs` | 批次、任务、公共快照、请求与回执校验 |
| `experiments/ai-rpg-engine/authoring/comparison-ledger.mjs` | 独立持久额度账本、整批预留、派发占用与结算 |
| `experiments/ai-rpg-engine/authoring/comparison-coordinator.mjs` | 候选调度、采用/放弃、暂停及按原 ID 恢复 |
| `experiments/ai-rpg-engine/authoring/comparison-coordinator.test.mjs` | 离线持久化、并发、未知及退出恢复验收 |
| `docs/ai-rpg-experiment/generation-comparison/C1-A.md` | 本批合同、证据与后续边界 |

继承的 75 个修改/新增文件在候选和旧工作树中的内容 hash 均保持不变。本批未改现有单模型发送路径。

## 已实现合同

### 公共上下文和候选隔离

- 每批 2–4 份候选，最多同时 2 路。支持跨模型及同模型不同参数，沿用已验证的模型能力与参数合同。
- 若需 M2 发送前补齐，先执行一次补齐及必要压缩；随后冻结玩家输入、完整来源历史、持续要求、变量、M2、M3、世界书判定和最终 messages。
- 所有剧情候选使用同一份 messages 及 messages hash；逐模型检查容量。任何候选容量不满足时，候选均不派发，不裁剪公共上下文。
- 候选分别保存请求、参数、原文、hash、回执和状态，未采用内容不进入正式历史、变量、CG、M2 或 M3。
- 候选进度 revision 与正式上下文 revision 分离。其他候选完成不使合法采用失效；不同标签采用不同结果只能有一个成功。

### 额度与派发

- 派发前按具体计划预留本地、控制层两份额度；本批用两个独立的 `ComparisonLedger` 实例验证，不能复用同一个层级或任意对象冒充另一层。
- 计划包含候选、发送前 M2/压缩及采用后 M2/压缩/M3，各阶段独立 ID。最多四候选加五个后台槽位，即 9 次上限；实际计划按需要减项。
- 任一层不足不开始生成。预留或回执落盘结果不确定时保留记录，先核对，不推断退款。
- 确认未派发和未启动任务释放额度；已派发失败、取消或未知仍计数。仅当本进程确认从未进入 Provider dispatch 时，才能形成绑定原请求的未派发证明。
- 未知候选占用并发位置；不因超时、新标签、重启或停用重新生成。取消剩余候选只停止未派发队列；本批没有虚构 Provider 取消能力。

### 采用、放弃与恢复

- 允许提前采用已完成的一份；原文直接作为唯一正式回复提交，不重新生成，也不修改会话默认模型或参数。
- 正式回合、变量、CG 分步骤持久化幂等收据。其余候选全部收束后执行采用后的 M2 → M3；下一轮由 `assertIdle` 阻断到整批收束。
- 采用后的后台失败保留正式回合并暂停后续任务，不把失败当成功。明确放弃保留候选记录，并恢复原输入草稿；不会覆盖用户已写入的新草稿。
- 恢复先核对存储和账本，再以原操作 ID 查询 Provider。已完成的采用步骤凭收据续接，世界书不重新抽取，候选不自动重发。
- 重启恢复停止尚未派发的候选队列，保留已派发请求供核对；已明确采用的本地提交及后续任务按原计划收束。
- 已撤权结果仍留记录，不能新采用；若正式写入已经成功但确认丢失，可核对原提交并补齐已提交回合的本地变量/CG投影。

## 受信适配器要求

这些方法由宿主代码提供，不接受浏览器上传函数、供应商地址或未经控制面校验的模型能力。

| 端口 | 必须保障 |
| --- | --- |
| `inspect` / `validateChoice` | 返回当前正式上下文、授权 revision 和具体后台需求；保存与临派发前检查模型资格、能力及参数 |
| `freeze` | 在正式会话锁内检查来源/CAS，按冻结操作 ID 持久化一次装配与世界书概率结果；重复核对返回原快照 |
| `guard` | 与正式会话授权变更互斥，派发开始前核对授权；不把整个远端等待串行化 |
| `prepareTask` | 从正式原始历史构造摘要/记忆任务，绝不读取未采用候选；压缩是否需要由宿主判定 |
| `apply` / `lookupCommit` | 在正式存储中原子检查上下文 CAS、幂等 ID 与 payload hash，返回可核对提交收据；变量/CG及后台更新各自幂等 |
| `restoreDraft` | 恢复放弃的输入时保留用户更新过的草稿，并记录原操作 |
| `dispatch` / `lookup` | 只由受控 Provider 执行，验证真实回执的请求绑定及派发事实；核对不能代替重新生成 |

核心提供 `preflight/create/read/list/stop/adopt/discard/reconcile/assertIdle/wait/close`。正式 HTTP 路由及普通发送必须接入这些边界后，才能声明实际会话并发与权限联动通过。

容量明确阻断：每份公共快照最多 4 MiB、每份原文最多 512 KiB、每个会话记录最多 24 批，账本最多 1000 组，并受现有持久记录总容量限制。没有静默截断或删除；正式接线还需处理超限回执的保留与诊断。

## 验证结果

在 `experiments/ai-rpg-engine` 执行：

```powershell
node --test --test-concurrency=1 authoring/comparison-coordinator.test.mjs authoring/joint-budget.test.mjs authoring/generation-settings.test.mjs authoring/model-control.test.mjs
```

**146/146 通过，0 失败、0 跳过**，包含 44 项本批检查及 102 项相关预算、参数和控制目录回归。最终日志：`C:/tmp/modelmirror-rpg-c1-evidence/accepted-regression.log`。

重点证据：

- 同一公共 messages hash、世界书只冻结一次，两路上限、最多九槽整批预留；多会话竞争预算不会超额。
- 部分成功、提前采用、重复操作、多标签冲突、撤权、目录变化、容量失败、未知按原 ID 核对。
- 两层预留确认丢失、结果落盘失败、正式写入后确认丢失、变量/CG提交中断、后台摘要失败均有覆盖。
- 真实子进程在正式写入后以退出码 86 终止，再打开原目录恢复：新增 Provider 调用 **0**，正式回合 **1**，变量更新 **1**，CG 更新 **1**，上下文冻结 **1**。
- 初次故障测试发现并修复 `Promise.all` 提前失败后另一 worker 尚未收束的问题；随后补齐末候选返回与采用检查点竞争，防止后台任务滞留。失败日志与测试中止记录保留，不冒充首次即通过。
- 新文件语法检查、差异空白检查及最终文件范围检查通过；本批未修改前端或打包配置，未重跑前端构建。
- 18662、18664、18666、18668 的既有验收路径均返回 HTTP 200。此项证明入口可访问，不代替浏览器功能验收。

证据目录 `C:/tmp/modelmirror-rpg-c1-evidence/` 包含 `source-preservation.json`、`protected-entries.json`、`source-hashes.json`、`verification-summary.json` 和 `final-status.txt`。退出恢复完整记录位于本工作树 `.rpg04-work/comparison-c1-tests/process-wKFoig/crash-recovery-evidence.json`（相对 RPG 目录）。测试数据与日志均未纳入版本控制。

## 后续与回退

下一 C1 子批：正式控制面批次预留接口、生产共享额度适配、通用创作与六卡正式会话接线，以及对应请求捕获和离线恢复验证。C2 再接双端界面及真实浏览器深度交互；真实模型验收另行授权。本批不宣称上述内容已完成。

首选回退为不实例化本协调器，保留新记录目录供原 ID 核对。没有旧会话迁移或既有入口替换；已派发或未知任务不能通过删除新目录消除。
