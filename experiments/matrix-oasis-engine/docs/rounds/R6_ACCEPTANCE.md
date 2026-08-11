# R6 验收记录

状态：R6.5 已验证，等待本地提交；standalone 与最终人工验收尚未收口。

固定基线：`430f24a4fd8510a0d54f14bcd240a80423d16719`
分支：`codex/matrix-oasis-r6-playable-3d-skeleton`
模块版本：`0.6.0-r6`

## 批次

- [x] R6.1 治理与冻结迁移
- [x] R6.2 第一人称移动骨架
- [x] R6.3 3D Action 终端
- [x] R6.4 Runtime 与 3D 世界接通
- [x] R6.5 自动验证与固定帧
- [ ] R6.6 standalone 与人工验收收口

每批记录精确 diff、命令/退出码、测试数量、冻结路径零差异、风险与回退。最终 HEAD、split tree、archive SHA-256、参考源码 SHA-256 和仓外截图只写入提交后的交付清单，避免文档自引用。

## R6.1 证据

本批精确变更 17 个模块内文件：治理/架构文档、schema v6 机器边界、R6 范围策略与正反 fixture、模块根版本/lock、ADR、威胁模型及本验收模板。不创建 playable 场景，不修改 `project.godot`。

- `npm.cmd ci --offline --no-audit --no-fund`：84 packages，退出 0；仅既有 esbuild install-script 提示。
- `node --test tests/round-scope.test.mjs tests/boundary.test.mjs`：107/107，通过。
- `npm.cmd run check:round-scope`：`ROUND_SCOPE_OK checked=17 changed=17`。
- `npm.cmd run check:parent-scope -- --base 430f24a4fd8510a0d54f14bcd240a80423d16719`：`PARENT_SCOPE_OK checked=17 changed=17`。
- `npm.cmd run check:boundary`：`BOUNDARY_OK checked=789 tracked=785`。
- 注入仓外 Godot 4.6.3 后 `npm.cmd test`：431/431，通过；首次受限运行的 4 个环境失败由 `GODOT_BIN` 缺失与 `C:\tmp` 创建权限造成，正常权限复跑已消除。
- `git diff --check` 通过；相对固定基线的父仓路径、R1–R5 冻结路径与 `apps/runtime-godot/project.godot` 均零差异。

本批提交 SHA 在 R6.2 验收记录或仓外交付清单中记录，避免提交内自引用。单独 revert 本批只移除 R6 治理与版本迁移，恢复完整 R5。

## R6.2 证据

R6.1 提交：`6c1b13e`。本批精确变更 14 个模块内文件：唯一批准的 `project.godot` 设置例外、独立第一人称控制器/player/primitive 碰撞测试舱、1 个 R6 GdUnit 测试、4 个官方参考供应链文件、vendor 验证扩展、命令接线和本验收记录。R4 主场景、R5 Runtime/HUD、GdUnit4 vendor、Creator、examples 与 packages 零差异。

- Godot 官方 demo 固定 commit `b4eff8de9d7ba5a4f1a2dea8bae60f28816b7eea`；非执行参考 2303 bytes，SHA-256 `dfda0bc36b5cfb719af3d9d104b274aff3b5387ec2c47e882178be02301bcb25`；MIT License 与适配说明独立锁定。
- `npm.cmd run verify:vendor`：GdUnit4 599 文件与官方参考均通过；`node --test tests/vendor.test.mjs`：7/7。
- `npm.cmd run verify:godot:import`：Godot 4.6.3 导入/解析通过。
- `npm.cmd run verify:godot:3d:controller`：GdUnit 通过，覆盖 60 Hz/Jolt/插值、InputMap、CharacterBody3D、碰撞层、输入归一、帧率无关加减速、pitch、重力落地、墙体阻挡与坡面稳定。
- 最新树 `npm.cmd run verify`：12/12 步通过；433/433 Node、Godot adapter 9 cases、parity 7 cases/26 runs/2 labs、Godot import/GdUnit/smoke、Creator 247 modules build 与 HTTP 200 smoke 全绿。
- `check:godot-boundary`：15 个第一方脚本通过；`check:round-scope`、`check:boundary` 与 `git diff --check` 通过。

本批不加入 RayCast3D、Action 终端或 Runtime playable lab；这些属于 R6.3–R6.4。单独 revert R6.2 恢复 R6.1 的纯治理状态。

## R6.3 证据

R6.2 提交：`fe87636`。本批精确变更 9 个模块内文件：6 个独立 Action 终端、确定性网格、中心射线、交互实验场景与 GdUnit 文件，`player.tscn` 增加相机中心 `RayCast3D`，模块命令增加交互门禁，并更新本验收记录。R4/R5 场景与 Runtime、GdUnit4 vendor、Creator、examples、packages 和历史验收记录零差异。

- `npm.cmd run verify:godot:3d:interaction`：Godot 4.6.3 GdUnit 通过；覆盖终端标签与可用状态、非法/禁用 action、0/1/64 个 action 的声明顺序与 8 列确定性布局、重建与清理，以及射线忽略世界层并只触发交互层中的可用终端。
- `RayCast3D` 固定距离 3 m、collision mask 仅逻辑交互层 3，并只检测 Area；终端世界碰撞为 layer 3，玩家与世界碰撞设置保持 R6.2 不变。
- 最新树 `npm.cmd run verify`：12/12 步通过；433/433 Node、Godot import/adapter/parity/GdUnit/smoke、Creator 247 modules build 与 HTTP 200 smoke 全绿。
- `check:godot-boundary`：19 个第一方脚本通过；`check:round-scope` 为 `checked=38 changed=35`；`check:parent-scope` 为 `checked=38 changed=35`；`check:boundary` 为 `checked=803 tracked=797`；`git diff --check` 通过。

本批只建立通用终端与交互器，不载入 Runtime Pack、不执行 Runtime action、不包含样例题材分支。单独 revert R6.3 恢复只有移动与碰撞的 R6.2 状态。

## R6.4 证据

R6.3 提交：`f0c78f9`。本批精确变更 7 个模块内文件：独立 playable lab 场景/脚本与 GdUnit，终端配置补充 ready 渲染，网格重建升级为失败不清空旧世界，并更新相应交互测试和本验收记录。冻结的 R5 loader/runtime/HUD 与 R4 Bootstrap 均零差异。

- playable lab 只通过冻结的 `MatrixOasisRuntimeArtifactLoader` 和 `MatrixOasisGodotRuntime` 加载、create 与 apply；Runtime 成功候选完整验证并完成终端重建后才一次性替换会话字段。
- GdUnit 共 34 个 Godot 用例通过；新增覆盖 Pack 派生 HUD、可用/禁用终端、真实 Runtime action 后变量与终端刷新、ending 清空、reset 恢复入口与玩家 transform、unknown/unavailable action 保持 snapshot 和终端对象引用不变。
- 独立场景使用第一方 primitive 封闭测试舱、R6 玩家与确定性终端网格；稳定 readiness 为 `MATRIX_OASIS_R6_PLAYABLE_3D_READY`，不修改 R4 主场景或 R5 Runtime Lab。
- 最新树 `npm.cmd run verify`：12/12 步通过；433/433 Node、Godot import/adapter/parity/34 GdUnit/smoke、Creator 247 modules build 与 HTTP 200 smoke 全绿。
- `check:godot-boundary`：21 个第一方脚本通过；`check:round-scope` 与 `check:parent-scope` 均为 `checked=42 changed=38`；`check:boundary` 为 `checked=806 tracked=803`；`git diff --check` 通过。

本批不增加样例驱动 CLI、trace runner、固定帧捕获或人工预览；这些属于 R6.5。单独 revert R6.4 恢复仅可交互但未接 Runtime 的 R6.3 状态。

## R6.5 证据

R6.4 提交：`859c264`。本批精确变更 12 个模块内文件：playable trace runner、R6 Node harness、verify/preview/capture CLI、3 个合同测试、根命令接线、终端标签可读性调整、playable lab trace/smoke seam 和本验收记录。R1–R5 冻结文件与父仓路径零差异。

- `npm.cmd run verify:godot:3d`：13/13 Node 合同、34 个 Godot 用例、7 个 Runtime case、26 次独立 Godot trace 与 2 个 playable scene smoke 全通过。
- mechanics 权威轨迹通过真实终端 signal 进入冻结 R5 Runtime，20 次 JSON trace 完全一致；末班地铁三个 ending、显式循环与 step limit，以及 unknown/unavailable/ended、正负溢出均与冻结 R3 Runtime Simulator 的可观察结果逐字段一致。
- `npm.cmd run smoke:godot:3d`：两个冻结样例均从仓外临时 Runtime Pack/Receipt 启动独立 playable scene，唯一 readiness marker 通过。
- `npm.cmd run capture:godot:3d`：mechanics 12 帧 960×540、末班地铁 12 帧 640×540；PNG 非空、尺寸、帧数与逐帧 SHA-256 均由仓外 manifest 锁定。首次 mechanics 捕获仅产生 0 字节音频占位并 fail closed，随后宽/窄两次独立复跑均成功；脚本不自动重试或伪装通过。
- 代表帧人工检查确认 primitive 场景、HUD、准星和中英文终端可见；终端标签增加固定宽度换行，640×540 不裁切关键 HUD。交互键与完整三 ending 仍留给 R6.6 人工实机验收。
- 最新树 `npm.cmd run verify`：12/12 步通过；446/446 Node、Godot 4.6.3 import/adapter/parity/R6 3D/GdUnit/smoke、Creator 247 modules build 与 HTTP 200 smoke 全绿。
- `check:godot-boundary`：22 个第一方脚本通过；`check:round-scope` 与 `check:parent-scope` 均为 `checked=50 changed=46`；`check:boundary` 为 `checked=814 tracked=806`；`git diff --check` 通过。

本批固定帧和临时生成物仅在仓外；不提交 Pack、Receipt、PNG、日志或 `.godot/`。单独 revert R6.5 恢复 R6.4 的可玩场景，但移除自动差分、预览、捕获与稳定 R6 trace 门。

## 未运行项

Godot 3D 功能、固定帧、standalone、父 client build 与人工交互留待相应批次；父后端、Docker、共享栈、父路由、导出和部署不属于 R6。

## 回退

每批以 `git revert <sha>` 逆序回退；整体回退六个 R6 提交恢复合并 R5 的固定基线。无数据库、服务、路由或运行数据需要恢复。
