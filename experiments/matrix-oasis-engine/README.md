# 矩阵绿洲 AI 原生 3D 游戏引擎（实验模块）

这是模镜仓库中可拆分的独立实验模块。R1–R8 已建立严格数据合同、确定性 Runtime、Godot 可玩层、离线 Scene Pack 与纯文本 Blueprint；R9 只增加 Meshy 优先的资产物化和离线规范化。

## R9 当前目标

```text
R8 Scene Blueprint
→ Meshy 道具/静态人物原始 GLB
→ 离线规范化 visual/collider
→ Prototype Asset Bundle
→ 固定测试布局的 Scene Pack / Godot 验证
```

- 固定基线：`da5fd0fe39234807ae3c4a1d543b9fd64de66d97`。
- 模块版本：`0.9.0-r9`，private/UNLICENSED。
- R1–R8、Creator、Godot、examples、Kenney 资产和 vendor 全部冻结。
- 环境继续使用 `kenney-prototype-room-v1`；R9 只生成一个 prop 和一个静态 character-placeholder。
- Marble、自动布局和一键预览均不进入 R9；自然语言到可玩 3D 闭环仍以 R10 为退出轮次。
- 真实 Meshy create/poll/download 不进入普通 `verify`，每个阶段都必须单独人工批准。

R9 已提供：

- `npm.cmd run plan:prototype-assets -- --prototype-dir <R8输出目录>`：离线列出资产计划，不发请求；
- `npm.cmd run qualify:meshy-asset -- --prototype-dir <目录> --brief <id> --operation <阶段>`：只在当次批准后执行一个 Meshy 阶段；
- `npm.cmd run materialize:prototype-assets -- --prototype-dir <目录> --acquired-dir <目录> --output <新目录>`：离线规范化并事务发布 Asset Bundle；
- `npm.cmd run verify:prototype-assets`：只使用本地夹具和 loopback 假 Provider，零外部费用；
- `npm.cmd run verify:prototype-assets:godot`：使用固定 Kenney 夹具离线构造资格 Scene Pack，并执行 Godot 4.6.3 import 与 headless smoke；传入三个仓外绝对目录参数时可验证已批准的真实 Asset Bundle。

R9 已完成真实 prop 与静态 character 的逐阶段 Meshy 资格、锁定工具链内的确定性规范化，以及固定测试布局的 Godot 显示/碰撞验证。该固定布局只用于 R9 资格，不是 R10 的通用 Blueprint 组装器。完整自动验证继续使用 `npm.cmd run verify`；任何父仓修改、共享栈操作或新的真实供应商调用仍需要单独授权。
