# 架构

状态：R12 末班地铁初版闭环实施中

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

R11.5把空间缓存作为R10 run之外的事务overlay保存。读取、恢复和启动时必须同时重新验证冻结R10 run、overlay、全部Scene GLB和compressed PLY；只有两侧交集才可见。预览会复制已验证字节到一次性Godot工程，显式启用gdgs Compute并完成editor import后再启动独立`spatial_lab`。Creator与R10 host的API键保持不变，R10 run目录和`current.json`不被空间层改写。

R11不调用供应商。SPZ转换和Godot渲染均为模块内离线能力，仓外输入不会被复制进Git。

R11不是自然语言到正式3D游戏的初版闭环证明。R12必须以最初的末班地铁案例从自然语言开始贯通正式人物、环境/道具资产、Pack/Spatial组装和Godot游戏运行时，并以另一非题材专用样例验证可泛化；R12通过前不得宣称初版闭环。

相关决策见[ADR-0012](./adr/0012-r11-spatial-environment-governance.md)。
