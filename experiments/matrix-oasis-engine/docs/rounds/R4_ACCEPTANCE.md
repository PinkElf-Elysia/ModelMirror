# R4 验收记录

状态：R4.4 已验证，等待本地提交；尚未达到人工验收门。

固定 `R4_BASE_SHA`：`df4a4b53e1f03f81fbf5a041065dc1443158c472`

## 批次

| 批次 | 状态 | 本地提交 |
| --- | --- | --- |
| R4.1 治理与冻结迁移 | 已完成 | `6370008` |
| R4.2 Godot 工具链与最小工程 | 已完成 | `70b2504` |
| R4.3 GdUnit4 与来源护栏 | 已完成 | `d611904` |
| R4.4 自动验证、MCP 资格与固定帧 | 已验证 | 本批提交，SHA 在 R4.5 记录 |
| R4.5 拆分与证据收口 | 未开始 | — |

## R4.1 证据

本批严格变更 20 个模块内路径：治理/架构文档、schema v4 边界、模块根版本和 lock 根元数据、round/boundary core 及其测试。未创建 Godot 工程、addon、二进制或 MCP 配置。

已执行并通过：

- `npm.cmd ci --offline --no-audit --no-fund`：84 packages，退出 0；未联网，lockfile 无依赖变化。
- `npm.cmd prefix` 精确指向 R4 模块根；`npm.cmd ls --all` 退出 0，无 missing/extraneous，仅既有 optional dependency 与 esbuild allow-scripts 提示。
- `node --test tests/round-scope.test.mjs tests/boundary.test.mjs`：89/89 通过；批准的 Godot/GdUnit 路径正向通过，根目录 Godot 文件、未知 addon、二进制、旧轮权威改动及父仓路径均精确拒绝。
- `npm.cmd run check:round-scope`：`ROUND_SCOPE_OK checked=20 changed=20`。
- `npm.cmd run check:parent-scope -- --base df4a4b53e1f03f81fbf5a041065dc1443158c472`：`PARENT_SCOPE_OK checked=20 changed=20`。
- `npm.cmd run check:boundary`：`BOUNDARY_OK checked=144 tracked=139`。
- `git diff --check`：退出 0；Creator、examples、全部 packages、R0–R3 ADR/验收相对基线零差异。

普通/full doctor 与完整 `verify` 本批未宣称通过：schema 已把 Godot 4.6.3 提升为 R4 必需工具，但外部引擎要到 R4.2 才准备。回退本批提交只恢复 R3 治理、模块根版本和文档，不触碰父仓或任何运行数据。

## R4.2 证据

本批新增最小 `apps/runtime-godot/` 工程、Godot 进程 harness、严格 doctor 与 import/smoke 命令，并同步 `.gd.uid` 源码身份边界。工程固定 Forward+、960×540、GDScript、内建 primitive 和唯一 readiness marker；未加入 addon、Runtime Pack、玩法、网络、资产或父项目适配。

仓外工具固定为官方 Windows 标准版 `4.6.3.stable.official.7d41c59c4`，`GODOT_BIN` 指向仓外工具缓存。官方 `SHA512-SUMS` 核验值为：

`d44ea7ef5bab754cacd49d581b6062836b2eea12a82e1d183aebfad9cd8c7db2bd82513337bd657d6d2d5c04d46239c0570b029faf1343e81e8a2fa7b85dd83b`

已执行并通过：

- `node --test tests/doctor.test.mjs tests/godot-harness.test.mjs tests/boundary.test.mjs`：72/72；含 exact 4.6.3、Forward+、场景节点、marker、`.gd.uid` 正向与工程外拒绝。
- `npm.cmd run verify:godot`：严格 doctor、editor headless import、headless smoke 全部退出 0；分别输出 `GODOT_IMPORT_OK` 与 `GODOT_SMOKE_OK`。
- `npm.cmd run verify`：12/12 steps；完整 Node tests 368/368，Creator 247 modules build、HTTP 200 smoke 及 R1–R3 全链通过。
- `npm.cmd run check:boundary`：`BOUNDARY_OK checked=151 tracked=144`。
- `npm.cmd run check:round-scope`：`ROUND_SCOPE_OK checked=39 changed=30`；`check:parent-scope` 同为 39/30。
- `git diff --check` 退出 0；R1–R3 packages、examples、Creator 与历史验收相对固定基线零差异。

受限沙箱不能创建 Godot 用户缓存时会以权限错误退出；在不改系统目录、不改环境值的正常进程权限下，同一命令已通过。`.godot/` 全部忽略且未跟踪。回退本批提交会移除独立 Godot 工程和 R4.2 harness，保留 R4.1 治理，不影响父仓和现有数据合同。

## R4.3 证据

本批原样 vendoring GdUnit4 `v6.2.0` / commit `d18770221c2df4a3c991a42fdce7907df40eea75` 的 `addons/gdUnit4/**`，保留 MIT 许可证，并新增机器可读来源锁、tree verifier 和四个中性工程测试。上游源码未修改；Godot 操作在仓外一次性工程副本中运行，避免导入器为 addon 写入 `.uid/.import` 派生文件。

固定供应链标识：

- 官方 tag 源归档 SHA-256：`74e00f49e245b9b0c1599d1359d0ea88d1a867d05d7e5b12fa982bc4ca312a1a`。
- 原样 addon：599 files、2,294,905 bytes。
- `matrix-oasis.vendor-tree/1` SHA-256：`4b1904e747517348cc05134d45b91e7244c92923fb4b6823e700fa4f255664ab`。
- MIT LICENSE SHA-256：`6be2166fe758ee8fbbc76cf676467cdb68a4756ed0ea079abc9eb987fc92bb7f`。

已执行并通过：

- `npm.cmd run verify:vendor`：精确 commit/tag/license/archive/tree 全部匹配；新增、删除、字节漂移和 junction 负向测试均拒绝。
- `node --test tests/godot-harness.test.mjs tests/vendor.test.mjs`：11/11；GdUnit 输出必须精确为 1 suite、4/4 tests、零 errors/failures/flaky/skipped/orphans。
- `npm.cmd run test:godot`：Godot 4.6.3 同一仓外副本先 import 后执行四个 GdUnit 测试，退出 0；测试项目设置、主场景、关键节点、readiness/smoke 合同和资源引用边界。
- `npm.cmd run verify:godot`：doctor → vendor → disposable import → GdUnit → smoke 全链退出 0；正式 vendor 前后 tree hash 不变。
- `npm.cmd test`：375/375；边界正向允许精确 vendor，第一方规则不扫描 vendored 源码，未知 addon 与 vendor 外 `.scn` 仍拒绝。
- `npm.cmd run verify`：12/12 steps；完整 375/375 Node tests、Godot/GdUnit、R1–R3、Creator 247 modules build 与 HTTP 200 smoke 全部通过。

R4.3 不新增 npm registry 依赖、不改 lockfile、不加入导出模板或平台二进制。若回退本批提交，将移除 vendored addon、来源锁、GdUnit 测试和供应链 harness；R4.2 最小工程仍可独立 import/smoke。

## R4.4 证据

本批新增第一方 GDScript 能力门、固定帧 Movie Maker harness 与仓外 MCP 资格脚本。正式 Godot 工程只增加 capture 参数和既有 GdUnit 测试内的合同断言；未加入网络、MCP addon/config、Runtime Pack、玩法或资产。

已执行并通过：

- `npm.cmd run check:godot-boundary`：`GODOT_BOUNDARY_OK checked=2`；12 个定向测试覆盖正式源码正向与网络、Socket、进程、环境变量、绝对路径、动态加载和文件写入负例。GdUnit4 不参与第一方扫描，继续由 vendor hash 控制。
- `npm.cmd run verify:godot`：严格 doctor → vendor integrity → 第一方 boundary → disposable import → 4 个 GdUnit → headless smoke 全链退出 0。
- `npm.cmd run capture:godot -- --output C:\tmp\matrix-oasis-r4-capture-r44`：Forward+、960×540、30 FPS、12 张 PNG 全部有效；单帧 11,847 bytes，SHA-256 `6c54ab454a3cd2a0c3db8bc923ced157c8a1ab49eeec73f1d070c11993409bc6`。已目视确认中性 primitive、地面、光照与相机；不做跨 GPU 像素 golden。
- `npm.cmd run qualify:godot-mcp -- --output C:\tmp\matrix-oasis-r4-mcp-r44e`：两项固定 npm integrity、MIT、stdio 握手、工具枚举、进程退出和测试前后源树哈希通过，树哈希前后均为 `425c112fb7a35db1e134189dc32f7dcbeda8b60ac17204a8920479af1fa0014a`。minimal `0.1.6` 的 4 工具与真实 LSP 扫描通过，记为后续只读候选；satellite `4.1.0` 的 12 个只读工具通过但 headless 编辑器项目观察未就绪，明确延后。
- `npm.cmd test`：390/390；`npm.cmd run verify`：12/12 steps，Godot/GdUnit、R1–R3、Creator 247 modules build 与 HTTP 200 smoke 全部通过。
- `npm.cmd run check:round-scope`：`ROUND_SCOPE_OK checked=651 changed=641`；`check:parent-scope` 同为 651/641；`npm.cmd run check:boundary`：`BOUNDARY_OK checked=761 tracked=761`。

图形帧、MCP 安装树、一次性项目和详细 JSON 报告全部位于仓外；自动 `verify` 不依赖 GPU 或 MCP 网络安装。回退本批提交只移除 R4.4 checker、capture/qualification harness 和相关文档，保留 R4.3 的可验证 Godot/GdUnit 工程。

## 最终仓外标识

最终 HEAD、split tree、archive SHA-256、Godot SHA-512、GdUnit tree hash、截图和详细日志只记录在提交后仓外交付清单，避免本文自引用。

用户明确回复“R4验收通过，可以创建PR”前不 push、不创建 PR。
