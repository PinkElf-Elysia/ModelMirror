# 矩阵绿洲 AI 原生 3D 游戏引擎（实验模块）

这是模镜仓库中的可拆分独立实验模块。R1–R5 已建立数据合同、Compiler/Receipt、三套确定性执行路径、Godot 4.6.3 工程底座与 Runtime 调试实验台；R6 在冻结这些权威输入的前提下建立第一个第一人称可玩 3D 闭环。

## R6 当前状态

- 固定基线：`430f24a4fd8510a0d54f14bcd240a80423d16719`。
- 模块版本：`0.6.0-r6`，private/UNLICENSED。
- Godot：标准版 4.6.3 + GDScript，通过仓外 `GODOT_BIN` 提供。
- R1–R5 packages、Creator、examples、Bootstrap、Runtime、R5 HUD、GdUnit4 vendor 与历史验收全部冻结。
- R6 只新增 `playable` 场景、第一人称控制器、射线交互、动态 Action 终端及验证 harness。
- `project.godot` 只允许加入 InputMap、Jolt 与物理插值；主场景仍是 R4 Bootstrap。
- Runtime Pack/Receipt 继续通过仓外临时双文件只读进入 Godot，不提交生成产物。
- R6 不实现 NPC、导航、Marble、正式资产、AI、存档、父项目接入或部署。

## 目标数据流

```text
R3 Runtime Pack + Receipt（冻结）
→ R5 Godot Loader / Runtime（冻结）
→ R6 动态 Action 终端
→ 第一人称移动、观察与射线交互
→ Runtime 状态迁移与世界刷新
```

## 独立验证

在模块根执行：

```powershell
npm.cmd ci --no-audit --no-fund
npm.cmd prefix
npm.cmd ls --all
npm.cmd run doctor:godot
npm.cmd run verify:godot:3d
npm.cmd run verify
npm.cmd run verify:extraction
npm.cmd run check:parent-scope -- --base 430f24a4fd8510a0d54f14bcd240a80423d16719
```

任何父仓修改或共享栈重建都必须另行人工批准。各批可逆序 `git revert`；没有数据库、服务、路由或运行数据需要恢复。
