# R13验收记录

状态：R13实施中；R13.5已验证，等待本地提交

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
| R13.3 Spatial Intent与Environment Facts合同 | 已完成 | `059f215daa622df65f3d0954d403b458bcc9a377` | 见下方摘要 |
| R13.4 Godot环境分析器 | 已完成 | `ddb5f76e74b0438e477cfdd18a99a244ac4fefdd` | 见下方摘要 |
| R13.5 Node Harness与离线资格 | 已验证 | 本批提交，SHA由R13.6记录 | 见下方摘要 |
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

## R13.4验证摘要

- 新增私有`@matrix-oasis/prototype-environment-analyzer@0.1.0-r13`，公开不透明Godot analyzer句柄与单次离线分析接口；配置、请求和失败表面均descriptor-safe，内部故障只暴露`PROTOTYPE_SPATIAL_ANALYZER_INTERNAL_ERROR`。
- 新增隔离`apps/runtime-godot/spatial_analysis/`场景；运行时载入已复验collider GLB，并在R13.5集成门中将原始Bundle校准与已发布Spatial Assembly最终collider变换区分为显式、哈希绑定的分析变换；不引用Creator、产品预览、供应商或案例坐标。
- Godot在主线程解析source geometry，通过异步NavigationMesh bake取得拓扑；物理同步后以真实ray与capsule查询形成floor/wall anchor、净空、顶高和连通分量，所有输出先量化为毫米整数并稳定排序。
- Godot边界新增一项精确例外：只有`spatial_analysis/environment_analyzer.gd`可读写Node创建的同目录临时路径；相同动态写入在其他文件、该文件使用其他参数形状、任意绝对路径和网络/进程/环境能力仍被拒绝。定向Godot边界16项全部通过。
- `npm.cmd run verify:r13`通过：参考6项、合同9项、分析器5项及Godot 4.6.3 import均通过。未设置`GODOT_BIN`时只在import前置门以`GODOT_4_6_3_NOT_AVAILABLE`停止；以进程内环境变量指向已验证仓外工具后通过，未写系统配置。
- 最新树上`npm.cmd run verify`全部24步通过；全量Node测试748/748，既有Godot import/adapter/parity/3D/Scene/Splat与Creator build/smoke全部通过。`check:boundary`为`checked=1126 tracked=1118`，`check:godot-boundary`为`checked=43`，`check:round-scope`为`checked=55 changed=52`，`git diff --check`通过。
- R1–R12、Creator和既有Godot产品场景保持零差异；本批尚未执行真实环境分析或发布facts，这些属于R13.5离线资格门。

本批回退只删除新analyzer workspace与隔离Godot分析目录，并恢复root验证及Godot边界的精确例外；不改变任何既有产品预览或运行数据。

## R13.5验证摘要

- 新增三项安全CLI、事务核心、真实Godot合成验证器与离线SVG捕获；输入只接受已复验的Spatial Intent、Spatial Environment Bundle、collider、compressed PLY及可选已发布Spatial Assembly，输出仅在`C:\tmp`同父staging中写入两份canonical facts/report并单次rename发布。
- 真实集成发现并关闭合同缺口：R11旧中性缓存的原始collider约1.3 m高，而实际运行世界由已发布Spatial Assembly显式放大；facts现记录`analysisTransform`的profile、来源SHA、YXZ root及collider变换。未知/隐式fit被Schema拒绝，禁止按包围盒猜尺度或注入案例坐标。
- floor anchor在物理ray命中后必须仍位于原属NavigationMesh polygon且高度差不超过固定200 mm floor snap，再执行真实capsule净空；多层/重叠几何不再产生合同外anchor。
- 锁定Godot 4.6.3 Windows工具链的中性矩形房间与L形双空间各重复20次，`SPATIAL_ANALYSIS_OK cases=2 runs=40 facts=rectangle:1:86:64,l-shape:2:67:67`，每个case的canonical facts字节完全一致。
- R11 `primary-density-v8`真实缓存成功：169 vertices、225 polygons、9 components、1339 floor anchors、620 wall anchors，facts SHA-256为`57af1fa171ee160100e94f58871e277c656d2dac247dbe9bf06ae4579ba6fa36`。
- R12 Creator v3末班地铁真实缓存成功：57 vertices、44 polygons、7 components、55 floor anchors、81 wall anchors，facts SHA-256为`d42e35b05eccaa0268e941099eaf16e875a18a4401785f5393f573fc1b76ea95`。两份facts均使用`spatial-assembly-collider-v1`且生成仓外navmesh/anchor SVG证据。
- `npm.cmd run test:spatial-analysis`为12/12；`npm.cmd run verify:spatial-analysis`通过Godot import与40次分析；`npm.cmd run verify:r13`完整通过，标识`interfaces=6 mvp=pending-spatial-solver`。
- 最新树上`npm.cmd run verify`全部24步通过；全量Node测试751/751，既有R1–R12、Creator build/smoke及Godot import/adapter/parity/3D/Scene/Splat回归全部通过。`check:round-scope`为`checked=75 changed=59`，`check:boundary`为`checked=1133 tracked=1126`，`git diff --check`通过。

本批回退删除Node harness、CLI、真实资格测试及集成修复；仓外source/facts/capture目录不随Git revert删除。

## 最终证据清单

- 六个线性提交与精确变更路径；
- 自动验证、standalone extraction、父仓范围与父client clean build；
- 中性矩形房间、L形双空间、R11缓存和R12缓存的仓外facts与调试捕获；
- source/split/archive SHA及两份真实facts SHA；
- 未运行项、已知限制和逆序revert路径。
