# 架构方向

最后更新：2026-08-06
状态：R0 隔离基线

## 当前系统

R0 的唯一可执行产品面是一个独立 Creator Web 空壳。它只展示实验状态，不生产、读取或运行游戏内容。

```text
┌───────────────────────────────┐
│ Creator Web：独立工程空壳     │
│ - 无父项目适配器              │
│ - 无 Game Pack                │
│ - 无外部网络                  │
│ - 无 Godot Runtime            │
└───────────────────────────────┘
```

## 未来组件方向

```text
Creator
  │ 作者编辑意图（未来）
  ▼
Authoring Game Pack
  │ 验证与编译（未来）
  ▼
Validator / Compiler
  │ 产生不可变运行包（未来）
  ▼
Immutable Runtime Pack
  │ 只读加载（未来）
  ▼
Godot Runtime
```

这不是接口定义。R0 不承诺 Pack 字段、序列化格式、版本策略、增量更新方式、AI 提案协议或 Godot 通信协议。

## 独立模块原则

- 模块内部拥有源代码、manifest、lockfile、构建、测试、诊断和拆分脚本。
- 父仓各模块未来只能经独立轮次设计、版本化并获人工批准的适配器被视为“可复用工具箱”；R0 不限定适配器协议。
- 在适配器获得人工审批前，父项目交互保持为空。
- 运行时依赖方向只能由本模块指向本模块内稳定接口，禁止隐式读取父源码或父环境。
- 每个后续轮次都必须保持“可 subtree 拆分”和“revert 即删除”的性质。

## R0 决策记录

见 [`adr/0001-isolated-experiment-module.md`](./adr/0001-isolated-experiment-module.md)。
