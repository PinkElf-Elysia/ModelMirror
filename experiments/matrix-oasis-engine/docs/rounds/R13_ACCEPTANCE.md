# R13验收记录

状态：R13实施中；R13.1已验证，等待本地提交

## 固定基线

- `R13_BASE_SHA=77ec8c4eace9f8dbd1dd119cd70727570bd99e9a`
- 分支：`codex/matrix-oasis-r13-spatial-facts`
- worktree：仓外独立R13 worktree
- 版本：`0.13.0-r13`

## 批次

| 批次 | 状态 | 提交 | 证据 |
|---|---|---|---|
| R13.1 治理与声明门 | 已验证 | 本批提交，SHA由R13.2记录 | 见下方摘要 |
| R13.2 开源参考来源护栏 | 未开始 | — | — |
| R13.3 Spatial Intent与Environment Facts合同 | 未开始 | — | — |
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

## 最终证据清单

- 六个线性提交与精确变更路径；
- 自动验证、standalone extraction、父仓范围与父client clean build；
- 中性矩形房间、L形双空间、R11缓存和R12缓存的仓外facts与调试捕获；
- source/split/archive SHA及两份真实facts SHA；
- 未运行项、已知限制和逆序revert路径。
