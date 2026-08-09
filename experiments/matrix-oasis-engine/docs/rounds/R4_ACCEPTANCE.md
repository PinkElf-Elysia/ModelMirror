# R4 验收记录

状态：R4.1 已验证，等待本地提交；尚未达到人工验收门。

固定 `R4_BASE_SHA`：`df4a4b53e1f03f81fbf5a041065dc1443158c472`

## 批次

| 批次 | 状态 | 本地提交 |
| --- | --- | --- |
| R4.1 治理与冻结迁移 | 已验证 | 本批提交，SHA 在 R4.2 记录 |
| R4.2 Godot 工具链与最小工程 | 未开始 | — |
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

## 最终仓外标识

最终 HEAD、split tree、archive SHA-256、Godot SHA-512、GdUnit tree hash、截图和详细日志只记录在提交后仓外交付清单，避免本文自引用。

用户明确回复“R4验收通过，可以创建PR”前不 push、不创建 PR。
