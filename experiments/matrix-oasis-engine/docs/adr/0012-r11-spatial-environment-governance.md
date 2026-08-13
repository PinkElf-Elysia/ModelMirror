# ADR-0012：R11 Marble Splat空间环境治理

状态：已接受

## 决策

R11以R10体验缺陷为唯一功能目标：用Marble SPZ构建具有平移视差的环境视觉，同时继续使用独立collider GLB承担物理。R1–R10保持冻结。

SPZ经固定`@playcanvas/splat-transform@3.3.0`和`@adobe/spz@0.2.2`离线转换为deterministic compressed PLY。Godot固定vendored gdgs v3.3.0、commit `70996511607a886dac9fdd5fc59a0445308eb3db`与Compute后端。SOG因实测字节不稳定，不作为权威缓存格式。

## 2026-08-13 性能与稳定性修订

真实1.92M点资格资产证明原Compute路径只有约7 FPS并出现连续帧宏观闪动；因此原“禁止降采样”的失败策略被用户后续显式决策替代。R11现在允许确定性LOD，或更换、修改Gaussian renderer，但必须保留完整源身份与全量转换统计，并把派生算法、目标点数、字节数和hash写入canonical证据。

退出门不降低：960x540固定视角连续采样仍须稳定不少于30 FPS，不得出现宏观闪动或黑帧，视觉、collider和玩家出生点必须对齐；还必须用一个不同来源、非过拟合的第二样例跑通完整离线链路。校准采用源位置1%/99%稳健边界、真实GLB边界与Authoring入口spawn三个独立证据面，并把结果写入canonical Assembly。满足这些条件前不得提交R11.6、push、创建PR或宣称初版收尾。panorama与静默Raster回退仍禁止，人工试摆仍不能代替canonical校准。

## 回退

逆序revert R11提交即可恢复R10；仓外转换物、run、截图和供应商资产不随Git回退删除。
