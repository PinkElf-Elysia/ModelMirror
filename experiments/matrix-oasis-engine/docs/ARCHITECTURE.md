# 架构方向

最后更新：2026-08-09
状态：R5 Godot Runtime Pack 适配器

## 当前系统

R1–R4 的合同、Validator、Compiler、两套 Simulator、parity harness、Creator、Godot Bootstrap 与 GdUnit4 vendor 均为冻结权威。R5 只消费 Compiler 产生的规范 Runtime Pack/Receipt，不修改任何上游语义。

```text
Authoring → Compiler → Runtime Pack + Receipt（R1–R3，冻结）
                                      │ paired local files
                                      ▼
Godot strict adapter → independent GDScript runtime → trace parity / debug HUD
```

R5 新场景实例化冻结的 R4 Bootstrap，并叠加独立 Runtime controller 与原生 Control HUD。Bootstrap 主场景、项目设置与 primitive 场景保持不变。

## 独立模块原则

- Godot 二进制、生成的 Runtime Pack/Receipt、图形证据和详细日志在仓外。
- 正式源码只依赖 Godot 标准 API；GdUnit4 是 dev-only vendored 测试框架。
- 自动门只使用 headless；图形捕获是 PR 前人工硬门。
- Godot 执行器独立于 JavaScript oracle；差分 harness 只调用冻结包的公开根接口。
- R5 不定义正式存档、网络协议、3D 玩法或资产绑定。

## 决策记录

- [ADR-0001：独立实验模块](./adr/0001-isolated-experiment-module.md)
- [ADR-0002：R1 活动轮次治理](./adr/0002-r1-active-round-governance.md)
- [ADR-0003：R2 参考模拟器治理](./adr/0003-r2-reference-simulator-governance.md)
- [ADR-0004：R3 Runtime Pack 与双执行治理](./adr/0004-r3-runtime-pack-governance.md)
- [ADR-0005：R4 Godot 工程底座治理](./adr/0005-r4-godot-foundation-governance.md)
- [ADR-0006：R5 Godot Runtime Pack 适配治理](./adr/0006-r5-godot-runtime-adapter-governance.md)
