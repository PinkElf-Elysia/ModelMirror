# 架构方向

最后更新：2026-08-10
状态：R7 独立 Scene Pack 与离线场景绑定

## 当前系统

R1–R6 的合同、Validator、Compiler、三套 Runtime 执行路径、Creator、Godot Bootstrap、Runtime Lab、playable 与 GdUnit4 vendor 均为冻结权威。R7 只增加独立 Scene Pack、本地 GLB 入口与场景组合层。

```text
Authoring → Compiler → Runtime Pack + Receipt（R1–R3，冻结）
                                      │ paired local files
                                      ▼
Godot strict adapter → independent GDScript runtime（R5，冻结）
                                      │
                                      ▼
Scene Pack + local GLB → scene composer
                                      │
                                      ▼
frozen first-person controller → ray interaction → dynamic Action terminals
```

R7 新建独立 scene lab，调用冻结 R5 loader/runtime 与 R6 playable 公开类。Scene Pack 绑定 Runtime 身份、GLB、placement、node spawn 与 action anchor；不修改 Runtime schema、snapshot 或 `project.godot`。

## 独立模块原则

- Godot 二进制、生成的 Runtime Pack/Receipt、图形证据和详细日志在仓外。
- 正式源码只依赖 Godot 标准 API；GdUnit4 是 dev-only vendored 测试框架。
- 自动门只使用 headless；图形捕获是 PR 前人工硬门。
- Godot 执行器独立于 JavaScript oracle；差分 harness 只调用冻结包的公开根接口。
- R7 仍不定义供应商在线协议、SPZ、NPC、导航、存档或网络协议。

## 决策记录

- [ADR-0001：独立实验模块](./adr/0001-isolated-experiment-module.md)
- [ADR-0002：R1 活动轮次治理](./adr/0002-r1-active-round-governance.md)
- [ADR-0003：R2 参考模拟器治理](./adr/0003-r2-reference-simulator-governance.md)
- [ADR-0004：R3 Runtime Pack 与双执行治理](./adr/0004-r3-runtime-pack-governance.md)
- [ADR-0005：R4 Godot 工程底座治理](./adr/0005-r4-godot-foundation-governance.md)
- [ADR-0006：R5 Godot Runtime Pack 适配治理](./adr/0006-r5-godot-runtime-adapter-governance.md)
- [ADR-0007：R6 第一人称可玩 3D 骨架治理](./adr/0007-r6-playable-3d-governance.md)
- [ADR-0008：R7 Scene Pack 与离线资产治理](./adr/0008-r7-scene-pack-governance.md)
