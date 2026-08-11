# 矩阵绿洲 AI 原生 3D 游戏引擎（实验模块）

这是模镜仓库中的可拆分独立实验模块。R1–R4 已建立数据合同、确定性双执行、Compiler/Receipt、parity Creator 与 Godot 4.6.3 验证底座；R5 在完整冻结这些权威输入的前提下建立 Runtime Pack 到 Godot 的第三执行路径。

## R5 当前状态

- 固定基线：`d47f1b15e5610f41d4d9f3e5fe91966530a1a4be`。
- 模块版本：`0.5.0-r5`，private/UNLICENSED。
- Godot：标准版 4.6.3 + GDScript，仓外通过 `GODOT_BIN` 提供。
- Godot 工程根：`apps/runtime-godot/`；只允许最小 Bootstrap、内建 primitive、测试和精确 GdUnit4 addon。
- R4 Bootstrap、GdUnit4 vendor、R1–R3 packages、Creator、examples 与全部历史验收字节冻结。
- schema v5 只放行新的 Runtime 适配源码、R5 测试、差分 harness 与独立 `runtime_lab` 场景。
- Runtime Pack/Receipt 通过仓外临时双文件只读进入 Godot，不提交生成产物。
- R5 不实现角色控制、Marble、3D 资产、AI、MCP 接入、父项目接入或部署。

## 目标数据流

```text
R3 Compiler → Runtime Pack + Receipt（冻结）
→ R5 Godot 严格适配器
→ 独立 GDScript 执行器
→ headless parity / 最小调试 HUD
```

## 独立验证

在模块根执行：

```powershell
npm.cmd ci --no-audit --no-fund
npm.cmd prefix
npm.cmd ls --all
npm.cmd run doctor:godot
npm.cmd run verify:godot
npm.cmd run verify
npm.cmd run verify:extraction
npm.cmd run check:parent-scope -- --base d47f1b15e5610f41d4d9f3e5fe91966530a1a4be
```

拆分脚本对自身创建的 Git 子进程启用 `core.longpaths=true`，不读取或改写用户的全局 Git 配置，以保证 Windows 上可检出 GdUnit4 的深层上游测试资源。

固定帧人工证据使用：

```powershell
npm.cmd run capture:godot -- --output C:\tmp\matrix-oasis-r4-capture
```

任何父仓修改或共享栈重建都必须另行人工批准。各批可逆序 `git revert`；没有数据库、服务、路由或运行数据需要恢复。
