# 架构方向

最后更新：2026-08-07
状态：R2.3 参考模拟器语义基线

## 当前系统

R1 已完成的 Authoring Game Pack 合同、验证器与两个样例在 R2 字节冻结。R2.2 已实现独立参考模拟器，R2.3 已用中性权威轨迹和可替换集成夹具固定其语义；Creator Web 仍沿用 R0 空壳，尚未接入模拟器。

```text
┌───────────────────────────────┐
│ Creator Web：独立工程空壳     │
│ - 无父项目适配器              │
│ - 未接入 R1 Pack/Validator    │
│ - R2.4 前未接入参考模拟器     │
│ - 无外部网络                  │
│ - 无 Godot Runtime            │
└───────────────────────────────┘
```

## 组件方向与 R2 边界

```text
Creator
  │ 作者编辑意图（未来）
  ▼
Authoring Game Pack
  │ R1 确定性验证
  ▼
Validator
  │ R2 参考执行与可观察轨迹（已实现）
  ▼
Deterministic Reference Simulator
  │ 不构成生产运行包
  ▼
Compiler（未来）
  │ 产生不可变运行包（未来）
  ▼
Immutable Runtime Pack
  │ 只读加载（未来）
  ▼
Godot Runtime
```

参考模拟器只消费已通过 R1 Validator 的 Pack，以显式输入产生确定性状态与单步轨迹；它不修改 Pack、不访问网络、不持久化状态，也不伪装为 Runtime Pack 或 Godot 行为。Creator 在 R2.4 只接入这些公开纯函数，提供最小内存实验台。

## 独立模块原则

- 模块内部拥有源代码、manifest、lockfile、构建、测试、诊断和拆分脚本。
- 父仓各模块未来只能经独立轮次设计、版本化并获人工批准的适配器被视为“可复用工具箱”；R2 不限定适配器协议。
- 在适配器获得人工审批前，父项目交互保持为空。
- 运行时依赖方向只能由本模块指向本模块内稳定接口，禁止隐式读取父源码或父环境。
- 每个后续轮次都必须保持“可 subtree 拆分”和“revert 即删除”的性质。

## R0 决策记录

见 [`adr/0001-isolated-experiment-module.md`](./adr/0001-isolated-experiment-module.md)。

## R1 决策记录

见 [`adr/0002-r1-active-round-governance.md`](./adr/0002-r1-active-round-governance.md)。

## R2 决策记录

见 [`adr/0003-r2-reference-simulator-governance.md`](./adr/0003-r2-reference-simulator-governance.md)。
