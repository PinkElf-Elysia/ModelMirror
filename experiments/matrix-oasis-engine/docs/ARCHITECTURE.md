# 架构

状态：R11 空间环境体验收口

```text
R10 frozen prototype run
  ├─ Runtime / Scene Pack / Meshy assets
  ├─ Marble collider GLB
  └─ Marble SPZ → deterministic compressed PLY
                         ↓
           R11 metric spatial assembler
                         ↓
       Godot Forward+ Compute gdgs renderer
                         ↓
       frozen R7 interaction + R10 builder host
```

R11不修改R1–R10合同或执行语义。新的Spatial Environment Bundle只绑定SPZ来源、deterministic compressed PLY、collider与显式米制校准；Scene Pack仍不扩展。新Godot wrapper组合gdgs视觉、冻结collider/Runtime/Action终端，并保持失败原子性。

R11不调用供应商。SPZ转换和Godot渲染均为模块内离线能力，仓外输入不会被复制进Git。

相关决策见[ADR-0012](./adr/0012-r11-spatial-environment-governance.md)。
