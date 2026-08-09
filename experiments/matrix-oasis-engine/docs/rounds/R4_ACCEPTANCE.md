# R4 验收记录

状态：R4.2 已验证，等待本地提交；尚未达到人工验收门。

固定 `R4_BASE_SHA`：`df4a4b53e1f03f81fbf5a041065dc1443158c472`

## 批次

| 批次 | 状态 | 本地提交 |
| --- | --- | --- |
| R4.1 治理与冻结迁移 | 已完成 | `6370008` |
| R4.2 Godot 工具链与最小工程 | 已验证 | 本批提交，SHA 在 R4.3 记录 |
| R4.3 GdUnit4 与来源护栏 | 未开始 | — |
| R4.4 自动验证、MCP 资格与固定帧 | 未开始 | — |
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

## 最终仓外标识

最终 HEAD、split tree、archive SHA-256、Godot SHA-512、GdUnit tree hash、截图和详细日志只记录在提交后仓外交付清单，避免本文自引用。

用户明确回复“R4验收通过，可以创建PR”前不 push、不创建 PR。
