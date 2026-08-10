# R5 验收记录

状态：R5.1 已验证，等待本地提交；尚未达到人工验收门。

固定 `R5_BASE_SHA`：`d47f1b15e5610f41d4d9f3e5fe91966530a1a4be`

## 批次

| 批次 | 状态 | 本地提交 |
| --- | --- | --- |
| R5.1 治理与适配合同 | 已验证 | 本批提交；SHA 在 R5.2 记录 |
| R5.2 严格 Runtime Pack 载入器 | 未开始 | 待记录 |
| R5.3 独立 GDScript 执行器 | 未开始 | 待记录 |
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

## 最终仓外标识

最终 HEAD、split tree、archive SHA-256、Godot/GdUnit 供应链标识、截图与详细日志只记录在提交后的仓外交付清单，避免本文自引用。

用户明确回复“R5验收通过，可以创建PR”前不 push、不创建 PR。
