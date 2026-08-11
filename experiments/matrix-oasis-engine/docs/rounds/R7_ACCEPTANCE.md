# R7 验收记录

状态：R7.1 已验证，等待本地提交；R7.2–R7.6 未开始。

固定基线：`a4a2a68d2fc5cf056c741cd3101fcf36a250ad6e`
分支：`codex/matrix-oasis-r7-scene-binding`
模块版本：`0.7.0-r7`

## 批次

- [x] R7.1 治理与冻结迁移
- [ ] R7.2 Scene Pack contracts/validator
- [ ] R7.3 Kenney GLB 与 Godot loader
- [ ] R7.4 Runtime 场景组合
- [ ] R7.5 自动验证与 Splat 资格
- [ ] R7.6 standalone 与人工验收收口

## R7.1 证据

本批只迁移机器边界、版本、治理文档与正反 scope fixture。R1–R6 实现、Creator、既有 examples、`project.godot`、Godot vendor 与父仓路径零差异。

- `node --test tests/round-scope.test.mjs`：53/53 通过；覆盖 committed/staged/unstaged/untracked、R1–R6 冻结、R7 新前缀、父仓拒绝与 standalone。
- `node scripts/check-round-scope.mjs`：`ROUND_SCOPE_OK checked=17 changed=17`。
- `node scripts/check-parent-scope.mjs --base a4a2a68...`：`PARENT_SCOPE_OK checked=17 changed=17`。
- `node scripts/check-boundary.mjs`：`BOUNDARY_OK checked=818 tracked=814`。
- `git diff --check` 通过；全部 17 个变更路径均在模块内，R1–R6 冻结路径与父仓路径零差异。

提交 SHA 在 R7.2 或仓外交付清单记录，避免自引用。单独 revert 本提交恢复完整 R6 治理与版本。

## 外部调用事实

R7 不调用 Marble/Meshy，不读取凭据、额度或任务状态。gdgs 仅允许固定 commit 的仓外副本资格验证，不 vendoring。

## 回退

每批逆序 `git revert <sha>`。整体回退六个 R7 提交恢复固定 R6 基线；无数据库、路由、服务、共享栈或运行数据需要恢复。
