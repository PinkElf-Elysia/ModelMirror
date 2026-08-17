# R14验收记录

状态：R14实施中；R14.3已验证，等待本地提交

## 基线

- `R14_BASE_SHA=296e560d5197ff1367ad75455b2b9f5852560fd8`
- 分支：`codex/matrix-oasis-r14-spatial-solver`
- worktree：仓外独立R14 worktree
- 版本：`0.14.0-r14`
- 供应商调用：禁止；全部资格复用已验证缓存

## 批次

| 批次 | 状态 | SHA | 证据 |
|---|---|---|---|
| R14.1 治理与声明门 | 已完成 | `1086293c` | 见下方摘要 |
| R14.2 Solution合同与Intent合成 | 已完成 | `474a7b2c` | 见下方摘要 |
| R14.3 确定性空间求解器 | 已验证 | 本批提交，后续记录 | 见下方摘要 |
| R14.4 Godot最终物理复验 | 未开始 | — | — |
| R14.5 solved overlay与预览 | 未开始 | — | — |
| R14.6 泛化与人工预览资格 | 未开始 | — | — |
| R14.7 默认切换与初版收口 | 等待用户人工验收 | — | — |

## 声明门

R14.7前必须保持`pending-spatial-solver / claimAllowed=false / blockingRound=R14`。自动测试、headless复验或单一案例均不能替代用户对中性与末班地铁两类案例的完整人工验收。

## R14.1验证摘要

- 20个变更路径全部位于模块R14精确allowlist；R1–R13、历史Creator/Godot产品场景、examples、vendor和父仓相对固定基线零差异。
- `npm.cmd ci --offline --no-audit --no-fund`安装122个锁定包；`npm.cmd prefix`与`npm.cmd ls --all`退出0。
- 治理定向门共164项通过；`check:round-scope`为`checked=20 changed=20`，`check:parent-scope`使用固定`R14_BASE_SHA`通过，`check:boundary`为`checked=1137 tracked=1133`，`check:mvp-claim`确认`pending-spatial-solver / false`。
- 锁定Godot 4.6.3下，既有Godot、Pack、Compiler、Runtime、Scene、R8–R13各分门及`verify:spatial-analysis`全部通过；分析夹具为2 cases、40 runs。
- 完整`npm.cmd test`为751/752；唯一失败是冻结R8 loopback provider的20 ms超时测试在请求到达fixture前触发，重复定向运行仍为调用计数0。该实现和测试相对固定基线零差异，记录为当前Windows/Node 24.18调度基线，不修改冻结R8文件掩盖证据。
- Creator生产build在允许其清理模块内`dist`的环境中通过（248 modules），smoke返回HTTP 200和三个冻结marker；`git diff --check`通过。

## R14.2验证摘要

- 新增私有、`UNLICENSED`的`prototype-spatial-solution-contracts@0.1.0-r14`与`prototype-spatial-solver@0.1.0-r14`；lock只增加两个workspace link，未增加registry依赖。
- Spatial Solution闭合合同显式绑定Intent、Facts、Runtime/Receipt、Asset Bundle和旧Spatial Assembly身份；固定单一Navigation component、zone domain、placement、spawn、terminal approach、搜索计数和硬约束证明，不包含提示词、供应商字段、路径或案例坐标。
- 离线Intent合成复验canonical Blueprint、Runtime/Receipt和Asset Bundle，逐brief执行跨合同身份检查；按Runtime node图推导对称zone邻接，environment不进入placement，character固定`human`，prop仅按真实bounds的1200 mm显式阈值分`compact/large`，不读取文案猜测wall/near/separate。
- 新增`synthesize:spatial-intent`事务CLI，只向临时根下尚不存在的目录发布一个canonical artifact；输入FileHandle身份、fatal UTF-8、大小和输出换身均fail closed。
- `verify:r14`定向22/22通过（R13合同9、Solution合同6、Intent合成与CLI7）；新声明文件strict TypeScript解析、`npm prefix`、`npm ls --all --depth=0`、boundary、round/parent scope和`git diff --check`均通过。
- 本批未实现R14.3求解算法、Godot物理复验、overlay或产品预览切换；`MVP_STATUS`继续为`pending-spatial-solver / claimAllowed=false / blockingRound=R14`。

## R14.3验证摘要

- `solvePrototypeSpatialLayout`复验canonical Intent、Facts、Runtime/Receipt和Asset Bundle及其跨合同身份；显式区分旧Spatial Assembly与直接Environment Bundle两类R13 transform来源，禁止把后者伪标成前者。
- 求解器使用整数/BigInt面积门选择单一Navigation component，以入口zone medoid和确定性最远点生成zone seeds，按导航polygon距离建立domain；同zone节点共享spawn/terminal station，不因普通node变化制造无意义传送目标。
- placement按wall优先、large/human优先、约束数和声明顺序生成最多256个候选；有界DFS最多展开100000状态，验证support、真实Asset footprint、clearance、near/separate、facing和全局非重叠；无解或超限不返回部分Solution，也不回退R12网格。
- Action terminal证明精确绑定冻结R6八列网格：单块`1250×500 mm`、列/行间距`1700/2250 mm`、网格原点`Z=-2400 mm`，并按0/1/8/9/64 action锁定整体footprint及中心偏移，避免把交互距离错误地量到整体矩形中心。
- `solve:spatial-layout`使用稳定FileHandle读取、fatal UTF-8、大小/身份复验、同父staging和单次rename，原子发布canonical Solution与脱敏report；已存在目标、换身或中途失败均fail closed。
- `verify:r14`为31/31；求解器覆盖0/2/6/7 placements、2/4/5 zones、floor/wall、互相facing、冲突约束、容量不足、身份漂移、同zone station、20次字节确定性和双文件事务发布。完整`npm.cmd test`在最终实现树为774/774；完整`npm.cmd run verify`为25/25阶段通过，包含Creator 248 modules构建与HTTP 200烟测。此前并发全量运行中冻结R8的20 ms timeout曾单次出现既有调度竞态，独立复跑1/1且最终全量已通过。
- TypeScript声明严格解析、Node语法、boundary、round/parent scope、MVP声明门和`git diff --check`通过；一方求解源码无随机、时间退出、网络、供应商调用、题材ID或案例坐标。
- 本批未实现R14.4真实Godot物理/导航/视线复验，未创建solved overlay，未接Creator或切换默认预览；`MVP_STATUS`继续为`pending-spatial-solver / claimAllowed=false / blockingRound=R14`。

## 回退

逆序revert七个R14提交；若R14.7尚未实施则只revert已存在批次。Git回退不删除仓外facts、solution、overlay、capture或run。
