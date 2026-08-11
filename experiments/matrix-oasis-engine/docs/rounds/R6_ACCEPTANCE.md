# R6 验收记录

状态：R6.1 已验证，等待本地提交；功能批次尚未开始。

固定基线：`430f24a4fd8510a0d54f14bcd240a80423d16719`
分支：`codex/matrix-oasis-r6-playable-3d-skeleton`
模块版本：`0.6.0-r6`

## 批次

- [x] R6.1 治理与冻结迁移
- [ ] R6.2 第一人称移动骨架
- [ ] R6.3 3D Action 终端
- [ ] R6.4 Runtime 与 3D 世界接通
- [ ] R6.5 自动验证与固定帧
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

## 未运行项

Godot 3D 功能、固定帧、standalone、父 client build 与人工交互留待相应批次；父后端、Docker、共享栈、父路由、导出和部署不属于 R6。

## 回退

每批以 `git revert <sha>` 逆序回退；整体回退六个 R6 提交恢复合并 R5 的固定基线。无数据库、服务、路由或运行数据需要恢复。
