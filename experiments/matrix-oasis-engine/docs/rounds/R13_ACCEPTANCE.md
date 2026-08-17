# R13验收记录

状态：R13实施中；R13.3已验证，等待本地提交

## 固定基线

- `R13_BASE_SHA=77ec8c4eace9f8dbd1dd119cd70727570bd99e9a`
- 分支：`codex/matrix-oasis-r13-spatial-facts`
- worktree：仓外独立R13 worktree
- 版本：`0.13.0-r13`

## 批次

| 批次 | 状态 | 提交 | 证据 |
|---|---|---|---|
| R13.1 治理与声明门 | 已完成 | `a849899e0606ebda85bd2060011edd4d0ef010d1` | 见下方摘要 |
| R13.2 开源参考来源护栏 | 已完成 | `43bb8f2869ea3b561677d1496bbf55f46e4484b8` | 见下方摘要 |
| R13.3 Spatial Intent与Environment Facts合同 | 已验证 | 本批提交，SHA由R13.4记录 | 见下方摘要 |
| R13.4 Godot环境分析器 | 未开始 | — | — |
| R13.5 Node Harness与离线资格 | 未开始 | — | — |
| R13.6 拆分与验收收口 | 未开始 | — | — |

## 声明边界

R13只证明空间语义意图与Godot环境事实可以被严格、离线、确定地提取。它不证明最终布局、出生点、Action终端、可玩性或初版体验已修复。`docs/MVP_STATUS.json`必须保持`pending-spatial-solver`与`claimAllowed=false`，直到R14求解器和两类案例完成验收。

## R13.1验证摘要

- 精确21个变更路径，全部位于模块和R13 allowlist；R1–R12冻结路径及父仓相对固定基线零差异。
- `node --test tests/round-scope.test.mjs tests/mvp-claim.test.mjs`：82项通过。
- `npm.cmd run check:round-scope`：`checked=21 changed=21`。
- `npm.cmd run check:parent-scope -- --base 77ec8c4eace9f8dbd1dd119cd70727570bd99e9a`：`checked=21 changed=21`。
- `npm.cmd run check:boundary`：`checked=1100 tracked=1096`。
- `npm.cmd run check:mvp-claim`：`pending-spatial-solver claimAllowed=false checked=5`。
- 在进程内显式设置仓外`GODOT_BIN`后，`npm.cmd run verify`全部21步通过；全量Node测试733项通过，Godot 4.6.3 import/adapter/parity/3D/scene/splat、Creator build与HTTP smoke均通过。
- 首次完整verify只因新终端未设置`GODOT_BIN`在doctor前置门停止；没有执行后续步骤。设置已验证的仓外Godot 4.6.3路径后同一命令完整通过，未写系统环境或仓库配置。
- `npm.cmd prefix`正确指向独立模块；`npm.cmd ci --offline`安装120个锁定依赖。`git diff --check`通过。

本批回退为单独revert治理提交；不会删除仓外工具或`node_modules`。

## R13.2验证摘要

- 固定Godogen、Holodeck、ProcTHOR和GameCraft-Bench四个精确commit；机器锁同时记录6个源文件的Git blob、完整字节数和SHA-256，以及各上游许可证身份。
- 仓内只保存4份原创非执行适配笔记和2份许可证文本；没有复制Python/C#/Godot求解器、Agent编排或replay源码，没有新增registry依赖。
- `npm.cmd run verify:spatial-references`：6项行为测试与来源完整性检查全部通过；精确4个来源、8个本地文件、6个去重后的笔记/许可证payload。
- 负向测试覆盖笔记字节漂移、未知可执行文件、commit漂移和许可证缺失；来源检查只读且不联网。
- 显式使用仓外Godot 4.6.3后，`npm.cmd run verify`全部21步通过；全量Node测试735项通过，Creator build/smoke及既有Godot import、adapter、parity、3D、Scene与Splat门全部通过。
- `npm.cmd run check:round-scope`：`checked=38 changed=34`；`npm.cmd run check:parent-scope -- --base 77ec8c4eace9f8dbd1dd119cd70727570bd99e9a`：`checked=38 changed=34`；`npm.cmd run check:boundary`：`checked=1111 tracked=1100`；`git diff --check`通过。冻结R1–R12与父仓零差异。

本批回退只删除非执行参考、验证脚本/测试与依赖说明，不影响Godot、Creator或既有运行链。

## R13.3验证摘要

- 新增私有`@matrix-oasis/prototype-spatial-planning-contracts@0.1.0-r13`，公开闭合的Spatial Intent与Environment Facts Schema、只读TypeScript类型、两项同步严格JSON验证接口和单一静态operational error。
- Spatial Intent只表达zone邻接、placement support/anchor/facing、near/separate、clearance与node context可达需求；Schema与测试拒绝坐标、路径、供应商字段和不可达布尔降级。
- Environment Facts固定Godot右手Y-up、毫米、Euler YXZ及350 mm半径/1800 mm高度/200 mm floor snap/45度坡度profile；语义门锁定导航顶点、polygon索引、连通分量、bounds、floor/wall anchor归属、真实capsule净空标识和identity。
- 验证阶段严格为`parse → schema → semantic → integrity`；非canonical文本、重复键、深度/字节超限、孤立代理项、未知字段和身份漂移均返回深冻结、排序稳定且不回显输入值的diagnostics。
- `npm.cmd run verify:spatial-contracts`：9项通过，含同一canonical Intent/Facts重复20次一致、输入不变及负向拓扑/anchor矩阵。
- 普通沙箱首次`npm.cmd test`因既有测试不能在`C:\tmp`创建临时目录而出现33项EPERM环境失败；R13合同9项全部通过。随后在允许既有仓外临时目录并显式使用Godot 4.6.3的同一树上，`npm.cmd run verify`全部23步通过，全量Node测试744/744，Creator build/smoke及既有Godot门全部通过。
- `npm.cmd run check:round-scope`、`check:boundary`、`check:mvp-claim`与`git diff --check`均通过；`pending-spatial-solver`与`claimAllowed=false`保持不变，R1–R12冻结内容零差异。

本批回退只删除新合同workspace及其root测试接线；不会改变Creator、Godot产品场景或任何既有Pack格式。

## 最终证据清单

- 六个线性提交与精确变更路径；
- 自动验证、standalone extraction、父仓范围与父client clean build；
- 中性矩形房间、L形双空间、R11缓存和R12缓存的仓外facts与调试捕获；
- source/split/archive SHA及两份真实facts SHA；
- 未运行项、已知限制和逆序revert路径。
