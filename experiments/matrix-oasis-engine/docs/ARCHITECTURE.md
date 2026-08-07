# 架构方向

最后更新：2026-08-07
状态：R2.1 参考模拟器治理基线

## 当前系统

R2.1 当前仍沿用 R0 Creator Web 空壳。R1 已完成的 Authoring Game Pack 合同、验证器与两个样例在 R2 字节冻结；确定性参考模拟器和 Creator 最小运行实验台尚未实现。

```text
┌───────────────────────────────┐
│ Creator Web：独立工程空壳     │
│ - 无父项目适配器              │
│ - 未接入 R1 Pack/Validator    │
│ - 未接入参考模拟器            │
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
  │ R2 参考执行与可观察轨迹（后续批次）
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

参考模拟器只能消费已通过 R1 Validator 的 Pack，以显式输入产生确定性状态与轨迹；不得修改 Pack、访问网络、持久化状态或伪装为 Runtime Pack/Godot 行为。Creator 后续只提供最小内存实验台。R2.1 只固定此方向和变更范围；后续批次必须严格实现已经人工批准的 R2 API 与语义计划。

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
