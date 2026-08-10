# R5 验收记录

状态：R5.3 已验证，等待本地提交；尚未达到人工验收门。

固定 `R5_BASE_SHA`：`d47f1b15e5610f41d4d9f3e5fe91966530a1a4be`

## 批次

| 批次 | 状态 | 本地提交 |
| --- | --- | --- |
| R5.1 治理与适配合同 | 已完成 | `f3dae37` |
| R5.2 严格 Runtime Pack 载入器 | 已完成 | `21ed3b3` |
| R5.3 独立 GDScript 执行器 | 已验证 | 本批提交；SHA 在 R5.4 记录 |
| R5.4 跨运行时差分 Harness | 未开始 | 待记录 |
| R5.5 最小 Runtime 调试 HUD | 未开始 | 待记录 |
| R5.6 拆分与验收收口 | 未开始 | 待记录 |

## R5.1 证据

R5.1 只修改治理、合同、范围门、测试名称与模块根版本，不创建 Runtime 源码、scene 或生成 Pack。

- 固定基线：`d47f1b15e5610f41d4d9f3e5fe91966530a1a4be`；R4 模块树与已验收 split tree `e8ead0d6cf1b365d8b753488087088c5c8952422` 一致。
- 独立分支/worktree：`codex/matrix-oasis-r5-godot-runtime-adapter`，`C:\\tmp\\modelmirror-matrix-oasis-r5`；初始工作树 clean。
- `npm.cmd ci --offline --no-audit --no-fund`：84 packages，退出 0；未联网，锁文件除模块根版本外无依赖变化。
- `npm.cmd prefix` 精确指向模块根；`npm.cmd ls --all` 退出 0，无 missing/extraneous。
- `node --test tests/round-scope.test.mjs tests/boundary.test.mjs`：101/101 通过。
- `npm.cmd test`：402/402 通过；`GODOT_BIN` 指向 R4 已核验仓外 Godot 4.6.3 console executable。
- `npm.cmd run verify`：12/12 通过；包含 Godot import、4 项 GdUnit、headless smoke、402 项 Node 测试、247-module Creator build 与 HTTP 200 smoke。沙箱内首次 GdUnit 因 Godot 无权写 `user://logs` 失败；使用相同仓外二进制在受控非沙箱环境重跑后通过，未修改源码、vendor 或共享进程。
- `npm.cmd run check:round-scope`：通过；所有改动均为 R5 allowlist，R1–R4 冻结路径零差异。
- `npm.cmd run check:parent-scope -- --base d47f1b15e5610f41d4d9f3e5fe91966530a1a4be`：通过；父仓路径零差异。
- `npm.cmd run check:boundary`：通过；765 个模块文件受检，761 个 tracked 文件，零违规。
- `git diff --check`：通过；未暂存、未提交、未 push，未运行父后端、Docker 或共享栈。

本批回退为单独 revert 治理提交；它不修改 R4 Bootstrap、GdUnit vendor、Creator、R1–R3 包、示例或父仓文件。

## R5.2 证据

R5.2 新增独立 GDScript 严格 JSON 解码、Runtime Pack/Receipt 固定合同与语义检查、只读载入器、opaque prepared handle、Godot probe、GdUnit 测试及 Node 临时编译验证。生成的 Runtime Pack 与 Receipt 只写 `C:\\tmp` 一次性目录，不提交产物。

- 精确变更为 15 个模块内文件；新增源码仅位于 `apps/runtime-godot/runtime/**`、测试仅位于 `apps/runtime-godot/test/r5/**` 及 R5 allowlist 中的 Node harness/文档；R1–R4 冻结路径和父仓路径零差异。
- 严格入口在解码前执行 16 MiB/16 KiB 限额、BOM 与字节级 UTF-8 检查；随后拒绝非规范键序、重复/未知字段、空白、注释、尾逗号、浮点、指数、越界整数、非规范转义、深度 257 和孤立 UTF-16 代理项。
- Runtime Pack 0.1.0 与 Receipt 0.1.0 的固定格式、union、ID、typed index、变量类型、condition depth、图可达性、compiler/profile、UTF-8 byteLength 与 SHA-256 均在 Godot 内独立检查；Receipt 明确不是签名。
- 公开失败结果只含只读静态 `phase/severity/code/path/message`；成功结果只含只读 result 与 opaque prepared handle，调用方取得的 Pack 副本不会污染内部数据。
- `npm.cmd run verify:godot:adapter`：退出 0，Godot 4.6.3 下 9/9 用例通过；两个冻结样例合法载入，非规范 JSON、非法 UTF-8、坏 hash、非法 index、未知字段、孤立代理项和越界整数均按稳定代码拒绝。
- `npm.cmd run test:godot`：退出 0，R5 adapter 7 项与冻结 R4 foundation 4 项合计 11/11 通过；GdUnit harness 读取最终总汇总，拒绝任一 suite/case 计数或失败状态不一致。
- `npm.cmd run check:godot-boundary`：`GODOT_BOUNDARY_OK checked=8`；动态文件读取只允许严格载入器以 `FileAccess.READ` 读取已批准参数路径，其他第一方脚本仍拒绝网络、进程、环境变量、动态脚本、文件写入和本机绝对路径字面量。
- `npm.cmd test`：407/407 通过；`npm.cmd run verify`：12/12 通过，包含完整 Godot 门、冻结 R1–R3 门、247-module Creator build 与 HTTP 200 smoke。
- `npm.cmd run check:round-scope`、`npm.cmd run check:parent-scope -- --base d47f1b15e5610f41d4d9f3e5fe91966530a1a4be`、`npm.cmd run check:boundary` 与 `git diff --check` 全部退出 0；boundary 为 `checked=774 tracked=765`。
- 本批未运行父后端、Docker、共享栈或父路由；未创建 scene、HUD、执行器、玩法、存档、网络或正式资产。

本批可单独 revert 回到 R5.1 治理状态；R4 Bootstrap、GdUnit vendor、Creator、R1–R3 实现与冻结样例保持不变。

## R5.3 证据

R5.3 新增独立、同步、纯内存的 GDScript Runtime 执行器，公开 create、inspect 与单步 apply 三个入口；不导入或复制 JavaScript evaluator，不创建 scene、HUD、文件写入、网络或题材分支。

- 精确变更为 6 个模块内文件：执行器、8 项 GdUnit 语义测试、3 个 Node/边界护栏文件及本验收记录；R1–R4 冻结路径、Creator、示例和父仓路径零差异。
- snapshot v1 绑定 source 与 artifact 身份；所有输入先复制和严格校验，所有成功/失败结果递归只读，调用方修改返回副本不会污染 prepared 数据。
- 九种 condition、三种 effect、两种 typed target、入口/effect/目标 Cue 顺序、重复 Cue、单步推进、显式循环、step limit、结束态和零可用 action 均按冻结 R3 语义实现。
- `set/add` 按声明顺序读取工作副本；正负安全整数溢出整步原子回滚，不迁移、不增加步数、不返回 Cue。预期失败沿用静态 `PACK_RUNTIME_*` code/path；内部故障固定为 `PACK_GODOT_RUNTIME_INTERNAL_ERROR`。
- prepared 每次调用都重验 Runtime Pack 结构、Receipt 结构、canonical bytes、byteLength 与 artifact SHA-256；prepared 内部 Pack 被替换时固定拒绝。
- `npm.cmd run verify:godot:import` 与 `npm.cmd run test:godot`：Godot 4.6.3 下退出 0；R5 session 8 项、R5 adapter 7 项与冻结 R4 foundation 4 项合计 19/19 通过。
- `npm.cmd run check:godot-boundary`：`GODOT_BOUNDARY_OK checked=10`；仅新增固定 runtime 诊断 JSON Pointer 白名单，`/opt/outside` 等真实本机绝对路径负例仍被拒绝。
- `npm.cmd test`：在固定 `GODOT_BIN` 环境下 409/409 通过；`npm.cmd run verify`：12/12 通过，包含完整 Godot 门、冻结 R1–R3 门、247-module Creator build 与 HTTP 200 smoke。
- `npm.cmd run check:round-scope` 与 `npm.cmd run check:parent-scope -- --base d47f1b15e5610f41d4d9f3e5fe91966530a1a4be`：均为 `checked=37 changed=33`；`npm.cmd run check:boundary`：`checked=776 tracked=774`；`git diff --check` 通过。
- 本批未运行父后端、Docker、共享栈或父路由；未实现跨运行时 trace/parity、runtime scene、HUD、玩法、存档、网络或正式资产。

本批可单独 revert 回到 R5.2 严格载入器状态；它不修改 R4 Bootstrap、GdUnit vendor、Creator、R1–R3 包或冻结样例。

## 最终仓外标识

最终 HEAD、split tree、archive SHA-256、Godot/GdUnit 供应链标识、截图与详细日志只记录在提交后的仓外交付清单，避免本文自引用。

用户明确回复“R5验收通过，可以创建PR”前不 push、不创建 PR。
