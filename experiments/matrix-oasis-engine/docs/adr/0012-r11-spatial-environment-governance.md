# ADR-0012：R11 Marble Splat空间环境治理

状态：已接受

## 决策

R11以R10体验缺陷为唯一功能目标：用Marble SPZ构建具有平移视差的环境视觉，同时继续使用独立collider GLB承担物理。R1–R10保持冻结。

SPZ经固定`@playcanvas/splat-transform@3.3.0`和`@adobe/spz@0.2.2`离线转换为deterministic compressed PLY。Godot固定vendored gdgs v3.3.0、commit `70996511607a886dac9fdd5fc59a0445308eb3db`与Compute后端。SOG因实测字节不稳定，不作为权威缓存格式。

## 失败策略

gdgs导入、Compute渲染、full-resolution性能、坐标/米制校准或视觉碰撞对齐失败时停止并报告。不得以panorama、Raster、降采样或人工试摆冒充R11成功。

## 回退

逆序revert R11提交即可恢复R10；仓外转换物、run、截图和供应商资产不随Git回退删除。
