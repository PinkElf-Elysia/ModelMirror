# 架构方向

最后更新：2026-08-11
状态：R8 自然语言原型生成

## 当前系统

R1–R7 的合同、Validator、Compiler、Runtime、Creator、Godot、Scene Pack、资产和验证链均为冻结权威。R8 在它们之前增加纯文本生成层，不修改执行语义或3D入口。

```text
pure text → Generation Proposal → Authoring + private Scene Blueprint（R8）
                                      │
                                      ▼
Compiler → Runtime Pack + Receipt → deterministic Runtime validation（冻结）
                                      │
                                      ▼
Scene Pack + local GLB → Godot playable pipeline（R7，冻结且R8不启动）
```

R8 的 Scene Blueprint 只是供应商无关的生成中间合同：它表达环境、资产需求、逻辑区域和节点可见关系，不含真实资产路径、哈希、3D坐标或供应商任务。生成编排最多执行一次初始请求和两次定向修复，并以冻结 Validator、Compiler 和 Runtime 作为发布前门禁。R9/R10 可以消费 Blueprint，但它不是 Runtime Pack、Scene Pack 或存档格式。

## 独立模块原则

- Godot 二进制、生成的 Runtime Pack/Receipt、图形证据和详细日志在仓外。
- 正式源码只依赖 Godot 标准 API；GdUnit4 是 dev-only vendored 测试框架。
- 自动门只使用 headless；图形捕获是 PR 前人工硬门。
- Godot 执行器独立于 JavaScript oracle；差分 harness 只调用冻结包的公开根接口。
- 唯一网络例外是模块内 OpenAI 兼容适配器；Creator、Godot和既有运行包仍完全离线。
- R8 不定义资产供应商协议、AI NPC、记忆、任务规划、世界事件、图片输入或运行期AI。

## 决策记录

- [ADR-0001：独立实验模块](./adr/0001-isolated-experiment-module.md)
- [ADR-0002：R1 活动轮次治理](./adr/0002-r1-active-round-governance.md)
- [ADR-0003：R2 参考模拟器治理](./adr/0003-r2-reference-simulator-governance.md)
- [ADR-0004：R3 Runtime Pack 与双执行治理](./adr/0004-r3-runtime-pack-governance.md)
- [ADR-0005：R4 Godot 工程底座治理](./adr/0005-r4-godot-foundation-governance.md)
- [ADR-0006：R5 Godot Runtime Pack 适配治理](./adr/0006-r5-godot-runtime-adapter-governance.md)
- [ADR-0007：R6 第一人称可玩 3D 骨架治理](./adr/0007-r6-playable-3d-governance.md)
- [ADR-0008：R7 Scene Pack 与离线资产治理](./adr/0008-r7-scene-pack-governance.md)
- [ADR-0009：R8 自然语言原型生成治理](./adr/0009-r8-natural-language-prototype-governance.md)
