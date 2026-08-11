# 矩阵绿洲 AI 原生 3D 游戏引擎（实验模块）

这是模镜仓库中的可拆分独立实验模块。R1–R6 已建立数据合同、Compiler/Receipt、三套确定性执行路径、Godot 4.6.3 工程底座、Runtime 调试实验台与第一人称可玩闭环；R7 在冻结这些权威输入的前提下增加独立 Scene Pack 与离线 GLB 场景绑定。

## R7 当前状态

- 固定基线：`a4a2a68d2fc5cf056c741cd3101fcf36a250ad6e`。
- 模块版本：`0.7.0-r7`，private/UNLICENSED。
- Godot：标准版 4.6.3 + GDScript，通过仓外 `GODOT_BIN` 提供。
- R1–R6 packages、Creator、examples、Bootstrap、Runtime、playable、GdUnit4 vendor 与历史验收全部冻结。
- R7 新增 `scene_binding`、Scene Pack contracts/validator、本地 GLB 夹具及验证 harness。
- `project.godot` 与 R4–R6 场景保持不变；R7 使用独立场景入口。
- Runtime Pack/Receipt 与 Scene Pack 继续通过仓外临时文件只读进入 Godot，不提交生成 Runtime 产物。
- R7 不调用 Marble/Meshy，不实现 SPZ、NPC、导航、AI、存档、父项目接入或部署。

## 目标数据流

```text
R3 Runtime Pack + Receipt（冻结）
+ R7 Scene Pack + local GLB
→ R5 Godot Loader / Runtime（冻结）
→ R7 场景组合器
→ R6 第一人称与动态 Action 终端（冻结）
→ Runtime 状态迁移与数据驱动世界刷新
```

## 独立验证

在模块根执行：

```powershell
npm.cmd ci --no-audit --no-fund
npm.cmd prefix
npm.cmd ls --all
npm.cmd run doctor:godot
npm.cmd run verify:scene-pack
npm.cmd run verify:godot:scene
npm.cmd run preview:godot:scene -- --example mechanics-conformance
npm.cmd run capture:godot:scene -- --example mechanics-conformance --output C:\tmp\matrix-oasis-r7-capture
npm.cmd run verify
npm.cmd run verify:extraction
npm.cmd run check:parent-scope -- --base a4a2a68d2fc5cf056c741cd3101fcf36a250ad6e
```

`qualify:godot-splat` 只针对仓外固定 checkout 生成资格报告，不会把 addon 写入正式工程。当前固定提交因实际版本 `3.3.0` 与计划锁定的 `3.2.0-beta` 不一致，结论为 `defer`。

任何父仓修改或共享栈重建都必须另行人工批准。各批可逆序 `git revert`；没有数据库、服务、路由或运行数据需要恢复。
