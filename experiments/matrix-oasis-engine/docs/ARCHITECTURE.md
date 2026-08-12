# 架构

状态：R10 初版原型闭环

```text
R8 Prototype Generator
  ├─ Authoring / Runtime / Blueprint
  ├─ R9 Asset Pipeline → Meshy prop + static character
  └─ R10 Environment Pipeline → Marble panorama + collider
             ↓
      R10 deterministic Assembler
             ↓
       frozen R7 Scene Pack
             ↓
      R10 Godot wrapper + frozen R7 scene lab
```

R10不修改R1–R9合同或执行语义。panorama通过私有Environment Bundle进入新wrapper；Scene Pack仍只引用GLB。宿主负责provider配置、审批、缓存、原子run和Godot子进程，Creator只访问same-origin loopback API。

主项目Marble适配仅作为已验证协议参考。R10模块实现独立Node provider，不导入父Python、凭据存储、路由或数据，确保subtree standalone。

相关决策见[ADR-0011](./adr/0011-r10-prototype-builder-governance.md)。
