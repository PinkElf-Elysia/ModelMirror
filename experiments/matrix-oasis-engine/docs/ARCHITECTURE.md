# 架构方向

最后更新：2026-08-07
状态：R1 合同与验证器轮次（R1.2 作者合同）

## 当前系统

当前唯一产品面仍是冻结的 R0 Creator Web 空壳。R1 不修改该页面；Authoring Game Pack 合同已在模块内部建立，验证器仍待后续批次实现。

```text
┌───────────────────────────────┐
│ Creator Web：独立工程空壳     │
│ - 无父项目适配器              │
│ - 无 Game Pack                │
│ - 无外部网络                  │
│ - 无 Godot Runtime            │
└───────────────────────────────┘
```

## 组件方向与 R1 边界

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

R1.2 已把 Authoring Game Pack 0.1.0 固定为内部稳定合同。后续批次只实现 Validator 与验收夹具；Compiler、Runtime Pack、AI 提案和 Godot 通信仍不在本轮定义。

## 独立模块原则

- 模块内部拥有源代码、manifest、lockfile、构建、测试、诊断和拆分脚本。
- 父仓各模块未来只能经独立轮次设计、版本化并获人工批准的适配器被视为“可复用工具箱”；R1 不限定适配器协议。
- 在适配器获得人工审批前，父项目交互保持为空。
- 运行时依赖方向只能由本模块指向本模块内稳定接口，禁止隐式读取父源码或父环境。
- 每个后续轮次都必须保持“可 subtree 拆分”和“revert 即删除”的性质。

## R0 决策记录

见 [`adr/0001-isolated-experiment-module.md`](./adr/0001-isolated-experiment-module.md)。

## R1 决策记录

见 [`adr/0002-r1-active-round-governance.md`](./adr/0002-r1-active-round-governance.md)。
