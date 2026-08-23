# 架构

状态：R16 Creator迁移与MVP重新资格实施中

R15在R14 Solution/Verification之后增加独立Runtime Replay Plan与Runtime Evidence层。R16在其外增加Creator Qualification层，绑定source、Solution、Verification和Evidence；它只定义Creator `ready`资格，不成为新的Runtime、Scene或Spatial权威格式。

```text
Scene Blueprint + Runtime + Asset Bounds ──→ Spatial Intent synthesizer
R13 Environment Facts + Spatial Intent ────→ deterministic bounded solver
                                               ↓
                                      Spatial Solution
                                               ↓
                              isolated Godot final verifier
                                               ↓
                                  solved overlay + preview
```

R14冻结R1–R13权威实现，以新workspace和隔离Godot目录消费既有合同。求解无随机、无时间退出、无部分成功；最终发布前必须通过真实导航、capsule、接地、重叠、穿透、terminal approach和视线复验。

R13固定的参考项目仍只作为非执行设计证据，不引入其运行依赖。相关决策见[ADR-0015](./adr/0015-r14-spatial-solver-governance.md)。
