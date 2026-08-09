# 矩阵绿洲 AI 原生 3D 游戏引擎（实验模块）

这是模镜仓库中的可拆分独立实验模块。R1–R3 已建立 Authoring Pack、确定性参考模拟器、Compiler、Runtime Pack/Receipt、独立 Runtime Simulator 与 parity Creator；R4 将这些能力完整冻结，只新增 Godot 4.6.3 工程和验证底座。

## R4 当前状态

- 固定基线：`df4a4b53e1f03f81fbf5a041065dc1443158c472`。
- 模块版本：`0.4.0-r4`，private/UNLICENSED。
- Godot：标准版 4.6.3 + GDScript，仓外通过 `GODOT_BIN` 提供。
- Godot 工程根：`apps/runtime-godot/`；只允许最小 Bootstrap、内建 primitive、测试和精确 GdUnit4 addon。
- R4.4 已增加第一方 GDScript 边界、固定 12 帧图形证据与仓外 MCP 资格报告；minimal MCP 仅获“后续只读候选”，satellite MCP 因 headless 编辑器桥接未就绪而延后，二者均未接入正式工程。
- schema v4 正向 allowlist 冻结 R1–R3，并拒绝未批准的 Godot 路径、addon、生成物和二进制。
- R4 不实现 Runtime Pack 桥接、玩法、控制器、Marble、3D 资产、AI、MCP 接入、父项目接入或部署。

## 目标数据流

```text
R1–R3 数据合同与模拟语义（冻结）
→ R4 Godot 4.6.3 独立工程
→ headless import / GdUnit4 / smoke
→ 仓外固定帧人工证据
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
npm.cmd run check:parent-scope -- --base df4a4b53e1f03f81fbf5a041065dc1443158c472
```

固定帧人工证据使用：

```powershell
npm.cmd run capture:godot -- --output C:\tmp\matrix-oasis-r4-capture
```

任何父仓修改或共享栈重建都必须另行人工批准。各批可逆序 `git revert`；没有数据库、服务、路由或运行数据需要恢复。
