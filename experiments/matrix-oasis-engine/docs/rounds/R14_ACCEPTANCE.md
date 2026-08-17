# R14验收记录

状态：R14实施中；R14.2已验证，等待本地提交

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
| R14.2 Solution合同与Intent合成 | 已验证 | 本批提交，后续记录 | 见下方摘要 |
| R14.3 确定性空间求解器 | 未开始 | — | — |
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

## 回退

逆序revert七个R14提交；若R14.7尚未实施则只revert已存在批次。Git回退不删除仓外facts、solution、overlay、capture或run。
