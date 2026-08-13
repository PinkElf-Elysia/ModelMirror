# 矩阵绿洲独立实验模块

这是模镜仓库中可拆分的独立AI原生3D引擎实验模块。R1–R10已建立严格Pack、确定性Runtime、Godot可玩层、真实Meshy资产、Marble panorama/collider与Creator一键预览；R11只收口环境空间体验。

## R11目标

```text
冻结R10原型 → Marble SPZ离线转换 + collider
→ Godot Compute Gaussian Splat → 米制对齐 → Creator空间预览
```

- 固定基线：`da2a914a2ff131507750a0afb8d8881180530f62`。
- 固定使用已取得的SPZ/collider缓存；本轮不调用Marble、Meshy或模型。
- 权威离线中间格式为deterministic compressed PLY；SOG不作为缓存合同。
- Godot固定Forward+ Compute后端，不允许Raster或panorama静默回退。
- R11通过只宣称体验级初版闭环，不宣称生产级环境资产或跨GPU性能一致。

主要命令：`verify:spatial-environment`、`verify:spatial-assembly`、`verify:godot:splat`、`verify:spatial-builder`和`verify:r11`。R11.5使用`import:spatial-prototype-cache`把已经验证的R10 run与Spatial产物发布为独立overlay，再由`preview:spatial-prototype`启动一次性Compute预览工程；不会修改R10 run或`current.json`。完整回归仍使用`npm.cmd run verify`。
