# 矩阵绿洲独立实验模块

R16已把R13–R15的分析、求解、物理复验和实际运行证据接入Creator；中性与末班地铁双真实缓存均从Creator入口重新资格并通过人工验收。当前状态为`r16-qualified`，完成标识为`MATRIX_OASIS_R16_CREATOR_MVP_READY`。

这是模镜仓库中可拆分的AI原生3D引擎实验模块。R1–R13已建立严格Pack、确定性Runtime、真实资产、Marble空间环境、Godot可玩层与环境事实底座；R14负责解决自动组装的系统性空间偏差。

## 已完成关键路径

```text
Scene Blueprint + Runtime + Asset Bounds → Spatial Intent
Spatial Intent + R13 Environment Facts → deterministic solver
→ Godot final verification → solved preview
```

- R16固定基线：`7c837fe3908a4a5b60551778313624f53bcd0d1b`。
- R14不调用模型或资产供应商，不按末班地铁坐标调参，也不回退旧AABB网格布局。
- 求解器只使用严格合同和R13环境事实；Godot 4.6.3以真实导航、碰撞和视线复验结果。
- `preview:prototype`现默认使用R16资格profile；`preview:r12`、`preview:r14`和`preview:r15`保留为显式回退入口。

当前状态由`docs/MVP_STATUS.json`和`npm.cmd run check:mvp-claim`机器化约束。完整回归仍使用`npm.cmd run verify`。
