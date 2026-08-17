# 矩阵绿洲独立实验模块

这是模镜仓库中可拆分的AI原生3D引擎实验模块。R1–R12已建立严格Pack、确定性Runtime、真实资产、Marble空间环境和Godot可玩层，也暴露了自动组装缺少可靠空间事实的问题。

## R13目标

```text
Scene Blueprint → Spatial Intent
Marble Collider GLB → Godot Environment Facts
→ 为R14确定性约束求解器提供可靠输入
```

- 固定基线：`77ec8c4eace9f8dbd1dd119cd70727570bd99e9a`。
- R13不实现布局求解、不修改Creator或现有预览，也不对末班地铁继续局部调参。
- 新分析器只使用本地已验证collider和Godot 4.6.3碰撞、导航、物理查询；无供应商调用或费用。
- R13只交付可验证的语义意图与环境事实。R14求解器及两类案例重新验收前，不得宣称初版闭环完成。

当前状态由`docs/MVP_STATUS.json`和`npm.cmd run check:mvp-claim`机器化约束。完整回归仍使用`npm.cmd run verify`。
