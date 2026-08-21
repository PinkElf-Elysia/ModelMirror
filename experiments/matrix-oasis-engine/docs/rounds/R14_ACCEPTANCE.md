# R14验收记录

状态：R14实施中；R14.6单一真实案例人工预览无明显阻塞，泛化与Creator产品链仍未通过，R14.7未开始

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
| R14.3 确定性空间求解器 | 已完成 | `ca7ca3da` | 见下方摘要 |
| R14.4 Godot最终物理复验 | 已完成 | `6a3c85bb` | 见下方摘要 |
| R14.5 solved overlay与预览 | 已完成 | `2a481e0a` | 见下方摘要 |
| R14.6 泛化与人工预览资格 | 部分完成，不满足退出门 | 未提交 | 末班地铁显式离线预览人工通过；第二真实环境与Creator等效链未通过 |
| R14.7 默认切换与初版收口 | 未开始 | — | 声明门保持关闭 |

## 声明门

R14.7前必须保持`pending-spatial-solver / claimAllowed=false / blockingRound=R14`。自动测试、headless复验或单一案例均不能替代用户对中性与末班地铁两类案例的完整人工验收。

## PR前事实边界（2026-08-21）

- 当前人工通过的结果来自已验证缓存的显式离线求解、物理复验与直接R14预览；它不是由Creator自然语言入口端到端生成。Creator仍使用历史默认预览，R14预览操作明确为offline-cache-only，因此用户目前不能通过Creator复现同等结果。
- 一方R14求解器、验证器、cache与Godot wrapper中没有末班地铁题材ID、文案、固定run/hash、绝对临时路径或案例坐标；临时资格脚本已从提交候选移除，并由测试拒绝重新引入。
- 地面统一、视觉安全边界、资产接地和终端布局采用通用几何/碰撞/导航证据；但最新组合只在末班地铁真实环境完成了人工验收。第二个真实环境尚未使用同版visual-safety与flat-support链重新资格，因此稳定泛化仍未证明。
- 当前实现仍包含固定视觉占用采样阈值、单一全局支撑高度、旧Spatial Assembly walkable envelope兼容入口等受限假设；非平面、多层或不同噪声分布的环境可能失败，不能据此宣称通用空间求解完成。
- 本轮不补Creator接线、不切换`preview:prototype`、不修改MVP声明；上述缺口必须在后续批次或PR审阅中如实保留，不能以现有绿测或单案例人工结果覆盖。

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

## R14.4验证摘要

- 新增私有、`UNLICENSED`的`prototype-spatial-verifier@0.1.0-r14`，公开面仅包含`createGodotSpatialSolutionVerifier`、`verifyPrototypeSpatialSolution`和固定operational error；类型直接引用权威Solution合同，不复制合同结构，lock只增加一个workspace link且无新增registry依赖。
- Node桥先复验canonical Intent、Facts、Solution、Runtime/Receipt、Asset Bundle及所有环境/资产字节的身份与哈希，再复制到一次性Godot工程；验证失败返回冻结静态diagnostics，进程或引擎故障只暴露`PROTOTYPE_SPATIAL_VERIFIER_INTERNAL_ERROR`。
- 独立`spatial_solution_verification`场景通过`GLTFDocument`载入真实环境collider和资产GLB，显式应用Solution的Godot YXZ世界变换；等待physics与NavigationServer同步后，使用`query_path`、Capsule `intersect_shape`/`cast_motion`和真实R6 Action Terminal复验接地、穿透、资产重叠、spawn净空、路径端点、terminal碰撞与3 m视线。
- 中性12×12 m真实Godot集成夹具验证2个placement、5个node context、5条路径与9个terminal；重复成功报告字节一致，placement移入墙体稳定拒绝为`PROTOTYPE_SPATIAL_VERIFY_ASSET_PENETRATION`，资产byte漂移在启动Godot前稳定拒绝。
- `verify:r14`为38/38定向Node测试并通过真实Godot集成；完整`npm.cmd test`在锁定`GODOT_BIN`、允许测试于`C:\\tmp`创建自有临时夹具的环境中为782/782，最终完整`npm.cmd run verify`为25/25阶段通过，包含Creator 248 modules生产构建与HTTP 200烟测。沙箱内首次全量仅因34项`mkdtemp C:\\tmp`被拒及未注入Godot路径失败，按原命令无沙箱重跑后全部消除，未改冻结测试规避证据。
- TypeScript声明严格解析、Node语法、workspace依赖树、Godot import、boundary与Godot boundary、`git diff --check`均通过；一方Verifier源码无网络、供应商、题材ID、随机布局或产品场景依赖。
- 本批未创建R14.5 solved overlay，未修改Creator、旧preview或任何产品Godot场景，也未进行人工图形验收；`MVP_STATUS`继续为`pending-spatial-solver / claimAllowed=false / blockingRound=R14`。

## R14.5验证摘要

- 新增独立`solved-runs/<R10 source run>/<solution hash>/` overlay与`solved-current.json`；发布使用同父staging、独占FileHandle、bigint身份/状态复验和单次目录rename，current最后原子替换。R10/R11既有run与current均不写入，失败或漂移不会替换现有可运行结果。
- 每次恢复、cache查找和启动均重新取R10+R11来源交集，并复验canonical Intent、Facts、Solution、Solver report、Godot verification report、Runtime/Receipt、真实Asset Bundle、Spatial Assembly和全部预览资产；verification计数、identity或任一字节漂移均使overlay失效。
- 新增显式`preview:r14`离线入口和独立`solved_spatial_prototype` Godot wrapper。wrapper只在Compute guard、冻结R11空间载入、Solution严格解码和Godot复验证据全部通过后输出`MATRIX_OASIS_R14_SOLVED_SPATIAL_READY`；不读panorama、不调用供应商、不回退R12 AABB网格或隐藏ground常量。
- Godot运行时使用Solution世界坐标放置人物/道具、player spawn和Action terminal；初始/reset使用已验证spawn，普通node transition默认保留玩家位置，仅当新显隐资产真实footprint与玩家重叠时使用该node安全spawn，ending和冻结Runtime语义保持不变。
- `verify:r14`共42项Node测试通过，并通过真实Godot物理复验夹具（2 placements、5 nodes、5 paths、9 terminals）；R14 preview定向4/4、Godot import、Godot一方源码边界、模块boundary、round/parent scope、workspace依赖树与`git diff --check`均通过。
- 最终完整`npm.cmd run verify`在锁定`GODOT_BIN`且允许模块于`C:\\tmp`创建自有临时目录的环境中为25/25阶段通过，含786/786 Node测试、Godot 4.6.3全门、Creator 248 modules生产build和HTTP 200 smoke。此前一次长链后段重复Godot import瞬态返回`GODOT_COMMAND_FAILED`，原`verify:spatial-analysis`立即完整复跑通过，最终全量重验消除该瞬态，未修改冻结实现掩盖证据。
- 本批未修改Creator默认预览、旧显式预览或MVP声明。R14.6仍需用中性与末班地铁真实缓存生成overlay并完成用户人工图形验收；`MVP_STATUS`继续为`pending-spatial-solver / claimAllowed=false / blockingRound=R14`。

## 回退

逆序revert七个R14提交；若R14.7尚未实施则只revert已存在批次。Git回退不删除仓外facts、solution、overlay、capture或run。
