# 架构方向

最后更新：2026-08-10
状态：R6 第一人称可玩 3D 骨架

## 当前系统

R1–R5 的合同、Validator、Compiler、三套 Runtime 执行路径、Creator、Godot Bootstrap、Runtime Lab 与 GdUnit4 vendor 均为冻结权威。R6 只在冻结 R5 Godot Runtime 之上增加可操控 3D 表现层。

```text
Authoring → Compiler → Runtime Pack + Receipt（R1–R3，冻结）
                                      │ paired local files
                                      ▼
Godot strict adapter → independent GDScript runtime（R5，冻结）
                                      │
                                      ▼
first-person controller → ray interaction → dynamic Action terminals
```

R6 新建独立 playable lab，调用冻结 R5 loader/runtime，并用内建 primitive 构建可碰撞测试舱和动态终端。R4 主场景与 R5 Runtime Lab 保持不变；`project.godot` 只有明确批准的输入、Jolt 与插值设置例外。

## 独立模块原则

- Godot 二进制、生成的 Runtime Pack/Receipt、图形证据和详细日志在仓外。
- 正式源码只依赖 Godot 标准 API；GdUnit4 是 dev-only vendored 测试框架。
- 自动门只使用 headless；图形捕获是 PR 前人工硬门。
- Godot 执行器独立于 JavaScript oracle；差分 harness 只调用冻结包的公开根接口。
- R6 不定义正式场景绑定、资产管线、NPC、导航、存档或网络协议。

## 决策记录

- [ADR-0001：独立实验模块](./adr/0001-isolated-experiment-module.md)
- [ADR-0002：R1 活动轮次治理](./adr/0002-r1-active-round-governance.md)
- [ADR-0003：R2 参考模拟器治理](./adr/0003-r2-reference-simulator-governance.md)
- [ADR-0004：R3 Runtime Pack 与双执行治理](./adr/0004-r3-runtime-pack-governance.md)
- [ADR-0005：R4 Godot 工程底座治理](./adr/0005-r4-godot-foundation-governance.md)
- [ADR-0006：R5 Godot Runtime Pack 适配治理](./adr/0006-r5-godot-runtime-adapter-governance.md)
- [ADR-0007：R6 第一人称可玩 3D 骨架治理](./adr/0007-r6-playable-3d-governance.md)
