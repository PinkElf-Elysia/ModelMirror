# 架构

状态：R13 空间语义事实底座实施中

```text
Scene Blueprint ──→ Spatial Intent（语义约束，无坐标）
Marble collider ──→ isolated Godot analyzer
                    ├─ NavigationMesh topology
                    ├─ physics-tested floor anchors
                    └─ collision-aligned wall anchors
                              ↓
                    Environment Facts
                              ↓
                    R14 solver（本轮不实现）
```

R13不修改冻结的R1–R12合同、Creator或Godot产品场景。分析器以Runtime GLB载入、碰撞geometry、NavigationMesh和物理space query为事实来源；所有浮点只在Godot内部存在，公开facts在输出前量化为毫米、毫角度和稳定拓扑顺序。

参考项目只以固定commit的非执行摘录进入供应链护栏，不引入其Python、C#、Unity、AI2-THOR或运行依赖。相关决策见[ADR-0014](./adr/0014-r13-spatial-facts-governance.md)。
