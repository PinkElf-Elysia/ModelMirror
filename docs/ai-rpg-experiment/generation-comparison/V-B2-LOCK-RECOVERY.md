# V B2 锁竞争修复与原操作恢复

## 开工合同

- 目标：已收束批次的轮询不再阻塞新批次采用；相同批次收尾合并，保持一次提交。
- 基线：`d9f20fe691e8ddef8623d779a25a9359717f2e8d`，独立 `codex/rpg-comparison-v-lock-fix`，逐文件 hash 继承 preview-fix 的 125 个未提交文件，不覆盖旧源码。
- 本批修改限定为 `studio/card-comparison.mjs`、相邻测试与本文档。无新依赖、公共接口、迁移、共享部署或候选生成。
- 原因：旧批次 GET 调用 comparisonSettled，在确认无需保存之前占用会话写锁；随后 B2 formal 提交遇到 SESSION_BUSY。
- 验收：确定性并发回归（旧查询/采用、同批收尾）、保存失败及重启后原 ID 恢复、六卡适配与 M2→M3；原始候选和上下文不变，采用只一次。
- 风险：会话持久化、中断恢复和真实后台费用。保留排他写锁，锁内重新核对状态；不把 BUSY 或未知当作免费/未发生。
- 恢复：18698 冻结原运行版本保留，独立副本修复不迁移旧会话。离线通过后暂停原页轮询，在原版本中使用原采用操作 ID 串行恢复，记录实测边界；仅允许剩余后台整理，原额度最多 4 次，不自动重试。
- 回退：停用新修复副本；旧入口、原始输出、账本与诊断快照保留，已派发结果继续按原 ID 核对。

## 验证结果

### 离线检查（通过）

工作目录：独立修复副本，未启动真实 Provider。

```powershell
node --test experiments/ai-rpg-engine/studio/card-comparison.test.mjs
node --test experiments/ai-rpg-engine/authoring/comparison-coordinator.test.mjs experiments/ai-rpg-engine/authoring/comparison-host.test.mjs experiments/ai-rpg-engine/studio/comparison-budget.test.mjs
git diff --check
```

- 修复前：两条新增并发测试失败，分别证实旧批次查询占写锁、同批收尾并发返回 SESSION_BUSY；原 ID 重启恢复基线测试通过。
- 修复后：卡片适配 7/7、协调器与账本 55/55，共 62/62；无失败、取消或跳过。差异检查通过。
- 已完成旧批次先只读核验；仅仍 pending 时获取写锁并重新读取，绝不写回锁外快照。同一批次的在途收尾共享一次保存；保存失败释放在途标记，保留显式恢复能力。
- 真正的并发写仍返回 SESSION_BUSY，不排队重发模型，不削弱运行版本、授权、原文及回执校验。

### B2 原操作真实恢复（通过）

- 原会话：`3f3e4f6d-efe8-4b95-a186-cc47c28b092b`；入口保持 18698。
- 原批次：`compare-bb21967c6ff11b984a832b1cffdc31fb414fbc92d87a2a6cf54530f99db635a9`。
- 原采用操作：`70fbd6fa-5b6d-4f95-bd89-9bf538fe5df9`。仅提交一次原 adopt 请求，未创建新的候选或批次。
- 保留原冻结运行版本：`46f868c19d64c14038ab9b2df8429a01638fdbc6b33266a7289d3cb963e18f7c`。暂停原页轮询后在原宿主串行恢复，再重新打开页面。**并发代码修复的证据来自独立副本的离线回归；没有把此次真实恢复冒称为补丁已部署到旧宿主。**
- 结果：批次 settled，诊断清空；formal、variables、cg 各一份原操作回执；正式回合由 1 变为 2，第一回合未改变，第二回合等于已选原文。
- M2：正常完成一次，整理已采用的第 1 回合，coveredThrough=1。参数 `temperature=0.2 / top_p=0.8 / max_tokens=8192`。未超限，压缩阶段释放且 request=null。
- M3：正常完成一次，只读第 2 回合完整原文和原记忆，processedThrough=2。参数 `temperature=0.1 / top_p=0.8 / max_tokens=16384`。
- 两次均为 `google/gemini-3.8-flash`、HTTP 200、finishReason=stop、retries=0。剧情默认模型、三用途参数、持续要求均未改变。
- 没有自动认证、换模型、候选再生成或再次采用；失败事件原样保留。B2 两份候选 request/result 及公共上下文与恢复前逐字段相等。

| 核对项 | 结果 |
| --- | --- |
| B2 低温候选原文 SHA256 | `1f6a3743588b17c629667c1ebfe3dcdcdf2f260d17b91610653edb8d9faf07dc`，不变 |
| B2 高温候选原文 SHA256 | `c21825dbfe791264c01c5c16abe4a90cc8855af22bbd430c1fd330a40f48ccb5`，不变 |
| M2 输出 SHA256 | `6f736c7cc76e0f29bd4784fa02b656dc70051a0c194504f66e075ebb61ecee4a` |
| M3 输出 SHA256 | `2c8f21f6a34a9b73285fdcbe57e31e07567657dba6d570d75fd78c04b7d5bbc4` |
| God 本地账本 | 7/8，reserved=0，剩余 1 |
| JP 共享 RPG 账本 | 23/25，reserved=0，剩余 2；历史已用记录保留 |
| 本轮 11 次总授权 | 已用 9，剩余 2（God 1、创作端 1） |
| 浏览器 | “已采用 · 对比已收束”；“已整理至第 2 回合”；草稿清空；无未知/待核对提示 |
| 入口保留 | 本轮登记的 22 个入口均 HTTP 200，包括 18692、18698；此项只证明可访问性 |

### 内容核对与剩余边界

M3 仅更新原“旧灯塔东侧维修门与钥匙交接”条目到 v2，引用第 2 回合：保留钥匙未插入/未转动、锁芯卡阻成因尚未查明，并把仓库钥匙及过去检修描述归于周岚的说法；未来探查仍为计划。未采用候选没有进入正式历史或整理来源。

M2 保留了较多与本轮锁修复无关的背景资料。结构、来源与幂等验证通过不等于内容无损或用户质量验收；B 组内容仍交由用户审阅。没有继续 C 组。旧验收宿主仍使用冻结代码，未来正式运行使用修复副本须另行整合，不静默迁移旧会话。

### 证据与改动文件

本机私有证据目录：`C:/tmp/modelmirror-rpg-v-live-20261107/lock-fix/`。

- `baseline.json`、`task-only.diff`、`source-manifest.json`：继承状态与本批差异/hash。
- `red-tests.log`、`card-comparison-tests.log`、`coordinator-tests.log`：红灯复现和 62 项通过日志。
- `backup-cards/`、`backup-card-budget/`：恢复前副本，不直接作为可执行恢复实例使用。
- `recovery-attempt.json`：一次性派发标记；`B2-before.json`、`B2-after-recovery.json`、`session-before.json`、`session-after.json`：原始本地证据，不纳入仓库。
- `recovery-checks.json`、`content-review.json`、`preserved-entries.json`：结果、内容边界和入口核对。
- `b2-memory-recovered.jpg`：真实浏览器记忆状态截图。

源码改动：`studio/card-comparison.mjs`、`studio/card-comparison.test.mjs` 与本文档，共 3 文件。其余 123 个继承文件未变；未 Commit、Push、PR 或共享部署。
