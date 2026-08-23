# 矩阵绿洲独立实验模块

R15已完成双真实缓存的实际Godot输入重放与运行证据。R16正在把同一分析、求解、复验和证据profile接入Creator；当前状态为`pending-creator-migration`，人工验收前仍禁止初版完成声明。

这是模镜仓库中可拆分的AI原生3D引擎实验模块。R1–R13已建立严格Pack、确定性Runtime、真实资产、Marble空间环境、Godot可玩层与环境事实底座；R14负责解决自动组装的系统性空间偏差。

## R14目标

```text
Scene Blueprint + Runtime + Asset Bounds → Spatial Intent
Spatial Intent + R13 Environment Facts → deterministic solver
→ Godot final verification → solved preview
```

- 固定基线：`296e560d5197ff1367ad75455b2b9f5852560fd8`。
- R14不调用模型或资产供应商，不按末班地铁坐标调参，也不回退旧AABB网格布局。
- 求解器只使用严格合同和R13环境事实；Godot 4.6.3以真实导航、碰撞和视线复验结果。
- R14.7人工验收前旧Creator默认预览保持不变，且不得宣称初版闭环完成。

当前状态由`docs/MVP_STATUS.json`和`npm.cmd run check:mvp-claim`机器化约束。完整回归仍使用`npm.cmd run verify`。
